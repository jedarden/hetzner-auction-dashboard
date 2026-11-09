"""
Config-History Store for the Hetzner Auction Dashboard

Implements the v2 "Historical stats: value percentile & all-time-low" design
from docs/plan/plan.md. Tracks a price histogram per config_signature (CPU
model + RAM + disk layout + datacenter -- never listing_id, which Hetzner
does not guarantee stable in meaning across ticks, see Edge Case Catalog
EC-4) across pipeline cycles, and derives a percentile rank + all-time-low
flag for each current listing against its own config's recorded history.

Superseded for ranking in 0.6.0: percentile and all-time-low now come from
offer_index.py, which counts each offer once instead of once per published
cycle (this histogram's weights follow the polling rate). This file is still
written for external readers; nothing in the pipeline reads it back for
ranking any more.

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

HISTORY_SCHEMA = pa.schema([
    pa.field("config_signature", pa.string()),
    # CPU cohort key (build_cpu_cohort_key). Not part of the original
    # plan.md schema sketch; kept for external readers.
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
    OTHER than "it doesn't exist yet" -- either a genuine 404, or (the actual
    case on Cloudflare Pages, which never 404s -- see fetch_history's own
    comment) a 200 response whose content-type is text/html, the SPA
    fallback served for any undeployed path. Both are expected on the very
    first run, or the first run after a fresh Pages project.

    Any other failure -- network error, timeout, non-404/non-text-html-200
    status, corrupt/unparseable Parquet bytes actually served as a file --
    must NOT be treated the same as "no history yet, start empty": that
    would silently discard every prior cycle's accumulated history and, on
    this cycle's write, publish a config_history.parquet containing only the
    current cycle's data. The whole point of this exception is to make that
    distinction so callers can abort the run instead (matching Pipeline Run
    Lifecycle's "on failure at any step, abort immediately... the previously
    published deployment keeps serving unchanged").
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

    # Cloudflare Pages does NOT 404 a nonexistent path -- it serves the SPA's
    # index.html with a 200 (confirmed empirically 2026-08-14: every path
    # tried, real or made-up, returned 200; only content-type distinguishes
    # a real deployed file, application/octet-stream for a .parquet, from
    # the text/html fallback). Without this check, the very first cycle
    # (before config_history.parquet has ever been published) gets a 200
    # containing HTML, fails to parse it as Parquet, and raises
    # HistoryFetchError -- which aborts every cycle forever, since the file
    # this checks for never gets a chance to be created. Must be checked
    # BEFORE attempting to parse, not inferred from a parse failure, so a
    # genuinely corrupt real Parquet file (content-type
    # application/octet-stream) still raises HistoryFetchError as intended.
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("text/html"):
        logger.info(
            f"No published config_history.parquet yet at {url} "
            f"(Cloudflare Pages SPA fallback returned text/html, bootstrap)"
        )
        return {}

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
