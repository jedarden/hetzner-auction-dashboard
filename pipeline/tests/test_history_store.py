"""
Unit tests for the v2 historical-value feature (docs/plan/plan.md
"Historical stats: value percentile & all-time-low").

Covers:
- config_signature / cpu cohort key generation and stability
- histogram accumulation across cycles (update_history)
- percentile derivation math, all-time-low detection, cohort fallback
- Parquet round-trip (write_history -> read back)
- HTTP fetch-back: bootstrap 404 vs. genuine failure (HistoryFetchError)
"""

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pyarrow.parquet as pq

from pipeline.cpu_matcher import BenchmarkMatch
from pipeline.enricher import CostMetricsEnricher
from pipeline.fetcher import DiskSpec, RawListing
from pipeline.history_store import (
    ConfigHistoryEntry,
    HistoryFetchError,
    _cohort_histogram,
    _percentile_from_histogram,
    _table_to_history,
    build_config_signature,
    build_cpu_cohort_key,
    compute_percentiles,
    fetch_history,
    history_to_table,
    update_history,
    write_history,
)


def _make_listing(
    listing_id="l1",
    datacenter="FSN1-DC3",
    cpu_raw="Intel Xeon E5-2680 v4",
    cpu_normalized="Intel Xeon E5-2680 v4",
    benchmark_matched=True,
    single_thread_score=1500,
    multi_thread_score=8000,
    ram_gb=64,
    ram_ecc=True,
    disks=None,
    price_base=2999,
    price_setup_fee=0,
):
    """Build a real EnrichedListing via the actual enricher, matching this
    repo's established test-fixture convention (see test_cost_metrics.py)."""
    if disks is None:
        disks = [DiskSpec(type="NVMe", count=2, capacity_gb=480)]

    raw = RawListing(
        listing_id=listing_id,
        datacenter=datacenter,
        location="FSN",
        available_from=None,
        cpu_raw=cpu_raw,
        ram_gb=ram_gb,
        ram_ecc=ram_ecc,
        disks=disks,
        uplink_speed=1000,
        price_base=price_base,
        price_setup_fee=price_setup_fee,
        fetched_at=datetime.now(UTC),
    )
    match = BenchmarkMatch(
        cpu_raw=cpu_raw,
        matched=benchmark_matched,
        cpu_normalized=cpu_normalized if benchmark_matched else None,
        passmark_id=1234 if benchmark_matched else None,
        single_thread_score=single_thread_score if benchmark_matched else None,
        multi_thread_score=multi_thread_score if benchmark_matched else None,
        cores=None,
        threads=None,
        match_method="direct" if benchmark_matched else None,
    )
    return CostMetricsEnricher().enrich_listing(raw, match)


class TestConfigSignature:
    def test_identical_configs_produce_identical_signature(self):
        a = _make_listing(listing_id="a")
        b = _make_listing(listing_id="b")  # listing_id must NOT affect the signature (EC-4)
        assert build_config_signature(a) == build_config_signature(b)

    def test_different_ram_changes_signature(self):
        a = _make_listing(ram_gb=64)
        b = _make_listing(ram_gb=128)
        assert build_config_signature(a) != build_config_signature(b)

    def test_different_datacenter_changes_signature(self):
        a = _make_listing(datacenter="FSN1-DC3")
        b = _make_listing(datacenter="NBG1-DC1")
        assert build_config_signature(a) != build_config_signature(b)

    def test_different_disk_layout_changes_signature(self):
        a = _make_listing(disks=[DiskSpec(type="NVMe", count=2, capacity_gb=480)])
        b = _make_listing(disks=[DiskSpec(type="HDD", count=4, capacity_gb=2048)])
        assert build_config_signature(a) != build_config_signature(b)

    def test_disk_order_does_not_change_signature(self):
        a = _make_listing(disks=[
            DiskSpec(type="NVMe", count=2, capacity_gb=480),
            DiskSpec(type="HDD", count=4, capacity_gb=2048),
        ])
        b = _make_listing(disks=[
            DiskSpec(type="HDD", count=4, capacity_gb=2048),
            DiskSpec(type="NVMe", count=2, capacity_gb=480),
        ])
        assert build_config_signature(a) == build_config_signature(b)

    def test_different_cpu_changes_signature(self):
        a = _make_listing(cpu_raw="Intel Xeon E5-2680 v4", cpu_normalized="Intel Xeon E5-2680 v4")
        b = _make_listing(cpu_raw="AMD EPYC 7401P", cpu_normalized="AMD EPYC 7401P")
        assert build_config_signature(a) != build_config_signature(b)

    def test_unmatched_cpu_falls_back_to_raw_string(self):
        listing = _make_listing(benchmark_matched=False, cpu_raw="Some Unknown CPU")
        assert build_cpu_cohort_key(listing) == "Some Unknown CPU"
        assert "Some Unknown CPU" in build_config_signature(listing)


class TestUpdateHistory:
    def test_first_observation_creates_entry(self):
        listing = _make_listing(price_base=2999)
        now = datetime(2026, 8, 14, 12, 0, 0, tzinfo=UTC)

        history = update_history({}, [listing], now)

        sig = build_config_signature(listing)
        entry = history[sig]
        assert entry.total_observations == 1
        assert entry.price_histogram == {listing.price_effective_monthly: 1}
        assert entry.min_price_effective_monthly == listing.price_effective_monthly
        assert entry.first_observed_at == now.isoformat()
        assert entry.last_observed_at == now.isoformat()

    def test_repeated_same_price_increments_count(self):
        listing = _make_listing(price_base=2999)
        t1 = datetime(2026, 8, 14, 12, 0, 0, tzinfo=UTC)
        t2 = datetime(2026, 8, 14, 12, 10, 0, tzinfo=UTC)

        history = update_history({}, [listing], t1)
        update_history(history, [listing], t2)

        sig = build_config_signature(listing)
        entry = history[sig]
        assert entry.total_observations == 2
        assert entry.price_histogram[listing.price_effective_monthly] == 2
        assert entry.first_observed_at == t1.isoformat()  # unchanged
        assert entry.last_observed_at == t2.isoformat()  # bumped

    def test_lower_price_updates_min_and_adds_histogram_bucket(self):
        cheap = _make_listing(price_base=1999)
        expensive = _make_listing(price_base=2999)
        t1 = datetime(2026, 8, 14, 12, 0, 0, tzinfo=UTC)
        t2 = datetime(2026, 8, 14, 12, 10, 0, tzinfo=UTC)

        history = update_history({}, [expensive], t1)
        update_history(history, [cheap], t2)

        sig = build_config_signature(expensive)
        entry = history[sig]
        assert entry.total_observations == 2
        assert entry.min_price_effective_monthly == cheap.price_effective_monthly
        assert len(entry.price_histogram) == 2

    def test_higher_price_does_not_lower_min(self):
        cheap = _make_listing(price_base=1999)
        expensive = _make_listing(price_base=2999)
        t = datetime(2026, 8, 14, 12, 0, 0, tzinfo=UTC)

        history = update_history({}, [cheap], t)
        update_history(history, [expensive], t)

        sig = build_config_signature(cheap)
        assert history[sig].min_price_effective_monthly == cheap.price_effective_monthly


class TestPercentileMath:
    def test_percentile_formula_matches_spec(self):
        # prices 90(x1), 100(x2), 110(x1) -- total 4 observations.
        # current=100 -> observations >= 100 are the two 100s and the 110 = 3/4
        histogram = {90: 1, 100: 2, 110: 1}
        assert _percentile_from_histogram(histogram, 4, 100) == pytest.approx(0.75)

    def test_percentile_for_price_below_everything_is_1(self):
        histogram = {90: 1, 100: 2, 110: 1}
        assert _percentile_from_histogram(histogram, 4, 50) == pytest.approx(1.0)

    def test_percentile_for_price_above_everything_is_0(self):
        histogram = {90: 1, 100: 2, 110: 1}
        assert _percentile_from_histogram(histogram, 4, 1000) == pytest.approx(0.0)

    def test_percentile_with_zero_observations_is_none(self):
        assert _percentile_from_histogram({}, 0, 100) is None


class TestComputePercentiles:
    def test_all_time_low_on_first_observation(self):
        listing = _make_listing(price_base=2999)
        history = update_history({}, [listing], datetime.now(UTC))

        compute_percentiles(history, [listing])

        assert listing.is_all_time_low is True
        assert listing.price_percentile_vs_history == pytest.approx(1.0)
        assert listing.history_sample_size == 1
        assert listing.history_cohort_fallback is False

    def test_not_all_time_low_when_price_rose(self):
        cheap = _make_listing(listing_id="cheap", price_base=1999)
        history = update_history({}, [cheap], datetime.now(UTC))

        expensive = _make_listing(listing_id="expensive", price_base=2999)
        update_history(history, [expensive], datetime.now(UTC))
        compute_percentiles(history, [expensive])

        assert expensive.is_all_time_low is False
        # 2 observations (1999, 2999); current=2999 -> only itself is >= 2999 -> 1/2
        assert expensive.price_percentile_vs_history == pytest.approx(0.5)

    def test_percentile_propagates_to_benchmark_metrics_when_matched(self):
        listing = _make_listing(benchmark_matched=True)
        history = update_history({}, [listing], datetime.now(UTC))
        compute_percentiles(history, [listing])

        assert listing.price_per_benchmark_point_multi_percentile_vs_history == listing.price_percentile_vs_history
        assert listing.price_per_benchmark_point_single_percentile_vs_history == listing.price_percentile_vs_history

    def test_percentile_is_none_for_benchmark_metrics_when_unmatched(self):
        listing = _make_listing(benchmark_matched=False)
        history = update_history({}, [listing], datetime.now(UTC))
        compute_percentiles(history, [listing])

        assert listing.price_percentile_vs_history is not None  # price history still tracked
        assert listing.price_per_benchmark_point_multi_percentile_vs_history is None
        assert listing.price_per_benchmark_point_single_percentile_vs_history is None

    def test_cohort_fallback_below_threshold(self):
        # Two different exact configs (different RAM) sharing the same CPU.
        a = _make_listing(listing_id="a", ram_gb=64, price_base=1999)
        b = _make_listing(listing_id="b", ram_gb=128, price_base=2999)

        history = update_history({}, [a], datetime.now(UTC))
        update_history(history, [b], datetime.now(UTC))

        # `a`'s own signature has only 1 observation -- below the default
        # threshold of 5 -- so it should fall back to the CPU-wide cohort
        # (a + b combined = 2 observations), not just its own 1.
        compute_percentiles(history, [a], min_observations=5)

        assert a.history_cohort_fallback is True
        assert a.history_sample_size == 2  # cohort total, not own total (1)

    def test_own_histogram_used_once_threshold_met(self):
        a = _make_listing(listing_id="a", ram_gb=64, price_base=1999)
        history = {}
        for i in range(3):
            update_history(history, [_make_listing(listing_id=f"a{i}", ram_gb=64, price_base=1999)], datetime.now(UTC))

        compute_percentiles(history, [a], min_observations=3)

        assert a.history_cohort_fallback is False
        assert a.history_sample_size == 3

    def test_cohort_histogram_merges_across_signatures(self):
        a = _make_listing(listing_id="a", ram_gb=64, price_base=1999)
        b = _make_listing(listing_id="b", ram_gb=128, price_base=2999)
        history = update_history({}, [a], datetime.now(UTC))
        update_history(history, [b], datetime.now(UTC))

        merged, total = _cohort_histogram(history, build_cpu_cohort_key(a))
        assert total == 2
        assert merged == {a.price_effective_monthly: 1, b.price_effective_monthly: 1}

    def test_no_history_at_all_is_none_percentile(self):
        # compute_percentiles called on a listing that was never folded into
        # `history` via update_history (defensive edge case).
        listing = _make_listing()
        compute_percentiles({}, [listing])

        assert listing.price_percentile_vs_history is None
        assert listing.history_sample_size == 0
        assert listing.is_all_time_low is False


class TestSerializationRoundTrip:
    def test_write_and_read_back(self):
        a = _make_listing(listing_id="a", price_base=1999)
        b = _make_listing(listing_id="b", price_base=2999)
        history = update_history({}, [a], datetime.now(UTC))
        update_history(history, [b], datetime.now(UTC))

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config_history.parquet"
            write_history(history, path)

            assert path.exists()
            table = pq.read_table(path)
            assert len(table) == 1  # a and b share one config_signature

            reloaded = _table_to_history(table)
            sig = build_config_signature(a)
            assert reloaded[sig].total_observations == 2
            assert reloaded[sig].min_price_effective_monthly == a.price_effective_monthly
            assert reloaded[sig].cpu_key == build_cpu_cohort_key(a)

    def test_schema_has_expected_columns(self):
        listing = _make_listing()
        history = update_history({}, [listing], datetime.now(UTC))
        table = history_to_table(history)

        assert set(table.column_names) == {
            "config_signature",
            "cpu_key",
            "first_observed_at",
            "last_observed_at",
            "total_observations",
            "min_price_effective_monthly",
            "price_histogram",
        }


class TestFetchHistory:
    @pytest.mark.asyncio
    async def test_404_returns_empty_history_not_an_error(self):
        mock_response = MagicMock(status_code=404)
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            history = await fetch_history("https://example.pages.dev/config_history.parquet")

        assert history == {}

    @pytest.mark.asyncio
    async def test_valid_200_response_parses_correctly(self):
        listing = _make_listing()
        history = update_history({}, [listing], datetime.now(UTC))
        table = history_to_table(history)

        import io
        buf = io.BytesIO()
        pq.write_table(table, buf)

        mock_response = MagicMock(status_code=200, content=buf.getvalue())
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            fetched = await fetch_history("https://example.pages.dev/config_history.parquet")

        sig = build_config_signature(listing)
        assert sig in fetched
        assert fetched[sig].total_observations == 1

    @pytest.mark.asyncio
    async def test_non_404_error_status_raises_history_fetch_error(self):
        mock_response = MagicMock(status_code=500)
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(HistoryFetchError):
                await fetch_history("https://example.pages.dev/config_history.parquet")

    @pytest.mark.asyncio
    async def test_network_error_raises_history_fetch_error(self):
        import httpx

        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.ConnectError("connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        # Must return a falsy value -- an AsyncMock's default truthy return
        # from __aexit__ tells the `async with` statement the exception was
        # handled, silently swallowing it (real httpx.AsyncClient.__aexit__
        # returns None/falsy, so this is purely a mock artifact to avoid).
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(HistoryFetchError):
                await fetch_history("https://example.pages.dev/config_history.parquet")

    @pytest.mark.asyncio
    async def test_corrupt_parquet_bytes_raises_history_fetch_error(self):
        mock_response = MagicMock(status_code=200, content=b"not a parquet file")
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(HistoryFetchError):
                await fetch_history("https://example.pages.dev/config_history.parquet")
