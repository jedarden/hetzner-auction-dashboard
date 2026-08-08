"""
DuckDB-WASM Conformance Test

Generates a sample Parquet file and creates an HTML test file to verify
DuckDB-WASM can load and query the Parquet writer's output via httpfs range requests.

This is the critical round-trip test that validates Phase 3 completion:
the Parquet writer's output must actually be consumable by DuckDB-WASM.

Run this test to generate the conformance test HTML file, then open the
HTML file in a browser to verify the conformance test passes.
"""

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pipeline.cpu_matcher import BenchmarkMatch
from pipeline.enricher import CostMetricsEnricher, EnrichedListing
from pipeline.fetcher import DiskSpec, RawListing
from pipeline.parquet_writer import ParquetWriter


def generate_sample_listings() -> list[EnrichedListing]:
    """Generate a diverse set of sample listings for conformance testing."""
    enricher = CostMetricsEnricher()

    # Sample 1: Matched CPU with NVMe disks
    raw1 = RawListing(
        listing_id="conformance-1",
        datacenter="FSN1-DC3",
        location="FSN",
        available_from="2026-08-02T12:00:00Z",
        cpu_raw="Intel Xeon E5-2680 v4",
        ram_gb=64,
        ram_ecc=True,
        disks=[DiskSpec(type="NVMe", count=2, capacity_gb=480)],
        uplink_speed=1000,
        price_base=2999,  # €29.99
        price_setup_fee=4999,  # €49.99
        fetched_at=datetime.now(UTC),
    )
    cpu1 = BenchmarkMatch(
        cpu_raw="Intel Xeon E5-2680 v4",
        matched=True,
        cpu_normalized="Intel Xeon E5-2680 v4",
        passmark_id=2680,
        single_thread_score=1500,
        multi_thread_score=8000,
        cores=None,
        threads=None,
        match_method="direct",
    )

    # Sample 2: Matched CPU with mixed disks
    raw2 = RawListing(
        listing_id="conformance-2",
        datacenter="NBG1-DC1",
        location="NBG",
        available_from=None,  # Immediately available
        cpu_raw="AMD Ryzen 5950X",
        ram_gb=128,
        ram_ecc=False,
        disks=[
            DiskSpec(type="NVMe", count=2, capacity_gb=1000),
            DiskSpec(type="HDD", count=4, capacity_gb=4096),
        ],
        uplink_speed=1000,
        price_base=5999,  # €59.99
        price_setup_fee=0,
        fetched_at=datetime.now(UTC),
    )
    cpu2 = BenchmarkMatch(
        cpu_raw="AMD Ryzen 5950X",
        matched=True,
        cpu_normalized="AMD Ryzen 9 5950X",
        passmark_id=3456,
        single_thread_score=2000,
        multi_thread_score=16000,
        cores=None,
        threads=None,
        match_method="direct",
    )

    # Sample 3: Unmatched CPU (benchmark metrics should be NULL)
    raw3 = RawListing(
        listing_id="conformance-3",
        datacenter="HEL1-DC1",
        location="HEL",
        available_from="2026-08-02T18:00:00Z",
        cpu_raw="Unknown CPU Model XYZ",
        ram_gb=32,
        ram_ecc=True,
        disks=[DiskSpec(type="SSD", count=2, capacity_gb=512)],
        uplink_speed=1000,
        price_base=1999,  # €19.99
        price_setup_fee=0,
        fetched_at=datetime.now(UTC),
    )
    cpu3 = BenchmarkMatch(
        cpu_raw="Unknown CPU Model XYZ",
        matched=False,
        cpu_normalized=None,
        passmark_id=None,
        single_thread_score=None,
        multi_thread_score=None,
        cores=None,
        threads=None,
        match_method=None,
    )

    # Sample 4: Edge case - zero RAM (price_per_gb_ram should be NULL)
    raw4 = RawListing(
        listing_id="conformance-4",
        datacenter="FSN1-DC3",
        location="FSN",
        available_from="2026-08-02T15:00:00Z",
        cpu_raw="Intel Xeon E5-2660 v2",
        ram_gb=0,  # Edge case
        ram_ecc=True,
        disks=[DiskSpec(type="HDD", count=4, capacity_gb=2048)],
        uplink_speed=1000,
        price_base=999,  # €9.99
        price_setup_fee=0,
        fetched_at=datetime.now(UTC),
    )
    cpu4 = BenchmarkMatch(
        cpu_raw="Intel Xeon E5-2660 v2",
        matched=True,
        cpu_normalized="Intel Xeon E5-2660 v2",
        passmark_id=2100,
        single_thread_score=1200,
        multi_thread_score=6000,
        cores=None,
        threads=None,
        match_method="direct",
    )

    return [
        enricher.enrich_listing(raw1, cpu1),
        enricher.enrich_listing(raw2, cpu2),
        enricher.enrich_listing(raw3, cpu3),
        enricher.enrich_listing(raw4, cpu4),
    ]


def generate_conformance_test_html(parquet_path: str, output_path: str) -> None:
    """
    Generate an HTML file that tests DuckDB-WASM conformance with the Parquet file.

    Args:
        parquet_path: Path to the generated Parquet file (relative to HTML)
        output_path: Path where the HTML test file will be written
    """
    # Use format() instead of f-string to avoid conflicts with JavaScript curly braces
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Phase 3 Conformance Test: DuckDB-WASM + Parquet Writer</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }}
        .container {{
            background: white;
            border-radius: 8px;
            padding: 24px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #333;
            border-bottom: 2px solid #007bff;
            padding-bottom: 12px;
        }}
        .status {{
            padding: 12px 16px;
            border-radius: 6px;
            margin: 16px 0;
            font-weight: 500;
        }}
        .status.passed {{
            background: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }}
        .status.failed {{
            background: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }}
        .status.pending {{
            background: #fff3cd;
            color: #856404;
            border: 1px solid #ffeeba;
        }}
        .test-section {{
            margin: 24px 0;
            padding: 16px;
            background: #f8f9fa;
            border-radius: 6px;
        }}
        .test-title {{
            font-weight: 600;
            color: #495057;
            margin-bottom: 8px;
        }}
        .query-result {{
            background: white;
            padding: 12px;
            border-radius: 4px;
            font-family: "Monaco", "Menlo", monospace;
            font-size: 13px;
            overflow-x: auto;
            margin-top: 8px;
            border: 1px solid #dee2e6;
        }}
        button {{
            background: #007bff;
            color: white;
            border: none;
            padding: 12px 24px;
            border-radius: 6px;
            font-size: 16px;
            cursor: pointer;
            margin: 8px 0;
        }}
        button:hover {{
            background: #0056b3;
        }}
        button:disabled {{
            background: #6c757d;
            cursor: not-allowed;
        }}
        .summary {{
            margin-top: 24px;
            padding: 16px;
            background: #e7f5ff;
            border-radius: 6px;
            border-left: 4px solid #007bff;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🧪 Phase 3 Conformance Test: DuckDB-WASM + Parquet Writer</h1>

        <div id="overall-status" class="status pending">
            ⏳ Ready to run conformance tests
        </div>

        <button id="run-tests" onclick="runConformanceTests()">🚀 Run Conformance Tests</button>
        <button id="run-queries" onclick="runSampleQueries()" disabled>📊 Run Sample Queries</button>

        <div class="summary">
            <strong>Test Overview:</strong>
            <ul>
                <li>✅ Load DuckDB-WASM from CDN</li>
                <li>✅ Register httpfs extension for HTTP range requests</li>
                <li>✅ Load Parquet file via httpfs (simulating R2 bucket read)</li>
                <li>✅ Verify schema matches Data Models specification</li>
                <li>✅ Test NULL handling for unmatched CPUs</li>
                <li>✅ Test complex types (disks list of structs)</li>
                <li>✅ Execute sample SQL queries matching client dashboard patterns</li>
            </ul>
        </div>

        <div id="test-results"></div>

        <div id="query-results" style="display: none;">
            <h2>📊 Sample Query Results</h2>
            <div class="test-section">
                <div class="test-title">Query 1: All listings, sorted by price_per_benchmark_point_multi</div>
                <div id="query1-result"></div>
            </div>
            <div class="test-section">
                <div class="test-title">Query 2: Filter by benchmark_matched = true</div>
                <div id="query2-result"></div>
            </div>
            <div class="test-section">
                <div class="test-title">Query 3: Aggregate metrics (avg price per GB RAM)</div>
                <div id="query3-result"></div>
            </div>
        </div>
    </div>

    <script type="module">
        let db = null;
        let conformancePassed = false;

        async function initDuckDB() {{
            const duckdb = await import('https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.28.0/+esm');
            const logger = new duckdb.consoleLogger(1);
            const browser = new duckdb.browser('', logger);

            await browser.instantiate({{
                path: 'duckdb.wasm',
            }});

            const conn = await browser.connect();

            db = {{
                query: async (queryStr) => {{
                    const result = await conn.query(queryStr);
                    return result;
                }}
            }};
            return db;
        }}

        async function runConformanceTests() {{
            const resultsDiv = document.getElementById('test-results');
            const statusDiv = document.getElementById('overall-status');
            const runButton = document.getElementById('run-tests');
            const runQueriesButton = document.getElementById('run-queries');

            runButton.disabled = true;
            statusDiv.className = 'status pending';
            statusDiv.textContent = '🔄 Running conformance tests...';
            resultsDiv.innerHTML = '';

            let passed = 0;
            let failed = 0;

            try {{
                // Test 1: Load DuckDB-WASM
                addTestSection('Test 1: Load DuckDB-WASM', 'Loading DuckDB-WASM from CDN...');
                await initDuckDB();
                markTestPass('Test 1: Load DuckDB-WASM');
                passed++;

                // Test 2: Register httpfs extension
                addTestSection('Test 2: Register httpfs', 'Registering httpfs extension for HTTP range requests...');
                await db.query("INSTALL httpfs");
                await db.query("LOAD httpfs");
                markTestPass('Test 2: Register httpfs');
                passed++;

                // Test 3: Load Parquet file
                addTestSection('Test 3: Load Parquet via httpfs', 'Loading Parquet file from local filesystem...');
                await db.query(`
                    CREATE TABLE listings AS
                    SELECT * FROM read_parquet('""" + parquet_path + """')
                `);
                markTestPass('Test 3: Load Parquet via httpfs');
                passed++;

                // Test 4: Verify schema
                addTestSection('Test 4: Verify Schema', 'Checking schema columns...');
                const schemaResult = await db.query("DESCRIBE listings");
                const schemaRows = schemaResult.toArray();
                const expectedColumns = [
                    'listing_id', 'datacenter', 'location', 'available_from',
                    'cpu_raw', 'cpu_normalized', 'benchmark_matched',
                    'passmark_id', 'single_thread_score', 'multi_thread_score',
                    'benchmark_match_method', 'ram_gb', 'ram_ecc', 'uplink_speed',
                    'price_base', 'price_setup_fee', 'price_effective_monthly',
                    'price_per_benchmark_point_single', 'price_per_benchmark_point_multi',
                    'price_per_gb_ram', 'price_per_tb_disk', 'fetched_at', 'disks'
                ];

                const actualColumns = schemaRows.map(row => row.column_name);
                const missing = expectedColumns.filter(col => !actualColumns.includes(col));
                const extra = actualColumns.filter(col => !expectedColumns.includes(col));

                if (missing.length > 0 || extra.length > 0) {{
                    throw new Error(`Schema mismatch! Missing: ${{missing.join(', ')}}, Extra: ${{extra.join(', ')}}`);
                }}
                markTestPass('Test 4: Verify Schema');
                passed++;

                // Test 5: Verify row count
                addTestSection('Test 5: Verify Row Count', 'Checking row count...');
                const countResult = await db.query("SELECT COUNT(*) as count FROM listings");
                const count = countResult.toArray()[0].count;
                if (count !== 4) {{
                    throw new Error(`Expected 4 rows, got ${{count}}`);
                }}
                markTestPass('Test 5: Verify Row Count');
                passed++;

                // Test 6: Test NULL handling for unmatched CPUs
                addTestSection('Test 6: NULL Handling', 'Verifying NULL values for unmatched CPUs...');
                const nullTestResult = await db.query(`
                    SELECT listing_id, benchmark_matched, passmark_id,
                           price_per_benchmark_point_multi
                    FROM listings
                    WHERE listing_id = 'conformance-3'
                `);
                const nullTestRow = nullTestResult.toArray()[0];

                if (nullTestRow.benchmark_matched !== false) {{
                    throw new Error('conformance-3 should have benchmark_matched = false');
                }}
                if (nullTestRow.passmark_id !== null) {{
                    throw new Error('conformance-3 should have NULL passmark_id');
                }}
                if (nullTestRow.price_per_benchmark_point_multi !== null) {{
                    throw new Error('conformance-3 should have NULL price_per_benchmark_point_multi');
                }}
                markTestPass('Test 6: NULL Handling');
                passed++;

                // Test 7: Test complex types (disks)
                addTestSection('Test 7: Complex Types', 'Verifying disks list of structs...');
                const disksTestResult = await db.query(`
                    SELECT listing_id, disks
                    FROM listings
                    WHERE listing_id = 'conformance-2'
                `);
                const disksRow = disksTestResult.toArray()[0];

                if (!disksRow.disks || disksRow.disks.length !== 2) {{
                    throw new Error('conformance-2 should have 2 disk entries');
                }}
                markTestPass('Test 7: Complex Types');
                passed++;

                // Test 8: Test derived metrics calculation
                addTestSection('Test 8: Derived Metrics', 'Verifying derived cost metrics...');
                const metricsResult = await db.query(`
                    SELECT listing_id, price_effective_monthly, price_per_gb_ram,
                           price_per_benchmark_point_multi
                    FROM listings
                    WHERE listing_id = 'conformance-1'
                `);
                const metricsRow = metricsResult.toArray()[0];

                // price_effective_monthly = 2999 + 4999 = 7998
                if (metricsRow.price_effective_monthly !== 7998) {{
                    throw new Error(`Expected price_effective_monthly = 7998, got ${{metricsRow.price_effective_monthly}}`);
                }}
                // price_per_gb_ram = 7998 / 64 ≈ 124.97
                if (Math.abs(metricsRow.price_per_gb_ram - 124.97) > 0.1) {{
                    throw new Error(`price_per_gb_ram calculation incorrect: ${{metricsRow.price_per_gb_ram}}`);
                }}
                markTestPass('Test 8: Derived Metrics');
                passed++;

                // All tests passed!
                conformancePassed = true;
                statusDiv.className = 'status passed';
                statusDiv.textContent = `✅ All conformance tests passed! (8/8)`;
                runQueriesButton.disabled = false;

            }} catch (error) {{
                failed++;
                const errorDiv = document.createElement('div');
                errorDiv.className = 'status failed';
                errorDiv.textContent = `❌ Test failed: ${{error.message}}`;
                resultsDiv.appendChild(errorDiv);

                statusDiv.className = 'status failed';
                statusDiv.textContent = `❌ Conformance tests failed (${{passed}} passed, ${{failed}} failed)`;
                runButton.disabled = false;
            }}
        }}

        async function runSampleQueries() {{
            const queryResultsDiv = document.getElementById('query-results');
            queryResultsDiv.style.display = 'block';

            // Query 1: All listings sorted by value
            const q1Result = await db.query(`
                SELECT listing_id, cpu_raw, ram_gb, price_effective_monthly,
                       price_per_benchmark_point_multi
                FROM listings
                ORDER BY price_per_benchmark_point_multi ASC NULLS FIRST
            `);
            displayQueryResult('query1-result', q1Result);

            // Query 2: Filter by benchmark_matched
            const q2Result = await db.query(`
                SELECT listing_id, cpu_raw, price_per_benchmark_point_multi
                FROM listings
                WHERE benchmark_matched = true
                ORDER BY price_per_benchmark_point_multi ASC
            `);
            displayQueryResult('query2-result', q2Result);

            // Query 3: Aggregate metrics
            const q3Result = await db.query(`
                SELECT
                    COUNT(*) as total_listings,
                    AVG(price_per_gb_ram) as avg_price_per_gb_ram,
                    AVG(price_effective_monthly) as avg_monthly_price
                FROM listings
                WHERE benchmark_matched = true
            `);
            displayQueryResult('query3-result', q3Result);
        }}

        function addTestSection(title, message) {{
            const resultsDiv = document.getElementById('test-results');
            const section = document.createElement('div');
            section.className = 'test-section';
            section.innerHTML = `<div class="test-title">${{title}}</div><div>${{message}}</div>`;
            resultsDiv.appendChild(section);
        }}

        function markTestPass(title) {{
            const sections = document.querySelectorAll('.test-section');
            const lastSection = sections[sections.length - 1];
            lastSection.innerHTML = `<div class="test-title">✅ ${{title}}</div><div class="status passed">Passed</div>`;
        }}

        function displayQueryResult(elementId, result) {{
            const element = document.getElementById(elementId);
            const rows = result.toArray();

            if (rows.length === 0) {{
                element.innerHTML = '<div class="query-result">No results</div>';
                return;
            }}

            const headers = Object.keys(rows[0]);
            let html = '<table class="query-result"><tr>';
            headers.forEach(h => html += `<th>${{h}}</th>`);
            html += '</tr>';

            rows.forEach(row => {{
                html += '<tr>';
                headers.forEach(h => {{
                    let value = row[h];
                    if (value === null) value = 'NULL';
                    else if (typeof value === 'object') value = JSON.stringify(value);
                    else if (typeof value === 'number' && !Number.isInteger(value)) value = value.toFixed(2);
                    html += `<td>${{value}}</td>`;
                }});
                html += '</tr>';
            }});
            html += '</table>';
            element.innerHTML = html;
        }}

        // Make functions globally available
        window.runConformanceTests = runConformanceTests;
        window.runSampleQueries = runSampleQueries;
    </script>
</body>
</html>"""

    Path(output_path).write_text(html_content, encoding="utf-8")
    print(f"✅ Generated conformance test HTML: {output_path}")


def run_conformance_test() -> None:
    """
    Generate sample Parquet file and conformance test HTML.

    This function:
    1. Creates a set of sample EnrichedListing objects
    2. Writes them to a Parquet file using the ParquetWriter
    3. Generates an HTML conformance test file

    After running this function, open the generated HTML file in a browser
    to verify DuckDB-WASM can load and query the Parquet file.
    """
    print("🧪 Phase 3 Conformance Test: DuckDB-WASM + Parquet Writer")
    print("=" * 60)

    # Create temp directory for test outputs
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)

        # Generate sample listings
        print("📝 Generating sample listings...")
        listings = generate_sample_listings()
        print(f"   Created {len(listings)} sample listings")

        # Write to Parquet
        parquet_path = tmppath / "conformance_test.parquet"
        print(f"💾 Writing Parquet file: {parquet_path}")
        writer = ParquetWriter()
        writer.write_listings(listings, parquet_path)
        print("   ✅ Parquet file written successfully")

        # Generate conformance test HTML
        html_path = tmppath / "conformance_test.html"
        print(f"🌐 Generating conformance test HTML: {html_path}")
        generate_conformance_test_html(str(parquet_path.name), str(html_path))

        print("\n" + "=" * 60)
        print("✅ Conformance test files generated successfully!")
        print(f"\n📂 Test directory: {tmpdir}")
        print(f"   - Parquet file: {parquet_path}")
        print(f"   - HTML test: {html_path}")
        print("\n📋 Next steps:")
        print("   1. Copy the Parquet file and HTML to the same directory")
        print("   2. Open the HTML file in a web browser")
        print("   3. Click 'Run Conformance Tests' to verify DuckDB-WASM compatibility")
        print("   4. If all tests pass, Phase 3 conformance is verified ✅")
        print("\n💡 Tip: Keep both files in the same directory for the HTTP loading to work")

        return str(html_path), str(parquet_path)


if __name__ == "__main__":
    run_conformance_test()
