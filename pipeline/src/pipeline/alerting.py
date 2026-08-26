"""
Threshold-based Telegram alerting for auction listings.

Fetches back the previous cycle's "already notified" identity set from the
live deployment (same fetch-back-before-update pattern as
config_history/listing_history), sends a Telegram alert via telegram-relay
for any listing under the price threshold that wasn't already notified, and
returns the new set to persist.

A listing that stays under threshold across cycles alerts once. Rising back
above threshold, or disappearing from the feed, drops it from the persisted
set, so a later re-drop alerts again. A listing under threshold whose send
fails is NOT added to the persisted set, so it retries next cycle rather than
being silently given up on.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from pipeline.history_store import build_config_signature

logger = logging.getLogger(__name__)

HETZNER_AUCTION_URL = "https://www.hetzner.com/sb"


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


def _format_message(listing) -> str:
    price = listing.price_effective_monthly / 100
    cpu = listing.cpu_normalized or listing.cpu_raw
    disk = ", ".join(f"{d.count}x{d.capacity_gb}GB {d.type}" for d in listing.disks) or "no disks listed"
    return (
        f"Hetzner auction: €{price:.2f}/mo\n"
        f"{cpu}, {listing.ram_gb}GB RAM, {disk}\n"
        f"{listing.datacenter}\n"
        f"{HETZNER_AUCTION_URL}"
    )


async def send_alert(relay_url: str, listing, timeout: float = 10.0) -> None:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(relay_url, json={"text": _format_message(listing)})
    response.raise_for_status()


async def evaluate_and_alert(
    listings,
    previous_alerted: set[tuple[str, str]],
    relay_url: str,
    threshold_cents: int,
) -> set[tuple[str, str]]:
    still_alerted: set[tuple[str, str]] = set()
    for listing in listings:
        price = listing.price_effective_monthly
        if price is None or price >= threshold_cents:
            continue
        identity = (listing.listing_id, build_config_signature(listing))
        if identity in previous_alerted:
            still_alerted.add(identity)
            continue
        try:
            await send_alert(relay_url, listing)
            still_alerted.add(identity)
            logger.info(f"Sent Telegram alert for {listing.listing_id} at €{price / 100:.2f}/mo")
        except httpx.HTTPError as exc:
            logger.error(f"Failed to send Telegram alert for {listing.listing_id}: {exc}")
    return still_alerted
