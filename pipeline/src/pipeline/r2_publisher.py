"""
R2 Publisher for Hetzner Auction Dashboard Artifacts

Implements the temp-key-then-swap lifecycle for publishing both:
- Parquet snapshot files (current auction listings)
- unmatched-cpus.json reports

This module ensures atomic updates to R2 storage following the verify-before-publish
discipline from the pipeline plan: write to temp key, verify, then atomic swap.

Architecture Reference: docs/plan/plan.md "Pipeline Run Lifecycle"
"""

import hashlib
import json
import logging
from pathlib import Path
from typing import Literal

import boto3
from botocore.exceptions import ClientError
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

# Cache-Control header per ADR-4: max-age=60 (well under 10-minute publish cadence)
CACHE_CONTROL_HEADER = "max-age=60"


class R2PublisherError(Exception):
    """Base exception for R2 publisher errors."""
    pass


class VerificationError(R2PublisherError):
    """Raised when artifact verification fails."""
    pass


class PublishError(R2PublisherError):
    """Raised when publish operation fails."""
    pass


class R2Publisher:
    """
    Publishes artifacts to Cloudflare R2 with temp-key-then-swap lifecycle.

    This publisher ensures atomic updates to R2 storage by:
    1. Writing to a temporary key
    2. Verifying the artifact is valid (Parquet or JSON)
    3. Atomically promoting to live key (copy-then-delete-old)
    4. Setting Cache-Control: max-age=60 header per ADR-4

    Any failure before step 3 leaves the live key untouched.
    """

    def __init__(
        self,
        account_id: str,
        access_key_id: str,
        secret_access_key: str,
        bucket_name: str,
        endpoint_url: str = "https://r2.cloudflarestorage.com",
    ):
        """
        Initialize the R2 publisher.

        Args:
            account_id: Cloudflare account ID
            access_key_id: R2 token access key ID
            secret_access_key: R2 token secret access key
            bucket_name: R2 bucket name
            endpoint_url: R2 API endpoint URL
        """
        self.account_id = account_id
        self.bucket_name = bucket_name

        # Initialize S3 client for R2
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",  # R2 doesn't use regions
        )

        logger.info(f"Initialized R2 publisher for bucket: {bucket_name}")

    def publish_parquet_snapshot(
        self,
        local_parquet_path: str | Path,
        live_key: str = "current_snapshot.parquet",
    ) -> dict:
        """
        Publish Parquet snapshot to R2 with temp-key-then-swap lifecycle.

        Args:
            local_parquet_path: Path to local Parquet file to publish
            live_key: The well-known live key (default: "current_snapshot.parquet")

        Returns:
            dict with publish metadata including etag and size

        Raises:
            VerificationError: If Parquet verification fails
            PublishError: If publish operation fails
            R2PublisherError: For other errors
        """
        local_parquet_path = Path(local_parquet_path)

        if not local_parquet_path.exists():
            raise R2PublisherError(f"Local Parquet file not found: {local_parquet_path}")

        logger.info(f"Publishing Parquet snapshot: {local_parquet_path} -> {live_key}")

        # Calculate local file hash for verification
        local_hash = self._calculate_file_hash(local_parquet_path)

        # Generate temp key
        temp_key = self._generate_temp_key(live_key)

        try:
            # Step 1: Write to temp key
            self._upload_file(
                local_parquet_path,
                temp_key,
                content_type="application/octet-stream",
            )
            logger.info(f"Uploaded to temp key: {temp_key}")

            # Step 2: Verify temp artifact
            self._verify_parquet_artifact(temp_key, expected_size=local_parquet_path.stat().st_size)
            logger.info("Parquet artifact verified")

            # Step 3: Get current live key hash before swap
            current_live_hash = self._get_object_hash(live_key)
            logger.info(f"Current live key hash: {current_live_hash}")

            # Step 4: Atomic swap (copy-then-delete-old)
            self._atomic_promote(temp_key, live_key)
            logger.info(f"Atomically promoted to live key: {live_key}")

            # Verify new live key has the expected hash
            new_live_hash = self._get_object_hash(live_key)
            if new_live_hash != local_hash:
                raise PublishError(
                    f"Live key hash mismatch after promotion: "
                    f"expected {local_hash}, got {new_live_hash}"
                )

            return {
                "live_key": live_key,
                "size": local_parquet_path.stat().st_size,
                "hash": local_hash,
                "previous_hash": current_live_hash,
            }

        except (ClientError, VerificationError, PublishError) as e:
            # On any error, abort without touching live key
            logger.error(f"Publish failed: {e}")

            # Clean up temp key if it exists
            try:
                self.s3_client.head_object(Bucket=self.bucket_name, Key=temp_key)
                self.s3_client.delete_object(Bucket=self.bucket_name, Key=temp_key)
                logger.info(f"Cleaned up temp key: {temp_key}")
            except ClientError:
                pass  # Temp key doesn't exist, no cleanup needed

            raise

    def publish_json_report(
        self,
        local_json_path: str | Path,
        live_key: str = "unmatched-cpus.json",
    ) -> dict:
        """
        Publish JSON report to R2 with temp-key-then-swap lifecycle.

        Args:
            local_json_path: Path to local JSON file to publish
            live_key: The well-known live key (default: "unmatched-cpus.json")

        Returns:
            dict with publish metadata including etag and size

        Raises:
            VerificationError: If JSON verification fails
            PublishError: If publish operation fails
            R2PublisherError: For other errors
        """
        local_json_path = Path(local_json_path)

        if not local_json_path.exists():
            raise R2PublisherError(f"Local JSON file not found: {local_json_path}")

        logger.info(f"Publishing JSON report: {local_json_path} -> {live_key}")

        # Calculate local file hash for verification
        local_hash = self._calculate_file_hash(local_json_path)

        # Generate temp key
        temp_key = self._generate_temp_key(live_key)

        try:
            # Step 1: Write to temp key
            self._upload_file(
                local_json_path,
                temp_key,
                content_type="application/json",
            )
            logger.info(f"Uploaded to temp key: {temp_key}")

            # Step 2: Verify temp artifact
            self._verify_json_artifact(temp_key, expected_size=local_json_path.stat().st_size)
            logger.info("JSON artifact verified")

            # Step 3: Get current live key hash before swap
            current_live_hash = self._get_object_hash(live_key)
            logger.info(f"Current live key hash: {current_live_hash}")

            # Step 4: Atomic swap (copy-then-delete-old)
            self._atomic_promote(temp_key, live_key)
            logger.info(f"Atomically promoted to live key: {live_key}")

            # Verify new live key has the expected hash
            new_live_hash = self._get_object_hash(live_key)
            if new_live_hash != local_hash:
                raise PublishError(
                    f"Live key hash mismatch after promotion: "
                    f"expected {local_hash}, got {new_live_hash}"
                )

            return {
                "live_key": live_key,
                "size": local_json_path.stat().st_size,
                "hash": local_hash,
                "previous_hash": current_live_hash,
            }

        except (ClientError, VerificationError, PublishError) as e:
            # On any error, abort without touching live key
            logger.error(f"Publish failed: {e}")

            # Clean up temp key if it exists
            try:
                self.s3_client.head_object(Bucket=self.bucket_name, Key=temp_key)
                self.s3_client.delete_object(Bucket=self.bucket_name, Key=temp_key)
                logger.info(f"Cleaned up temp key: {temp_key}")
            except ClientError:
                pass  # Temp key doesn't exist, no cleanup needed

            raise

    def _generate_temp_key(self, live_key: str) -> str:
        """Generate a temporary key name for the given live key."""
        return f".tmp/{live_key}.tmp"

    def _upload_file(
        self,
        local_path: Path,
        key: str,
        content_type: str,
    ) -> None:
        """
        Upload a file to R2 with proper headers.

        Args:
            local_path: Local file path
            key: R2 object key
            content_type: Content-Type header value
        """
        extra_args = {
            "ContentType": content_type,
            "CacheControl": CACHE_CONTROL_HEADER,
        }

        self.s3_client.upload_file(
            str(local_path),
            self.bucket_name,
            key,
            ExtraArgs=extra_args,
        )

    def _verify_parquet_artifact(self, key: str, expected_size: int) -> None:
        """
        Verify that the R2 object is valid Parquet.

        Args:
            key: R2 object key to verify
            expected_size: Expected file size in bytes

        Raises:
            VerificationError: If verification fails
        """
        try:
            # Check object exists and get metadata
            head = self.s3_client.head_object(Bucket=self.bucket_name, Key=key)
            actual_size = head["ContentLength"]

            if actual_size != expected_size:
                raise VerificationError(
                    f"Size mismatch: expected {expected_size}, got {actual_size}"
                )

            if actual_size == 0:
                raise VerificationError("Parquet file is empty")

            # Verify Cache-Control header is set
            cache_control = head.get("CacheControl", "")
            if cache_control != CACHE_CONTROL_HEADER:
                raise VerificationError(
                    f"Cache-Control header not set correctly: {cache_control}"
                )

            # Download to memory and verify Parquet structure
            import io

            buffer = io.BytesIO()
            self.s3_client.download_fileobj(self.bucket_name, key, buffer)
            buffer.seek(0)

            # Try to read as Parquet - will raise if invalid
            try:
                table = pq.read_table(buffer)
                logger.info(f"Parquet verification successful: {len(table)} rows")
            except Exception as e:
                raise VerificationError(f"Invalid Parquet file: {e}")

        except ClientError as e:
            raise VerificationError(f"Failed to verify Parquet artifact: {e}")

    def _verify_json_artifact(self, key: str, expected_size: int) -> None:
        """
        Verify that the R2 object is valid JSON.

        Args:
            key: R2 object key to verify
            expected_size: Expected file size in bytes

        Raises:
            VerificationError: If verification fails
        """
        try:
            # Check object exists and get metadata
            head = self.s3_client.head_object(Bucket=self.bucket_name, Key=key)
            actual_size = head["ContentLength"]

            if actual_size != expected_size:
                raise VerificationError(
                    f"Size mismatch: expected {expected_size}, got {actual_size}"
                )

            if actual_size == 0:
                raise VerificationError("JSON file is empty")

            # Verify Cache-Control header is set
            cache_control = head.get("CacheControl", "")
            if cache_control != CACHE_CONTROL_HEADER:
                raise VerificationError(
                    f"Cache-Control header not set correctly: {cache_control}"
                )

            # Download to memory and verify JSON structure
            import io

            buffer = io.BytesIO()
            self.s3_client.download_fileobj(self.bucket_name, key, buffer)
            buffer.seek(0)

            try:
                json_content = json.loads(buffer.read().decode("utf-8"))
                logger.info(f"JSON verification successful: {json_content.keys()}")
            except json.JSONDecodeError as e:
                raise VerificationError(f"Invalid JSON file: {e}")

        except ClientError as e:
            raise VerificationError(f"Failed to verify JSON artifact: {e}")

    def _get_object_hash(self, key: str) -> str | None:
        """
        Get the current hash of an R2 object.

        Returns ETag if object exists, None if object doesn't exist.

        Args:
            key: R2 object key

        Returns:
            ETag hash string or None
        """
        try:
            head = self.s3_client.head_object(Bucket=self.bucket_name, Key=key)
            return head["ETag"].strip('"')
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return None
            raise

    def _calculate_file_hash(self, file_path: Path) -> str:
        """
        Calculate SHA-256 hash of a local file.

        Args:
            file_path: Local file path

        Returns:
            Hex-encoded SHA-256 hash
        """
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _atomic_promote(self, temp_key: str, live_key: str) -> None:
        """
        Atomically promote temp key to live key using copy-then-delete-old.

        R2/S3 has no native rename, so we copy then delete the old key.
        This is atomic from the client's perspective: either the old key
        or the new key exists, never a partial state.

        Args:
            temp_key: Temporary key path
            live_key: Live/well-known key path

        Raises:
            PublishError: If promotion fails
        """
        try:
            # Copy temp to live key
            copy_source = {"Bucket": self.bucket_name, "Key": temp_key}
            self.s3_client.copy_object(
                CopySource=copy_source,
                Bucket=self.bucket_name,
                Key=live_key,
            )
            logger.info(f"Copied {temp_key} -> {live_key}")

            # Verify live key exists before deleting old
            self.s3_client.head_object(Bucket=self.bucket_name, Key=live_key)

            # Delete temp key
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=temp_key)
            logger.info(f"Deleted temp key: {temp_key}")

        except ClientError as e:
            raise PublishError(f"Failed to promote temp key to live key: {e}")


def publish_artifacts(
    parquet_path: str | Path,
    json_path: str | Path,
    r2_config: dict,
) -> dict[str, dict]:
    """
    Convenience function to publish both artifacts in sequence.

    Args:
        parquet_path: Path to Parquet snapshot file
        json_path: Path to unmatched-cpus.json report
        r2_config: Dictionary with R2 configuration (account_id, access_key_id,
                   secret_access_key, bucket_name, endpoint_url)

    Returns:
        dict with publish results for both artifacts

    Raises:
        R2PublisherError: If either publish fails
    """
    publisher = R2Publisher(
        account_id=r2_config["account_id"],
        access_key_id=r2_config["access_key_id"],
        secret_access_key=r2_config["secret_access_key"],
        bucket_name=r2_config["bucket_name"],
        endpoint_url=r2_config.get("endpoint_url", "https://r2.cloudflarestorage.com"),
    )

    results = {}

    # Publish Parquet snapshot
    results["parquet"] = publisher.publish_parquet_snapshot(parquet_path)

    # Publish JSON report
    results["json"] = publisher.publish_json_report(json_path)

    return results
