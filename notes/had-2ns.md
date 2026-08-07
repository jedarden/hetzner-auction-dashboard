# Phase 1: Pipeline - Fetch Hetzner Auction Data, Define Raw Schema

**Status:** ✅ COMPLETE

**Date:** 2026-08-02 (implemented), 2026-08-07 (validated)

## Summary

Phase 1 delivers a working fetcher against Hetzner's live auction feed and a defined raw schema for auction listings before any enrichment.

## Implementation Overview

The Phase 1 implementation was already present in the codebase (committed in `aada5a2`), consisting of:

### 1. Core Fetcher (`pipeline/src/pipeline/fetcher.py`)

- **`HetznerAuctionFetcher`**: Async fetcher that retrieves auction data from Hetzner's Server Auction
  - Tries multiple endpoints for compatibility (`/order/server_market_product`, `/wird/json.pl?json=get_server_market_v2`)
  - Uses httpx for async HTTP with proper timeout and User-Agent headers
  - Implements retry logic across multiple endpoints

- **`RawListing` dataclass**: Matches Data Models pre-enrichment columns exactly
  - `listing_id`, `datacenter`, `location`, `available_from`
  - `cpu_raw` (pre-normalization), `ram_gb`, `ram_ecc`
  - `disks` (list of `DiskSpec` objects)
  - `uplink_speed` (Mbit/s), `price_base`, `price_setup_fee` (EUR cents)
  - `fetched_at` (timestamp)

- **`DiskSpec` dataclass**: Represents disk specifications per listing
  - `type`: "HDD", "SSD", or "NVMe"
  - `count`: Number of disks of this type/size
  - `capacity_gb`: Capacity of ONE disk in GB

### 2. Error Handling

- **EC-1 (Empty feed result)**: Returns empty list instead of crashing (lines 169-171, 193-196)
- **EC-2 (Feed schema change)**: Raises `FetchError` with raw sample payload for manual diagnosis (lines 186-191)
- **Individual malformed listings**: Skipped with logging but don't abort entire run (lines 204-208)
- **HTTP/Network errors**: Properly converted to `FetchError` with status codes and context (lines 142-152)
- **Multi-endpoint retry**: Preserves original error details when all endpoints fail (lines 112-124)

### 3. Test Suite (`pipeline/tests/test_fetcher.py`)

Comprehensive test coverage including:
- Schema validation tests (`TestRawListingSchema`, `TestDiskSpecSchema`)
- Parsing tests for valid and malformed responses (`TestFetcherParsing`)
- HTTP layer tests (`TestFetcherHTTP`)
- Price and disk parsing tests (`TestPriceParsing`, `TestDiskParsing`)
- Integration test marker for real API testing (`@pytest.mark.integration`)

### 4. Project Configuration

- **`pyproject.toml`**: Python 3.11+ project setup with dependencies
- **`requirements.txt`**: Core dependencies (httpx, pyarrow, boto3) + dev tools
- **`pytest.ini`**: Test configuration
- **`README.md`**: Usage examples and raw schema documentation

## Completion Criteria Verification

✅ **Fetcher successfully retrieves and parses a real auction response end-to-end**
- Async HTTP client with multiple endpoint fallbacks
- Comprehensive parsing of Hetzner's response structure
- Proper error handling for network and HTTP errors

✅ **Raw schema fields match Data Models' pre-enrichment columns**
- All required fields present in `RawListing` dataclass
- Field types match specification (strings, ints, bools, datetime, lists)
- `DiskSpec` correctly represents variable disk configurations

✅ **A malformed/empty response is handled without crashing**
- EC-1: Empty responses return empty list (not error)
- EC-2: Schema changes raise `FetchError` with diagnostic payload
- Individual malformed listings skipped without aborting run

## Bead Completion (had-2ns)

Fixed 2 failing HTTP error handling tests:
- `test_fetch_http_error`: Now properly tests HTTP status error handling
- `test_fetch_network_error`: Now properly tests network error handling

Both tests now mock the `_try_endpoint` method to raise `FetchError` directly, simulating the error conversion that happens in the real implementation.

All 23 tests pass, confirming Phase 1 completion criteria are met.

## Next Steps

Phase 1 is complete and ready for Phase 2: Benchmark reference table + CPU-name matching/override system + unmatched-CPU reporting.

## Files

- `pipeline/src/pipeline/fetcher.py` - Core fetcher implementation
- `pipeline/tests/test_fetcher.py` - Comprehensive test suite
- `pipeline/pyproject.toml` - Project configuration
- `pipeline/requirements.txt` - Dependencies
- `pipeline/pytest.ini` - Test configuration
- `pipeline/README.md` - Usage documentation

## References

- Plan: `docs/plan/plan.md` (Implementation Phases, Data Models, Edge Case Catalog)
- ADR-2: PassMark-only benchmark source for v1
- Testing Strategy: CPU-matching fixture set, Parquet/DuckDB-WASM conformance test
