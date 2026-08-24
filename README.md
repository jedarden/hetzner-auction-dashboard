# hetzner-auction-dashboard

Client-side dashboard for Hetzner server auction listings. A polling pipeline
pre-joins auction data with CPU benchmark scores and cost metrics, publishes
versioned Parquet generations to Garage, and atomically advances a small
manifest. The independently deployed Cloudflare Pages interface resolves that
manifest and uses DuckDB-WASM for local search/filtering without client-side
joins or a request-time application backend.

The accepted target architecture is documented in
[`docs/architecture.md`](docs/architecture.md). The repository is migrating
from its original model, which bundled fresh data into a complete Cloudflare
Pages deployment every ten minutes.

## Structure

- `docs/notes/` — features, constraints, design decisions
- `docs/research/` — external reference material and prior art
- `docs/plan/plan.md` — complete application plan
- `docs/architecture.md` — canonical deployment and data-publication architecture

---

Part of [jedarden.com](https://jedarden.com)

*This GitHub repo is a read-only mirror of git.ardenone.com/jedarden/hetzner-auction-dashboard — issues and PRs are welcome here either way.*
