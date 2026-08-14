# Browser Testing Plan for had-3opn

## Task Overview
Verify web/index.html actually loads real Parquet data in a browser.

## Current Status Assessment

### ✅ Completed Checks
1. **Placeholder URL Fix**: Confirmed that `web/index.html` now correctly points to `/current_snapshot.parquet` instead of the old placeholder URL (https://auction-data.example.com/listings.parquet)
2. **No Additional Placeholders**: Grep search found no other 'example.com', 'TODO', 'FIXME', or 'Replace with' placeholders in the web/ directory
3. **Conformance Test Files Exist**: Both `test_output/conformance/conformance_test.html` and `conformance_test.parquet` are present

### ❌ Pending Verification
1. **Live Site Not Deployed**: https://hetzner-auction-dashboard.pages.dev returns 000 (site not accessible)
   - This is expected: had-3080 (deployment bead) is blocked on dependencies (had-3ur5, had-39ks, had-1qcb)
2. **No Browser Testing Performed Yet**: No recorded evidence that conformance_test.html was actually opened in a browser and confirmed to work

## Test Setup

### Local Server
A local HTTP server is running to serve the files for browser testing:
- **URL**: http://localhost:8765/
- **Test File**: http://localhost:8765/test_output/conformance/conformance_test.html
- **Server PID**: 1092914

### Tailscale Access
The "bench" machine is available on the Tailscale network:
- **Tailscale IP**: 100.67.12.56
- **Status**: active, direct connection
- **Usage**: This machine should have a browser for manual testing

## Acceptance Criteria Testing

### 1. Conformance Test (test_output/conformance/conformance_test.html)

**URL**: http://localhost:8765/test_output/conformance/conformance_test.html

**Expected Behavior**:
1. Open the file in a browser (Chrome/Firefox/Safari 90+)
2. Click "🚀 Run Conformance Tests" button
3. Wait for DuckDB-WASM to load from CDN
4. Verify all 8 tests pass:
   - Test 1: Load DuckDB-WASM
   - Test 2: Register httpfs extension
   - Test 3: Load Parquet file via httpfs
   - Test 4: Verify Schema (all expected columns present)
   - Test 5: Verify Row Count (4 rows expected)
   - Test 6: NULL Handling (conformance-3 has NULL passmark_id)
   - Test 7: Complex Types (conformance-2 has 2 disk entries)
   - Test 8: Derived Metrics (price calculations correct)

**Success Criteria**:
- Status shows: "✅ All conformance tests passed! (8/8)"
- "📊 Run Sample Queries" button becomes enabled
- No console errors (F12 → Console tab)
- DuckDB-WASM loads successfully from CDN
- Parquet file loads via httpfs range requests

**Failure Indicators**:
- Any test fails with red "❌ Test Failed" status
- Console shows CORS errors, WASM load failures, or network errors
- Tests hang indefinitely (timeout issues)

### 2. Live Site Testing (BLOCKED - had-3080 not completed)

**URL**: https://hetzner-auction-dashboard.pages.dev/

**Expected Behavior** (once deployed):
1. Open live site in browser
2. Dashboard loads without console errors
3. Real Parquet data displays from /current_snapshot.parquet
4. Staleness indicator shows recent timestamp
5. Filters and sorting work correctly
6. "Best Deal Now" button functions

**Success Criteria**:
- No fallback mode message ("Using Sample Data")
- Results show real auction listings (not mock data)
- Network tab shows successful /current_snapshot.parquet fetch
- No CORS errors (same-origin now per ADR-7)

**Current Blocker**:
- had-3080 is blocked on had-3ur5, had-39ks, had-1qcb
- Cloudflare Pages project not yet created/deployed
- No live /current_snapshot.parquet file available

## Manual Testing Instructions

### For Testing on Bench Machine (100.67.12.56)

1. **SSH to bench**:
   ```bash
   ssh bench@100.67.12.56
   ```

2. **Open browser and navigate to**:
   ```
   http://<this-server-ip>:8765/test_output/conformance/conformance_test.html
   ```

3. **Run the conformance tests**:
   - Click "🚀 Run Conformance Tests"
   - Monitor console for errors (F12)
   - Verify all 8 tests pass
   - Screenshot the final "All conformance tests passed" state

4. **Test sample queries**:
   - Click "📊 Run Sample Queries" (enabled after tests pass)
   - Verify SQL queries return expected results

### Alternative: Direct Tailscale URL

If the local server is accessible via Tailscale:
```
http://<tailscale-ip>:8765/test_output/conformance/conformance_test.html
```

## Code Analysis Results

### Conformance Test HTML Structure
The conformance test file contains:
- ✅ Proper DuckDB-WASM CDN loading code
- ✅ httpfs extension registration for HTTP range requests
- ✅ Comprehensive schema validation (all 24 expected columns)
- ✅ NULL handling tests for unmatched CPUs
- ✅ Complex type tests (disks list of structs)
- ✅ Derived metrics verification
- ✅ Sample SQL queries matching dashboard patterns

### web/index.html Parquet Loading
The main dashboard file contains:
- ✅ Correct Parquet URL: `/current_snapshot.parquet` (line ~2145)
- ✅ DuckDB-WASM loading with error handling
- ✅ Fallback to mock data if real data fails to load
- ✅ Staleness tracking and refresh countdown
- ✅ CORS error handling and detailed error states
- ✅ Memory limit and timeout error handling

## Summary

**What's Done**:
- Code structure is correct for both files
- Placeholder URLs have been fixed
- Conformance test file is properly generated
- Local server is running for browser testing

**What's Missing**:
- **Critical**: No browser has actually opened and tested these files yet
- **Critical**: Live site is not deployed (had-3080 blocked)
- **Important**: No screenshots or test execution logs exist

**Next Steps**:
1. Test conformance_test.html in a real browser on bench machine
2. Wait for had-3080 to complete, then test live site
3. Document test results with screenshots and console logs
4. Update this bead with verification evidence

## Files for Browser Testing

- **Conformance Test**: http://localhost:8765/test_output/conformance/conformance_test.html
- **Main Dashboard**: http://localhost:8765/web/index.html (will use mock data as fallback)
- **Test Data**: http://localhost:8765/test_output/conformance/conformance_test.parquet

Server is ready for browser testing. Proceed with manual verification on bench machine.