"""Tests for the per-offer configuration index (pipeline/offer_index.py)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pyarrow.parquet as pq
import pytest

from pipeline.fetcher import DiskSpec
from pipeline.history_store import build_config_signature
from pipeline.listing_history_store import update_listing_history
from pipeline.offer_index import (
    OFFER_INDEX_SCHEMA,
    OfferIndexFetchError,
    compute_offer_percentiles,
    fetch_offer_index,
    hardware_key,
    merge_listing_history,
    observe_listings,
    offers_to_table,
    split_signature,
    table_to_offers,
    write_offer_index,
)
from test_history_store import _make_listing

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def _offers(*cycles):
    """Fold successive cycles of listings into a fresh index."""
    offers = {}
    for i, listings in enumerate(cycles):
        observe_listings(offers, listings, T0 + timedelta(minutes=i))
    return offers


class TestSignatureParsing:
    def test_split_and_hardware_key(self):
        listing = _make_listing(
            datacenter="FSN1-DC5",
            disks=[DiskSpec(type="NVMe", count=1, capacity_gb=512), DiskSpec(type="HDD", count=2, capacity_gb=8000)],
        )
        sig = build_config_signature(listing)
        assert split_signature(sig) == (
            "Intel Xeon E5-2680 v4", 64, True, "HDD:2:8000,NVMe:1:512", "FSN1-DC5"
        )
        assert hardware_key(sig) == sig.rsplit("|", 1)[0]
        assert "FSN1" not in hardware_key(sig)

    def test_cpu_name_containing_pipe_still_parses(self):
        assert split_signature("Odd|CPU|32|False|SSD:2:512|HEL1-DC2")[0] == "Odd|CPU"


class TestObserve:
    def test_one_offer_per_listing_id_whatever_the_cycle_count(self):
        a = _make_listing(listing_id="a", price_base=3000)
        offers = _offers([a], [a], [a], [a])
        assert len(offers) == 1

    def test_price_drop_tracks_first_lowest_last(self):
        offers = _offers(
            [_make_listing(listing_id="a", price_base=3000)],
            [_make_listing(listing_id="a", price_base=2500)],
            [_make_listing(listing_id="a", price_base=2700)],
        )
        (offer,) = offers.values()
        assert offer.first_price - offer.lowest_price == 500
        assert offer.last_price - offer.lowest_price == 200
        assert offer.first_seen_at == T0.isoformat()
        assert offer.last_seen_at == (T0 + timedelta(minutes=2)).isoformat()

    def test_same_hardware_in_two_datacenters_is_two_offers_one_hardware(self):
        offers = _offers([
            _make_listing(listing_id="a", datacenter="FSN1-DC1"),
            _make_listing(listing_id="b", datacenter="HEL1-DC2"),
        ])
        assert len(offers) == 2
        assert len({hardware_key(o.config_signature) for o in offers.values()}) == 1


class TestMergeListingHistory:
    def test_relisted_lifecycles_collapse_to_one_offer(self):
        """A flapping feed re-creates lifecycles for the same server; the
        index must still count it once (2026-10-27)."""
        history = {}
        a = _make_listing(listing_id="a", price_base=3000)
        for i in range(0, 10, 2):
            update_listing_history(history, [a], T0 + timedelta(minutes=i))
            update_listing_history(history, [], T0 + timedelta(minutes=i + 1))
        assert len(history) == 5

        offers = merge_listing_history({}, history)
        assert len(offers) == 1
        (offer,) = offers.values()
        assert offer.first_seen_at == T0.isoformat()

    def test_merge_is_idempotent_and_keeps_offers_aged_out_of_history(self):
        history = {}
        a = _make_listing(listing_id="a", price_base=3000)
        update_listing_history(history, [a], T0)
        offers = merge_listing_history({}, history)
        merge_listing_history(offers, history)
        assert len(offers) == 1

        # listing_history drops the lifecycle after its retention window ...
        update_listing_history(history, [], T0 + timedelta(minutes=1))
        update_listing_history(history, [], T0 + timedelta(days=200), retention_days=180)
        assert history == {}
        # ... but the index keeps the offer.
        merge_listing_history(offers, history)
        assert len(offers) == 1


class TestPercentiles:
    def test_first_offer_is_its_own_all_time_low(self):
        a = _make_listing(listing_id="a")
        offers = _offers([a])
        compute_offer_percentiles(offers, [a])
        assert a.is_all_time_low is True
        assert a.price_percentile_vs_history == pytest.approx(1.0)
        assert a.history_sample_size == 1

    def test_each_offer_counts_once_however_long_it_stays_listed(self):
        """Observation weighting let one unsold offer dominate; offers don't."""
        slow = _make_listing(listing_id="slow", price_base=4000)
        cycles = [[slow]] * 50
        current = _make_listing(listing_id="new", price_base=3000)
        cheap = [_make_listing(listing_id=f"c{i}", price_base=2000) for i in range(4)]
        offers = _offers(*cycles, cheap, [current])
        compute_offer_percentiles(offers, [current])
        # Offers' lowest prices: 4000, 2000 x4, 3000 -> at or above 3000: 2 of 6.
        assert current.history_sample_size == 6
        assert current.price_percentile_vs_history == pytest.approx(2 / 6)
        assert current.is_all_time_low is False

    def test_ranks_across_datacenters(self):
        others = [_make_listing(listing_id=f"o{i}", datacenter="HEL1-DC2", price_base=5000) for i in range(5)]
        current = _make_listing(listing_id="x", datacenter="FSN1-DC1", price_base=1000)
        offers = _offers(others, [current])
        compute_offer_percentiles(offers, [current])
        assert current.history_cohort_fallback is False
        assert current.history_sample_size == 6
        assert current.is_all_time_low is True

    def test_cpu_cohort_fallback_below_threshold(self):
        a = _make_listing(listing_id="a", ram_gb=64)
        b = _make_listing(listing_id="b", ram_gb=128)
        offers = _offers([a, b])
        compute_offer_percentiles(offers, [a], min_offers=5)
        assert a.history_cohort_fallback is True
        assert a.history_sample_size == 2
        # All-time low stays about the exact hardware, not the cohort.
        assert a.is_all_time_low is True

    def test_benchmark_percentiles_follow_match(self):
        matched = _make_listing(listing_id="m", benchmark_matched=True)
        unmatched = _make_listing(listing_id="u", benchmark_matched=False, cpu_raw="Mystery CPU")
        offers = _offers([matched, unmatched])
        compute_offer_percentiles(offers, [matched, unmatched])
        assert matched.price_per_benchmark_point_multi_percentile_vs_history == matched.price_percentile_vs_history
        assert unmatched.price_percentile_vs_history is not None
        assert unmatched.price_per_benchmark_point_multi_percentile_vs_history is None

    def test_no_offers_known(self):
        a = _make_listing()
        compute_offer_percentiles({}, [a])
        assert a.price_percentile_vs_history is None
        assert a.history_sample_size == 0
        assert a.is_all_time_low is False


class TestSerialization:
    def test_round_trip(self, tmp_path):
        offers = _offers(
            [_make_listing(listing_id="a", price_base=3000), _make_listing(listing_id="b", ram_gb=128)],
            [_make_listing(listing_id="a", price_base=2000)],
        )
        path = tmp_path / "offer_index.parquet"
        write_offer_index(offers, path)
        table = pq.read_table(path)
        assert table.schema.equals(OFFER_INDEX_SCHEMA)
        assert table_to_offers(table) == offers
        row = next(r for r in table.to_pylist() if r["listing_id"] == "a")
        assert row["ram_gb"] == 64 and row["ram_ecc"] is True
        assert row["hardware_key"] == hardware_key(row["config_signature"])

    def test_empty_index_writes_a_valid_file(self):
        assert offers_to_table({}).num_rows == 0


def _client_returning(response=None, error=None):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=response, side_effect=error)
    return client


class TestFetch:
    @pytest.mark.asyncio
    async def test_no_url_is_bootstrap(self):
        assert await fetch_offer_index(None) == {}

    @pytest.mark.asyncio
    async def test_round_trips_a_published_index(self, tmp_path):
        offers = _offers([_make_listing(listing_id="a")])
        path = tmp_path / "offer_index.parquet"
        write_offer_index(offers, path)
        response = MagicMock(status_code=200, content=path.read_bytes())
        with patch("pipeline.offer_index.httpx.AsyncClient", return_value=_client_returning(response)):
            assert await fetch_offer_index("https://x/offer_index.parquet") == offers

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [404, 500])
    async def test_named_but_unreadable_index_aborts(self, status):
        """The manifest names it, so a 404 is a failure, not a bootstrap."""
        response = MagicMock(status_code=status, content=b"")
        with patch("pipeline.offer_index.httpx.AsyncClient", return_value=_client_returning(response)):
            with pytest.raises(OfferIndexFetchError):
                await fetch_offer_index("https://x/offer_index.parquet")

    @pytest.mark.asyncio
    async def test_network_error_and_corrupt_bytes_abort(self):
        with patch("pipeline.offer_index.httpx.AsyncClient",
                   return_value=_client_returning(error=httpx.ConnectError("down"))):
            with pytest.raises(OfferIndexFetchError):
                await fetch_offer_index("https://x/offer_index.parquet")
        response = MagicMock(status_code=200, content=b"not parquet")
        with patch("pipeline.offer_index.httpx.AsyncClient", return_value=_client_returning(response)):
            with pytest.raises(OfferIndexFetchError):
                await fetch_offer_index("https://x/offer_index.parquet")


class TestSeedFromConfigHistory:
    def test_configs_seen_only_before_listing_history_get_one_placeholder(self):
        from pipeline.history_store import update_history
        from pipeline.offer_index import UNKNOWN_LISTING_ID, seed_from_config_history

        old = _make_listing(listing_id="gone", ram_gb=32, price_base=1500)
        current = _make_listing(listing_id="now", ram_gb=64)
        history = update_history({}, [old], T0)
        update_history(history, [old], T0 + timedelta(minutes=1))
        update_history(history, [current], T0 + timedelta(minutes=2))

        offers = _offers([current])
        seed_from_config_history(offers, history)
        seed_from_config_history(offers, history)

        assert len(offers) == 2
        placeholder = offers[(build_config_signature(old), UNKNOWN_LISTING_ID)]
        assert placeholder.lowest_price == old.price_effective_monthly
        assert placeholder.first_seen_at == T0.isoformat()
        # A configuration that has real offers is never seeded.
        assert (build_config_signature(current), UNKNOWN_LISTING_ID) not in offers
