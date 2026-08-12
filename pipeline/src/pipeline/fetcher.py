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
    - price_base, price_ipv4_monthly, price_setup_fee (EUR cents)
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
    price_ipv4_monthly: int = 0  # EUR cents for the primary IPv4 address


class HetznerAuctionFetcher:
    """
    Fetches auction listings from Hetzner's Server Auction.

    Uses the public live data endpoint (no authentication required).
    """

    def __init__(
        self,
        timeout: float = 30.0,
        user_agent: str | None = None,
    ):
        self.live_data_url = "https://www.hetzner.com/_resources/app/data/app/live_data_sb.json"
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
                data = await self._try_endpoint(client)
                return self._parse_response(data)

        except httpx.HTTPStatusError as e:
            raise FetchError(
                f"HTTP error from Hetzner: {e.response.status_code}",
                status_code=e.response.status_code,
            ) from e
        except httpx.RequestError as e:
            raise FetchError(f"Network error fetching from Hetzner: {e}") from e

    async def _try_endpoint(self, client: httpx.AsyncClient) -> dict[str, Any]:
        """Fetch and return JSON data from the live data endpoint."""
        url = self.live_data_url
        logger.debug(f"Fetching from: {url}")

        try:
            response = await client.get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            # Convert HTTP errors to FetchError with status code
            raise FetchError(
                f"HTTP error from {url}: {e.response.status_code}",
                status_code=e.response.status_code,
            ) from e
        except httpx.RequestError as e:
            # Convert network errors to FetchError
            raise FetchError(f"Network error fetching from {url}: {e}") from e

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
        """Parse a single listing item from Hetzner's live data response.

        The live data schema has nested Hardware/Prices/Details structure.
        See docs/notes/hetzner-live-feed-schema-2026-08-06.md for field mapping.
        """
        # Extract listing ID (note: "Id" not "id" in the live data feed)
        listing_id = str(item.get("Id") or "")
        if not listing_id:
            raise ValueError("Missing listing_id (Id field)")

        # Extract CPU (raw - will be normalized later)
        hardware = item.get("Hardware", {})
        cpu_section = hardware.get("CPU", {})
        cpu_raw = cpu_section.get("Name", "")
        if not cpu_raw:
            raise ValueError(f"Missing cpu_raw for listing {listing_id}")

        # Extract RAM
        ram_section = hardware.get("RAM", {})
        ram_gb = int(ram_section.get("Size", 0))
        ram_ecc = bool(ram_section.get("ecc", False))

        # Extract datacenter
        details = item.get("Details", {})
        datacenter_section = details.get("Datacenter", {})
        datacenter = datacenter_section.get("Name", "")

        # Extract location from datacenter (unchanged helper)
        location = self._extract_location_from_dc(datacenter)

        # Available from is not present in this feed
        available_from = None

        # Extract uplink speed
        uplink_speed = int(details.get("Bandwidth", 1000))

        # Extract pricing (EUR cents)
        prices = item.get("Prices", {})
        monthly_section = prices.get("monthly", {})
        setup_section = prices.get("setup", {})
        ip_prices = item.get("IPPrices", {})

        price_base = self._parse_price_cents(monthly_section.get("EUR", 0))
        price_ipv4_monthly = self._parse_price_cents(
            ip_prices.get("monthly", {}).get("EUR", 0)
        )
        price_setup_fee = self._parse_price_cents(setup_section.get("EUR", 0))

        # Extract disks (separate bead - leave current implementation alone)
        disks = self._parse_disks(item)

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
            price_ipv4_monthly=price_ipv4_monthly,
            price_setup_fee=price_setup_fee,
            fetched_at=fetched_at,
        )

    def _parse_disks(self, item: dict[str, Any]) -> list[DiskSpec]:
        """Parse disk specifications from a listing.

        Real schema: item['Hardware']['Storage']['Details'] is a dict with keys
        hdd, sata, nvme (each a flat list of per-disk capacity-in-GB integers).
        See docs/notes/hetzner-live-feed-schema-2026-08-06.md.
        """
        disks = []

        # Extract the Details dictionary from Hardware.Storage
        hardware = item.get("Hardware", {})
        storage = hardware.get("Storage", {})
        details = storage.get("Details", {})

        if not isinstance(details, dict):
            return disks

        # Process each disk type (skip 'general' - it's a redundant union)
        for feed_key, disk_type in [
            ("nvme", "NVMe"),
            ("sata", "SSD"),
            ("hdd", "HDD"),
        ]:
            capacity_list = details.get(feed_key, [])

            if not isinstance(capacity_list, list):
                continue

            # Group identical capacities: count how many disks of each capacity
            capacity_counts: dict[int, int] = {}
            for capacity in capacity_list:
                if isinstance(capacity, (int, float)) and capacity > 0:
                    capacity_gb = int(capacity)
                    capacity_counts[capacity_gb] = capacity_counts.get(capacity_gb, 0) + 1

            # Emit one DiskSpec per distinct capacity for this disk type
            for capacity_gb, count in capacity_counts.items():
                disks.append(DiskSpec(type=disk_type, count=count, capacity_gb=capacity_gb))

        return disks

    def _parse_price_cents(self, price_value: Any) -> int:
        """Convert price value to integer EUR cents."""
        if isinstance(price_value, (int, float)):
            # Assume already in EUR if numeric, convert to cents
            # Use round to avoid floating point precision issues
            return round(price_value * 100)

        if isinstance(price_value, str):
            # Parse string like "€19.99" or "19.99" or "1999" (cents)
            cleaned = price_value.replace("€", "").replace("EUR", "").replace(",", ".").strip()

            if "." in cleaned:
                # Decimal EUR value
                eur = float(cleaned)
                return round(eur * 100)
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
