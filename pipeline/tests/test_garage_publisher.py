import io
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from botocore.exceptions import ClientError

from pipeline.enricher import EnrichedListing
from pipeline.fetcher import DiskSpec
from pipeline.garage_publisher import (
    ARTIFACTS,
    OPTIONAL_ARTIFACTS,
    GaragePublisher,
    GaragePublisherError,
    dataset_hash,
    generation_retention_from_env,
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
    s3.get_object.side_effect = lambda **kw: {
        "Body": io.BytesIO((tmp_path / Path(kw["Key"]).name).read_bytes())
    }
    mock_client.return_value = s3
    for name in (
        "current_snapshot.parquet",
        "config_history.parquet",
        "listing_history.parquet",
        "offer_index.parquet",
    ):
        pq.write_table(pa.table({"id": [1]}), tmp_path / name)
    (tmp_path / "unmatched-cpus.json").write_text("{}", encoding="utf-8")
    (tmp_path / "alerted-listings.json").write_text("[]", encoding="utf-8")

    publisher = GaragePublisher()
    manifest = publisher.publish(
        tmp_path, "abc", datetime(2026, 8, 22, 18, 31, tzinfo=UTC)
    )

    assert s3.upload_file.call_count == len(ARTIFACTS)
    s3.put_object.assert_called_once()
    body = json.loads(s3.put_object.call_args.kwargs["Body"])
    assert body == manifest
    assert body["dataset_hash"] == "abc"
    assert manifest["generation"].startswith("20260822T183100Z-")
    assert all(
        path.startswith(f"generations/{manifest['generation']}/")
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
    s3.get_object.side_effect = lambda **kw: {
        "Body": io.BytesIO((tmp_path / Path(kw["Key"]).name).read_bytes())
    }
    mock_client.return_value = s3
    for name in (
        "current_snapshot.parquet",
        "config_history.parquet",
        "listing_history.parquet",
        "offer_index.parquet",
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
        "offer_index.parquet",
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
    s3.get_object.side_effect = lambda **kw: {
        "Body": io.BytesIO(
            json.dumps(prior).encode()
            if kw["Key"] == "manifest.json"
            else (tmp_path / Path(kw["Key"]).name).read_bytes()
        )
    }
    sizes = [(tmp_path / name).stat().st_size for name in ARTIFACTS]
    s3.head_object.side_effect = [{"ContentLength": size} for size in sizes[:-1]] + [
        {"ContentLength": 0}
    ]

    with pytest.raises(GaragePublisherError, match="Remote verification failed"):
        publisher.publish(tmp_path, "new", datetime(2026, 8, 22, 18, 31, tzinfo=UTC))

    assert publisher.get_manifest() == prior
    s3.put_object.assert_not_called()


@patch("pipeline.garage_publisher.boto3.client")
def test_same_second_retry_cannot_overwrite_live_generation(
    mock_client, monkeypatch, tmp_path
):
    publisher, s3 = _publisher(mock_client, monkeypatch)
    _write_generation(tmp_path)
    s3.head_object.side_effect = lambda **kw: {
        "ContentLength": (tmp_path / Path(kw["Key"]).name).stat().st_size
    }
    s3.get_object.side_effect = lambda **kw: {
        "Body": io.BytesIO((tmp_path / Path(kw["Key"]).name).read_bytes())
    }
    now = datetime(2026, 8, 22, 18, 31, tzinfo=UTC)
    prior = publisher.publish(tmp_path, "first", now)
    prior_keys = {call.args[2] for call in s3.upload_file.call_args_list}

    (tmp_path / "unmatched-cpus.json").write_text('{"new": true}', encoding="utf-8")
    s3.head_object.side_effect = lambda **kw: {"ContentLength": 0}
    with pytest.raises(GaragePublisherError, match="Remote verification failed"):
        publisher.publish(tmp_path, "second", now)

    retry_keys = {call.args[2] for call in s3.upload_file.call_args_list[len(ARTIFACTS):]}
    assert prior_keys.isdisjoint(retry_keys)
    s3.put_object.assert_called_once()
    s3.get_object.side_effect = lambda **kw: {
        "Body": io.BytesIO(json.dumps(prior).encode())
    }
    assert publisher.get_manifest() == prior


@patch("pipeline.garage_publisher.boto3.client")
def test_local_parquet_data_pages_are_read_before_upload(
    mock_client, monkeypatch, tmp_path
):
    publisher, s3 = _publisher(mock_client, monkeypatch)
    _write_generation(tmp_path)
    path = tmp_path / "current_snapshot.parquet"
    pq.write_table(pa.table({"x": list(range(10000))}), path, compression="gzip")
    damaged = bytearray(path.read_bytes())
    damaged[20] ^= 0xFF
    path.write_bytes(damaged)
    pq.read_metadata(path)  # The footer alone is still valid.

    with pytest.raises(GaragePublisherError, match="current_snapshot.parquet"):
        publisher.publish(tmp_path, "new")
    s3.upload_file.assert_not_called()
    s3.put_object.assert_not_called()


@patch("pipeline.garage_publisher.boto3.client")
def test_equal_length_remote_corruption_keeps_prior_pointer(
    mock_client, monkeypatch, tmp_path
):
    publisher, s3 = _publisher(mock_client, monkeypatch)
    _write_generation(tmp_path)
    prior = _manifest_for("20260822T183000Z")
    s3.head_object.side_effect = lambda **kw: {
        "ContentLength": (tmp_path / Path(kw["Key"]).name).stat().st_size
    }

    def remote_object(**kw):
        if kw["Key"] == "manifest.json":
            return {"Body": io.BytesIO(json.dumps(prior).encode())}
        filename = Path(kw["Key"]).name
        payload = (
            b"[]"
            if filename == "unmatched-cpus.json"
            else (tmp_path / filename).read_bytes()
        )
        return {"Body": io.BytesIO(payload)}

    s3.get_object.side_effect = remote_object
    with pytest.raises(GaragePublisherError, match="content differs"):
        publisher.publish(tmp_path, "new")
    s3.put_object.assert_not_called()
    assert publisher.get_manifest() == prior


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


@pytest.mark.parametrize(
    "generation",
    ["20260822T183100Z", "20260822T183100Z-0123456789abcdef0123456789abcdef"],
)
def test_manifest_contract_accepts_legacy_and_unique_generations(generation):
    assert validate_manifest(_manifest_for(generation))["generation"] == generation


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 2),
        ("generation", "20261322T183100Z"),
        ("published_at", "2026-08-22T18:31:04"),
    ],
)
def test_manifest_contract_rejects_invalid_version_and_timestamps(field, value):
    manifest = _manifest_for("20260822T183100Z")
    manifest[field] = value
    with pytest.raises(GaragePublisherError):
        validate_manifest(manifest)


def test_manifest_without_offer_index_is_still_valid():
    """The manifest live when offer_index.parquet was introduced cannot name
    it; rejecting it would fail every cycle's change check forever."""
    manifest = {
        "schema_version": 1,
        "generation": "20261109T125951Z",
        "published_at": "2026-11-09T12:59:51+00:00",
        "dataset_hash": "abc",
        "files": {
            name: f"generations/20261109T125951Z/{name}"
            for name in ARTIFACTS
            if name not in OPTIONAL_ARTIFACTS
        },
    }
    assert validate_manifest(manifest) is manifest


@pytest.mark.parametrize("drop,extra", [("listing_history.parquet", None), (None, "surprise.parquet")])
def test_manifest_contract_rejects_missing_required_or_unknown_files(drop, extra):
    generation = "20261109T125951Z"
    files = {name: f"generations/{generation}/{name}" for name in ARTIFACTS if name != drop}
    if extra:
        files[extra] = f"generations/{generation}/{extra}"
    manifest = {
        "schema_version": 1,
        "generation": generation,
        "published_at": "2026-11-09T12:59:51+00:00",
        "dataset_hash": "abc",
        "files": files,
    }
    with pytest.raises(GaragePublisherError, match="every published artifact"):
        validate_manifest(manifest)


def _manifest_for(generation):
    return {
        "schema_version": 1,
        "generation": generation,
        "published_at": "2026-08-22T18:30:04+00:00",
        "dataset_hash": "h",
        "files": {name: f"generations/{generation}/{name}" for name in ARTIFACTS},
    }


def _stage_bucket(s3, manifest, keys):
    s3.get_object.side_effect = lambda **kw: {
        "Body": io.BytesIO(json.dumps(manifest).encode())
    }
    paginator = MagicMock()
    paginator.paginate.return_value = [
        {"Contents": [{"Key": k} for k in keys[:3]]},
        {"Contents": [{"Key": k} for k in keys[3:]]},
    ]
    s3.get_paginator.return_value = paginator
    s3.delete_objects.return_value = {}
    return paginator


def _deleted_keys(s3):
    return [
        obj["Key"]
        for call in s3.delete_objects.call_args_list
        for obj in call.kwargs["Delete"]["Objects"]
    ]


@patch("pipeline.garage_publisher.boto3.client")
def test_prune_keeps_live_and_newest_generations(mock_client, monkeypatch):
    monkeypatch.setenv("GARAGE_KEY_PREFIX", "hetzner-auction-data")
    publisher, s3 = _publisher(mock_client, monkeypatch)
    gens = [f"20261101T0{h}0000Z" for h in range(6)]  # 00..05, oldest first
    keys = [
        f"hetzner-auction-data/generations/{g}/{name}"
        for g in gens
        for name in ("current_snapshot.parquet", "listing_history.parquet")
    ]
    # Junk that must never be touched even though it sits under the prefix.
    keys += [
        "hetzner-auction-data/generations/not-a-generation/x.parquet",
        "hetzner-auction-data/generations/20261101T000000Z/nested/x.parquet",
    ]
    # Live manifest points at an OLD generation (e.g. later publishes failed
    # after uploading but before the pointer swap); it must survive.
    paginator = _stage_bucket(s3, _manifest_for(gens[1]), keys)

    assert publisher.prune_generations(keep=2) == 3

    paginator.paginate.assert_called_once_with(
        Bucket="bucket", Prefix="hetzner-auction-data/generations/"
    )
    deleted = set(_deleted_keys(s3))
    expected = {
        f"hetzner-auction-data/generations/{g}/{name}"
        for g in (gens[0], gens[2], gens[3])
        for name in ("current_snapshot.parquet", "listing_history.parquet")
    }
    assert deleted == expected


@patch("pipeline.garage_publisher.boto3.client")
def test_prune_without_manifest_deletes_nothing(mock_client, monkeypatch):
    publisher, s3 = _publisher(mock_client, monkeypatch)
    s3.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey"}}, "GetObject"
    )

    assert publisher.prune_generations(keep=1) == 0
    s3.get_paginator.assert_not_called()
    s3.delete_objects.assert_not_called()


@patch("pipeline.garage_publisher.boto3.client")
def test_prune_nothing_to_delete_makes_no_delete_call(mock_client, monkeypatch):
    publisher, s3 = _publisher(mock_client, monkeypatch)
    keys = [
        "generations/20261101T000000Z/a.json",
        "generations/20261101T010000Z/a.json",
    ]
    _stage_bucket(s3, _manifest_for("20261101T010000Z"), keys)

    assert publisher.prune_generations(keep=5) == 0
    s3.delete_objects.assert_not_called()


@patch("pipeline.garage_publisher.boto3.client")
def test_prune_batches_deletes_and_reports_partial_failure(mock_client, monkeypatch):
    publisher, s3 = _publisher(mock_client, monkeypatch)
    # 2500 old single-file generations + the live one -> three delete batches.
    gens = [
        f"2026{1 + i // 1000:02d}{1 + i % 28:02d}T{i % 24:02d}{i % 60:02d}00Z"
        for i in range(2500)
    ]
    gens = sorted(set(gens))
    live = "20261231T235959Z"
    keys = [f"generations/{g}/a.json" for g in gens] + [f"generations/{live}/a.json"]
    _stage_bucket(s3, _manifest_for(live), keys)
    s3.delete_objects.side_effect = [
        {},
        {"Errors": [{"Key": "generations/x/a.json", "Code": "InternalError"}]},
        {},
    ]

    with pytest.raises(GaragePublisherError, match="Could not delete 1 of"):
        publisher.prune_generations(keep=1)

    assert s3.delete_objects.call_count == 3
    sizes = [
        len(c.kwargs["Delete"]["Objects"]) for c in s3.delete_objects.call_args_list
    ]
    assert max(sizes) <= 1000
    assert f"generations/{live}/a.json" not in _deleted_keys(s3)


@patch("pipeline.garage_publisher.boto3.client")
def test_prune_rejects_zero_retention(mock_client, monkeypatch):
    publisher, s3 = _publisher(mock_client, monkeypatch)
    with pytest.raises(GaragePublisherError):
        publisher.prune_generations(keep=0)
    s3.delete_objects.assert_not_called()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 24), ("", 24), ("6", 6), ("0", 24), ("-3", 24), ("lots", 24)],
)
def test_generation_retention_from_env(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("GARAGE_GENERATION_RETENTION", raising=False)
    else:
        monkeypatch.setenv("GARAGE_GENERATION_RETENTION", raw)
    assert generation_retention_from_env() == expected
