"""
Unit tests for Parquet Writer

Tests the Parquet writer that serializes EnrichedListing objects to a denormalized
Parquet file for consumption by DuckDB-WASM.
"""

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pyarrow.parquet as pq
from pipeline.cpu_matcher import BenchmarkMatch
from pipeline.enricher import EnrichedListing
from pipeline.fetcher import DiskSpec
from pipeline.parquet_writer import ParquetWriter, write_listings_to_parquet


class TestParquetWriterBasics:
    """Test basic Parquet writer functionality."""

    def test_write_empty_listings_raises_error(self):
        """Writing an empty listings list should raise ValueError."""
        writer = ParquetWriter()
        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            with pytest.raises(ValueError, match="Cannot write empty listings"):
                writer.write_listings([], tmp.name)

    def test_write_single_listing(self):
        """Writing a single listing should produce valid Parquet."""
        listing = self._make_sample_listing()
        writer = ParquetWriter()

        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            writer.write_listings([listing], tmp.name)

            # Verify file exists and is readable
            assert Path(tmp.name).exists()
            table = pq.read_table(tmp.name)
            assert len(table) == 1

    def test_write_multiple_listings(self):
        """Writing multiple listings should preserve all rows."""
        listings = [
            self._make_sample_listing(listing_id=f"listing-{i}")
            for i in range(10)
        ]
        writer = ParquetWriter()

        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            writer.write_listings(listings, tmp.name)

            table = pq.read_table(tmp.name)
            assert len(table) == 10

    def test_convenience_function(self):
        """The convenience function should work like the class."""
        listings = [self._make_sample_listing()]

        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            write_listings_to_parquet(listings, tmp.name)

            table = pq.read_table(tmp.name)
            assert len(table) == 1


class TestParquetSchema:
    """Test that the Parquet schema matches Data Models specification."""

    def test_schema_has_all_required_columns(self):
        """All required columns should be present in the schema."""
        listing = self._make_sample_listing()
        writer = ParquetWriter()

        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            writer.write_listings([listing], tmp.name)
            table = pq.read_table(tmp.name)

            # Check for all required column names
            required_columns = [
                "listing_id",
                "datacenter",
                "location",
                "available_from",
                "cpu_raw",
                "cpu_normalized",
                "benchmark_matched",
                "passmark_id",
                "single_thread_score",
                "multi_thread_score",
                "benchmark_match_method",
                "ram_gb",
                "ram_ecc",
                "uplink_speed",
                "price_base",
                "price_setup_fee",
                "price_effective_monthly",
                "price_per_benchmark_point_single",
                "price_per_benchmark_point_multi",
                "price_per_gb_ram",
                "price_per_tb_disk",
                "fetched_at",
                "disks",
            ]

            assert set(table.column_names) == set(required_columns)

    def test_column_types_are_correct(self):
        """Column types should match the Data Models specification."""
        listing = self._make_sample_listing()
        writer = ParquetWriter()

        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            writer.write_listings([listing], tmp.name)
            table = pq.read_table(tmp.name)

            # Check key column types
            assert table.schema.field("listing_id").type == "string"
            assert table.schema.field("benchmark_matched").type == "bool"
            assert table.schema.field("ram_gb").type == "int32"
            assert table.schema.field("price_base").type == "int32"
            assert table.schema.field("price_per_benchmark_point_multi").type == "float64"


class TestNullHandling:
    """Test proper handling of nullable fields."""

    def test_unmatched_cpu_has_null_scores(self):
        """Unmatched CPUs should have NULL benchmark scores."""
        listing = self._make_sample_listing(
            benchmark_matched=False,
            passmark_id=None,
            single_thread_score=None,
            multi_thread_score=None,
            cpu_normalized=None,
            benchmark_match_method=None,
        )

        writer = ParquetWriter()

        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            writer.write_listings([listing], tmp.name)
            table = pq.read_table(tmp.name)

            # Verify NULL values
            assert table.column("passmark_id")[0].is_valid is False
            assert table.column("single_thread_score")[0].is_valid is False
            assert table.column("multi_thread_score")[0].is_valid is False
            assert table.column("cpu_normalized")[0].as_py() is None
            assert table.column("benchmark_match_method")[0].as_py() is None

    def test_zero_division_metrics_are_null(self):
        """Metrics that would divide by zero should be NULL."""
        listing = self._make_sample_listing(
            ram_gb=0,  # Will cause price_per_gb_ram to be NULL
        )

        writer = ParquetWriter()

        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            writer.write_listings([listing], tmp.name)
            table = pq.read_table(tmp.name)

            # price_per_gb_ram should be NULL due to zero RAM
            assert table.column("price_per_gb_ram")[0].is_valid is False

    def test_available_from_can_be_null(self):
        """available_from field can be None (immediately available)."""
        listing = self._make_sample_listing(available_from=None)

        writer = ParquetWriter()

        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            writer.write_listings([listing], tmp.name)
            table = pq.read_table(tmp.name)

            assert table.column("available_from")[0].as_py() is None


class TestDisksField:
    """Test the complex disks field (list of structs)."""

    def test_single_disk(self):
        """A single disk should be stored as a list with one struct."""
        listing = self._make_sample_listing(
            disks=[DiskSpec(type="NVMe", count=2, capacity_gb=480)]
        )

        writer = ParquetWriter()

        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            writer.write_listings([listing], tmp.name)
            table = pq.read_table(tmp.name)

            disks_value = table.column("disks")[0].as_py()
            assert len(disks_value) == 1
            assert disks_value[0] == {"type": "NVMe", "count": 2, "capacity_gb": 480}

    def test_multiple_disks(self):
        """Multiple disks should be stored as a list with multiple structs."""
        listing = self._make_sample_listing(
            disks=[
                DiskSpec(type="NVMe", count=2, capacity_gb=480),
                DiskSpec(type="HDD", count=4, capacity_gb=2048),
            ]
        )

        writer = ParquetWriter()

        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            writer.write_listings([listing], tmp.name)
            table = pq.read_table(tmp.name)

            disks_value = table.column("disks")[0].as_py()
            assert len(disks_value) == 2
            assert disks_value[0] == {"type": "NVMe", "count": 2, "capacity_gb": 480}
            assert disks_value[1] == {"type": "HDD", "count": 4, "capacity_gb": 2048}

    def test_empty_disks_list(self):
        """An empty disks list should be stored correctly."""
        listing = self._make_sample_listing(disks=[])

        writer = ParquetWriter()

        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            writer.write_listings([listing], tmp.name)
            table = pq.read_table(tmp.name)

            disks_value = table.column("disks")[0].as_py()
            assert len(disks_value) == 0


class TestDataIntegrity:
    """Test that data is written and read back correctly."""

    def test_all_fields_preserved(self):
        """All listing fields should be preserved in round-trip."""
        original = self._make_sample_listing(
            listing_id="test-listing-123",
            datacenter="FSN1-DC3",
            location="FSN",
            cpu_raw="Intel Xeon E5-2680 v4",
            cpu_normalized="Intel Xeon E5-2680 v4",
            benchmark_matched=True,
            passmark_id=1234,
            single_thread_score=1500,
            multi_thread_score=8000,
            benchmark_match_method="direct",
            ram_gb=64,
            ram_ecc=True,
            uplink_speed=1000,
            price_base=2999,  # €29.99
            price_setup_fee=0,
        )

        writer = ParquetWriter()

        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            writer.write_listings([original], tmp.name)
            table = pq.read_table(tmp.name)

            # Verify key fields
            assert table.column("listing_id")[0].as_py() == "test-listing-123"
            assert table.column("datacenter")[0].as_py() == "FSN1-DC3"
            assert table.column("location")[0].as_py() == "FSN"
            assert table.column("cpu_raw")[0].as_py() == "Intel Xeon E5-2680 v4"
            assert table.column("ram_gb")[0].as_py() == 64
            assert table.column("ram_ecc")[0].as_py() is True
            assert table.column("price_base")[0].as_py() == 2999

    def test_derived_metrics_computed_correctly(self):
        """Derived cost metrics should be computed correctly."""
        listing = self._make_sample_listing(
            price_base=2999,  # €29.99
            price_setup_fee=4999,  # €49.99
            benchmark_matched=True,
            single_thread_score=1500,
            multi_thread_score=8000,
            ram_gb=64,
            disks=[DiskSpec(type="NVMe", count=2, capacity_gb=480)],  # 960 GB = 0.96 TB
        )

        writer = ParquetWriter()

        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            writer.write_listings([listing], tmp.name)
            table = pq.read_table(tmp.name)

            # price_effective_monthly = 2999 + 4999 = 7998 (€79.98)
            assert table.column("price_effective_monthly")[0].as_py() == 7998

            # price_per_benchmark_point_single = 7998 / 1500 ≈ 5.33
            assert abs(table.column("price_per_benchmark_point_single")[0].as_py() - 5.332) < 0.01

            # price_per_benchmark_point_multi = 7998 / 8000 ≈ 1.00
            assert abs(table.column("price_per_benchmark_point_multi")[0].as_py() - 0.99975) < 0.01

            # price_per_gb_ram = 7998 / 64 ≈ 124.97
            assert abs(table.column("price_per_gb_ram")[0].as_py() - 124.97) < 0.01

            # price_per_tb_disk = 7998 / 0.96 ≈ 8331.25
            assert abs(table.column("price_per_tb_disk")[0].as_py() - 8331.25) < 0.01


class TestCompressionOptions:
    """Test compression and row group size options."""

    def test_snappy_compression(self):
        """Snappy compression should work."""
        listing = self._make_sample_listing()
        writer = ParquetWriter(compression="snappy")

        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            writer.write_listings([listing], tmp.name)

            # Verify file exists and is readable
            assert Path(tmp.name).exists()
            table = pq.read_table(tmp.name)
            assert len(table) == 1

    def test_gzip_compression(self):
        """GZIP compression should work."""
        listing = self._make_sample_listing()
        writer = ParquetWriter(compression="gzip")

        with tempfile.NamedTemporaryFile(suffix=".parquet") as tmp:
            writer.write_listings([listing], tmp.name)

            # Verify file exists and is readable
            assert Path(tmp.name).exists()
            table = pq.read_table(tmp.name)
            assert len(table) == 1


class TestErrorHandling:
    """Test error handling edge cases."""

    def test_write_failure_raises_ioerror(self):
        """Write failures should raise IOError."""
        listing = self._make_sample_listing()
        writer = ParquetWriter()

        # Try to write to an invalid path
        with pytest.raises(IOError):
            writer.write_listings([listing], "/nonexistent/path/file.parquet")


# Fixtures and helper methods

def _make_sample_listing(
    listing_id="test-listing-1",
    datacenter="FSN1-DC3",
    location="FSN",
    available_from="2026-08-02T12:00:00Z",
    cpu_raw="Intel Xeon E5-2680 v4",
    cpu_normalized="Intel Xeon E5-2680 v4",
    benchmark_matched=True,
    passmark_id=1234,
    single_thread_score=1500,
    multi_thread_score=8000,
    benchmark_match_method="direct",
    ram_gb=64,
    ram_ecc=True,
    uplink_speed=1000,
    price_base=2999,
    price_setup_fee=0,
    disks=None,
    fetched_at=None,
) -> EnrichedListing:
    """Helper to create a sample EnrichedListing for testing."""
    if disks is None:
        disks = [DiskSpec(type="NVMe", count=2, capacity_gb=480)]

    if fetched_at is None:
        fetched_at = datetime.now(UTC)

    # Create listing with derived metrics
    from pipeline.enricher import CostMetricsEnricher

    raw_listing = _make_raw_listing(
        listing_id=listing_id,
        datacenter=datacenter,
        location=location,
        available_from=available_from,
        cpu_raw=cpu_raw,
        ram_gb=ram_gb,
        ram_ecc=ram_ecc,
        disks=disks,
        uplink_speed=uplink_speed,
        price_base=price_base,
        price_setup_fee=price_setup_fee,
    )

    cpu_match = BenchmarkMatch(
        matched=benchmark_matched,
        cpu_normalized=cpu_normalized,
        passmark_id=passmark_id,
        single_thread_score=single_thread_score,
        multi_thread_score=multi_thread_score,
        match_method=benchmark_match_method,
    )

    enricher = CostMetricsEnricher()
    return enricher.enrich_listing(raw_listing, cpu_match)


def _make_raw_listing(
    listing_id,
    datacenter,
    location,
    available_from,
    cpu_raw,
    ram_gb,
    ram_ecc,
    disks,
    uplink_speed,
    price_base,
    price_setup_fee,
) -> "pipeline.fetcher.RawListing":
    """Helper to create a RawListing for testing."""
    from pipeline.fetcher import RawListing

    return RawListing(
        listing_id=listing_id,
        datacenter=datacenter,
        location=location,
        available_from=available_from,
        cpu_raw=cpu_raw,
        ram_gb=ram_gb,
        ram_ecc=ram_ecc,
        disks=disks,
        uplink_speed=uplink_speed,
        price_base=price_base,
        price_setup_fee=price_setup_fee,
        fetched_at=datetime.now(UTC),
    )