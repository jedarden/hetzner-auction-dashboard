# Ideas Ledger

Tracks every idea considered by `/plan-idea-gen` runs against `docs/plan/plan.md`, so future runs can dedupe. Each run is a dated section below.

---

## Run: 2026-08-02

**Pool:** 97 ideas across 8 lenses. **Triaged to:** 33 survivors (11 clusters). **After crossover/consolidation:** 30. **Advanced to kill pass:** 15 (max 2/cluster). **Killed:** 2. **Completeness-gap batch:** 4 generated, 2 survived kill pass. **Final candidate pool:** 15. **Selected finalists:** 10.

### Lens 1 — Invert the problem
- Budget allocator (multi-listing spend optimization) — merged with Capacity-first search at crossover → **Multi-listing optimizer** — advanced, cut in final cross-cluster trim (lost to User-selectable-primary-axis on cost/leverage).
- Sell-signal mode (notify on strictly-better replacement for owned box) — survived triage, survived kill pass (weakly — most speculative new user-flow), **cut in final 10** (narrower use case than Cross-link-Hetzner-Cloud).
- Anti-recommendation "hall of shame" — CUT at triage: redundant, value metric already makes bad deals visually obvious.
- Reverse auction simulator (drop-probability) — merged with Comps/appraisal view at crossover → Comps-grounded simulator — advanced, **cut in final cross-cluster trim** (fully v2-history-dependent, low near-term buildability).
- Fair-price baseline (absolute deviation framing) — CUT at triage: reframing of the plan's own already-planned value-percentile stat.
- Cross-link to Hetzner's own Cloud catalog — survived triage, won Cluster 4 pairwise, survived kill pass. **FINALIST.**
- Buyer's remorse checker — CUT at triage: low practical value, one-off curiosity.
- Capacity-first search (bin-packing to a target) — merged into Multi-listing optimizer, see above.
- User-selectable primary axis (RAM/disk-first sort) — survived triage, won Cluster 1 pairwise, survived kill pass. **FINALIST.**
- Losing bidder's feed — CUT at triage: marginal value, needs history it doesn't have in v1.
- Reverse geography/latency-zone picker — CUT at triage: marginal over existing datacenter filter.
- Deprecation countdown (forecast future supply) — CUT at triage: speculative, needs a whole separate data source.

### Lens 2 — Adjacent-domain transplant
- Fare-calendar heatmap — CUT at triage: a chart-type choice over data v2 already plans, not a new mechanism.
- Comps/appraisal view — merged into Comps-grounded simulator (see Lens 1), cut in final trim.
- Rarity tiers — CUT at triage: redundant reframing of existing percentile/coverage data.
- Draft board (live session shortlist) — survived triage, won Cluster 5 pairwise slot, **KILLED at adversarial pass**: too thin a distinction from Saved presets relative to its added UI complexity.
- Unit-price shelf tag — CUT at triage: a styling choice, not a distinct feature.
- Technical-indicator overlay (moving avg/Bollinger bands) — CUT at triage: niche, real complexity for marginal insight beyond planned stats.
- Match-score badge (user-weighted % match) — CUT at triage: real risk of violating ADR-3's anti-blending stance.
- Substitution suggestions — CUT at triage: feature creep, marginal value over re-filtering.
- Hopper-style buy/wait predictor — survived triage, lost Cluster 1 pairwise to Starter-configs+button, cut before final 10.
- Roster/build slots (named saved profiles) — merged into Saved/named filter presets at triage (near-duplicate mechanism).
- Weather-radar heat map — CUT at triage: redundant with datacenter filter + planned volume stats.
- "Surprise me" discovery button — CUT at triage: low value for a personal deal-hunting tool.

### Lens 3 — Remove a constraint
- Public read-only recommendation API — CUT at triage: the published Parquet file already is this surface.
- Opt-in blended "quick glance" score — CUT at triage: duplicates the plan's own existing v2 "optional blend" candidate.
- Multi-source benchmarking as v1 default — CUT at triage: directly contradicts ADR-2 (generated to test/kill it; ADR-2 held).
- Backfill history from third-party archives — survived triage, won Cluster 9 pairwise slot, cut in final cross-cluster trim (speculative, no confirmed data source per research).
- Community-maintained benchmark-map via public PRs — survived triage, won Cluster 9 pairwise, survived kill pass (flagged: scope down to lightweight PR-acceptance only). **FINALIST.**
- Static-rendered SEO/shareability page — CUT at triage: marginal value, project has no stated interest in public discovery growth.
- One-click order via Hetzner Robot API — CUT at triage: directly contradicts What It Is NOT (generated to test/kill it; held).
- Multi-provider beyond Hetzner — CUT at triage: dramatically increases maintenance burden against "no enterprise ceremony."
- Sub-minute push instead of poll — CUT at triage: infeasible without Hetzner's cooperation.
- Native mobile app/PWA — CUT at triage: disproportionate engineering vs. the already-responsive static site.
- Monetized/paid tier — CUT at triage: contradicts What It Is NOT's non-commercial framing (generated to test/kill it; held).
- Offline-capable via Service Worker cache — survived triage, lost Cluster 9 pairwise to Community-benchmark-map, cut before final 10.

### Lens 4 — 10x cheaper/simpler version
- No pipeline — client-side raw fetch + build-time benchmark JSON — CUT at triage: directly violates the plan's core client-side-join prohibition (generated to test/kill it; held).
- Single scheduled Job instead of Deployment — CUT at triage: directly violates the "no Job/CronJob" house rule (generated to test/kill it; held).
- CSV instead of Parquet — CUT at triage: loses the core arbitrary-SQL value prop DuckDB-WASM was chosen for.
- Single static HTML, no framework — survived triage, won Cluster 8 pairwise, survived kill pass. **FINALIST.**
- Manual weekly benchmark-map spot-checks, skip automation — CUT at triage: undermines the plan's own "coverage must stay visible/automated" principle.
- Laptop-script month 1 — survived triage, won Cluster 8 pairwise slot, survived kill pass. **FINALIST.**
- Exact-string-match only, no fuzzy matching — CUT at triage: regresses the carefully-designed Benchmark Strategy matching approach.
- Sort-only client, no filter UI — CUT at triage: filter UI is already core, carefully-scoped v1 requirement.
- Reuse Server Radar's existing data instead of building a pipeline — CUT at triage: no confirmed public export/API exists per research; unhealthy third-party dependency.
- Spreadsheet + manual paste, zero code — CUT at triage: a validation exercise, not a product idea.
- Manual "refresh now" trigger instead of always-on loop — CUT at triage: works against the automated-cadence value prop that's core, not incidental.
- Tailscale-only serving, skip Cloudflare — survived triage, lost Cluster 8 pairwise (contradicts an already-settled ADR-1-adjacent hosting decision), cut before final 10.
- Trim v0 filter set to price+RAM+CPU-family only — CUT at triage: the full filter list is already tightly and reasonably scoped.

### Lens 5 — Power-user workflow
- Saved filter presets via URL-encoded state — survived triage (merged with Roster/build slots), won Cluster 5 pairwise, survived kill pass. **FINALIST.**
- Keyboard-driven navigation (vim-style) — CUT at triage: generic UI polish, not differentiating.
- Raw SQL console mode — survived triage, lost Cluster 6 pairwise to Publish-schema-public-surface, cut before final 10.
- CLI companion tool (direct DuckDB query) — survived triage, won Cluster 6 pairwise slot, cut in final cross-cluster trim (niche for a genuinely single-user project).
- Export current filtered view to CSV — CUT at triage: minor convenience, not worth separate tracking.
- Multi-profile split-view comparison — CUT at triage: real UI complexity for modest gain over switching between saved presets.
- Command palette (Cmd-K style) — CUT at triage: generic UX polish, low priority for v1-adjacent scope.
- Configurable/reorderable columns — CUT at triage: marginal, most users use the default set.
- Client-side-only notes/tags on watched listings — survived triage, lost Cluster 5 pairwise, cut before final 10.
- Diff view between two snapshots — survived triage, won Cluster 3 pairwise, survived kill pass. **FINALIST.**
- Bookmarklet for one-click reopen — CUT at triage: minor convenience, an implementation detail of saved presets.
- RSS/Atom feed of top-N by value — survived triage, lost Cluster 6 pairwise, cut before final 10.

### Lens 6 — Failure-mode/reliability-driven
- Self-check `/status` page — survived triage, lost Cluster 10 pairwise to Canary-snapshot, cut before final 10.
- Dead-man's-switch external monitor — survived triage, merged with Self-check page at crossover → Unified health-check endpoint, lost Cluster 10 pairwise, cut before final 10.
- Benchmark-map schema-drift detection (PassMark source changes) — CUT at triage: speculative, premature before Phase 2 reveals actual integration shape.
- Canary/staging snapshot + sanity check — survived triage, merged with Outlier/sanity-band alerting, won Cluster 10 pairwise, survived kill pass. **FINALIST.**
- Benchmark-map rollback process — CUT at triage: git itself already provides this (git-tracked per Components).
- Outlier/sanity-band alerting — merged into Canary/staging snapshot, see above.
- R2 cost/usage tripwire — survived triage, lost Cluster 10 pairwise, cut before final 10.
- Cloudflare Pages regional-outage awareness — CUT at triage: a documentation gap, not a distinct feature.
- Fixture-set coverage-regression tracking — CUT at triage: marginal value relative to build effort for a solo maintainer.
- Chaos-test the atomic swap — survived triage, lost Cluster 10 pairwise, cut before final 10.
- Back up benchmark-map + v2 history to B2 via ARMOR — survived triage, won Cluster 10 pairwise slot (narrowed to v2-history-only per kill pass — benchmark-map is already git-backed), cut in final cross-cluster trim (v2-history-dependent, low near-term urgency).
- Idempotency check across retried runs — CUT at triage: narrow edge case, existing abort-and-retry design already handles it adequately.

### Lens 7 — Novice-user/intuitiveness
- Plain-language value tooltip — merged with Glossary tooltips at triage → Explainer tooltips, won Cluster 7 pairwise slot, cut in final cross-cluster trim.
- Guided onboarding quiz — merged into Starter-configs idea (Lens 1) at triage.
- Traffic-light deal labels — CUT at triage: real risk of violating ADR-3's anti-blending stance, redundant with the numeric percentile.
- "What is a Hetzner auction?" explainer page — CUT at triage: lost pairwise to Explainer tooltips and Marketplace banner.
- Glossary tooltips for jargon — merged into Explainer tooltips, see above.
- Cost-of-ownership context — CUT at triage: redundant with Cross-link-Hetzner-Cloud (both make price feel concrete via comparison).
- Mobile-first simplified view — CUT at triage: scope creep beyond an already-responsive static site.
- "This isn't a marketplace" clarity banner — survived triage, won Cluster 7 pairwise, survived kill pass, **cut in final cross-cluster trim** (weakest-justified — no evidenced need, per kill pass).
- Visual capacity meters — CUT at triage: a styling choice, not a distinct feature.
- "vs. buying new" cost context — CUT at triage: folds into Cross-link-Hetzner-Cloud.
- Single "just show me one good deal" button — merged into Starter-configs idea, see Lens 1. **FINALIST** (as part of merged idea).

### Lens 8 — What would a competitor ship first
- Pull price-trend ahead of benchmark maturity — CUT at triage: argues against the plan's own well-reasoned, research-backed strategic bet with no new evidence.
- Ship a minimal 2-listing compare view early — CUT at triage: plan already explicitly excludes this from v1 with clear reasoning; no new information to justify moving it up.
- Decouple simple threshold alerts from historical-stats dependency — survived triage, advanced, **KILLED at adversarial pass**: directly contradicts an explicit, already-reasoned v1/v2 boundary in What It Is NOT with no new evidence strong enough to override it.
- Publish a versioned, documented Parquet schema as a public data surface — survived triage, won Cluster 6 pairwise, survived kill pass, **cut in final cross-cluster trim** (lowest urgency of the access-surface ideas).
- Position as a free Apify-alternative — CUT at triage: marketing framing, not a distinct implementation idea.
- Test a lightweight deal-quality label — CUT at triage: same ADR-3 risk as Traffic-light labels/Match-score badge.
- Snapshot-only AMD/Intel + datacenter stat pages — survived triage, lost Cluster 4 pairwise to Cross-link-Hetzner-Cloud, cut before final 10.
- Telegram/Discord bot access surface — CUT at triage: real engineering investment against "no enterprise ceremony"; solved territory elsewhere per research.
- Add non-auction Hetzner standard listings too — CUT at triage: meaningful scope expansion already substantially covered by the cheaper Cross-link-Hetzner-Cloud idea.
- Per-user customizable poll/alert cadence — CUT at triage: depends on an alerting system not yet scoped.
- Auto-expire stale saved filters after 90 days — CUT at triage: a UX detail, not tracked as its own idea (folds into Saved presets if ever needed).
- Explicit zero-setup, no-login commitment — CUT at triage: a positioning statement already implicit in the architecture, not a distinct feature.

### Completeness-gap round (targeted batch)
Gap identified: nothing in the survivor set gave the plan's own Scenario 4 "Adoption" success metric any actual tooling — it was a personal vibe-check with zero support.
- Deals-acted-on log (manual retroactive marking) — generated, triaged out: strictly worse than the automatic-capture variant below.
- Click-through value capture (automatic, at the moment of clicking through to Hetzner) — survived triage, survived kill pass. **FINALIST.**
- Monthly self-review prompt (in-page banner) — survived triage, survived kill pass (narrowly — flagged as a "nagging banner" risk), **cut in final selection** (Click-through capture covers the more valuable, less-annoying half of the same gap).
- Time-since-last-visit nudge — CUT at triage: redundant with Monthly self-review prompt's same underlying signal.

---

### Final 10 (this run)

1. Starter-configs + one-click "best deal now" button
2. User-selectable primary sort axis (RAM/disk-first mode)
3. Diff view between two snapshots
4. Cross-link to Hetzner's own Cloud catalog pricing
5. Saved/named filter presets (URL-encoded)
6. Single static HTML page, no framework
7. Laptop-script month 1
8. Community-maintained benchmark-map (lightweight PR acceptance)
9. Canary/staging snapshot with an outlier sanity-check
10. Click-through value capture

### Adoption decision

- **Adopted:** 1, 2, 3, 4 — beads `had-4ct`, `had-1vp`, `had-33l`, `had-39b` (P2), added to `docs/plan/plan.md`'s new "v1.1 — Adopted Idea-Gen Additions" section.
- **Adopted, deprioritized:** 5 (Saved/named filter presets) — bead `had-2ua` (P3), explicitly built after 1-4.
- **Adopted, resolves an Open Question:** 6 (Single static HTML, no framework) — no bead (it's a settled decision, not a build task); folded directly into Architecture > Client, with an explicit clarifying note that it decides frontend rendering only — the pipeline/backend that generates the Parquet data is unchanged. Removed the now-resolved "Frontend framework choice" line from Open Questions.
- **Rejected:** 7 (Laptop-script month 1) — user directive: set up the cluster container and GitOps work up front instead; this just confirms the plan's existing Phase 4/6 approach, no plan change needed.
- **Rejected:** 8 (Community-maintained benchmark-map), 9 (Canary/staging snapshot), 10 (Click-through value capture) — no plan change; remain available to resurface in a future idea-gen run if new reasoning changes the calculus.
