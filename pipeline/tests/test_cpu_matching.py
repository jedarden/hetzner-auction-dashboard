"""
CPU-Matching Fixture Set

Tests the CPU matching logic against three categories of fixtures:
1. Known-tricky same-chip variants (must resolve to correct match)
2. Intentionally-unmatchable strings (must produce benchmark_matched = false)
3. Near-miss adversarial pairs (similar but distinct CPUs, must never cross-match)

This is the primary defense against Risk Register R1 (false-positive matches).
"""

import pytest
from pipeline.cpu_matcher import CpuMatcher, BenchmarkMatch
from pathlib import Path


class TestCpuMatchingFixtures:
    """
    CPU matching fixture tests as per Testing Strategy.

    Category 1: Known-tricky same-chip variants
    - Different whitespace, capitalization, missing prefixes
    - Must all resolve to the correct canonical match
    """

    @pytest.fixture
    def cpu_matcher(self):
        """Initialize CPU matcher with test benchmark-map directory."""
        # Use the actual benchmark-map directory
        benchmark_map_dir = Path(__file__).parent.parent.parent / "benchmark-map"
        return CpuMatcher(benchmark_map_dir)

    # Category 1: Same-chip variants - Xeon E5-2680 v4
    def test_xeon_e5_2680_v4_variants(self, cpu_matcher):
        """Test all variants of Xeon E5-2680 v4 resolve to same correct match."""
        variants = [
            "Intel Xeon E5-2680 v4",  # Canonical form
            "Xeon E5-2680 v4",  # Missing Intel prefix
            "E5-2680 v4",  # Minimal form
            "E5-2680v4",  # No space before v4
            "Intel Xeon E5-2680v4",  # No space, full prefix
        ]

        expected_passmark_id = 5773  # From reference.csv
        expected_single = 2012
        expected_multi = 21339

        for variant in variants:
            result = cpu_matcher.match_cpu(variant)

            assert result.matched, f"Variant '{variant}' should match but didn't"
            assert result.passmark_id == expected_passmark_id, \
                f"Variant '{variant}' matched wrong CPU ID: {result.passmark_id} != {expected_passmark_id}"
            assert result.single_thread_score == expected_single, \
                f"Variant '{variant}' has wrong single-thread score"
            assert result.multi_thread_score == expected_multi, \
                f"Variant '{variant}' has wrong multi-thread score"
            assert result.match_method in ["direct", "alias"], \
                f"Variant '{variant}' should match via direct or alias method"

    def test_xeon_e5_2660_v4_variants(self, cpu_matcher):
        """Test all variants of Xeon E5-2660 v4 resolve to same correct match."""
        variants = [
            "Intel Xeon E5-2660 v4",
            "Xeon E5-2660 v4",
            "E5-2660 v4",
            "E5-2660v4",
        ]

        expected_passmark_id = 5771

        for variant in variants:
            result = cpu_matcher.match_cpu(variant)
            assert result.matched, f"Variant '{variant}' should match but didn't"
            assert result.passmark_id == expected_passmark_id, \
                f"Variant '{variant}' matched wrong CPU ID"

    def test_epyc_variants(self, cpu_matcher):
        """Test AMD EPYC variants resolve correctly."""
        variants = [
            "AMD EPYC 7401P",
            "EPYC 7401P",
        ]

        expected_passmark_id = 5055

        for variant in variants:
            result = cpu_matcher.match_cpu(variant)
            assert result.matched, f"EPYC variant '{variant}' should match"
            assert result.passmark_id == expected_passmark_id, \
                f"EPYC variant '{variant}' matched wrong CPU ID"

    def test_ryzen_variants(self, cpu_matcher):
        """Test AMD Ryzen variants resolve correctly."""
        variants = [
            "AMD Ryzen 9 7950X",
            "Ryzen 9 7950X",
        ]

        expected_passmark_id = 6455

        for variant in variants:
            result = cpu_matcher.match_cpu(variant)
            assert result.matched, f"Ryzen variant '{variant}' should match"
            assert result.passmark_id == expected_passmark_id, \
                f"Ryzen variant '{variant}' matched wrong CPU ID"

    def test_intel_core_variants(self, cpu_matcher):
        """Test Intel Core variants resolve correctly."""
        variants = [
            "Intel Core i9-12900K",
            "Core i9-12900K",
            "i9-12900K",
        ]

        expected_passmark_id = 5200

        for variant in variants:
            result = cpu_matcher.match_cpu(variant)
            assert result.matched, f"Core variant '{variant}' should match"
            assert result.passmark_id == expected_passmark_id, \
                f"Core variant '{variant}' matched wrong CPU ID"

    # Category 2: Intentionally-unmatchable strings
    def test_intentionally_unmatchable_strings(self, cpu_matcher):
        """
        Test that intentionally-unmatchable strings produce benchmark_matched = false.

        These represent:
        - CPU families with no benchmark entry
        - Malformed CPU strings
        - Unknown/future CPUs not in reference table
        """
        unmatchable_strings = [
            "Unknown CPU Model 9999",  # Completely made up
            "Quantum Processor Q-1000",  # Fictional CPU
            "Intel Future Processor 2099",  # Future CPU
            "AMD EPYC 9999",  # Non-existent EPYC model
            "Xeon E9-9999 v9",  # Non-existent Xeon series
            "",  # Empty string
            "N/A",  # Not applicable
            "Generic Processor",  # Too generic
        ]

        for cpu_string in unmatchable_strings:
            result = cpu_matcher.match_cpu(cpu_string)

            assert not result.matched, \
                f"Unmatchable string '{cpu_string}' should NOT match (matched={result.matched})"
            assert result.passmark_id is None, \
                f"Unmatchable string '{cpu_string}' should have NULL passmark_id"
            assert result.single_thread_score is None, \
                f"Unmatchable string '{cpu_string}' should have NULL single_thread_score"
            assert result.multi_thread_score is None, \
                f"Unmatchable string '{cpu_string}' should have NULL multi_thread_score"

    # Category 3: Near-miss adversarial pairs
    def test_near_miss_adversarial_xeon_generations(self, cpu_matcher):
        """
        Test near-miss adversarial pairs: Xeon E5-2680 v3 vs v4.

        These are similar but distinct CPUs. The matcher must:
        - Match each to its correct PassMark ID
        - NEVER cross-match v3 to v4 or vice versa
        """
        v4_variants = ["Intel Xeon E5-2680 v4", "E5-2680 v4", "E5-2680v4"]
        v3_variants = ["Intel Xeon E5-2680 v3", "E5-2680 v3", "E5-2680v3"]

        v4_passmark_id = 5773
        v3_passmark_id = 4756

        # Test v4 variants match v4, never v3
        for v4_variant in v4_variants:
            result = cpu_matcher.match_cpu(v4_variant)
            assert result.matched, f"v4 variant '{v4_variant}' should match"
            assert result.passmark_id == v4_passmark_id, \
                f"v4 variant '{v4_variant}' should match v4 PassMark ID {v4_passmark_id}, not v3 {v3_passmark_id}"
            assert result.passmark_id != v3_passmark_id, \
                f"v4 variant '{v4_variant}' must NEVER match v3 PassMark ID {v3_passmark_id} (RISK R1 DEFENSE)"

        # Test v3 variants match v3, never v4
        for v3_variant in v3_variants:
            result = cpu_matcher.match_cpu(v3_variant)
            assert result.matched, f"v3 variant '{v3_variant}' should match"
            assert result.passmark_id == v3_passmark_id, \
                f"v3 variant '{v3_variant}' should match v3 PassMark ID {v3_passmark_id}, not v4 {v4_passmark_id}"
            assert result.passmark_id != v4_passmark_id, \
                f"v3 variant '{v3_variant}' must NEVER match v4 PassMark ID {v4_passmark_id} (RISK R1 DEFENSE)"

    def test_near_miss_adversarial_xeon_2660_generations(self, cpu_matcher):
        """Test near-miss adversarial pairs: Xeon E5-2660 v3 vs v4."""
        v4_variants = ["Intel Xeon E5-2660 v4", "E5-2660 v4", "E5-2660v4"]
        v3_variants = ["Intel Xeon E5-2660 v3", "E5-2660 v3", "E5-2660v3"]

        v4_passmark_id = 5771
        v3_passmark_id = 4754

        for v4_variant in v4_variants:
            result = cpu_matcher.match_cpu(v4_variant)
            assert result.matched, f"v4 variant '{v4_variant}' should match"
            assert result.passmark_id == v4_passmark_id, \
                f"v4 variant '{v4_variant}' should match v4 PassMark ID, not v3"
            assert result.passmark_id != v3_passmark_id, \
                f"v4 variant '{v4_variant}' must NEVER match v3 PassMark ID (RISK R1 DEFENSE)"

        for v3_variant in v3_variants:
            result = cpu_matcher.match_cpu(v3_variant)
            assert result.matched, f"v3 variant '{v3_variant}' should match"
            assert result.passmark_id == v3_passmark_id, \
                f"v3 variant '{v3_variant}' should match v3 PassMark ID, not v4"
            assert result.passmark_id != v4_passmark_id, \
                f"v3 variant '{v3_variant}' must NEVER match v4 PassMark ID (RISK R1 DEFENSE)"

    def test_near_miss_adversarial_epyc_models(self, cpu_matcher):
        """Test near-miss adversarial pairs: AMD EPYC 7402 vs 7452."""
        epyc_7402_variants = ["AMD EPYC 7402", "EPYC 7402"]
        epyc_7452_variants = ["AMD EPYC 7452", "EPYC 7452"]

        epyc_7402_passmark_id = 5952
        epyc_7452_passmark_id = 5954

        for variant in epyc_7402_variants:
            result = cpu_matcher.match_cpu(variant)
            assert result.matched, f"EPYC 7402 variant '{variant}' should match"
            assert result.passmark_id == epyc_7402_passmark_id, \
                f"EPYC 7402 variant should match correct PassMark ID"
            assert result.passmark_id != epyc_7452_passmark_id, \
                f"EPYC 7402 must NEVER match EPYC 7452 PassMark ID (RISK R1 DEFENSE)"

        for variant in epyc_7452_variants:
            result = cpu_matcher.match_cpu(variant)
            assert result.matched, f"EPYC 7452 variant '{variant}' should match"
            assert result.passmark_id == epyc_7452_passmark_id, \
                f"EPYC 7452 variant should match correct PassMark ID"
            assert result.passmark_id != epyc_7402_passmark_id, \
                f"EPYC 7452 must NEVER match EPYC 7402 PassMark ID (RISK R1 DEFENSE)"

    def test_near_miss_adversarial_ryzen_models(self, cpu_matcher):
        """Test near-miss adversarial pairs: Ryzen 9 7900X vs 7950X."""
        ryzen_7950x_variants = ["AMD Ryzen 9 7950X", "Ryzen 9 7950X"]
        ryzen_7900x_variants = ["AMD Ryzen 9 7900X", "Ryzen 9 7900X"]

        ryzen_7950x_passmark_id = 6455
        ryzen_7900x_passmark_id = 6453

        for variant in ryzen_7950x_variants:
            result = cpu_matcher.match_cpu(variant)
            assert result.matched, f"Ryzen 7950X variant '{variant}' should match"
            assert result.passmark_id == ryzen_7950x_passmark_id, \
                f"Ryzen 7950X variant should match correct PassMark ID"
            assert result.passmark_id != ryzen_7900x_passmark_id, \
                f"Ryzen 7950X must NEVER match Ryzen 7900X PassMark ID (RISK R1 DEFENSE)"

        for variant in ryzen_7900x_variants:
            result = cpu_matcher.match_cpu(variant)
            assert result.matched, f"Ryzen 7900X variant '{variant}' should match"
            assert result.passmark_id == ryzen_7900x_passmark_id, \
                f"Ryzen 7900X variant should match correct PassMark ID"
            assert result.passmark_id != ryzen_7950x_passmark_id, \
                f"Ryzen 7900X must NEVER match Ryzen 7950X PassMark ID (RISK R1 DEFENSE)"

    def test_empty_and_none_inputs(self, cpu_matcher):
        """Test edge cases: empty string and None."""
        # Empty string
        result = cpu_matcher.match_cpu("")
        assert not result.matched, "Empty string should not match"
        assert result.cpu_raw == ""

        # None-like behavior (empty after stripping)
        result = cpu_matcher.match_cpu("   ")
        assert not result.matched, "Whitespace-only string should not match"


class TestCpuMatcherFunctionality:
    """Test CPU matcher functionality and data loading."""

    @pytest.fixture
    def cpu_matcher(self):
        """Initialize CPU matcher with test benchmark-map directory."""
        benchmark_map_dir = Path(__file__).parent.parent.parent / "benchmark-map"
        return CpuMatcher(benchmark_map_dir)

    def test_matcher_initialization(self, cpu_matcher):
        """Test that CPU matcher initializes correctly."""
        assert cpu_matcher is not None
        assert cpu_matcher.get_reference_table_size() > 0, "Reference table should not be empty"
        assert cpu_matcher.get_aliases_count() > 0, "Should have aliases defined"

    def test_benchmark_match_dataclass(self):
        """Test BenchmarkMatch dataclass structure."""
        match = BenchmarkMatch(
            cpu_raw="Test CPU",
            cpu_normalized="Test CPU Normalized",
            passmark_id=1234,
            single_thread_score=1000,
            multi_thread_score=10000,
            matched=True,
            match_method="direct",
        )

        assert match.cpu_raw == "Test CPU"
        assert match.cpu_normalized == "Test CPU Normalized"
        assert match.passmark_id == 1234
        assert match.single_thread_score == 1000
        assert match.multi_thread_score == 10000
        assert match.matched is True
        assert match.match_method == "direct"

    def test_cpu_string_normalization(self, cpu_matcher):
        """Test CPU string normalization."""
        # Extra whitespace
        normalized = cpu_matcher.normalize_cpu_string("  Xeon  E5-2680  v4  ")
        assert normalized == "Xeon E5-2680 v4", "Should normalize extra whitespace"

        # Multiple spaces
        normalized = cpu_matcher.normalize_cpu_string("Xeon    E5-2680    v4")
        assert normalized == "Xeon E5-2680 v4", "Should collapse multiple spaces"

        # Empty string
        normalized = cpu_matcher.normalize_cpu_string("")
        assert normalized == "", "Should handle empty string"

        # None case (handled by match_cpu, not normalize_cpu_string)
        result = cpu_matcher.match_cpu(None)
        assert not result.matched, "None input should not match"
