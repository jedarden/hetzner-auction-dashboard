"""
Test unmatched CPU reporter functionality.
"""

import json
import pytest
from pathlib import Path
from datetime import UTC, datetime
from pipeline.unmatched_reporter import UnmatchedCpuReporter, process_listings_batch
from pipeline.cpu_matcher import BenchmarkMatch


class TestUnmatchedCpuReporter:
    """Test unmatched CPU reporting."""

    def test_reporter_initialization(self):
        """Test reporter initializes correctly."""
        reporter = UnmatchedCpuReporter()
        assert reporter.get_unmatched_count() == 0
        assert reporter.get_total_affected_listings() == 0
        assert not reporter.has_unmatched_cpus()

    def test_process_unmatched_cpu(self):
        """Test processing a single unmatched CPU."""
        reporter = UnmatchedCpuReporter()

        # Create an unmatched benchmark match
        unmatched_match = BenchmarkMatch(
            cpu_raw="Unknown CPU Model",
            cpu_normalized=None,
            passmark_id=None,
            single_thread_score=None,
            multi_thread_score=None,
            matched=False,
            match_method=None,
        )

        reporter.process_listing("listing-1", unmatched_match)

        assert reporter.get_unmatched_count() == 1
        assert reporter.get_total_affected_listings() == 1
        assert reporter.has_unmatched_cpus()

    def test_process_matched_cpu(self):
        """Test that matched CPUs are not tracked."""
        reporter = UnmatchedCpuReporter()

        # Create a matched benchmark match
        matched_match = BenchmarkMatch(
            cpu_raw="Intel Xeon E5-2680 v4",
            cpu_normalized="Intel Xeon E5-2680 v4",
            passmark_id=5773,
            single_thread_score=2012,
            multi_thread_score=21339,
            matched=True,
            match_method="direct",
        )

        reporter.process_listing("listing-1", matched_match)

        assert reporter.get_unmatched_count() == 0
        assert reporter.get_total_affected_listings() == 0
        assert not reporter.has_unmatched_cpus()

    def test_multiple_listings_same_cpu(self):
        """Test tracking multiple listings with the same unmatched CPU."""
        reporter = UnmatchedCpuReporter()

        unmatched_match = BenchmarkMatch(
            cpu_raw="Unknown CPU Model",
            cpu_normalized=None,
            passmark_id=None,
            single_thread_score=None,
            multi_thread_score=None,
            matched=False,
            match_method=None,
        )

        # Process 3 listings with the same unmatched CPU
        for i in range(1, 4):
            reporter.process_listing(f"listing-{i}", unmatched_match)

        assert reporter.get_unmatched_count() == 1
        assert reporter.get_total_affected_listings() == 3

    def test_multiple_different_unmatched_cpus(self):
        """Test tracking multiple different unmatched CPUs."""
        reporter = UnmatchedCpuReporter()

        unmatched_cpus = ["Unknown CPU 1", "Unknown CPU 2", "Unknown CPU 3"]

        for i, cpu_name in enumerate(unmatched_cpus):
            unmatched_match = BenchmarkMatch(
                cpu_raw=cpu_name,
                cpu_normalized=None,
                passmark_id=None,
                single_thread_score=None,
                multi_thread_score=None,
                matched=False,
                match_method=None,
            )
            reporter.process_listing(f"listing-{i}", unmatched_match)

        assert reporter.get_unmatched_count() == 3
        assert reporter.get_total_affected_listings() == 3

    def test_generate_report(self, tmp_path):
        """Test generating the unmatched CPUs JSON report."""
        reporter = UnmatchedCpuReporter()

        # Add some unmatched CPUs
        for i in range(1, 4):
            unmatched_match = BenchmarkMatch(
                cpu_raw=f"Unknown CPU {i}",
                cpu_normalized=None,
                passmark_id=None,
                single_thread_score=None,
                multi_thread_score=None,
                matched=False,
                match_method=None,
            )
            # Add multiple listings for CPU 2 to test sorting
            count = 3 if i == 2 else 1
            for j in range(count):
                reporter.process_listing(f"listing-{i}-{j}", unmatched_match)

        # Generate report
        output_path = tmp_path / "unmatched-cpus.json"
        reporter.generate_report(output_path)

        # Verify report exists and is valid JSON
        assert output_path.exists()

        with open(output_path, "r") as f:
            report = json.load(f)

        assert "generated_at" in report
        assert "total_unmatched_cpus" in report
        assert "unmatched_cpus" in report

        assert report["total_unmatched_cpus"] == 3
        assert len(report["unmatched_cpus"]) == 3

        # Verify sorting by affected_count descending
        # CPU 2 should be first (3 listings), then CPU 1 and CPU 3 (1 each)
        assert report["unmatched_cpus"][0]["cpu_raw"] == "Unknown CPU 2"
        assert report["unmatched_cpus"][0]["affected_count"] == 3

    def test_empty_cpu_string_skipped(self):
        """Test that empty CPU strings are skipped."""
        reporter = UnmatchedCpuReporter()

        empty_match = BenchmarkMatch(
            cpu_raw="",
            cpu_normalized=None,
            passmark_id=None,
            single_thread_score=None,
            multi_thread_score=None,
            matched=False,
            match_method=None,
        )

        reporter.process_listing("listing-1", empty_match)

        assert reporter.get_unmatched_count() == 0
        assert reporter.get_total_affected_listings() == 0

    def test_sample_listing_ids_limit(self):
        """Test that only 5 sample listing IDs are kept per CPU."""
        reporter = UnmatchedCpuReporter()

        unmatched_match = BenchmarkMatch(
            cpu_raw="Unknown CPU",
            cpu_normalized=None,
            passmark_id=None,
            single_thread_score=None,
            multi_thread_score=None,
            matched=False,
            match_method=None,
        )

        # Add 10 listings with the same unmatched CPU
        for i in range(10):
            reporter.process_listing(f"listing-{i}", unmatched_match)

        report_data = reporter.get_report_data()
        assert len(report_data) == 1

        entry = report_data[0]
        assert entry["affected_count"] == 10
        assert len(entry["sample_listing_ids"]) == 5  # Limited to 5

    def test_process_listings_batch_integration(self, tmp_path):
        """Test batch processing integration."""
        from pipeline.cpu_matcher import CpuMatcher

        # Initialize matcher and reporter
        benchmark_map_dir = Path(__file__).parent.parent.parent / "benchmark-map"
        cpu_matcher = CpuMatcher(benchmark_map_dir)
        reporter = UnmatchedCpuReporter()

        # Create test listings (mix of matched and unmatched)
        listings = [
            {"listing_id": "l1", "cpu_raw": "Intel Xeon E5-2680 v4"},  # Should match
            {"listing_id": "l2", "cpu_raw": "Unknown CPU Model"},  # Should not match
            {"listing_id": "l3", "cpu_raw": "E5-2680 v4"},  # Should match via alias
            {"listing_id": "l4", "cpu_raw": "Another Unknown CPU"},  # Should not match
        ]

        # Process batch
        process_listings_batch(listings, cpu_matcher, reporter)

        # Verify results
        assert reporter.get_unmatched_count() == 2  # 2 unmatched CPUs
        assert reporter.get_total_affected_listings() == 2  # 2 affected listings

        # Verify the unmatched CPUs are correct
        report_data = reporter.get_report_data()
        unmatched_cpu_names = [entry["cpu_raw"] for entry in report_data]

        assert "Unknown CPU Model" in unmatched_cpu_names
        assert "Another Unknown CPU" in unmatched_cpu_names

        # Generate and verify report
        output_path = tmp_path / "unmatched-cpus.json"
        reporter.generate_report(output_path)

        with open(output_path, "r") as f:
            report = json.load(f)

        assert report["total_unmatched_cpus"] == 2
