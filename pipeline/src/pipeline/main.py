"""
Hetzner Auction Pipeline — entrypoint

Runs the 10-minute refresh loop described in docs/plan/plan.md's "Pipeline Run
Lifecycle": fetch -> normalize/match -> compute -> write -> publish. Every
step already implements its own verify-before-publish discipline (see
r2_publisher.py) — this module's only job is to wire the existing pieces
together and keep the loop alive across a single run's failure.

Configuration is entirely environment-variable driven (see the ConfigMap /
ExternalSecret in declarative-config's k8s/iad-ci/hetzner-auction-dashboard/).
"""

import asyncio
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

from pipeline.cpu_matcher import CpuMatcher
from pipeline.enricher import enrich_listings_batch
from pipeline.fetcher import FetchError, HetznerAuctionFetcher
from pipeline.parquet_writer import write_listings_to_parquet
from pipeline.r2_publisher import R2Publisher, R2PublisherError
from pipeline.unmatched_reporter import UnmatchedCpuReporter

logging.basicConfig(
    level=os.environ.get("PIPELINE_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

REFRESH_INTERVAL_SECONDS = int(os.environ.get("PIPELINE_REFRESH_INTERVAL_SECONDS", "600"))

# The Dockerfile's WORKDIR is /app with `pipeline/src` copied to /app/src and
# `benchmark-map/` copied to /app/benchmark-map. CpuMatcher's own default
# (relative to its module file) assumes an unflattened repo checkout and
# resolves to the wrong path once /app/src drops the `pipeline/` directory
# level the default math depends on — pass it explicitly rather than rely on
# that default in this deployed layout.
BENCHMARK_MAP_DIR = Path(os.environ.get("BENCHMARK_MAP_DIR", "/app/benchmark-map"))

PARQUET_SNAPSHOT_KEY = os.environ.get("PARQUET_SNAPSHOT_KEY", "current_snapshot.parquet")
UNMATCHED_REPORT_KEY = os.environ.get("UNMATCHED_REPORT_KEY", "unmatched-cpus.json")


def _build_publisher() -> R2Publisher:
    return R2Publisher(
        account_id=os.environ["R2_ACCOUNT_ID"],
        access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        bucket_name=os.environ["R2_BUCKET_NAME"],
        endpoint_url=os.environ.get(
            "R2_ENDPOINT_URL",
            f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        ),
    )


async def run_once(cpu_matcher: CpuMatcher, publisher: R2Publisher) -> None:
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

    with tempfile.TemporaryDirectory(prefix="hetzner-pipeline-") as tmpdir:
        tmp_parquet = Path(tmpdir) / "snapshot.parquet"
        tmp_report = Path(tmpdir) / "unmatched-cpus.json"

        write_listings_to_parquet(enriched, tmp_parquet)
        reporter.generate_report(tmp_report)

        publisher.publish_parquet_snapshot(tmp_parquet, live_key=PARQUET_SNAPSHOT_KEY)
        publisher.publish_json_report(tmp_report, live_key=UNMATCHED_REPORT_KEY)

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
    publisher = _build_publisher()

    while True:
        cycle_start = time.monotonic()
        try:
            await run_once(cpu_matcher, publisher)
        except FetchError as e:
            # Per Pipeline Run Lifecycle: abort before any write, keep serving
            # the last snapshot, retry next cycle.
            logger.error(f"Fetch failed, keeping last published snapshot: {e}")
        except R2PublisherError as e:
            logger.error(f"Publish failed, live key untouched: {e}")
        except Exception:
            logger.exception("Unexpected error in pipeline cycle — keeping last published snapshot")

        elapsed = time.monotonic() - cycle_start
        sleep_for = max(0.0, REFRESH_INTERVAL_SECONDS - elapsed)
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
