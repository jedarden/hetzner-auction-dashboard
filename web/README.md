# Hetzner Auction Dashboard - Starter Configs

This document describes the starter configs implementation for the Hetzner Auction Dashboard.

## Overview

Starter configs are pre-built filter presets that allow first-time visitors to quickly find relevant server listings without manually configuring filters. Each preset defines a specific use case with optimized filter criteria and sorting.

## Current Presets

### 1. Budget Web Server 🌐
- **Use case**: Affordable web hosting and light workloads
- **Filters**: Max €30/month, 16GB+ RAM, benchmark-matched only
- **Sort**: Value (multi-thread benchmark)

### 2. Home NAS 💾
- **Use case**: File server, backup, or media storage
- **Filters**: Max €50/month, 8GB+ RAM, 4TB+ HDD storage
- **Sort**: Value per TB disk

### 3. Game Server 🎮
- **Use case**: High single-thread performance for game hosting
- **Filters**: Max €60/month, 32GB+ RAM, Ryzen CPU, NVMe storage
- **Sort**: Value (single-thread benchmark)

### 4. Compute Intensive ⚡
- **Use case**: Rendering, scientific computing, batch processing
- **Filters**: Max €80/month, 64GB+ RAM, benchmark-matched only
- **Sort**: Value (multi-thread benchmark)

### 5. Storage Focused 📦
- **Use case**: Bulk storage, backups, archives
- **Filters**: Max €40/month, 16GB+ RAM, 2TB+ HDD storage
- **Sort**: Value per TB disk

## File Structure

```
web/
├── index.html              # Main dashboard with starter config UI
├── starter-configs.json    # Preset definitions (loaded in production)
└── README.md              # This documentation
```

## Adding New Presets

To add a new preset:

1. **Edit `starter-configs.json`** (or the `starterConfigs` object in `index.html` for development):

```json
{
  "id": "your-new-preset",
  "name": "Your New Preset",
  "description": "Brief description of the use case",
  "icon": "🎯",
  "filters": {
    "price_effective_monthly_max": 100,
    "ram_gb_min": 32,
    "cpu_family_preference": "any",
    "disk_type": "any",
    "benchmark_matched_only": true
  },
  "sort": "price_per_benchmark_point_multi",
  "sort_direction": "asc"
}
```

2. **Filter Options**:
   - `price_effective_monthly_max`: Maximum monthly price (€)
   - `ram_gb_min`: Minimum RAM (GB)
   - `cpu_family_preference`: "any", "ryzen", "xeon", or "core"
   - `disk_type`: "any", "nvme", "ssd", or "hdd"
   - `disk_size_tb_min`: Minimum total disk size (TB)
   - `benchmark_matched_only`: true/false

3. **Sort Options**:
   - `price_per_benchmark_point_multi`: Value per multi-thread benchmark
   - `price_per_benchmark_point_single`: Value per single-thread benchmark
   - `price_per_gb_ram`: Value per GB RAM
   - `price_per_tb_disk`: Value per TB disk

4. **Sort Direction**: "asc" (ascending) or "desc" (descending)

## Integration with DuckDB-WASM

The current implementation uses mock data for demonstration. In production:

1. The `starter-configs.json` file will be loaded via `fetch()`
2. Listings data will come from DuckDB-WASM querying the Parquet file
3. Filter logic will be converted to SQL WHERE clauses
4. Sort logic will be converted to SQL ORDER BY clauses

Example SQL integration:

```sql
SELECT * FROM auction_listings
WHERE price_effective_monthly <= {max_price}
  AND ram_gb >= {min_ram}
  AND benchmark_matched = {matched_only}
ORDER BY {sort_column} {sort_direction}
LIMIT 100
```

## User Experience

1. **One-click application**: Users click a preset card to apply all filters and sorting instantly
2. **Visual feedback**: Active preset is highlighted with a blue border
3. **Manual override**: Users can manually adjust filters after selecting a preset
4. **Clear indication**: Preset selection is cleared when filters are manually modified

## Future Enhancements

Potential improvements for v1.1+:

- **Custom presets**: Allow users to save their own filter combinations
- **URL encoding**: Bookmarkable preset links (see bead `had-2ua`)
- **A/B testing**: Track which presets are most used
- **Dynamic recommendations**: Suggest presets based on user behavior
- **Preset categories**: Group presets by workload type

## Bead Tracking

This implementation corresponds to bead `had-4ct` from the 2026-08-02 idea-gen run.

See `docs/notes/ideas-ledger.md` for the full context of how this feature was prioritized and selected.
