"""Persistent lifecycle history for active and no-longer-observed auction offers."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from pipeline.parquet_writer import ParquetWriter
from pipeline.history_store import build_config_signature


class ListingHistoryFetchError(Exception):
    pass


BASE_SCHEMA = ParquetWriter()._build_schema()
LISTING_HISTORY_SCHEMA = pa.schema(list(BASE_SCHEMA) + [
    pa.field("listing_instance_id", pa.string()),
    pa.field("config_signature", pa.string()),
    pa.field("active", pa.bool_()),
    pa.field("first_seen_at", pa.string()),
    pa.field("last_seen_at", pa.string()),
    pa.field("inactive_at", pa.string()),
    pa.field("observation_count", pa.int32()),
    pa.field("first_price_effective_monthly", pa.int32()),
    pa.field("lowest_price_effective_monthly", pa.int32()),
    pa.field("last_price_effective_monthly", pa.int32()),
])


@dataclass
class ListingLifecycle:
    row: dict

    @property
    def instance_id(self):
        return self.row["listing_instance_id"]


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _listing_row(listing) -> dict:
    writer = ParquetWriter()
    return {
        "listing_id": listing.listing_id,
        "datacenter": listing.datacenter,
        "location": listing.location,
        "available_from": listing.available_from,
        "cpu_raw": listing.cpu_raw,
        "cpu_normalized": listing.cpu_normalized,
        "benchmark_matched": listing.benchmark_matched,
        "passmark_id": listing.passmark_id,
        "single_thread_score": listing.single_thread_score,
        "multi_thread_score": listing.multi_thread_score,
        "cpu_cores": listing.cpu_cores,
        "cpu_threads": listing.cpu_threads,
        "benchmark_match_method": listing.benchmark_match_method,
        "ram_gb": listing.ram_gb,
        "ram_ecc": listing.ram_ecc,
        "uplink_speed": listing.uplink_speed,
        "price_base": listing.price_base,
        "price_ipv4_monthly": listing.price_ipv4_monthly,
        "price_setup_fee": listing.price_setup_fee,
        "price_effective_monthly": listing.price_effective_monthly,
        "price_per_benchmark_point_single": listing.price_per_benchmark_point_single,
        "price_per_benchmark_point_multi": listing.price_per_benchmark_point_multi,
        "price_per_gb_ram": listing.price_per_gb_ram,
        "price_per_tb_disk": listing.price_per_tb_disk,
        "price_percentile_vs_history": listing.price_percentile_vs_history,
        "price_per_benchmark_point_single_percentile_vs_history": listing.price_per_benchmark_point_single_percentile_vs_history,
        "price_per_benchmark_point_multi_percentile_vs_history": listing.price_per_benchmark_point_multi_percentile_vs_history,
        "is_all_time_low": listing.is_all_time_low,
        "history_sample_size": listing.history_sample_size,
        "history_cohort_fallback": listing.history_cohort_fallback,
        "fetched_at": _iso(listing.fetched_at),
        "disks": writer._serialize_disks(listing.disks),
    }


async def fetch_listing_history(url: str, timeout: float = 30.0) -> dict[str, ListingLifecycle]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise ListingHistoryFetchError(f"Failed to fetch {url}: {exc}") from exc
    if response.status_code == 404 or (
        response.status_code == 200 and response.headers.get("content-type", "").startswith("text/html")
    ):
        return {}
    if response.status_code != 200:
        raise ListingHistoryFetchError(f"Unexpected status {response.status_code} fetching {url}")
    try:
        table = pq.read_table(BytesIO(response.content))
    except Exception as exc:
        raise ListingHistoryFetchError(f"Failed to parse listing history from {url}: {exc}") from exc
    return {row["listing_instance_id"]: ListingLifecycle(row) for row in table.to_pylist()}


def _rematch_unmatched_history(history, cpu_matcher) -> None:
    """
    Re-attempt CPU matching for history rows still carrying
    benchmark_matched=False from a prior cycle.

    A row's benchmark fields are otherwise frozen once written (see
    update_listing_history's active-transition loop, which only ever
    touches `active`/`inactive_at` for rows no longer in the current
    fetch) -- so a benchmark-map fix landing after a CPU has gone
    inactive would otherwise never reach it. This makes that self-healing:
    every cycle, retry the match for anything still unmatched against
    whatever reference/alias/override data is loaded *now*.
    """
    if cpu_matcher is None:
        return
    for item in history.values():
        row = item.row
        if row["benchmark_matched"]:
            continue
        match = cpu_matcher.match_cpu(row["cpu_raw"])
        if not match.matched:
            continue
        row["cpu_normalized"] = match.cpu_normalized
        row["benchmark_matched"] = True
        row["passmark_id"] = match.passmark_id
        row["single_thread_score"] = match.single_thread_score
        row["multi_thread_score"] = match.multi_thread_score
        row["cpu_cores"] = match.cores
        row["cpu_threads"] = match.threads
        row["benchmark_match_method"] = match.match_method
        price = row["price_effective_monthly"]
        row["price_per_benchmark_point_single"] = (
            price / match.single_thread_score if match.single_thread_score else None
        )
        row["price_per_benchmark_point_multi"] = (
            price / match.multi_thread_score if match.multi_thread_score else None
        )


def update_listing_history(history, listings, now: datetime, retention_days: int = 180, cpu_matcher=None):
    now = now.astimezone(UTC)
    now_iso = now.isoformat()
    active_by_identity = {
        (item.row["listing_id"], item.row["config_signature"]): item
        for item in history.values() if item.row["active"]
    }
    seen = set()
    for listing in listings:
        config_signature = build_config_signature(listing)
        identity = (listing.listing_id, config_signature)
        item = active_by_identity.get(identity)
        current = _listing_row(listing)
        if item is None:
            seed = f"{listing.listing_id}|{config_signature}|{now_iso}"
            instance_id = hashlib.sha256(seed.encode()).hexdigest()[:24]
            current.update({
                "listing_instance_id": instance_id,
                "config_signature": config_signature,
                "active": True,
                "first_seen_at": now_iso,
                "last_seen_at": now_iso,
                "inactive_at": None,
                "observation_count": 1,
                "first_price_effective_monthly": listing.price_effective_monthly,
                "lowest_price_effective_monthly": listing.price_effective_monthly,
                "last_price_effective_monthly": listing.price_effective_monthly,
            })
            item = ListingLifecycle(current)
            history[instance_id] = item
        else:
            preserved = item.row
            current.update({
                "listing_instance_id": item.instance_id,
                "config_signature": config_signature,
                "active": True,
                "first_seen_at": preserved["first_seen_at"],
                "last_seen_at": now_iso,
                "inactive_at": None,
                "observation_count": preserved["observation_count"] + 1,
                "first_price_effective_monthly": preserved["first_price_effective_monthly"],
                "lowest_price_effective_monthly": min(preserved["lowest_price_effective_monthly"], listing.price_effective_monthly),
                "last_price_effective_monthly": listing.price_effective_monthly,
            })
            item.row = current
        seen.add(item.instance_id)

    for instance_id, item in history.items():
        if item.row["active"] and instance_id not in seen:
            item.row["active"] = False
            item.row["inactive_at"] = now_iso

    cutoff = now - timedelta(days=retention_days)
    expired = [key for key, item in history.items()
               if not item.row["active"] and datetime.fromisoformat(item.row["inactive_at"]) < cutoff]
    for key in expired:
        del history[key]

    _rematch_unmatched_history(history, cpu_matcher)
    return history


def write_listing_history(history, output_path: str | Path) -> None:
    rows = [item.row for item in history.values()]
    arrays = [pa.array([row.get(name) for row in rows], type=LISTING_HISTORY_SCHEMA.field(name).type)
              for name in LISTING_HISTORY_SCHEMA.names]
    table = pa.Table.from_arrays(arrays, schema=LISTING_HISTORY_SCHEMA)
    pq.write_table(table, output_path, compression="snappy")
