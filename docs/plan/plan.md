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

- Fetches current listings from Hetzner's public Server Auction data feed every **10 minutes**. Hetzner doesn't document a fixed update schedule (price drops happen at randomized intervals by design), so this matches the practical cadence third-party tools converge on — frequent enough to catch price drops and new listings without hammering the endpoint.
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

### Historical stats

The 10-minute pipeline cadence means every run is a snapshot, so a real time series accumulates for free — worth deriving once v1's live view is solid. Grouped by what each stat is for:

**Per-config price/value history** (keyed by a config signature — CPU model + RAM + disk layout + datacenter — not by Hetzner's `listing_id`, since the same effective config reappears under a new listing ID every auction cycle):
- All-time-low tracked *separately* for raw price and for `price_per_benchmark_point` — consistent with the plan's "no blended score" stance, these stay two independent facts, not one.
- Price velocity: average price (and price-per-benchmark-point) drop per snapshot tick while a listing is live — signals whether a listing is still falling or near its floor.
- Listing lifetime: how many ticks a listing survives before disappearing — a proxy for how fast that config sells, i.e. how much patience a given deal affords.

**Market-level trends** (aggregate, not per-listing):
- Rolling median/percentile of `price_per_benchmark_point`, overall and per CPU family, so a current listing can be flagged "N% below its trailing 7/30-day value baseline" — a benchmark-normalized version of Server Radar's raw-price index, which is exactly the combination the research found nobody has built.
- Listing volume over time by CPU family, datacenter, RAM tier, disk type.
- AMD vs. Intel value trend (price-per-benchmark-point, not just raw price).
- Benchmark coverage rate over time (% of listings with a matched score) — an internal health metric for `benchmark-map/`; coverage regressions should be visible over time, not just inferable from the current unmatched-CPU report.

**Decision-support fields** derived from the above, for later UI surfacing:

- **Value percentile (headline stat).** Hetzner repeatedly auctions batches of the same decommissioned server model, so exact config signatures (CPU + RAM + disk layout + datacenter) recur naturally over time — that's a real historical distribution to rank against, not an approximation. For each current listing, compute where its `price_per_benchmark_point` (and, separately, its raw price) falls in the distribution of every prior observation of that *same* config signature — e.g. "cheaper than 85% of every time this exact config has ever appeared." This turns "is this a good deal" into a direct percentile instead of a guess.
- **Cohort fallback.** A config needs enough accumulated duplicate observations for its own distribution to mean anything. Below some minimum sample count (exact threshold TBD once real data volume is known), fall back to ranking against the broader CPU-family cohort instead of the exact config — keeps newly-appeared or rare configs from showing a meaningless percentile off 1–2 data points.
- An "at/near all-time-low" badge (separately for price and for price-per-benchmark-point), which is really the percentile stat's 0th-percentile special case.

### Storage pattern for history

Storing this without breaking the pipeline's stateless-per-run design (everything lives in Cloudflare — R2 for data, Pages for the static site — so history can't just live in local pipeline state):

- **Never read-modify-write a growing file.** Rewriting an ever-larger Parquet file in R2 every 10 minutes has no real locking and gets slower/riskier as it grows — a bad fit for object storage.
- Instead, each run writes one small **immutable, timestamped snapshot file** to R2, Hive-partitioned by time (e.g. `history/dt=2026-08-02/1010.parquet`), containing that run's per-config *summary* rows (min price, min price-per-benchmark-point, listing count per config signature) rather than a full raw dump — keeps files small and bounded by distinct-config count, not total listings × every tick forever.
- History files are append-only — written once, never mutated. DuckDB-WASM assembles the full history as one logical table via a glob over the partitioned files (`read_parquet('history/**/*.parquet')`); the client does the assembly at query time, not the pipeline.
- The pipeline's per-run logic stays identical in spirit to the current-snapshot writer (fetch → compute → write-once) — it just also writes to this second, additive location.
- Unbounded growth is a later problem, not a v1-of-this-feature one: a periodic compaction step (e.g. monthly, rolling raw 10-minute ticks into daily aggregates) can bound storage once this ships. Not needed to start.

### Other candidates

- Performance-normalized alerting ("notify when €/PassMark drops below X") — depends on the historical-stats work above.
- Multi-source benchmark cross-validation (Geekbench/YABS corpora) to verify and extend PassMark coverage.
- Workload-weighted scoring (user-adjustable single-thread vs. multi-thread emphasis) instead of, or alongside, the separate per-resource metrics.
- Comparison/side-by-side view.
- Browser extension overlaying benchmark scores directly on Hetzner's own auction page — unexplored by any tool found in research.

## Open Questions

- Frontend framework choice (plain JS + DuckDB-WASM vs. a light framework) — low-stakes given how thin the UI is.
- Which Rackspace Spot cluster hosts the pipeline — any is viable since the dataset regenerates on its own cadence and nothing is stateful; final choice TBD.
