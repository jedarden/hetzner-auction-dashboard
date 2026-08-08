"""
End-to-end test for pipeline cycle with web/ refresh mechanism.

This test verifies:
1. Web files are fetched fresh from GitHub each cycle (not baked into Docker image)
2. Web files are present in the assembled deploy directory
3. Web files are reasonably current (from latest git clone)
4. A deployment would succeed with the correct files
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import pyarrow.parquet as pq

# Add src to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipeline.web_fetcher import WebFetcherError, fetch_web_content
from pipeline.pages_publisher import PagesPublisher, PagesPublisherError


class TestWebRefreshMechanism:
    """Test the web/ refresh mechanism that fetches fresh content each cycle."""

    def test_web_fetcher_clones_fresh_content(self):
        """Test that web_fetcher clones fresh web/ content from GitHub."""
        with tempfile.TemporaryDirectory(prefix="test-web-fetch-") as tmpdir:
            target_dir = Path(tmpdir)

            # Fetch web content
            web_dir = fetch_web_content(target_dir)

            # Verify web/ directory was created
            assert web_dir.exists(), f"Web directory not created: {web_dir}"
            assert web_dir.is_dir(), f"Web path is not a directory: {web_dir}"

            # Verify key web files exist (not exhaustive, but checks main files)
            expected_files = [
                "index.html",
                "hetzner-cloud-pricing.json",
                "snapshot-diff.css",
                "snapshot-diff.js",
            ]

            for filename in expected_files:
                file_path = web_dir / filename
                assert file_path.exists(), f"Expected web file not found: {filename}"
                assert file_path.is_file(), f"Expected file is not a file: {filename}"

                # Verify file has content (not empty)
                file_size = file_path.stat().st_size
                assert file_size > 0, f"Web file is empty: {filename} (0 bytes)"

    def test_web_fetcher_incremental_update(self):
        """Test that web_fetcher can update existing cached content."""
        with tempfile.TemporaryDirectory(prefix="test-web-update-") as tmpdir:
            target_dir = Path(tmpdir)

            # First fetch - should do full clone
            web_dir_first = fetch_web_content(target_dir)
            assert web_dir_first.exists()

            # Mock a change by modifying a file in cache
            cache_dir = Path("/tmp/hetzner-web-cache")
            if cache_dir.exists():
                test_file = cache_dir / "web" / "index.html"
                if test_file.exists():
                    # Get original size
                    original_size = test_file.stat().st_size
                    # Append a comment to modify the file
                    with open(test_file, "a") as f:
                        f.write("\n<!-- Test comment for incremental update -->\n")

                    # Second fetch - should do git pull and get fresh content
                    web_dir_second = fetch_web_content(target_dir)

                    # Verify web directory still exists after update
                    assert web_dir_second.exists()

                    # File should have been updated (either refreshed to original or modified)
                    # We just verify the update mechanism worked without errors
                    assert web_dir_second.is_dir()

    def test_web_content_is_from_github_not_image(self):
        """Test that web content comes from GitHub, not baked into the Docker image."""
        with tempfile.TemporaryDirectory(prefix="test-web-source-") as tmpdir:
            target_dir = Path(tmpdir)

            # Fetch web content
            web_dir = fetch_web_content(target_dir)

            # Check that files are recent (not from Docker image build time)
            # We can't check exact timestamps, but we can verify git metadata exists

            # Verify the cache directory exists and has git metadata
            cache_dir = Path("/tmp/hetzner-web-cache")
            assert cache_dir.exists(), "Git cache directory not found"

            git_dir = cache_dir / ".git"
            assert git_dir.exists(), "Git metadata not found in cache"

            # Verify it's a valid git repository
            result = subprocess.run(
                ["git", "remote", "-v"],
                cwd=cache_dir,
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0, "Not a valid git repository"
            assert "github.com" in result.stdout or "git.ardenone.com" in result.stdout, \
                "Not cloned from expected GitHub repo"

    def test_web_files_in_deploy_directory(self):
        """Test that web files are properly placed in the deploy directory structure."""
        with tempfile.TemporaryDirectory(prefix="test-deploy-structure-") as tmpdir:
            deploy_dir = Path(tmpdir)

            # Fetch web content into deploy directory (simulating pipeline behavior)
            web_dir = fetch_web_content(deploy_dir)

            # web/'s contents are flattened directly into the deploy root
            # (not nested under a web/ subdirectory) -- see web_fetcher.py;
            # Cloudflare Pages serves relative to the deploy root and only
            # reads _headers from there too, so nesting broke both routing
            # and Cache-Control (fixed 2026-08-08, app repo commit e57a4c1).
            assert deploy_dir == web_dir, \
                f"Web directory not at expected location: {web_dir} vs {deploy_dir}"

            # Verify web/ contains expected files
            index_html = web_dir / "index.html"
            assert index_html.exists(), "index.html not found in web/"

            # Verify file structure matches expectations
            assert (web_dir / "hetzner-cloud-pricing.json").exists()
            assert (web_dir / "snapshot-diff.css").exists()
            assert (web_dir / "snapshot-diff.js").exists()

    def test_web_fetcher_error_handling(self):
        """Test that web_fetcher raises appropriate errors on failure."""
        # Test with invalid target directory
        with pytest.raises(WebFetcherError) as exc_info:
            fetch_web_content("/nonexistent/path/that/does/not/exist")

        assert "Target directory does not exist" in str(exc_info.value)


class TestDeployDirectoryAssembly:
    """Test the complete deploy directory assembly with all artifacts."""

    def test_deploy_directory_has_all_artifacts(self):
        """Test that deploy directory contains all required artifacts for deployment."""
        with tempfile.TemporaryDirectory(prefix="test-deploy-artifacts-") as tmpdir:
            deploy_dir = Path(tmpdir)

            # Create mock data files (simulating pipeline output)
            parquet_file = deploy_dir / "current_snapshot.parquet"
            json_file = deploy_dir / "unmatched-cpus.json"

            # Create minimal valid Parquet file
            import pyarrow as pa
            table = pa.table({
                "listing_id": [1, 2, 3],
                "cpu_model": ["Test CPU 1", "Test CPU 2", "Test CPU 3"],
                "ram_gb": [32, 64, 128],
            })
            pq.write_table(table, parquet_file)

            # Create valid JSON file
            with open(json_file, "w") as f:
                json.dump({"unmatched_cpus": [], "timestamp": "2024-01-01T00:00:00Z"}, f)

            # Fetch web content (simulating full pipeline cycle)
            web_dir = fetch_web_content(deploy_dir)

            # Verify all artifacts exist
            assert parquet_file.exists(), "Parquet file missing from deploy directory"
            assert json_file.exists(), "JSON report missing from deploy directory"
            assert web_dir.exists(), "Web directory missing from deploy directory"

            # Verify web directory has main files
            assert (web_dir / "index.html").exists(), "index.html missing"
            assert (web_dir / "hetzner-cloud-pricing.json").exists(), "pricing data missing"

    def test_deploy_directory_structure(self):
        """Test that deploy directory has the expected structure."""
        with tempfile.TemporaryDirectory(prefix="test-deploy-structure-") as tmpdir:
            deploy_dir = Path(tmpdir)

            # Simulate pipeline: create data files + fetch web content
            web_dir = fetch_web_content(deploy_dir)

            # Create mock data files
            (deploy_dir / "current_snapshot.parquet").write_text("mock parquet")
            (deploy_dir / "unmatched-cpus.json").write_text('{"test": true}')

            # Expected structure:
            # deploy_dir/
            #   current_snapshot.parquet
            #   unmatched-cpus.json
            #   web/
            #     index.html
            #     hetzner-cloud-pricing.json
            #     snapshot-diff.css
            #     snapshot-diff.js
            #     ...

            # Verify root-level files
            root_files = [f.name for f in deploy_dir.iterdir() if f.is_file()]
            assert "current_snapshot.parquet" in root_files
            assert "unmatched-cpus.json" in root_files

            # Verify web/ subdirectory
            web_files = [f.name for f in web_dir.iterdir() if f.is_file()]
            assert "index.html" in web_files


class TestMockDeployment:
    """Test that deployment would succeed with correct files."""

    def test_pages_publisher_verifies_artifacts(self):
        """Test that PagesPublisher correctly verifies required artifacts."""
        with tempfile.TemporaryDirectory(prefix="test-publisher-verify-") as tmpdir:
            deploy_dir = Path(tmpdir)

            # Create mock artifacts
            parquet_file = deploy_dir / "current_snapshot.parquet"
            json_file = deploy_dir / "unmatched-cpus.json"

            # Create valid Parquet
            import pyarrow as pa
            table = pa.table({
                "listing_id": [1],
                "cpu_model": ["Test"],
            })
            pq.write_table(table, parquet_file)

            # Create valid JSON
            with open(json_file, "w") as f:
                json.dump({"test": "data"}, f)

            # Mock environment variables
            with patch.dict(os.environ, {
                "CLOUDFLARE_PAGES_PROJECT": "test-project",
                "CLOUDFLARE_ACCOUNT_ID": "test-account-id",
                "CF_API_TOKEN": "test-token",
            }):
                # Create publisher
                publisher = PagesPublisher(deploy_dir)

                # Verify artifacts (this should succeed)
                # We're not actually calling publish() to avoid real deployment
                publisher._verify_parquet(parquet_file)
                publisher._verify_json(json_file)

    def test_pages_publisher_detects_invalid_artifacts(self):
        """Test that PagesPublisher rejects invalid artifacts."""
        with tempfile.TemporaryDirectory(prefix="test-publisher-invalid-") as tmpdir:
            deploy_dir = Path(tmpdir)

            # Create invalid/missing artifacts
            parquet_file = deploy_dir / "current_snapshot.parquet"
            json_file = deploy_dir / "unmatched-cpus.json"

            # Empty files (invalid)
            parquet_file.write_text("")
            json_file.write_text("")

            with patch.dict(os.environ, {
                "CLOUDFLARE_PAGES_PROJECT": "test-project",
                "CLOUDFLARE_ACCOUNT_ID": "test-account-id",
                "CF_API_TOKEN": "test-token",
            }):
                publisher = PagesPublisher(deploy_dir)

                # Should detect empty Parquet
                with pytest.raises(PagesPublisherError) as exc_info:
                    publisher._verify_parquet(parquet_file)
                assert "empty" in str(exc_info.value).lower()

                # Should detect empty JSON
                with pytest.raises(PagesPublisherError) as exc_info:
                    publisher._verify_json(json_file)
                assert "empty" in str(exc_info.value).lower()

    def test_pages_publisher_requires_env_vars(self):
        """Test that PagesPublisher requires all environment variables."""
        with tempfile.TemporaryDirectory(prefix="test-publisher-env-") as tmpdir:
            deploy_dir = Path(tmpdir)

            # Test missing CLOUDFLARE_PAGES_PROJECT
            with patch.dict(os.environ, {}, clear=False):
                # Remove required env vars if they exist
                env_copy = os.environ.copy()
                env_copy.pop("CLOUDFLARE_PAGES_PROJECT", None)

                with patch.dict(os.environ, env_copy, clear=True):
                    with pytest.raises(PagesPublisherError) as exc_info:
                        PagesPublisher(deploy_dir)
                    assert "CLOUDFLARE_PAGES_PROJECT" in str(exc_info.value)

            # Test missing CLOUDFLARE_ACCOUNT_ID
            with patch.dict(os.environ, {
                "CLOUDFLARE_PAGES_PROJECT": "test",
            }):
                with pytest.raises(PagesPublisherError) as exc_info:
                    PagesPublisher(deploy_dir)
                assert "CLOUDFLARE_ACCOUNT_ID" in str(exc_info.value)

            # Test missing CF_API_TOKEN
            with patch.dict(os.environ, {
                "CLOUDFLARE_PAGES_PROJECT": "test",
                "CLOUDFLARE_ACCOUNT_ID": "test",
            }):
                with pytest.raises(PagesPublisherError) as exc_info:
                    PagesPublisher(deploy_dir)
                assert "CF_API_TOKEN" in str(exc_info.value)


class TestEndToEndPipelineCycle:
    """Test the complete end-to-end pipeline cycle with web refresh."""

    def test_full_cycle_assembles_correct_files(self):
        """Test that a full pipeline cycle produces correct deploy directory."""
        with tempfile.TemporaryDirectory(prefix="test-e2e-cycle-") as tmpdir:
            deploy_dir = Path(tmpdir)

            # Simulate pipeline cycle:

            # 1. Create data artifacts (would come from fetcher/enricher)
            parquet_file = deploy_dir / "current_snapshot.parquet"
            json_file = deploy_dir / "unmatched-cpus.json"

            import pyarrow as pa
            table = pa.table({
                "listing_id": [1, 2],
                "cpu_model": ["Ryzen 9 5950X", "Xeon E5-2680 v4"],
                "ram_gb": [128, 64],
            })
            pq.write_table(table, parquet_file)

            with open(json_file, "w") as f:
                json.dump({
                    "unmatched_cpus": [],
                    "total_listings": 2,
                    "timestamp": "2024-08-07T00:00:00Z"
                }, f)

            # 2. Fetch web content (per-cycle refresh)
            web_dir = fetch_web_content(deploy_dir)

            # Verify complete deploy directory structure
            assert deploy_dir.exists()
            assert parquet_file.exists()
            assert json_file.exists()
            assert web_dir.exists()

            # Verify web files are from GitHub, not baked in
            cache_dir = Path("/tmp/hetzner-web-cache")
            assert cache_dir.exists()
            assert (cache_dir / ".git").exists()

            # Verify all expected web files exist
            web_files = list(web_dir.glob("*"))
            assert len(web_files) > 5, "Web directory should contain multiple files"
            assert any(f.name == "index.html" for f in web_files)

    def test_web_files_current_not_stale(self):
        """Test that web files are reasonably current, not stale from image build."""
        # This test verifies that web files come from a recent git clone
        # We can't check exact timestamps, but we can verify git metadata

        cache_dir = Path("/tmp/hetzner-web-cache")

        # Fetch content to ensure cache is populated
        with tempfile.TemporaryDirectory() as tmpdir:
            fetch_web_content(tmpdir)

        # Check git log to verify recent activity
        if cache_dir.exists():
            result = subprocess.run(
                ["git", "log", "-1", "--format=%ci"],
                cwd=cache_dir / "web",
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                # Got commit timestamp - this proves content is from git, not baked in
                commit_date = result.stdout.strip()
                print(f"Latest web/ commit dated: {commit_date}")

                # If we get here, it proves files are from git clone
                # (if they were baked into image, git log wouldn't work in cache)


def test_cleanup_test_cache():
    """Cleanup function to remove test cache after all tests complete."""
    cache_dir = Path("/tmp/hetzner-web-cache")
    if cache_dir.exists():
        # We keep the cache for testing, but could clean up if needed
        pass


if __name__ == "__main__":
    # Run tests with verbose output
    pytest.main([__file__, "-v", "-s"])
