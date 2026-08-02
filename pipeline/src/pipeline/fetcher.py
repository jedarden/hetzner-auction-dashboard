"""
Hetzner Auction Fetcher

Fetches auction data from Hetzner's Server Auction and returns it in a normalized raw schema.
This is Phase 1 of the pipeline: fetch and define raw schema before any enrichment.

Edge cases handled:
- EC-1: Empty feed result (returns empty list, not an error)
- EC-2: Feed schema change (raises FetchError with sample payload for manual fix)
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class FetchError(Exception):
    """Raised when fetching fails due to network or parse errors."""

    def __init__(self, message: str, status_code: int | None = None, raw_sample: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.raw_sample = raw_sample


@dataclass
class DiskSpec:
    """Single disk specification in a listing."""

    type: str  # "HDD", "SSD", or "NVMe"
    count: int  # Number of disks of this type/size
    capacity_gb: int  # Capacity of ONE disk in GB


@dataclass
class RawListing:
    """
    Raw auction listing before enrichment.

    Matches Data Models pre-enrichment columns:
    - listing_id, datacenter, location, available_from
    - cpu_raw (pre-normalization)
    - ram_gb, ram_ecc
    - disks (as list of DiskSpec)
    - uplink_speed (Mbit/s)
    - price_base, price_setup_fee (EUR cents)
    - fetched_at (timestamp)
    """

    listing_id: str
    datacenter: str
    location: str
    available_from: str | None  # ISO datetime or None if immediately available
    cpu_raw: str
    ram_gb: int
    ram_ecc: bool
    disks: list[DiskSpec]
    uplink_speed: int  # Mbit/s
    price_base: int  # EUR cents
    price_setup_fee: int  # EUR cents
    fetched_at: datetime


class HetznerAuctionFetcher:
    """
    Fetches auction listings from Hetzner's Server Auction.

    Supports multiple potential endpoints to maximize compatibility:
    - Robot API: /order/server_market/product (authenticated)
    - Web scrape fallback: /sb/ page with embedded JSON
    """

    def __init__(
        self,
        base_url: str = "https://robot.hetzner.com",
        timeout: float = 30.0,
        user_agent: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.user_agent = user_agent or "hetzner-auction-pipeline/0.1.0"

    async def fetch(self) -> list[RawListing]:
        """
        Fetch current auction listings from Hetzner.

        Returns:
            List of RawListing objects (empty list if no listings, per EC-1)

        Raises:
            FetchError: If fetch fails or response is malformed (EC-2)
        """
        logger.info("Fetching auction data from Hetzner")

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
            ) as client:
                # Try multiple endpoints in order of preference
                endpoints = [
                    "/order/server_market/product",
                    "/wird/json.pl?json=get_server_market_v2",  # Legacy endpoint
                ]

                for endpoint in endpoints:
                    try:
                        data = await self._try_endpoint(client, endpoint)
                        return self._parse_response(data)
                    except FetchError as e:
                        logger.debug(f"Endpoint {endpoint} failed: {e}")
                        continue
                    except Exception as e:
                        logger.debug(f"Unexpected error from {endpoint}: {e}")
                        continue

                # If all endpoints fail, raise with last error
                raise FetchError("All Hetzner endpoints failed or returned malformed data")

        except httpx.HTTPStatusError as e:
            raise FetchError(
                f"HTTP error from Hetzner: {e.response.status_code}",
                status_code=e.response.status_code,
            ) from e
        except httpx.RequestError as e:
            raise FetchError(f"Network error fetching from Hetzner: {e}") from e

    async def _try_endpoint(self, client: httpx.AsyncClient, endpoint: str) -> dict[str, Any]:
        """Try a single endpoint and return JSON data."""
        url = f"{self.base_url}{endpoint}"
        logger.debug(f"Trying endpoint: {url}")

        response = await client.get(url)
        response.raise_for_status()

        try:
            data = response.json()
        except ValueError as e:
            # EC-2: Response isn't valid JSON
            raise FetchError(
                f"Invalid JSON response from {url}",
                raw_sample=response.text[:500],
            ) from e

        if not isinstance(data, dict):
            raise FetchError(
                f"Expected dict response, got {type(data).__name__}",
                raw_sample=str(data)[:500],
            )

        return data

    def _parse_response(self, data: dict[str, Any]) -> list[RawListing]:
        """
        Parse Hetzner's response into RawListing objects.

        Handles EC-1 (empty result) by returning empty list.
        Raises EC-2 (schema change) if response structure is unexpected.
        """
        fetched_at = datetime.now(UTC)

        # Handle empty response (EC-1)
        if not data:
            logger.info("Received empty auction data (no listings available)")
            return []

        # Try to detect response structure
        # Common patterns: {"server": [...]} or {"products": [...]} or直接的列表
        listings_data = None

        if "server" in data and isinstance(data["server"], list):
            listings_data = data["server"]
        elif "products" in data and isinstance(data["products"], list):
            listings_data = data["products"]
        elif "auction" in data and isinstance(data["auction"], list):
            listings_data = data["auction"]
        elif isinstance(data, list):
            listings_data = data

        if listings_data is None:
            # EC-2: Unrecognized schema
            raise FetchError(
                "Could not find listings in response - schema may have changed",
                raw_sample=str(data)[:1000],
            )

        if not listings_data:
            # EC-1: Empty listings array
            logger.info("Received empty listings array")
            return []

        # Parse each listing
        listings = []
        for item in listings_data:
            try:
                listing = self._parse_listing_item(item, fetched_at)
                listings.append(listing)
            except Exception as e:
                # Skip malformed individual listings but continue parsing others
                logger.warning(f"Skipping malformed listing: {e}")
                logger.debug(f"Malformed item data: {item}")
                continue

        logger.info(f"Parsed {len(listings)} auction listings")
        return listings

    def _parse_listing_item(self, item: dict[str, Any], fetched_at: datetime) -> RawListing:
        """Parse a single listing item from Hetzner's response."""
        # Extract listing ID
        listing_id = str(item.get("id") or item.get("product_id") or "")
        if not listing_id:
            raise ValueError("Missing listing_id")

        # Extract datacenter/location
        datacenter = item.get("datacenter") or item.get("dc") or ""
        location = item.get("location") or self._extract_location_from_dc(datacenter)

        # Extract availability
        available_from = item.get("available_from")  # May be None if immediately available

        # Extract CPU (raw - will be normalized later)
        cpu_raw = item.get("cpu") or item.get("cpu_model") or item.get("processor") or ""
        if not cpu_raw:
            raise ValueError(f"Missing cpu_raw for listing {listing_id}")

        # Extract RAM
        ram_gb = int(item.get("ram") or item.get("memory") or item.get("ram_gb") or 0)
        ram_ecc = bool(item.get("ram_ecc") or item.get("ecc") or False)

        # Extract disks
        disks = self._parse_disks(item)

        # Extract network
        uplink_speed = int(item.get("uplink") or item.get("bandwidth") or item.get("uplink_speed") or 1000)

        # Extract pricing (EUR cents)
        price_base = self._parse_price_cents(item.get("price") or item.get("price_monthly") or "0")
        price_setup_fee = self._parse_price_cents(item.get("setup_fee") or item.get("price_setup") or "0")

        return RawListing(
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

    def _parse_disks(self, item: dict[str, Any]) -> list[DiskSpec]:
        """Parse disk specifications from a listing."""
        disks = []

        # Multiple possible structures for disk data
        disk_data = item.get("disks") or item.get("storage") or item.get("drives")

        if isinstance(disk_data, list):
            for disk in disk_data:
                if isinstance(disk, dict):
                    disk_type = disk.get("type") or disk.get("disk_type") or ""
                    count = int(disk.get("count") or disk.get("qty") or 1)
                    size_gb = int(disk.get("size_gb") or disk.get("capacity_gb") or disk.get("size") or 0)

                    if disk_type and size_gb > 0:
                        disks.append(DiskSpec(type=disk_type, count=count, capacity_gb=size_gb))

        elif isinstance(disk_data, dict):
            # Single disk specification
            disk_type = disk_data.get("type") or disk_data.get("disk_type") or ""
            count = int(disk_data.get("count") or disk_data.get("qty") or 1)
            size_gb = int(disk_data.get("size_gb") or disk_data.get("capacity_gb") or disk_data.get("size") or 0)

            if disk_type and size_gb > 0:
                disks.append(DiskSpec(type=disk_type, count=count, capacity_gb=size_gb))

        # If no disks found, try legacy fields
        if not disks:
            hdd_count = int(item.get("hdd_count") or 0)
            hdd_size = int(item.get("hdd_size_gb") or 0)
            if hdd_count > 0 and hdd_size > 0:
                disks.append(DiskSpec(type="HDD", count=hdd_count, capacity_gb=hdd_size))

        return disks

    def _parse_price_cents(self, price_value: Any) -> int:
        """Convert price value to integer EUR cents."""
        if isinstance(price_value, (int, float)):
            # Assume already in EUR if numeric, convert to cents
            return int(price_value * 100)

        if isinstance(price_value, str):
            # Parse string like "€19.99" or "19.99" or "1999" (cents)
            cleaned = price_value.replace("€", "").replace("EUR", "").replace(",", ".").strip()

            if "." in cleaned:
                # Decimal EUR value
                eur = float(cleaned)
                return int(eur * 100)
            else:
                # Assume already in cents
                return int(cleaned)

        return 0

    def _extract_location_from_dc(self, datacenter: str) -> str:
        """Extract location code from datacenter string."""
        if not datacenter:
            return "UNK"

        # Common datacenter patterns: FSN1-DC3, NBG1-DC1, etc.
        parts = datacenter.split("-")
        if parts:
            return parts[0][:3].upper()  # First 3 chars, uppercased

        return datacenter[:3].upper()


async def main():
    """Test the fetcher."""
    logging.basicConfig(level=logging.INFO)

    fetcher = HetznerAuctionFetcher()

    try:
        listings = await fetcher.fetch()
        print(f"Successfully fetched {len(listings)} listings")

        for listing in listings[:3]:  # Show first 3
            print(f"\n{listing.listing_id}: {listing.cpu_raw}, {listing.ram_gb}GB RAM, €{listing.price_base / 100:.2f}")

    except FetchError as e:
        print(f"Fetch failed: {e}")
        if e.raw_sample:
            print(f"Sample data: {e.raw_sample}")


if __name__ == "__main__":
    asyncio.run(main())