import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pyarrow.parquet as pq

from pipeline.enricher import EnrichedListing
from pipeline.fetcher import DiskSpec
from pipeline.garage_publisher import GaragePublisher, dataset_hash


def _listing(fetched_at):
    return EnrichedListing(
        listing_id="1", datacenter="FSN1-DC1", location="FSN", available_from=None,
        cpu_raw="CPU", cpu_normalized="CPU", benchmark_matched=True, passmark_id=1,
        single_thread_score=100, multi_thread_score=1000, cpu_cores=4, cpu_threads=8,
        benchmark_match_method="direct", ram_gb=64, ram_ecc=False,
        disks=[DiskSpec("NVMe", 2, 512)], uplink_speed=1000, price_base=5000,
        price_ipv4_monthly=100, price_setup_fee=0, fetched_at=fetched_at,
        price_effective_monthly=5100, price_per_benchmark_point_single=51,
        price_per_benchmark_point_multi=5.1, price_per_gb_ram=79.6875,
        price_per_tb_disk=4980.46875,
    )


def test_dataset_hash_ignores_poll_timestamp_and_history_annotations():
    first = _listing(datetime(2026, 8, 22, tzinfo=UTC))
    second = _listing(datetime(2026, 8, 23, tzinfo=UTC))
    second.history_sample_size = 99
    assert dataset_hash([first]) == dataset_hash([second])
    second.price_base = 4900
    assert dataset_hash([first]) != dataset_hash([second])


@patch("pipeline.garage_publisher.boto3.client")
def test_manifest_is_written_after_verified_generation(mock_client, monkeypatch, tmp_path):
    for name, value in {
        "GARAGE_S3_ENDPOINT": "https://s3.example",
        "GARAGE_ACCESS_KEY_ID": "key",
        "GARAGE_SECRET_ACCESS_KEY": "secret",
        "GARAGE_BUCKET": "bucket",
        "GARAGE_PUBLIC_BASE_URL": "https://data.example",
    }.items():
        monkeypatch.setenv(name, value)
    s3 = MagicMock()
    s3.head_object.side_effect = lambda **kw: {
        "ContentLength": (tmp_path / Path(kw["Key"]).name).stat().st_size
    }
    mock_client.return_value = s3
    for name in ("current_snapshot.parquet", "config_history.parquet", "listing_history.parquet"):
        pq.write_table(pa.table({"id": [1]}), tmp_path / name)
    (tmp_path / "unmatched-cpus.json").write_text("{}", encoding="utf-8")

    publisher = GaragePublisher()
    manifest = publisher.publish(tmp_path, "abc", datetime(2026, 8, 22, 18, 31, tzinfo=UTC))

    assert s3.upload_file.call_count == 4
    s3.put_object.assert_called_once()
    body = json.loads(s3.put_object.call_args.kwargs["Body"])
    assert body == manifest
    assert body["dataset_hash"] == "abc"
    assert all(path.startswith("generations/20260822T183100Z/") for path in body["files"].values())
