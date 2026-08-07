"""
Web Content Fetcher for Hetzner Auction Dashboard

Fetches the latest web/ content from GitHub at the start of each pipeline cycle.
This ensures web code changes take effect without requiring a pipeline image rebuild.

Per ADR-7: the pipeline keeps its own current copy of web/ (pulled from the repo)
rather than baking it into the image at build time.
"""

import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Public read-only GitHub repo URL - no auth needed
REPO_URL = "https://github.com/jedarden/hetzner-auction-dashboard.git"
WEB_SUBDIR = "web"


class WebFetcherError(Exception):
    """Raised when web content fetch fails."""
    pass


def fetch_web_content(target_dir: Path | str, repo_url: str = REPO_URL, web_subdir: str = WEB_SUBDIR) -> Path:
    """
    Fetch the latest web/ content from GitHub.

    Performs a shallow clone of just the web/ subdirectory. Uses a temporary
    cache directory that persists across cycles to support incremental git pull.

    Args:
        target_dir: Directory where web/ content will be copied (must exist)
        repo_url: GitHub repository URL (public, no auth needed)
        web_subdir: Subdirectory within the repo to fetch (default: "web")

    Returns:
        Path to the web content directory (target_dir / web_subdir)

    Raises:
        WebFetcherError: If git clone/pull or copy fails
    """
    target_dir = Path(target_dir)
    web_target = target_dir / web_subdir

    if not target_dir.exists():
        raise WebFetcherError(f"Target directory does not exist: {target_dir}")

    # Use a persistent cache directory in /tmp for the git clone
    cache_dir = Path("/tmp/hetzner-web-cache")

    try:
        if cache_dir.exists():
            # Repository already cloned - do a shallow fetch and reset
            logger.info(f"Updating cached web/ content from {cache_dir}")
            subprocess.run(
                ["git", "fetch", "--depth=1", "origin", "main"],
                cwd=cache_dir,
                check=True,
                capture_output=True,
                timeout=60,
            )
            subprocess.run(
                ["git", "reset", "--hard", "origin/main"],
                cwd=cache_dir,
                check=True,
                capture_output=True,
                timeout=30,
            )
        else:
            # First time - shallow clone just the web/ subdirectory
            logger.info(f"Cloning {web_subdir}/ from {repo_url} to {cache_dir}")
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth=1",
                    "--no-checkout",
                    "--filter=blob:none",
                    "--sparse",
                    repo_url,
                    str(cache_dir),
                ],
                check=True,
                capture_output=True,
                timeout=120,
            )

            # Configure sparse checkout to only pull web/
            subprocess.run(
                ["git", "sparse-checkout", "init", "--cone"],
                cwd=cache_dir,
                check=True,
                capture_output=True,
                timeout=30,
            )
            subprocess.run(
                ["git", "sparse-checkout", "set", web_subdir],
                cwd=cache_dir,
                check=True,
                capture_output=True,
                timeout=30,
            )
            subprocess.run(
                ["git", "checkout"],
                cwd=cache_dir,
                check=True,
                capture_output=True,
                timeout=30,
            )

        # Verify web/ exists in the cache
        cached_web = cache_dir / web_subdir
        if not cached_web.exists():
            raise WebFetcherError(f"Web subdirectory not found in cache: {cached_web}")

        # Copy web/ content to target directory (replace if exists)
        logger.info(f"Copying web/ content from {cached_web} to {web_target}")

        # Remove existing web/ directory if it exists
        if web_target.exists():
            import shutil
            shutil.rmtree(web_target)

        # Copy fresh content
        import shutil
        shutil.copytree(cached_web, web_target)

        logger.info(f"Web content updated successfully at {web_target}")
        return web_target

    except subprocess.TimeoutExpired as e:
        raise WebFetcherError(f"Git operation timed out: {e}")
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if e.stderr else "no stderr output"
        raise WebFetcherError(f"Git operation failed: {stderr}")
    except Exception as e:
        raise WebFetcherError(f"Failed to fetch web content: {e}")
