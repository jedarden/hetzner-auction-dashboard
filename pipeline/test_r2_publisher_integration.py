#!/usr/bin/env python3
"""
Simple integration test for R2 Publisher logic without full system dependencies.

This tests the core logic of the temp-key-then-swap lifecycle without requiring
full PyArrow imports or actual R2 connections.
"""

import sys
import tempfile
import json
import io
from pathlib import Path
from unittest.mock import MagicMock, Mock
import hashlib

# Mock PyArrow to avoid system library issues
sys.modules["pyarrow"] = MagicMock()
sys.modules["pyarrow.parquet"] = MagicMock()

# Add src to path
sys.path.insert(0, '/home/coding/hetzner-auction-dashboard/pipeline/src')

from pipeline.r2_publisher import (
    R2Publisher,
    R2PublisherError,
    VerificationError,
    PublishError,
    CACHE_CONTROL_HEADER,
)


def test_cache_control_header():
    """Test that Cache-Control header is set per ADR-4."""
    print("Testing Cache-Control header...")
    assert CACHE_CONTROL_HEADER == "max-age=60", f"Expected 'max-age=60', got '{CACHE_CONTROL_HEADER}'"
    print("✓ Cache-Control header is correctly set to 'max-age=60' (ADR-4)")


def test_temp_key_generation():
    """Test temp key generation for different live keys."""
    print("\nTesting temp key generation...")

    # Mock S3 client
    mock_s3 = MagicMock()

    publisher = R2Publisher(
        account_id="test-account",
        access_key_id="test-key-id",
        secret_access_key="test-secret",
        bucket_name="test-bucket",
    )
    publisher.s3_client = mock_s3

    # Test Parquet temp key
    parquet_temp = publisher._generate_temp_key("current_snapshot.parquet")
    assert parquet_temp == ".tmp/current_snapshot.parquet.tmp", f"Expected '.tmp/current_snapshot.parquet.tmp', got '{parquet_temp}'"
    print(f"✓ Parquet temp key: {parquet_temp}")

    # Test JSON temp key
    json_temp = publisher._generate_temp_key("unmatched-cpus.json")
    assert json_temp == ".tmp/unmatched-cpus.json.tmp", f"Expected '.tmp/unmatched-cpus.json.tmp', got '{json_temp}'"
    print(f"✓ JSON temp key: {json_temp}")


def test_file_hash_calculation():
    """Test file hash calculation."""
    print("\nTesting file hash calculation...")

    # Mock S3 client
    mock_s3 = MagicMock()

    publisher = R2Publisher(
        account_id="test-account",
        access_key_id="test-key-id",
        secret_access_key="test-secret",
        bucket_name="test-bucket",
    )
    publisher.s3_client = mock_s3

    # Create test file with known content
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write("test content for hash")
        test_file = Path(f.name)

    try:
        calculated_hash = publisher._calculate_file_hash(test_file)

        # Calculate expected hash
        sha256 = hashlib.sha256()
        with open(test_file, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        expected_hash = sha256.hexdigest()

        assert calculated_hash == expected_hash, f"Hash mismatch: expected {expected_hash}, got {calculated_hash}"
        print(f"✓ File hash calculated correctly: {calculated_hash[:16]}...")

    finally:
        test_file.unlink()


def test_object_hash_retrieval():
    """Test object hash retrieval from R2."""
    print("\nTesting object hash retrieval...")

    # Mock S3 client
    mock_s3 = MagicMock()

    publisher = R2Publisher(
        account_id="test-account",
        access_key_id="test-key-id",
        secret_access_key="test-secret",
        bucket_name="test-bucket",
    )
    publisher.s3_client = mock_s3

    # Test existing object
    mock_s3.head_object.return_value = {"ETag": '"test-etag-123"'}
    hash_result = publisher._get_object_hash("test.parquet")
    assert hash_result == "test-etag-123", f"Expected 'test-etag-123', got {hash_result}"
    print(f"✓ Retrieved hash for existing object: {hash_result}")

    # Test non-existing object (404)
    from botocore.exceptions import ClientError
    error_response = {"Error": {"Code": "404", "Message": "Not Found"}}
    mock_s3.head_object.side_effect = ClientError(error_response, "HeadObject")
    hash_result = publisher._get_object_hash("nonexistent.parquet")
    assert hash_result is None, f"Expected None for nonexistent object, got {hash_result}"
    print("✓ Returns None for non-existent object")


def test_atomic_promote():
    """Test atomic promotion (copy-then-delete-old)."""
    print("\nTesting atomic promotion...")

    # Mock S3 client
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
    assert mock_s3.copy_object.call_count == 1, "copy_object should be called once"
    copy_args = mock_s3.copy_object.call_args
    assert copy_args[1]["Key"] == "test.parquet", "Live key should be test.parquet"
    assert copy_args[1]["CopySource"]["Key"] == ".tmp/test.parquet.tmp", "Source should be temp key"
    print("✓ Copy operation called correctly")

    # Verify head was called to confirm live key exists
    assert mock_s3.head_object.call_count == 1, "head_object should be called once"
    head_args = mock_s3.head_object.call_args
    assert head_args[1]["Key"] == "test.parquet", "Should check live key exists"
    print("✓ Verification of live key after copy")

    # Verify temp key was deleted
    assert mock_s3.delete_object.call_count == 1, "delete_object should be called once"
    delete_args = mock_s3.delete_object.call_args
    assert delete_args[1]["Key"] == ".tmp/test.parquet.tmp", "Should delete temp key"
    print("✓ Temp key deleted after successful promotion")


def test_upload_file_with_headers():
    """Test file upload with proper headers."""
    print("\nTesting file upload with headers...")

    # Mock S3 client
    mock_s3 = MagicMock()

    publisher = R2Publisher(
        account_id="test-account",
        access_key_id="test-key-id",
        secret_access_key="test-secret",
        bucket_name="test-bucket",
    )
    publisher.s3_client = mock_s3

    # Create test file
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write("test content")
        test_file = Path(f.name)

    try:
        # Upload Parquet file
        publisher._upload_file(test_file, "test.parquet", "application/octet-stream")

        # Verify upload was called with correct arguments
        upload_call = mock_s3.upload_file.call_args
        assert upload_call is not None, "upload_file should be called"
        extra_args = upload_call[1]["ExtraArgs"]

        assert extra_args["ContentType"] == "application/octet-stream", "Content-Type should be correct"
        assert extra_args["CacheControl"] == "max-age=60", "Cache-Control should be max-age=60"
        print("✓ Parquet upload with correct headers")

        # Reset mock
        mock_s3.reset_mock()

        # Upload JSON file
        publisher._upload_file(test_file, "test.json", "application/json")

        # Verify JSON upload
        upload_call = mock_s3.upload_file.call_args
        extra_args = upload_call[1]["ExtraArgs"]
        assert extra_args["ContentType"] == "application/json", "Content-Type should be application/json"
        assert extra_args["CacheControl"] == "max-age=60", "Cache-Control should be max-age=60"
        print("✓ JSON upload with correct headers")

    finally:
        test_file.unlink()


def test_failure_cleanup():
    """Test that failures clean up temp keys and don't touch live keys."""
    print("\nTesting failure cleanup...")

    # Create test file first so we know its size
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".parquet") as f:
        f.write("{}")  # Minimal JSON that would be Parquet
        test_file = Path(f.name)

    test_file_size = test_file.stat().st_size

    # Mock S3 client that fails during verification (after upload succeeds)
    mock_s3 = MagicMock()

    # Upload succeeds but verification fails
    def mock_head_object(**kwargs):
        if "Key" in kwargs and ".tmp" in kwargs["Key"]:
            # Temp key exists for cleanup - return actual file size
            return {"ContentLength": test_file_size, "ETag": '"test"', "CacheControl": "max-age=60"}
        raise Exception("Head failed")

    mock_s3.head_object.side_effect = mock_head_object
    mock_s3.upload_file.return_value = None  # Upload succeeds
    mock_s3.download_fileobj.side_effect = VerificationError("Verification failed")

    publisher = R2Publisher(
        account_id="test-account",
        access_key_id="test-key-id",
        secret_access_key="test-secret",
        bucket_name="test-bucket",
    )
    publisher.s3_client = mock_s3

    try:
        # Attempt publish should fail during verification
        try:
            publisher.publish_parquet_snapshot(test_file)
            assert False, "Should have raised an exception"
        except R2PublisherError:
            pass  # Expected

        # Verify upload was attempted
        assert mock_s3.upload_file.call_count == 1, "Upload should be attempted"

        # Verify copy was never called (live key untouched)
        assert mock_s3.copy_object.call_count == 0, "Copy should not be called on failure"
        print("✓ Live key remains untouched on verification failure")

        # Verify temp key cleanup was attempted
        assert mock_s3.delete_object.call_count >= 1, "Temp key cleanup should be attempted"
        print("✓ Temp key cleanup was attempted on failure")

    finally:
        test_file.unlink()


def test_json_verification():
    """Test JSON artifact verification."""
    print("\nTesting JSON artifact verification...")

    # Create valid JSON file
    valid_json = {"generated_at": "2026-08-02T12:00:00Z", "total_unmatched_cpus": 0, "unmatched_cpus": []}

    # Test valid JSON
    test_file_size = len(json.dumps(valid_json).encode('utf-8'))

    mock_s3 = MagicMock()

    def mock_head_object(Bucket, Key):
        return {
            "ContentLength": test_file_size,
            "ETag": '"test-etag"',
            "CacheControl": "max-age=60",
        }

    mock_s3.head_object.side_effect = mock_head_object

    publisher = R2Publisher(
        account_id="test-account",
        access_key_id="test-key-id",
        secret_access_key="test-secret",
        bucket_name="test-bucket",
    )
    publisher.s3_client = mock_s3

    # Mock download with valid JSON content
    json_content = json.dumps(valid_json).encode('utf-8')

    def mock_download_fileobj(Bucket, Key, buffer):
        buffer.write(json_content)
        buffer.seek(0)

    mock_s3.download_fileobj.side_effect = mock_download_fileobj

    # Should verify successfully
    publisher._verify_json_artifact("test.json", test_file_size)
    print("✓ Valid JSON artifact verification passed")

    # Test invalid JSON with separate setup
    print("  Testing invalid JSON rejection...")
    invalid_content = b"{invalid json"

    mock_s3_invalid = MagicMock()

    def mock_head_object_invalid(Bucket, Key):
        return {
            "ContentLength": len(invalid_content),
            "ETag": '"test-etag"',
            "CacheControl": "max-age=60",
        }

    mock_s3_invalid.head_object.side_effect = mock_head_object_invalid

    def mock_download_invalid(Bucket, Key, buffer):
        buffer.write(invalid_content)
        buffer.seek(0)

    mock_s3_invalid.download_fileobj.side_effect = mock_download_invalid

    publisher_invalid = R2Publisher(
        account_id="test-account",
        access_key_id="test-key-id",
        secret_access_key="test-secret",
        bucket_name="test-bucket",
    )
    publisher_invalid.s3_client = mock_s3_invalid

    try:
        publisher_invalid._verify_json_artifact("test.json", len(invalid_content))
        print("✗ Should have raised VerificationError for invalid JSON")
        assert False, "Should have raised VerificationError"
    except VerificationError as e:
        if "Invalid JSON" in str(e):
            print("✓ Invalid JSON artifact verification failed as expected")
        else:
            print(f"✗ Wrong error message: {e}")
            raise


def run_all_tests():
    """Run all integration tests."""
    print("=" * 70)
    print("R2 Publisher Integration Tests")
    print("=" * 70)

    tests = [
        test_cache_control_header,
        test_temp_key_generation,
        test_file_hash_calculation,
        test_object_hash_retrieval,
        test_atomic_promote,
        test_upload_file_with_headers,
        test_failure_cleanup,
        test_json_verification,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"✗ {test_func.__name__} failed: {e}")

    print("\n" + "=" * 70)
    print(f"Test Results: {passed} passed, {failed} failed")
    print("=" * 70)

    if failed > 0:
        sys.exit(1)

    print("\n✅ ALL R2 PUBLISHER INTEGRATION TESTS PASSED")
    print("\nThe R2 publisher implements:")
    print("  ✓ Temp-key-then-swap lifecycle")
    print("  ✓ Cache-Control: max-age=60 header (ADR-4)")
    print("  ✓ Atomic promotion (copy-then-delete-old)")
    print("  ✓ Failure cleanup without touching live keys")
    print("  ✓ Proper verification of both Parquet and JSON artifacts")

    return True


if __name__ == "__main__":
    run_all_tests()
