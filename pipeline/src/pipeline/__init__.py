"""
Hetzner Auction Pipeline

Fetches, enriches, and publishes Hetzner auction data.
"""

__version__ = "0.1.10"

from pipeline.web_fetcher import WebFetcherError, fetch_web_content

__all__ = ["WebFetcherError", "fetch_web_content"]
