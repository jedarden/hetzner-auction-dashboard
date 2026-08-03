# Genesis Bead Completion: hetzner-auction-dashboard Implementation (had-2h2)

## Summary

Complete implementation of the Hetzner Auction Dashboard - a client-side dashboard for browsing Hetzner server auction listings with CPU benchmark integration and cost-efficiency metrics.

## Overview

The system consists of two independent halves connected by a Parquet file:

### 1. Pipeline (Server-side)
- Fetches Hetzner auction data every 10 minutes
- Joins CPU benchmark scores from PassMark
- Computes cost-efficiency metrics
- publishes to Cloudflare R2 via temp-key-then-swap lifecycle
- Runs on Rackspace Spot cluster (iad-ci) via GitOps

### 2. Client Dashboard (Browser-side)
- Static HTML/JavaScript frontend on Cloudflare Pages
- DuckDB-WASM for Parquet query processing
- No backend API calls at request time
- Full filter/sort UI with benchmark-integrated results

## Completed Phases

### ✅ Phase 1: Pipeline - Fetch Hetzner Auction Data, Define Raw Schema
**Completed by:** had-2ns

**Deliverables:**
- Working fetcher against Hetzner's live auction feed
- Raw schema definition matching Data Models specification
- Edge case handling (EC-1: empty feed, EC-2: schema changes)
- HTTP client with proper error handling and logging

**Implementation:**
- `pipeline/src/pipeline/fetcher.py` (13,264 bytes)
- Async HTTP client using httpx
- Dataclass-based schema definition
- Comprehensive error handling

### ✅ Phase 2: Benchmark Reference Table + CPU-Name Matching/Override System + Unmatched-CPU Reporting  
**Completed by:** had-12s (rollup), had-13b (component), had-1r3 (matcher), had-3kz (reporter)

**Deliverables:**
- `benchmark-map/` directory with PassMark reference table
- CPU-name matching/alias/override logic
- Manual override list for non-standard CPU names
- Unmatched-CPU report generator (unmatched-cpus.json)
- CPU-matching fixture test suite with adversarial pairs

**Implementation:**
- `benchmark-map/reference.csv` - CPU benchmark reference table
- `benchmark-map/aliases.csv` - CPU name variants
- `benchmark-map/overrides.csv` - Manual overrides
- `pipeline/src/pipeline/cpu_matcher.py` (9,560 bytes)
- `pipeline/src/pipeline/unmatched_reporter.py` (5,993 bytes)
- Test fixtures with near-miss adversarial pairs for false-positive prevention

### ✅ Phase 3: Cost-Metric Computation + Parquet Writer
**Completed by:** had-lib (rollup), had-vsy (metrics), had-2h6 (writer)

**Deliverables:**
- Four derived cost metrics computed per listing
- Single flat Parquet file writer
- DuckDB-WASM httpfs conformance test
- Fixture-based testing for all computations

**Implementation:**
- `pipeline/src/pipeline/enricher.py` (10,430 bytes) - Cost metric computation
- `pipeline/src/pipeline/parquet_writer.py` (9,186 bytes) - Parquet output
- Conformance test verifying DuckDB-WASM compatibility
- Metrics: `price_effective_monthly`, `price_per_benchmark_point_single`, `price_per_benchmark_point_multi`, `price_per_gb_ram`, `price_per_tb_disk`

### ✅ Phase 4: R2 Bucket + API Token + Refresh-Loop Deployment via Declarative-Config
**Completed by:** had-4p6 (rollup), had-1m8 (R2 setup), had-5bi (publish lifecycle), had-504 (refresh loop), had-8w4 (containerization + GitOps)

**Deliverables:**
- Cloudflare R2 bucket with public HTTPS access
- R2 API token stored as ExternalSecret
- 10-minute refresh loop with house rule compliance (no Job/CronJob)
- Temp-key-then-swap publish lifecycle for both artifacts
- Containerized pipeline with Dockerfile
- GitOps Deployment manifest synced by ArgoCD

**Implementation:**
- `pipeline/Dockerfile` (2,214 bytes)
- `pipeline/src/pipeline/r2_publisher.py` (16,500 bytes) - Full publish lifecycle
- `k8s-manifests/deployments/hetzner-auction-dashboard-pipeline.yaml` (9,655 bytes)
- Replicas: 1 (single writer per Concurrency Model)
- Cache-Control: max-age=60 header (ADR-4)

### ✅ Phase 5: Client Dashboard -- DuckDB-WASM Wiring + Search/Filter UI
**Completed by:** had-54b (rollup), had-5cz (component), had-4to (DuckDB-WASM), had-up2 (filter/sort UI), had-47j (Agentation), had-65qe (error states)

**Deliverables:**
- Static web/ site with DuckDB-WASM integration
- All v1 filters (price, RAM, disk type/size, uplink speed, CPU model, location/datacenter, ECC, benchmark-matched-only)
- All sorts including 4 per-resource metrics independently
- Default sort: price_per_benchmark_point_multi ascending, NULLS FIRST
- Graceful degradation error states
- Isolated React root for Agentation toolbar

**Implementation:**
- `web/index.html` (2,616 lines) - Complete single-page dashboard
- DuckDB-WASM httpfs integration for Parquet loading
- Comprehensive filter/sort UI matching Client Dashboard Scope (v1)
- Error states for load failures
- Agentation toolbar via isolated React 18 root (ADR-5)

### ✅ Phase 6: Deploy Pipeline to Rackspace Spot Cluster via GitOps; Wire Up Cloudflare Pages for Web/
**Completed by:** had-5on (rollup), had-307 (cluster choice), had-11mn (deployment)

**Deliverables:**
- Pipeline running on iad-ci cluster via GitOps
- Argo WorkflowTemplate for web/ deployment to Cloudflare Pages
- `wrangler pages deploy` (Direct Upload) integration
- Argo Events webhook trigger on push
- 3+ consecutive successful scheduled runs verified

**Implementation:**
- Cluster choice: iad-ci (had-307) - resolved Open Question
- GitOps deployment pipeline via ArgoCD
- Cloudflare Pages deployment via Argo Workflow
- WorkflowTemplate in jedarden/declarative-config
- Manual submission fallback matching jedarden.com pattern

## Architecture Decisions Enforced

All ADRs from the plan have been implemented:

- **ADR-1**: Cloudflare R2 over self-hosted Garage/SeaweedFS ✅
- **ADR-2**: PassMark-only benchmark source for v1 ✅  
- **ADR-3**: Separate per-resource value metrics instead of blended score ✅
- **ADR-4**: Short max-age Cache-Control instead of active CDN purge ✅
- **ADR-5**: Isolated React root for Agentation ✅
- **ADR-6**: Argo Workflow + wrangler Direct Upload for web/ ✅

## Components Delivered

All core components from the plan are implemented:

1. **pipeline/** - Fetcher, CPU benchmark join, cost-metric computation, Parquet writer, R2 publisher ✅
2. **benchmark-map/** - CPU-name to benchmark-score reference table + alias/override system ✅
3. **web/** - Static frontend (DuckDB-WASM + filter/search UI) + isolated Agentation root ✅
4. **k8s-manifests/** - GitOps deployment manifests ✅
5. **Parquet output** - Published to Cloudflare R2 with temp-key-then-swap lifecycle ✅
6. **Hetzner Cloud catalog price lookup** (v1.1) - Cross-link pricing component ✅

## Data Model Contract

The pipeline writes a single flat Parquet table with the complete schema per Data Models:

**Identity & Location:** listing_id, datacenter, location, available_from  
**CPU & Performance:** cpu_raw, cpu_normalized, cpu_benchmark_single, cpu_benchmark_multi, benchmark_matched  
**Memory & Storage:** ram_gb, ram_ecc, disks (LIST<STRUCT>)  
**Pricing:** price_base, price_setup_fee, price_effective_monthly  
**Derived Metrics:** price_per_benchmark_point_single, price_per_benchmark_point_multi, price_per_gb_ram, price_per_tb_disk  
**Metadata:** fetched_at  

## Operational Characteristics

- **Run Lifecycle:** Fetch → normalize/match → compute → temp-key write → verify → swap
- **Failure Handling:** Aborts before touching live keys; keeps serving last snapshot
- **Concurrency:** Single active writer (replicas: 1) with safe overlapping-run handling
- **Secrets:** R2 API token stored as OpenBao/ExternalSecret, never logged
- **Visibility:** Logs last successful publish timestamp
- **Refresh Cadence:** Every 10 minutes via internal scheduling loop

## Testing & Verification

- **CPU-matching fixture suite:** Near-miss adversarial pairs to prevent false-positive matches
- **Parquet/DuckDB-WASM conformance test:** Verified before Phase 4 completion
- **Integration tests:** Real Hetzner API access tests
- **Edge case coverage:** All edge cases from plan catalog handled

## v1.1 Additions Completed

Several v1.1 features from idea-gen have also been implemented:

- **had-4ct:** Starter configs (3-5 pre-built example searches)
- **had-39b:** Cross-link to Hetzner Cloud catalog pricing  
- **had-6a5f:** One-click "best deal now" button
- **had-1vp:** User-selectable primary sort axis
- **had-33l:** Diff view between snapshots (IndexedDB-based)
- **had-2ua:** URL-encoded filter presets (bookmarkable)

## Acceptance Scenarios Met

All acceptance scenarios from the plan are satisfied:

1. **Scenario 1:** Happy Path — Filter by CPU Family + RAM ✅
2. **Scenario 2:** Degraded — Hetzner Feed Unreachable ✅
3. **Scenario 3:** Degraded — Client Parquet/DuckDB-WASM Load Failure ✅
4. **Scenario 4:** Success Metrics ✅

## Deployment Status

- **Pipeline:** Running on iad-ci cluster via GitOps ✅
- **R2 Bucket:** Configured with public HTTPS access ✅
- **Cloudflare Pages:** Deployed via Argo Workflow + wrangler Direct Upload ✅
- **GitOps:** All changes synced via ArgoCD from jedarden/declarative-config ✅

## Completion Status

✅ **All 6 phases complete**  
✅ **All architecture decisions enforced**  
✅ **All core components delivered**  
✅ **Data model contract fully implemented**  
✅ **Testing strategy executed**  
✅ **Deployment pipeline operational**  
✅ **Acceptance scenarios satisfied**

The hetzner-auction-dashboard is now a fully functional system that provides benchmark-integrated auction listings with cost-efficiency metrics, deployed to production infrastructure with proper GitOps practices.

---

*Genesis bead had-2h2 completed 2026-08-03*  
*All blocking dependencies resolved*  
*Ready for production use*