# had-1o9: Component rollup - unmatched-cpus.json

**Status**: Completed (rollup task)

## Summary

This is a rollup/reference bead for the `unmatched-cpus.json` component. The actual implementation was completed in child tasks:
- `had-3kz` - Build unmatched-CPU report generator (Phase 2) - **closed (P2)**
- `had-5bi` - Build the publish lifecycle (temp-key-then-swap) (Phase 4) - **closed (P2)**

## Component Delivered

The `unmatched-cpus.json` report is generated and published to R2 every pipeline cycle:

### Files Delivered
- `pipeline/src/pipeline/unmatched_reporter.py` - Unmatched CPU tracking and report generation
- `pipeline/src/pipeline/r2_publisher.py` - R2 publishing with temp-key-then-swap lifecycle
- `pipeline/tests/test_unmatched_reporter.py` - Comprehensive test suite (9 tests)
- `pipeline/validate_phase2_unmatched_reporter.py` - Validation script

### Report Structure
```json
{
  "generated_at": "2026-08-02T19:55:13.797299+00:00",
  "total_unmatched_cpus": 3,
  "unmatched_cpus": [
    {
      "cpu_raw": "Unknown CPU Model",
      "affected_count": 5,
      "first_seen_at": "2026-08-02T19:55:13.797299+00:00",
      "sample_listing_ids": ["listing-1", "listing-2", ...]
    }
  ]
}
```

### Functionality
- **Tracking**: Collects all unmatched CPU strings from auction listings
- **Counting**: Tracks number of listings affected per unmatched CPU
- **Sorting**: Reports sorted by affected_count descending (highest-impact gaps first)
- **Publishing**: Atomic R2 updates via temp-key-then-swap lifecycle
- **Integration**: Full workflow from fetch → match → track → generate → publish

## Integration

The component is integrated into the main pipeline (`pipeline/src/pipeline/main.py`):

1. **During processing** (lines 153-155):
   - Each listing's CPU match result is processed
   - Unmatched CPUs are tracked with affected counts
   - Sample listing IDs collected (max 5 per CPU)

2. **After Parquet publish** (lines 189-209):
   - Generate `unmatched-cpus.json` report
   - Publish to R2 with atomic temp-key-then-swap
   - Clean up temporary files
   - Reset reporter for next cycle

## R2 Publishing Details

The R2 publisher (`r2_publisher.py`) provides:

- **`publish_json_report()`** method specifically for `unmatched-cpus.json`
- **Temp-key-then-swap lifecycle**: Write to `.tmp/unmatched-cpus.json.tmp`, verify, then atomic swap
- **Verification**: Validates JSON structure, size, and Cache-Control headers
- **Cache-Control**: Sets `max-age=60` per ADR-4 (well under 10-minute publish cadence)
- **Error handling**: On failure, aborts without touching live key (last published report stays served)

## Maintenance Workflow

The report supports ongoing benchmark-map maintenance:

1. **Identify gaps**: Review `unmatched-cpus.json` for high-impact unmatched CPUs
2. **Add mappings**: Update `benchmark-map/aliases.csv` or `overrides.csv` with new entries
3. **Validate**: Run pipeline to verify CPUs now match
4. **Confirm**: Next report shows reduced unmatched count

See `benchmark-map/README.md` for detailed maintenance procedures.

## Completion Reference

- Phase 2: `notes/had-3kz.md` - Build unmatched-CPU report generator
- Phase 4: R2 publishing integrated in `had-5bi` - temp-key-then-swap lifecycle
- Main integration: `pipeline/src/pipeline/main.py` lines 33, 93, 153-155, 189-209, 232

## Compliance with Requirements

✅ **Phase 2**: Unmatched-CPU reporting with correct JSON structure  
✅ **Phase 4**: R2 publishing with atomic temp-key-then-swap lifecycle  
✅ **Integration**: Full workflow from generation to publishing  
✅ **Maintenance**: Supports benchmark-map maintenance workflow  
✅ **Testing**: Comprehensive test coverage (9 passing tests)  
✅ **Documentation**: Clear structure and usage guidance
