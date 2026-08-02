# Hetzner Auction Pipeline

Fetches, enriches, and publishes Hetzner auction data.

## Phase 1: Fetcher + Raw Schema

This implements the first phase of the pipeline:
- Fetches auction data from Hetzner's Server Auction
- Defines raw schema matching Data Models specification
- Handles edge cases EC-1 (empty feed) and EC-2 (schema changes)

## Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Install in development mode
pip install -e .
```

## Usage

### Running the Fetcher

```python
import asyncio
from pipeline.fetcher import HetznerAuctionFetcher

async def main():
    fetcher = HetznerAuctionFetcher()
    listings = await fetcher.fetch()

    print(f"Fetched {len(listings)} listings")
    for listing in listings:
        print(f"{listing.listing_id}: {listing.cpu_raw}, {listing.ram_gb}GB RAM")

asyncio.run(main())
```

### Testing

```bash
# Run unit tests
pytest tests/test_fetcher.py

# Run integration tests (requires real Hetzner API access)
pytest tests/test_fetcher.py -m integration
```

## Raw Schema

The `RawListing` dataclass matches the Data Models pre-enrichment columns:

- `listing_id`: Unique identifier for the listing
- `datacenter`: Datacenter code (e.g., "FSN1-DC3")
- `location`: Location code (e.g., "FSN")
- `available_from`: ISO datetime when available (None if immediately)
- `cpu_raw`: Raw CPU description (to be normalized in Phase 2)
- `ram_gb`: RAM in GB
- `ram_ecc`: Whether RAM is ECC
- `disks`: List of `DiskSpec` objects (type, count, capacity_gb)
- `uplink_speed`: Network uplink in Mbit/s
- `price_base`: Base monthly price in EUR cents
- `price_setup_fee`: Setup fee in EUR cents
- `fetched_at`: Timestamp when data was fetched

## Error Handling

The fetcher implements two key edge cases from the plan:

### EC-1: Empty Feed Result
Returns an empty list (not an error) when the auction has no listings.

### EC-2: Feed Schema Change
Raises `FetchError` with:
- Status code (if HTTP error)
- Sample of raw payload (for manual fix diagnosis)

## Next Phases

- **Phase 2**: CPU benchmark reference table + matching/override system
- **Phase 3**: Cost-metric computation + Parquet writer
- **Phase 4**: R2 bucket + API token + refresh-loop Deployment
- **Phase 5**: Client dashboard with DuckDB-WASM
