"""
Unit tests for Telegram threshold alerting (docs/notes/telegram-alerting.md).

Covers:
- crossing detection: new-under-threshold alerts, already-alerted skips,
  above-threshold/disappeared clears the persisted set
- each of the three criteria (price, multi-thread score, RAM) independently
  gating an otherwise-matching listing
- a failed send is not persisted, so it retries next cycle
- HTTP fetch-back: bootstrap 404/HTML vs. genuine failure (AlertStateFetchError)
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pipeline.alerting import (
    AlertStateFetchError,
    evaluate_and_alert,
    fetch_alert_state,
    write_alert_state,
)
from pipeline.cpu_matcher import BenchmarkMatch
from pipeline.enricher import CostMetricsEnricher
from pipeline.history_store import build_config_signature
from pipeline.fetcher import DiskSpec, RawListing

# Production defaults (main.py) -- used as the criteria in every test below
# so a test failure means "this changed relative to what's actually deployed."
MAX_PRICE_CENTS = 5500  # €55.00
MIN_MULTI_THREAD_SCORE = 30000
MIN_RAM_GB = 64


def _make_listing(listing_id="l1", price_base=5000, price_setup_fee=0, multi_thread_score=35000, ram_gb=64):
    raw = RawListing(
        listing_id=listing_id,
        datacenter="FSN1-DC3",
        location="FSN",
        available_from=None,
        cpu_raw="Intel Xeon E5-2680 v4",
        ram_gb=ram_gb,
        ram_ecc=True,
        disks=[DiskSpec(type="NVMe", count=2, capacity_gb=480)],
        uplink_speed=1000,
        price_base=price_base,
        price_setup_fee=price_setup_fee,
        fetched_at=datetime.now(UTC),
    )
    match = BenchmarkMatch(
        cpu_raw=raw.cpu_raw,
        matched=True,
        cpu_normalized="Intel Xeon E5-2680 v4",
        passmark_id=1234,
        single_thread_score=1500,
        multi_thread_score=multi_thread_score,
        cores=14,
        threads=28,
        match_method="direct",
    )
    return CostMetricsEnricher().enrich_listing(raw, match)


def _mock_client(mock_response):
    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client.post.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock()
    return mock_client


async def _evaluate(listings, previous_alerted):
    return await evaluate_and_alert(
        listings, previous_alerted, "https://relay/send", MAX_PRICE_CENTS, MIN_MULTI_THREAD_SCORE, MIN_RAM_GB
    )


class TestEvaluateAndAlert:
    @pytest.mark.asyncio
    async def test_new_listing_matching_all_criteria_sends_and_is_persisted(self):
        listing = _make_listing(price_base=5000)  # €50.00, 35000 multi-thread, 64GB -- all pass
        ok_response = MagicMock(status_code=200)
        ok_response.raise_for_status = MagicMock()
        mock_client = _mock_client(ok_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await _evaluate([listing], previous_alerted=set())

        identity = (listing.listing_id, build_config_signature(listing))
        assert result == {identity}
        mock_client.post.assert_awaited_once()
        sent_text = mock_client.post.call_args.kwargs["json"]["text"]
        assert "€50.00/mo" in sent_text
        assert "35000" in sent_text
        assert "https://www.hetzner.com/sb" in sent_text

    @pytest.mark.asyncio
    async def test_already_alerted_listing_is_not_resent(self):
        listing = _make_listing()
        identity = (listing.listing_id, build_config_signature(listing))
        mock_client = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await _evaluate([listing], previous_alerted={identity})

        assert result == {identity}
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_listing_at_or_above_price_ceiling_is_not_alerted(self):
        listing = _make_listing(price_base=5500)  # price_effective_monthly == ceiling
        mock_client = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await _evaluate([listing], previous_alerted=set())

        assert result == set()
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_listing_at_or_below_score_floor_is_not_alerted(self):
        listing = _make_listing(multi_thread_score=30000)  # score == floor, not strictly above
        mock_client = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await _evaluate([listing], previous_alerted=set())

        assert result == set()
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_unmatched_cpu_with_no_score_is_not_alerted(self):
        listing = _make_listing(multi_thread_score=None)
        mock_client = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await _evaluate([listing], previous_alerted=set())

        assert result == set()
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_listing_below_ram_floor_is_not_alerted(self):
        listing = _make_listing(ram_gb=32)
        mock_client = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await _evaluate([listing], previous_alerted=set())

        assert result == set()
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_listing_that_no_longer_matches_is_dropped_from_state(self):
        listing = _make_listing(price_base=7000)  # now above the price ceiling
        identity = (listing.listing_id, build_config_signature(listing))
        mock_client = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await _evaluate([listing], previous_alerted={identity})

        assert result == set()

    @pytest.mark.asyncio
    async def test_disappeared_listing_is_dropped_from_state(self):
        previous = {("gone", "some-signature")}
        mock_client = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await _evaluate([], previous_alerted=previous)

        assert result == set()

    @pytest.mark.asyncio
    async def test_failed_send_is_not_persisted_so_it_retries_next_cycle(self):
        listing = _make_listing()
        error_response = MagicMock(status_code=502, text="bad gateway")

        def _raise(*args, **kwargs):
            import httpx
            raise httpx.HTTPStatusError("502", request=MagicMock(), response=error_response)

        error_response.raise_for_status = _raise
        mock_client = _mock_client(error_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await _evaluate([listing], previous_alerted=set())

        assert result == set()


class TestFetchAlertState:
    @pytest.mark.asyncio
    async def test_404_returns_empty_state_not_an_error(self):
        mock_response = MagicMock(status_code=404)
        mock_client = _mock_client(mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            state = await fetch_alert_state("https://example.pages.dev/alerted-listings.json")

        assert state == set()

    @pytest.mark.asyncio
    async def test_bootstrap_200_html_fallback_returns_empty_state_not_an_error(self):
        mock_response = MagicMock(
            status_code=200,
            content=b"<!doctype html><html>...</html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
        mock_client = _mock_client(mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            state = await fetch_alert_state("https://example.pages.dev/alerted-listings.json")

        assert state == set()

    @pytest.mark.asyncio
    async def test_valid_200_response_parses_correctly(self):
        import json
        body = json.dumps([{"listing_id": "l1", "config_signature": "sig1"}]).encode()
        mock_response = MagicMock(
            status_code=200, content=body, headers={"content-type": "application/json"}
        )
        mock_client = _mock_client(mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            state = await fetch_alert_state("https://example.pages.dev/alerted-listings.json")

        assert state == {("l1", "sig1")}

    @pytest.mark.asyncio
    async def test_non_404_error_status_raises_alert_state_fetch_error(self):
        mock_response = MagicMock(status_code=500)
        mock_client = _mock_client(mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(AlertStateFetchError):
                await fetch_alert_state("https://example.pages.dev/alerted-listings.json")


class TestWriteAlertState:
    def test_round_trips_through_json(self, tmp_path):
        identities = {("l1", "sig1"), ("l2", "sig2")}
        path = tmp_path / "alerted-listings.json"

        write_alert_state(identities, path)
        import json
        rows = json.loads(path.read_text())

        assert {(row["listing_id"], row["config_signature"]) for row in rows} == identities
