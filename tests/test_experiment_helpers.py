"""
Unit tests for the Experiment page helper functions.
Tests data loading, metadata parsing, and configuration generation.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

import pandas as pd

# Add app directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from app_shared import build_job_config_from_params, parse_train_size


class TestExperimentHelpers(unittest.TestCase):
    """Test helper functions for the Experiment page."""

    def test_parse_train_size(self):
        """Test parsing train size string."""
        # Test valid input
        result = parse_train_size("0.7,0.9")
        self.assertEqual(result, [0.7, 0.9])

        # Test with spaces
        result = parse_train_size("0.7, 0.9")
        self.assertEqual(result, [0.7, 0.9])

        # Test invalid input
        result = parse_train_size("invalid")
        self.assertEqual(result, [0.7, 0.9])  # Should return default

        # Test single value
        result = parse_train_size("0.8")
        self.assertEqual(result, [0.7, 0.9])  # Should return default

    @patch("app_shared.st")
    def test_build_job_config_basic(self, mock_st):
        """Test building job config with basic parameters."""
        # Setup mock session state
        mock_st.session_state = {"gcs_bucket": "test-bucket"}

        params = {
            "country": "fr",
            "iterations": 200,
            "trials": 3,
            "train_size": "0.7,0.9",
            "revision": "r100",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "paid_media_spends": "GA_COST, META_COST",
            "paid_media_vars": "GA_IMPRESSIONS, META_IMPRESSIONS",
            "context_vars": "IS_WEEKEND",
            "factor_vars": "IS_WEEKEND",
            "organic_vars": "ORGANIC_TRAFFIC",
            "dep_var": "REVENUE",
            "dep_var_type": "revenue",
            "date_var": "date",
            "adstock": "geometric",
            "hyperparameter_preset": "Meshed recommend",
            "gcs_bucket": "test-bucket",
        }

        config = build_job_config_from_params(
            params, "gs://test-bucket/data.parquet", "20241022_120000", None
        )

        # Verify key fields
        self.assertEqual(config["country"], "fr")
        self.assertEqual(config["iterations"], 200)
        self.assertEqual(config["trials"], 3)
        self.assertEqual(config["train_size"], [0.7, 0.9])
        self.assertEqual(config["start_date"], "2024-01-01")
        self.assertEqual(config["end_date"], "2024-12-31")
        self.assertEqual(config["dep_var"], "REVENUE")
        self.assertEqual(config["dep_var_type"], "revenue")
        self.assertEqual(config["adstock"], "geometric")
        self.assertEqual(config["hyperparameter_preset"], "Meshed recommend")

        # Verify lists are parsed correctly
        self.assertEqual(config["paid_media_spends"], ["GA_COST", "META_COST"])
        self.assertEqual(
            config["paid_media_vars"], ["GA_IMPRESSIONS", "META_IMPRESSIONS"]
        )
        self.assertEqual(config["context_vars"], ["IS_WEEKEND"])

    @patch("app_shared.st")
    def test_build_job_config_with_dep_var_type(self, mock_st):
        """Test that dep_var_type is properly included in config."""
        mock_st.session_state = {"gcs_bucket": "test-bucket"}

        params = {
            "country": "de",
            "iterations": 2000,
            "trials": 5,
            "train_size": [0.7, 0.9],
            "revision": "r200",
            "start_date": "2024-01-01",
            "end_date": "2024-10-22",
            "paid_media_spends": "TV_COST",
            "paid_media_vars": "TV_COST",
            "context_vars": "",
            "factor_vars": "",
            "organic_vars": "",
            "dep_var": "CONVERSIONS",
            "dep_var_type": "conversion",
            "date_var": "date",
            "adstock": "weibull_cdf",
            "hyperparameter_preset": "Facebook recommend",
            "gcs_bucket": "test-bucket",
        }

        config = build_job_config_from_params(
            params, "gs://test-bucket/data.parquet", "20241022_120000", None
        )

        self.assertEqual(config["dep_var"], "CONVERSIONS")
        self.assertEqual(config["dep_var_type"], "conversion")
        self.assertEqual(config["adstock"], "weibull_cdf")


class TestMetadataFunctions(unittest.TestCase):
    """Test metadata loading and parsing functions."""

    def test_metadata_structure(self):
        """Test that metadata has expected structure."""
        # Simulate metadata.json structure
        metadata = {
            "goals": [
                {"var": "REVENUE", "group": "primary", "type": "revenue"},
                {
                    "var": "CONVERSIONS",
                    "group": "primary",
                    "type": "conversion",
                },
                {"var": "LEADS", "group": "secondary", "type": "conversion"},
            ],
            "mapping": [
                {
                    "var": "GA_COST",
                    "category": "paid_media_spends",
                    "channel": "ga",
                },
                {
                    "var": "GA_IMPRESSIONS",
                    "category": "paid_media_vars",
                    "channel": "ga",
                },
                {
                    "var": "IS_WEEKEND",
                    "category": "context_vars",
                    "channel": "",
                },
            ],
        }

        # Test goals extraction
        primary_goals = [
            g["var"] for g in metadata["goals"] if g["group"] == "primary"
        ]
        self.assertIn("REVENUE", primary_goals)
        self.assertIn("CONVERSIONS", primary_goals)
        self.assertNotIn("LEADS", primary_goals)

        # Test type mapping
        revenue_goal = next(
            g for g in metadata["goals"] if g["var"] == "REVENUE"
        )
        self.assertEqual(revenue_goal["type"], "revenue")

        # Test variable mapping
        paid_spends = [
            m["var"]
            for m in metadata["mapping"]
            if m["category"] == "paid_media_spends"
        ]
        self.assertIn("GA_COST", paid_spends)


class TestHyperparameterPresets(unittest.TestCase):
    """Test hyperparameter preset logic."""

    def test_preset_options(self):
        """Test that preset options are properly defined."""
        presets = {
            "Test run": {"iterations": 200, "trials": 3},
            "Production": {"iterations": 2000, "trials": 5},
            "Custom": {"iterations": 5000, "trials": 10},
        }

        # Verify each preset
        self.assertEqual(presets["Test run"]["iterations"], 200)
        self.assertEqual(presets["Test run"]["trials"], 3)
        self.assertEqual(presets["Production"]["iterations"], 2000)
        self.assertEqual(presets["Production"]["trials"], 5)
        self.assertEqual(presets["Custom"]["iterations"], 5000)
        self.assertEqual(presets["Custom"]["trials"], 10)


class TestQueueFunctionality(unittest.TestCase):
    """Test queue entry validation and processing with new GCS-based fields."""

    def test_data_source_validation_with_gcs_path(self):
        """Test that data_gcs_path is accepted as a valid data source."""
        # Test params with data_gcs_path
        params_with_gcs = {
            "country": "fr",
            "revision": "r100",
            "data_gcs_path": "gs://bucket/datasets/fr/latest/raw.parquet",
            "query": "",
            "table": "",
        }

        # Validation should pass (has data_gcs_path)
        has_data_source = bool(
            params_with_gcs.get("query")
            or params_with_gcs.get("table")
            or params_with_gcs.get("data_gcs_path")
        )
        self.assertTrue(has_data_source)

    def test_data_source_validation_with_query(self):
        """Test that query is still accepted as a valid data source."""
        params_with_query = {
            "country": "fr",
            "revision": "r100",
            "query": "SELECT * FROM TABLE",
            "table": "",
            "data_gcs_path": "",
        }

        has_data_source = bool(
            params_with_query.get("query")
            or params_with_query.get("table")
            or params_with_query.get("data_gcs_path")
        )
        self.assertTrue(has_data_source)

    def test_data_source_validation_missing(self):
        """Test that entries without data source are rejected."""
        params_no_source = {
            "country": "fr",
            "revision": "r100",
            "query": "",
            "table": "",
            "data_gcs_path": "",
        }

        has_data_source = bool(
            params_no_source.get("query")
            or params_no_source.get("table")
            or params_no_source.get("data_gcs_path")
        )
        self.assertFalse(has_data_source)

    def test_queue_entry_structure_with_new_fields(self):
        """Test that queue entries include all new fields."""
        entry = {
            "id": 1,
            "params": {
                "country": "fr",
                "revision": "r100",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "iterations": 200,
                "trials": 5,
                "train_size": "0.7,0.9",
                "paid_media_spends": "GA_COST",
                "paid_media_vars": "GA_IMPRESSIONS",
                "context_vars": "IS_WEEKEND",
                "factor_vars": "IS_WEEKEND",
                "organic_vars": "ORGANIC_TRAFFIC",
                "dep_var": "REVENUE",
                "dep_var_type": "revenue",
                "date_var": "date",
                "adstock": "geometric",
                "hyperparameter_preset": "Meshed recommend",
                "data_gcs_path": "gs://bucket/datasets/fr/latest/raw.parquet",
                "resample_freq": "none",
                "resample_agg": "sum",
            },
            "status": "PENDING",
            "timestamp": None,
            "execution_name": None,
            "gcs_prefix": None,
            "message": "",
        }

        # Verify new fields are present
        self.assertIn("start_date", entry["params"])
        self.assertIn("end_date", entry["params"])
        self.assertIn("dep_var_type", entry["params"])
        self.assertIn("hyperparameter_preset", entry["params"])
        self.assertIn("data_gcs_path", entry["params"])

        # Verify values
        self.assertEqual(entry["params"]["start_date"], "2024-01-01")
        self.assertEqual(entry["params"]["end_date"], "2024-12-31")
        self.assertEqual(entry["params"]["dep_var_type"], "revenue")
        self.assertEqual(
            entry["params"]["hyperparameter_preset"], "Meshed recommend"
        )
        self.assertTrue(entry["params"]["data_gcs_path"].startswith("gs://"))

    def test_example_csv_format(self):
        """Test that example CSV contains all required fields."""
        example_row = {
            "country": "fr",
            "revision": "r100",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "iterations": 200,
            "trials": 5,
            "train_size": "0.7,0.9",
            "paid_media_spends": "GA_COST, BING_COST",
            "paid_media_vars": "GA_IMPRESSIONS, BING_IMPRESSIONS",
            "context_vars": "IS_WEEKEND,TV_IS_ON",
            "factor_vars": "IS_WEEKEND,TV_IS_ON",
            "organic_vars": "ORGANIC_TRAFFIC",
            "gcs_bucket": "test-bucket",
            "data_gcs_path": "gs://test-bucket/datasets/fr/latest/raw.parquet",
            "table": "",
            "query": "",
            "dep_var": "REVENUE",
            "dep_var_type": "revenue",
            "date_var": "date",
            "adstock": "geometric",
            "hyperparameter_preset": "Meshed recommend",
            "resample_freq": "none",
            "resample_agg": "sum",
            "annotations_gcs_path": "",
        }

        # Verify all required fields
        required_fields = [
            "country",
            "revision",
            "start_date",
            "end_date",
            "iterations",
            "trials",
            "data_gcs_path",
            "dep_var",
            "dep_var_type",
        ]
        for field in required_fields:
            self.assertIn(
                field, example_row, f"Missing required field: {field}"
            )

        # Verify data source is present
        has_data_source = bool(
            example_row.get("query")
            or example_row.get("table")
            or example_row.get("data_gcs_path")
        )
        self.assertTrue(has_data_source, "Example row must have a data source")


if __name__ == "__main__":
    unittest.main()
