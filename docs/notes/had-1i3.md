# Pipeline Implementation Language Decision (had-1i3)

**Decision**: Python 3.11+ for the pipeline implementation

**Date**: 2026-08-02

## Chosen Stack

- **Language**: Python 3.11+
- **Parquet writer**: `pyarrow`
- **HTTP client**: `httpx` (async support for future flexibility)
- **R2/S3 client**: `boto3` (s3client)
- **Container base**: `python:3.11-slim` or `python:3.11-alpine`

## Rationale

### 1. Parquet/DuckDB-WASM Compatibility (Critical Path)

The plan's Phase 3 requires a conformance test: "the chosen Parquet writer's output is confirmed compatible with DuckDB-WASM's httpfs range-request reads." `pyarrow` is the gold standard for Parquet writing and has proven compatibility with DuckDB-WASM. This is the primary technical driver.

### 2. Data Manipulation Strength

The pipeline workload is naturally suited to Python:
- CPU name normalization and fuzzy matching
- String operations and alias table lookups
- Derived metric computation (price_per_benchmark_point_*, price_per_gb_ram, price_per_tb_disk)
- List/struct operations for the variable disk configuration representation

Python's readability for these operations reduces maintenance burden for a solo project.

### 3. HTTP and Cloudflare R2 Integration

Well-established, battle-tested libraries:
- `httpx`: Modern HTTP client with async support for future optimization potential
- `boto3`: AWS S3 SDK with Cloudflare R2 S3 compatibility (standard pattern in this environment)

### 4. Containerization and GitOps Integration

- Standard Python containerization patterns: predictable builds, small base images
- Clean integration with existing declarative-config workflow
- Standard Python packaging (pyproject.toml) integrates cleanly with container builds

### 5. Maintainability for Solo Project

- Large ecosystem means common problems have known solutions
- Readability reduces long-term maintenance burden
- Type hints (Python 3.11+) provide sufficient safety for this scale without the complexity of stricter type systems

## Alternatives Considered

### Rust
- **Pros**: Strong performance, type safety, small binary size
- **Cons**: Parquet ecosystem less mature than pyarrow; overkill for 10-minute cadence where Python is sufficient
- **Verdict**: Rejected - PyArrow compatibility and ecosystem maturity favor Python

### Go
- **Pros**: Good containerization, strong HTTP support
- **Cons**: Parquet libraries less mature; data manipulation patterns less natural than Python for this workload
- **Verdict**: Rejected - Ecosystem maturity and data manipulation patterns favor Python

### TypeScript/Node
- **Pros**: Familiar for web-focused projects, good HTTP libraries
- **Cons**: Parquet libraries lack pyarrow's ecosystem maturity; less proven DuckDB-WASM compatibility
- **Verdict**: Rejected - Parquet/DuckDB-WASM compatibility confidence favors Python

## Implementation Notes

- Use Python 3.11+ for modern typing and performance improvements
- `pyarrow` for Parquet writing (confirmed compatible before Phase 3 completion)
- `httpx` for HTTP client (async support leaves optimization headroom)
- `boto3` for R2 operations (standard S3-compatible pattern)
- Type hints throughout for maintainability
- Standard Python packaging (pyproject.toml) for dependency management

## Impact on Other Phases

- **Phase 1**: Fetcher implementation uses this stack
- **Phase 3**: Parquet writer uses `pyarrow`; conformance test validates DuckDB-WASM compatibility
- **Phase 4**: Container deployment uses standard Python containerization patterns
- **Phase 5**: Client (static HTML/JS) is unaffected - this decision only affects the pipeline

## References

- Plan: `/home/coding/hetzner-auction-dashboard/docs/plan/plan.md`
- Open Question: "Pipeline implementation language/runtime" (marked "Resolve before Phase 1")
- Phase 3 completion criteria: "Parquet writer's output passes the DuckDB-WASM httpfs conformance test"
