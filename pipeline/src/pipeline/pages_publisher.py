"""
Cloudflare Pages Publisher for Hetzner Auction Dashboard

Publishes auction data artifacts to Cloudflare Pages using wrangler CLI.

This publisher verifies required artifacts exist and are valid, then deploys
the directory to Cloudflare Pages using the wrangler CLI.

Environment variables required:
- CLOUDFLARE_PAGES_PROJECT: Cloudflare Pages project name
- CLOUDFLARE_ACCOUNT_ID: Cloudflare account ID
- CF_API_TOKEN: Cloudflare API token with Pages:Edit permission
"""

import json
import logging
import os
import subprocess
from pathlib import Path

import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


class PagesPublisherError(Exception):
    """Raised when Pages publishing fails."""
    pass


class PagesPublisher:
    """
    Publishes a directory of artifacts to Cloudflare Pages.

    Verifies that current_snapshot.parquet and unmatched-cpus.json exist
    and are valid, then runs wrangler pages deploy.
    """

    def __init__(self, directory: str | Path):
        """
        Initialize the publisher with a directory to deploy.

        Args:
            directory: Path to directory containing artifacts to deploy
        """
        self.directory = Path(directory)

        # Read required environment variables
        self.project_name = os.getenv("CLOUDFLARE_PAGES_PROJECT")
        self.account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
        self.api_token = os.getenv("CF_API_TOKEN")

        if not self.project_name:
            raise PagesPublisherError("CLOUDFLARE_PAGES_PROJECT environment variable not set")
        if not self.account_id:
            raise PagesPublisherError("CLOUDFLARE_ACCOUNT_ID environment variable not set")
        if not self.api_token:
            raise PagesPublisherError("CF_API_TOKEN environment variable not set")

        logger.info(f"Initialized PagesPublisher for directory: {self.directory}")
        logger.info(f"Cloudflare Pages project: {self.project_name}")

    def publish(self) -> dict:
        """
        Verify artifacts and deploy to Cloudflare Pages.

        This method:
        1. Verifies current_snapshot.parquet exists and is valid Parquet
        2. Verifies unmatched-cpus.json exists and is valid JSON
        3. Runs wrangler pages deploy on the directory

        Returns:
            dict with deployment metadata

        Raises:
            PagesPublisherError: If verification fails or wrangler deploy fails
        """
        logger.info("Starting publish process...")

        # Verify current_snapshot.parquet
        parquet_path = self.directory / "current_snapshot.parquet"
        self._verify_parquet(parquet_path)
        logger.info(f"Verified current_snapshot.parquet ({parquet_path.stat().st_size} bytes)")

        # Verify unmatched-cpus.json
        json_path = self.directory / "unmatched-cpus.json"
        self._verify_json(json_path)
        logger.info(f"Verified unmatched-cpus.json ({json_path.stat().st_size} bytes)")

        # Deploy via wrangler
        deployment_info = self._wrangler_deploy()

        return {
            "directory": str(self.directory),
            "parquet_size": parquet_path.stat().st_size,
            "json_size": json_path.stat().st_size,
            "deployment_info": deployment_info,
        }

    def _verify_parquet(self, path: Path) -> None:
        """
        Verify that a Parquet file exists and is valid.

        Args:
            path: Path to Parquet file

        Raises:
            PagesPublisherError: If verification fails
        """
        if not path.exists():
            raise PagesPublisherError(f"Parquet file not found: {path}")

        file_size = path.stat().st_size
        if file_size == 0:
            raise PagesPublisherError(f"Parquet file is empty: {path}")

        try:
            table = pq.read_table(path)
            logger.info(f"Parquet file valid: {len(table)} rows, {file_size} bytes")
        except Exception as e:
            raise PagesPublisherError(f"Invalid Parquet file: {e}")

    def _verify_json(self, path: Path) -> None:
        """
        Verify that a JSON file exists and is valid.

        Args:
            path: Path to JSON file

        Raises:
            PagesPublisherError: If verification fails
        """
        if not path.exists():
            raise PagesPublisherError(f"JSON file not found: {path}")

        file_size = path.stat().st_size
        if file_size == 0:
            raise PagesPublisherError(f"JSON file is empty: {path}")

        try:
            with open(path, "r", encoding="utf-8") as f:
                json.load(f)
            logger.info(f"JSON file valid: {file_size} bytes")
        except json.JSONDecodeError as e:
            raise PagesPublisherError(f"Invalid JSON file: {e}")

    def _wrangler_deploy(self) -> dict:
        """
        Run wrangler pages deploy on the directory.

        Returns:
            dict with deployment metadata

        Raises:
            PagesPublisherError: If wrangler deploy fails
        """
        # Set Cloudflare credentials for wrangler
        env = os.environ.copy()
        env["CLOUDFLARE_API_TOKEN"] = self.api_token
        env["CLOUDFLARE_ACCOUNT_ID"] = self.account_id

        cmd = [
            "wrangler",
            "pages",
            "deploy",
            str(self.directory),
            f"--project-name={self.project_name}",
            "--branch=main",
            "--commit-dirty=true",
        ]

        logger.info(f"Running wrangler deploy: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
            )
        except subprocess.TimeoutExpired as e:
            raise PagesPublisherError(f"Wrangler deploy timed out after 300 seconds")

        if result.returncode != 0:
            error_output = result.stderr if result.stderr else result.stdout
            raise PagesPublisherError(f"Wrangler deploy failed (exit {result.returncode}): {error_output}")

        logger.info("Wrangler deploy succeeded")

        return {
            "success": True,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
