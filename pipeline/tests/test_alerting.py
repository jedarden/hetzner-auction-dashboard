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
MAX_PRICE_CENTS = 6000  # €60.00
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
    async def test_listing_at_price_ceiling_is_alerted(self):
        listing = _make_listing(price_base=6000)  # price_effective_monthly == ceiling
        ok_response = MagicMock(status_code=200)
        ok_response.raise_for_status = MagicMock()
        mock_client = _mock_client(ok_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await _evaluate([listing], previous_alerted=set())

        assert result == {(listing.listing_id, build_config_signature(listing))}
        mock_client.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_listing_above_price_ceiling_is_not_alerted(self):
        listing = _make_listing(price_base=6001)
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


# --- signature rules (TELEGRAM_ALERT_RULES) -------------------------------------
from pipeline.alerting import AlertRule, global_rule, matches_rule, matching_rules, parse_rules  # noqa: E402


def _make_typed_listing(cpu="Intel Core i5-13500", ram_gb=64, ram_ecc=False, disks=None,
                        price_base=6000, multi_thread_score=30789, listing_id="s1"):
    raw = RawListing(
        listing_id=listing_id, datacenter="NBG1-DC1", location="NBG", available_from=None,
        cpu_raw=cpu, ram_gb=ram_gb, ram_ecc=ram_ecc,
        disks=disks if disks is not None else [DiskSpec(type="NVMe", count=2, capacity_gb=512)],
        uplink_speed=1000, price_base=price_base, price_setup_fee=0, fetched_at=datetime.now(UTC),
    )
    match = BenchmarkMatch(cpu_raw=cpu, matched=True, cpu_normalized=cpu, passmark_id=1, single_thread_score=4000,
                           multi_thread_score=multi_thread_score, cores=14, threads=20, match_method="direct")
    return CostMetricsEnricher().enrich_listing(raw, match)


I5_RULE = AlertRule(name="i5-13500 64GB+ 2xNVMe", max_price_cents=7000, min_ram_gb=64,
                    cpus=("Intel Core i5-13500",), min_nvme=2)
W2295_RULE = AlertRule(name="W-2295 128GB ECC", max_price_cents=13000, min_ram_gb=128,
                       cpus=("Intel Xeon W-2295",), ecc=True, min_nvme=2)
GLOBAL = global_rule(MAX_PRICE_CENTS, MIN_MULTI_THREAD_SCORE, MIN_RAM_GB)


class TestParseRules:
    def test_empty_or_missing_is_no_rules(self):
        assert parse_rules(None) == [] and parse_rules("") == [] and parse_rules("  ") == []

    def test_parses_the_production_shape(self):
        rules = parse_rules('[{"name":"i5","cpus":["Intel Core i5-13500"],"min_ram_gb":64,"min_nvme":2,"max_price_eur":70},'
                            ' {"name":"w","cpus":["Intel Xeon W-2295"],"min_ram_gb":128,"ecc":true,"min_nvme":2,"max_price_eur":129.99}]')
        assert [r.name for r in rules] == ["i5", "w"]
        assert rules[0].max_price_cents == 7000 and rules[0].ecc is None and rules[0].min_multi_thread_score is None
        assert rules[1].max_price_cents == 12999 and rules[1].ecc is True and rules[1].cpu_set() == {"intel xeon w-2295"}

    @pytest.mark.parametrize("bad", [
        "not json", '{"name":"x"}', '[{"cpus":["a"],"max_price_eur":10}]', '[{"name":"x"}]',
        '[{"name":"x","max_price_eur":"cheap"}]', '[{"name":"x","max_price_eur":0}]',
        '[{"name":"x","max_price_eur":10,"cpus":[]}]', '[{"name":"x","max_price_eur":10,"ecc":"yes"}]',
        '[{"name":"x","max_price_eur":10,"min_nvme":-1}]', '[{"name":"x","max_price_eur":10,"min_ram_gb":true}]',
    ])
    def test_malformed_rules_raise_naming_the_problem(self, bad):
        with pytest.raises(ValueError):
            parse_rules(bad)


class TestMatchesRule:
    def test_cpu_ram_nvme_price_all_hold(self):
        assert matches_rule(_make_typed_listing(price_base=6000), I5_RULE)  # €61.70 effective < €70

    def test_cpu_match_is_case_insensitive_exact(self):
        assert matches_rule(_make_typed_listing(cpu="intel core I5-13500"), I5_RULE)
        assert not matches_rule(_make_typed_listing(cpu="Intel Core i5-12500"), I5_RULE)

    def test_price_ceiling_is_inclusive_on_effective_price(self):
        # The enricher's effective price is what the rule sees (in production
        # that includes the IPv4 line); in these fixtures it equals price_base.
        assert matches_rule(_make_typed_listing(price_base=7000), I5_RULE)
        assert not matches_rule(_make_typed_listing(price_base=7001), I5_RULE)

    def test_nvme_floor_counts_only_nvme(self):
        assert not matches_rule(_make_typed_listing(disks=[DiskSpec(type="NVMe", count=1, capacity_gb=1000),
                                                           DiskSpec(type="SSD", count=2, capacity_gb=512)]), I5_RULE)
        assert matches_rule(_make_typed_listing(disks=[DiskSpec(type="NVMe", count=1, capacity_gb=1000),
                                                       DiskSpec(type="NVMe", count=2, capacity_gb=512)]), I5_RULE)

    def test_ram_floor_and_ecc_requirement(self):
        ok = _make_typed_listing(cpu="Intel Xeon W-2295", ram_gb=128, ram_ecc=True, price_base=11800)
        assert matches_rule(ok, W2295_RULE)
        assert not matches_rule(_make_typed_listing(cpu="Intel Xeon W-2295", ram_gb=64, ram_ecc=True, price_base=11800), W2295_RULE)
        assert not matches_rule(_make_typed_listing(cpu="Intel Xeon W-2295", ram_gb=128, ram_ecc=False, price_base=11800), W2295_RULE)

    def test_signature_rule_needs_no_benchmark_score(self):
        assert matches_rule(_make_typed_listing(multi_thread_score=None), I5_RULE)
        assert not matches_rule(_make_typed_listing(multi_thread_score=None), GLOBAL)

    def test_global_rule_is_the_legacy_criteria(self):
        cheap_fast = _make_listing(price_base=5000)
        assert matches_rule(cheap_fast, GLOBAL) and matching_rules(cheap_fast, [GLOBAL, I5_RULE]) == ("global",)


class TestEvaluateWithRules:
    @pytest.mark.asyncio
    async def test_listing_failing_global_but_matching_a_signature_rule_alerts_with_the_rule_tag(self):
        listing = _make_typed_listing(price_base=6500)  # €65.00: over the €60 global ceiling, under the i5 rule's €70
        ok = MagicMock(status_code=200); ok.raise_for_status = MagicMock()
        client = _mock_client(ok)
        with patch("httpx.AsyncClient", return_value=client):
            result = await evaluate_and_alert([listing], set(), "https://relay/send", rules=[GLOBAL, I5_RULE])
        assert result == {(listing.listing_id, build_config_signature(listing))}
        text = client.post.call_args.kwargs["json"]["text"]
        assert text.startswith("[i5-13500 64GB+ 2xNVMe] Hetzner auction: €65.00/mo")
        assert "64GB RAM" in text

    @pytest.mark.asyncio
    async def test_listing_matching_no_rule_is_silent(self):
        listing = _make_typed_listing(cpu="Intel Core i9-9900K", price_base=6500)
        client = AsyncMock()
        with patch("httpx.AsyncClient", return_value=client):
            result = await evaluate_and_alert([listing], set(), "https://relay/send", rules=[GLOBAL, I5_RULE])
        assert result == set() and not client.post.called

    @pytest.mark.asyncio
    async def test_two_rules_one_alert_naming_both(self):
        broad = AlertRule(name="any i5", max_price_cents=9000, cpus=("Intel Core i5-13500",))
        listing = _make_typed_listing(price_base=6000)
        ok = MagicMock(status_code=200); ok.raise_for_status = MagicMock()
        client = _mock_client(ok)
        with patch("httpx.AsyncClient", return_value=client):
            result = await evaluate_and_alert([listing], set(), "https://relay/send", rules=[I5_RULE, broad])
        assert len(result) == 1 and client.post.await_count == 1
        assert client.post.call_args.kwargs["json"]["text"].startswith("[i5-13500 64GB+ 2xNVMe] [any i5] ")

    @pytest.mark.asyncio
    async def test_rules_none_without_thresholds_is_a_usage_error(self):
        with pytest.raises(ValueError):
            await evaluate_and_alert([], set(), "https://relay/send")
