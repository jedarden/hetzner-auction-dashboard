"""
Unit tests for Pages Publisher

Tests the verify-then-deploy lifecycle for publishing auction data artifacts
to Cloudflare Pages via wrangler CLI.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

import pytest
import pyarrow.parquet as pq

from pipeline.pages_publisher import PagesPublisher, PagesPublisherError
from pipeline.cpu_matcher import BenchmarkMatch
from pipeline.parquet_writer import write_listings_to_parquet
from pipeline.unmatched_reporter import UnmatchedCpuReporter


class TestPagesPublisherInitialization:
    """Test Pages publisher initialization."""

    def test_init_with_directory_and_env_vars(self):
        """Publisher should initialize with directory and environment variables."""
        deploy_dir = tempfile.mkdtemp()

        # Set required environment variables
        os.environ["CLOUDFLARE_PAGES_PROJECT"] = "test-project"
        os.environ["CLOUDFLARE_ACCOUNT_ID"] = "test-account"
        os.environ["CF_API_TOKEN"] = "test-token"

        try:
            publisher = PagesPublisher(directory=deploy_dir)

            assert publisher.directory == Path(deploy_dir)
            assert publisher.project_name == "test-project"
            assert publisher.account_id == "test-account"
            assert publisher.api_token == "test-token"
        finally:
            del os.environ["CLOUDFLARE_PAGES_PROJECT"]
            del os.environ["CLOUDFLARE_ACCOUNT_ID"]
            del os.environ["CF_API_TOKEN"]

    def test_init_missing_project_env_var_raises_error(self):
        """Publisher should raise error when CLOUDFLARE_PAGES_PROJECT not set."""
        os.environ.pop("CLOUDFLARE_PAGES_PROJECT", None)
        os.environ["CLOUDFLARE_ACCOUNT_ID"] = "test-account"
        os.environ["CF_API_TOKEN"] = "test-token"

        deploy_dir = tempfile.mkdtemp()

        with pytest.raises(PagesPublisherError, match="CLOUDFLARE_PAGES_PROJECT environment variable not set"):
            PagesPublisher(directory=deploy_dir)

    def test_init_missing_account_id_env_var_raises_error(self):
        """Publisher should raise error when CLOUDFLARE_ACCOUNT_ID not set."""
        os.environ["CLOUDFLARE_PAGES_PROJECT"] = "test-project"
        os.environ.pop("CLOUDFLARE_ACCOUNT_ID", None)
        os.environ["CF_API_TOKEN"] = "test-token"

        deploy_dir = tempfile.mkdtemp()

        with pytest.raises(PagesPublisherError, match="CLOUDFLARE_ACCOUNT_ID environment variable not set"):
            PagesPublisher(directory=deploy_dir)

    def test_init_missing_api_token_env_var_raises_error(self):
        """Publisher should raise error when CF_API_TOKEN not set."""
        os.environ["CLOUDFLARE_PAGES_PROJECT"] = "test-project"
        os.environ["CLOUDFLARE_ACCOUNT_ID"] = "test-account"
        os.environ.pop("CF_API_TOKEN", None)

        deploy_dir = tempfile.mkdtemp()

        with pytest.raises(PagesPublisherError, match="CF_API_TOKEN environment variable not set"):
            PagesPublisher(directory=deploy_dir)


class TestArtifactVerification:
    """Test artifact verification methods."""

    def test_verify_parquet_success(self):
        """Should successfully verify valid Parquet file."""
        listing = _make_sample_listing()

        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
            parquet_path = Path(tmp.name)

        try:
            write_listings_to_parquet([listing], parquet_path)

            publisher = _create_publisher()
            # Should not raise
            publisher._verify_parquet(parquet_path)
        finally:
            parquet_path.unlink()

    def test_verify_parquet_nonexistent_raises_error(self):
        """Should raise error for nonexistent Parquet file."""
        publisher = _create_publisher()

        with pytest.raises(PagesPublisherError, match="Parquet file not found"):
            publisher._verify_parquet(Path("/nonexistent/file.parquet"))

    def test_verify_parquet_empty_file_raises_error(self):
        """Should raise error for empty Parquet file."""
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
            parquet_path = Path(tmp.name)

        try:
            publisher = _create_publisher()

            with pytest.raises(PagesPublisherError, match="Parquet file is empty"):
                publisher._verify_parquet(parquet_path)
        finally:
            parquet_path.unlink()

    def test_verify_parquet_invalid_file_raises_error(self):
        """Should raise error for invalid Parquet file."""
        with tempfile.NamedTemporaryFile(suffix=".parquet", mode="w", delete=False) as tmp:
            tmp.write("not a parquet file")
            parquet_path = Path(tmp.name)

        try:
            publisher = _create_publisher()

            with pytest.raises(PagesPublisherError, match="Invalid Parquet file"):
                publisher._verify_parquet(parquet_path)
        finally:
            parquet_path.unlink()

    def test_verify_json_success(self):
        """Should successfully verify valid JSON file."""
        reporter = UnmatchedCpuReporter()
        cpu_match = BenchmarkMatch(
            cpu_raw="Unknown CPU",
            matched=False,
            cpu_normalized=None,
            passmark_id=None,
            single_thread_score=None,
            multi_thread_score=None,
            cores=None,
            threads=None,
            match_method=None,
        )
        reporter.process_listing("test-listing-1", cpu_match)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            json_path = Path(tmp.name)

        try:
            reporter.generate_report(json_path)

            publisher = _create_publisher()
            # Should not raise
            publisher._verify_json(json_path)
        finally:
            json_path.unlink()

    def test_verify_json_nonexistent_raises_error(self):
        """Should raise error for nonexistent JSON file."""
        publisher = _create_publisher()

        with pytest.raises(PagesPublisherError, match="JSON file not found"):
            publisher._verify_json(Path("/nonexistent/file.json"))

    def test_verify_json_empty_file_raises_error(self):
        """Should raise error for empty JSON file."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            json_path = Path(tmp.name)

        try:
            publisher = _create_publisher()

            with pytest.raises(PagesPublisherError, match="JSON file is empty"):
                publisher._verify_json(json_path)
        finally:
            json_path.unlink()

    def test_verify_json_invalid_content_raises_error(self):
        """Should raise error for invalid JSON content."""
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as tmp:
            tmp.write("{invalid json content")
            json_path = Path(tmp.name)

        try:
            publisher = _create_publisher()

            with pytest.raises(PagesPublisherError, match="Invalid JSON file"):
                publisher._verify_json(json_path)
        finally:
            json_path.unlink()


class TestWranglerDeploy:
    """Test wrangler pages deploy execution."""

    @patch("subprocess.run")
    def test_wrangler_deploy_success(self, mock_run):
        """Should successfully run wrangler deploy."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Published successfully",
            stderr=""
        )

        publisher = _create_publisher()
        deploy_dir = tempfile.mkdtemp()

        result = publisher._wrangler_deploy()

        assert result["success"] is True
        assert "stdout" in result
        assert "stderr" in result

        # Verify wrangler was called with correct arguments
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert "wrangler" in cmd
        assert "pages" in cmd
        assert "deploy" in cmd
        assert "--project-name=test-project" in cmd
        assert "--branch=main" in cmd
        assert "--commit-dirty=true" in cmd

        # Verify credentials were set
        env = call_args[1]["env"]
        assert env["CLOUDFLARE_API_TOKEN"] == "test-token"
        assert env["CLOUDFLARE_ACCOUNT_ID"] == "test-account"

    @patch("subprocess.run")
    def test_wrangler_deploy_failure_raises_error(self, mock_run):
        """Should raise PagesPublisherError when wrangler fails."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Deploy failed: authentication error"
        )

        publisher = _create_publisher()

        with pytest.raises(PagesPublisherError, match="Wrangler deploy failed"):
            publisher._wrangler_deploy()

    @patch("subprocess.run")
    def test_wrangler_deploy_timeout_raises_error(self, mock_run):
        """Should raise PagesPublisherError on timeout."""
        from subprocess import TimeoutExpired
        mock_run.side_effect = TimeoutExpired("wrangler", 300)

        publisher = _create_publisher()

        with pytest.raises(PagesPublisherError, match="Wrangler deploy timed out"):
            publisher._wrangler_deploy()


class TestPublish:
    """Test end-to-end publish method."""

    @patch("subprocess.run")
    def test_publish_success(self, mock_run):
        """Should successfully publish both artifacts."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Published successfully",
            stderr=""
        )

        # Create sample files
        listing = _make_sample_listing()
        reporter = UnmatchedCpuReporter()
        cpu_match = BenchmarkMatch(
            cpu_raw="Unknown CPU",
            matched=False,
            cpu_normalized=None,
            passmark_id=None,
            single_thread_score=None,
            multi_thread_score=None,
            cores=None,
            threads=None,
            match_method=None,
        )
        reporter.process_listing("test-listing-1", cpu_match)

        with tempfile.TemporaryDirectory(prefix="hetzner-pages-test-") as tmpdir:
            deploy_dir = Path(tmpdir)
            parquet_path = deploy_dir / "current_snapshot.parquet"
            json_path = deploy_dir / "unmatched-cpus.json"

            write_listings_to_parquet([listing], parquet_path)
            reporter.generate_report(json_path)

            publisher = PagesPublisher(directory=deploy_dir)
            result = publisher.publish()

            assert result["directory"] == str(deploy_dir)
            assert result["parquet_size"] > 0
            assert result["json_size"] > 0
            assert "deployment_info" in result

    def test_publish_missing_parquet_raises_error(self):
        """Should raise error when current_snapshot.parquet is missing."""
        with tempfile.TemporaryDirectory(prefix="hetzner-pages-test-") as tmpdir:
            deploy_dir = Path(tmpdir)

            publisher = PagesPublisher(directory=deploy_dir)

            with pytest.raises(PagesPublisherError, match="Parquet file not found"):
                publisher.publish()

    def test_publish_missing_json_raises_error(self):
        """Should raise error when unmatched-cpus.json is missing."""
        with tempfile.TemporaryDirectory(prefix="hetzner-pages-test-") as tmpdir:
            deploy_dir = Path(tmpdir)

            # Create parquet file but not JSON
            listing = _make_sample_listing()
            parquet_path = deploy_dir / "current_snapshot.parquet"
            write_listings_to_parquet([listing], parquet_path)

            publisher = PagesPublisher(directory=deploy_dir)

            with pytest.raises(PagesPublisherError, match="JSON file not found"):
                publisher.publish()

    def test_publish_invalid_parquet_raises_error(self):
        """Should raise error when Parquet file is invalid."""
        with tempfile.TemporaryDirectory(prefix="hetzner-pages-test-") as tmpdir:
            deploy_dir = Path(tmpdir)

            # Create invalid parquet file
            parquet_path = deploy_dir / "current_snapshot.parquet"
            parquet_path.write_text("invalid parquet content")

            # Create valid JSON
            json_path = deploy_dir / "unmatched-cpus.json"
            json_path.write_text('{"test": "data"}')

            publisher = PagesPublisher(directory=deploy_dir)

            with pytest.raises(PagesPublisherError, match="Invalid Parquet file"):
                publisher.publish()

    def test_publish_invalid_json_raises_error(self):
        """Should raise error when JSON file is invalid."""
        with tempfile.TemporaryDirectory(prefix="hetzner-pages-test-") as tmpdir:
            deploy_dir = Path(tmpdir)

            # Create valid parquet
            listing = _make_sample_listing()
            parquet_path = deploy_dir / "current_snapshot.parquet"
            write_listings_to_parquet([listing], parquet_path)

            # Create invalid JSON
            json_path = deploy_dir / "unmatched-cpus.json"
            json_path.write_text("{invalid json")

            publisher = PagesPublisher(directory=deploy_dir)

            with pytest.raises(PagesPublisherError, match="Invalid JSON file"):
                publisher.publish()

    @patch("subprocess.run")
    def test_publish_wrangler_failure_raises_error(self, mock_run):
        """Should raise error when wrangler deploy fails."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="Deploy failed"
        )

        with tempfile.TemporaryDirectory(prefix="hetzner-pages-test-") as tmpdir:
            deploy_dir = Path(tmpdir)

            # Create valid files
            listing = _make_sample_listing()
            parquet_path = deploy_dir / "current_snapshot.parquet"
            write_listings_to_parquet([listing], parquet_path)

            json_path = deploy_dir / "unmatched-cpus.json"
            json_path.write_text('{"test": "data"}')

            publisher = PagesPublisher(directory=deploy_dir)

            with pytest.raises(PagesPublisherError, match="Wrangler deploy failed"):
                publisher.publish()


# Helper functions

def _create_publisher():
    """Create a test publisher with environment variables set."""
    os.environ["CLOUDFLARE_PAGES_PROJECT"] = "test-project"
    os.environ["CLOUDFLARE_ACCOUNT_ID"] = "test-account"
    os.environ["CF_API_TOKEN"] = "test-token"

    deploy_dir = tempfile.mkdtemp()
    return PagesPublisher(directory=deploy_dir)


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
):
    """Helper to create a sample EnrichedListing for testing."""
    from datetime import UTC, datetime
    from pipeline.enricher import CostMetricsEnricher
    from pipeline.fetcher import RawListing, DiskSpec

    if disks is None:
        disks = [DiskSpec(type="NVMe", count=2, capacity_gb=480)]

    if fetched_at is None:
        fetched_at = datetime.now(UTC)

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
        cores=None,
        threads=None,
        match_method=benchmark_match_method,
    )

    enricher = CostMetricsEnricher()
    return enricher.enrich_listing(raw_listing, cpu_match)
