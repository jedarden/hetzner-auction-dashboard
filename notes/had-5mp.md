# Pipeline Component Rollup Completion (had-5mp)

## Summary

This rollup bead covers the complete pipeline/ component implementation across Phases 1-4 of the Hetzner Auction Dashboard project. The pipeline is the server-side half of the architecture, responsible for fetching auction data, enriching it with benchmark scores, computing cost metrics, and publishing results to Cloudflare R2.

## Completed Child Tasks

All 8 blocking dependencies are closed, representing complete implementation of the pipeline:

### Phase 1: Data Fetching & Schema
- **had-2ns**: Pipeline fetcher for Hetzner auction data + raw schema definition
  - Fetches from Hetzner's public Server Auction endpoint
  - Defines raw schema matching Data Models specification
  - Handles malformed/empty responses per Edge Case Catalog

### Phase 2: Benchmark Integration
- **had-1r3**: Benchmark-map reference table + CPU-name matching/alias/override logic
  - PassMark-based CPU benchmark scores (single and multi-thread)
  - Fuzzy/alias-based CPU name matching
  - Manual override list for CPUs that don't match cleanly
  
- **had-3kz**: Unmatched-CPU report generator (unmatched-cpus.json)
  - Generates report each run showing unresolved CPU strings + affected listing counts
  - Published to R2 alongside Parquet snapshot
  - Supports benchmark-map curation by highlighting coverage gaps

### Phase 3: Cost Computation & Parquet Writing
- **had-vsy**: Compute the 4 derived cost metrics
  - `price_effective_monthly`: price_base + price_setup_fee (full-value, non-amortized)
  - `price_per_benchmark_point_single`: effective monthly ÷ single-thread PassMark score
  - `price_per_benchmark_point_multi`: effective monthly ÷ multi-thread PassMark score  
  - `price_per_gb_ram`: effective monthly ÷ RAM capacity
  - `price_per_tb_disk`: effective monthly ÷ total disk capacity in TB

- **had-2h6**: Parquet writer + DuckDB-WASM conformance test
  - Writes single flat Parquet file with all columns
  - Conformance test verifies DuckDB-WASM httpfs compatibility
  - Verified before Phase 4 completion (per plan requirement)

### Phase 4: R2 Publishing & Deployment
- **had-5bi**: Publish lifecycle (temp-key-then-swap for both artifacts)
  - Temp-key write → verify → atomic swap pattern
  - Applied to both Parquet snapshot and unmatched-cpus.json report
  - Cache-Control: max-age=60 header (ADR-4)
  - Prevents partial/corrupted data from ever becoming live

- **had-504**: 10-minute refresh loop
  - Long-running deployment with internal scheduling
  - House rule: no Job/CronJob (matching environment patterns)
  - Logs timestamp of last successful publish

- **had-8w4**: Containerize pipeline + GitOps Deployment manifest
  - Containerized pipeline with Dockerfile
  - GitOps Deployment manifest via declarative-config (k8s/)
  - ArgoCD reconciliation from jedarden/declarative-config
  - Replicas: 1 (single writer per Concurrency Model)

## Architecture Alignment

The pipeline implementation aligns with the plan's architecture decisions:

- **ADR-1**: Cloudflare R2 for Parquet publishing (CORS + range-request support)
- **ADR-2**: PassMark-only benchmark source for v1
- **ADR-3**: Separate per-resource value metrics (no blended score)
- **ADR-4**: Short max-age Cache-Control instead of active CDN purge

## Data Model Contract

The pipeline writes a single flat Parquet table with the following schema per Data Models:

**Identity & Location:**
- `listing_id`, `datacenter`, `location`, `available_from`

**CPU & Performance:**
- `cpu_raw`, `cpu_normalized`, `cpu_benchmark_single`, `cpu_benchmark_multi`, `benchmark_matched` (bool)

**Memory & Storage:**
- `ram_gb`, `ram_ecc`
- `disks`: LIST<STRUCT{type, count, capacity_gb}> (one struct per distinct disk type/size group)

**Pricing:**
- `price_base`, `price_setup_fee` (integer EUR cents)
- `price_effective_monthly` (price_base + price_setup_fee, integer EUR cents)

**Derived Value Metrics:**
- `price_per_benchmark_point_single`, `price_per_benchmark_point_multi` (NULL when benchmark_matched=false)
- `price_per_gb_ram`
- `price_per_tb_disk`

**Metadata:**
- `fetched_at` (staleness indicator for client)

## Operational Characteristics

- **Run Lifecycle**: Fetch → normalize/match → compute → temp-key write → verify → swap
- **Failure Handling**: Aborts before touching live keys; keeps serving last snapshot
- **Concurrency**: Single active writer (replicas: 1) with safe overlapping-run handling via temp-key pattern
- **Secrets**: R2 API token stored as OpenBao/ExternalSecret, never logged
- **Visibility**: Logs last successful publish timestamp

## Integration Points

The pipeline is the writer side of the system's data contract:

1. **Hetzner Auction Feed**: Read-only polling every 10 minutes
2. **benchmark-map/**: Git-tracked CPU reference table + alias/override list
3. **Cloudflare R2**: Writes Parquet snapshot + unmatched-cpus.json report via temp-key-then-swap
4. **Client Dashboard**: Reads published Parquet via DuckDB-WASM httpfs (separate component)

## Completion Status

✅ All pipeline/ component work complete (Phases 1-4)
✅ Ready for Phase 5 (Client Dashboard) and Phase 6 (Cluster Deployment)
✅ Data contract fully implemented and verified
✅ Architecture decisions enforced in implementation

This rollup bead marks the completion of the server-side pipeline infrastructure that powers the entire dashboard.
