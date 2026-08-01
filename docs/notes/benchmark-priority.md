# Benchmark score is the point of this project

The CPU benchmark score is the single most important field in this dashboard — it's the reason this tool exists instead of just using Hetzner's own auction page.

- Price, RAM, and disk are already visible on Hetzner's own listing page. A benchmark score, normalized and joined onto every listing, is not — that join is the one piece of real work this project does. Everything else is UI over data Hetzner already publishes.
- Default sort and the dashboard's framing of "value" should center on price-per-benchmark-point, not raw price. Two listings at the same price with very different CPU performance are not equivalent, and raw-price sorting hides that.
- The benchmark join happens entirely in the pipeline, precomputed into the Parquet file (see `docs/plan/plan.md`). The client never computes or looks up benchmark data at query time — it only filters/sorts on the already-joined column. Do not reintroduce a client-side lookup or join for this.
- Practical implication for maintenance: an incomplete or wrong CPU-to-benchmark match undermines the dashboard's core value more than any UI gap would. The `benchmark-map/` override list is the highest-priority thing to keep accurate — treat drift there as a bug, not a nice-to-have.
