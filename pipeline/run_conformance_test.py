#!/usr/bin/env python3
"""
Run DuckDB-WASM Conformance Test

This script generates the conformance test files in a persistent location
so they can be easily tested in a browser.
"""

import sys
import tempfile
from pathlib import Path

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from tests.conformance_test_duckdb_wasm import generate_conformance_test_html, generate_sample_listings
from pipeline.parquet_writer import ParquetWriter


def main():
    """Run the conformance test and generate persistent files."""
    print("🧪 Phase 3 Conformance Test: DuckDB-WASM + Parquet Writer")
    print("=" * 60)

    # Create output directory in project root
    output_dir = Path(__file__).parent.parent / "test_output" / "conformance"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate sample listings
    print("📝 Generating sample listings...")
    listings = generate_sample_listings()
    print(f"   Created {len(listings)} sample listings")

    # Write to Parquet
    parquet_path = output_dir / "conformance_test.parquet"
    print(f"💾 Writing Parquet file: {parquet_path}")
    writer = ParquetWriter()
    writer.write_listings(listings, parquet_path)
    print("   ✅ Parquet file written successfully")

    # Generate conformance test HTML
    html_path = output_dir / "conformance_test.html"
    print(f"🌐 Generating conformance test HTML: {html_path}")
    generate_conformance_test_html("conformance_test.parquet", str(html_path))

    print("\n" + "=" * 60)
    print("✅ Conformance test files generated successfully!")
    print(f"\n📂 Output directory: {output_dir}")
    print(f"   - Parquet file: {parquet_path}")
    print(f"   - HTML test: {html_path}")
    print("\n📋 Next steps:")
    print("   1. Open the HTML file in a web browser")
    print("   2. Click 'Run Conformance Tests' to verify DuckDB-WASM compatibility")
    print("   3. If all tests pass, Phase 3 conformance is verified ✅")
    print("\n💡 Tip: Files are in the same directory for HTTP loading to work")

    return str(html_path), str(parquet_path)


if __name__ == "__main__":
    main()
