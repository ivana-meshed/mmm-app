"""
Tests for benchmark_mmm.py script

Tests configuration parsing, variant generation, and basic functionality.
Note: Some tests may be skipped if google.cloud or pandas are not installed.
"""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Mock google.cloud before importing benchmark_mmm
import sys
from unittest.mock import MagicMock

# Mock google cloud modules if not available
try:
    import google.cloud.storage  # noqa: F401
except ImportError:
    sys.modules["google.cloud"] = MagicMock()
    sys.modules["google.cloud.storage"] = MagicMock()

# Now we can import
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

try:
    import benchmark_mmm
except ImportError as e:
    raise unittest.SkipTest(
        f"Cannot import benchmark_mmm: {e}. "
        "Install dependencies to run tests."
    )


class TestBenchmarkConfig(unittest.TestCase):
    """Test BenchmarkConfig class."""

    def test_valid_config(self):
        """Test loading a valid configuration."""
        config_dict = {
            "name": "test_benchmark",
            "description": "Test description",
            "base_config": {
                "country": "de",
                "goal": "UPLOAD_VALUE",
                "version": "20251211_115528",
            },
            "variants": {},
        }

        config = benchmark_mmm.BenchmarkConfig(config_dict)
        self.assertEqual(config.name, "test_benchmark")
        self.assertEqual(config.description, "Test description")
        self.assertEqual(config.base_config["country"], "de")

    def test_missing_required_field(self):
        """Test that missing required fields raise ValueError."""
        config_dict = {
            "name": "test",
            # Missing 'description', 'base_config', 'variants'
        }

        with self.assertRaises(ValueError):
            benchmark_mmm.BenchmarkConfig(config_dict)

    def test_default_values(self):
        """Test default values are set correctly."""
        config_dict = {
            "name": "test",
            "description": "desc",
            "base_config": {"country": "de", "goal": "goal", "version": "v1"},
            "variants": {},
        }

        config = benchmark_mmm.BenchmarkConfig(config_dict)
        self.assertEqual(config.max_combinations, 50)
        self.assertEqual(config.iterations, 2000)
        self.assertEqual(config.trials, 5)


class TestBenchmarkRunner(unittest.TestCase):
    """Test BenchmarkRunner class."""

    def setUp(self):
        """Set up test fixtures."""
        # Mock GCS client
        self.mock_client = MagicMock()
        self.mock_bucket = MagicMock()
        self.mock_client.bucket.return_value = self.mock_bucket

        with patch("benchmark_mmm.storage.Client") as mock_storage:
            mock_storage.return_value = self.mock_client
            self.runner = benchmark_mmm.BenchmarkRunner()

    def test_generate_adstock_variants(self):
        """Test generating adstock variants."""
        base_config = {
            "country": "de",
            "paid_media_spends": ["SPEND_FB", "SPEND_GOOGLE"],
            "paid_media_vars": ["FB_IMPRESSIONS", "GOOGLE_CLICKS"],
        }

        specs = [
            {
                "name": "geometric",
                "description": "Geometric adstock",
                "type": "geometric",
            },
            {
                "name": "weibull_cdf",
                "description": "Weibull CDF",
                "type": "weibull_cdf",
            },
        ]

        variants = self.runner._generate_adstock_variants(base_config, specs)

        self.assertEqual(len(variants), 2)
        self.assertEqual(variants[0]["benchmark_test"], "adstock")
        self.assertEqual(variants[0]["benchmark_variant"], "geometric")
        self.assertEqual(variants[0]["adstock"], "geometric")
        self.assertEqual(variants[1]["adstock"], "weibull_cdf")

    def test_generate_split_variants(self):
        """Test generating train/val/test split variants."""
        base_config = {"country": "de"}

        specs = [
            {
                "name": "70_90",
                "description": "70-90 split",
                "train_size": [0.7, 0.9],
            },
            {
                "name": "75_90",
                "description": "75-90 split",
                "train_size": [0.75, 0.9],
            },
        ]

        variants = self.runner._generate_split_variants(base_config, specs)

        self.assertEqual(len(variants), 2)
        self.assertEqual(variants[0]["benchmark_test"], "train_split")
        self.assertEqual(variants[0]["train_size"], [0.7, 0.9])
        self.assertEqual(variants[1]["train_size"], [0.75, 0.9])

    def test_variant_to_queue_params_includes_data_gcs_path(self):
        """Test that data_gcs_path is constructed from data_version."""
        variant = {
            "country": "de",
            "revision": "test-rev",
            "data_version": "20251211_115528",
            "selected_goal": "UPLOAD_VALUE",
            "paid_media_spends": ["SPEND_FB"],
            "paid_media_vars": ["FB_IMPRESSIONS"],
            "benchmark_test": "adstock",
            "benchmark_variant": "geometric",
        }

        params = self.runner._variant_to_queue_params(variant, "test_benchmark")

        # Verify data_gcs_path is constructed correctly
        self.assertIn("data_gcs_path", params)
        self.assertIn("mapped-datasets/de/20251211_115528/raw.parquet", params["data_gcs_path"])
        
        # Verify dep_var is set from selected_goal
        self.assertEqual(params["dep_var"], "UPLOAD_VALUE")
        
        # Verify benchmark metadata is preserved
        self.assertEqual(params["benchmark_id"], "test_benchmark")
        self.assertEqual(params["benchmark_test"], "adstock")
        self.assertEqual(params["benchmark_variant"], "geometric")

    def test_variant_to_queue_params_without_data_version(self):
        """Test handling when data_version is missing."""
        variant = {
            "country": "de",
            "paid_media_spends": [],
        }

        params = self.runner._variant_to_queue_params(variant, "test_benchmark")

        # Should still create params but data_gcs_path will be None
        self.assertIn("data_gcs_path", params)
        self.assertIsNone(params["data_gcs_path"])
        """Test spend→spend mapping variant generation."""
        base_config = {
            "country": "de",
            "paid_media_spends": ["SPEND_FB", "SPEND_GOOGLE"],
        }

        specs = [
            {
                "name": "all_spend_to_spend",
                "description": "All spend to spend",
                "type": "spend_to_spend",
            }
        ]

        variants = self.runner._generate_spend_var_variants(
            base_config, specs
        )

        self.assertEqual(len(variants), 1)
        self.assertEqual(
            variants[0]["paid_media_vars"],
            ["SPEND_FB", "SPEND_GOOGLE"],
        )
        self.assertEqual(
            variants[0]["var_to_spend_mapping"],
            {"SPEND_FB": "SPEND_FB", "SPEND_GOOGLE": "SPEND_GOOGLE"},
        )

    def test_generate_time_agg_variants(self):
        """Test time aggregation variant generation."""
        base_config = {"country": "de"}

        specs = [
            {
                "name": "daily",
                "description": "Daily aggregation",
                "frequency": "none",
            },
            {
                "name": "weekly",
                "description": "Weekly aggregation",
                "frequency": "W",
            },
        ]

        variants = self.runner._generate_time_agg_variants(
            base_config, specs
        )

        self.assertEqual(len(variants), 2)
        self.assertEqual(variants[0]["resample_freq"], "none")
        self.assertEqual(variants[1]["resample_freq"], "W")

    def test_max_combinations_limit(self):
        """Test that max_combinations limits generated variants."""
        base_config = {"country": "de"}

        benchmark_config_dict = {
            "name": "test",
            "description": "desc",
            "base_config": {
                "country": "de",
                "goal": "goal",
                "version": "v1",
            },
            "max_combinations": 2,  # Limit to 2
            "variants": {
                "adstock": [
                    {"name": "v1", "type": "geometric"},
                    {"name": "v2", "type": "weibull_cdf"},
                    {"name": "v3", "type": "weibull_pdf"},
                ]
            },
        }

        benchmark_config = benchmark_mmm.BenchmarkConfig(
            benchmark_config_dict
        )
        variants = self.runner.generate_variants(
            base_config, benchmark_config
        )

        # Should be limited to 2 even though we defined 3
        self.assertEqual(len(variants), 2)


class TestBenchmarkConfigFiles(unittest.TestCase):
    """Test that example benchmark config files are valid."""

    def test_example_configs_valid(self):
        """Test that all example configs can be loaded."""
        benchmark_dir = Path(__file__).parent.parent / "benchmarks"

        if not benchmark_dir.exists():
            self.skipTest("Benchmarks directory not found")

        json_files = list(benchmark_dir.glob("*.json"))

        self.assertGreater(
            len(json_files), 0, "No example configs found"
        )

        for config_file in json_files:
            with self.subTest(config=config_file.name):
                with open(config_file) as f:
                    config_dict = json.load(f)

                # Should not raise
                config = benchmark_mmm.BenchmarkConfig(config_dict)
                self.assertIsNotNone(config.name)
                self.assertIsNotNone(config.description)


if __name__ == "__main__":
    unittest.main()
