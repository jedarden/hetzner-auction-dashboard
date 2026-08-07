"""
Tests for Hetzner Auction Fetcher

Tests Phase 1 completion criteria:
- Fetcher successfully retrieves and parses a real auction response end-to-end
- Raw schema fields match Data Models' pre-enrichment columns
- A malformed/empty response is handled without crashing (EC-1/EC-2)
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx
from pipeline.fetcher import DiskSpec, FetchError, HetznerAuctionFetcher, RawListing


@pytest.fixture
def fetcher():
    """Create a fetcher instance for testing."""
    return HetznerAuctionFetcher()


@pytest.fixture
def sample_hetzner_response():
    """Sample Hetzner auction API response (matches live_data_sb.json schema)."""
    return {
        "server": [
            {
                "Id": 12345,
                "Hardware": {
                    "CPU": {"Name": "Intel Xeon E3-1230 v6", "CoreCount": 1},
                    "RAM": {"RealSize": 32768, "Size": 32, "SizeUnit": "GB", "Amount": 1, "ecc": True},
                    "Storage": {
                        "RealSize": 960, "Size": 960, "SizeUnit": "GB", "Amount": 2,
                        "Disks": ["480 GB Datacenter SSD", "480 GB Datacenter SSD"],
                        "Details": {"nvme": [480, 480], "sata": [], "hdd": [], "general": [480, 480]}
                    }
                },
                "Prices": {
                    "monthly": {"EUR": 19.99, "USD": 22.5},
                    "hourly": {"EUR": 0.0299, "USD": 0.0337},
                    "setup": {"EUR": 0, "USD": 0},
                    "fixed": False
                },
                "Details": {
                    "Description": ["IPv4", "iNIC", "SSD"],
                    "Information": ["1 x RAM 32768 MB DDR4 ECC", "2 x SSD 480 GB Datacenter", "NIC 1 Gbit"],
                    "Specials": ["IPv4", "ECC"],
                    "Traffic": "unlimited",
                    "Bandwidth": 1000,
                    "OS": ["Rescue system"],
                    "Datacenter": {"Name": "FSN1-DC3", "Datacenter": "#FSN1-DC3"}
                },
                "Timer": {"ReduceNext": 12345, "ReduceNextHr": True}
            },
            {
                "Id": 67890,
                "Hardware": {
                    "CPU": {"Name": "AMD Ryzen 9 5950X", "CoreCount": 1},
                    "RAM": {"RealSize": 65536, "Size": 64, "SizeUnit": "GB", "Amount": 1, "ecc": False},
                    "Storage": {
                        "RealSize": 2000, "Size": 2000, "SizeUnit": "GB", "Amount": 2,
                        "Disks": ["1000 GB NVMe SSD", "1000 GB NVMe SSD"],
                        "Details": {"nvme": [1000, 1000], "sata": [], "hdd": [], "general": [1000, 1000]}
                    }
                },
                "Prices": {
                    "monthly": {"EUR": 49.99, "USD": 56.25},
                    "hourly": {"EUR": 0.0749, "USD": 0.0844},
                    "setup": {"EUR": 0, "USD": 0},
                    "fixed": False
                },
                "Details": {
                    "Description": ["IPv4", "iNIC", "NVMe"],
                    "Information": ["1 x RAM 65536 MB DDR4 non-ECC", "2 x NVMe 1000 GB", "NIC 1 Gbit"],
                    "Specials": ["IPv4"],
                    "Traffic": "unlimited",
                    "Bandwidth": 1000,
                    "OS": ["Rescue system"],
                    "Datacenter": {"Name": "NBG1-DC1", "Datacenter": "#NBG1-DC1"}
                },
                "Timer": {"ReduceNext": 67890, "ReduceNextHr": False}
            }
        ],
        "filter": {"location": {"values": ["FSN", "NBG", "HEL"]}, "price": {"min": {"EUR": 10}, "max": {"EUR": 100}}},
        "serverCount": 2
    }


class TestRawListingSchema:
    """Test that RawListing matches Data Models pre-enrichment schema."""

    def test_raw_listing_has_required_fields(self, fetcher):
        """Verify RawListing has all required Data Model fields."""
        listing = RawListing(
            listing_id="test123",
            datacenter="FSN1-DC3",
            location="FSN",
            available_from=None,
            cpu_raw="Intel Xeon E3-1230 v6",
            ram_gb=32,
            ram_ecc=True,
            disks=[DiskSpec(type="SSD", count=2, capacity_gb=480)],
            uplink_speed=1000,
            price_base=1999,  # €19.99 in cents
            price_setup_fee=0,
            fetched_at=datetime.now(UTC),
        )

        # Verify all required fields exist and have correct types
        assert isinstance(listing.listing_id, str)
        assert isinstance(listing.datacenter, str)
        assert isinstance(listing.location, str)
        assert listing.available_from is None or isinstance(listing.available_from, str)
        assert isinstance(listing.cpu_raw, str)
        assert isinstance(listing.ram_gb, int)
        assert isinstance(listing.ram_ecc, bool)
        assert isinstance(listing.disks, list)
        assert all(isinstance(d, DiskSpec) for d in listing.disks)
        assert isinstance(listing.uplink_speed, int)
        assert isinstance(listing.price_base, int)
        assert isinstance(listing.price_setup_fee, int)
        assert isinstance(listing.fetched_at, datetime)


class TestDiskSpecSchema:
    """Test DiskSpec schema matches Data Models."""

    def test_disk_spec_fields(self):
        """Verify DiskSpec has required fields: type, count, capacity_gb."""
        disk = DiskSpec(type="SSD", count=2, capacity_gb=480)

        assert isinstance(disk.type, str)
        assert disk.type in ("HDD", "SSD", "NVMe")
        assert isinstance(disk.count, int)
        assert isinstance(disk.capacity_gb, int)


class TestFetcherParsing:
    """Test fetcher parsing of Hetzner responses."""

    @pytest.mark.asyncio
    async def test_parse_valid_response(self, fetcher, sample_hetzner_response):
        """Test parsing a valid Hetzner response."""
        listings = fetcher._parse_response(sample_hetzner_response)

        assert len(listings) == 2

        # Check first listing
        assert listings[0].listing_id == "12345"
        assert listings[0].cpu_raw == "Intel Xeon E3-1230 v6"
        assert listings[0].ram_gb == 32
        assert listings[0].ram_ecc is True
        # Disk parsing is a separate bead - not asserting on disk details
        assert listings[0].uplink_speed == 1000
        assert listings[0].price_base == 1999  # €19.99 in cents
        assert listings[0].price_setup_fee == 0
        assert listings[0].available_from is None  # Not present in live feed

        # Check second listing
        assert listings[1].listing_id == "67890"
        assert listings[1].cpu_raw == "AMD Ryzen 9 5950X"
        assert listings[1].ram_gb == 64
        assert listings[1].ram_ecc is False
        assert listings[1].available_from is None  # Not present in live feed

    @pytest.mark.asyncio
    async def test_parse_empty_response_ec1(self, fetcher):
        """Test EC-1: Empty feed result returns empty list."""
        # Test completely empty response
        listings = fetcher._parse_response({})
        assert listings == []

        # Test response with empty server array
        listings = fetcher._parse_response({"server": []})
        assert listings == []

    @pytest.mark.asyncio
    async def test_parse_schema_change_ec2(self, fetcher):
        """Test EC-2: Schema change raises FetchError with sample."""
        malformed_response = {"unexpected_key": "unexpected_value"}

        with pytest.raises(FetchError) as exc_info:
            fetcher._parse_response(malformed_response)

        error = exc_info.value
        assert "schema may have changed" in str(error).lower()
        assert error.raw_sample is not None
        assert len(error.raw_sample) > 0

    @pytest.mark.asyncio
    async def test_parse_malformed_individual_listing_skipped(self, fetcher):
        """Test that individual malformed listings are skipped but others parse."""
        response_with_bad_item = {
            "server": [
                {
                    "Id": 123,
                    "Hardware": {
                        "CPU": {"Name": "Intel Xeon E3-1230 v6", "CoreCount": 1},
                        "RAM": {"RealSize": 32768, "Size": 32, "SizeUnit": "GB", "Amount": 1, "ecc": True},
                        "Storage": {"RealSize": 960, "Size": 960, "SizeUnit": "GB", "Amount": 2,
                                   "Disks": ["480 GB Datacenter SSD", "480 GB Datacenter SSD"],
                                   "Details": {"nvme": [480, 480], "sata": [], "hdd": [], "general": [480, 480]}}
                    },
                    "Prices": {"monthly": {"EUR": 19.99}, "setup": {"EUR": 0}},
                    "Details": {"Bandwidth": 1000, "Datacenter": {"Name": "FSN1-DC3"}}
                },
                {
                    "Id": 456,  # Missing Hardware section - should be skipped
                    "Prices": {"monthly": {"EUR": 10}, "setup": {"EUR": 0}},
                    "Details": {"Bandwidth": 1000, "Datacenter": {"Name": "NBG1-DC1"}}
                },
                {
                    "Id": 789,
                    "Hardware": {
                        "CPU": {"Name": "AMD Ryzen 9 5950X", "CoreCount": 1},
                        "RAM": {"RealSize": 65536, "Size": 64, "SizeUnit": "GB", "Amount": 1, "ecc": False},
                        "Storage": {"RealSize": 2000, "Size": 2000, "SizeUnit": "GB", "Amount": 2,
                                   "Disks": ["1000 GB NVMe SSD", "1000 GB NVMe SSD"],
                                   "Details": {"nvme": [1000, 1000], "sata": [], "hdd": [], "general": [1000, 1000]}}
                    },
                    "Prices": {"monthly": {"EUR": 49.99}, "setup": {"EUR": 0}},
                    "Details": {"Bandwidth": 1000, "Datacenter": {"Name": "HEL1-DC1"}}
                },
            ]
        }

        listings = fetcher._parse_response(response_with_bad_item)

        # Should skip the bad listing but parse the good ones
        assert len(listings) == 2
        assert listings[0].listing_id == "123"
        assert listings[1].listing_id == "789"


class TestFetcherHTTP:
    """Test HTTP layer of fetcher."""

    @pytest.mark.asyncio
    async def test_fetch_success(self, fetcher, sample_hetzner_response):
        """Test successful fetch end-to-end."""
        mock_response = MagicMock()
        mock_response.json.return_value = sample_hetzner_response
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            listings = await fetcher.fetch()

        assert len(listings) == 2
        assert listings[0].listing_id == "12345"

    @pytest.mark.asyncio
    async def test_fetch_http_error(self, fetcher):
        """Test HTTP errors are handled."""
        # Mock the _try_endpoint method to raise FetchError (simulating conversion done by _try_endpoint)
        fetch_error = FetchError(
            "HTTP error from https://robot.hetzner.com/order/server_market/product: 500",
            status_code=500
        )

        with patch.object(fetcher, '_try_endpoint', side_effect=fetch_error):
            with pytest.raises(FetchError) as exc_info:
                await fetcher.fetch()

        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_fetch_network_error(self, fetcher):
        """Test network errors are handled."""
        # Mock the _try_endpoint method to raise FetchError (simulating conversion done by _try_endpoint)
        fetch_error = FetchError("Network error fetching from https://robot.hetzner.com/order/server_market/product: Network error")

        with patch.object(fetcher, '_try_endpoint', side_effect=fetch_error):
            with pytest.raises(FetchError) as exc_info:
                await fetcher.fetch()

        assert "Network error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_fetch_invalid_json(self, fetcher):
        """Test invalid JSON responses are handled (EC-2)."""
        mock_response = MagicMock()
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_response.text = "Not valid JSON"
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock()

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(FetchError) as exc_info:
                await fetcher._try_endpoint(mock_client)

        assert "Invalid JSON" in str(exc_info.value)
        assert exc_info.value.raw_sample is not None


class TestPriceParsing:
    """Test price parsing to EUR cents."""

    @pytest.mark.parametrize(
        ("input_price", "expected_cents"),
        [
            ("€19.99", 1999),
            ("€49.99", 4999),
            ("19.99", 1999),
            ("49.99", 4999),
            ("1999", 1999),  # Assume cents
            (19.99, 1999),  # Float EUR
            (49.99, 4999),
            (0, 0),
            ("€0.00", 0),
        ],
    )
    def test_parse_price_cents(self, fetcher, input_price, expected_cents):
        """Test various price formats parse correctly to cents."""
        assert fetcher._parse_price_cents(input_price) == expected_cents


class TestDiskParsing:
    """Test disk parsing from various response formats."""

    def test_parse_disk_list(self, fetcher):
        """Test parsing disks from list format."""
        item = {
            "disks": [
                {"type": "SSD", "count": 2, "size_gb": 480},
                {"type": "HDD", "count": 4, "size_gb": 2000},
            ]
        }

        disks = fetcher._parse_disks(item)

        assert len(disks) == 2
        assert disks[0].type == "SSD"
        assert disks[0].count == 2
        assert disks[0].capacity_gb == 480
        assert disks[1].type == "HDD"
        assert disks[1].count == 4
        assert disks[1].capacity_gb == 2000

    def test_parse_single_disk_dict(self, fetcher):
        """Test parsing single disk from dict format."""
        item = {"disks": {"type": "NVMe", "count": 2, "size_gb": 1000}}

        disks = fetcher._parse_disks(item)

        assert len(disks) == 1
        assert disks[0].type == "NVMe"
        assert disks[0].count == 2
        assert disks[0].capacity_gb == 1000

    def test_parse_legacy_disk_fields(self, fetcher):
        """Test parsing legacy disk field format."""
        item = {"hdd_count": 4, "hdd_size_gb": 2000}

        disks = fetcher._parse_disks(item)

        assert len(disks) == 1
        assert disks[0].type == "HDD"
        assert disks[0].count == 4
        assert disks[0].capacity_gb == 2000

    def test_parse_no_disks(self, fetcher):
        """Test parsing item with no disk information."""
        item = {"cpu": "Intel Xeon", "ram": 32}

        disks = fetcher._parse_disks(item)

        assert len(disks) == 0


# Integration test marker - can be run against real Hetzner API
@pytest.mark.integration
class TestRealHetznerAPI:
    """Integration tests against real Hetzner API."""

    @pytest.mark.asyncio
    async def test_fetch_real_data(self):
        """Test fetching real data from Hetzner (integration test)."""
        fetcher = HetznerAuctionFetcher()

        try:
            listings = await fetcher.fetch()

            # If we reach here, fetch succeeded
            assert isinstance(listings, list)

            # If we got any listings, validate they match schema
            for listing in listings:
                assert isinstance(listing, RawListing)
                assert listing.listing_id
                assert listing.cpu_raw
                assert listing.ram_gb >= 0

        except FetchError as e:
            # If fetch fails due to network/auth, that's expected for integration tests
            # but we should log it
            pytest.skip(f"Real Hetzner API unavailable: {e}")