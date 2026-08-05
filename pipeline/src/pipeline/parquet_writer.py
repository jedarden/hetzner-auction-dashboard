"""
Parquet Writer for Hetzner Auction Listings

Serializes EnrichedListing objects to a single denormalized Parquet file.
This is Phase 3 of the pipeline: write computed listing records to Parquet.

The schema matches the Data Models specification exactly - all columns
that the client dashboard might filter/sort on are present in this one file.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from pipeline.enricher import EnrichedListing

logger = logging.getLogger(__name__)


class ParquetWriter:
    """
    Writes enriched auction listings to a Parquet file.

    This writer produces a single denormalized Parquet file with all
    columns needed for client-side filtering and sorting via DuckDB-WASM.
    """

    def __init__(self, compression: str = "snappy", row_group_size: int | None = None):
        """
        Initialize the Parquet writer.

        Args:
            compression: Compression codec (default: "snappy")
            row_group_size: Target row group size (None for PyArrow default)
        """
        self.compression = compression
        self.row_group_size = row_group_size

    def write_listings(self, listings: list[EnrichedListing], output_path: str | Path) -> None:
        """
        Write enriched listings to a Parquet file.

        Args:
            listings: List of EnrichedListing objects to write
            output_path: Path where the Parquet file will be written

        Raises:
            IOError: If writing fails
            ValueError: If listings is empty
        """
        if not listings:
            raise ValueError("Cannot write empty listings list to Parquet file")

        output_path = Path(output_path)
        logger.info(f"Writing {len(listings)} listings to {output_path}")

        # Convert listings to Arrow table
        table = self._listings_to_table(listings)

        # Write to Parquet file
        try:
            pq.write_table(
                table,
                output_path,
                compression=self.compression,
                row_group_size=self.row_group_size,
            )
            logger.info(f"Successfully wrote {len(listings)} listings to {output_path}")
        except Exception as e:
            logger.error(f"Failed to write Parquet file: {e}")
            raise IOError(f"Parquet write failed: {e}") from e

    def _listings_to_table(self, listings: list[EnrichedListing]) -> pa.Table:
        """
        Convert EnrichedListing objects to a PyArrow Table.

        The schema matches Data Models exactly - all columns are denormalized
        into a single table for efficient client-side querying.

        Returns:
            PyArrow Table with the listing data
        """
        # Extract data for each column
        data = {
            # Core identifier fields
            "listing_id": [listing.listing_id for listing in listings],
            "datacenter": [listing.datacenter for listing in listings],
            "location": [listing.location for listing in listings],
            "available_from": [listing.available_from for listing in listings],
            # CPU fields (raw + normalized + match results)
            "cpu_raw": [listing.cpu_raw for listing in listings],
            "cpu_normalized": [listing.cpu_normalized for listing in listings],
            "benchmark_matched": [listing.benchmark_matched for listing in listings],
            "passmark_id": [listing.passmark_id for listing in listings],
            "single_thread_score": [listing.single_thread_score for listing in listings],
            "multi_thread_score": [listing.multi_thread_score for listing in listings],
            "benchmark_match_method": [listing.benchmark_match_method for listing in listings],
            # Hardware specs
            "ram_gb": [listing.ram_gb for listing in listings],
            "ram_ecc": [listing.ram_ecc for listing in listings],
            "uplink_speed": [listing.uplink_speed for listing in listings],
            # Pricing (EUR cents)
            "price_base": [listing.price_base for listing in listings],
            "price_setup_fee": [listing.price_setup_fee for listing in listings],
            "price_effective_monthly": [listing.price_effective_monthly for listing in listings],
            # Derived cost metrics (EUR cents per unit)
            "price_per_benchmark_point_single": [
                listing.price_per_benchmark_point_single for listing in listings
            ],
            "price_per_benchmark_point_multi": [
                listing.price_per_benchmark_point_multi for listing in listings
            ],
            "price_per_gb_ram": [listing.price_per_gb_ram for listing in listings],
            "price_per_tb_disk": [listing.price_per_tb_disk for listing in listings],
            # Timestamp
            "fetched_at": [self._serialize_datetime(listing.fetched_at) for listing in listings],
            # Disks (list of structs)
            "disks": [self._serialize_disks(listing.disks) for listing in listings],
        }

        # Build Arrow table with explicit schema
        schema = self._build_schema()
        arrays = [pa.array(data[name], type=schema.field(name).type) for name in schema.names]

        return pa.Table.from_arrays(arrays, schema=schema)

    def _build_schema(self) -> pa.Schema:
        """
        Build the PyArrow schema for the listings table.

        The schema matches Data Models exactly - all columns are typed
        appropriately for DuckDB-WASM consumption.

        Returns:
            PyArrow Schema object
        """
        # Disk struct type: LIST<STRUCT<type: string, count: int, capacity_gb: int>>
        disk_struct = pa.struct([
            pa.field("type", pa.string()),
            pa.field("count", pa.int32()),
            pa.field("capacity_gb", pa.int32()),
        ])
        disk_list = pa.list_(disk_struct)

        return pa.schema([
            # Core identifier fields
            pa.field("listing_id", pa.string()),
            pa.field("datacenter", pa.string()),
            pa.field("location", pa.string()),
            pa.field("available_from", pa.string()),  # ISO datetime or None
            # CPU fields (raw + normalized + match results)
            pa.field("cpu_raw", pa.string()),
            pa.field("cpu_normalized", pa.string()),  # None if unmatched
            pa.field("benchmark_matched", pa.bool_()),
            pa.field("passmark_id", pa.int32()),  # None if unmatched
            pa.field("single_thread_score", pa.int32()),  # None if unmatched
            pa.field("multi_thread_score", pa.int32()),  # None if unmatched
            pa.field("benchmark_match_method", pa.string()),  # None if unmatched
            # Hardware specs
            pa.field("ram_gb", pa.int32()),
            pa.field("ram_ecc", pa.bool_()),
            pa.field("uplink_speed", pa.int32()),  # Mbit/s
            # Pricing (EUR cents)
            pa.field("price_base", pa.int32()),
            pa.field("price_setup_fee", pa.int32()),
            pa.field("price_effective_monthly", pa.int32()),
            # Derived cost metrics (EUR cents per unit, nullable for division-by-zero)
            pa.field("price_per_benchmark_point_single", pa.float64()),
            pa.field("price_per_benchmark_point_multi", pa.float64()),
            pa.field("price_per_gb_ram", pa.float64()),
            pa.field("price_per_tb_disk", pa.float64()),
            # Timestamp
            pa.field("fetched_at", pa.string()),  # ISO datetime string
            # Disks (list of structs)
            pa.field("disks", disk_list),
        ])

    def _serialize_disks(self, disks: list) -> list[dict[str, Any]]:
        """
        Convert DiskSpec objects to dictionaries for Arrow serialization.

        Args:
            disks: List of DiskSpec objects

        Returns:
            List of dictionaries with disk data
        """
        return [{"type": disk.type, "count": disk.count, "capacity_gb": disk.capacity_gb} for disk in disks]

    def _serialize_datetime(self, dt: datetime | None) -> str | None:
        """
        Convert datetime to ISO string for Parquet storage.

        Args:
            dt: Datetime object or None

        Returns:
            ISO datetime string or None
        """
        if dt is None:
            return None
        return dt.isoformat()


def write_listings_to_parquet(
    listings: list[EnrichedListing],
    output_path: str | Path,
    compression: str = "snappy",
    row_group_size: int | None = None,
) -> None:
    """
    Convenience function to write listings to Parquet.

    Args:
        listings: List of EnrichedListing objects to write
        output_path: Path where the Parquet file will be written
        compression: Compression codec (default: "snappy")
        row_group_size: Target row group size (None for PyArrow default)

    Raises:
        IOError: If writing fails
        ValueError: If listings is empty
    """
    writer = ParquetWriter(compression=compression, row_group_size=row_group_size)
    writer.write_listings(listings, output_path)