"""Atomic, manifest-versioned publication of dashboard artifacts to Garage."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
import pyarrow.parquet as pq
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

ARTIFACTS = (
    "current_snapshot.parquet",
    "config_history.parquet",
    "listing_history.parquet",
    "unmatched-cpus.json",
)


class GaragePublisherError(RuntimeError):
    pass


def dataset_hash(listings: list[Any]) -> str:
    """Hash material auction state, excluding poll timestamps/history annotations."""
    rows = []
    ignored = {
        "fetched_at",
        "price_percentile_vs_history",
        "price_per_benchmark_point_single_percentile_vs_history",
        "price_per_benchmark_point_multi_percentile_vs_history",
        "is_all_time_low",
        "history_sample_size",
        "history_cohort_fallback",
    }
    for listing in listings:
        row = asdict(listing)
        for key in ignored:
            row.pop(key, None)
        rows.append(row)
    payload = json.dumps(sorted(rows, key=lambda row: row["listing_id"]), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class GaragePublisher:
    def __init__(self) -> None:
        required = {
            "GARAGE_S3_ENDPOINT": os.getenv("GARAGE_S3_ENDPOINT"),
            "GARAGE_ACCESS_KEY_ID": os.getenv("GARAGE_ACCESS_KEY_ID"),
            "GARAGE_SECRET_ACCESS_KEY": os.getenv("GARAGE_SECRET_ACCESS_KEY"),
            "GARAGE_BUCKET": os.getenv("GARAGE_BUCKET"),
            "GARAGE_PUBLIC_BASE_URL": os.getenv("GARAGE_PUBLIC_BASE_URL"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise GaragePublisherError(f"Missing Garage configuration: {', '.join(missing)}")
        self.bucket = required["GARAGE_BUCKET"]
        self.public_base_url = required["GARAGE_PUBLIC_BASE_URL"].rstrip("/")
        self.key_prefix = os.getenv("GARAGE_KEY_PREFIX", "").strip("/")
        self.s3 = boto3.client(
            "s3",
            endpoint_url=required["GARAGE_S3_ENDPOINT"],
            aws_access_key_id=required["GARAGE_ACCESS_KEY_ID"],
            aws_secret_access_key=required["GARAGE_SECRET_ACCESS_KEY"],
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            region_name="garage",
        )

    def get_manifest(self) -> dict[str, Any] | None:
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=self._storage_key("manifest.json"))
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"NoSuchKey", "NoSuchBucket", "404"}:
                return None
            raise GaragePublisherError(f"Could not read active manifest: {code}") from exc
        try:
            return json.loads(response["Body"].read())
        except (ValueError, KeyError) as exc:
            raise GaragePublisherError("Active manifest is invalid JSON") from exc

    def is_changed(self, digest: str) -> bool:
        manifest = self.get_manifest()
        return not manifest or manifest.get("dataset_hash") != digest

    def active_file_url(self, filename: str) -> str | None:
        manifest = self.get_manifest()
        if not manifest:
            return None
        relative = manifest.get("files", {}).get(filename)
        return f"{self.public_base_url}/{relative}" if relative else None

    def publish(self, directory: str | Path, digest: str, now: datetime | None = None) -> dict[str, Any]:
        directory = Path(directory)
        now = now or datetime.now(UTC)
        generation = now.strftime("%Y%m%dT%H%M%SZ")
        prefix = f"generations/{generation}"
        files: dict[str, str] = {}

        for filename in ARTIFACTS:
            path = directory / filename
            self._verify(path)
            relative_key = f"{prefix}/{filename}"
            key = self._storage_key(relative_key)
            content_type = "application/json" if filename.endswith(".json") else "application/vnd.apache.parquet"
            self.s3.upload_file(
                str(path), self.bucket, key,
                ExtraArgs={"ContentType": content_type, "CacheControl": "public, max-age=31536000, immutable"},
            )
            head = self.s3.head_object(Bucket=self.bucket, Key=key)
            if head.get("ContentLength") != path.stat().st_size:
                raise GaragePublisherError(f"Remote verification failed for {filename}")
            files[filename] = relative_key

        manifest = {
            "schema_version": 1,
            "generation": generation,
            "published_at": now.isoformat(),
            "dataset_hash": digest,
            "files": files,
        }
        body = json.dumps(manifest, sort_keys=True, indent=2).encode()
        self.s3.put_object(
            Bucket=self.bucket,
            Key=self._storage_key("manifest.json"),
            Body=body,
            ContentType="application/json",
            CacheControl="no-cache, max-age=0, must-revalidate",
        )
        logger.info("Published Garage generation %s", generation)
        return manifest

    def _storage_key(self, relative_key: str) -> str:
        return f"{self.key_prefix}/{relative_key}" if self.key_prefix else relative_key

    @staticmethod
    def _verify(path: Path) -> None:
        if not path.is_file() or path.stat().st_size == 0:
            raise GaragePublisherError(f"Artifact missing or empty: {path.name}")
        try:
            if path.suffix == ".parquet":
                pq.read_metadata(path)
            else:
                json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise GaragePublisherError(f"Artifact verification failed: {path.name}") from exc
