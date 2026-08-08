"""
CPU Benchmark Matcher

Normalizes raw CPU strings from Hetzner auction listings and matches them
against PassMark benchmark scores via reference table, aliases, and overrides.

This is the core logic that prevents false-positive matches (Risk Register R1)
and ensures unmatched CPUs are surfaced, not guessed.
"""

import csv
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkMatch:
    """Result of a CPU benchmark match attempt."""

    cpu_raw: str  # Original CPU string from Hetzner
    cpu_normalized: Optional[str]  # Normalized/canonical CPU name
    passmark_id: Optional[int]  # PassMark CPU ID
    single_thread_score: Optional[int]  # PassMark single-thread score
    multi_thread_score: Optional[int]  # PassMark multi-thread score
    cores: Optional[int]  # Physical core count (from PassMark reference data)
    threads: Optional[int]  # Thread count (from PassMark reference data)
    matched: bool  # Whether a match was found
    match_method: Optional[str]  # How the match was made: "direct", "alias", "override"


class CpuMatcher:
    """
    Matches raw CPU strings to PassMark benchmark scores.

    Matching priority (as per plan.md Benchmark Strategy):
    1. Direct match against reference table
    2. Alias match via aliases.csv
    3. Manual override via overrides.csv
    4. No match (benchmark_matched = false)
    """

    def __init__(self, benchmark_map_dir: Optional[Path] = None):
        """
        Initialize the CPU matcher.

        Args:
            benchmark_map_dir: Path to benchmark-map directory. If None, uses
                             default location relative to this module.
        """
        if benchmark_map_dir is None:
            # Default to project root benchmark-map directory
            benchmark_map_dir = Path(__file__).parent.parent.parent.parent / "benchmark-map"

        self.benchmark_map_dir = Path(benchmark_map_dir)
        self.reference_table: Dict[str, dict] = {}
        self.aliases: Dict[str, str] = {}
        self.overrides: Dict[str, int] = {}

        self._load_reference_table()
        self._load_aliases()
        self._load_overrides()

    def _load_reference_table(self):
        """Load PassMark reference table from CSV."""
        reference_path = self.benchmark_map_dir / "reference.csv"

        if not reference_path.exists():
            logger.warning(f"Reference table not found at {reference_path}")
            return

        def _optional_int(value: Optional[str]) -> Optional[int]:
            # cores/threads are a later addition to the schema; older rows
            # or ones a lookup couldn't resolve may have this blank.
            if value is None or value.strip() == "":
                return None
            return int(value)

        try:
            with open(reference_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    cpu_model = row["cpu_model"]
                    self.reference_table[cpu_model] = {
                        "passmark_id": int(row["passmark_id"]),
                        "single_thread_score": int(row["single_thread_score"]),
                        "multi_thread_score": int(row["multi_thread_score"]),
                        "cores": _optional_int(row.get("cores")),
                        "threads": _optional_int(row.get("threads")),
                    }

            logger.info(f"Loaded {len(self.reference_table)} CPUs from reference table")

        except Exception as e:
            logger.error(f"Failed to load reference table: {e}")
            raise

    def _load_aliases(self):
        """Load CPU alias mappings from CSV."""
        aliases_path = self.benchmark_map_dir / "aliases.csv"

        if not aliases_path.exists():
            logger.warning(f"Aliases file not found at {aliases_path}")
            return

        try:
            with open(aliases_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Skip comments and empty lines
                    raw_pattern = row["raw_pattern"].strip()
                    if not raw_pattern or raw_pattern.startswith("#"):
                        continue

                    canonical_model = row["canonical_model"]
                    self.aliases[raw_pattern] = canonical_model

            logger.info(f"Loaded {len(self.aliases)} CPU aliases")

        except Exception as e:
            logger.error(f"Failed to load aliases: {e}")
            raise

    def _load_overrides(self):
        """Load manual CPU overrides from CSV."""
        overrides_path = self.benchmark_map_dir / "overrides.csv"

        if not overrides_path.exists():
            logger.warning(f"Overrides file not found at {overrides_path}")
            return

        try:
            with open(overrides_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Skip comments and empty lines
                    raw_cpu = row["raw_cpu"].strip()
                    if not raw_cpu or raw_cpu.startswith("#"):
                        continue

                    passmark_id = int(row["passmark_id"])
                    self.overrides[raw_cpu] = passmark_id

            logger.info(f"Loaded {len(self.overrides)} CPU overrides")

        except Exception as e:
            logger.error(f"Failed to load overrides: {e}")
            raise

    def normalize_cpu_string(self, cpu_raw: str) -> str:
        """
        Normalize a raw CPU string for matching.

        Applies standard cleaning: extra whitespace removal, case normalization,
        minor spacing fixes, etc. Does NOT apply alias mappings.

        Args:
            cpu_raw: Raw CPU string from Hetzner listing

        Returns:
            Normalized CPU string
        """
        if not cpu_raw:
            return ""

        # Basic cleaning
        normalized = cpu_raw.strip()

        # Normalize whitespace: multiple spaces to single space
        normalized = re.sub(r"\s+", " ", normalized)

        # Trim surrounding whitespace
        normalized = normalized.strip()

        return normalized

    def match_cpu(self, cpu_raw: str) -> BenchmarkMatch:
        """
        Match a raw CPU string to PassMark benchmark scores.

        Follows the matching priority defined in Benchmark Strategy:
        1. Direct match against reference table
        2. Alias match via aliases.csv
        3. Manual override via overrides.csv
        4. No match (returns matched=False)

        Args:
            cpu_raw: Raw CPU string from Hetzner listing

        Returns:
            BenchmarkMatch with match results
        """
        if not cpu_raw:
            return BenchmarkMatch(
                cpu_raw=cpu_raw,
                cpu_normalized=None,
                passmark_id=None,
                single_thread_score=None,
                multi_thread_score=None,
                cores=None,
                threads=None,
                matched=False,
                match_method=None,
            )

        # Step 1: Try direct match
        if cpu_raw in self.reference_table:
            data = self.reference_table[cpu_raw]
            logger.debug(f"Direct match found: {cpu_raw}")
            return BenchmarkMatch(
                cpu_raw=cpu_raw,
                cpu_normalized=cpu_raw,
                passmark_id=data["passmark_id"],
                single_thread_score=data["single_thread_score"],
                multi_thread_score=data["multi_thread_score"],
                cores=data["cores"],
                threads=data["threads"],
                matched=True,
                match_method="direct",
            )

        # Step 2: Try alias match
        if cpu_raw in self.aliases:
            canonical = self.aliases[cpu_raw]
            if canonical in self.reference_table:
                data = self.reference_table[canonical]
                logger.debug(f"Alias match found: {cpu_raw} -> {canonical}")
                return BenchmarkMatch(
                    cpu_raw=cpu_raw,
                    cpu_normalized=canonical,
                    passmark_id=data["passmark_id"],
                    single_thread_score=data["single_thread_score"],
                    multi_thread_score=data["multi_thread_score"],
                    cores=data["cores"],
                    threads=data["threads"],
                    matched=True,
                    match_method="alias",
                )

        # Step 3: Try manual override
        if cpu_raw in self.overrides:
            passmark_id = self.overrides[cpu_raw]
            # Find the CPU data by passmark_id
            for cpu_model, data in self.reference_table.items():
                if data["passmark_id"] == passmark_id:
                    logger.debug(f"Override match found: {cpu_raw} -> {cpu_model}")
                    return BenchmarkMatch(
                        cpu_raw=cpu_raw,
                        cpu_normalized=cpu_model,
                        passmark_id=passmark_id,
                        single_thread_score=data["single_thread_score"],
                        multi_thread_score=data["multi_thread_score"],
                        cores=data["cores"],
                        threads=data["threads"],
                        matched=True,
                        match_method="override",
                    )

            logger.warning(f"Override found for {cpu_raw} but PassMark ID {passmark_id} not in reference table")

        # Step 4: No match found
        logger.debug(f"No match found for CPU: {cpu_raw}")
        return BenchmarkMatch(
            cpu_raw=cpu_raw,
            cpu_normalized=None,
            passmark_id=None,
            single_thread_score=None,
            multi_thread_score=None,
            cores=None,
            threads=None,
            matched=False,
            match_method=None,
        )

    def get_reference_table_size(self) -> int:
        """Return the number of CPUs in the reference table."""
        return len(self.reference_table)

    def get_aliases_count(self) -> int:
        """Return the number of aliases defined."""
        return len(self.aliases)

    def get_overrides_count(self) -> int:
        """Return the number of manual overrides defined."""
        return len(self.overrides)
