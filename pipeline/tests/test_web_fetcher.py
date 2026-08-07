"""
Unit tests for web_fetcher module.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.web_fetcher import WebFetcherError, fetch_web_content


class TestWebFetcher:
    """Tests for web content fetching."""

    def test_fetch_web_content_creates_cache_on_first_run(self, tmp_path):
        """Test that first run creates a git clone and copies web content."""
        with patch("subprocess.run") as mock_run:
            # Mock successful git clone and copy operations
            mock_run.return_value = MagicMock(returncode=0)

            web_dir = fetch_web_content(tmp_path)

            # Should have created git clone, sparse checkout, and copy
            assert mock_run.call_count >= 4
            assert web_dir == tmp_path

    def test_fetch_web_content_updates_existing_cache(self, tmp_path):
        """Test that subsequent runs update existing cache."""
        cache_dir = Path("/tmp/hetzner-web-cache")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # Simulate existing cache
            with patch.object(Path, "exists", return_value=True):
                web_dir = fetch_web_content(tmp_path)

                # Should do git fetch/reset instead of clone
                call_args = [call[0][0] for call in mock_run.call_args_list]
                assert any("fetch" in cmd for cmd in call_args)
                assert any("reset" in cmd for cmd in call_args)

    def test_fetch_web_content_handles_git_timeout(self, tmp_path):
        """Test that git timeout raises WebFetcherError."""
        import subprocess

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("git", 60)

            with pytest.raises(WebFetcherError, match="timed out"):
                fetch_web_content(tmp_path)

    def test_fetch_web_content_handles_git_failure(self, tmp_path):
        """Test that git failure raises WebFetcherError."""
        import subprocess

        with patch("subprocess.run") as mock_run:
            result = MagicMock(returncode=1)
            result.stderr = b"fatal: repository not found"
            mock_run.return_value = result

            with pytest.raises(WebFetcherError, match="Git operation failed"):
                fetch_web_content(tmp_path)

    def test_fetch_web_content_requires_existing_target_dir(self):
        """Test that non-existent target directory raises error."""
        with pytest.raises(WebFetcherError, match="Target directory does not exist"):
            fetch_web_content("/nonexistent/path")

    @pytest.mark.integration
    def test_real_web_fetch(self, tmp_path):
        """Integration test: actually fetch web content from GitHub."""
        # This test requires network access and git installed
        try:
            web_dir = fetch_web_content(tmp_path)

            # Verify web directory was created
            assert web_dir.exists()
            assert web_dir.is_dir()

            # Verify expected files exist
            assert (web_dir / "index.html").exists()
            assert (web_dir / "snapshot-diff.js").exists()
            assert (web_dir / "snapshot-diff.css").exists()

        except (subprocess.CalledProcessError, WebFetcherError) as e:
            pytest.skip(f"Network/git unavailable: {e}")
