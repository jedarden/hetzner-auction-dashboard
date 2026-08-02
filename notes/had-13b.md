# had-13b: Component rollup - benchmark-map/

**Status**: Completed (rollup task)

## Summary

This is a rollup/reference bead for the `benchmark-map/` component. The actual implementation was completed in child task `had-1r3` (closed).

## Component Delivered

The `benchmark-map/` directory contains the complete CPU benchmark reference system:

### Files Delivered
- `reference.csv` - 72 CPU entries with PassMark IDs and benchmark scores
- `aliases.csv` - 103 CPU name variant mappings for fuzzy matching
- `overrides.csv` - 9 manual override entries for edge cases
- `README.md` - Comprehensive documentation

### Functionality
- Raw CPU string → normalized model name → PassMark ID resolution
- Cascading match approach: direct match → alias match → override match → unmatched
- Supports the pipeline's CPU matching logic with proper defense against false positives
- Includes comprehensive documentation for ongoing maintenance

## Integration

This component is used by:
- The pipeline's CPU matching logic (Phase 2)
- The CPU-matching fixture set for testing
- The unmatched-CPU reporting system

## Maintenance Notes

As noted in `docs/notes/benchmark-priority.md`, this is the highest-maintenance artifact in the repo. The component includes:
- Clear documentation on adding new CPUs
- Maintenance workflow using `unmatched-cpus.json`
- Risk register defense via near-miss adversarial test pairs

## Completion Reference

See commit `40af877` - "Build benchmark-map reference table + CPU-name matching/alias/override logic (had-1r3)"
