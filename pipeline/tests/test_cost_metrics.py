"""
Test cost metrics enrichment functionality.

Tests the computation of derived cost metrics for auction listings:
- price_effective_monthly (= price_base + price_setup_fee, full-value non-amortized)
- price_per_benchmark_point_single (NULL when benchmark_matched = false)
- price_per_benchmark_point_multi (NULL when benchmark_matched = false)
- price_per_gb_ram
- price_per_tb_disk

Fixtures cover:
- Zero setup fee vs non-zero setup fee
- Matched vs unmatched CPU listings
- Various disk configurations
- Edge cases (zero values, divide-by-zero prevention)
"""

import pytest
from datetime import UTC, datetime
from pathlib import Path

from pipeline.enricher import CostMetricsEnricher, EnrichedListing, enrich_listings_batch
from pipeline.cpu_matcher import BenchmarkMatch
from pipeline.fetcher import RawListing, DiskSpec


class TestCostMetricsEnricher:
    """Test cost metrics computation."""

    @pytest.fixture
    def enricher(self):
        """Initialize cost metrics enricher."""
        return CostMetricsEnricher()

    @pytest.fixture
    def sample_listing_with_zero_setup(self):
        """Create a sample listing with zero setup fee."""
        return RawListing(
            listing_id="hetzner-001",
            datacenter="FSN1-DC3",
            location="FSN",
            available_from=None,
            cpu_raw="Intel Xeon E5-2680 v4",
            ram_gb=64,
            ram_ecc=True,
            disks=[
                DiskSpec(type="NVMe", count=2, capacity_gb=512),
            ],
            uplink_speed=1000,
            price_base=1999,  # €19.99 in cents
            price_setup_fee=0,  # Zero setup fee
            fetched_at=datetime.now(UTC),
        )

    @pytest.fixture
    def sample_listing_with_setup_fee(self):
        """Create a sample listing with non-zero setup fee."""
        return RawListing(
            listing_id="hetzner-002",
            datacenter="NBG1-DC1",
            location="NBG",
            available_from="2026-08-03T10:00:00Z",
            cpu_raw="AMD EPYC 7401P",
            ram_gb=128,
            ram_ecc=True,
            disks=[
                DiskSpec(type="SSD", count=4, capacity_gb=1000),
            ],
            uplink_speed=1000,
            price_base=2499,  # €24.99 in cents
            price_setup_fee=4999,  # €49.99 setup fee
            fetched_at=datetime.now(UTC),
        )

    @pytest.fixture
    def matched_cpu_match(self):
        """Create a matched CPU benchmark result."""
        return BenchmarkMatch(
            cpu_raw="Intel Xeon E5-2680 v4",
            cpu_normalized="Intel Xeon E5-2680 v4",
            passmark_id=5773,
            single_thread_score=2012,
            multi_thread_score=21339,
            matched=True,
            match_method="direct",
        )

    @pytest.fixture
    def unmatched_cpu_match(self):
        """Create an unmatched CPU benchmark result."""
        return BenchmarkMatch(
            cpu_raw="Unknown CPU Model X",
            cpu_normalized=None,
            passmark_id=None,
            single_thread_score=None,
            multi_thread_score=None,
            matched=False,
            match_method=None,
        )

    def test_price_effective_monthly_zero_setup(self, enricher, sample_listing_with_zero_setup, matched_cpu_match):
        """Test price_effective_monthly with zero setup fee."""
        enriched = enricher.enrich_listing(sample_listing_with_zero_setup, matched_cpu_match)

        # price_effective_monthly = price_base + price_setup_fee
        # = 1999 + 0 = 1999 (€19.99)
        assert enriched.price_effective_monthly == 1999
        assert enriched.price_effective_monthly == enriched.price_base + enriched.price_setup_fee

    def test_price_effective_monthly_with_setup_fee(self, enricher, sample_listing_with_setup_fee, matched_cpu_match):
        """Test price_effective_monthly with non-zero setup fee."""
        enriched = enricher.enrich_listing(sample_listing_with_setup_fee, matched_cpu_match)

        # price_effective_monthly = price_base + price_setup_fee
        # = 2499 + 4999 = 7498 (€74.98)
        assert enriched.price_effective_monthly == 7498
        assert enriched.price_effective_monthly == enriched.price_base + enriched.price_setup_fee

    def test_price_per_benchmark_point_single_matched(self, enricher, sample_listing_with_zero_setup, matched_cpu_match):
        """Test price_per_benchmark_point_single for matched CPU."""
        enriched = enricher.enrich_listing(sample_listing_with_zero_setup, matched_cpu_match)

        # price_per_benchmark_point_single = price_effective_monthly / single_thread_score
        # = 1999 / 2012 ≈ 0.994 EUR cents per point
        assert enriched.price_per_benchmark_point_single is not None
        assert abs(enriched.price_per_benchmark_point_single - (1999 / 2012)) < 0.001

    def test_price_per_benchmark_point_multi_matched(self, enricher, sample_listing_with_zero_setup, matched_cpu_match):
        """Test price_per_benchmark_point_multi for matched CPU."""
        enriched = enricher.enrich_listing(sample_listing_with_zero_setup, matched_cpu_match)

        # price_per_benchmark_point_multi = price_effective_monthly / multi_thread_score
        # = 1999 / 21339 ≈ 0.094 EUR cents per point
        assert enriched.price_per_benchmark_point_multi is not None
        assert abs(enriched.price_per_benchmark_point_multi - (1999 / 21339)) < 0.001

    def test_benchmark_point_metrics_null_for_unmatched(self, enricher, sample_listing_with_zero_setup, unmatched_cpu_match):
        """Test that benchmark-point metrics are NULL for unmatched CPUs."""
        enriched = enricher.enrich_listing(sample_listing_with_zero_setup, unmatched_cpu_match)

        # When benchmark_matched = false, benchmark-point metrics should be None
        assert enriched.benchmark_matched is False
        assert enriched.price_per_benchmark_point_single is None
        assert enriched.price_per_benchmark_point_multi is None

    def test_price_per_gb_ram(self, enricher, sample_listing_with_zero_setup, matched_cpu_match):
        """Test price_per_gb_ram computation."""
        enriched = enricher.enrich_listing(sample_listing_with_zero_setup, matched_cpu_match)

        # price_per_gb_ram = price_effective_monthly / ram_gb
        # = 1999 / 64 ≈ 31.2 EUR cents per GB
        assert enriched.price_per_gb_ram is not None
        assert abs(enriched.price_per_gb_ram - (1999 / 64)) < 0.01

    def test_price_per_tb_disk_single_disk(self, enricher, sample_listing_with_zero_setup, matched_cpu_match):
        """Test price_per_tb_disk with single disk type."""
        enriched = enricher.enrich_listing(sample_listing_with_zero_setup, matched_cpu_match)

        # Total disk capacity = 2 * 512 GB = 1024 GB = 1.024 TB
        # price_per_tb_disk = 1999 / 1.024 ≈ 1952.1 EUR cents per TB
        assert enriched.price_per_tb_disk is not None
        expected_tb = (2 * 512) / 1000
        assert abs(enriched.price_per_tb_disk - (1999 / expected_tb)) < 1

    def test_price_per_tb_disk_multiple_disks(self, enricher, sample_listing_with_setup_fee, matched_cpu_match):
        """Test price_per_tb_disk with multiple disk types."""
        # Modify listing to have multiple disk types
        sample_listing_with_setup_fee.disks = [
            DiskSpec(type="SSD", count=2, capacity_gb=500),
            DiskSpec(type="HDD", count=4, capacity_gb=2000),
        ]
        # Create a matched CPU match for this listing
        cpu_match = BenchmarkMatch(
            cpu_raw="AMD EPYC 7401P",
            cpu_normalized="AMD EPYC 7401P",
            passmark_id=5055,
            single_thread_score=2044,
            multi_thread_score=20582,
            matched=True,
            match_method="direct",
        )

        enriched = enricher.enrich_listing(sample_listing_with_setup_fee, cpu_match)

        # Total disk capacity = (2 * 500) + (4 * 2000) = 1000 + 8000 = 9000 GB = 9 TB
        # price_per_tb_disk = 7498 / 9 ≈ 833.1 EUR cents per TB
        assert enriched.price_per_tb_disk is not None
        expected_tb = ((2 * 500) + (4 * 2000)) / 1000
        assert abs(enriched.price_per_tb_disk - (7498 / expected_tb)) < 1

    def test_no_divide_by_zero_zero_ram(self, enricher):
        """Test that zero RAM doesn't cause divide-by-zero."""
        listing = RawListing(
            listing_id="test-001",
            datacenter="FSN1-DC3",
            location="FSN",
            available_from=None,
            cpu_raw="Intel Xeon E5-2680 v4",
            ram_gb=0,  # Zero RAM
            ram_ecc=False,
            disks=[DiskSpec(type="SSD", count=1, capacity_gb=500)],
            uplink_speed=1000,
            price_base=1999,
            price_setup_fee=0,
            fetched_at=datetime.now(UTC),
        )

        cpu_match = BenchmarkMatch(
            cpu_raw="Intel Xeon E5-2680 v4",
            cpu_normalized="Intel Xeon E5-2680 v4",
            passmark_id=5773,
            single_thread_score=2012,
            multi_thread_score=21339,
            matched=True,
            match_method="direct",
        )

        enriched = enricher.enrich_listing(listing, cpu_match)

        # Should return None instead of causing divide-by-zero
        assert enriched.price_per_gb_ram is None
        # Other metrics should still work
        assert enriched.price_effective_monthly == 1999
        assert enriched.price_per_benchmark_point_single is not None

    def test_no_divide_by_zero_zero_disks(self, enricher):
        """Test that zero disk capacity doesn't cause divide-by-zero."""
        listing = RawListing(
            listing_id="test-002",
            datacenter="FSN1-DC3",
            location="FSN",
            available_from=None,
            cpu_raw="Intel Xeon E5-2680 v4",
            ram_gb=64,
            ram_ecc=True,
            disks=[],  # No disks
            uplink_speed=1000,
            price_base=1999,
            price_setup_fee=0,
            fetched_at=datetime.now(UTC),
        )

        cpu_match = BenchmarkMatch(
            cpu_raw="Intel Xeon E5-2680 v4",
            cpu_normalized="Intel Xeon E5-2680 v4",
            passmark_id=5773,
            single_thread_score=2012,
            multi_thread_score=21339,
            matched=True,
            match_method="direct",
        )

        enriched = enricher.enrich_listing(listing, cpu_match)

        # Should return None instead of causing divide-by-zero
        assert enriched.price_per_tb_disk is None
        # Other metrics should still work
        assert enriched.price_effective_monthly == 1999
        assert enriched.price_per_gb_ram is not None

    def test_enriched_listing_fields_preserved(self, enricher, sample_listing_with_setup_fee, matched_cpu_match):
        """Test that all original fields are preserved in enriched listing."""
        enriched = enricher.enrich_listing(sample_listing_with_setup_fee, matched_cpu_match)

        # Original RawListing fields should be preserved
        assert enriched.listing_id == sample_listing_with_setup_fee.listing_id
        assert enriched.datacenter == sample_listing_with_setup_fee.datacenter
        assert enriched.location == sample_listing_with_setup_fee.location
        assert enriched.available_from == sample_listing_with_setup_fee.available_from
        assert enriched.cpu_raw == sample_listing_with_setup_fee.cpu_raw
        assert enriched.ram_gb == sample_listing_with_setup_fee.ram_gb
        assert enriched.ram_ecc == sample_listing_with_setup_fee.ram_ecc
        assert enriched.uplink_speed == sample_listing_with_setup_fee.uplink_speed
        assert enriched.price_base == sample_listing_with_setup_fee.price_base
        assert enriched.price_setup_fee == sample_listing_with_setup_fee.price_setup_fee

        # CPU match fields should be populated
        assert enriched.cpu_normalized == matched_cpu_match.cpu_normalized
        assert enriched.benchmark_matched == matched_cpu_match.matched
        assert enriched.passmark_id == matched_cpu_match.passmark_id
        assert enriched.single_thread_score == matched_cpu_match.single_thread_score
        assert enriched.multi_thread_score == matched_cpu_match.multi_thread_score
        assert enriched.benchmark_match_method == matched_cpu_match.match_method


class TestEnrichListingsBatch:
    """Test batch enrichment functionality."""

    def test_batch_enrichment_integration(self):
        """Test enriching a batch of listings."""
        from pipeline.cpu_matcher import CpuMatcher

        # Initialize CPU matcher
        benchmark_map_dir = Path(__file__).parent.parent.parent / "benchmark-map"
        cpu_matcher = CpuMatcher(benchmark_map_dir)

        # Create sample listings (mix of matched and unmatched CPUs)
        listings = [
            RawListing(
                listing_id="hetzner-001",
                datacenter="FSN1-DC3",
                location="FSN",
                available_from=None,
                cpu_raw="Intel Xeon E5-2680 v4",  # Should match
                ram_gb=64,
                ram_ecc=True,
                disks=[DiskSpec(type="NVMe", count=2, capacity_gb=512)],
                uplink_speed=1000,
                price_base=1999,
                price_setup_fee=0,
                fetched_at=datetime.now(UTC),
            ),
            RawListing(
                listing_id="hetzner-002",
                datacenter="NBG1-DC1",
                location="NBG",
                available_from="2026-08-03T10:00:00Z",
                cpu_raw="Unknown CPU Model X",  # Should NOT match
                ram_gb=128,
                ram_ecc=True,
                disks=[DiskSpec(type="SSD", count=4, capacity_gb=1000)],
                uplink_speed=1000,
                price_base=2499,
                price_setup_fee=4999,
                fetched_at=datetime.now(UTC),
            ),
        ]

        # Enrich batch
        enriched_listings = enrich_listings_batch(listings, cpu_matcher)

        # Should return same number of listings
        assert len(enriched_listings) == 2

        # First listing should have matched CPU
        assert enriched_listings[0].listing_id == "hetzner-001"
        assert enriched_listings[0].benchmark_matched is True
        assert enriched_listings[0].price_per_benchmark_point_single is not None
        assert enriched_listings[0].price_per_benchmark_point_multi is not None

        # Second listing should have unmatched CPU
        assert enriched_listings[1].listing_id == "hetzner-002"
        assert enriched_listings[1].benchmark_matched is False
        assert enriched_listings[1].price_per_benchmark_point_single is None
        assert enriched_listings[1].price_per_benchmark_point_multi is None

        # Both should have effective monthly price computed
        assert enriched_listings[0].price_effective_monthly == 1999  # 1999 + 0
        assert enriched_listings[1].price_effective_monthly == 7498  # 2499 + 4999

    def test_fixture_with_zero_and_nonzero_setup_fees(self):
        """Test fixtures covering both zero and non-zero setup fees."""
        from pipeline.cpu_matcher import CpuMatcher

        benchmark_map_dir = Path(__file__).parent.parent.parent / "benchmark-map"
        cpu_matcher = CpuMatcher(benchmark_map_dir)

        listings = [
            # Zero setup fee
            RawListing(
                listing_id="zero-setup-001",
                datacenter="FSN1-DC3",
                location="FSN",
                available_from=None,
                cpu_raw="AMD Ryzen 9 7950X",
                ram_gb=32,
                ram_ecc=False,
                disks=[DiskSpec(type="NVMe", count=1, capacity_gb=1000)],
                uplink_speed=1000,
                price_base=2999,
                price_setup_fee=0,  # Zero setup fee
                fetched_at=datetime.now(UTC),
            ),
            # Non-zero setup fee
            RawListing(
                listing_id="with-setup-002",
                datacenter="NBG1-DC1",
                location="NBG",
                available_from=None,
                cpu_raw="Intel Core i7-12700K",
                ram_gb=16,
                ram_ecc=False,
                disks=[DiskSpec(type="SSD", count=2, capacity_gb=500)],
                uplink_speed=1000,
                price_base=1599,
                price_setup_fee=2999,  # Non-zero setup fee
                fetched_at=datetime.now(UTC),
            ),
        ]

        enriched_listings = enrich_listings_batch(listings, cpu_matcher)

        # Both should have matched CPUs
        assert enriched_listings[0].benchmark_matched is True
        assert enriched_listings[1].benchmark_matched is True

        # Zero setup fee: price_effective_monthly should equal price_base
        assert enriched_listings[0].price_effective_monthly == 2999
        assert enriched_listings[0].price_effective_monthly == enriched_listings[0].price_base

        # Non-zero setup fee: price_effective_monthly should be base + setup
        assert enriched_listings[1].price_effective_monthly == 4598  # 1599 + 2999
        assert enriched_listings[1].price_effective_monthly == enriched_listings[1].price_base + enriched_listings[1].price_setup_fee

    def test_fixture_with_unmatched_listing(self):
        """Test fixture with one unmatched listing."""
        from pipeline.cpu_matcher import CpuMatcher

        benchmark_map_dir = Path(__file__).parent.parent.parent / "benchmark-map"
        cpu_matcher = CpuMatcher(benchmark_map_dir)

        listings = [
            # Matched CPU
            RawListing(
                listing_id="matched-001",
                datacenter="FSN1-DC3",
                location="FSN",
                available_from=None,
                cpu_raw="Intel Xeon E5-2680 v4",
                ram_gb=64,
                ram_ecc=True,
                disks=[DiskSpec(type="NVMe", count=2, capacity_gb=512)],
                uplink_speed=1000,
                price_base=1999,
                price_setup_fee=0,
                fetched_at=datetime.now(UTC),
            ),
            # Unmatched CPU
            RawListing(
                listing_id="unmatched-002",
                datacenter="NBG1-DC1",
                location="NBG",
                available_from=None,
                cpu_raw="Generic Future CPU 9000",
                ram_gb=128,
                ram_ecc=True,
                disks=[DiskSpec(type="SSD", count=4, capacity_gb=1000)],
                uplink_speed=1000,
                price_base=2499,
                price_setup_fee=4999,
                fetched_at=datetime.now(UTC),
            ),
        ]

        enriched_listings = enrich_listings_batch(listings, cpu_matcher)

        # First listing should match
        assert enriched_listings[0].benchmark_matched is True
        assert enriched_listings[0].price_per_benchmark_point_single is not None
        assert enriched_listings[0].price_per_benchmark_point_multi is not None

        # Second listing should NOT match
        assert enriched_listings[1].benchmark_matched is False
        assert enriched_listings[1].cpu_normalized is None
        assert enriched_listings[1].passmark_id is None
        assert enriched_listings[1].single_thread_score is None
        assert enriched_listings[1].multi_thread_score is None
        # Benchmark-point metrics should be NULL
        assert enriched_listings[1].price_per_benchmark_point_single is None
        assert enriched_listings[1].price_per_benchmark_point_multi is None
        # But other metrics should still work
        assert enriched_listings[1].price_effective_monthly is not None
        assert enriched_listings[1].price_per_gb_ram is not None
        assert enriched_listings[1].price_per_tb_disk is not None
