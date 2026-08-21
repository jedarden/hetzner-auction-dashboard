from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq

from pipeline.cpu_matcher import CpuMatcher
from pipeline.listing_history_store import update_listing_history, write_listing_history
from test_pages_publisher import _make_sample_listing


def test_tracks_lifecycle_and_marks_missing_listing_inactive(tmp_path):
    history = {}
    first = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    listing = _make_sample_listing()
    listing.config_signature = "cpu|64|true|ssd|FSN1"

    update_listing_history(history, [listing], first)
    assert len(history) == 1
    item = next(iter(history.values()))
    assert item.row["active"] is True
    assert item.row["observation_count"] == 1

    listing.price_effective_monthly -= 100
    update_listing_history(history, [listing], first + timedelta(minutes=10))
    assert item.row["observation_count"] == 2
    assert item.row["lowest_price_effective_monthly"] == listing.price_effective_monthly

    update_listing_history(history, [], first + timedelta(minutes=20))
    assert item.row["active"] is False
    assert item.row["inactive_at"] is not None

    path = tmp_path / "listing_history.parquet"
    write_listing_history(history, path)
    row = pq.read_table(path).to_pylist()[0]
    assert row["listing_instance_id"]
    assert row["active"] is False


def test_reappearance_creates_a_new_lifecycle():
    history = {}
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    listing = _make_sample_listing()
    listing.config_signature = "same-config"
    update_listing_history(history, [listing], now)
    update_listing_history(history, [], now + timedelta(minutes=10))
    update_listing_history(history, [listing], now + timedelta(minutes=20))
    assert len(history) == 2
    assert sum(item.row["active"] for item in history.values()) == 1


def test_prunes_inactive_rows_outside_retention():
    history = {}
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    listing = _make_sample_listing()
    listing.config_signature = "old-config"
    update_listing_history(history, [listing], now - timedelta(days=200))
    update_listing_history(history, [], now - timedelta(days=199))
    update_listing_history(history, [], now, retention_days=180)
    assert history == {}


def test_inactive_row_self_heals_once_benchmark_map_covers_its_cpu():
    """
    A row that was unmatched when first observed, then went inactive, must
    pick up a later benchmark-map fix on the next cycle -- even though it
    never reappears in a live fetch. Without this, a CPU added to
    reference.csv after a listing went inactive stays "Unscored" forever
    (frozen at whatever match state it had when its benchmark fields were
    last written).
    """
    cpu_matcher = CpuMatcher(Path(__file__).parent.parent.parent / "benchmark-map")
    history = {}
    now = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)

    # AMD EPYC 7502 IS in reference.csv (added alongside this test) -- but
    # simulate a row recorded back when it wasn't, i.e. benchmark_matched=False.
    unmatched_listing = _make_sample_listing(
        cpu_raw="AMD EPYC 7502",
        cpu_normalized=None,
        benchmark_matched=False,
        passmark_id=None,
        single_thread_score=None,
        multi_thread_score=None,
        benchmark_match_method=None,
    )
    unmatched_listing.config_signature = "epyc-7502-config"

    # A genuinely-unmatchable CPU must NOT get healed into a false match.
    truly_unmatched_listing = _make_sample_listing(
        listing_id="test-listing-2",
        cpu_raw="Totally Fictional CPU Model Zeta-9000",
        cpu_normalized=None,
        benchmark_matched=False,
        passmark_id=None,
        single_thread_score=None,
        multi_thread_score=None,
        benchmark_match_method=None,
    )
    truly_unmatched_listing.config_signature = "fictional-cpu-config"

    update_listing_history(history, [unmatched_listing, truly_unmatched_listing], now, cpu_matcher=None)
    update_listing_history(history, [], now + timedelta(minutes=10), cpu_matcher=None)

    rows_by_cpu = {item.row["cpu_raw"]: item.row for item in history.values()}
    assert rows_by_cpu["AMD EPYC 7502"]["benchmark_matched"] is False
    assert rows_by_cpu["Totally Fictional CPU Model Zeta-9000"]["benchmark_matched"] is False

    # Next cycle runs with a matcher that now covers AMD EPYC 7502.
    update_listing_history(history, [], now + timedelta(minutes=20), cpu_matcher=cpu_matcher)

    rows_by_cpu = {item.row["cpu_raw"]: item.row for item in history.values()}
    healed = rows_by_cpu["AMD EPYC 7502"]
    assert healed["benchmark_matched"] is True
    assert healed["passmark_id"] == 3880
    assert healed["single_thread_score"] == 2098
    assert healed["multi_thread_score"] == 51871
    assert healed["cpu_normalized"] == "AMD EPYC 7502"
    assert healed["benchmark_match_method"] == "direct"
    assert healed["price_per_benchmark_point_single"] == (
        healed["price_effective_monthly"] / 2098
    )
    assert healed["price_per_benchmark_point_multi"] == (
        healed["price_effective_monthly"] / 51871
    )
    # active/lifecycle bookkeeping must be untouched by the heal
    assert healed["active"] is False

    still_unmatched = rows_by_cpu["Totally Fictional CPU Model Zeta-9000"]
    assert still_unmatched["benchmark_matched"] is False
    assert still_unmatched["passmark_id"] is None
