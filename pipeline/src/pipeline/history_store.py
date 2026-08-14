"""
Config-History Store for the Hetzner Auction Dashboard

Implements the v2 "Historical stats: value percentile & all-time-low" design
from docs/plan/plan.md. Tracks a price histogram per config_signature (CPU
model + RAM + disk layout + datacenter -- never listing_id, which Hetzner
does not guarantee stable in meaning across ticks, see Edge Case Catalog
EC-4) across pipeline cycles, and derives a percentile rank + all-time-low
flag for each current listing against its own config's recorded history.

Storage is a bounded price->count histogram per config (one entry per
DISTINCT price a config has ever sold at), not a per-tick log -- see the
Anti-Patterns Catalog's "never read-modify-write a growing published file."
config_history.parquet is fetched back over HTTP each cycle, updated in
memory, and rewritten whole -- same fetch-back-before-republish pattern
web_fetcher.py already uses for web/.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

# Below this many recorded observations, a config's own histogram is too
# thin to rank against meaningfully -- fall back to the broader same-CPU
# cohort (plan.md: "exact threshold TBD once real data volume is known"). 5
# is a conservative starting point, easily tuned once real volume is
# observed from the first few weeks of live runs.
MIN_OBSERVATIONS_FOR_OWN_HISTOGRAM = 5

HISTORY_SCHEMA = pa.schema([
    pa.field("config_signature", pa.string()),
    # Cohort grouping key for the below-threshold fallback. Not part of the
    # original plan.md schema sketch (config_signature/first_observed_at/
    # last_observed_at/total_observations/min_price_effective_monthly/
    # price_histogram) -- added so the fallback has something to group on
    # without maintaining a second stored table (see _cohort_histogram).
    pa.field("cpu_key", pa.string()),
    pa.field("first_observed_at", pa.string()),
    pa.field("last_observed_at", pa.string()),
    # Written for external readers per the original schema sketch, but never
    # read back on load -- total_observations is always re-derived from
    # price_histogram (ConfigHistoryEntry.total_observations), so there is
    # only ever one source of truth for it in this process.
    pa.field("total_observations", pa.int64()),
    pa.field("min_price_effective_monthly", pa.int32()),
    pa.field("price_histogram", pa.list_(pa.struct([
        pa.field("price_effective_monthly", pa.int32()),
        pa.field("observation_count", pa.int64()),
    ]))),
])


class HistoryFetchError(Exception):
    """
    Raised when fetching the live config_history.parquet fails for a reason
    OTHER than "it doesn't exist yet" (a genuine 404 -- expected on the very
    first run, or the first run after a fresh Pages project).

    Any other failure -- network error, timeout, non-404 HTTP status,
    corrupt/unparseable Parquet bytes -- must NOT be treated the same as
    "no history yet, start empty": that would silently discard every prior
    cycle's accumulated history and, on this cycle's write, publish a
    config_history.parquet containing only the current cycle's data. The
    whole point of this exception is to make that distinction so callers can
    abort the run instead (matching Pipeline Run Lifecycle's "on failure at
    any step, abort immediately... the previously published deployment keeps
    serving unchanged").
    """
    pass


@dataclass
class ConfigHistoryEntry:
    """One row of config_history.parquet -- accumulated price observations
    for one exact (CPU model, RAM, disk layout, datacenter) configuration."""

    config_signature: str
    cpu_key: str
    first_observed_at: str
    last_observed_at: str
    min_price_effective_monthly: int
    price_histogram: dict  # {price_effective_monthly (int): observation_count (int)}

    @property
    def total_observations(self) -> int:
        return sum(self.price_histogram.values())


def build_cpu_cohort_key(listing) -> str:
    """
    Broader grouping key used for the cohort fallback -- CPU model alone,
    ignoring RAM/disk/datacenter. Falls back to cpu_raw when the CPU hasn't
    matched a benchmark entry yet (an unmatched listing still has a real
    price worth tracking; it just can't be grouped by canonical model name).
    """
    return listing.cpu_normalized if listing.cpu_normalized else listing.cpu_raw


def build_config_signature(listing) -> str:
    """
    Deterministic key for "this exact configuration" -- CPU + RAM + disk
    layout + datacenter (EC-4: never listing_id, which Hetzner reuses across
    ticks with different specs and is not assumed stable in meaning across
    ticks).

    Disk layout is sorted before joining so struct ordering in the raw feed
    never changes the signature for an otherwise-identical config.
    """
    cpu_key = build_cpu_cohort_key(listing)
    disk_key = ",".join(
        sorted(f"{d.type}:{d.count}:{d.capacity_gb}" for d in listing.disks)
    )
    return f"{cpu_key}|{listing.ram_gb}|{listing.ram_ecc}|{disk_key}|{listing.datacenter}"


async def fetch_history(url: str, timeout: float = 30.0) -> dict[str, ConfigHistoryEntry]:
    """
    Fetch-back the currently-live config_history.parquet before this cycle
    updates it. See HistoryFetchError's docstring for why a 404 (bootstrap)
    and any other failure are deliberately NOT handled the same way.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
    except httpx.HTTPError as e:
        raise HistoryFetchError(f"Failed to fetch {url}: {e}") from e

    if response.status_code == 404:
        logger.info(f"No published config_history.parquet yet at {url} (bootstrap)")
        return {}

    if response.status_code != 200:
        raise HistoryFetchError(f"Unexpected status {response.status_code} fetching {url}")

    try:
        table = pq.read_table(BytesIO(response.content))
    except Exception as e:
        raise HistoryFetchError(f"Failed to parse config_history.parquet from {url}: {e}") from e

    return _table_to_history(table)


def _table_to_history(table: pa.Table) -> dict[str, ConfigHistoryEntry]:
    history: dict[str, ConfigHistoryEntry] = {}
    for row in table.to_pylist():
        histogram = {
            item["price_effective_monthly"]: item["observation_count"]
            for item in row["price_histogram"]
        }
        entry = ConfigHistoryEntry(
            config_signature=row["config_signature"],
            cpu_key=row["cpu_key"],
            first_observed_at=row["first_observed_at"],
            last_observed_at=row["last_observed_at"],
            min_price_effective_monthly=row["min_price_effective_monthly"],
            price_histogram=histogram,
        )
        history[entry.config_signature] = entry
    return history


def update_history(history: dict, listings, now: datetime) -> dict:
    """
    Accumulate this cycle's listings into `history` in place: find-or-create
    each listing's config_signature entry, increment its price's histogram
    count, bump last_observed_at, lower min_price_effective_monthly if this
    cycle's price is a new low. Returns `history` for convenience.
    """
    now_iso = now.isoformat()
    for listing in listings:
        signature = build_config_signature(listing)
        price = listing.price_effective_monthly

        entry = history.get(signature)
        if entry is None:
            entry = ConfigHistoryEntry(
                config_signature=signature,
                cpu_key=build_cpu_cohort_key(listing),
                first_observed_at=now_iso,
                last_observed_at=now_iso,
                min_price_effective_monthly=price,
                price_histogram={},
            )
            history[signature] = entry

        entry.price_histogram[price] = entry.price_histogram.get(price, 0) + 1
        entry.last_observed_at = now_iso
        entry.min_price_effective_monthly = min(entry.min_price_effective_monthly, price)

    return history


def _percentile_from_histogram(histogram: dict, total_observations: int, current_price: int):
    """
    percentile = fraction of every recorded observation this price is
    cheaper than or equal to (plan.md's formula) -- i.e. "cheaper than N% of
    every time this exact config has appeared." Returns None if there are no
    observations to rank against at all.
    """
    if total_observations == 0:
        return None
    at_or_above = sum(count for price, count in histogram.items() if price >= current_price)
    return at_or_above / total_observations


def _cohort_histogram(history: dict, cpu_key: str) -> tuple[dict, int]:
    """
    Merge every config_signature entry sharing cpu_key into one combined
    histogram, for the below-threshold fallback. Computed on the fly rather
    than stored as a second table -- cheap at this project's data volume and
    avoids a second place the same counts could drift out of sync.
    """
    merged: dict = {}
    for entry in history.values():
        if entry.cpu_key != cpu_key:
            continue
        for price, count in entry.price_histogram.items():
            merged[price] = merged.get(price, 0) + count
    return merged, sum(merged.values())


def compute_percentiles(
    history: dict,
    listings,
    min_observations: int = MIN_OBSERVATIONS_FOR_OWN_HISTOGRAM,
) -> None:
    """
    Mutate each listing in place with its history-derived fields. Call this
    AFTER update_history() has folded the current cycle's own prices into
    `history` -- each listing is deliberately ranked against a set that
    includes itself (a first-ever observation of a config is trivially its
    own all-time-low, which is the correct 0th-percentile special case).

    Sets, per listing:
    - price_percentile_vs_history: float in [0, 1], or None if no history
      data exists at all (own signature AND cpu cohort both empty)
    - price_per_benchmark_point_{single,multi}_percentile_vs_history: same
      value as price_percentile_vs_history when benchmark_matched, else None
      -- dividing price by a positive per-config constant (the matched
      CPU's fixed benchmark score) doesn't change rank order, so one
      histogram legitimately serves all three metrics (plan.md)
    - is_all_time_low: whether this price is (tied for) this exact config's
      lowest ever recorded -- always about the exact signature, never the
      broader cohort, even when the percentile itself used the fallback
    - history_sample_size: observation count backing the percentile actually
      used (own or cohort)
    - history_cohort_fallback: whether the cohort fallback was used
    """
    for listing in listings:
        signature = build_config_signature(listing)
        entry = history.get(signature)

        histogram = entry.price_histogram if entry else {}
        total = entry.total_observations if entry else 0
        used_cohort = False

        if total < min_observations:
            cohort_key = build_cpu_cohort_key(listing)
            cohort_histogram, cohort_total = _cohort_histogram(history, cohort_key)
            if cohort_total > total:
                histogram, total = cohort_histogram, cohort_total
                used_cohort = True

        percentile = _percentile_from_histogram(histogram, total, listing.price_effective_monthly)

        listing.price_percentile_vs_history = percentile
        listing.price_per_benchmark_point_single_percentile_vs_history = (
            percentile if listing.benchmark_matched else None
        )
        listing.price_per_benchmark_point_multi_percentile_vs_history = (
            percentile if listing.benchmark_matched else None
        )
        listing.history_sample_size = total
        listing.history_cohort_fallback = used_cohort
        listing.is_all_time_low = (
            entry is not None
            and listing.price_effective_monthly <= entry.min_price_effective_monthly
        )


def history_to_table(history: dict) -> pa.Table:
    data = {name: [] for name in HISTORY_SCHEMA.names}
    for entry in history.values():
        data["config_signature"].append(entry.config_signature)
        data["cpu_key"].append(entry.cpu_key)
        data["first_observed_at"].append(entry.first_observed_at)
        data["last_observed_at"].append(entry.last_observed_at)
        data["total_observations"].append(entry.total_observations)
        data["min_price_effective_monthly"].append(entry.min_price_effective_monthly)
        data["price_histogram"].append([
            {"price_effective_monthly": price, "observation_count": count}
            for price, count in sorted(entry.price_histogram.items())
        ])

    arrays = [pa.array(data[name], type=HISTORY_SCHEMA.field(name).type) for name in HISTORY_SCHEMA.names]
    return pa.Table.from_arrays(arrays, schema=HISTORY_SCHEMA)


def write_history(history: dict, output_path: str | Path) -> None:
    table = history_to_table(history)
    pq.write_table(table, output_path, compression="snappy")
