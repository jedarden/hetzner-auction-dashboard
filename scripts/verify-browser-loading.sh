#!/bin/bash
# Verification script for had-3opn: Browser data loading verification
# This script checks the readiness of browser-based DuckDB-WASM + Parquet loading

set -euo pipefail

echo "================================================"
echo "Browser Data Loading Verification (had-3opn)"
echo "================================================"
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Track results
PASSED=0
FAILED=0
WARNINGS=0

pass() {
    echo -e "${GREEN}✓${NC} $1"
    ((PASSED++))
}

fail() {
    echo -e "${RED}✗${NC} $1"
    ((FAILED++))
}

warn() {
    echo -e "${YELLOW}⚠${NC} $1"
    ((WARNINGS++))
}

info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

echo "1. Checking file existence..."
echo "----------------------------------------------"

# Check conformance test files
if [ -f "test_output/conformance/conformance_test.html" ]; then
    SIZE=$(wc -c < test_output/conformance/conformance_test.html)
    pass "conformance_test.html exists ($SIZE bytes)"
else
    fail "conformance_test.html missing"
fi

if [ -f "test_output/conformance/conformance_test.parquet" ]; then
    SIZE=$(wc -c < test_output/conformance/conformance_test.parquet)
    pass "conformance_test.parquet exists ($SIZE bytes)"
else
    fail "conformance_test.parquet missing"
fi

# Check main dashboard
if [ -f "web/index.html" ]; then
    SIZE=$(wc -c < web/index.html)
    pass "web/index.html exists ($SIZE bytes)"
else
    fail "web/index.html missing"
fi

echo ""
echo "2. Checking for placeholder values..."
echo "----------------------------------------------"

PLACEHOLDER_COUNT=$(grep -r 'example\.com|TODO|FIXME|Replace with' web/ --include='*.html' --include='*.js' --include='*.ts' 2>/dev/null | wc -l || echo "0")
if [ "$PLACEHOLDER_COUNT" -eq 0 ]; then
    pass "No placeholder URLs found (example.com, TODO, FIXME, Replace with)"
else
    fail "Found $PLACEHOLDER_COUNT placeholder values"
    grep -r 'example\.com|TODO|FIXME|Replace with' web/ --include='*.html' --include='*.js' --include='*.ts' | head -5
fi

echo ""
echo "3. Checking USE_REAL_DATA configuration..."
echo "----------------------------------------------"

USE_REAL_DATA_COUNT=$(grep "const USE_REAL_DATA = false" web/index.html 2>/dev/null | wc -l || echo "0")

if [ "$USE_REAL_DATA_COUNT" -eq 0 ]; then
    pass "USE_REAL_DATA is enabled (no 'false' flags found)"
else
    warn "USE_REAL_DATA is DISABLED ($USE_REAL_DATA_COUNT occurrences of 'false')"
    grep -n "const USE_REAL_DATA" web/index.html
fi

echo ""
echo "4. Checking Parquet URL configuration..."
echo "----------------------------------------------"

PARQUET_URL=$(grep -oP 'const parquetUrl = .*\K[^\s]+' web/index.html 2>/dev/null || echo "")
if [ -n "$PARQUET_URL" ]; then
    if [[ "$PARQUET_URL" == "/current_snapshot.parquet" ]]; then
        pass "Parquet URL is same-origin: $PARQUET_URL (ADR-7 compliant)"
    else
        if [[ "$PARQUET_URL" =~ example\.com ]]; then
            fail "Parquet URL is placeholder: $PARQUET_URL"
        else
            warn "Parquet URL is non-standard: $PARQUET_URL"
        fi
    fi
else
    fail "Could not find Parquet URL configuration"
fi

echo ""
echo "5. Checking DuckDB-WASM import configuration..."
echo "----------------------------------------------"

DUCKDB_URL=$(grep -oP "import\('https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@\K[^\+]+" web/index.html 2>/dev/null || echo "")
if [ -n "$DUCKDB_URL" ]; then
    pass "DuckDB-WASM CDN URL configured: $DUCKDB_URL"
else
    warn "Could not find DuckDB-WASM import URL"
fi

echo ""
echo "6. Checking httpfs extension loading..."
echo "----------------------------------------------"

HTTPFS_COUNT=$(grep -c "INSTALL httpfs" web/index.html 2>/dev/null || echo "0")
if [ "$HTTPFS_COUNT" -gt 0 ]; then
    pass "httpfs extension is configured for Parquet loading"
else
    warn "httpfs extension not found in configuration"
fi

echo ""
echo "================================================"
echo "Summary"
echo "================================================"
echo -e "${GREEN}Passed:${NC} $PASSED"
echo -e "${YELLOW}Warnings:${NC} $WARNINGS"
echo -e "${RED}Failed:${NC} $FAILED"
echo ""

if [ $FAILED -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo -e "${GREEN}✓ All checks passed! Ready for browser testing.${NC}"
    exit 0
elif [ $FAILED -eq 0 ]; then
    echo -e "${YELLOW}⚠ Checks passed with warnings. Review warnings above.${NC}"
    exit 0
else
    echo -e "${RED}✗ Some checks failed. Review failures above.${NC}"
    exit 1
fi
