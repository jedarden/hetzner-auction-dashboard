# hetzner-auction-dashboard Plan

_Last updated: 2026-08-12._ This plan and its companion docs (`docs/research/existing-tools.md`, `docs/notes/benchmark-priority.md`) are living references — if this date and either of those drift more than a few weeks apart, treat the older one as stale and reconcile before trusting it.

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
**Expected:** Pipeline aborts before invoking `wrangler pages deploy` (see Pipeline Run Lifecycle); the previously published deployment keeps serving unchanged; the failure is logged.
**Pass criteria:**
- No new Cloudflare Pages deployment is created for the failed run
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
- Publishes the Parquet file and `unmatched-cpus.json` by bundling them into the same **Cloudflare Pages** deployment as `web/` and running `wrangler pages deploy` (see ADR-7 — supersedes the original R2-based design in ADR-1). Same-origin serving means no CORS configuration is needed; Cloudflare's CDN already serves HTTP range requests for static assets, so DuckDB-WASM's partial reads work unchanged. Requires a Cloudflare Pages API token (Account: Cloudflare Pages:Edit) stored as a cluster secret (OpenBao/ExternalSecret) — reuses the same already-live token this environment's other Pages deploys use, not a new credential.
- Runs as a long-lived Deployment with an internal refresh loop (house rule: no Job/CronJob) on a Rackspace Spot cluster, wired through GitOps (`jedarden/declarative-config`, `k8s/` path) — never a live kubectl mutation. The cluster only needs egress to Cloudflare's API; nothing is served from cluster ingress.
- **Format verification.** Before the pipeline depends on it in production, the chosen Parquet writer's output is confirmed compatible with DuckDB-WASM's httpfs range-request reads via the conformance test in Testing Strategy — checked once by the end of Phase 3 (see Phase 3 completion criteria and Testing Strategy), not re-verified every run and not deferred to Phase 4 or 5.
- **Operational visibility.** The pipeline logs the timestamp of its last successful publish, so a stalled pipeline is visible without needing the dashboard open — the same `fetched_at` value the client already surfaces as a staleness indicator, just also checked from the pipeline side.

### Pipeline Run Lifecycle

**Rewritten 2026-08-06 for ADR-7** — R2's temp-key-then-swap no longer applies; Cloudflare Pages' own atomic deployment promotion replaces it. Every run — the v1 current-snapshot Parquet file and its companion `unmatched-cpus.json` unmatched-CPU report (see Benchmark Strategy) — follows this verify-locally-then-deploy discipline:

1. **Fetch** — pull current listings from Hetzner's auction feed.
2. **Normalize/match** — clean CPU strings, resolve against `benchmark-map/`, flag unmatched.
3. **Compute** — derive `price_effective_monthly` first (see Data Models for the setup-fee formula), then `price_per_benchmark_point_single`, `price_per_benchmark_point_multi`, `price_per_gb_ram`, `price_per_tb_disk` (see Data Models for each formula).
4. **Write locally, into a fresh copy of `web/`** — write `current_snapshot.parquet` and `unmatched-cpus.json` into a local working copy of the current `web/` static files (not the live site — nothing is public yet at this point). The pipeline keeps its own current copy of `web/` (pulled from the repo; see Implementation Phases for how it stays in sync with code changes) so a data-only deploy always carries the latest code along with it.
5. **Verify** — confirm both files are structurally sane before `wrangler` ever runs: non-zero size, and parses as valid Parquet (snapshot) or valid JSON (unmatched-CPU report). A verification failure aborts before step 6 — nothing gets deployed.
6. **Deploy** — run `wrangler pages deploy` against the assembled directory. This is where Cloudflare Pages' own atomicity takes over: the deployment either completes and is promoted to production as a whole, or fails and the previously-promoted deployment keeps serving unchanged — there is no partial-deploy state a visitor can land on either way, same guarantee R2's copy-then-delete-old swap gave, different mechanism providing it.
7. **On failure at any step** — abort immediately without invoking `wrangler`. The previously deployed snapshot keeps serving untouched; the run is simply retried next cycle.

Cache staleness after a deploy is bounded the same way ADR-4 bounded it for R2 — a short `Cache-Control` on just the two data files, now via a `_headers` file in `web/` rather than a per-object header set at R2 write time (see ADR-7).

v2's percentile/all-time-low feature is implemented (`config_history.parquet` — see "Historical stats: value percentile & all-time-low"): it's fetched back over HTTP and rewritten each cycle, the same fetch-back-before-republish pattern this lifecycle already uses for `web/`, `current_snapshot.parquet`, and `unmatched-cpus.json` — never a read-modify-write of a growing raw log. Velocity/lifetime/market-trend candidates remain deferred to v2 with no storage design yet, since v1 doesn't need them.

### Concurrency Model

The pipeline Deployment **MUST run as a single active writer** (`replicas: 1`). A rolling redeploy could briefly overlap two pods each running the lifecycle above — Cloudflare Pages' deployment promotion is what keeps that safe regardless: whichever pod's `wrangler pages deploy` call is promoted last wins, and a visitor never sees a partial write, only either the previous deployment or a fully-published new one. This is deliberately simpler than a distributed lock — with a 10-minute cadence and deploy-based publishing, a lost overlapping run just means one cycle's data doesn't make it live, never corruption. (Unlike the R2 design, an overlapping run here also means one cycle's *entire deployment* — code included — gets superseded by the other; since both pods would be deploying from the same underlying `web/` code anyway, this has no practical effect beyond the data half.)

### 2. Client (fully static, browser-only)

- Static site bundling DuckDB-WASM, deployed to **Cloudflare Pages** via an Argo Workflow on iad-ci using `wrangler pages deploy` (Direct Upload) to submit the built artifacts directly — never Cloudflare's own git-integration auto-build. Same deployment pattern as jedarden.com's `website-build` template (see ADR-6).
- **Frontend framework: none.** Resolved via idea-gen (2026-08-02, see `docs/notes/ideas-ledger.md`): a single static HTML page with DuckDB-WASM loaded inline/from a CDN, no JS framework — the cheapest answer to the plan's former Open Question. This decides the client's rendering approach only; the pipeline that fetches, joins, and publishes the Parquet data (this section's other bullets) is unchanged and unaffected by this choice — the data still comes from a real backend process, just not a per-request one.
- Loads the Parquet file over HTTP via DuckDB-WASM's httpfs, pointed at the same-origin `/current_snapshot.parquet` path (bundled into the same Cloudflare Pages deployment as the page itself — see ADR-7), using range requests so only the needed row groups are fetched. Same-origin means no CORS configuration is needed, unlike the original R2-based design.
- All search/filter/sort UI translates directly to SQL `WHERE`/`ORDER BY` against the single pre-joined table — no joins, no benchmark lookup, at query time.
- No backend calls at request time. The only "dynamic" part of the deployed site is that the Parquet file itself changes on the pipeline's refresh cadence.
- **Agentation** ([github.com/benjitaylor/agentation](https://github.com/benjitaylor/agentation)) mounted in the page — the standing house convention for UI feedback on any repo with a web frontend in this workspace, so annotated feedback (element selectors, positions, notes) can be handed to an agent instead of prose descriptions. Agentation requires React 18+, which would otherwise contradict the framework-free decision above — resolved via ADR-5: an isolated React root mounts *only* the Agentation toolbar (via CDN ESM imports, no npm install or bundler), while the dashboard itself (filters, sorts, DuckDB-WASM queries) stays plain HTML/JS with no build step.

### Dependency Integration Contracts

- **Hetzner auction feed** — surface used: `GET https://www.hetzner.com/_resources/app/data/app/live_data_sb.json`, a public unauthenticated JSON endpoint, polled read-only every 10 minutes. **Corrected 2026-08-06** — the original entry here pointed at the Robot API (`robot.hetzner.com/order/server_market/product`) and a legacy `/wird/json.pl` path; neither returns the real feed, and Phase 1's original fetcher was built against a schema that doesn't match either. Full endpoint verification, example payload, and the raw-feed→`RawListing` field mapping (nested `Hardware`/`Prices`/`Details` structure, not the flat shape originally assumed) live in `docs/notes/hetzner-live-feed-schema-2026-08-06.md`. Forbidden: no write/order calls; no Robot API authentication needed since this only reads public listings — that part of the original contract was already correct. Unavailable/changed: if the feed is unreachable or its schema changes shape again (see Edge Case Catalog EC-2), the pipeline aborts the run and keeps serving the last published snapshot; a schema change additionally needs a manual pipeline update, since that's a code change, not a transient blip.
- **Cloudflare Pages (Direct Upload)** — surface used: `wrangler pages deploy` for both the code deploy path (ADR-6) and the pipeline's 10-minute data publish (ADR-7, supersedes the original R2-based entry here). Forbidden: no Cloudflare git-integration auto-build (ADR-6); pipeline never deploys without first verifying the local Parquet/JSON output is structurally valid (Pipeline Run Lifecycle step 5). Unavailable: pipeline aborts the run without invoking `wrangler` and retries next cycle — same handling as a feed outage; the previously-promoted deployment keeps serving.
- **DuckDB-WASM / httpfs** — surface used: `read_parquet()` over an HTTP(S) URL with range requests, entirely client-side. Forbidden: no server-side query execution, no client-side benchmark join (see What It Is NOT). Unavailable/fails to load: see Graceful Degradation — the dashboard shows an explicit error state rather than a blank page.
- **Agentation (+ its React 18 peer dependency)** — surface used: mounted as an isolated component tree via CDN ESM import, rendering only its own feedback toolbar; never touches the dashboard's own DOM/state. Forbidden: no dependency on Agentation for any core dashboard functionality — it must be removable with zero effect on filters/sorts/data loading. Unavailable/fails to load (CDN down, ESM import fails): the toolbar silently doesn't appear; the dashboard itself is unaffected, since it was already rendering independently.

### Architecture Decision Records

**ADR-1: Cloudflare R2 over self-hosted Garage/SeaweedFS.** — **SUPERSEDED 2026-08-06 by ADR-7.** Kept for history; do not implement against this ADR. R2 is dropped entirely — the Parquet snapshot and unmatched-cpus.json now bundle into the same Cloudflare Pages deployment as `web/` (see ADR-7). The self-host-vs-R2 framing below was already the wrong comparison in hindsight: it never considered "bundle into the Pages deploy that already exists for `web/`," only "R2 vs. running our own object store."

Decision: publish the Parquet file to Cloudflare R2. Rationale: native CORS + HTTP range-request support required for DuckDB-WASM's partial reads, without standing up public HTTPS ingress for what's otherwise a personal tool, and it matches Server Radar's proven architecture for this exact use case. Rejected alternative: self-hosting on Garage or SeaweedFS — rejected because it would require its own public ingress and TLS just to serve one static file, for no real benefit over a managed object store built for exactly this. Invalidation trigger: if R2 cost or Cloudflare account limits ever become a real constraint (unlikely at this data volume), revisit self-hosting. (CDN cache freshness after each publish's atomic swap is a related but separate concern — see ADR-4.)

**ADR-2: PassMark-only benchmark source for v1.**
Decision: source CPU benchmark scores exclusively from PassMark in v1. Rationale: matches the proven approach of every existing implementation (Auction Browser+, hzfind, Server Auction Tracker) — no reason to start anywhere less-validated, and it keeps the matching pipeline to one schema instead of reconciling multiple sources upfront. Rejected alternative: launching with multi-source cross-validation (Geekbench/YABS — see Benchmark Strategy's v2 candidate for the specific sources) from day one — rejected because it multiplies matching complexity before the single-source join is even proven solid. Invalidation trigger: if unmatched-CPU coverage gaps stay persistently high after the override list has had a real chance to mature (see Risk Register R6), promote the v2 cross-validation candidate ahead of schedule.

**ADR-3: Separate per-resource value metrics instead of one blended score.**
Decision: expose `price_per_benchmark_point_single`, `price_per_benchmark_point_multi`, `price_per_gb_ram`, and `price_per_tb_disk` as independent columns rather than a single weighted "Total Score." The same reasoning applies one level deeper, inside the benchmark metric itself: single-thread and multi-thread PassMark scores are two independent CPU-performance axes, so `price_per_benchmark_point` is split into `_single` and `_multi` variants rather than picked-or-blended into one number. `price_per_benchmark_point_multi` is the default sort (see Client Dashboard Scope) since most Hetzner auction hardware is multi-core server-class and PassMark's own primary CPU Mark rating is multi-thread-based, but `_single` stays equally queryable for buyers who weight single-core workloads more. Rationale: a fixed arbitrary weighting (as Auction Browser+ and Server Auction Tracker do) hides the number that matters most for a given buyer's use case; separate, sortable columns let the user decide what to prioritize. Rejected alternative: a blended 0–100 score like the existing tools, and — one level deeper — a fixed-weight single/multi blend; both rejected for the same reason, the exact category weakness this project differentiates against (see Competitive Positioning). Invalidation trigger: if personal usage shows a blended score would genuinely save filtering effort without hiding anything, reconsider it as an optional additional column — never as a replacement for the separate metrics.

**ADR-4: Short max-age Cache-Control instead of an active CDN purge on swap.** — **SUPERSEDED 2026-08-06 by ADR-7.** Kept for history. The R2 temp-key-then-swap lifecycle this tuned no longer exists; ADR-7 covers the replacement Cache-Control mechanism (a Cloudflare Pages `_headers` file) for the new Pages-deployment publish path.
Decision: set a short `Cache-Control: max-age=60` header (well under the 10-minute publish cadence) on both the Parquet snapshot and the `unmatched-cpus.json` report at publish time (temp-key write, carried through the swap — see Pipeline Run Lifecycle), rather than issuing an explicit Cloudflare cache-purge call after each swap. Rationale: bounds any post-swap CDN staleness (EC-7) to roughly a minute — small relative to the 10-minute cadence — without adding a Cloudflare zone-level cache-purge credential the pipeline doesn't otherwise need (its only credential today is the R2 API token, scoped to the bucket — see Security); an active purge also isn't instantaneous in practice (purge propagation itself takes time), so the freshness gain over a short max-age doesn't justify the added credential surface and per-run API call. Rejected alternative: an explicit CDN purge step after every swap — rejected because it needs a broader-scoped Cloudflare API token than R2 alone requires, adds one more per-run failure point, and its freshness guarantee isn't actually absolute either. Invalidation trigger: if a max-age this short ever causes a measurable origin-load or cost problem (unlikely at this traffic scale), revisit — either lengthen it slightly or move to purge-on-swap.

**ADR-5: Isolated React root for Agentation, instead of adopting a framework or skipping it.**
Decision: mount Agentation (the workspace's standard UI-feedback tool, house convention — see `docs/notes/*` and the environment's CLAUDE.md) via a small, self-contained React 18 root loaded from a CDN as ESM, isolated from the rest of the page. The dashboard itself — filters, sorts, DuckDB-WASM queries — stays the plain HTML/JS with no build step already decided above; only Agentation's own toolbar renders inside React. Rationale: Agentation genuinely requires React 18+ (confirmed against its README, not assumed), which directly conflicts with the just-settled no-framework decision; isolating it to its own root gets the house-standard feedback tool without dragging the entire dashboard into a build pipeline it doesn't otherwise need. Rejected alternatives: (a) adopt React for the whole frontend — rejected because it reverses a decision made for good reasons (cheapest, lowest-risk answer to the former Open Question) for the sake of a tool that only needs a toolbar, not a rewrite; (b) skip Agentation for this repo — rejected because the house convention applies to any repo with a web frontend, and the cost of including it (one small, removable, isolated root) is low enough that skipping it isn't worth deviating from a workspace-wide standard. Invalidation trigger: if `agentation` turns out not to publish a CDN-consumable ESM build (unverified as of this writing — confirm early in Phase 5, before committing further to this approach), fall back to rejected alternative (a) or (b) rather than inventing a bespoke bundling step just for this one dependency.

**ADR-6: Argo Workflow + wrangler Direct Upload instead of Cloudflare's git-integration build for `web/`.**
Decision: deploy `web/` to Cloudflare Pages via a new Argo Workflow on iad-ci that runs `wrangler pages deploy` (Direct Upload) against the already-built static artifacts, rather than connecting the Pages project to Forgejo/GitHub and letting Cloudflare run its own build on every push. Matches jedarden.com's existing `website-build` WorkflowTemplate pattern. Rationale: Cloudflare's git-integration path meters production deployments (500/month on Free, 5,000/month on Pro) — 10-minute-cadence *data* publishing already lives entirely in R2 (ADR-1) precisely to avoid that quota, and routing the comparatively rare `web/` *code* deploys through Cloudflare's own build system would be the one remaining place this project touches that metered path at all. Direct Upload is also Cloudflare's own documented recommendation for "bring your own CI" ("if you want to integrate your own build platform... choose Direct Upload over Git integration"), which is exactly this project's situation — every other build/deploy in this workspace already runs through Argo Workflows on iad-ci, not a third-party platform's native CI. Rejected alternative: Cloudflare's git-integration auto-build on push — rejected because it would be the only deploy path in this project not running through iad-ci, adds a second, Cloudflare-controlled build step with its own (murkier, unverified for Direct-Upload-vs-git-integration) quota accounting, and gains nothing Direct Upload doesn't already provide for a project this size. Invalidation trigger: if Argo Events' webhook wiring for this repo turns out meaningfully harder to stand up than expected (e.g. Forgejo-vs-GitHub webhook source mismatch), fall back to a manual `wrangler pages deploy` submitted the same way jedarden.com's "manual submit" fallback works, before reconsidering Cloudflare's native build.

**ADR-7: Drop Cloudflare R2 — bundle the Parquet snapshot and unmatched-cpus.json into the same Cloudflare Pages deployment as `web/`, published via the pipeline's own `wrangler pages deploy` each cycle. Supersedes ADR-1 and ADR-4.**

Decision: the pipeline no longer publishes to an R2 bucket. Every 10-minute cycle, it assembles a complete deploy directory — the current `web/` static files plus the freshly-written `current_snapshot.parquet` and `unmatched-cpus.json` — and runs `wrangler pages deploy` against it, using the *same* Cloudflare Pages project and `wrangler` mechanism ADR-6 already established for code deploys. Data and code are no longer two separate infrastructure axes (R2 bucket + Pages project); they're one axis with two independent publish triggers (the pipeline's 10-minute timer, and a code push).

Rationale — this reopens ADR-1's decision because a fact ADR-1 didn't have turned out to matter: Cloudflare Pages' documented build quota (500/month Free, 5,000/month Pro) is scoped specifically to git-integration builds — "each time you push new code to your Git repository, Pages will build and deploy your site" (Cloudflare Pages limits docs). **Direct Upload deployments skip the build step entirely and are not documented as counting against that quota** (verified against Cloudflare's Direct Upload and CI docs — neither mentions a build-quota interaction, though neither explicitly rules one out either; treat as "very likely fine, not contractually guaranteed"). Since `web/`'s code deploys already use Direct Upload (ADR-6), a 10-minute-cadence Direct Upload data publish sits on the same already-accepted mechanism, not a new one. That removes ADR-1's original constraint entirely — R2 was never chosen because it was better than Pages for this, only because Pages was assumed to be quota-hostile to a 10-minute cadence, and that assumption was wrong.

With the quota objection gone, bundling into Pages is strictly simpler on every other axis:
- **No CORS.** R2 needed explicit CORS configuration for the browser's cross-origin DuckDB-WASM httpfs reads (Architecture > Pipeline's original bullet). Pages-served data is same-origin with the HTML querying it — nothing to configure.
- **Range requests already work.** Cloudflare's CDN serves range requests for static assets regardless of R2 vs. Pages, so DuckDB-WASM httpfs is unaffected either way.
- **One credential, already live.** R2 needed its own bucket-scoped API token, a new OpenBao secret, and (per had-1m8's stalled history) had never actually been provisioned. The Cloudflare Pages API token already used by `jedarden.com`/`devimprint`/`ai-code-battle`/etc.'s deploys (OpenBao `rs-manager/iad-ci/cloudflare/pages`, confirmed `SecretSynced` and live 2026-08-06) is an account-wide "Cloudflare Pages: Edit" grant, not scoped to one project — it should already have permission to create and deploy this project too, with no new Cloudflare-account action needed. (Flagged as "should," not "does" — unverified until the first real deploy; see Implementation Phases.)
- **One fewer external system.** Drops the R2 bucket, its Terraform resource, its CORS policy, its OpenBao path, and the `boto3` dependency — one Cloudflare Pages project covers both halves.

New tradeoff being knowingly accepted (this is the real cost of this ADR, not free): **data and code now share one deployment history.** Every pipeline cycle's data-only deploy re-uploads the entire `web/` output alongside the fresh data files (Direct Upload has no "patch one file" mode — every deploy is a complete new deployment), and every code-only deploy has to explicitly avoid clobbering the current data. This is handled by having the code-deploy step *fetch the currently-live* `current_snapshot.parquet` and `unmatched-cpus.json` before redeploying, and re-include them unchanged:

```
build-command: "curl -sf https://hetzner-auction-dashboard.pages.dev/current_snapshot.parquet -o current_snapshot.parquet; curl -sf https://hetzner-auction-dashboard.pages.dev/unmatched-cpus.json -o unmatched-cpus.json; true"
build-dir: web
output-dir: .
```
run as the `website-build` WorkflowTemplate's `build-command` (ADR-6 — no new WorkflowTemplate needed for the code-deploy side, same reuse pattern as before). If both curls fail (e.g. first-ever deploy, nothing live yet), the `; true` keeps the build from failing — the dashboard just shows no data until the next pipeline cycle, same as a fresh R2 bucket would have. A rollback of a bad code deploy now also rolls back to whatever data snapshot existed at that deploy, and vice versa — previously fully independent. Accepted because this is a personal, low-traffic tool where a data snapshot being up to 10 minutes "behind" after an unrelated rollback is a non-issue; **invalidation trigger:** if this coupling ever causes a real incident (e.g. wanting to roll back a bad code push without losing the latest data, or a data-publish failure blocking an unrelated urgent code fix), revisit — split back to two Pages projects (one for code, one for data-only) rather than reintroducing R2.

Cache staleness (what ADR-4 handled for R2) is now handled by a `_headers` file committed in `web/` (a real, documented Cloudflare Pages feature — path-pattern-based response header overrides), setting a short `Cache-Control` specifically on the two data files so the dashboard's staleness indicator stays meaningful without depending on unverified purge-on-deploy behavior:
```
/current_snapshot.parquet
  Cache-Control: public, max-age=60
/unmatched-cpus.json
  Cache-Control: public, max-age=60
```
The rest of `web/` (HTML/JS/CSS) is unaffected and keeps Cloudflare Pages' normal asset caching.

Also changes the pipeline's own dependencies: `r2_publisher.py`'s `boto3`/S3 calls are replaced by a `wrangler pages deploy` invocation (shelling out, or the Cloudflare API directly — implementation's call, tracked in beads), so the container image needs Node.js + `wrangler` alongside Python, where it previously only needed `boto3`. R2's temp-key-then-swap atomicity (Pipeline Run Lifecycle, below) is replaced by Cloudflare Pages' own atomic deployment promotion — a `wrangler pages deploy` either succeeds completely and gets promoted, or fails and the previous deployment keeps serving; there is no partial-deploy state visible to visitors either way, so this is not a weaker guarantee than R2's, just a different mechanism providing it.

Rejected alternative: keep R2 (ADR-1's original design) — rejected because the coupling downside is real but small at this project's scale, while R2 costs an entire second Cloudflare product surface (bucket, CORS, its own token/OpenBao path) for a benefit (data/code independence) this project doesn't actually need yet.

## Components

- `pipeline/` — fetcher + CPU benchmark join + cost-metric computation + Parquet writer; containerized; runs the refresh loop.
- `benchmark-map/` — maintained CPU-name → benchmark-score reference table + alias/override list, git-tracked and hand-maintained. Highest-maintenance artifact in the repo; see `docs/notes/benchmark-priority.md`.
- Unmatched-CPU report (`unmatched-cpus.json`) — generated by the pipeline each run and bundled into the same Cloudflare Pages deployment alongside the Parquet snapshot (see ADR-7); not part of the git-tracked `benchmark-map/` directory (see Benchmark Strategy).
- `web/` — static frontend (DuckDB-WASM + filter/search UI), deployed to Cloudflare Pages via an Argo Workflow + `wrangler` Direct Upload (see ADR-6), never Cloudflare's own git-integration build. A single static HTML page, no JS framework (see Architecture > Client), plus an isolated React root mounting Agentation for UI feedback (see ADR-5). Also carries a `_headers` file setting a short `Cache-Control` on the two data files (see ADR-7).
- Parquet output + `unmatched-cpus.json` — bundled into the same Cloudflare Pages deployment as `web/`, published by the pipeline's own `wrangler pages deploy` each cycle (see ADR-7 — no separate object store).
- Hetzner Cloud catalog price lookup (v1.1) — small, hand-maintained CPU/RAM/disk-tier → nearest Hetzner Cloud SKU price table, used only for the Cross-link addition below; much lower-maintenance than `benchmark-map/` since Cloud catalog pricing changes rarely. See v1.1 — Adopted Idea-Gen Additions.

## Benchmark Strategy

This is the part of the project that actually matters (see `docs/notes/benchmark-priority.md`) — everything else is solved territory that Server Radar and hetzner-cli already demonstrate.

- **Source (v1):** PassMark single- and multi-thread scores, matching the proven approach of every existing implementation. No reason to start anywhere less-validated.
- **Matching:** raw CPU string → normalized model name → PassMark ID, via an alias table for naming variants (e.g. "Xeon E5-2680 v4" vs. "E5-2680v4") plus a manual override list for anything that doesn't match cleanly.
- **No blended score.** Deliberately do *not* build an Auction-Browser+/Server-Auction-Tracker-style single weighted "Total Score" — and don't blend single-thread and multi-thread PassMark scores into one figure either. Expose `price_per_benchmark_point_single`, `price_per_benchmark_point_multi`, `price_per_gb_ram`, and `price_per_tb_disk` as independent, separately sortable/filterable columns (closer to hzfind's approach). A fixed arbitrary weighting hides the number that matters most; separate columns let the user decide what to prioritize. `price_per_benchmark_point_multi` is the default sort (see Client Dashboard Scope) since PassMark's own primary CPU Mark rating is multi-thread-based and most auction hardware is multi-core server-class; `_single` remains independently available for single-core-sensitive workloads.
- **Unmatched CPUs are surfaced, never guessed.** A listing whose CPU has no benchmark match gets a `NULL` score and an explicit "unscored" state in the UI — it is never silently dropped or given a default/estimated value. Each run, the pipeline publishes a companion `unmatched-cpus.json` file at a well-known same-origin path alongside the Parquet snapshot, bundled into the same Cloudflare Pages deployment (see ADR-7, Pipeline Run Lifecycle) — overwritten every cycle with that run's unresolved `cpu_raw` strings and their affected-listing counts, not accumulated across runs. Since the site already serves the Parquet file at that same origin, that same base URL surfaces this report for direct viewing — no separate UI or git-commit path needed. The override list can then be extended from it, highest-volume gaps first; coverage gaps are the main way this project can fail quietly, so they need to stay visible (see Risk Register R6).
- **v2 candidate (not v1) — Geekbench/YABS cross-validation:** cross-validate/extend PassMark coverage using the disconnected community benchmark data that already exists for Hetzner auction hardware: Geekbench results (posted to Geekbench Browser) and YABS — Yet Another Bench Script — results (posted to community boards like VPSBenchmarks and BareMetalBench), all submitted after-the-fact by buyers rather than joined to any live feed. No existing tool mines this back into a live feed — it's a real opportunity, but out of scope until the PassMark-only v1 is solid. This is the canonical, fuller description of the candidate referenced elsewhere in this plan as "Geekbench/YABS" (see ADR-2, v2/Future Candidates).

## Data Models

Single flat table, one row per auction listing:

- `listing_id`, `datacenter`, `location`, `available_from` (always `NULL` — **corrected 2026-08-06**: the live feed exposes no future-availability window at all, so this column can never be populated from real data. Every listing in the feed is immediately-orderable inventory by construction; kept as a schema column rather than dropped in case a real signal for it ever appears, but no code should attempt to source it. See `docs/notes/hetzner-live-feed-schema-2026-08-06.md`.)
- `cpu_raw`, `cpu_normalized`, `cpu_benchmark_single`, `cpu_benchmark_multi`, `benchmark_matched` (bool)
- `ram_gb`, `ram_ecc`
- `disks`: `LIST<STRUCT{type: HDD/SSD/NVMe, count, capacity_gb}>` — one struct per distinct disk type/size group in the listing (e.g. a 2×NVMe + 2×HDD listing produces two structs), not fixed slot-columns; keeps a single denormalized row per listing while still representing variable, mixed disk configurations
- `uplink_speed` (integer, Mbit/s — e.g. `1000` for a 1 GBit uplink; stored as a plain integer rather than a fixed enum since `docs/research/existing-tools.md` doesn't document a discrete value set for Hetzner's actual auction listings (hetzner-cli treats it as a numeric "bandwidth minimum" filter and sort key, not a small fixed category); an integer supports both a numeric range filter and a categorical selector built on top of it without a schema change either way)
- `price_base`, `price_ipv4_monthly`, `price_setup_fee` (integer, EUR cents — e.g. `1999` for €19.99; Hetzner publishes the required primary IPv4 charge separately in `IPPrices`, so it must not be omitted from buyer cost), `price_effective_monthly` (= `price_base` + `price_ipv4_monthly` + `price_setup_fee`, same integer-EUR-cents unit as its inputs — the setup fee is folded in at full value rather than amortized over an assumed contract length. The dashboard labels recurring server + IPv4 cost separately from this conservative first-month value and identifies prices as excluding VAT.)
- derived: `price_per_benchmark_point_single`, `price_per_benchmark_point_multi` (each = `price_effective_monthly` ÷ the matching raw PassMark score; NULL when `benchmark_matched = false`; kept as two independent columns rather than one blended figure, per ADR-3 — `price_per_benchmark_point_multi` is the default sort, see Client Dashboard Scope), `price_per_gb_ram` (= `price_effective_monthly` ÷ `ram_gb`), `price_per_tb_disk` (= `price_effective_monthly` ÷ total disk capacity in TB; total capacity = Σ (`count` × `capacity_gb`) across every `disks` struct — each struct's `capacity_gb` is the size of ONE disk in that count-group, so `count` must multiply in or a multi-disk group undercounts — summed regardless of type, no per-type weighting, per ADR-3, then ÷ 1000 to convert the GB sum to TB)
- v2 historical-value (see "Historical stats: value percentile & all-time-low"): `price_percentile_vs_history`, `price_per_benchmark_point_single_percentile_vs_history`, `price_per_benchmark_point_multi_percentile_vs_history` (each a float in `[0, 1]`, NULL until any history exists for the config; identical values per listing by construction, kept as separate columns per ADR-3), `is_all_time_low` (bool), `history_sample_size` (int, observation count backing the percentile actually used), `history_cohort_fallback` (bool, whether the broader CPU-model cohort was used instead of the exact config's own thin history)
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
| EC-5 | Truncated/corrupt local write | The pipeline's local Parquet/JSON write is truncated or unreadable before `wrangler` ever runs | Caught by the verify step in Pipeline Run Lifecycle before deploy — a local file that doesn't parse as valid Parquet (snapshot) or valid JSON (unmatched-CPU report) never gets passed to `wrangler pages deploy` |
| EC-6 | Uncached Parquet range request | Client requests a byte range the CDN hasn't cached yet | Falls back to a Cloudflare Pages origin fetch for that range (normal cache-miss behavior) — slightly slower first load after each publish, not a correctness issue |
| EC-7 | Post-deploy CDN staleness | Cloudflare's edge cache may keep serving previously-cached bytes for a short window after a new deployment promotes, instead of the just-published version | Bounded by the short `Cache-Control` set via `web/`'s `_headers` file on just the two data files (see Pipeline Run Lifecycle, ADR-7) — well under the 10-minute publish cadence, so any residual staleness window self-resolves quickly without needing an active CDN purge call |

### Failure Modes & Resilience

Taxonomy by type, each cross-referencing the atomicity design in Pipeline Run Lifecycle:

- **Feed/network failures** (Hetzner endpoint unreachable, timeout, non-200 response): abort the run before any write, keep serving the last snapshot, retry on the next 10-minute cycle. No backoff/retry-within-a-run needed — the next scheduled tick is the retry.
- **Deploy failures** (`wrangler pages deploy` rejected, auth failure, local write succeeds but verify fails): abort before invoking `wrangler`; the live deployment is never touched. Same retry-next-cycle handling as a feed failure.
- **Client-load failures** (Parquet fetch fails, CORS error, DuckDB-WASM init fails in-browser): see Graceful Degradation — an explicit error state, never a silent blank page or stale-looking render.
- **Internal matching-logic failures** (CPU normalization throws on an unexpected string, benchmark-map lookup errors): the one bad listing is skipped and logged (see Anti-Patterns Catalog and Security's untrusted-input policy) — a single malformed listing must never abort the whole run.

### Anti-Patterns Catalog

Consolidating the "deliberately do not" decisions already scattered through this plan into one place:

- **No blended benchmark score.** Hides the number that matters most behind an arbitrary weighting — see ADR-3.
- **Never guess an unmatched CPU's score.** A NULL/unscored state is always more honest than an estimate that could be wrong in either direction — see Benchmark Strategy.
- **Never read-modify-write a growing published file.** No real locking on either object storage or a deployment artifact, and it gets slower and riskier as the file grows — see the v2 history storage pattern and Pipeline Run Lifecycle. (Originally framed around R2 specifically; the principle outlives ADR-7's move off R2.)
- **No client-side join or benchmark lookup.** The client only filters/sorts a pre-joined column; reintroducing a lookup in the browser undoes the entire point of precomputing the join server-side — see What It Is NOT.
- **No live kubectl mutation of the pipeline Deployment.** All changes go through `declarative-config` + ArgoCD, matching the standing house rule — a live edit just gets reverted by selfHeal anyway.
- **Never abort an entire run over one malformed listing.** Skip and log it instead — one bad record shouldn't cost every other listing in the run its data — see Failure Modes & Resilience.

### Rollback & Safe Defaults

The safe default for any pipeline run failure, at any step, is to do nothing to the live snapshot and simply try again next cycle. There's no separate rollback command to define, because the design never lets a bad run become visible in the first place (Pipeline Run Lifecycle's verify-then-deploy gate, backed by Cloudflare Pages' own atomic deployment promotion) — the previously published deployment IS the rollback state, always, by construction. Nothing about this system requires manual intervention to recover from a single failed run.

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

- **Starter configs** (`had-4ct`). 3-5 pre-built example searches (e.g. "Budget web server," "Home NAS," "Game server") as one-click filter presets, for a visitor who doesn't want to configure filters from scratch.
- **One-click "best deal now" button** (`had-6a5f`). A single button returning one defensible top-value listing (highest `price_per_benchmark_point_multi` among `benchmark_matched = true` listings). Split from Starter configs — same finalist origin, separable feature.
- **User-selectable primary sort axis** (`had-1vp`). Let RAM- or storage-heavy buyers promote `price_per_gb_ram` or `price_per_tb_disk` to the primary sort instead of `price_per_benchmark_point_multi` — a direct, opt-in extension of ADR-3's "separate metrics, user decides priority" stance; the benchmark-value default (Client Dashboard Scope) doesn't change.
- **Diff view between two snapshots** (`had-33l`). Client-side (IndexedDB) comparison of "what changed since I last looked" — cache the previous fetched snapshot, diff row-by-config-signature against the current one. Gets partial price-history value weeks before v2's full historical-stats architecture ships, with zero pipeline changes.
- **Cross-link to Hetzner's own Cloud catalog pricing** (`had-39b`). Show "X% cheaper than the equivalent still-selling Hetzner Cloud instance" on each listing, using the small hand-maintained lookup table noted in Components.
- **Saved/named filter presets, URL-encoded** (`had-2ua`). Serialize filter state into URL query params on every change, read back on load — bookmarkable, no sharing required. **Deprioritized behind the four items above** — build last within this tier.

Tracked as beads in this repo's `.beads/` workspace (prefix `had`).

## Testing Strategy

- **CPU-matching fixture set.** A curated set of real Hetzner auction CPU strings, covering three distinct categories: (1) known-tricky *same-chip* variants that must all resolve to one correct match (e.g. "Xeon E5-2680 v4" vs. "E5-2680v4", differing whitespace/casing); (2) intentionally-unmatchable strings (e.g. a family with no benchmark entry at all), asserted as explicit `benchmark_matched = false`; and (3) **near-miss adversarial pairs** — real, similarly-named but genuinely distinct CPU models (e.g. "Xeon E5-2680 v3" vs. "Xeon E5-2680 v4," same model number, different generation) — each asserted against its own correct match and asserted to *never* cross-match the other. Category (3) is the fixture set's only defense against Risk Register R1 (false-positive matches): categories (1) and (2) only prove the matcher finds the right answer or honestly gives up, neither proves it doesn't confidently produce the *wrong* one. This is the project's core heuristic logic (see Benchmark Strategy), so it's the one place this kind of test pays for itself; everywhere else in this project is comparatively low-risk.
- **Parquet/DuckDB-WASM conformance test.** Before Phase 3 is considered complete, a round-trip test writes a sample Parquet file with the pipeline's chosen writer and confirms DuckDB-WASM can actually load and query it via httpfs range requests — the one hand-off point between the two halves of the architecture where "should work in theory" isn't good enough. Gated at Phase 3, not later, so neither Phase 4's Pages publish infrastructure nor Phase 5's client UI gets built on top of an unverified file format.
- **Definition of done.** Before considering the pipeline or client phase complete: the CPU-matching fixture suite and the Parquet/DuckDB-WASM conformance test both pass. No CI-gated benchmark infrastructure beyond that — this is a solo hobby project, not a system with a regression budget to enforce.

## Security

Proportionate to what this actually is — a personal dashboard with no user accounts, no PII, and no external users.

- **Threat model:** none, explicitly. There are no external users, no authentication, and no PII anywhere in the system. The only credential in the whole project is a Cloudflare Pages API token (Account: Cloudflare Pages:Edit, account-wide rather than per-project — see ADR-7) — the same scope that lets it publish both the code deploy (ADR-6) and the pipeline's data deploy (ADR-7) to the same project.
- **Secrets handling:** the Cloudflare Pages token is stored as an OpenBao-backed ExternalSecret (matching existing patterns in this environment — it's the same token/path already used by this environment's other Pages-deployed sites, not a new credential minted for this project), never logged by the pipeline, and rotated on the same ad-hoc cadence as other tokens in this environment — no fixed rotation schedule needed for a personal tool.
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
  - Unmatched-CPU report is generated in the `unmatched-cpus.json` shape (unresolved `cpu_raw` strings + affected-listing counts) and lists every unresolved CPU seen in the fixture set — publishing it alongside the Parquet file happens in Phase 4, once the Cloudflare Pages publish path exists (ADR-7)

- [ ] **Phase 3: Cost-metric computation + Parquet writer**
  Delivers: `price_effective_monthly`, `price_per_benchmark_point_single`, `price_per_benchmark_point_multi`, `price_per_gb_ram`, and `price_per_tb_disk` computed per listing, written to a single flat Parquet file.
  Completion criteria:
  - `price_effective_monthly` computes correctly against a fixture set (`price_base` + `price_setup_fee`, per Data Models' full-value (non-amortized) setup-fee note), including one listing with a non-zero `price_setup_fee` and one with zero, confirming the fee folds in only when present
  - All four per-resource metrics (`price_per_benchmark_point_single`, `price_per_benchmark_point_multi`, `price_per_gb_ram`, `price_per_tb_disk`) compute correctly against a fixture set of known listings, including one with `benchmark_matched = false` (both benchmark-point metrics are NULL, never a divide-by-zero or fallback estimate)
  - Parquet writer's output passes the DuckDB-WASM httpfs conformance test (Testing Strategy) — required for Phase 3 to be considered complete, since Phase 4's Pages publish and Phase 5's client UI both build on an assumed-working file format

- [ ] **Phase 4: Cloudflare Pages API token + refresh-loop Deployment via declarative-config** (rewritten 2026-08-06 for ADR-7 — was "R2 bucket + API token")
  Delivers: a running pipeline Deployment (`replicas: 1`, GitOps-managed) that fetches, computes, and publishes via `wrangler pages deploy` on the 10-minute cadence, bundling the Parquet snapshot and `unmatched-cpus.json` into the same Cloudflare Pages deployment as `web/` (see ADR-7).
  Completion criteria:
  - Deployment reconciles cleanly via ArgoCD from `declarative-config`
  - Cloudflare Pages token is stored as an ExternalSecret (reusing the existing `rs-manager/iad-ci/cloudflare/pages` OpenBao path — already live, used by this environment's other Pages deploys) and never appears in pipeline logs
  - A forced failure mid-run (e.g. killed fetch) leaves the live deployment untouched — confirmed by checking the previously-promoted deployment is still the one served, since Cloudflare Pages' own atomicity (not a manual hash comparison) is what provides this guarantee now
  - Both the Parquet snapshot and the `unmatched-cpus.json` report publish each cycle via the same `wrangler pages deploy` call, with the `_headers`-file Cache-Control applied (see Benchmark Strategy, Pipeline Run Lifecycle, ADR-7)
  - The first real deploy confirms the reused Cloudflare Pages token actually has permission to create and deploy this (new, not-yet-existing) project — ADR-7 flagged this as expected-but-unverified

- [ ] **Phase 5: Client dashboard — DuckDB-WASM wiring + search/filter UI**
  Delivers: the static `web/` site that loads the published Parquet file via DuckDB-WASM httpfs and implements all Client Dashboard Scope (v1) filters/sorts.
  Completion criteria:
  - Dashboard loads a real published snapshot and returns correct filtered/sorted results for each filter type in scope
  - Default sort is `price_per_benchmark_point_multi` ascending, `NULLS FIRST`; unscored listings are visibly flagged and grouped at the top of the results, not hidden or sorted to the bottom
  - A simulated load failure (bad URL) shows the Graceful Degradation error state, not a blank page
  - Early in this phase (before the rest of the UI is built out): confirm `agentation` actually publishes a CDN-consumable ESM build (ADR-5's invalidation trigger) — if not, resolve which rejected alternative to fall back to before proceeding further
  - Agentation's toolbar is mounted via the isolated React root (ADR-5) and functions independently of the dashboard — removing it has zero effect on filters/sorts/data loading

- [ ] **Phase 6: Deploy pipeline to a Rackspace Spot cluster via GitOps; wire the code-only deploy path to not clobber live data** (rewritten 2026-08-06 for ADR-7 — was "build the Argo Workflow that deploys web/")
  Delivers: both halves live in production — pipeline running unattended on its chosen cluster, publishing data via its own `wrangler pages deploy` calls (Phase 4); and the *code* deploy path — the existing generic `website-build` WorkflowTemplate (ADR-6, no new template needed) — parametrized with the curl-preserve `build-command` from ADR-7 so a `web/`-only push doesn't overwrite the live data with nothing.
  Completion criteria:
  - Pipeline completes at least 3 consecutive scheduled runs without manual intervention, each producing a new live Cloudflare Pages deployment
  - A code-only push (a `web/` change with no pipeline involvement) deploys via `website-build` with the curl-preserve `build-command` (ADR-7), and the live site's data is unchanged afterward — this is the one new failure mode ADR-7 introduces and Phase 6 must prove doesn't happen
  - The live site loads the real Parquet file end-to-end with no CORS errors (same-origin now, so absence of CORS errors is itself a completion signal — their presence would mean something regressed to a cross-origin setup)
  - Open Questions' cluster choice is resolved and reflected in `declarative-config` (already done — see Open Questions)

## v2 / Future Candidates

Not part of the initial build — noted so later scope decisions don't have to be re-derived from scratch:

### Historical stats: value percentile & all-time-low — IMPLEMENTED 2026-08-14

**Shipped** (`pipeline/src/pipeline/history_store.py`, image 0.1.13+). Answers: how does this listing's price compare to the extremes/distribution of every time this exact config has ever been auctioned. The design below is kept as originally resolved on 2026-08-12; three deviations from it, found during implementation, are called out inline rather than silently rewritten over:

1. **`cpu_key` column added** to `config_history.parquet`, beyond the schema sketch below — the cohort fallback (see below) needs something to group same-CPU-different-config entries on without maintaining a second stored table. It's `cpu_normalized` (falling back to `cpu_raw` when unmatched), computed the same way `config_signature`'s own CPU component is.
2. **`MIN_OBSERVATIONS_FOR_OWN_HISTOGRAM = 5`** is the concrete value chosen for the "exact threshold TBD" note below — a conservative starting point, not derived from real data yet (none existed at implementation time). Easy to retune once real volume is observed; it's a single module-level constant in `history_store.py`.
3. **Fetch-back failure is NOT treated the same as "no history yet."** A 404 fetching the live `config_history.parquet` means bootstrap (nothing published yet, expected on the first run) and starts from an empty history. Any OTHER failure — network error, timeout, non-404 status, corrupt/unparseable bytes — raises `HistoryFetchError`, which `main.py` handles exactly like a Hetzner feed failure: abort the cycle, keep the last published snapshot, retry next cycle. Conflating the two would have been a silent data-loss bug: a transient network blip would otherwise discard every prior cycle's accumulated history and publish a `config_history.parquet` containing only that cycle's data.

The percentile lands as three columns on `current_snapshot.parquet` (`price_percentile_vs_history`, `price_per_benchmark_point_single_percentile_vs_history`, `price_per_benchmark_point_multi_percentile_vs_history` — identical values per listing, per the "one histogram serves all three metrics" note below, but kept independent per ADR-3's stance) plus `is_all_time_low`, `history_sample_size`, and `history_cohort_fallback` (surfaced in the UI so a thin-data percentile isn't presented with false confidence). The client only ever reads these precomputed columns — never `config_history.parquet` itself, consistent with What It Is NOT's no-client-side-join stance.

The original 2026-08-12 design, kept for reference:

**Storage: `config_history.parquet`, one row per config signature** (CPU model + RAM + disk layout + datacenter — not `listing_id`, since the same effective config reappears under a new listing ID every auction cycle):

```
config_signature                    (key)
first_observed_at
last_observed_at
total_observations                  (sum of price_histogram counts)
min_price_effective_monthly         (all-time-low, kept explicit for O(1) lookup)
price_histogram: LIST<STRUCT<price_effective_monthly: int, observation_count: int>>
```

A price histogram, not a raw per-tick log: for each config, `(price → count)` rather than one row per observation. Storage is bounded by *distinct prices a config has ever sold at*, not by tick count — Hetzner reuses price points for the same recurring hardware, so this compresses far better than an ever-growing raw log, and never needs a compaction step, because there's nothing per-tick accumulating to compact.

**No new storage technology needed.** The pipeline is stateless per run, but doesn't need to be for this: each cycle it fetches the currently-live `config_history.parquet` back over HTTP before publishing — the same fetch-back-before-republish pattern `web_fetcher.py` already uses for `web/`, and ADR-7's code-deploy build-command already uses for `current_snapshot.parquet`/`unmatched-cpus.json`. It updates the fetched table in memory (find-or-create each current listing's `(config_signature, price)` histogram entry, increment its count, bump `last_observed_at`, lower `min_price_effective_monthly` if applicable), rewrites the whole file, and deploys it alongside `current_snapshot.parquet` in the same `wrangler pages deploy` call. No Hive-partitioning: Cloudflare Pages Direct Upload re-uploads the entire deploy directory every cycle regardless of file count (ADR-7 — "every deploy is a complete new deployment"), so splitting history into partition files buys nothing here the way it would have under R2's per-object writes; one accumulating file is simpler and no worse.

**Percentile derivation** — rank a current listing's price against its config's histogram:

```
percentile = sum(count for price, count in histogram if price >= current_price) / total_observations
```

i.e. "cheaper than N% of every time this exact config has appeared." An "at/near all-time-low" badge is this stat's 0th-percentile special case, not a separate computation.

**One histogram serves all three value metrics, not three.** `config_signature` fixes the CPU model, which fixes `single_thread_score`/`multi_thread_score` for every observation sharing that signature — so `price_per_benchmark_point_multi` and `_single` are just `price_effective_monthly` divided by a per-config constant. Dividing by a positive constant doesn't change rank order, so the percentile computed from raw price *is* the percentile for both derived metrics too — consistent with ADR-3 (single/multi never blended), the three percentiles are still reported as independent output columns, they're just derived from one shared rank rather than three separately-maintained histograms.

**Where the result lands:** computed server-side, once per cycle, and written as a plain derived column directly onto each listing's row in `current_snapshot.parquet` (e.g. `price_percentile_vs_history`) — consistent with the existing no-client-side-join principle (see What It Is NOT): the browser reads a precomputed number, it never queries `config_history.parquet` itself to produce the badge. That file exists purely as the pipeline's own persistent input across cycles.

**Cohort fallback still applies:** below some minimum `total_observations` (exact threshold TBD once real data volume is known), a config's own histogram is too thin to rank against meaningfully — fall back to the broader CPU-family cohort instead of showing a percentile off 1–2 data points.

**Known blind spot, confirmed by observation 2026-08-12:** the pipeline only samples every 10 minutes, so a listing that appears and is taken again within one tick is invisible to this design entirely — never recorded in `config_history.parquet`, never flagged by the percentile badge, regardless of how good the price was. Real instances of this have already been observed (configs available for only a couple of minutes before disappearing) — those are exactly the deals this feature is least able to catch, since it's a sampling-cadence problem upstream of any storage/index design, not something a smarter schema fixes. Closing this gap would mean polling more often than 10 minutes, which has its own cost under the current architecture (ADR-7: every cycle re-uploads the full Pages deployment).

**Decision (2026-08-12): keep the 10-minute cadence, accept the blind spot.** Two reasons: (1) usage here isn't continuous real-time monitoring, so missing a deal that's gone in under 10 minutes is a real but acceptable cost, not a functional failure; (2) polling meaningfully faster risks Hetzner rate-limiting or blocking the fetcher outright, which would break the whole pipeline, not just this feature — a worse outcome than the blind spot it would fix. **Invalidation trigger:** revisit only if usage shifts toward wanting to catch flash deals in real time — and if it does, treat it as its own scoped change (fetch interval, its rate-limit risk, and its Pages-redeploy-cost tradeoff), not something to bolt onto the percentile feature.

### Historical stats: velocity, listing lifetime, market-level trends — still open, storage not yet designed

Not covered by `config_history.parquet` above — that structure deliberately drops the *time* dimension (it's a cumulative count per price, not a per-tick timeline), which is fine for percentile/all-time-low but can't answer anything that needs to know *when* an observation happened:

- **Price velocity** — average price drop per snapshot tick while a specific listing is live. Needs per-tick data keyed by `listing_id` (not `config_signature` — this is about one listing's own lifetime, the opposite key choice from the percentile feature above), sorted `(listing_id, fetched_at)`.
- **Listing lifetime** — how many ticks a listing survives before disappearing. Same `listing_id`-keyed timeline as velocity, and subject to the same 10-minute sampling blind spot above — a listing that lives under one tick reads identically to a listing that was never posted at all.
- **Market-level trends** — rolling median/percentile of `price_per_benchmark_point_multi` over a trailing window, listing volume over time by CPU family/datacenter/RAM tier/disk type, AMD vs. Intel value trend, benchmark coverage rate over time. All need a time axis across the whole market, not a per-config cumulative count.

If these get built, they need their own storage decision — most likely a genuine per-tick or per-day time-indexed structure (not a reuse of `config_history.parquet`'s histogram, which has no time axis to query against). Revisit when actually scoped rather than inheriting a design built for a different question.

### Other candidates

- Performance-normalized alerting ("notify when €/PassMark drops below X") — depends on the historical-stats work above.
- Multi-source benchmark cross-validation (Geekbench/YABS — see Benchmark Strategy's v2 candidate for the specific sources) to verify and extend PassMark coverage.
- Optional user-adjustable single-/multi-thread blend as a convenience column — v1 already exposes `price_per_benchmark_point_single` and `price_per_benchmark_point_multi` as independent, separately sortable columns (see ADR-3 and Benchmark Strategy), so this candidate is only about an opt-in weighted combination for users who want one number, never a replacement for the two separate metrics.
- Comparison/side-by-side view.
- Browser extension overlaying benchmark scores directly on Hetzner's own auction page — unexplored by any tool found in research.

## Open Questions

- ~~Pipeline implementation language/runtime (affects which Parquet-writer library is available — see Architecture's Format-verification note and Testing Strategy's Parquet/DuckDB-WASM conformance test, Phase 3 — and what the containerized `pipeline/` component, per Components, is built with) — final choice TBD. **Resolve before Phase 1** (the fetcher itself is Phase 1's deliverable, and it has to be written in something).~~ **RESOLVED (2026-08-02)**: Python 3.11+ with pyarrow. See `docs/notes/had-1i3.md` for full rationale.
- ~~Which Rackspace Spot cluster hosts the pipeline — any is viable since the dataset regenerates on its own cadence and nothing is stateful; final choice TBD. **Resolve before Phase 6** (deployment phase needs a target cluster).~~ **RESOLVED (2026-08-03)**: iad-ci cluster. See `notes/had-307-cluster-deployment.md` for full rationale including existing infrastructure patterns and GitOps consistency.

## Risk Register

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R1 | CPU-matching produces false-positive matches (wrong benchmark score attached to a listing) | Medium | High | The manual override list and unmatched-CPU report (see Benchmark Strategy) only ever cover zero-match and ambiguous-match cases (EC-3) — by construction neither can surface a confident-but-wrong match, since that `cpu_raw` string resolved successfully. Actual false-positive protection is the CPU-matching fixture set's near-miss adversarial-pair category (Testing Strategy): real, similarly-named-but-distinct CPU models asserted to never cross-match each other |
| R2 | Cloudflare Pages outage | Low | Medium | Pipeline aborts and retries next cycle (Pipeline Run Lifecycle); dashboard keeps serving the last snapshot it already loaded client-side until Cloudflare Pages recovers |
| R3 | Hetzner changes the auction feed's format without notice | Medium | High | Pipeline fails closed on parse error (EC-2), keeps serving the last snapshot, logs the raw payload for a quick manual fix |
| R4 | DuckDB-WASM hits a scale ceiling as Hetzner's auction volume grows | Low | Medium | See Performance Ceiling — fallback is server-side pre-aggregation or narrower default filters |
| R5 | Concurrent pipeline writers corrupt the live Parquet file during a rolling redeploy | Low | High | Mitigated structurally by `replicas: 1` + Cloudflare Pages' own atomic deployment promotion (Concurrency Model) — last deploy promoted wins, no partial writes ever visible |
| R6 | PassMark coverage stays persistently sparse despite a maturing override list — some listings never get a benchmark score | Medium | Medium | Unmatched CPUs are surfaced, never guessed (Benchmark Strategy): NULL score, explicit "unscored" flag, sorted to the top, never blended or dropped — so this fails safely rather than silently, unlike R1. Each run's `unmatched-cpus.json` includes affected-listing counts per unresolved CPU, so overrides can be ranked highest-volume-first from it (see Plan B) — no sorted-output requirement on the pipeline itself, just the counts to rank by. If coverage stays thin after the override list has had a real chance to mature, ADR-2's invalidation trigger promotes the Geekbench/YABS v2 cross-validation candidate ahead of schedule |

## Plan B / Fallback Strategies

- **If DuckDB-WASM + Cloudflare Pages range requests turns out too slow at real scale** (R4): fall back to a thin server-side API doing the pre-filtering Hetzner-side, trading away some of the "no backend at request time" simplicity for scale headroom — the Parquet schema (Data Models) stays the contract either way, just read by a small service instead of directly by the browser.
- **If PassMark coverage stays too sparse after the override list has matured** (R6; see also ADR-2's invalidation trigger): prioritize manual overrides for the highest-volume unmatched CPUs first, ranked by each CPU's affected-listing count in the current `unmatched-cpus.json` report (see Benchmark Strategy), rather than broadening to multi-source matching before it's actually needed.
- **If Cloudflare Pages has a sustained outage** (risk R2): no separate plan needed — retry-next-cycle and last-known-good serving (Pipeline Run Lifecycle) are already the fallback.
