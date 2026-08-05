"""
Integration test for CPU matching and unmatched reporting.

This test verifies that the complete workflow works:
1. CpuMatcher matches CPU strings to benchmark data
2. UnmatchedCpuReporter tracks unmatched CPUs
3. The report can be generated and contains correct data
"""

import json
import pytest
from pathlib import Path
from pipeline.cpu_matcher import CpuMatcher
from pipeline.unmatched_reporter import UnmatchedCpuReporter


class TestBenchmarkIntegration:
    """Integration tests for the complete benchmark matching workflow."""

    @pytest.fixture
    def cpu_matcher(self):
        """Initialize CPU matcher."""
        benchmark_map_dir = Path(__file__).parent.parent.parent / "benchmark-map"
        return CpuMatcher(benchmark_map_dir)

    def test_full_workflow_with_sample_listings(self, cpu_matcher, tmp_path):
        """Test the complete workflow with sample auction listings."""
        # Initialize reporter
        reporter = UnmatchedCpuReporter()

        # Simulate a batch of auction listings (mix of matched and unmatched)
        sample_listings = [
            # These should match successfully
            {"listing_id": "hetzner-001", "cpu_raw": "Intel Xeon E5-2680 v4"},
            {"listing_id": "hetzner-002", "cpu_raw": "E5-2680v4"},  # Alias match
            {"listing_id": "hetzner-003", "cpu_raw": "AMD EPYC 7401P"},
            {"listing_id": "hetzner-004", "cpu_raw": "EPYC 7401P"},  # Alias match
            {"listing_id": "hetzner-005", "cpu_raw": "AMD Ryzen 9 7950X"},

            # These should NOT match (intentionally unmatchable)
            {"listing_id": "hetzner-006", "cpu_raw": "Unknown CPU Model X"},
            {"listing_id": "hetzner-007", "cpu_raw": "Generic Processor"},
            {"listing_id": "hetzner-008", "cpu_raw": "Future Tech CPU 9000"},

            # More matches
            {"listing_id": "hetzner-009", "cpu_raw": "Intel Core i7-12700K"},
            {"listing_id": "hetzner-010", "cpu_raw": "Core i7-12700K"},  # Alias

            # More unmatched (same CPU appears multiple times)
            {"listing_id": "hetzner-011", "cpu_raw": "Unknown CPU Model X"},
            {"listing_id": "hetzner-012", "cpu_raw": "Unknown CPU Model X"},
        ]

        # Process each listing
        for listing in sample_listings:
            listing_id = listing["listing_id"]
            cpu_raw = listing["cpu_raw"]

            # Match CPU
            match_result = cpu_matcher.match_cpu(cpu_raw)

            # Track unmatched
            reporter.process_listing(listing_id, match_result)

        # Verify statistics
        assert reporter.get_unmatched_count() == 2  # 2 unique unmatched CPUs
        assert reporter.get_total_affected_listings() == 4  # 4 total unmatched listings

        # Verify specific unmatched CPUs
        report_data = reporter.get_report_data()
        unmatched_cpus = {entry["cpu_raw"]: entry for entry in report_data}

        # "Unknown CPU Model X" should appear 3 times (hetzner-006, 011, 012)
        assert "Unknown CPU Model X" in unmatched_cpus
        assert unmatched_cpus["Unknown CPU Model X"]["affected_count"] == 3
        assert len(unmatched_cpus["Unknown CPU Model X"]["sample_listing_ids"]) == 3

        # "Generic Processor" should appear 1 time
        assert "Generic Processor" in unmatched_cpus
        assert unmatched_cpus["Generic Processor"]["affected_count"] == 1

        # "Future Tech CPU 9000" should appear 1 time
        assert "Future Tech CPU 9000" in unmatched_cpus
        assert unmatched_cpus["Future Tech CPU 9000"]["affected_count"] == 1

        # Generate the report
        output_path = tmp_path / "unmatched-cpus.json"
        reporter.generate_report(output_path)

        # Verify the report file
        assert output_path.exists()

        with open(output_path, "r") as f:
            report = json.load(f)

        # Check report structure
        assert "generated_at" in report
        assert "total_unmatched_cpus" in report
        assert "unmatched_cpus" in report

        assert report["total_unmatched_cpus"] == 2
        assert len(report["unmatched_cpus"]) == 2

        # Verify sorting by affected_count (highest first)
        assert report["unmatched_cpus"][0]["cpu_raw"] == "Unknown CPU Model X"
        assert report["unmatched_cpus"][0]["affected_count"] == 3

        print(f"✅ Integration test passed!")
        print(f"   Processed {len(sample_listings)} listings")
        print(f"   Matched: {len(sample_listings) - reporter.get_total_affected_listings()}")
        print(f"   Unmatched: {reporter.get_total_affected_listings()}")
        print(f"   Generated report: {output_path}")

    def test_all_cpus_match_successfully(self, cpu_matcher):
        """Test that all our reference CPUs can be matched correctly."""
        # Sample of CPUs from our reference table
        reference_cpus = [
            "Intel Xeon E5-2680 v4",
            "Intel Xeon E5-2660 v4",
            "Intel Xeon E5-2680 v3",
            "AMD EPYC 7401P",
            "AMD EPYC 7551",
            "AMD Ryzen 9 7950X",
            "Intel Core i9-12900K",
        ]

        for cpu in reference_cpus:
            result = cpu_matcher.match_cpu(cpu)
            assert result.matched, f"Reference CPU '{cpu}' should match"
            assert result.passmark_id is not None, f"Reference CPU '{cpu}' should have PassMark ID"
            assert result.single_thread_score is not None, f"Reference CPU '{cpu}' should have single-thread score"
            assert result.multi_thread_score is not None, f"Reference CPU '{cpu}' should have multi-thread score"

        print(f"✅ All {len(reference_cpus)} reference CPUs matched successfully")

    def test_benchmark_map_health(self, cpu_matcher):
        """Test that the benchmark map is properly configured."""
        # Verify we have data loaded
        assert cpu_matcher.get_reference_table_size() > 0, "Reference table should not be empty"
        assert cpu_matcher.get_aliases_count() > 0, "Should have aliases defined"

        print(f"📊 Benchmark Map Health:")
        print(f"   Reference CPUs: {cpu_matcher.get_reference_table_size()}")
        print(f"   Aliases: {cpu_matcher.get_aliases_count()}")
        print(f"   Overrides: {cpu_matcher.get_overrides_count()}")

        # Spot check some known CPUs
        known_cpus = [
            ("Intel Xeon E5-2680 v4", 5773),
            ("Intel Xeon E5-2680 v3", 4756),
            ("AMD EPYC 7401P", 5055),
        ]

        for cpu_name, expected_passmark_id in known_cpus:
            result = cpu_matcher.match_cpu(cpu_name)
            assert result.matched, f"Known CPU '{cpu_name}' should match"
            assert result.passmark_id == expected_passmark_id, \
                f"Known CPU '{cpu_name}' should have PassMark ID {expected_passmark_id}"
