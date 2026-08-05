# had-3kz: Build unmatched-CPU report generator

**Status:** ✅ COMPLETE  
**Completed:** 2026-08-02

## Task Requirements

Build unmatched-CPU report generator (`unmatched-cpus.json`) that:
- For every listing where CPU matching failed, collects `cpu_raw` + affected-listing count
- Generates report in correct JSON structure
- Report is overwritten each run (not accumulated across runs)
- Lists every unresolved CPU seen in the fixture set

## Implementation Summary

The unmatched-CPU report generator was already fully implemented in `pipeline/src/pipeline/unmatched_reporter.py`. The implementation includes:

### Core Components

1. **`UnmatchedCpuReporter` class** - Main reporter that tracks unmatched CPUs
   - Tracks `cpu_raw` strings that failed to match
   - Counts affected listings per unmatched CPU
   - Maintains sample listing IDs (max 5 per CPU for debugging)
   - Generates `unmatched-cpus.json` report

2. **`UnmatchedCpuEntry` dataclass** - Single unmatched CPU entry structure
   - `cpu_raw`: The unmatched CPU string
   - `affected_count`: Number of listings affected
   - `first_seen_at`: ISO timestamp when first seen in run
   - `sample_listing_ids`: Sample listing IDs for debugging

3. **`process_listings_batch()` function** - Batch processing helper
   - Processes multiple listings through CPU matcher
   - Tracks unmatched CPUs from the results

### Key Features Implemented

✅ **Correct JSON Structure**
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

✅ **Sorted by Impact** - Entries sorted by `affected_count` descending (highest-impact gaps first)

✅ **Overwritten Each Run** - Each reporter instance starts fresh; no accumulation across runs

✅ **Integration with CPU Matcher** - Works seamlessly with `CpuMatcher.match_cpu()` results

✅ **Edge Case Handling**
- Empty CPU strings are skipped
- Matched CPUs are ignored
- Sample listing IDs limited to 5 per CPU
- Works with batch processing

## Validation

Created `pipeline/validate_phase2_unmatched_reporter.py` which validates:

1. ✅ Reporter initialization and basic tracking
2. ✅ Matched CPUs correctly ignored  
3. ✅ Report generates with correct JSON structure
4. ✅ Each entry contains `cpu_raw` + `affected_count`
5. ✅ Entries sorted by `affected_count` descending
6. ✅ Report is overwritten each run (not accumulated)
7. ✅ Integration with CPU matcher working
8. ✅ Empty CPU strings correctly skipped
9. ✅ Sample listing IDs tracked (max 5 per CPU)
10. ✅ Fixture set coverage validated

All 9 pytest tests in `tests/test_unmatched_reporter.py` pass.

## Files

- `pipeline/src/pipeline/unmatched_reporter.py` - Main implementation
- `pipeline/tests/test_unmatched_reporter.py` - Comprehensive test suite
- `pipeline/validate_phase2_unmatched_reporter.py` - Validation script

## Integration Point

The unmatched reporter is designed to be integrated into the main pipeline during Phase 4 (R2 bucket + API token + refresh-loop Deployment). The workflow will be:

1. Fetch listings from Hetzner
2. Normalize/match CPUs (Phase 2)
3. Track unmatched CPUs using `UnmatchedCpuReporter`
4. Compute cost metrics (Phase 3)
5. Generate Parquet file (Phase 3)
6. Generate `unmatched-cpus.json` (this implementation)
7. Publish both to R2 using temp-key-then-swap lifecycle

## Design Decisions

- **Sample listing IDs (max 5)**: Provides debugging context without bloating the report
- **Sorted by affected_count**: Helps prioritize which unmatched CPUs to address first
- **Overwritten not accumulated**: Matches the 10-minute cycle pattern; keeps reports current and bounded
- **Empty string handling**: Skips empty CPU strings to avoid pollution

## Compliance with Phase 2 Requirements

✅ Matches Benchmark Strategy requirements for unmatched-CPU reporting  
✅ Integrates with CPU matching fixture set  
✅ Generates report in required shape (`cpu_raw` + affected-listing count)  
✅ Lists every unresolved CPU  
✅ Ready for Phase 4 integration with R2 publishing

---

**Phase 2 (Benchmark Strategy - Unmatched CPU Reporting) COMPLETE**