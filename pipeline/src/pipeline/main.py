"""
Hetzner Auction Pipeline — entrypoint

Runs the 10-minute refresh loop described in docs/plan/plan.md's "Pipeline Run
Lifecycle": fetch -> normalize/match -> compute -> write -> publish. Every
step already implements its own verify-before-publish discipline (see
pages_publisher.py) — this module's only job is to wire the existing pieces
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
from pipeline.pages_publisher import PagesPublisher, PagesPublisherError
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
WEB_SOURCE_DIR = Path(os.environ.get("WEB_SOURCE_DIR", "/app/web"))


def _build_publisher() -> PagesPublisher:
    return PagesPublisher(directory=WEB_SOURCE_DIR)


async def run_once(cpu_matcher: CpuMatcher, publisher: PagesPublisher) -> None:
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
        deploy_dir = Path(tmpdir)
        parquet_path = deploy_dir / PARQUET_SNAPSHOT_KEY
        json_path = deploy_dir / UNMATCHED_REPORT_KEY

        write_listings_to_parquet(enriched, parquet_path)
        reporter.generate_report(json_path)

        # Copy web/ content to deployment directory
        import shutil
        for item in WEB_SOURCE_DIR.iterdir():
            dest = deploy_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

        # Create PagesPublisher for this deployment directory and publish
        deploy_publisher = PagesPublisher(directory=deploy_dir)
        deploy_publisher.publish()

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
        except PagesPublisherError as e:
            logger.error(f"Publish failed, live deployment untouched: {e}")
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
