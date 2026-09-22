"""
Threshold-based Telegram alerting for auction listings.

Fetches back the previous cycle's "already notified" identity set from the
live deployment (same fetch-back-before-update pattern as
config_history/listing_history), sends a Telegram alert via telegram-relay
for any listing that matches at least one alert rule and wasn't already
notified, and returns the new set to persist.

Two kinds of rule, evaluated as an OR:

- the global rule (TELEGRAM_ALERT_MAX_PRICE_EUR / _MIN_MULTI_THREAD_SCORE /
  _MIN_RAM_GB): any CPU at or below a price ceiling, above a benchmark floor, with
  enough RAM -- the original single threshold;
- signature rules (TELEGRAM_ALERT_RULES, a JSON list): a named set of CPUs
  with a RAM floor, an optional ECC requirement, an NVMe-count floor and an
  inclusive price ceiling. These exist because the listing history shows the servers
  worth buying clear in minutes at a config-specific price (an i5-13500 64 GB
  at <=€70 is gone in a quarter of an hour; the same box at €95 sits for a
  day), so one global ceiling either misses them or drowns in noise.

A listing that stays under threshold across cycles alerts once. Rising back
above threshold, or disappearing from the feed, drops it from the persisted
set, so a later re-drop alerts again. A listing under threshold whose send
fails is NOT added to the persisted set, so it retries next cycle rather than
being silently given up on.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from pipeline.history_store import build_config_signature

logger = logging.getLogger(__name__)

HETZNER_AUCTION_URL = "https://www.hetzner.com/sb"


@dataclass(frozen=True)
class AlertRule:
    """One alert criterion. A listing matches when every set field holds.

    cpus: normalized CPU names (case-insensitive exact match); None = any CPU.
    ecc: True/False to require/forbid ECC; None = don't care.
    min_multi_thread_score: strictly-above floor; None = no benchmark gate
      (a signature rule already pins the CPU, so the score adds nothing).
    """

    name: str
    max_price_cents: int
    min_ram_gb: int = 0
    cpus: tuple[str, ...] | None = None
    ecc: bool | None = None
    min_nvme: int = 0
    min_multi_thread_score: int | None = None

    def cpu_set(self) -> frozenset[str]:
        return frozenset(c.casefold() for c in (self.cpus or ()))


def global_rule(max_price_cents: int, min_multi_thread_score: int, min_ram_gb: int) -> AlertRule:
    return AlertRule(
        name="global",
        max_price_cents=max_price_cents,
        min_ram_gb=min_ram_gb,
        min_multi_thread_score=min_multi_thread_score,
    )


def parse_rules(text: str | None) -> list[AlertRule]:
    """Parse TELEGRAM_ALERT_RULES. Empty/None -> []. Malformed -> ValueError,
    naming the offending rule, so a config typo is loud rather than a silent
    non-alert."""
    if text is None or not text.strip():
        return []
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"TELEGRAM_ALERT_RULES is not valid JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError("TELEGRAM_ALERT_RULES must be a JSON list of rule objects")
    rules: list[AlertRule] = []
    for i, item in enumerate(raw):
        where = f"rule #{i + 1}" + (f" ({item.get('name')!r})" if isinstance(item, dict) and item.get("name") else "")
        if not isinstance(item, dict):
            raise ValueError(f"{where}: must be an object")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{where}: 'name' is required")
        if "max_price_eur" not in item:
            raise ValueError(f"{where}: 'max_price_eur' is required")
        try:
            max_price_cents = round(float(item["max_price_eur"]) * 100)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{where}: 'max_price_eur' must be a number") from exc
        if max_price_cents <= 0:
            raise ValueError(f"{where}: 'max_price_eur' must be positive")
        cpus = item.get("cpus")
        if cpus is not None and (not isinstance(cpus, list) or not cpus or not all(isinstance(c, str) and c.strip() for c in cpus)):
            raise ValueError(f"{where}: 'cpus' must be a non-empty list of strings")
        ecc = item.get("ecc")
        if ecc is not None and not isinstance(ecc, bool):
            raise ValueError(f"{where}: 'ecc' must be true, false or omitted")
        for key in ("min_ram_gb", "min_nvme", "min_multi_thread_score"):
            v = item.get(key)
            if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
                raise ValueError(f"{where}: '{key}' must be a non-negative integer")
        rules.append(
            AlertRule(
                name=name.strip(),
                max_price_cents=max_price_cents,
                min_ram_gb=int(item.get("min_ram_gb") or 0),
                cpus=tuple(cpus) if cpus else None,
                ecc=ecc,
                min_nvme=int(item.get("min_nvme") or 0),
                min_multi_thread_score=item.get("min_multi_thread_score"),
            )
        )
    return rules


class AlertStateFetchError(Exception):
    """Fetching back the previous alert state failed in a way that must abort
    the cycle. Falling back to an empty set here would re-alert on every
    already-notified listing on a transient fetch failure -- same rationale
    as HistoryFetchError/ListingHistoryFetchError."""


async def fetch_alert_state(url: str, timeout: float = 30.0) -> set[tuple[str, str]]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise AlertStateFetchError(f"Failed to fetch {url}: {exc}") from exc
    if response.status_code == 404 or (
        response.status_code == 200 and response.headers.get("content-type", "").startswith("text/html")
    ):
        return set()
    if response.status_code != 200:
        raise AlertStateFetchError(f"Unexpected status {response.status_code} fetching {url}")
    try:
        rows = json.loads(response.content)
    except Exception as exc:
        raise AlertStateFetchError(f"Failed to parse alert state from {url}: {exc}") from exc
    return {(row["listing_id"], row["config_signature"]) for row in rows}


def write_alert_state(identities: set[tuple[str, str]], output_path: str | Path) -> None:
    rows = [
        {"listing_id": listing_id, "config_signature": config_signature}
        for listing_id, config_signature in sorted(identities)
    ]
    Path(output_path).write_text(json.dumps(rows))


def _format_message(listing, rule_names: tuple[str, ...] = ()) -> str:
    price = listing.price_effective_monthly / 100
    cpu = listing.cpu_normalized or listing.cpu_raw
    disk = ", ".join(f"{d.count}x{d.capacity_gb}GB {d.type}" for d in listing.disks) or "no disks listed"
    ecc = " ECC" if getattr(listing, "ram_ecc", False) else ""
    tag = "".join(f"[{n}] " for n in rule_names if n != "global")
    return (
        f"{tag}Hetzner auction: €{price:.2f}/mo\n"
        f"{cpu} (multi-thread {listing.multi_thread_score}), {listing.ram_gb}GB{ecc} RAM, {disk}\n"
        f"{listing.datacenter}\n"
        f"{HETZNER_AUCTION_URL}"
    )


async def send_alert(relay_url: str, listing, timeout: float = 10.0, rule_names: tuple[str, ...] = ()) -> None:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(relay_url, json={"text": _format_message(listing, rule_names)})
    response.raise_for_status()


def nvme_count(listing) -> int:
    return sum(int(d.count or 0) for d in (listing.disks or []) if str(d.type).casefold() == "nvme")


def matches_rule(listing, rule: AlertRule) -> bool:
    price = listing.price_effective_monthly
    if price is None or price > rule.max_price_cents:
        return False
    if rule.min_multi_thread_score is not None:
        score = listing.multi_thread_score
        if score is None or score <= rule.min_multi_thread_score:
            return False
    if (listing.ram_gb or 0) < rule.min_ram_gb:
        return False
    if rule.cpus is not None:
        cpu = listing.cpu_normalized or listing.cpu_raw or ""
        if cpu.casefold() not in rule.cpu_set():
            return False
    if rule.ecc is not None and bool(getattr(listing, "ram_ecc", False)) != rule.ecc:
        return False
    if rule.min_nvme and nvme_count(listing) < rule.min_nvme:
        return False
    return True


def matching_rules(listing, rules: list[AlertRule]) -> tuple[str, ...]:
    return tuple(r.name for r in rules if matches_rule(listing, r))


def _matches_criteria(
    listing, max_price_cents: int, min_multi_thread_score: int, min_ram_gb: int
) -> bool:
    """The original single-threshold check, kept for callers and tests that
    predate rules; identical to matches_rule against global_rule(...)."""
    return matches_rule(listing, global_rule(max_price_cents, min_multi_thread_score, min_ram_gb))


async def evaluate_and_alert(
    listings,
    previous_alerted: set[tuple[str, str]],
    relay_url: str,
    max_price_cents: int | None = None,
    min_multi_thread_score: int | None = None,
    min_ram_gb: int | None = None,
    rules: list[AlertRule] | None = None,
) -> set[tuple[str, str]]:
    """Alert on every listing matching at least one rule. `rules` is the full
    rule list; when it is omitted the three legacy thresholds form the single
    global rule (the pre-rules call shape). The dedup identity is per listing,
    not per rule: a listing matching two rules alerts once, naming both."""
    if rules is None:
        if None in (max_price_cents, min_multi_thread_score, min_ram_gb):
            raise ValueError("evaluate_and_alert needs either rules= or the three legacy thresholds")
        rules = [global_rule(max_price_cents, min_multi_thread_score, min_ram_gb)]
    still_alerted: set[tuple[str, str]] = set()
    for listing in listings:
        hit = matching_rules(listing, rules)
        if not hit:
            continue
        identity = (listing.listing_id, build_config_signature(listing))
        if identity in previous_alerted:
            still_alerted.add(identity)
            continue
        try:
            await send_alert(relay_url, listing, rule_names=hit)
            still_alerted.add(identity)
            logger.info(
                f"Sent Telegram alert for {listing.listing_id} at "
                f"€{listing.price_effective_monthly / 100:.2f}/mo "
                f"(multi-thread {listing.multi_thread_score}, {listing.ram_gb}GB RAM, rules {', '.join(hit)})"
            )
        except httpx.HTTPError as exc:
            logger.error(f"Failed to send Telegram alert for {listing.listing_id}: {exc}")
    return still_alerted
