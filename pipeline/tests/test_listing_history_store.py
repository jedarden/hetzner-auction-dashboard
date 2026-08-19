from datetime import UTC, datetime, timedelta

import pyarrow.parquet as pq

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
