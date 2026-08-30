# hetzner-auction-dashboard

Find a Hetzner auction server by filtering current listings on CPU performance,
memory, storage, location, and price. Search and filtering run in the browser;
there is no request-time application backend.

**Live:** [hetzner-auction-dashboard.pages.dev](https://hetzner-auction-dashboard.pages.dev)

The polling pipeline pre-joins Hetzner listings with CPU benchmark scores and
cost metrics, publishes versioned Parquet generations, and atomically advances
a small manifest. The browser loads the current generation into DuckDB-WASM.

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
