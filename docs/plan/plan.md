# hetzner-auction-dashboard Plan

## Overview

A fully static, client-side dashboard for browsing Hetzner's dedicated server auction. A separate scheduled pipeline fetches auction listings, enriches each one with a CPU benchmark score and derived cost-efficiency metrics, and publishes the result as a single flat Parquet file. The browser loads that Parquet file directly into DuckDB-WASM and does all search/filtering/sorting locally via SQL — there is no client-side join and no per-request backend API.

## Competitive Positioning

Per `docs/research/existing-tools.md`: joining a CPU benchmark score onto live auction listings is **not a novel idea** — three existing tools (Auction Browser+, hzfind, Server Auction Tracker) already do it, all sourcing PassMark exclusively. The category leader on every other axis, **Server Radar** (366 stars, actively maintained, SvelteKit + DuckDB-WASM — architecturally the closest sibling to this project), has **no** benchmark integration at all.

The gap isn't "does a benchmark score exist" — it's that nobody has paired good benchmark data with sustained maintenance and feature depth. Concretely, every existing implementation has one of these weaknesses:

1. Single-source (PassMark only), unverified/incomplete CPU coverage (~90 models at best, disclosed by only one tool).
2. Opaque fixed-weight blended scores (Auction Browser+, Server Auction Tracker) that hide the number that actually matters instead of exposing it directly.
3. No performance-normalized history or alerting — every price-history feature found (Server Radar) tracks raw €, not €-per-benchmark-point over time.
4. No workload weighting — nothing lets a user favor single-thread vs. multi-thread performance for their own use case.

This project's differentiation, in priority order: **(a)** treat the benchmark join itself as the core deliverable and keep it accurate and well-covered (unmatched CPUs surfaced, not silently dropped or guessed — see Benchmark Strategy), **(b)** expose value as separate, transparent per-resource metrics rather than one opaque blended score, **(c)** leave room to grow into Server-Radar-grade breadth (history, alerts) later without having to re-architect, since v1 deliberately does not compete on that axis yet.

## Architecture

Two independent halves connected only by a Parquet file:

### 1. Pipeline (server-side, scheduled)

- Fetches current listings from Hetzner's public Server Auction data feed.
- Normalizes each listing's free-text CPU name and matches it against a maintained CPU benchmark reference table (see Benchmark Strategy). Matching is fuzzy/alias-based with a manual-override list for CPUs that don't match cleanly — this is the part expected to need ongoing curation, not the dashboard code itself.
- Computes derived cost fields for every listing: price per benchmark point, price per GB RAM, price per TB disk, effective total monthly cost.
- Writes ONE denormalized Parquet file — no relational structure, every column a query might filter/sort on is already present.
- Publishes the Parquet file to **Cloudflare R2** (native CORS + HTTP range-request support, required for DuckDB-WASM to do partial reads instead of downloading the whole file on every page load). Chosen over self-hosting on Garage/SeaweedFS to avoid standing up public HTTPS ingress for what's otherwise a personal tool, and it matches Server Radar's proven architecture for this exact use case. Requires an R2 API token stored as a cluster secret (OpenBao/ExternalSecret, matching existing patterns) for the pipeline to push to.
- Runs as a long-lived Deployment with an internal refresh loop (house rule: no Job/CronJob) on a Rackspace Spot cluster, wired through GitOps (`jedarden/declarative-config`, `k8s/` path) — never a live kubectl mutation. Compute (pipeline) and hosting (R2/Pages) are intentionally decoupled: the cluster only needs egress to Cloudflare's API, nothing is served from cluster ingress.

### 2. Client (fully static, browser-only)

- Static site bundling DuckDB-WASM, deployed to **Cloudflare Pages** — same hosting pattern as jedarden.com.
- Loads the Parquet file over HTTP via DuckDB-WASM's httpfs, pointed at the R2 bucket's public URL, using range requests so only the needed row groups are fetched.
- All search/filter/sort UI translates directly to SQL `WHERE`/`ORDER BY` against the single pre-joined table — no joins, no benchmark lookup, at query time.
- No backend calls at request time. The only "dynamic" part of the deployed site is that the Parquet file itself changes on the pipeline's refresh cadence.

## Components

- `pipeline/` — fetcher + CPU benchmark join + cost-metric computation + Parquet writer; containerized; runs the refresh loop.
- `benchmark-map/` — maintained CPU-name → benchmark-score reference table + alias/override list + unmatched-CPU report. Highest-maintenance artifact in the repo; see `docs/notes/benchmark-priority.md`.
- `web/` — static frontend (DuckDB-WASM + filter/search UI), deployed to Cloudflare Pages.
- Parquet output — published to Cloudflare R2 (CORS + range-request support).

## Benchmark Strategy

This is the part of the project that actually matters (see `docs/notes/benchmark-priority.md`) — everything else is solved territory that Server Radar and hetzner-cli already demonstrate.

- **Source (v1):** PassMark single- and multi-thread scores, matching the proven approach of every existing implementation. No reason to start anywhere less-validated.
- **Matching:** raw CPU string → normalized model name → PassMark ID, via an alias table for naming variants (e.g. "Xeon E5-2680 v4" vs. "E5-2680v4") plus a manual override list for anything that doesn't match cleanly.
- **No blended score.** Deliberately do *not* build an Auction-Browser+/Server-Auction-Tracker-style single weighted "Total Score." Expose `price_per_benchmark_point`, `price_per_gb_ram`, and `price_per_tb_disk` as independent, separately sortable/filterable columns (closer to hzfind's approach). A fixed arbitrary weighting hides the number that matters most; separate columns let the user decide what to prioritize.
- **Unmatched CPUs are surfaced, never guessed.** A listing whose CPU has no benchmark match gets a `NULL` score and an explicit "unscored" state in the UI — it is never silently dropped or given a default/estimated value. The pipeline writes an unmatched-CPU report each run so the override list can be extended; coverage gaps are the main way this project can fail quietly, so they need to stay visible.
- **v2 candidate (not v1):** cross-validate/extend PassMark coverage using the disconnected community benchmark data that already exists for Hetzner auction hardware (Geekbench Browser, VPSBenchmarks, BareMetalBench results posted after-the-fact by buyers). No existing tool mines this back into a live feed — it's a real opportunity, but out of scope until the PassMark-only v1 is solid.

## Data Models

Single flat table, one row per auction listing:

- `listing_id`, `datacenter`, `location`, `available_from`
- `cpu_raw`, `cpu_normalized`, `cpu_benchmark_single`, `cpu_benchmark_multi`, `benchmark_matched` (bool), `benchmark_match_confidence`
- `ram_gb`, `ram_ecc`
- `disks` (type: HDD/SSD/NVMe, count, capacity per disk)
- `uplink_speed`
- `price_base`, `price_setup_fee`, `price_effective_monthly`
- derived: `price_per_benchmark_point`, `price_per_gb_ram`, `price_per_tb_disk`
- `fetched_at` (staleness display in the UI)

## Client Dashboard Scope (v1)

Search/filter/sort only, over the current snapshot — no history, no alerts, no comparison view, no auto-buy. These are all proven features elsewhere (see research) that can be layered on later without changing the core architecture; v1 stays scoped to nailing the benchmark join and a clean filter/sort experience on top of it.

- Filters: price, RAM, disk type/size, CPU model, location/datacenter, ECC, benchmark-matched-only toggle.
- Sorts: any column, including all three per-resource value metrics independently (not just price).
- Default sort: `price_per_benchmark_point` ascending — reinforces that benchmark-adjusted value, not raw price, is the point.
- Staleness indicator driven by `fetched_at`.

## Implementation Phases

- [ ] Phase 1: Pipeline — fetch Hetzner auction data, define raw schema
- [ ] Phase 2: Benchmark reference table + CPU-name matching/override system + unmatched-CPU reporting
- [ ] Phase 3: Cost-metric computation + Parquet writer
- [ ] Phase 4: R2 bucket + API token (secret via OpenBao/ExternalSecret) + refresh-loop Deployment via declarative-config
- [ ] Phase 5: Client dashboard — DuckDB-WASM wiring + search/filter UI
- [ ] Phase 6: Deploy pipeline to a Rackspace Spot cluster via GitOps; wire up Cloudflare Pages for `web/`

## v2 / Future Candidates

Not part of the initial build — noted so later scope decisions don't have to be re-derived from scratch:

- Price history (append rather than overwrite the Parquet file) and performance-normalized alerting ("notify when €/PassMark drops below X") — the specific combination the research found nobody has built.
- Multi-source benchmark cross-validation (Geekbench/YABS corpora) to verify and extend PassMark coverage.
- Workload-weighted scoring (user-adjustable single-thread vs. multi-thread emphasis) instead of, or alongside, the separate per-resource metrics.
- Comparison/side-by-side view.
- Browser extension overlaying benchmark scores directly on Hetzner's own auction page — unexplored by any tool found in research.

## Open Questions

- Refresh cadence for the pipeline — existing tools cluster around 5–15 minutes; needs to be weighed against how often CPU coverage actually needs re-checking versus how often prices change.
- Frontend framework choice (plain JS + DuckDB-WASM vs. a light framework) — low-stakes given how thin the UI is.
- Which Rackspace Spot cluster hosts the pipeline — any is viable since the dataset regenerates on its own cadence and nothing is stateful; final choice TBD.
