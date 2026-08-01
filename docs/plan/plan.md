# hetzner-auction-dashboard Plan

## Overview

A fully static, client-side dashboard for browsing Hetzner's dedicated server auction. A separate scheduled pipeline fetches auction listings, enriches each one with a CPU benchmark score and derived cost-efficiency metrics, and publishes the result as a single flat Parquet file. The browser loads that Parquet file directly into DuckDB-WASM and does all search/filtering/sorting locally via SQL — there is no client-side join and no per-request backend API.

## Architecture

Two independent halves connected only by a Parquet file:

### 1. Pipeline (server-side, scheduled)

- Fetches current listings from Hetzner's public Server Auction data feed.
- Normalizes each listing's free-text CPU name and matches it against a maintained CPU benchmark reference table (see Data Models). Matching is fuzzy/alias-based with a manual-override list for CPUs that don't match cleanly — this is the part expected to need ongoing curation, not the dashboard code itself.
- Computes derived cost fields for every listing: price per benchmark point, price per GB RAM, price per TB disk, effective total monthly cost.
- Writes ONE denormalized Parquet file — no relational structure, every column a query might filter/sort on is already present.
- Publishes the Parquet file to static storage that supports CORS + HTTP range requests (required for DuckDB-WASM to do partial reads instead of downloading the whole file on every page load).
- Runs as a long-lived Deployment with an internal refresh loop (house rule: no Job/CronJob) on a Rackspace Spot cluster, wired through GitOps (`jedarden/declarative-config`, `k8s/` path) — never a live kubectl mutation.

### 2. Client (fully static, browser-only)

- Static site bundling DuckDB-WASM.
- Loads the Parquet file over HTTP via DuckDB-WASM's httpfs, using range requests so only the needed row groups are fetched.
- All search/filter/sort UI translates directly to SQL `WHERE`/`ORDER BY` against the single pre-joined table — no joins, no benchmark lookup, at query time.
- No backend calls at request time. The only "dynamic" part of the deployed site is that the Parquet file itself changes on the pipeline's refresh cadence.

## Components

- `pipeline/` — fetcher + CPU benchmark join + cost-metric computation + Parquet writer; containerized; runs the refresh loop.
- `benchmark-map/` — maintained CPU-name → benchmark-score reference table + alias/override list. Highest-maintenance artifact in the repo; see `docs/notes/`.
- `web/` — static frontend (DuckDB-WASM + filter/search UI).
- Parquet output — published to object storage with CORS + range-request support (candidate hosts TBD, see Open Questions).

## Data Models

Single flat table, one row per auction listing:

- `listing_id`, `datacenter`, `location`, `available_from`
- `cpu_raw`, `cpu_normalized`, `cpu_benchmark_single`, `cpu_benchmark_multi`
- `ram_gb`, `ram_ecc`
- `disks` (type: HDD/SSD/NVMe, count, capacity per disk)
- `uplink_speed`
- `price_base`, `price_setup_fee`, `price_effective_monthly`
- derived: `price_per_benchmark_point`, `price_per_gb_ram`, `price_per_tb_disk`
- `fetched_at` (staleness display in the UI)

## Implementation Phases

- [ ] Phase 1: Pipeline — fetch Hetzner auction data, define raw schema
- [ ] Phase 2: Benchmark reference table + CPU-name matching/override system
- [ ] Phase 3: Cost-metric computation + Parquet writer
- [ ] Phase 4: Static hosting for the Parquet file (CORS + range requests) + refresh-loop Deployment via declarative-config
- [ ] Phase 5: Client dashboard — DuckDB-WASM wiring + search/filter UI
- [ ] Phase 6: Deploy pipeline to a Rackspace Spot cluster via GitOps; wire static site hosting

## Open Questions

- Which benchmark source to standardize on (PassMark single/multi-thread is the common default) and how to source/refresh it sustainably.
- Where to host the Parquet file: cluster-local S3-compatible bucket (Garage/SeaweedFS — note SeaweedFS has had stability issues on ardenone-cluster) vs. the existing B2/Cloudflare path (ARMOR) vs. Cloudflare R2 directly, matching the jedarden.com hosting pattern.
- Refresh cadence for auction data — Hetzner's listings turn over frequently intraday.
- Frontend framework choice (plain JS + DuckDB-WASM vs. a light framework) — low-stakes given how thin the UI is.
- Whether to track price history over time (would require appending rather than overwriting the Parquet file) — out of scope for v1, revisit later.
- Which Rackspace Spot cluster hosts the pipeline — any is viable since the dataset regenerates on its own cadence and nothing is stateful; final choice TBD.
