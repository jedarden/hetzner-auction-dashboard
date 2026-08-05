"""
Unit tests for R2 Publisher

Tests the temp-key-then-swap lifecycle for publishing both Parquet and JSON artifacts.
"""

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch
import hashlib

import pytest
import pyarrow.parquet as pq

from pipeline.r2_publisher import (
    R2Publisher,
    R2PublisherError,
    VerificationError,
    PublishError,
    publish_artifacts,
)
from pipeline.cpu_matcher import BenchmarkMatch
from pipeline.enricher import EnrichedListing
from pipeline.fetcher import DiskSpec
from pipeline.parquet_writer import ParquetWriter
from pipeline.unmatched_reporter import UnmatchedCpuReporter


class TestR2PublisherInitialization:
    """Test R2 publisher initialization."""

    def test_init_with_required_params(self):
        """Publisher should initialize with required parameters."""
        publisher = R2Publisher(
            account_id="test-account",
            access_key_id="test-key-id",
            secret_access_key="test-secret",
            bucket_name="test-bucket",
        )

        assert publisher.account_id == "test-account"
        assert publisher.bucket_name == "test-bucket"
        assert publisher.s3_client is not None

    def test_init_with_custom_endpoint(self):
        """Publisher should support custom endpoint URL."""
        publisher = R2Publisher(
            account_id="test-account",
            access_key_id="test-key-id",
            secret_access_key="test-secret",
            bucket_name="test-bucket",
            endpoint_url="https://custom.endpoint.com",
        )

        assert publisher.s3_client is not None


class TestTempKeyGeneration:
    """Test temp key generation."""

    def test_generate_temp_key_for_parquet(self):
        """Should generate temp key for Parquet live key."""
        publisher = R2Publisher(
            account_id="test-account",
            access_key_id="test-key-id",
            secret_access_key="test-secret",
            bucket_name="test-bucket",
        )

        temp_key = publisher._generate_temp_key("current_snapshot.parquet")
        assert temp_key == ".tmp/current_snapshot.parquet.tmp"

    def test_generate_temp_key_for_json(self):
        """Should generate temp key for JSON live key."""
        publisher = R2Publisher(
            account_id="test-account",
            access_key_id="test-key-id",
            secret_access_key="test-secret",
            bucket_name="test-bucket",
        )

        temp_key = publisher._generate_temp_key("unmatched-cpus.json")
        assert temp_key == ".tmp/unmatched-cpus.json.tmp"


class TestFileHashCalculation:
    """Test file hash calculation."""

    def test_calculate_file_hash(self):
        """Should calculate SHA-256 hash of local file."""
        publisher = R2Publisher(
            account_id="test-account",
            access_key_id="test-key-id",
            secret_access_key="test-secret",
            bucket_name="test-bucket",
        )

        # Create temp file with known content
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("test content")
            temp_path = Path(f.name)

        try:
            hash_result = publisher._calculate_file_hash(temp_path)

            # Calculate expected hash
            sha256 = hashlib.sha256()
            with open(temp_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    sha256.update(chunk)
            expected_hash = sha256.hexdigest()

            assert hash_result == expected_hash
        finally:
            temp_path.unlink()


class TestParquetPublishing:
    """Test Parquet artifact publishing."""

    def test_publish_parquet_snapshot_success(self):
        """Should successfully publish Parquet snapshot."""
        # Create sample Parquet file
        listing = self._make_sample_listing()
        writer = ParquetWriter()

        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
            parquet_path = Path(tmp.name)

        try:
            writer.write_listings([listing], parquet_path)

            # Mock S3 client
            mock_s3 = MagicMock()
            mock_s3.head_object.return_value = {
                "ContentLength": parquet_path.stat().st_size,
                "ETag": '"test-etag"',
                "CacheControl": "max-age=60",
            }

            # Mock download for verification
            import io

            with open(parquet_path, "rb") as f:
                mock_s3.download_fileobj.return_value = io.BytesIO(f.read())

            publisher = R2Publisher(
                account_id="test-account",
                access_key_id="test-key-id",
                secret_access_key="test-secret",
                bucket_name="test-bucket",
            )
            publisher.s3_client = mock_s3

            # Publish
            result = publisher.publish_parquet_snapshot(parquet_path)

            assert result["live_key"] == "current_snapshot.parquet"
            assert result["size"] > 0
            assert result["hash"] is not None

            # Verify upload was called
            mock_s3.upload_file.assert_called_once()

            # Verify copy was called for promotion
            mock_s3.copy_object.assert_called_once()

            # Verify temp key was deleted
            mock_s3.delete_object.assert_called()

        finally:
            parquet_path.unlink()

    def test_publish_parquet_nonexistent_file_raises_error(self):
        """Should raise error for nonexistent Parquet file."""
        publisher = R2Publisher(
            account_id="test-account",
            access_key_id="test-key-id",
            secret_access_key="test-secret",
            bucket_name="test-bucket",
        )

        with pytest.raises(R2PublisherError, match="Local Parquet file not found"):
            publisher.publish_parquet_snapshot("/nonexistent/file.parquet")

    def test_publish_parquet_verification_failure_cleans_up(self):
        """Should clean up temp key on verification failure."""
        # Create sample Parquet file
        listing = self._make_sample_listing()
        writer = ParquetWriter()

        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
            parquet_path = Path(tmp.name)

        try:
            writer.write_listings([listing], parquet_path)

            # Mock S3 client that fails during verification
            mock_s3 = MagicMock()
            mock_s3.head_object.side_effect = Exception("Verification failed")

            publisher = R2Publisher(
                account_id="test-account",
                access_key_id="test-key-id",
                secret_access_key="test-secret",
                bucket_name="test-bucket",
            )
            publisher.s3_client = mock_s3

            # Publish should raise error
            with pytest.raises(R2PublisherError):
                publisher.publish_parquet_snapshot(parquet_path)

            # Upload was attempted
            mock_s3.upload_file.assert_called_once()

            # Temp key cleanup was attempted
            mock_s3.delete_object.assert_called_once()

        finally:
            parquet_path.unlink()

    def test_publish_parquet_size_mismatch_raises_error(self):
        """Should raise error when uploaded file size doesn't match."""
        # Create sample Parquet file
        listing = self._make_sample_listing()
        writer = ParquetWriter()

        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
            parquet_path = Path(tmp.name)

        try:
            writer.write_listings([listing], parquet_path)

            # Mock S3 client with wrong size
            mock_s3 = MagicMock()
            mock_s3.head_object.return_value = {
                "ContentLength": 123,  # Wrong size
                "ETag": '"test-etag"',
                "CacheControl": "max-age=60",
            }

            publisher = R2Publisher(
                account_id="test-account",
                access_key_id="test-key-id",
                secret_access_key="test-secret",
                bucket_name="test-bucket",
            )
            publisher.s3_client = mock_s3

            # Publish should raise verification error
            with pytest.raises(VerificationError, match="Size mismatch"):
                publisher.publish_parquet_snapshot(parquet_path)

        finally:
            parquet_path.unlink()

    def test_cache_control_header_set_correctly(self):
        """Should set Cache-Control: max-age=60 header per ADR-4."""
        # Create sample Parquet file
        listing = self._make_sample_listing()
        writer = ParquetWriter()

        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
            parquet_path = Path(tmp.name)

        try:
            writer.write_listings([listing], parquet_path)

            # Mock S3 client
            mock_s3 = MagicMock()
            mock_s3.head_object.return_value = {
                "ContentLength": parquet_path.stat().st_size,
                "ETag": '"test-etag"',
                "CacheControl": "max-age=60",  # Correct header
            }

            # Mock download for verification
            import io

            with open(parquet_path, "rb") as f:
                mock_s3.download_fileobj.return_value = io.BytesIO(f.read())

            publisher = R2Publisher(
                account_id="test-account",
                access_key_id="test-key-id",
                secret_access_key="test-secret",
                bucket_name="test-bucket",
            )
            publisher.s3_client = mock_s3

            # Publish
            result = publisher.publish_parquet_snapshot(parquet_path)

            # Verify Cache-Control header was passed in upload
            upload_call = mock_s3.upload_file.call_args
            assert upload_call is not None
            extra_args = upload_call[1]["ExtraArgs"]
            assert extra_args["CacheControl"] == "max-age=60"

        finally:
            parquet_path.unlink()


class TestJSONPublishing:
    """Test JSON artifact publishing."""

    def test_publish_json_report_success(self):
        """Should successfully publish JSON report."""
        # Create sample JSON file
        reporter = UnmatchedCpuReporter()
        cpu_match = BenchmarkMatch(
            cpu_raw="Unknown CPU",
            matched=False,
            cpu_normalized=None,
            passmark_id=None,
            single_thread_score=None,
            multi_thread_score=None,
            match_method=None,
        )
        reporter.process_listing("test-listing-1", cpu_match)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            json_path = Path(tmp.name)

        try:
            reporter.generate_report(json_path)

            # Mock S3 client
            mock_s3 = MagicMock()
            mock_s3.head_object.return_value = {
                "ContentLength": json_path.stat().st_size,
                "ETag": '"test-etag"',
                "CacheControl": "max-age=60",
            }

            # Mock download for verification
            import io

            with open(json_path, "rb") as f:
                mock_s3.download_fileobj.return_value = io.BytesIO(f.read())

            publisher = R2Publisher(
                account_id="test-account",
                access_key_id="test-key-id",
                secret_access_key="test-secret",
                bucket_name="test-bucket",
            )
            publisher.s3_client = mock_s3

            # Publish
            result = publisher.publish_json_report(json_path)

            assert result["live_key"] == "unmatched-cpus.json"
            assert result["size"] > 0
            assert result["hash"] is not None

            # Verify upload was called
            mock_s3.upload_file.assert_called_once()

            # Verify copy was called for promotion
            mock_s3.copy_object.assert_called_once()

            # Verify temp key was deleted
            mock_s3.delete_object.assert_called_once()

        finally:
            json_path.unlink()

    def test_publish_json_invalid_content_raises_error(self):
        """Should raise error when uploaded JSON is invalid."""
        # Create invalid JSON file
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as tmp:
            tmp.write("{invalid json content")
            json_path = Path(tmp.name)

        try:
            # Mock S3 client
            mock_s3 = MagicMock()
            mock_s3.head_object.return_value = {
                "ContentLength": json_path.stat().st_size,
                "ETag": '"test-etag"',
                "CacheControl": "max-age=60",
            }

            # Mock download with invalid content
            import io

            with open(json_path, "rb") as f:
                mock_s3.download_fileobj.return_value = io.BytesIO(f.read())

            publisher = R2Publisher(
                account_id="test-account",
                access_key_id="test-key-id",
                secret_access_key="test-secret",
                bucket_name="test-bucket",
            )
            publisher.s3_client = mock_s3

            # Publish should raise verification error
            with pytest.raises(VerificationError, match="Invalid JSON"):
                publisher.publish_json_report(json_path)

        finally:
            json_path.unlink()

    def test_publish_json_empty_file_raises_error(self):
        """Should raise error when JSON file is empty."""
        # Create empty JSON file
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            json_path = Path(tmp.name)

        try:
            # Mock S3 client with empty size
            mock_s3 = MagicMock()
            mock_s3.head_object.return_value = {
                "ContentLength": 0,
                "ETag": '"test-etag"',
                "CacheControl": "max-age=60",
            }

            publisher = R2Publisher(
                account_id="test-account",
                access_key_id="test-key-id",
                secret_access_key="test-secret",
                bucket_name="test-bucket",
            )
            publisher.s3_client = mock_s3

            # Publish should raise verification error
            with pytest.raises(VerificationError, match="JSON file is empty"):
                publisher.publish_json_report(json_path)

        finally:
            json_path.unlink()


class TestAtomicPromotion:
    """Test atomic promotion (copy-then-delete-old)."""

    def test_atomic_promote_copies_then_deletes(self):
        """Should copy temp to live key, then delete temp key."""
        mock_s3 = MagicMock()

        publisher = R2Publisher(
            account_id="test-account",
            access_key_id="test-key-id",
            secret_access_key="test-secret",
            bucket_name="test-bucket",
        )
        publisher.s3_client = mock_s3

        # Perform atomic promotion
        publisher._atomic_promote(".tmp/test.parquet.tmp", "test.parquet")

        # Verify copy was called
        mock_s3.copy_object.assert_called_once()
        copy_args = mock_s3.copy_object.call_args
        assert copy_args[1]["Key"] == "test.parquet"
        assert copy_args[1]["CopySource"]["Key"] == ".tmp/test.parquet.tmp"

        # Verify head was called to confirm live key exists
        mock_s3.head_object.assert_called_with(Bucket="test-bucket", Key="test.parquet")

        # Verify temp key was deleted
        mock_s3.delete_object.assert_called_once_with(
            Bucket="test-bucket", Key=".tmp/test.parquet.tmp"
        )

    def test_atomic_promote_failure_raises_error(self):
        """Should raise error when copy operation fails."""
        mock_s3 = MagicMock()
        mock_s3.copy_object.side_effect = Exception("Copy failed")

        publisher = R2Publisher(
            account_id="test-account",
            access_key_id="test-key-id",
            secret_access_key="test-secret",
            bucket_name="test-bucket",
        )
        publisher.s3_client = mock_s3

        # Should raise PublishError
        with pytest.raises(PublishError, match="Failed to promote"):
            publisher._atomic_promote(".tmp/test.parquet.tmp", "test.parquet")


class TestObjectHashRetrieval:
    """Test object hash retrieval."""

    def test_get_object_hash_existing(self):
        """Should return ETag for existing object."""
        mock_s3 = MagicMock()
        mock_s3.head_object.return_value = {"ETag": '"test-etag-123"'}

        publisher = R2Publisher(
            account_id="test-account",
            access_key_id="test-key-id",
            secret_access_key="test-secret",
            bucket_name="test-bucket",
        )
        publisher.s3_client = mock_s3

        hash_result = publisher._get_object_hash("test.parquet")
        assert hash_result == "test-etag-123"

    def test_get_object_hash_nonexistent(self):
        """Should return None for nonexistent object."""
        from botocore.exceptions import ClientError

        mock_s3 = MagicMock()
        error_response = {"Error": {"Code": "404", "Message": "Not Found"}}
        mock_s3.head_object.side_effect = ClientError(error_response, "HeadObject")

        publisher = R2Publisher(
            account_id="test-account",
            access_key_id="test-key-id",
            secret_access_key="test-secret",
            bucket_name="test-bucket",
        )
        publisher.s3_client = mock_s3

        hash_result = publisher._get_object_hash("test.parquet")
        assert hash_result is None


class TestConvenienceFunction:
    """Test the convenience publish_artifacts function."""

    def test_publish_artifacts_publishes_both(self):
        """Should publish both Parquet and JSON artifacts."""
        # Create sample files
        listing = self._make_sample_listing()
        writer = ParquetWriter()
        reporter = UnmatchedCpuReporter()

        cpu_match = BenchmarkMatch(
            cpu_raw="Unknown CPU",
            matched=False,
            cpu_normalized=None,
            passmark_id=None,
            single_thread_score=None,
            multi_thread_score=None,
            match_method=None,
        )
        reporter.process_listing("test-listing-1", cpu_match)

        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
            parquet_path = Path(tmp.name)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            json_path = Path(tmp.name)

        try:
            writer.write_listings([listing], parquet_path)
            reporter.generate_report(json_path)

            # Mock S3 client
            mock_s3 = MagicMock()
            mock_s3.head_object.return_value = {
                "ContentLength": 1000,
                "ETag": '"test-etag"',
                "CacheControl": "max-age=60",
            }

            # Mock downloads for verification
            import io

            with open(parquet_path, "rb") as f:
                parquet_content = f.read()
            with open(json_path, "rb") as f:
                json_content = f.read()

            download_side_effect = [
                io.BytesIO(parquet_content),
                io.BytesIO(json_content),
            ]
            mock_s3.download_fileobj.side_effect = download_side_effect

            with patch("boto3.client", return_value=mock_s3):
                r2_config = {
                    "account_id": "test-account",
                    "access_key_id": "test-key-id",
                    "secret_access_key": "test-secret",
                    "bucket_name": "test-bucket",
                }

                results = publish_artifacts(parquet_path, json_path, r2_config)

                assert "parquet" in results
                assert "json" in results
                assert results["parquet"]["live_key"] == "current_snapshot.parquet"
                assert results["json"]["live_key"] == "unmatched-cpus.json"

        finally:
            parquet_path.unlink()
            json_path.unlink()


# Test failure scenario: mid-publish failure leaves live keys unchanged
class TestFailureScenarios:
    """Test failure scenarios and cleanup."""

    def test_mid_publish_failure_leaves_live_key_unchanged(self):
        """Forced mid-run failure should leave both live R2 keys' hashes unchanged."""
        # Create sample files
        listing = self._make_sample_listing()
        writer = ParquetWriter()

        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
            parquet_path = Path(tmp.name)

        try:
            writer.write_listings([listing], parquet_path)

            # Mock S3 client that fails during verification
            mock_s3 = MagicMock()
            mock_s3.head_object.side_effect = Exception("Simulated mid-run failure")

            publisher = R2Publisher(
                account_id="test-account",
                access_key_id="test-key-id",
                secret_access_key="test-secret",
                bucket_name="test-bucket",
            )
            publisher.s3_client = mock_s3

            # Get current live key hash before attempted publish
            initial_hash = publisher._get_object_hash("current_snapshot.parquet")

            # Attempt publish should fail
            with pytest.raises(R2PublisherError):
                publisher.publish_parquet_snapshot(parquet_path)

            # Verify live key hash is unchanged
            final_hash = publisher._get_object_hash("current_snapshot.parquet")
            assert initial_hash == final_hash, "Live key hash should remain unchanged after failure"

        finally:
            parquet_path.unlink()

    def test_upload_failure_does_not_touch_live_key(self):
        """Upload failure should not touch live key."""
        # Create sample file
        listing = self._make_sample_listing()
        writer = ParquetWriter()

        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
            parquet_path = Path(tmp.name)

        try:
            writer.write_listings([listing], parquet_path)

            # Mock S3 client that fails during upload
            mock_s3 = MagicMock()
            mock_s3.upload_file.side_effect = Exception("Upload failed")

            publisher = R2Publisher(
                account_id="test-account",
                access_key_id="test-key-id",
                secret_access_key="test-secret",
                bucket_name="test-bucket",
            )
            publisher.s3_client = mock_s3

            # Attempt publish should fail
            with pytest.raises(R2PublisherError):
                publisher.publish_parquet_snapshot(parquet_path)

            # Verify copy was never called (live key untouched)
            mock_s3.copy_object.assert_not_called()

        finally:
            parquet_path.unlink()


# Helper methods

def _make_sample_listing(
    listing_id="test-listing-1",
    datacenter="FSN1-DC3",
    location="FSN",
    available_from="2026-08-02T12:00:00Z",
    cpu_raw="Intel Xeon E5-2680 v4",
    cpu_normalized="Intel Xeon E5-2680 v4",
    benchmark_matched=True,
    passmark_id=1234,
    single_thread_score=1500,
    multi_thread_score=8000,
    benchmark_match_method="direct",
    ram_gb=64,
    ram_ecc=True,
    uplink_speed=1000,
    price_base=2999,
    price_setup_fee=0,
    disks=None,
    fetched_at=None,
) -> EnrichedListing:
    """Helper to create a sample EnrichedListing for testing."""
    if disks is None:
        disks = [DiskSpec(type="NVMe", count=2, capacity_gb=480)]

    if fetched_at is None:
        fetched_at = datetime.now(UTC)

    # Create listing with derived metrics
    from pipeline.enricher import CostMetricsEnricher
    from pipeline.fetcher import RawListing

    raw_listing = RawListing(
        listing_id=listing_id,
        datacenter=datacenter,
        location=location,
        available_from=available_from,
        cpu_raw=cpu_raw,
        ram_gb=ram_gb,
        ram_ecc=ram_ecc,
        disks=disks,
        uplink_speed=uplink_speed,
        price_base=price_base,
        price_setup_fee=price_setup_fee,
        fetched_at=fetched_at,
    )

    cpu_match = BenchmarkMatch(
        cpu_raw=cpu_raw,
        matched=benchmark_matched,
        cpu_normalized=cpu_normalized,
        passmark_id=passmark_id,
        single_thread_score=single_thread_score,
        multi_thread_score=multi_thread_score,
        match_method=benchmark_match_method,
    )

    enricher = CostMetricsEnricher()
    return enricher.enrich_listing(raw_listing, cpu_match)
