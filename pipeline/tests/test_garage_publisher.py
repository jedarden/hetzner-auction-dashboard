import io
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from pipeline.enricher import EnrichedListing
from pipeline.fetcher import DiskSpec
from pipeline.garage_publisher import (
    ARTIFACTS,
    GaragePublisher,
    GaragePublisherError,
    dataset_hash,
    validate_manifest,
)


def _listing(fetched_at):
    return EnrichedListing(
        listing_id="1",
        datacenter="FSN1-DC1",
        location="FSN",
        available_from=None,
        cpu_raw="CPU",
        cpu_normalized="CPU",
        benchmark_matched=True,
        passmark_id=1,
        single_thread_score=100,
        multi_thread_score=1000,
        cpu_cores=4,
        cpu_threads=8,
        benchmark_match_method="direct",
        ram_gb=64,
        ram_ecc=False,
        disks=[DiskSpec("NVMe", 2, 512)],
        uplink_speed=1000,
        price_base=5000,
        price_ipv4_monthly=100,
        price_setup_fee=0,
        fetched_at=fetched_at,
        price_effective_monthly=5100,
        price_per_benchmark_point_single=51,
        price_per_benchmark_point_multi=5.1,
        price_per_gb_ram=79.6875,
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
def test_manifest_is_written_after_verified_generation(
    mock_client, monkeypatch, tmp_path
):
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
    for name in (
        "current_snapshot.parquet",
        "config_history.parquet",
        "listing_history.parquet",
    ):
        pq.write_table(pa.table({"id": [1]}), tmp_path / name)
    (tmp_path / "unmatched-cpus.json").write_text("{}", encoding="utf-8")
    (tmp_path / "alerted-listings.json").write_text("[]", encoding="utf-8")

    publisher = GaragePublisher()
    manifest = publisher.publish(
        tmp_path, "abc", datetime(2026, 8, 22, 18, 31, tzinfo=UTC)
    )

    assert s3.upload_file.call_count == 5
    s3.put_object.assert_called_once()
    body = json.loads(s3.put_object.call_args.kwargs["Body"])
    assert body == manifest
    assert body["dataset_hash"] == "abc"
    assert all(
        path.startswith("generations/20260822T183100Z/")
        for path in body["files"].values()
    )
    # Regression (2026-08-27): alerted-listings.json was missing from ARTIFACTS,
    # so active_file_url("alerted-listings.json") always returned None and the
    # alert dedup fetch-back never found what the previous cycle just wrote --
    # same listing alerted twice in consecutive cycles.
    assert "alerted-listings.json" in body["files"]


@patch("pipeline.garage_publisher.boto3.client")
def test_storage_prefix_is_hidden_from_public_manifest(
    mock_client, monkeypatch, tmp_path
):
    for name, value in {
        "GARAGE_S3_ENDPOINT": "https://s3.example",
        "GARAGE_ACCESS_KEY_ID": "key",
        "GARAGE_SECRET_ACCESS_KEY": "secret",
        "GARAGE_BUCKET": "shared-bucket",
        "GARAGE_KEY_PREFIX": "hetzner-auction-data/",
        "GARAGE_PUBLIC_BASE_URL": "https://data.example",
    }.items():
        monkeypatch.setenv(name, value)
    s3 = MagicMock()
    s3.head_object.side_effect = lambda **kw: {
        "ContentLength": (tmp_path / Path(kw["Key"]).name).stat().st_size
    }
    mock_client.return_value = s3
    for name in (
        "current_snapshot.parquet",
        "config_history.parquet",
        "listing_history.parquet",
    ):
        pq.write_table(pa.table({"id": [1]}), tmp_path / name)
    (tmp_path / "unmatched-cpus.json").write_text("{}", encoding="utf-8")
    (tmp_path / "alerted-listings.json").write_text("[]", encoding="utf-8")

    manifest = GaragePublisher().publish(
        tmp_path, "abc", datetime(2026, 8, 22, 18, 31, tzinfo=UTC)
    )

    assert all(
        call.args[2].startswith("hetzner-auction-data/generations/")
        for call in s3.upload_file.call_args_list
    )
    assert s3.put_object.call_args.kwargs["Key"] == "hetzner-auction-data/manifest.json"
    assert all(path.startswith("generations/") for path in manifest["files"].values())


def _write_generation(directory: Path) -> None:
    for name in (
        "current_snapshot.parquet",
        "config_history.parquet",
        "listing_history.parquet",
    ):
        pq.write_table(pa.table({"id": [1]}), directory / name)
    (directory / "unmatched-cpus.json").write_text("{}", encoding="utf-8")
    (directory / "alerted-listings.json").write_text("[]", encoding="utf-8")


def _publisher(mock_client, monkeypatch):
    for name, value in {
        "GARAGE_S3_ENDPOINT": "https://s3.example",
        "GARAGE_ACCESS_KEY_ID": "key",
        "GARAGE_SECRET_ACCESS_KEY": "secret",
        "GARAGE_BUCKET": "bucket",
        "GARAGE_PUBLIC_BASE_URL": "https://data.example",
    }.items():
        monkeypatch.setenv(name, value)
    s3 = MagicMock()
    mock_client.return_value = s3
    return GaragePublisher(), s3


@patch("pipeline.garage_publisher.boto3.client")
def test_local_generation_is_complete_before_any_upload(
    mock_client, monkeypatch, tmp_path
):
    publisher, s3 = _publisher(mock_client, monkeypatch)
    _write_generation(tmp_path)
    (tmp_path / "alerted-listings.json").unlink()

    with pytest.raises(GaragePublisherError, match="alerted-listings.json"):
        publisher.publish(tmp_path, "abc", datetime(2026, 8, 22, 18, 31, tzinfo=UTC))

    s3.upload_file.assert_not_called()
    s3.put_object.assert_not_called()


@patch("pipeline.garage_publisher.boto3.client")
def test_remote_verification_failure_does_not_replace_prior_manifest(
    mock_client, monkeypatch, tmp_path
):
    publisher, s3 = _publisher(mock_client, monkeypatch)
    _write_generation(tmp_path)
    prior = {
        "schema_version": 1,
        "generation": "20260822T183000Z",
        "published_at": "2026-08-22T18:30:04+00:00",
        "dataset_hash": "prior",
        "files": {name: f"generations/20260822T183000Z/{name}" for name in ARTIFACTS},
    }
    s3.get_object.return_value = {"Body": io.BytesIO(json.dumps(prior).encode())}
    sizes = [(tmp_path / name).stat().st_size for name in ARTIFACTS]
    s3.head_object.side_effect = [{"ContentLength": size} for size in sizes[:-1]] + [
        {"ContentLength": 0}
    ]

    with pytest.raises(GaragePublisherError, match="Remote verification failed"):
        publisher.publish(tmp_path, "new", datetime(2026, 8, 22, 18, 31, tzinfo=UTC))

    assert publisher.get_manifest() == prior
    s3.put_object.assert_not_called()


@patch("pipeline.garage_publisher.boto3.client")
def test_unchanged_dataset_suppresses_all_publication(
    mock_client, monkeypatch, tmp_path
):
    publisher, s3 = _publisher(mock_client, monkeypatch)
    current = {
        "schema_version": 1,
        "generation": "20260822T183000Z",
        "published_at": "2026-08-22T18:30:04+00:00",
        "dataset_hash": "same",
        "files": {name: f"generations/20260822T183000Z/{name}" for name in ARTIFACTS},
    }
    s3.get_object.return_value = {"Body": io.BytesIO(json.dumps(current).encode())}

    assert publisher.publish_if_changed(tmp_path, "same") is None
    s3.upload_file.assert_not_called()
    s3.put_object.assert_not_called()


def test_manifest_contract_rejects_wrong_generation_paths():
    manifest = {
        "schema_version": 1,
        "generation": "20260822T183100Z",
        "published_at": "2026-08-22T18:31:04+00:00",
        "dataset_hash": "abc",
        "files": {name: f"generations/20260822T183100Z/{name}" for name in ARTIFACTS},
    }
    manifest["files"]["current_snapshot.parquet"] = (
        "other-generation/current_snapshot.parquet"
    )

    with pytest.raises(GaragePublisherError, match="file path"):
        validate_manifest(manifest)
