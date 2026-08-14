"""
Cost Metrics Enricher

Computes derived cost metrics for Hetzner auction listings.

This is the enrichment phase that combines raw listing data with CPU benchmark
match results to compute per-unit pricing metrics.

Derived metrics computed:
- price_effective_monthly: Base price + primary IPv4 + setup fee (full-value, non-amortized)
- price_per_benchmark_point_single: Price / single-thread score (NULL if unmatched)
- price_per_benchmark_point_multi: Price / multi-thread score (NULL if unmatched)
- price_per_gb_ram: Price per GB of RAM
- price_per_tb_disk: Price per TB of total disk capacity

Edge cases handled:
- NULL benchmark-point metrics when benchmark_matched = false
- No divide-by-zero errors (returns None instead)
- Zero RAM or disk capacity handled gracefully
"""

import logging
from dataclasses import dataclass
from typing import Optional

from pipeline.cpu_matcher import BenchmarkMatch
from pipeline.fetcher import RawListing, DiskSpec

logger = logging.getLogger(__name__)


@dataclass
class EnrichedListing:
    """
    Enriched auction listing with CPU match results and derived cost metrics.

    Extends RawListing data with:
    - CPU benchmark match results
    - Derived cost metrics (EUR per unit)
    """

    # Core fields from RawListing
    listing_id: str
    datacenter: str
    location: str
    available_from: str | None
    cpu_raw: str
    cpu_normalized: Optional[str]  # Normalized CPU name (or None if unmatched)
    benchmark_matched: bool  # Whether CPU matched to benchmark
    passmark_id: Optional[int]  # PassMark CPU ID (or None if unmatched)
    single_thread_score: Optional[int]  # Single-thread score (or None if unmatched)
    multi_thread_score: Optional[int]  # Multi-thread score (or None if unmatched)
    cpu_cores: Optional[int]  # Physical core count (or None if unmatched/unresolved)
    cpu_threads: Optional[int]  # Thread count (or None if unmatched/unresolved)
    benchmark_match_method: Optional[str]  # How match was made: direct/alias/override
    ram_gb: int
    ram_ecc: bool
    disks: list[DiskSpec]
    uplink_speed: int
    price_base: int  # EUR cents
    price_setup_fee: int  # EUR cents
    fetched_at: any  # datetime

    # Derived cost metrics (EUR cents per unit, or None)
    price_effective_monthly: Optional[int]  # price_base + price_setup_fee
    price_per_benchmark_point_single: Optional[float]  # EUR cents per single-thread point
    price_per_benchmark_point_multi: Optional[float]  # EUR cents per multi-thread point
    price_per_gb_ram: Optional[float]  # EUR cents per GB RAM
    price_per_tb_disk: Optional[float]  # EUR cents per TB disk capacity
    price_ipv4_monthly: int = 0  # EUR cents for the primary IPv4 address

    # v2 historical-value fields (docs/plan/plan.md "Historical stats: value
    # percentile & all-time-low"). Left at these defaults by the enricher
    # itself -- CostMetricsEnricher.enrich_listing() has no access to other
    # listings or the config_history.parquet state it depends on. They're
    # filled in by pipeline.history_store.compute_percentiles() as a
    # separate post-processing pass in main.py's run_once(), mutating the
    # already-constructed EnrichedListing objects in place. Defaulted (unlike
    # every other field on this dataclass, deliberately not defaulted) so
    # adding this whole feature layer doesn't force every existing
    # EnrichedListing(...) call site across the test suite to be touched.
    price_percentile_vs_history: Optional[float] = None  # 0.0-1.0, None if no history yet
    price_per_benchmark_point_single_percentile_vs_history: Optional[float] = None
    price_per_benchmark_point_multi_percentile_vs_history: Optional[float] = None
    is_all_time_low: bool = False
    history_sample_size: int = 0
    history_cohort_fallback: bool = False


class CostMetricsEnricher:
    """
    Enriches raw auction listings with CPU match results and derived cost metrics.

    This is the core enrichment logic that combines:
    - Raw listing data (from HetznerAuctionFetcher)
    - CPU benchmark match results (from CpuMatcher)
    - Derived cost metrics computed from both
    """

    def enrich_listing(self, listing: RawListing, cpu_match: BenchmarkMatch) -> EnrichedListing:
        """
        Enrich a single listing with CPU match results and cost metrics.

        Args:
            listing: RawListing from HetznerAuctionFetcher
            cpu_match: BenchmarkMatch from CpuMatcher.match_cpu()

        Returns:
            EnrichedListing with all derived metrics computed
        """
        # Compute derived cost metrics
        price_effective_monthly = self._compute_price_effective_monthly(
            listing.price_base, listing.price_ipv4_monthly, listing.price_setup_fee
        )

        price_per_benchmark_point_single = self._compute_price_per_benchmark_point_single(
            price_effective_monthly, cpu_match.single_thread_score, cpu_match.matched
        )

        price_per_benchmark_point_multi = self._compute_price_per_benchmark_point_multi(
            price_effective_monthly, cpu_match.multi_thread_score, cpu_match.matched
        )

        price_per_gb_ram = self._compute_price_per_gb_ram(
            price_effective_monthly, listing.ram_gb
        )

        total_disk_capacity_tb = self._compute_total_disk_capacity_tb(listing.disks)
        price_per_tb_disk = self._compute_price_per_tb_disk(
            price_effective_monthly, total_disk_capacity_tb
        )

        return EnrichedListing(
            # Core fields from RawListing
            listing_id=listing.listing_id,
            datacenter=listing.datacenter,
            location=listing.location,
            available_from=listing.available_from,
            cpu_raw=listing.cpu_raw,
            cpu_normalized=cpu_match.cpu_normalized,
            benchmark_matched=cpu_match.matched,
            passmark_id=cpu_match.passmark_id,
            single_thread_score=cpu_match.single_thread_score,
            multi_thread_score=cpu_match.multi_thread_score,
            cpu_cores=cpu_match.cores,
            cpu_threads=cpu_match.threads,
            benchmark_match_method=cpu_match.match_method,
            ram_gb=listing.ram_gb,
            ram_ecc=listing.ram_ecc,
            disks=listing.disks,
            uplink_speed=listing.uplink_speed,
            price_base=listing.price_base,
            price_ipv4_monthly=listing.price_ipv4_monthly,
            price_setup_fee=listing.price_setup_fee,
            fetched_at=listing.fetched_at,
            # Derived cost metrics
            price_effective_monthly=price_effective_monthly,
            price_per_benchmark_point_single=price_per_benchmark_point_single,
            price_per_benchmark_point_multi=price_per_benchmark_point_multi,
            price_per_gb_ram=price_per_gb_ram,
            price_per_tb_disk=price_per_tb_disk,
        )

    def _compute_price_effective_monthly(
        self, price_base: int, price_ipv4_monthly: int, price_setup_fee: int
    ) -> int:
        """
        Compute first-month price (base + IPv4 + setup fee, full-value non-amortized).

        Args:
            price_base: Monthly base price in EUR cents
            price_ipv4_monthly: Monthly primary IPv4 price in EUR cents
            price_setup_fee: One-time setup fee in EUR cents

        Returns:
            Effective monthly price in EUR cents
        """
        return price_base + price_ipv4_monthly + price_setup_fee

    def _compute_price_per_benchmark_point_single(
        self, price_effective_monthly: int, single_thread_score: Optional[int], benchmark_matched: bool
    ) -> Optional[float]:
        """
        Compute price per single-thread benchmark point.

        Returns None if:
        - benchmark_matched is false (CPU has no benchmark data)
        - single_thread_score is None or zero (avoid divide-by-zero)

        Args:
            price_effective_monthly: Effective monthly price in EUR cents
            single_thread_score: Single-thread benchmark score (or None)
            benchmark_matched: Whether CPU matched to benchmark

        Returns:
            EUR cents per single-thread point, or None if not applicable
        """
        # Return None if CPU didn't match to benchmark
        if not benchmark_matched:
            return None

        # Return None if no score or zero score (avoid divide-by-zero)
        if single_thread_score is None or single_thread_score == 0:
            return None

        return price_effective_monthly / single_thread_score

    def _compute_price_per_benchmark_point_multi(
        self, price_effective_monthly: int, multi_thread_score: Optional[int], benchmark_matched: bool
    ) -> Optional[float]:
        """
        Compute price per multi-thread benchmark point.

        Returns None if:
        - benchmark_matched is false (CPU has no benchmark data)
        - multi_thread_score is None or zero (avoid divide-by-zero)

        Args:
            price_effective_monthly: Effective monthly price in EUR cents
            multi_thread_score: Multi-thread benchmark score (or None)
            benchmark_matched: Whether CPU matched to benchmark

        Returns:
            EUR cents per multi-thread point, or None if not applicable
        """
        # Return None if CPU didn't match to benchmark
        if not benchmark_matched:
            return None

        # Return None if no score or zero score (avoid divide-by-zero)
        if multi_thread_score is None or multi_thread_score == 0:
            return None

        return price_effective_monthly / multi_thread_score

    def _compute_price_per_gb_ram(self, price_effective_monthly: int, ram_gb: int) -> Optional[float]:
        """
        Compute price per GB of RAM.

        Returns None if ram_gb is zero (avoid divide-by-zero).

        Args:
            price_effective_monthly: Effective monthly price in EUR cents
            ram_gb: RAM capacity in GB

        Returns:
            EUR cents per GB RAM, or None if ram_gb is zero
        """
        if ram_gb == 0:
            return None

        return price_effective_monthly / ram_gb

    def _compute_total_disk_capacity_tb(self, disks: list[DiskSpec]) -> float:
        """
        Compute total disk capacity in terabytes.

        Args:
            disks: List of DiskSpec objects

        Returns:
            Total capacity in TB (sum of all disks)
        """
        total_gb = sum(disk.capacity_gb * disk.count for disk in disks)
        return total_gb / 1000  # Convert GB to TB

    def _compute_price_per_tb_disk(
        self, price_effective_monthly: int, total_disk_capacity_tb: float
    ) -> Optional[float]:
        """
        Compute price per TB of disk capacity.

        Returns None if total_disk_capacity_tb is zero (avoid divide-by-zero).

        Args:
            price_effective_monthly: Effective monthly price in EUR cents
            total_disk_capacity_tb: Total disk capacity in TB

        Returns:
            EUR cents per TB disk, or None if capacity is zero
        """
        if total_disk_capacity_tb == 0:
            return None

        return price_effective_monthly / total_disk_capacity_tb


def enrich_listings_batch(
    listings: list[RawListing], cpu_matcher, enricher: CostMetricsEnricher | None = None
) -> list[EnrichedListing]:
    """
    Enrich a batch of listings with CPU matches and cost metrics.

    Args:
        listings: List of RawListing objects
        cpu_matcher: CpuMatcher instance
        enricher: CostMetricsEnricher instance (created if None)

    Returns:
        List of EnrichedListing objects
    """
    if enricher is None:
        enricher = CostMetricsEnricher()

    enriched = []
    for listing in listings:
        # Match CPU
        cpu_match = cpu_matcher.match_cpu(listing.cpu_raw)

        # Enrich listing
        enriched_listing = enricher.enrich_listing(listing, cpu_match)
        enriched.append(enriched_listing)

    logger.info(f"Enriched {len(enriched)} listings with cost metrics")
    return enriched
