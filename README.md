# hetzner-auction-dashboard

Client-side dashboard for Hetzner server auction listings — a scheduled pipeline pre-joins auction data with CPU benchmark scores and cost-per-metric fields into a Parquet file; the static frontend uses DuckDB-WASM purely for search/filtering (no client-side joins). Deployable to any Rackspace Spot cluster since the dataset regenerates on its own cadence.

## Structure

- `docs/notes/` — features, constraints, design decisions
- `docs/research/` — external reference material and prior art
- `docs/plan/plan.md` — complete application plan
