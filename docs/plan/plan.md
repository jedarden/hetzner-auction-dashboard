# hetzner-auction-dashboard Plan

_Last updated: 2026-08-01._ This plan and its companion docs (`docs/research/existing-tools.md`, `docs/notes/benchmark-priority.md`) are living references — if this date and either of those drift more than a few weeks apart, treat the older one as stale and reconcile before trusting it.

## Overview

A fully static, client-side dashboard for browsing Hetzner's dedicated server auction. A separate scheduled pipeline fetches auction listings, enriches each one with a CPU benchmark score and derived cost-efficiency metrics, and publishes the result as a single flat Parquet file. The browser loads that Parquet file directly into DuckDB-WASM and does all search/filtering/sorting locally via SQL — there is no client-side join and no per-request backend API. Success looks like: reaching for this dashboard instead of Hetzner's own auction page whenever deal-hunting, because it shows the same listings ranked by real value — price per benchmark point — instead of raw price alone.

## Competitive Positioning

Per `docs/research/existing-tools.md`: joining a CPU benchmark score onto live auction listings is **not a novel idea** — three existing tools (Auction Browser+, hzfind, Server Auction Tracker) already do it, all sourcing PassMark exclusively. The category leader on every other axis, **Server Radar** (366 stars, actively maintained, SvelteKit + DuckDB-WASM — architecturally the closest sibling to this project), has **no** benchmark integration at all.

Also worth naming, though it's not a benchmark-joining competitor: **hetzner-cli** (Robot API CLI/library, not a web dashboard) has the widest raw filter/sort field coverage found in the survey — 11 sort keys, including a numeric bandwidth-minimum filter — and zero benchmark integration of its own. It's cited elsewhere in this plan (Benchmark Strategy, Data Models) as the precedent for filter/sort field design, not as competition on the benchmark join.

The gap isn't "does a benchmark score exist" — it's that nobody has paired good benchmark data with sustained maintenance and feature depth. Concretely, every existing implementation has one of these weaknesses:

1. Single-source (PassMark only), unverified/incomplete CPU coverage (~90 models at best, disclosed by only one tool).
2. Opaque fixed-weight blended scores (Auction Browser+, Server Auction Tracker) that hide the number that actually matters instead of exposing it directly.
3. No performance-normalized history or alerting — every price-history feature found (Server Radar) tracks raw €, not €-per-benchmark-point over time.
4. No workload weighting — nothing lets a user favor single-thread vs. multi-thread performance for their own use case.

This project's differentiation, in priority order: **(a)** treat the benchmark join itself as the core deliverable and keep it accurate and well-covered (unmatched CPUs surfaced, not silently dropped or guessed — see Benchmark Strategy), **(b)** expose value as separate, transparent per-resource metrics rather than one opaque blended score, **(c)** leave room to grow into Server-Radar-grade breadth (history, alerts) later without having to re-architect, since v1 deliberately does not compete on that axis yet.

## What It Is NOT

- **Not a price-alert/notification bot.** No push notifications, email, or webhooks in v1 — performance-normalized alerting is a named v2 candidate, gated on the historical-stats work landing first.
- **Not a marketplace or reseller.** This never buys, holds, or resells servers, and never wraps Hetzner's checkout flow. It's a read-only view over data Hetzner already publishes; ordering still happens on Hetzner's own site.
- **Not a general-purpose CPU benchmark database.** `benchmark-map/` exists only to answer "what's this Hetzner auction listing's CPU worth" — it only ever contains CPUs that have actually appeared in the auction feed, not a standalone PassMark mirror.
- **Not a client-side join.** The browser never computes or looks up a benchmark score at query time — it only filters/sorts a column already joined server-side into the Parquet file (see Benchmark Strategy). If a future UI change ever seems to need a lookup against `benchmark-map/` in the browser, that's a signal the pipeline is missing a precomputed field, not a signal to add a join client-side.

## Acceptance Scenarios

### Scenario 1: Happy Path — Filter by CPU Family + RAM
**Setup:** Pipeline has published a current Parquet snapshot; the dashboard is loaded in a browser.
**Action:** User sets a CPU-family filter (e.g. "Ryzen") and a minimum-RAM filter (e.g. ≥64GB), leaving everything else at defaults.
**Expected:** Results show only matching listings, sorted by `price_per_benchmark_point_multi` ascending with `NULLS FIRST` (the default sort); any listing with `benchmark_matched = false` has a NULL `price_per_benchmark_point_multi` (and NULL `price_per_benchmark_point_single`) and therefore sorts to the top of the results, visibly flagged as unscored rather than blended in at the bottom or omitted.
**Pass criteria:**
- Filtered set matches only listings satisfying both filters
- Default sort order is `price_per_benchmark_point_multi` ascending, `NULLS FIRST`
- Unscored listings appear with an explicit "unscored" indicator, grouped at the top of the results, never blank or omitted
**Fail criteria:**
- An unscored listing is silently dropped from results
- Default sort is raw price instead of `price_per_benchmark_point_multi`
- Unscored listings sort to the bottom of the results (NULLS LAST) or are scattered among scored listings instead of grouped at the top

### Scenario 2: Degraded — Hetzner Feed Unreachable
**Setup:** A scheduled 10-minute pipeline run starts; Hetzner's auction feed endpoint times out or errors.
**Action:** Pipeline attempts its normal fetch → normalize → compute → publish cycle.
**Expected:** Pipeline aborts before touching the live Parquet key in R2 (see Pipeline Run Lifecycle); the previously published snapshot keeps serving unchanged; the failure is logged.
**Pass criteria:**
- Live R2 key is byte-identical to before the failed run
- Dashboard continues to load and query the last-known-good snapshot
- Failure is logged with enough detail to diagnose (endpoint, status/error, timestamp)
**Fail criteria:**
- Live key is partially overwritten or corrupted
- Dashboard shows broken/empty results because of an upstream failure it had no part in

### Scenario 3: Degraded — Client Parquet/DuckDB-WASM Load Failure
**Setup:** Dashboard loads in a browser; the Parquet fetch via DuckDB-WASM httpfs fails (network error, CORS misconfiguration, or WASM init failure).
**Action:** User opens the dashboard normally.
**Expected:** Dashboard shows a plain error state naming the likely cause (see Graceful Degradation), instead of a blank page or silent hang.
**Pass criteria:**
- An explicit error message is shown, never a blank/frozen UI
- No stale or partial data is rendered as if it were current
**Fail criteria:**
- Blank page with no indication anything went wrong
- Partial/garbled data rendered without an error indicator

### Scenario 4: Success Metrics
- **Performance:** `[FILL IN once real data volume is known — see Performance Ceiling]`. Rough expectation is sub-second initial query time and near-instant re-filter/re-sort on a typical current-auction-sized snapshot, since DuckDB-WASM's range requests only fetch the row groups a query touches.
- **Functionality:** v1 supports every filter/sort listed in Client Dashboard Scope (v1) against a live, benchmark-joined snapshot, with correct staleness display and unscored-listing flagging. History, alerts, and comparison view are explicitly not required for v1.
- **Adoption:** the one signal that matters for a solo tool — checking this dashboard instead of opening Hetzner's own Server Auction page directly when deal-hunting. Catching myself opening Hetzner's page out of habit within the first month is a sign the dashboard isn't pulling its weight yet.

## Architecture

Two independent halves connected only by a Parquet file:

### 1. Pipeline (server-side, scheduled)

- Fetches current listings from Hetzner's public Server Auction data feed every **10 minutes**. Hetzner doesn't document a fixed update schedule (price drops happen at randomized intervals by design), so this matches the practical cadence third-party tools converge on — frequent enough to catch price drops and new listings without hammering the endpoint.
- Normalizes each listing's free-text CPU name and matches it against a maintained CPU benchmark reference table (see Benchmark Strategy). Matching is fuzzy/alias-based with a manual-override list for CPUs that don't match cleanly — this is the part expected to need ongoing curation, not the dashboard code itself.
- Computes derived cost fields for every listing: effective total monthly cost (`price_effective_monthly`, folding in the setup fee — see Data Models for the formula), then price per benchmark point — single-thread and multi-thread computed and stored separately, never blended (see ADR-3) — price per GB RAM, and price per TB disk (see Data Models for each formula).
- Writes ONE denormalized Parquet file — no relational structure, every column a query might filter/sort on is already present.
- Publishes the Parquet file to **Cloudflare R2** (native CORS + HTTP range-request support, required for DuckDB-WASM to do partial reads instead of downloading the whole file on every page load). Chosen over self-hosting on Garage/SeaweedFS to avoid standing up public HTTPS ingress for what's otherwise a personal tool, and it matches Server Radar's proven architecture for this exact use case. Requires an R2 API token stored as a cluster secret (OpenBao/ExternalSecret, matching existing patterns) for the pipeline to push to.
- Runs as a long-lived Deployment with an internal refresh loop (house rule: no Job/CronJob) on a Rackspace Spot cluster, wired through GitOps (`jedarden/declarative-config`, `k8s/` path) — never a live kubectl mutation. Compute (pipeline) and hosting (R2/Pages) are intentionally decoupled: the cluster only needs egress to Cloudflare's API, nothing is served from cluster ingress.
- **Format verification.** Before the pipeline depends on it in production, the chosen Parquet writer's output is confirmed compatible with DuckDB-WASM's httpfs range-request reads via the conformance test in Testing Strategy — checked once by the end of Phase 3 (see Phase 3 completion criteria and Testing Strategy), not re-verified every run and not deferred to Phase 4 or 5.
- **Operational visibility.** The pipeline logs the timestamp of its last successful publish, so a stalled pipeline is visible without needing the dashboard open — the same `fetched_at` value the client already surfaces as a staleness indicator, just also checked from the pipeline side.

### Pipeline Run Lifecycle

Every run — the v1 current-snapshot Parquet file, its companion `unmatched-cpus.json` unmatched-CPU report (see Benchmark Strategy), and later each v2 history file — follows the same verify-before-publish discipline (steps 1-5 below), so one failure mode and one fix covers all three. Step 6's specific "promote, then clean up" mechanics differ slightly for history files, since they never reuse a key — see that step for the scoping:

1. **Fetch** — pull current listings from Hetzner's auction feed.
2. **Normalize/match** — clean CPU strings, resolve against `benchmark-map/`, flag unmatched.
3. **Compute** — derive `price_effective_monthly` first (see Data Models for the setup-fee formula), then `price_per_benchmark_point_single`, `price_per_benchmark_point_multi`, `price_per_gb_ram`, `price_per_tb_disk` (see Data Models for each formula).
4. **Write to a temp key in R2** — never write directly to the live key the client reads. Set a short `Cache-Control: max-age=60` header on the object at write time (see ADR-4) — well under the 10-minute publish cadence, so any CDN staleness after the swap self-resolves quickly.
5. **Verify** — confirm the temp object is readable and structurally sane before it's allowed to become live: non-zero size, and parses as valid Parquet (for the snapshot/history files) or valid JSON (for the unmatched-CPU report).
6. **Promote temp key to its permanent key.** For the snapshot and the unmatched-CPU report — which reuse one fixed, well-known live key every run — this is an atomic swap: copy-then-delete-old, since R2/S3-compatible storage has no native rename; the old key is only deleted after the new one is confirmed in place. A v2 history file instead writes to a brand-new, never-before-used key each run (see Storage pattern for history), so there is no old key to delete — promotion there is just the temp-to-permanent-key copy, confirmed in place, with nothing to clean up after. Either way, the `Cache-Control` header carries through to the live/permanent key.
7. **On failure at any step** — abort immediately without touching the live key. The previously published snapshot keeps serving untouched; the run is simply retried next cycle.

This is the same anti-pattern-avoidance the plan already reasons through for v2 history files (never read-modify-write a growing file) — applied back to v1, which faces the identical overwrite risk every 10-minute cycle and needs the same guarantee, despite not having stated one until now.

### Concurrency Model

The pipeline Deployment **MUST run as a single active writer** (`replicas: 1`). A rolling redeploy could briefly overlap two pods running the lifecycle above concurrently — the temp-key-then-swap pattern is what keeps that safe regardless: whichever instance's swap lands last wins, and the client never sees a partial write, only either the old snapshot or a fully-written new one. This is deliberately simpler than a distributed lock — with a 10-minute cadence and swap-based publishing, a lost overlapping run just means one cycle's data doesn't make it live, never corruption.

### 2. Client (fully static, browser-only)

- Static site bundling DuckDB-WASM, deployed to **Cloudflare Pages** — same hosting pattern as jedarden.com.
- **Frontend framework: none.** Resolved via idea-gen (2026-08-02, see `docs/notes/ideas-ledger.md`): a single static HTML page with DuckDB-WASM loaded inline/from a CDN, no JS framework — the cheapest answer to the plan's former Open Question. This decides the client's rendering approach only; the pipeline that fetches, joins, and publishes the Parquet data (this section's other bullets) is unchanged and unaffected by this choice — the data still comes from a real backend process, just not a per-request one.
- Loads the Parquet file over HTTP via DuckDB-WASM's httpfs, pointed at the R2 bucket's public URL, using range requests so only the needed row groups are fetched.
- All search/filter/sort UI translates directly to SQL `WHERE`/`ORDER BY` against the single pre-joined table — no joins, no benchmark lookup, at query time.
- No backend calls at request time. The only "dynamic" part of the deployed site is that the Parquet file itself changes on the pipeline's refresh cadence.
- **Agentation** ([github.com/benjitaylor/agentation](https://github.com/benjitaylor/agentation)) mounted in the page — the standing house convention for UI feedback on any repo with a web frontend in this workspace, so annotated feedback (element selectors, positions, notes) can be handed to an agent instead of prose descriptions. Agentation requires React 18+, which would otherwise contradict the framework-free decision above — resolved via ADR-5: an isolated React root mounts *only* the Agentation toolbar (via CDN ESM imports, no npm install or bundler), while the dashboard itself (filters, sorts, DuckDB-WASM queries) stays plain HTML/JS with no build step.

### Dependency Integration Contracts

- **Hetzner auction feed** — surface used: the public Server Auction listings endpoint, polled read-only every 10 minutes. Forbidden: no write/order calls; no Robot API authentication needed since this only reads public listings. Unavailable/changed: if the feed is unreachable or its schema changes shape (see Edge Case Catalog), the pipeline aborts the run and keeps serving the last published snapshot; a schema change additionally needs a manual pipeline update, since that's a code change, not a transient blip.
- **Cloudflare R2** — surface used: S3-compatible PUT/COPY/DELETE for the temp-key-then-swap publish pattern (see Pipeline Run Lifecycle), plus the public bucket URL with CORS and range-request support for the client's reads. Forbidden: no read-modify-write against the live key, ever. Unavailable: pipeline aborts the run and retries next cycle — same handling as a feed outage.
- **DuckDB-WASM / httpfs** — surface used: `read_parquet()` over an HTTP(S) URL with range requests, entirely client-side. Forbidden: no server-side query execution, no client-side benchmark join (see What It Is NOT). Unavailable/fails to load: see Graceful Degradation — the dashboard shows an explicit error state rather than a blank page.
- **Agentation (+ its React 18 peer dependency)** — surface used: mounted as an isolated component tree via CDN ESM import, rendering only its own feedback toolbar; never touches the dashboard's own DOM/state. Forbidden: no dependency on Agentation for any core dashboard functionality — it must be removable with zero effect on filters/sorts/data loading. Unavailable/fails to load (CDN down, ESM import fails): the toolbar silently doesn't appear; the dashboard itself is unaffected, since it was already rendering independently.

### Architecture Decision Records

**ADR-1: Cloudflare R2 over self-hosted Garage/SeaweedFS.**
Decision: publish the Parquet file to Cloudflare R2. Rationale: native CORS + HTTP range-request support required for DuckDB-WASM's partial reads, without standing up public HTTPS ingress for what's otherwise a personal tool, and it matches Server Radar's proven architecture for this exact use case. Rejected alternative: self-hosting on Garage or SeaweedFS — rejected because it would require its own public ingress and TLS just to serve one static file, for no real benefit over a managed object store built for exactly this. Invalidation trigger: if R2 cost or Cloudflare account limits ever become a real constraint (unlikely at this data volume), revisit self-hosting. (CDN cache freshness after each publish's atomic swap is a related but separate concern — see ADR-4.)

**ADR-2: PassMark-only benchmark source for v1.**
Decision: source CPU benchmark scores exclusively from PassMark in v1. Rationale: matches the proven approach of every existing implementation (Auction Browser+, hzfind, Server Auction Tracker) — no reason to start anywhere less-validated, and it keeps the matching pipeline to one schema instead of reconciling multiple sources upfront. Rejected alternative: launching with multi-source cross-validation (Geekbench/YABS — see Benchmark Strategy's v2 candidate for the specific sources) from day one — rejected because it multiplies matching complexity before the single-source join is even proven solid. Invalidation trigger: if unmatched-CPU coverage gaps stay persistently high after the override list has had a real chance to mature (see Risk Register R6), promote the v2 cross-validation candidate ahead of schedule.

**ADR-3: Separate per-resource value metrics instead of one blended score.**
Decision: expose `price_per_benchmark_point_single`, `price_per_benchmark_point_multi`, `price_per_gb_ram`, and `price_per_tb_disk` as independent columns rather than a single weighted "Total Score." The same reasoning applies one level deeper, inside the benchmark metric itself: single-thread and multi-thread PassMark scores are two independent CPU-performance axes, so `price_per_benchmark_point` is split into `_single` and `_multi` variants rather than picked-or-blended into one number. `price_per_benchmark_point_multi` is the default sort (see Client Dashboard Scope) since most Hetzner auction hardware is multi-core server-class and PassMark's own primary CPU Mark rating is multi-thread-based, but `_single` stays equally queryable for buyers who weight single-core workloads more. Rationale: a fixed arbitrary weighting (as Auction Browser+ and Server Auction Tracker do) hides the number that matters most for a given buyer's use case; separate, sortable columns let the user decide what to prioritize. Rejected alternative: a blended 0–100 score like the existing tools, and — one level deeper — a fixed-weight single/multi blend; both rejected for the same reason, the exact category weakness this project differentiates against (see Competitive Positioning). Invalidation trigger: if personal usage shows a blended score would genuinely save filtering effort without hiding anything, reconsider it as an optional additional column — never as a replacement for the separate metrics.

**ADR-4: Short max-age Cache-Control instead of an active CDN purge on swap.**
Decision: set a short `Cache-Control: max-age=60` header (well under the 10-minute publish cadence) on both the Parquet snapshot and the `unmatched-cpus.json` report at publish time (temp-key write, carried through the swap — see Pipeline Run Lifecycle), rather than issuing an explicit Cloudflare cache-purge call after each swap. Rationale: bounds any post-swap CDN staleness (EC-7) to roughly a minute — small relative to the 10-minute cadence — without adding a Cloudflare zone-level cache-purge credential the pipeline doesn't otherwise need (its only credential today is the R2 API token, scoped to the bucket — see Security); an active purge also isn't instantaneous in practice (purge propagation itself takes time), so the freshness gain over a short max-age doesn't justify the added credential surface and per-run API call. Rejected alternative: an explicit CDN purge step after every swap — rejected because it needs a broader-scoped Cloudflare API token than R2 alone requires, adds one more per-run failure point, and its freshness guarantee isn't actually absolute either. Invalidation trigger: if a max-age this short ever causes a measurable origin-load or cost problem (unlikely at this traffic scale), revisit — either lengthen it slightly or move to purge-on-swap.

**ADR-5: Isolated React root for Agentation, instead of adopting a framework or skipping it.**
Decision: mount Agentation (the workspace's standard UI-feedback tool, house convention — see `docs/notes/*` and the environment's CLAUDE.md) via a small, self-contained React 18 root loaded from a CDN as ESM, isolated from the rest of the page. The dashboard itself — filters, sorts, DuckDB-WASM queries — stays the plain HTML/JS with no build step already decided above; only Agentation's own toolbar renders inside React. Rationale: Agentation genuinely requires React 18+ (confirmed against its README, not assumed), which directly conflicts with the just-settled no-framework decision; isolating it to its own root gets the house-standard feedback tool without dragging the entire dashboard into a build pipeline it doesn't otherwise need. Rejected alternatives: (a) adopt React for the whole frontend — rejected because it reverses a decision made for good reasons (cheapest, lowest-risk answer to the former Open Question) for the sake of a tool that only needs a toolbar, not a rewrite; (b) skip Agentation for this repo — rejected because the house convention applies to any repo with a web frontend, and the cost of including it (one small, removable, isolated root) is low enough that skipping it isn't worth deviating from a workspace-wide standard. Invalidation trigger: if `agentation` turns out not to publish a CDN-consumable ESM build (unverified as of this writing — confirm early in Phase 5, before committing further to this approach), fall back to rejected alternative (a) or (b) rather than inventing a bespoke bundling step just for this one dependency.

## Components

- `pipeline/` — fetcher + CPU benchmark join + cost-metric computation + Parquet writer; containerized; runs the refresh loop.
- `benchmark-map/` — maintained CPU-name → benchmark-score reference table + alias/override list, git-tracked and hand-maintained. Highest-maintenance artifact in the repo; see `docs/notes/benchmark-priority.md`.
- Unmatched-CPU report (`unmatched-cpus.json`) — generated by the pipeline each run and published to R2 alongside the Parquet snapshot; not part of the git-tracked `benchmark-map/` directory (see Benchmark Strategy).
- `web/` — static frontend (DuckDB-WASM + filter/search UI), deployed to Cloudflare Pages. A single static HTML page, no JS framework (see Architecture > Client), plus an isolated React root mounting Agentation for UI feedback (see ADR-5).
- Parquet output — published to Cloudflare R2 (CORS + range-request support).
- Hetzner Cloud catalog price lookup (v1.1) — small, hand-maintained CPU/RAM/disk-tier → nearest Hetzner Cloud SKU price table, used only for the Cross-link addition below; much lower-maintenance than `benchmark-map/` since Cloud catalog pricing changes rarely. See v1.1 — Adopted Idea-Gen Additions.

## Benchmark Strategy

This is the part of the project that actually matters (see `docs/notes/benchmark-priority.md`) — everything else is solved territory that Server Radar and hetzner-cli already demonstrate.

- **Source (v1):** PassMark single- and multi-thread scores, matching the proven approach of every existing implementation. No reason to start anywhere less-validated.
- **Matching:** raw CPU string → normalized model name → PassMark ID, via an alias table for naming variants (e.g. "Xeon E5-2680 v4" vs. "E5-2680v4") plus a manual override list for anything that doesn't match cleanly.
- **No blended score.** Deliberately do *not* build an Auction-Browser+/Server-Auction-Tracker-style single weighted "Total Score" — and don't blend single-thread and multi-thread PassMark scores into one figure either. Expose `price_per_benchmark_point_single`, `price_per_benchmark_point_multi`, `price_per_gb_ram`, and `price_per_tb_disk` as independent, separately sortable/filterable columns (closer to hzfind's approach). A fixed arbitrary weighting hides the number that matters most; separate columns let the user decide what to prioritize. `price_per_benchmark_point_multi` is the default sort (see Client Dashboard Scope) since PassMark's own primary CPU Mark rating is multi-thread-based and most auction hardware is multi-core server-class; `_single` remains independently available for single-core-sensitive workloads.
- **Unmatched CPUs are surfaced, never guessed.** A listing whose CPU has no benchmark match gets a `NULL` score and an explicit "unscored" state in the UI — it is never silently dropped or given a default/estimated value. Each run, the pipeline publishes a companion `unmatched-cpus.json` file to R2 at a well-known key alongside the Parquet snapshot, using the same temp-key-then-swap discipline (see Pipeline Run Lifecycle) — overwritten every cycle with that run's unresolved `cpu_raw` strings and their affected-listing counts, not accumulated across runs. Since the bucket already serves the Parquet file over a public CORS-enabled URL, that same base URL surfaces this report for direct viewing — no separate UI or git-commit path needed. The override list can then be extended from it, highest-volume gaps first; coverage gaps are the main way this project can fail quietly, so they need to stay visible (see Risk Register R6).
- **v2 candidate (not v1) — Geekbench/YABS cross-validation:** cross-validate/extend PassMark coverage using the disconnected community benchmark data that already exists for Hetzner auction hardware: Geekbench results (posted to Geekbench Browser) and YABS — Yet Another Bench Script — results (posted to community boards like VPSBenchmarks and BareMetalBench), all submitted after-the-fact by buyers rather than joined to any live feed. No existing tool mines this back into a live feed — it's a real opportunity, but out of scope until the PassMark-only v1 is solid. This is the canonical, fuller description of the candidate referenced elsewhere in this plan as "Geekbench/YABS" (see ADR-2, v2/Future Candidates).

## Data Models

Single flat table, one row per auction listing:

- `listing_id`, `datacenter`, `location`, `available_from`
- `cpu_raw`, `cpu_normalized`, `cpu_benchmark_single`, `cpu_benchmark_multi`, `benchmark_matched` (bool)
- `ram_gb`, `ram_ecc`
- `disks`: `LIST<STRUCT{type: HDD/SSD/NVMe, count, capacity_gb}>` — one struct per distinct disk type/size group in the listing (e.g. a 2×NVMe + 2×HDD listing produces two structs), not fixed slot-columns; keeps a single denormalized row per listing while still representing variable, mixed disk configurations
- `uplink_speed` (integer, Mbit/s — e.g. `1000` for a 1 GBit uplink; stored as a plain integer rather than a fixed enum since `docs/research/existing-tools.md` doesn't document a discrete value set for Hetzner's actual auction listings (hetzner-cli treats it as a numeric "bandwidth minimum" filter and sort key, not a small fixed category); an integer supports both a numeric range filter and a categorical selector built on top of it without a schema change either way)
- `price_base`, `price_setup_fee` (integer, EUR cents — e.g. `1999` for €19.99; Hetzner prices auction listings in EUR, and integer cents avoids the float-rounding error a decimal-euro type would introduce across the four separate metrics that divide through this value), `price_effective_monthly` (= `price_base` + `price_setup_fee`, same integer-EUR-cents unit as its two inputs — the setup fee is folded in at full value rather than amortized over an assumed contract length, since Hetzner auction listings carry no stated minimum term, checked against `docs/research/existing-tools.md`, which doesn't document one either. Treating month one as the conservative baseline avoids inventing a commitment length the buyer hasn't made — a deliberate modeling choice, not a Hetzner-stated fact.)
- derived: `price_per_benchmark_point_single`, `price_per_benchmark_point_multi` (each = `price_effective_monthly` ÷ the matching raw PassMark score; NULL when `benchmark_matched = false`; kept as two independent columns rather than one blended figure, per ADR-3 — `price_per_benchmark_point_multi` is the default sort, see Client Dashboard Scope), `price_per_gb_ram` (= `price_effective_monthly` ÷ `ram_gb`), `price_per_tb_disk` (= `price_effective_monthly` ÷ total disk capacity in TB; total capacity = Σ (`count` × `capacity_gb`) across every `disks` struct — each struct's `capacity_gb` is the size of ONE disk in that count-group, so `count` must multiply in or a multi-disk group undercounts — summed regardless of type, no per-type weighting, per ADR-3, then ÷ 1000 to convert the GB sum to TB)
- `fetched_at` (staleness display in the UI)

This schema is the actual interface contract between the two halves of the system — the pipeline is the only writer, the client is the only reader, and neither side reads/writes anything else. Removing or renaming a column, or changing a column's type or unit (e.g. switching `price_base` from its declared integer-EUR-cents type to a different currency or representation), is a breaking change and needs the client updated in lockstep. Adding a new column is always safe — the client's SQL only ever references columns it already knows about, so an extra column is invisible until the UI is updated to use it.

## Failure Handling & Data Safety

### Edge Case Catalog

| # | Name | Description | Resolution |
|---|------|--------------|------------|
| EC-1 | Empty feed result | Hetzner's feed returns zero listings for a run | Publish it if the response was well-formed (an empty auction is real, e.g. between drops); if malformed/error instead, abort per Pipeline Run Lifecycle and keep the last snapshot |
| EC-2 | Feed schema change | Hetzner changes the shape/fields of the auction feed response | Fail closed: abort the run, log the parse error with a sample of the raw payload, keep serving the last snapshot until the pipeline's parser is updated for the new shape |
| EC-3 | Ambiguous CPU match | A raw `cpu_raw` string matches more than one benchmark-map entry | Do not guess — treat as unmatched (`benchmark_matched = false`) and add to the unmatched-CPU report (`unmatched-cpus.json`, see Benchmark Strategy) for manual override-list resolution, same as a zero-match case |
| EC-4 | Listing ID reuse | Hetzner's `listing_id` reappears across ticks with different specs | Assumption: `listing_id` is unique within a single run but is NOT assumed stable in meaning across ticks — the pipeline never carries state keyed by `listing_id` between runs. If this assumption is wrong, v1's exposure is limited (no history yet), and v2's config-signature key deliberately sidesteps it, since that key never relies on `listing_id` as a stable identifier at all — it's keyed on CPU + RAM + disk + datacenter precisely because `listing_id` isn't assumed stable across ticks (see v2 Historical Stats, where the same config reappears under a new `listing_id` each cycle) |
| EC-5 | Truncated/corrupt publish | R2 accepts the temp-key write but the resulting object is truncated or unreadable | Caught by the verify step in Pipeline Run Lifecycle before swap — a temp object that doesn't parse as valid Parquet (snapshot/history files) or valid JSON (unmatched-CPU report) never gets promoted to the live key |
| EC-6 | Uncached Parquet range request | Client requests a byte range the CDN hasn't cached yet | Falls back to an R2 origin fetch for that range (normal cache-miss behavior) — slightly slower first load after each publish, not a correctness issue |
| EC-7 | Post-swap CDN staleness | Cloudflare's edge cache may keep serving previously-cached bytes at the live key's URL for a short window after an atomic swap, instead of the just-published version | Bounded by the short `Cache-Control: max-age=60` header set on publish (see Pipeline Run Lifecycle, ADR-4) — well under the 10-minute publish cadence, so any residual staleness window self-resolves quickly without needing an active CDN purge call |

### Failure Modes & Resilience

Taxonomy by type, each cross-referencing the atomicity design in Pipeline Run Lifecycle:

- **Feed/network failures** (Hetzner endpoint unreachable, timeout, non-200 response): abort the run before any write, keep serving the last snapshot, retry on the next 10-minute cycle. No backoff/retry-within-a-run needed — the next scheduled tick is the retry.
- **Storage/R2 failures** (write rejected, auth failure, temp-key write succeeds but verify fails): abort before the swap step; the live key is never touched. Same retry-next-cycle handling as a feed failure.
- **Client-load failures** (Parquet fetch fails, CORS error, DuckDB-WASM init fails in-browser): see Graceful Degradation — an explicit error state, never a silent blank page or stale-looking render.
- **Internal matching-logic failures** (CPU normalization throws on an unexpected string, benchmark-map lookup errors): the one bad listing is skipped and logged (see Anti-Patterns Catalog and Security's untrusted-input policy) — a single malformed listing must never abort the whole run.

### Anti-Patterns Catalog

Consolidating the "deliberately do not" decisions already scattered through this plan into one place:

- **No blended benchmark score.** Hides the number that matters most behind an arbitrary weighting — see ADR-3.
- **Never guess an unmatched CPU's score.** A NULL/unscored state is always more honest than an estimate that could be wrong in either direction — see Benchmark Strategy.
- **Never read-modify-write a growing R2 file.** No real locking on object storage, and it gets slower and riskier as the file grows — see the v2 history storage pattern and Pipeline Run Lifecycle.
- **No client-side join or benchmark lookup.** The client only filters/sorts a pre-joined column; reintroducing a lookup in the browser undoes the entire point of precomputing the join server-side — see What It Is NOT.
- **No live kubectl mutation of the pipeline Deployment.** All changes go through `declarative-config` + ArgoCD, matching the standing house rule — a live edit just gets reverted by selfHeal anyway.
- **Never abort an entire run over one malformed listing.** Skip and log it instead — one bad record shouldn't cost every other listing in the run its data — see Failure Modes & Resilience.

### Rollback & Safe Defaults

The safe default for any pipeline run failure, at any step, is to do nothing to the live snapshot and simply try again next cycle. There's no separate rollback command to define, because the design never lets a bad run become visible in the first place (Pipeline Run Lifecycle's verify-then-swap gate) — the previously published snapshot IS the rollback state, always, by construction. Nothing about this system requires manual intervention to recover from a single failed run.

### Graceful Degradation / Offline Mode

Two distinct "something's wrong" states the client can be in, and what the user sees for each:

- **Parquet fails to load in-browser** (network error, CORS misconfiguration, DuckDB-WASM init failure): an explicit error state naming the likely cause (e.g. "Could not load auction data — check your connection and reload"), never a blank page or silent hang — see Scenario 3.
- **Pipeline has stopped updating, but the last snapshot still loads fine**: the dashboard loads and works normally; the existing staleness indicator driven by `fetched_at` (see Client Dashboard Scope) is what surfaces this — no separate "pipeline down" banner needed, since a stale-but-correct snapshot degrades gracefully on its own.

## Client Dashboard Scope (v1)

Search/filter/sort only, over the current snapshot — no history, no alerts, no comparison view, no auto-buy. These are all proven features elsewhere (see `docs/research/existing-tools.md`) that can be layered on later without changing the core architecture; v1 stays scoped to nailing the benchmark join and a clean filter/sort experience on top of it.

- Filters: price (`price_effective_monthly`, not `price_base` — consistent with the value-focused framing used throughout this doc, see Data Models), RAM, disk type/size, uplink speed, CPU model, location/datacenter, ECC, benchmark-matched-only toggle.
- Sorts: any column, including all four per-resource value metrics independently (not just price) — `price_per_benchmark_point_single`, `price_per_benchmark_point_multi`, `price_per_gb_ram`, `price_per_tb_disk`.
- Default sort: `price_per_benchmark_point_multi` ascending, `NULLS FIRST` — multi-thread is the default because most Hetzner auction hardware is multi-core server-class and PassMark's own primary CPU Mark rating is multi-thread-based; `price_per_benchmark_point_single` remains an equally first-class, independently sortable column for buyers who weight single-core workloads more (see ADR-3). NULLS FIRST reinforces that benchmark-adjusted value, not raw price, is the point, and puts unscored listings (NULL `price_per_benchmark_point_multi`) at the top of the results instead of the bottom, keeping benchmark-map coverage gaps visible rather than easy to miss.
- Staleness indicator driven by `fetched_at`.

### Performance Ceiling

`docs/research/existing-tools.md` doesn't record an exact figure for how many listings Hetzner's Server Auction carries at any one time, so there's no verified data volume to size against yet. `[FILL IN once real listing-count data is observed from the pipeline's first few weeks of runs]` — the design should comfortably handle at least that scale, since DuckDB-WASM's range-request model only fetches the row groups a query touches rather than the whole file. If Hetzner's auction volume (or this schema's width) ever grows past what a single flat Parquet file can serve responsively client-side, the fallback is server-side pre-aggregation (a thin API doing the heavy filtering before the client sees rows) or narrower default filters on initial load — not a rearchitecture of the "no backend at request time" design.

## v1.1 — Adopted Idea-Gen Additions

Not part of v1's Phase 5 completion gate — adopted from the 2026-08-02 idea-gen run (full ledger and every considered/killed alternative: `docs/notes/ideas-ledger.md`) as near-term, non-blocking additions once v1 ships. Kept as a separate tier so Phase 5's gate stays exactly what it already is.

- **Starter configs + one-click "best deal now" button** (`had-4ct`). 3-5 pre-built example searches (e.g. "Budget web server," "Home NAS," "Game server") as one-click filter presets, plus a single button returning one defensible top-value listing (highest `price_per_benchmark_point_multi` among `benchmark_matched = true` listings) — for a visitor who doesn't want to configure filters at all.
- **User-selectable primary sort axis** (`had-1vp`). Let RAM- or storage-heavy buyers promote `price_per_gb_ram` or `price_per_tb_disk` to the primary sort instead of `price_per_benchmark_point_multi` — a direct, opt-in extension of ADR-3's "separate metrics, user decides priority" stance; the benchmark-value default (Client Dashboard Scope) doesn't change.
- **Diff view between two snapshots** (`had-33l`). Client-side (IndexedDB) comparison of "what changed since I last looked" — cache the previous fetched snapshot, diff row-by-config-signature against the current one. Gets partial price-history value weeks before v2's full historical-stats architecture ships, with zero pipeline changes.
- **Cross-link to Hetzner's own Cloud catalog pricing** (`had-39b`). Show "X% cheaper than the equivalent still-selling Hetzner Cloud instance" on each listing, using the small hand-maintained lookup table noted in Components.
- **Saved/named filter presets, URL-encoded** (`had-2ua`). Serialize filter state into URL query params on every change, read back on load — bookmarkable, no sharing required. **Deprioritized behind the four items above** — build last within this tier.

Tracked as beads in this repo's `.beads/` workspace (prefix `had`).

## Testing Strategy

- **CPU-matching fixture set.** A curated set of real Hetzner auction CPU strings, covering three distinct categories: (1) known-tricky *same-chip* variants that must all resolve to one correct match (e.g. "Xeon E5-2680 v4" vs. "E5-2680v4", differing whitespace/casing); (2) intentionally-unmatchable strings (e.g. a family with no benchmark entry at all), asserted as explicit `benchmark_matched = false`; and (3) **near-miss adversarial pairs** — real, similarly-named but genuinely distinct CPU models (e.g. "Xeon E5-2680 v3" vs. "Xeon E5-2680 v4," same model number, different generation) — each asserted against its own correct match and asserted to *never* cross-match the other. Category (3) is the fixture set's only defense against Risk Register R1 (false-positive matches): categories (1) and (2) only prove the matcher finds the right answer or honestly gives up, neither proves it doesn't confidently produce the *wrong* one. This is the project's core heuristic logic (see Benchmark Strategy), so it's the one place this kind of test pays for itself; everywhere else in this project is comparatively low-risk.
- **Parquet/DuckDB-WASM conformance test.** Before Phase 3 is considered complete, a round-trip test writes a sample Parquet file with the pipeline's chosen writer and confirms DuckDB-WASM can actually load and query it via httpfs range requests — the one hand-off point between the two halves of the architecture where "should work in theory" isn't good enough. Gated at Phase 3, not later, so neither Phase 4's R2 publish infrastructure nor Phase 5's client UI gets built on top of an unverified file format.
- **Definition of done.** Before considering the pipeline or client phase complete: the CPU-matching fixture suite and the Parquet/DuckDB-WASM conformance test both pass. No CI-gated benchmark infrastructure beyond that — this is a solo hobby project, not a system with a regression budget to enforce.

## Security

Proportionate to what this actually is — a personal dashboard with no user accounts, no PII, and no external users.

- **Threat model:** none, explicitly. There are no external users, no authentication, and no PII anywhere in the system. The only credential in the whole project is the R2 API token, scoped to the bucket (not per-object) — that scope is what lets the same token publish both the Parquet snapshot and the `unmatched-cpus.json` report to it, via the temp-key-then-swap lifecycle (see Pipeline Run Lifecycle).
- **Secrets handling:** the R2 token is stored as an OpenBao-backed ExternalSecret (matching existing patterns in this environment), never logged by the pipeline, and rotated on the same ad-hoc cadence as other tokens in this environment — no fixed rotation schedule needed for a personal tool.
- **Untrusted input:** Hetzner's auction feed is the one piece of external input this system consumes. Parse it defensively — a malformed field on a single listing gets that listing skipped and logged (see Failure Modes & Resilience), never a crash of the whole run.
- **Supply chain:** pin the DuckDB-WASM version and pipeline library versions explicitly; no `:latest` image tags on the pipeline container, matching the house rule already in place for this environment.

Explicitly out of scope at this scale: audit logging and a per-threat security matrix — there's no security boundary here worth building either for.

## Implementation Phases

- [ ] **Phase 1: Pipeline — fetch Hetzner auction data, define raw schema**
  Delivers: a working fetcher against Hetzner's live auction feed and a defined raw schema for what a listing looks like before any enrichment.
  Completion criteria:
  - Fetcher successfully retrieves and parses a real auction response end-to-end
  - Raw schema fields match Data Models' pre-enrichment columns (`listing_id`, `cpu_raw`, `ram_gb`, `disks`, `price_base`, etc.)
  - A malformed/empty response is handled without crashing (see Edge Case Catalog EC-1/EC-2)

- [ ] **Phase 2: Benchmark reference table + CPU-name matching/override system + unmatched-CPU reporting**
  Delivers: the `benchmark-map/` artifact — PassMark reference table, alias table, manual override list — plus the pipeline logic that generates the unmatched-CPU report (`unmatched-cpus.json`) each run (see Benchmark Strategy).
  Completion criteria:
  - The CPU-matching fixture set (Testing Strategy) resolves correctly against the reference/alias tables
  - An intentionally-unmatchable CPU string produces `benchmark_matched = false`, never a guessed score
  - Near-miss adversarial pairs (Testing Strategy category 3) each resolve to their own correct match and are asserted to never cross-match each other — this is the fixture set's only defense against Risk Register R1, so it's gated here explicitly rather than left implicit in the first bullet
  - Unmatched-CPU report is generated in the `unmatched-cpus.json` shape (unresolved `cpu_raw` strings + affected-listing counts) and lists every unresolved CPU seen in the fixture set — publishing it to R2 alongside the Parquet file happens in Phase 4, once the R2 publish path exists

- [ ] **Phase 3: Cost-metric computation + Parquet writer**
  Delivers: `price_effective_monthly`, `price_per_benchmark_point_single`, `price_per_benchmark_point_multi`, `price_per_gb_ram`, and `price_per_tb_disk` computed per listing, written to a single flat Parquet file.
  Completion criteria:
  - `price_effective_monthly` computes correctly against a fixture set (`price_base` + `price_setup_fee`, per Data Models' full-value (non-amortized) setup-fee note), including one listing with a non-zero `price_setup_fee` and one with zero, confirming the fee folds in only when present
  - All four per-resource metrics (`price_per_benchmark_point_single`, `price_per_benchmark_point_multi`, `price_per_gb_ram`, `price_per_tb_disk`) compute correctly against a fixture set of known listings, including one with `benchmark_matched = false` (both benchmark-point metrics are NULL, never a divide-by-zero or fallback estimate)
  - Parquet writer's output passes the DuckDB-WASM httpfs conformance test (Testing Strategy) — required for Phase 3 to be considered complete, since Phase 4's R2 publish and Phase 5's client UI both build on an assumed-working file format

- [ ] **Phase 4: R2 bucket + API token (secret via OpenBao/ExternalSecret) + refresh-loop Deployment via declarative-config**
  Delivers: a running pipeline Deployment (`replicas: 1`, GitOps-managed) that fetches, computes, and publishes to R2 on the 10-minute cadence using the full temp-key-then-swap lifecycle.
  Completion criteria:
  - Deployment reconciles cleanly via ArgoCD from `declarative-config`
  - R2 API token is stored as an ExternalSecret and never appears in pipeline logs
  - A forced failure mid-run (e.g. killed fetch) leaves both live R2 keys untouched — the Parquet snapshot and `unmatched-cpus.json` — each independently verified by comparing its object hash before/after, since a kill could land between the two writes and checking only one key would miss that case
  - Both the Parquet snapshot and the `unmatched-cpus.json` report publish to R2 each cycle via the same temp-key-then-swap lifecycle, with the `Cache-Control: max-age=60` header applied (see Benchmark Strategy, Pipeline Run Lifecycle, ADR-4)

- [ ] **Phase 5: Client dashboard — DuckDB-WASM wiring + search/filter UI**
  Delivers: the static `web/` site that loads the published Parquet file via DuckDB-WASM httpfs and implements all Client Dashboard Scope (v1) filters/sorts.
  Completion criteria:
  - Dashboard loads a real published snapshot and returns correct filtered/sorted results for each filter type in scope
  - Default sort is `price_per_benchmark_point_multi` ascending, `NULLS FIRST`; unscored listings are visibly flagged and grouped at the top of the results, not hidden or sorted to the bottom
  - A simulated load failure (bad URL) shows the Graceful Degradation error state, not a blank page
  - Early in this phase (before the rest of the UI is built out): confirm `agentation` actually publishes a CDN-consumable ESM build (ADR-5's invalidation trigger) — if not, resolve which rejected alternative to fall back to before proceeding further
  - Agentation's toolbar is mounted via the isolated React root (ADR-5) and functions independently of the dashboard — removing it has zero effect on filters/sorts/data loading

- [ ] **Phase 6: Deploy pipeline to a Rackspace Spot cluster via GitOps; wire up Cloudflare Pages for `web/`**
  Delivers: both halves live in production — pipeline running unattended on its chosen cluster, `web/` served from Cloudflare Pages against the real R2 bucket.
  Completion criteria:
  - Pipeline completes at least 3 consecutive scheduled runs without manual intervention
  - Cloudflare Pages deployment serves the dashboard and successfully loads the live Parquet file end-to-end
  - Open Questions' cluster choice is resolved and reflected in `declarative-config`

## v2 / Future Candidates

Not part of the initial build — noted so later scope decisions don't have to be re-derived from scratch:

### Historical stats

The 10-minute pipeline cadence means every run is a snapshot, so a real time series accumulates for free — worth deriving once v1's live view is solid. Grouped by what each stat is for:

**Per-config price/value history** (keyed by a config signature — CPU model + RAM + disk layout + datacenter — not by Hetzner's `listing_id`, since the same effective config reappears under a new listing ID every auction cycle):
- All-time-low tracked *separately* for raw price, `price_per_benchmark_point_single`, and `price_per_benchmark_point_multi` — consistent with the plan's "no blended score" stance (and v1's single/multi split, see ADR-3), these stay independent facts, never blended into one.
- Price velocity: average price (and price-per-benchmark-point) drop per snapshot tick while a listing is live — signals whether a listing is still falling or near its floor.
- Listing lifetime: how many ticks a listing survives before disappearing — a proxy for how fast that config sells, i.e. how much patience a given deal affords.

**Market-level trends** (aggregate, not per-listing):
- Rolling median/percentile of `price_per_benchmark_point_multi` (and separately, `price_per_benchmark_point_single`), overall and per CPU family, so a current listing can be flagged "N% below its trailing 7/30-day value baseline" — a benchmark-normalized version of Server Radar's raw-price index, which is exactly the combination the research found nobody has built.
- Listing volume over time by CPU family, datacenter, RAM tier, disk type.
- AMD vs. Intel value trend (price-per-benchmark-point, not just raw price).
- Benchmark coverage rate over time (% of listings with a matched score) — an internal health metric for `benchmark-map/`; coverage regressions should be visible over time, not just inferable from the current unmatched-CPU report.

**Decision-support fields** derived from the above, for later UI surfacing:

- **Value percentile (headline stat).** Hetzner repeatedly auctions batches of the same decommissioned server model, so exact config signatures (CPU + RAM + disk layout + datacenter) recur naturally over time — that's a real historical distribution to rank against, not an approximation. For each current listing, compute where its `price_per_benchmark_point_multi` (and, separately, its raw price and `price_per_benchmark_point_single`) falls in the distribution of every prior observation of that *same* config signature — e.g. "cheaper than 85% of every time this exact config has ever appeared." This turns "is this a good deal" into a direct percentile instead of a guess.
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
- Multi-source benchmark cross-validation (Geekbench/YABS — see Benchmark Strategy's v2 candidate for the specific sources) to verify and extend PassMark coverage.
- Optional user-adjustable single-/multi-thread blend as a convenience column — v1 already exposes `price_per_benchmark_point_single` and `price_per_benchmark_point_multi` as independent, separately sortable columns (see ADR-3 and Benchmark Strategy), so this candidate is only about an opt-in weighted combination for users who want one number, never a replacement for the two separate metrics.
- Comparison/side-by-side view.
- Browser extension overlaying benchmark scores directly on Hetzner's own auction page — unexplored by any tool found in research.

## Open Questions

- Pipeline implementation language/runtime (affects which Parquet-writer library is available — see Architecture's Format-verification note and Testing Strategy's Parquet/DuckDB-WASM conformance test, Phase 3 — and what the containerized `pipeline/` component, per Components, is built with) — final choice TBD. **Resolve before Phase 1** (the fetcher itself is Phase 1's deliverable, and it has to be written in something).
- Which Rackspace Spot cluster hosts the pipeline — any is viable since the dataset regenerates on its own cadence and nothing is stateful; final choice TBD. **Resolve before Phase 6** (deployment phase needs a target cluster).

## Risk Register

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R1 | CPU-matching produces false-positive matches (wrong benchmark score attached to a listing) | Medium | High | The manual override list and unmatched-CPU report (see Benchmark Strategy) only ever cover zero-match and ambiguous-match cases (EC-3) — by construction neither can surface a confident-but-wrong match, since that `cpu_raw` string resolved successfully. Actual false-positive protection is the CPU-matching fixture set's near-miss adversarial-pair category (Testing Strategy): real, similarly-named-but-distinct CPU models asserted to never cross-match each other |
| R2 | Cloudflare R2/Pages outage | Low | Medium | Pipeline aborts and retries next cycle (Pipeline Run Lifecycle); dashboard keeps serving the last snapshot it already loaded client-side until R2 recovers |
| R3 | Hetzner changes the auction feed's format without notice | Medium | High | Pipeline fails closed on parse error (EC-2), keeps serving the last snapshot, logs the raw payload for a quick manual fix |
| R4 | DuckDB-WASM hits a scale ceiling as Hetzner's auction volume grows | Low | Medium | See Performance Ceiling — fallback is server-side pre-aggregation or narrower default filters |
| R5 | Concurrent pipeline writers corrupt the live Parquet file during a rolling redeploy | Low | High | Mitigated structurally by `replicas: 1` + the temp-key-then-swap pattern (Concurrency Model) — last swap wins, no partial writes ever visible |
| R6 | PassMark coverage stays persistently sparse despite a maturing override list — some listings never get a benchmark score | Medium | Medium | Unmatched CPUs are surfaced, never guessed (Benchmark Strategy): NULL score, explicit "unscored" flag, sorted to the top, never blended or dropped — so this fails safely rather than silently, unlike R1. Each run's `unmatched-cpus.json` includes affected-listing counts per unresolved CPU, so overrides can be ranked highest-volume-first from it (see Plan B) — no sorted-output requirement on the pipeline itself, just the counts to rank by. If coverage stays thin after the override list has had a real chance to mature, ADR-2's invalidation trigger promotes the Geekbench/YABS v2 cross-validation candidate ahead of schedule |

## Plan B / Fallback Strategies

- **If DuckDB-WASM + R2 range requests turns out too slow at real scale** (R4): fall back to a thin server-side API doing the pre-filtering Hetzner-side, trading away some of the "no backend at request time" simplicity for scale headroom — the Parquet schema (Data Models) stays the contract either way, just read by a small service instead of directly by the browser.
- **If PassMark coverage stays too sparse after the override list has matured** (R6; see also ADR-2's invalidation trigger): prioritize manual overrides for the highest-volume unmatched CPUs first, ranked by each CPU's affected-listing count in the current `unmatched-cpus.json` report (see Benchmark Strategy), rather than broadening to multi-source matching before it's actually needed.
- **If R2/Cloudflare Pages has a sustained outage** (R2): no separate plan needed — retry-next-cycle and last-known-good serving (Pipeline Run Lifecycle) are already the fallback.
