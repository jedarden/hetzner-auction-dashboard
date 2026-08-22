"""
Hetzner Auction Pipeline — entrypoint

Runs the one-minute refresh loop described in docs/architecture.md: fetch ->
normalize/match -> compute -> write immutable generation -> publish manifest.
Every step implements verify-before-publish discipline; this module wires the pieces
together and keep the loop alive across a single run's failure.

Configuration is entirely environment-variable driven (see the ConfigMap /
ExternalSecret in declarative-config's k8s/iad-ci/hetzner-auction-dashboard/).
"""

import asyncio
import logging
import os
import random
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from pipeline.cpu_matcher import CpuMatcher
from pipeline.enricher import enrich_listings_batch
from pipeline.fetcher import FetchError, HetznerAuctionFetcher
from pipeline.history_store import (
    HistoryFetchError,
    compute_percentiles,
    fetch_history,
    update_history,
    write_history,
)
from pipeline.listing_history_store import (
    ListingHistoryFetchError,
    fetch_listing_history,
    update_listing_history,
    write_listing_history,
)
from pipeline.parquet_writer import write_listings_to_parquet
from pipeline.garage_publisher import GaragePublisher, GaragePublisherError, dataset_hash
from pipeline.unmatched_reporter import UnmatchedCpuReporter

logging.basicConfig(
    level=os.environ.get("PIPELINE_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

REFRESH_INTERVAL_SECONDS = int(os.environ.get("PIPELINE_REFRESH_INTERVAL_SECONDS", "60"))

# The Dockerfile's WORKDIR is /app with `pipeline/src` copied to /app/src and
# `benchmark-map/` copied to /app/benchmark-map. CpuMatcher's own default
# (relative to its module file) assumes an unflattened repo checkout and
# resolves to the wrong path once /app/src drops the `pipeline/` directory
# level the default math depends on — pass it explicitly rather than rely on
# that default in this deployed layout.
BENCHMARK_MAP_DIR = Path(os.environ.get("BENCHMARK_MAP_DIR", "/app/benchmark-map"))

PARQUET_SNAPSHOT_KEY = os.environ.get("PARQUET_SNAPSHOT_KEY", "current_snapshot.parquet")
UNMATCHED_REPORT_KEY = os.environ.get("UNMATCHED_REPORT_KEY", "unmatched-cpus.json")
WEB_CACHE_DIR = Path(os.environ.get("WEB_CACHE_DIR", "/tmp/web-cache"))

# v2 historical-value feature (docs/plan/plan.md "Historical stats: value
# percentile & all-time-low"). CONFIG_HISTORY_BASE_URL is the live site's own
# origin -- config_history.parquet is fetched back from the *published*
# deployment each cycle before being updated and rewritten, same pattern as
# web_fetcher.py's per-cycle web/ refresh.
CONFIG_HISTORY_KEY = os.environ.get("CONFIG_HISTORY_KEY", "config_history.parquet")
LISTING_HISTORY_KEY = os.environ.get("LISTING_HISTORY_KEY", "listing_history.parquet")
LISTING_HISTORY_RETENTION_DAYS = int(os.environ.get("LISTING_HISTORY_RETENTION_DAYS", "180"))
CONFIG_HISTORY_BASE_URL = os.environ.get(
    "CONFIG_HISTORY_BASE_URL", "https://hetzner-auction-dashboard.pages.dev"
)


async def run_once(cpu_matcher: CpuMatcher) -> None:
    fetcher = HetznerAuctionFetcher()
    raw_listings = await fetcher.fetch()
    logger.info(f"Fetched {len(raw_listings)} raw listings")

    if not raw_listings:
        logger.info("No listings in this run (EC-1) — nothing to publish, will retry next cycle")
        return

    reporter = UnmatchedCpuReporter()
    matches = []
    for listing in raw_listings:
        match = cpu_matcher.match_cpu(listing.cpu_raw)
        matches.append(match)
        reporter.process_listing(listing.listing_id, match)

    # enrich_listings_batch re-matches internally rather than accepting the
    # `matches` computed above — cheap enough (dict lookups) not to bother
    # threading the precomputed results through.
    enriched = enrich_listings_batch(raw_listings, cpu_matcher=cpu_matcher)
    logger.info(f"Enriched {len(enriched)} listings")

    publisher = GaragePublisher()
    digest = dataset_hash(enriched)
    if not publisher.is_changed(digest):
        logger.info("Auction dataset unchanged; skipping history rewrite and publication")
        return

    # Fetch-back the currently-live config_history.parquet before folding
    # this cycle's prices into it (v2 historical-value feature). "Nothing
    # published yet" (a 404, or -- the actual case on Cloudflare Pages,
    # which never 404s -- a 200 serving the SPA's index.html) starts from
    # empty history; any other failure raises HistoryFetchError, which
    # run_once's caller (main_loop) handles the same way as a Hetzner feed
    # failure -- abort this cycle, keep the last published snapshot, retry
    # next cycle. See HistoryFetchError's docstring for why that distinction
    # matters.
    history_url = publisher.active_file_url(CONFIG_HISTORY_KEY) or f"{CONFIG_HISTORY_BASE_URL}/{CONFIG_HISTORY_KEY}"
    history = await fetch_history(history_url)
    now = datetime.now(UTC)
    update_history(history, enriched, now)
    compute_percentiles(history, enriched)
    logger.info(f"Config history now tracks {len(history)} distinct configurations")

    listing_history_url = publisher.active_file_url(LISTING_HISTORY_KEY) or f"{CONFIG_HISTORY_BASE_URL}/{LISTING_HISTORY_KEY}"
    listing_history = await fetch_listing_history(listing_history_url)
    update_listing_history(
        listing_history, enriched, now, LISTING_HISTORY_RETENTION_DAYS, cpu_matcher=cpu_matcher
    )
    logger.info(f"Listing history now tracks {len(listing_history)} offer lifecycles")

    with tempfile.TemporaryDirectory(prefix="hetzner-pipeline-") as tmpdir:
        deploy_dir = Path(tmpdir)
        parquet_path = deploy_dir / PARQUET_SNAPSHOT_KEY
        json_path = deploy_dir / UNMATCHED_REPORT_KEY
        history_path = deploy_dir / CONFIG_HISTORY_KEY
        listing_history_path = deploy_dir / LISTING_HISTORY_KEY

        write_listings_to_parquet(enriched, parquet_path)
        reporter.generate_report(json_path)
        write_history(history, history_path)
        write_listing_history(listing_history, listing_history_path)

        publisher.publish(deploy_dir, digest, now)

    logger.info(
        f"Cycle complete: published {len(enriched)} listings, "
        f"{reporter.get_unmatched_count()} unmatched CPUs "
        f"({reporter.get_total_affected_listings()} affected listings)"
    )


async def main_loop() -> None:
    logger.info(f"Starting hetzner-auction-pipeline (refresh interval: {REFRESH_INTERVAL_SECONDS}s)")
    cpu_matcher = CpuMatcher(benchmark_map_dir=BENCHMARK_MAP_DIR)
    logger.info(
        f"Loaded benchmark map: {cpu_matcher.get_reference_table_size()} reference entries, "
        f"{cpu_matcher.get_aliases_count()} aliases, {cpu_matcher.get_overrides_count()} overrides"
    )

    consecutive_failures = 0
    while True:
        cycle_start = time.monotonic()
        failed = False
        try:
            await run_once(cpu_matcher)
        except FetchError as e:
            failed = True
            # Per Pipeline Run Lifecycle: abort before any write, keep serving
            # the last snapshot, retry next cycle.
            logger.error(f"Fetch failed, keeping last published snapshot: {e}")
        except HistoryFetchError as e:
            failed = True
            # Same handling as a feed failure -- see HistoryFetchError's
            # docstring for why this must NOT fall back to an empty history.
            logger.error(f"Config-history fetch-back failed, keeping last published snapshot: {e}")
        except ListingHistoryFetchError as e:
            failed = True
            logger.error(f"Listing-history fetch-back failed, keeping last published snapshot: {e}")
        except GaragePublisherError as e:
            failed = True
            logger.error(f"Publish failed, live deployment untouched: {e}")
        except Exception:
            failed = True
            logger.exception("Unexpected error in pipeline cycle — keeping last published snapshot")

        consecutive_failures = consecutive_failures + 1 if failed else 0
        base_delay = min(
            REFRESH_INTERVAL_SECONDS * (2 ** max(0, consecutive_failures - 1)),
            900,
        )
        jittered_delay = base_delay * random.uniform(0.9, 1.1)
        elapsed = time.monotonic() - cycle_start
        sleep_for = max(0.0, jittered_delay - elapsed)
        logger.info(f"Cycle took {elapsed:.1f}s, sleeping {sleep_for:.1f}s until next run")
        await asyncio.sleep(sleep_for)


def main() -> None:
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        logger.info("Shutting down (interrupt received)")
        sys.exit(0)


if __name__ == "__main__":
    main()
