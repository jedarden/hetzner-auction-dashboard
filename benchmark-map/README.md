# Benchmark Map

This directory contains the CPU benchmark reference data and matching logic for the Hetzner auction dashboard.

## Files

### `reference.csv`
Main PassMark reference table with the following columns:
- `passmark_id`: PassMark CPU ID
- `cpu_model`: Canonical CPU model name
- `single_thread_score`: PassMark single-thread benchmark score
- `multi_thread_score`: PassMark multi-thread benchmark score

This is the source of truth for all benchmark data. Add new CPUs here as they appear in Hetzner's auction feed.

### `aliases.csv`
Maps common CPU name variants to canonical model names.

Columns:
- `raw_pattern`: The CPU string pattern as it might appear in Hetzner listings
- `canonical_model`: The canonical model name that exists in `reference.csv`

Add entries here for different naming conventions (spacing, capitalization, missing prefixes/suffixes).

### `overrides.csv`
Manual override list for CPU strings that cannot be matched through normal alias patterns.

Columns:
- `raw_cpu`: Exact CPU string from Hetzner feed
- `passmark_id`: Direct PassMark ID mapping (bypasses normal matching)
- `notes`: Explanation for why this override exists

Use this for:
- Very unusual spellings that don't fit alias patterns
- CPUs with ambiguous generation information
- Temporary fixes while investigating proper classification

## Matching Priority

The pipeline uses a cascading match approach:

1. **Direct match**: Check if `raw_cpu` exactly matches a canonical model in `reference.csv`
2. **Alias match**: Try to find `raw_cpu` in `aliases.csv` and use the mapped canonical model
3. **Override match**: Check `overrides.csv` for exact `raw_cpu` match
4. **No match**: `benchmark_matched = false`, CPU added to `unmatched-cpus.json`

## Maintenance

This is the highest-maintenance artifact in the repo (see `docs/notes/benchmark-priority.md`).

When updating:
1. Check `unmatched-cpus.json` from the latest pipeline run
2. Identify high-volume unmatched CPUs (by affected-listing count)
3. Add appropriate entries to either `aliases.csv` or `overrides.csv`
4. Test against the CPU-matching fixture set in `pipeline/tests/`

## Adding New CPUs

1. Find the CPU on [PassMark](https://www.cpubenchmark.net/)
2. Get the PassMark ID and benchmark scores
3. Add to `reference.csv` with canonical model name
4. Add any known Hetzner listing variants to `aliases.csv`
5. Run the CPU-matching test suite to verify

## Risk Register R1 Defense

The CPU-matching fixture set (`pipeline/tests/test_cpu_matching.py`) includes:
- Known-tricky same-chip variants (must resolve to one correct match)
- Intentionally-unmatchable strings (must produce `benchmark_matched = false`)
- **Near-miss adversarial pairs** (similar but distinct CPUs must never cross-match)

This is the primary defense against false-positive matches. When adding new entries, verify they don't create cross-match opportunities with existing entries.
