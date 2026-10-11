"""Atomic, manifest-versioned publication of dashboard artifacts to Garage."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

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
    # Alert dedup state (main.py's ALERT_STATE_KEY). Without this, publish()
    # never uploads it or registers it in manifest.json, so active_file_url()
    # always returns None and fetch_alert_state() always falls back to the
    # Cloudflare Pages origin -- which never actually receives this file and
    # 200s with the SPA's index.html, read by fetch_alert_state() as "nothing
    # published yet". previously_alerted is then unconditionally empty on
    # every cycle, so a listing that stays under threshold across two cycles
    # gets alerted twice instead of once. Confirmed live 2026-08-27: listing
    # 3064574 alerted at 11:22:13 and again at 11:23:16.
    "alerted-listings.json",
)
MANIFEST_SCHEMA_VERSION: Final = 1
MANIFEST_KEYS: Final = frozenset(
    {"schema_version", "generation", "published_at", "dataset_hash", "files"}
)
_GENERATION_RE: Final = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")


class GaragePublisherError(RuntimeError):
    pass


def validate_manifest(manifest: Any) -> dict[str, Any]:
    """Validate and return a manifest that conforms to the v1 data contract.

    The manifest is a public API consumed by the browser.  Keep this check
    strict so a malformed pointer cannot make an otherwise healthy generation
    appear live, and so a future schema can be introduced explicitly by
    incrementing ``schema_version``.
    """
    if not isinstance(manifest, dict):
        raise GaragePublisherError("Manifest must be a JSON object")
    if set(manifest) != MANIFEST_KEYS:
        missing = sorted(MANIFEST_KEYS - set(manifest))
        extra = sorted(set(manifest) - MANIFEST_KEYS)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise GaragePublisherError(f"Invalid manifest contract: {'; '.join(details)}")

    version = manifest["schema_version"]
    if isinstance(version, bool) or version != MANIFEST_SCHEMA_VERSION:
        raise GaragePublisherError(f"Unsupported manifest schema version: {version!r}")

    generation = manifest["generation"]
    if not isinstance(generation, str) or not _GENERATION_RE.fullmatch(generation):
        raise GaragePublisherError(
            "Manifest generation must be a UTC timestamp identifier"
        )

    published_at = manifest["published_at"]
    if not isinstance(published_at, str):
        raise GaragePublisherError("Manifest published_at must be an ISO-8601 string")
    try:
        published_datetime = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GaragePublisherError(
            "Manifest published_at is not valid ISO-8601"
        ) from exc
    if (
        published_datetime.tzinfo is None
        or published_datetime.utcoffset() != UTC.utcoffset(published_datetime)
    ):
        raise GaragePublisherError("Manifest published_at must include a UTC offset")

    digest = manifest["dataset_hash"]
    if not isinstance(digest, str) or not digest:
        raise GaragePublisherError("Manifest dataset_hash must be a non-empty string")

    files = manifest["files"]
    if not isinstance(files, dict) or set(files) != set(ARTIFACTS):
        raise GaragePublisherError(
            "Manifest files must list every published artifact exactly once"
        )
    expected_prefix = f"generations/{generation}/"
    for filename in ARTIFACTS:
        relative_key = files[filename]
        if (
            not isinstance(relative_key, str)
            or not relative_key.startswith(expected_prefix)
            or relative_key != f"{expected_prefix}{filename}"
        ):
            raise GaragePublisherError(f"Manifest file path is invalid for {filename}")

    return manifest


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
    payload = json.dumps(
        sorted(rows, key=lambda row: row["listing_id"]),
        sort_keys=True,
        separators=(",", ":"),
    )
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
            raise GaragePublisherError(
                f"Missing Garage configuration: {', '.join(missing)}"
            )
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
            response = self.s3.get_object(
                Bucket=self.bucket, Key=self._storage_key("manifest.json")
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"NoSuchKey", "NoSuchBucket", "404"}:
                return None
            raise GaragePublisherError(
                f"Could not read active manifest: {code}"
            ) from exc
        try:
            manifest = json.loads(response["Body"].read())
        except (TypeError, ValueError, KeyError) as exc:
            raise GaragePublisherError("Active manifest is invalid JSON") from exc
        return validate_manifest(manifest)

    def is_changed(self, digest: str) -> bool:
        manifest = self.get_manifest()
        return not manifest or manifest.get("dataset_hash") != digest

    def active_file_url(self, filename: str) -> str | None:
        if filename not in ARTIFACTS:
            raise GaragePublisherError(f"Unknown published artifact: {filename}")
        manifest = self.get_manifest()
        if not manifest:
            return None
        relative = manifest.get("files", {}).get(filename)
        return f"{self.public_base_url}/{relative}" if relative else None

    def publish(
        self, directory: str | Path, digest: str, now: datetime | None = None
    ) -> dict[str, Any]:
        directory = Path(directory)
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            raise GaragePublisherError("Generation timestamp must include a timezone")
        now = now.astimezone(UTC)
        generation = now.strftime("%Y%m%dT%H%M%SZ")
        prefix = f"generations/{generation}"

        # Complete local validation is a publication barrier.  No remote
        # object is written until every expected artifact is present and
        # parseable, which keeps failures before the pointer swap invisible.
        for filename in ARTIFACTS:
            self._verify(directory / filename)

        files = {filename: f"{prefix}/{filename}" for filename in ARTIFACTS}
        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "generation": generation,
            "published_at": now.isoformat(),
            "dataset_hash": digest,
            "files": files,
        }
        validate_manifest(manifest)

        # Generation objects are immutable and are all verified before the
        # sole mutable object, manifest.json, is replaced.
        for filename in ARTIFACTS:
            path = directory / filename
            key = self._storage_key(files[filename])
            content_type = (
                "application/json"
                if filename.endswith(".json")
                else "application/vnd.apache.parquet"
            )
            try:
                self.s3.upload_file(
                    str(path),
                    self.bucket,
                    key,
                    ExtraArgs={
                        "ContentType": content_type,
                        "CacheControl": "public, max-age=31536000, immutable",
                    },
                )
                head = self.s3.head_object(Bucket=self.bucket, Key=key)
            except Exception as exc:
                raise GaragePublisherError(
                    f"Could not publish or verify {filename}"
                ) from exc
            if head.get("ContentLength") != path.stat().st_size:
                raise GaragePublisherError(f"Remote verification failed for {filename}")

        body = json.dumps(manifest, sort_keys=True, indent=2).encode()
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=self._storage_key("manifest.json"),
                Body=body,
                ContentType="application/json",
                CacheControl="no-cache, max-age=0, must-revalidate",
            )
        except Exception as exc:
            # S3/ Garage replaces one object atomically.  Since this is the
            # first pointer write in this method, a failed put leaves the
            # prior manifest in place.
            raise GaragePublisherError("Could not replace active manifest") from exc

        logger.info("Published Garage generation %s", generation)
        return manifest

    def publish_if_changed(
        self, directory: str | Path, digest: str, now: datetime | None = None
    ) -> dict[str, Any] | None:
        """Publish only when the material auction dataset differs.

        The refresh loop uses this operation as its publication gate.  A
        no-change cycle performs no local generation uploads and leaves the
        current manifest untouched.
        """
        if not self.is_changed(digest):
            logger.info("Auction dataset unchanged; suppressing publication")
            return None
        return self.publish(directory, digest, now)

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
            raise GaragePublisherError(
                f"Artifact verification failed: {path.name}"
            ) from exc
