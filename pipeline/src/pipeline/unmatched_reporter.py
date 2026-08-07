"""
Unmatched CPU Reporter

Generates the unmatched-cpus.json report that lists all unmatched CPU strings
and their affected listing counts from each pipeline run.

This report is published alongside the Parquet snapshot to Cloudflare Pages and used to
maintain the benchmark-map alias and override lists.
"""

import json
import logging
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Dict, List

from pipeline.cpu_matcher import BenchmarkMatch

logger = logging.getLogger(__name__)


@dataclass
class UnmatchedCpuEntry:
    """Single unmatched CPU entry in the report."""

    cpu_raw: str  # The unmatched CPU string
    affected_count: int  # Number of listings affected
    first_seen_at: str  # ISO timestamp when first seen in this run
    sample_listing_ids: List[str]  # Sample listing IDs for debugging


class UnmatchedCpuReporter:
    """
    Tracks and reports unmatched CPUs from auction listings.

    Maintains a count of affected listings per unmatched CPU and generates
    the `unmatched-cpus.json` report each run.
    """

    def __init__(self):
        """Initialize the unmatched CPU reporter."""
        self.unmatched_cpus: Dict[str, UnmatchedCpuEntry] = {}
        self.run_timestamp = datetime.now(UTC).isoformat()

    def process_listing(self, listing_id: str, cpu_match: BenchmarkMatch) -> None:
        """
        Process a single listing's CPU match result.

        If the CPU didn't match, adds it to the unmatched tracking.

        Args:
            listing_id: The listing ID
            cpu_match: The BenchmarkMatch result from CpuMatcher
        """
        if cpu_match.matched:
            # CPU matched successfully - nothing to track
            return

        cpu_raw = cpu_match.cpu_raw

        # Skip empty CPU strings
        if not cpu_raw or not cpu_raw.strip():
            logger.debug(f"Skipping empty CPU string for listing {listing_id}")
            return

        # Track this unmatched CPU
        if cpu_raw not in self.unmatched_cpus:
            self.unmatched_cpus[cpu_raw] = UnmatchedCpuEntry(
                cpu_raw=cpu_raw,
                affected_count=0,
                first_seen_at=self.run_timestamp,
                sample_listing_ids=[],
            )

        # Increment affected count
        entry = self.unmatched_cpus[cpu_raw]
        entry.affected_count += 1

        # Add sample listing ID (keep max 5 samples per CPU)
        if len(entry.sample_listing_ids) < 5:
            entry.sample_listing_ids.append(listing_id)

        logger.debug(f"Tracked unmatched CPU '{cpu_raw}' for listing {listing_id} (count: {entry.affected_count})")

    def get_report_data(self) -> List[Dict]:
        """
        Get the unmatched CPU report data as a list of dicts.

        Returns sorted list by affected_count descending (highest-impact gaps first).

        Returns:
            List of dicts representing unmatched CPU entries
        """
        # Convert to list of dicts and sort by affected_count descending
        entries = [
            {
                "cpu_raw": entry.cpu_raw,
                "affected_count": entry.affected_count,
                "first_seen_at": entry.first_seen_at,
                "sample_listing_ids": entry.sample_listing_ids,
            }
            for entry in self.unmatched_cpus.values()
        ]

        # Sort by affected_count descending
        entries.sort(key=lambda x: x["affected_count"], reverse=True)

        return entries

    def generate_report(self, output_path: Path) -> None:
        """
        Generate the unmatched-cpus.json report file.

        Args:
            output_path: Path where to write the JSON report
        """
        report_data = {
            "generated_at": self.run_timestamp,
            "total_unmatched_cpus": len(self.unmatched_cpus),
            "unmatched_cpus": self.get_report_data(),
        }

        try:
            # Ensure parent directory exists
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(report_data, f, indent=2, ensure_ascii=False)

            logger.info(f"Generated unmatched CPU report: {output_path}")
            logger.info(f"Total unmatched CPUs: {len(self.unmatched_cpus)}")

            # Log top 5 unmatched CPUs by affected count
            top_5 = report_data["unmatched_cpus"][:5]
            if top_5:
                logger.info("Top 5 unmatched CPUs by affected count:")
                for i, entry in enumerate(top_5, 1):
                    logger.info(f"  {i}. {entry['cpu_raw']} ({entry['affected_count']} listings)")

        except Exception as e:
            logger.error(f"Failed to generate unmatched CPU report: {e}")
            raise

    def get_unmatched_count(self) -> int:
        """Return the total number of unique unmatched CPUs."""
        return len(self.unmatched_cpus)

    def get_total_affected_listings(self) -> int:
        """Return the total number of listings affected by unmatched CPUs."""
        return sum(entry.affected_count for entry in self.unmatched_cpus.values())

    def has_unmatched_cpus(self) -> bool:
        """Check if there are any unmatched CPUs."""
        return len(self.unmatched_cpus) > 0


def process_listings_batch(listings: List[dict], cpu_matcher, reporter: UnmatchedCpuReporter) -> None:
    """
    Process a batch of listings and track unmatched CPUs.

    Args:
        listings: List of listing dicts with cpu_raw field
        cpu_matcher: CpuMatcher instance
        reporter: UnmatchedCpuReporter instance
    """
    for listing in listings:
        listing_id = listing.get("listing_id", "unknown")
        cpu_raw = listing.get("cpu_raw", "")

        # Match the CPU
        cpu_match = cpu_matcher.match_cpu(cpu_raw)

        # Track if unmatched
        reporter.process_listing(listing_id, cpu_match)
