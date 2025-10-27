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


if __name__ == "__main__":
    unittest.main()
