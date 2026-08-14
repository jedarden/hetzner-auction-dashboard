# Browser Test Plan: had-3opn Verification

## Objective
Verify that DuckDB-WASM actually loads real Parquet data in a browser environment.

## Test Environment Setup

### Method 1: Python HTTP Server (Quick Test)
```bash
cd /home/coding/hetzner-auction-dashboard

# Test conformance test
python3 -m http.server 8080 --directory test_output/conformance &
# Open: http://localhost:8080/conformance_test.html

# Test main dashboard
python3 -m http.server 8081 --directory web &
# Open: http://localhost:8081/index.html
```

### Method 2: Cloudflare Pages Deploy (Production Test)
Once had-3080 deploy lands, test at:
- Main: `https://hetzner-auction-dashboard.pages.dev`
- Should load `/current_snapshot.parquet` via same-origin request

## Test 1: Conformance Test (Phase 3)

### File Location
`test_output/conformance/conformance_test.html`

### How to Run
1. Start HTTP server: `python3 -m http.server 8080 --directory test_output/conformance`
2. Open browser: `http://localhost:8080/conformance_test.html`
3. Click "Run Conformance Tests" button

### Expected Results
All 8 tests should pass:
1. ✅ Load DuckDB-WASM from CDN
2. ✅ Register httpfs extension
3. ✅ Load conformance_test.parquet via httpfs
4. ✅ Verify schema (23 columns)
5. ✅ Verify row count (4 rows)
6. ✅ NULL handling for unmatched CPUs
7. ✅ Complex types (disks array)
8. ✅ Derived metrics calculation

### Success Criteria
- Green status: "✅ All conformance tests passed! (8/8)"
- "Run Sample Queries" button becomes enabled
- No browser console errors

### Failure Indicators
- Red status showing test failures
- Browser console errors about DuckDB-WASM loading
- CORS errors when loading Parquet file
- Timeout errors (>30s for DuckDB-WASM CDN load)

## Test 2: Main Dashboard (Phase 5)

### Current State
⚠️ **BLOCKER**: `USE_REAL_DATA = false` in `web/index.html` (lines 2384, 2430)

The dashboard has all the correct code infrastructure:
- DuckDB-WASM loading ✅
- httpfs extension ✅
- Parquet URL `/current_snapshot.parquet` ✅
- Error handling ✅
- Data staleness tracking ✅

BUT real data loading is **disabled by configuration flag**.

### How to Test (After Fix)

#### Step 1: Enable Real Data Loading
Edit `web/index.html`:
```javascript
// Line 2384: Change from:
const USE_REAL_DATA = false;
// To:
const USE_REAL_DATA = true;

// Line 2430: Change from:
const USE_REAL_DATA = false;
// To:
const USE_REAL_DATA = true;
```

#### Step 2: Serve and Test
```bash
# Create a test Parquet file (or use production current_snapshot.parquet)
cd /home/coding/hetzner-auction-dashboard
python3 -m http.server 8081 --directory web &
# Open: http://localhost:8081/index.html
```

#### Expected Results
1. **Loading State**: Blue spinner with "Loading auction data from Parquet files..."
2. **Success**: Dashboard displays real auction data
3. **Console Logs**:
   - "Loading DuckDB-WASM..."
   - "DuckDB initialized successfully"
   - "httpfs extension loaded"
   - "Loading Parquet data..."
   - "Loaded X listings from Parquet"
   - "Data loading complete in Xms"
4. **Staleness Indicator**: Shows actual timestamp from data

#### Failure Modes
| Error | Symptom | Fix |
|-------|---------|-----|
| DuckDB-WASM load timeout | Red error after 30s | Check network, CDN access |
| Parquet fetch failed | Red error, no data | Verify `/current_snapshot.parquet` exists |
| CORS blocked | Red error, "CORS policy" | Ensure same-origin serving |
| Memory limit exceeded | Browser tab crashes | Close other tabs |

### Success Criteria for Production Deploy

After had-3080 deploy, test at `https://hetzner-auction-dashboard.pages.dev`:

1. ✅ Page loads without errors
2. ✅ Data staleness shows recent timestamp (not "Unknown")
3. ✅ Listings display (not blank, not mock data)
4. ✅ Filters and sorting work
5. ✅ Best Deal button shows actual listing (not mock Ryzen 9 5950X)

## Verification Checklist

### Before Closing This Bead
- [ ] Test `conformance_test.html` in real browser
- [ ] Verify all 8 tests pass
- [ ] Document findings in notes/had-3opn-findings.md
- [ ] Commit findings and test plan

### After had-3080 Deploy
- [ ] Change `USE_REAL_DATA = true` in web/index.html
- [ ] Test live site at hetzner-auction-dashboard.pages.dev
- [ ] Verify real Parquet data loads (not mock data)
- [ ] Check data staleness indicator shows actual timestamp
- [ ] Confirm listings show real auction data

### Production Readiness
- [ ] Add pre-deploy check for `USE_REAL_DATA = true` in CI
- [ ] Add error tracking (Sentry/etc.) for DuckDB-WASM failures
- [ ] Add uptime monitoring for dashboard functionality
- [ ] Document rollback procedure if real data loading fails

## Quick Test Commands

```bash
# Verify files exist
ls -la test_output/conformance/
ls -la web/

# Quick syntax check (no runtime errors)
grep -n "USE_REAL_DATA" web/index.html

# Verify no placeholder URLs
grep -r 'example\.com|TODO|FIXME|Replace with' web/ --include='*.html' --include='*.js'

# Start test server
python3 -m http.server 8080 --directory test_output/conformance &
echo "Open: http://localhost:8080/conformance_test.html"
```

## Critical Path Summary

1. **Immediate**: Browser-test conformance_test.html → proves DuckDB-WASM works
2. **Before Deploy**: Flip USE_REAL_DATA flag → enables real data loading
3. **After Deploy**: Test live site → confirms production data flow

## Risk Assessment

**Current Risk**: HIGH
- Dashboard appears complete but is hardcoded to mock data
- Production deploy would show fake listings to users
- No automated checks prevent USE_REAL_DATA=false in production

**Mitigation**:
1. Browser-test conformance test to prove DuckDB-WASM actually works
2. Add CI guard to prevent USE_REAL_DATA=false in production
3. Manual verification on first production deploy

---

**Status**: Awaiting manual browser test of conformance_test.html
**Next Action**: Run conformance test in browser to verify DuckDB-WASM functionality
