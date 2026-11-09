"""
Offer index: every auction offer ever seen, one row per offer, kept forever.

This is the source for "every unique configuration and its price
distribution". config_history.parquet's price_histogram cannot serve that
purpose: it counts one observation per published cycle, so its weights follow
the polling rate (about 5/h per listing in August 2026, 18/h in Sep-Oct, 60/h
from November) and favour offers that stay unsold longest. Counting offers
instead makes every server count once whatever the cadence.

An offer is identified by (config_signature, listing_id), the same identity
listing_history uses to match an active lifecycle. Hetzner re-lists the same
server under the same id after it briefly disappears -- on 2026-10-27 a
flapping feed produced 9.7k lifecycles for 3k servers, one id 116 times -- so
counting lifecycles would double count; counting ids does not.

Auction prices only fall while an offer is listed (10 of 24k lifecycles ever
rose after their low), so an offer's lowest price is also the price it last
sold or vanished at. The distribution shown to users is over that lowest
price.

The index is merged idempotently every cycle from both listing_history (which
seeds it on the first run and repairs it if it is ever lost) and the current
listings. Unlike listing_history it has no retention window.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from pipeline.history_store import build_config_signature, build_cpu_cohort_key

logger = logging.getLogger(__name__)

# Below this many offers of the same hardware, rank against every offer with
# the same CPU instead.
MIN_OFFERS_FOR_OWN_DISTRIBUTION = 5

OFFER_INDEX_SCHEMA = pa.schema([
    pa.field("config_signature", pa.string()),
    # config_signature without the datacenter: the "configuration" users mean.
    pa.field("hardware_key", pa.string()),
    pa.field("listing_id", pa.string()),
    pa.field("cpu", pa.string()),
    pa.field("ram_gb", pa.int32()),
    pa.field("ram_ecc", pa.bool_()),
    # Sorted "type:count:capacity_gb" entries joined by commas, as in the signature.
    pa.field("disk_layout", pa.string()),
    pa.field("datacenter", pa.string()),
    pa.field("single_thread_score", pa.int32()),
    pa.field("multi_thread_score", pa.int32()),
    pa.field("first_seen_at", pa.string()),
    pa.field("last_seen_at", pa.string()),
    pa.field("first_price_effective_monthly", pa.int32()),
    pa.field("lowest_price_effective_monthly", pa.int32()),
    pa.field("last_price_effective_monthly", pa.int32()),
])


class OfferIndexFetchError(Exception):
    """The published offer index exists but could not be read.

    Treated like the other fetch-back failures: abort the cycle rather than
    rebuild from listing_history alone, which would drop offers that have
    already aged out of listing_history's retention window.
    """


@dataclass
class Offer:
    config_signature: str
    listing_id: str
    single_thread_score: int | None
    multi_thread_score: int | None
    first_seen_at: str
    last_seen_at: str
    first_price: int
    lowest_price: int
    last_price: int

    @property
    def key(self) -> tuple[str, str]:
        return (self.config_signature, self.listing_id)


def split_signature(signature: str) -> tuple[str, int, bool, str, str]:
    """Return (cpu, ram_gb, ram_ecc, disk_layout, datacenter) from a signature.

    Splits from the right so a CPU name containing "|" still parses."""
    cpu, ram, ecc, disks, datacenter = signature.rsplit("|", 4)
    return cpu, int(ram), ecc.lower() == "true", disks, datacenter


def hardware_key(signature: str) -> str:
    return signature.rsplit("|", 1)[0]


async def fetch_offer_index(url: str | None, timeout: float = 30.0) -> dict[tuple[str, str], Offer]:
    """Fetch the live offer index. ``url`` is None when the live manifest does
    not name one yet (first rollout): start empty and let listing_history
    seed it."""
    if url is None:
        logger.info("No published offer index yet (bootstrap from listing history)")
        return {}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise OfferIndexFetchError(f"Failed to fetch {url}: {exc}") from exc
    if response.status_code != 200:
        raise OfferIndexFetchError(f"Unexpected status {response.status_code} fetching {url}")
    try:
        table = pq.read_table(BytesIO(response.content))
    except Exception as exc:
        raise OfferIndexFetchError(f"Failed to parse offer index from {url}: {exc}") from exc
    return table_to_offers(table)


def table_to_offers(table: pa.Table) -> dict[tuple[str, str], Offer]:
    offers = {}
    for row in table.to_pylist():
        offer = Offer(
            config_signature=row["config_signature"],
            listing_id=row["listing_id"],
            single_thread_score=row["single_thread_score"],
            multi_thread_score=row["multi_thread_score"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            first_price=row["first_price_effective_monthly"],
            lowest_price=row["lowest_price_effective_monthly"],
            last_price=row["last_price_effective_monthly"],
        )
        offers[offer.key] = offer
    return offers


def _ts(value: str) -> datetime:
    # Compare parsed instants, not strings: isoformat() drops the fraction
    # when microseconds are zero, which breaks lexical ordering.
    return datetime.fromisoformat(value)


def _merge(offers: dict, candidate: Offer) -> None:
    existing = offers.get(candidate.key)
    if existing is None:
        offers[candidate.key] = candidate
        return
    if _ts(candidate.first_seen_at) < _ts(existing.first_seen_at):
        existing.first_seen_at = candidate.first_seen_at
        existing.first_price = candidate.first_price
    if _ts(candidate.last_seen_at) >= _ts(existing.last_seen_at):
        existing.last_seen_at = candidate.last_seen_at
        existing.last_price = candidate.last_price
    existing.lowest_price = min(existing.lowest_price, candidate.lowest_price)
    # Benchmark scores can appear later when a CPU alias is added.
    if candidate.multi_thread_score is not None:
        existing.multi_thread_score = candidate.multi_thread_score
    if candidate.single_thread_score is not None:
        existing.single_thread_score = candidate.single_thread_score


def merge_listing_history(offers: dict, listing_history: dict) -> dict:
    """Fold every listing_history lifecycle into ``offers``. Idempotent."""
    for item in listing_history.values():
        row = item.row
        _merge(offers, Offer(
            config_signature=row["config_signature"],
            listing_id=str(row["listing_id"]),
            single_thread_score=row.get("single_thread_score"),
            multi_thread_score=row.get("multi_thread_score"),
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            first_price=row["first_price_effective_monthly"],
            lowest_price=row["lowest_price_effective_monthly"],
            last_price=row["last_price_effective_monthly"],
        ))
    return offers


def observe_listings(offers: dict, listings, now) -> dict:
    """Fold the current cycle's listings into ``offers``. Idempotent."""
    now_iso = now.isoformat()
    for listing in listings:
        price = listing.price_effective_monthly
        _merge(offers, Offer(
            config_signature=build_config_signature(listing),
            listing_id=str(listing.listing_id),
            single_thread_score=listing.single_thread_score,
            multi_thread_score=listing.multi_thread_score,
            first_seen_at=now_iso,
            last_seen_at=now_iso,
            first_price=price,
            lowest_price=price,
            last_price=price,
        ))
    return offers


# listing_id of a placeholder offer seeded from config_history.
UNKNOWN_LISTING_ID = ""


def seed_from_config_history(offers: dict, config_history: dict) -> dict:
    """Add one placeholder offer for each configuration known only to
    config_history.

    config_history started on 2026-08-14, listing_history on 2026-08-20, so
    configurations seen only in that week have no offer rows. They get one
    offer (listing_id "", a lower bound on the real count) at their lowest
    recorded price, so the index still lists every configuration ever seen.
    Call after observe_listings(): a signature first seen this cycle is then
    already present and is not seeded. Idempotent.
    """
    known = {offer.config_signature for offer in offers.values()}
    for signature, entry in config_history.items():
        if signature in known:
            continue
        price = entry.min_price_effective_monthly
        placeholder = Offer(
            config_signature=signature,
            listing_id=UNKNOWN_LISTING_ID,
            single_thread_score=None,
            multi_thread_score=None,
            first_seen_at=entry.first_observed_at,
            last_seen_at=entry.last_observed_at,
            first_price=price,
            lowest_price=price,
            last_price=price,
        )
        offers[placeholder.key] = placeholder
    return offers


def compute_offer_percentiles(
    offers: dict,
    listings,
    min_offers: int = MIN_OFFERS_FOR_OWN_DISTRIBUTION,
) -> None:
    """Rank each listing against every offer ever seen of the same hardware.

    Call after observe_listings() so each listing is part of its own set (a
    first-ever offer is trivially its own all-time low).

    Sets, per listing:
    - price_percentile_vs_history: share of offers of this hardware (any
      datacenter) whose lowest price was at or above this price, i.e.
      "cheaper than N% of offers"; None if no offers are known at all
    - the two benchmark percentile columns: the same value when the CPU is
      benchmark-matched, else None (dividing by a fixed score keeps rank)
    - is_all_time_low: price at or below every offer of this hardware
    - history_sample_size: offers in the set actually ranked against
    - history_cohort_fallback: whether that set was every offer with the
      same CPU because the hardware had fewer than ``min_offers``
    """
    by_hardware: dict[str, list[int]] = {}
    by_cpu: dict[str, list[int]] = {}
    for offer in offers.values():
        hw = hardware_key(offer.config_signature)
        by_hardware.setdefault(hw, []).append(offer.lowest_price)
        by_cpu.setdefault(split_signature(offer.config_signature)[0], []).append(offer.lowest_price)

    for listing in listings:
        signature = build_config_signature(listing)
        own = by_hardware.get(hardware_key(signature), [])
        prices, used_cohort = own, False
        if len(own) < min_offers:
            cohort = by_cpu.get(build_cpu_cohort_key(listing), [])
            if len(cohort) > len(own):
                prices, used_cohort = cohort, True

        price = listing.price_effective_monthly
        percentile = (
            sum(1 for p in prices if p >= price) / len(prices) if prices else None
        )
        listing.price_percentile_vs_history = percentile
        listing.price_per_benchmark_point_single_percentile_vs_history = (
            percentile if listing.benchmark_matched else None
        )
        listing.price_per_benchmark_point_multi_percentile_vs_history = (
            percentile if listing.benchmark_matched else None
        )
        listing.history_sample_size = len(prices)
        listing.history_cohort_fallback = used_cohort
        listing.is_all_time_low = bool(own) and price <= min(own)


def offers_to_table(offers: dict) -> pa.Table:
    data = {name: [] for name in OFFER_INDEX_SCHEMA.names}
    for offer in sorted(offers.values(), key=lambda o: o.key):
        cpu, ram_gb, ram_ecc, disks, datacenter = split_signature(offer.config_signature)
        data["config_signature"].append(offer.config_signature)
        data["hardware_key"].append(hardware_key(offer.config_signature))
        data["listing_id"].append(offer.listing_id)
        data["cpu"].append(cpu)
        data["ram_gb"].append(ram_gb)
        data["ram_ecc"].append(ram_ecc)
        data["disk_layout"].append(disks)
        data["datacenter"].append(datacenter)
        data["single_thread_score"].append(offer.single_thread_score)
        data["multi_thread_score"].append(offer.multi_thread_score)
        data["first_seen_at"].append(offer.first_seen_at)
        data["last_seen_at"].append(offer.last_seen_at)
        data["first_price_effective_monthly"].append(offer.first_price)
        data["lowest_price_effective_monthly"].append(offer.lowest_price)
        data["last_price_effective_monthly"].append(offer.last_price)
    arrays = [
        pa.array(data[name], type=OFFER_INDEX_SCHEMA.field(name).type)
        for name in OFFER_INDEX_SCHEMA.names
    ]
    return pa.Table.from_arrays(arrays, schema=OFFER_INDEX_SCHEMA)


def write_offer_index(offers: dict, output_path: str | Path) -> None:
    pq.write_table(offers_to_table(offers), output_path, compression="snappy")
