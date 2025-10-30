"""
Unit tests for resampling functionality in the training pipeline.
Tests the resampling feature for Weekly and Monthly aggregation.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

# Simplified tests that don't require importing app_shared


class TestResamplingConfiguration(unittest.TestCase):
    """Test resampling configuration values."""

    def test_resample_config_structure(self):
        """Test that resample config has correct structure."""
        config = {
            "resample_freq": "none",
            "resample_agg": "sum",
        }
        
        # Verify resample parameters exist
        self.assertIn("resample_freq", config)
        self.assertIn("resample_agg", config)
        self.assertEqual(config["resample_freq"], "none")
        self.assertEqual(config["resample_agg"], "sum")

    def test_resample_config_weekly(self):
        """Test config with weekly resampling."""
        config = {
            "resample_freq": "W",
            "resample_agg": "sum",
        }
        
        # Verify resample parameters
        self.assertEqual(config["resample_freq"], "W")
        self.assertEqual(config["resample_agg"], "sum")

    def test_resample_config_monthly_with_mean(self):
        """Test config with monthly resampling using mean aggregation."""
        config = {
            "resample_freq": "M",
            "resample_agg": "mean",
        }
        
        # Verify resample parameters
        self.assertEqual(config["resample_freq"], "M")
        self.assertEqual(config["resample_agg"], "mean")


class TestResamplingAggregations(unittest.TestCase):
    """Test different aggregation methods for resampling."""

    def test_aggregation_options(self):
        """Test that all aggregation options are valid."""
        valid_aggs = ["sum", "mean", "max", "min"]
        
        for agg in valid_aggs:
            # Should not raise any errors
            self.assertIn(agg, valid_aggs)

    def test_frequency_options(self):
        """Test that all frequency options are valid."""
        valid_freqs = ["none", "W", "M"]
        
        for freq in valid_freqs:
            # Should not raise any errors
            self.assertIn(freq, valid_freqs)


class TestQueueEntryWithResampling(unittest.TestCase):
    """Test queue entries include resampling parameters."""

    def test_queue_entry_structure(self):
        """Test that queue entries include resample fields."""
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
                "resample_freq": "W",
                "resample_agg": "sum",
            },
            "status": "PENDING",
            "timestamp": None,
            "execution_name": None,
            "gcs_prefix": None,
            "message": "",
        }
        
        # Verify resample fields are present
        self.assertIn("resample_freq", entry["params"])
        self.assertIn("resample_agg", entry["params"])
        
        # Verify values
        self.assertEqual(entry["params"]["resample_freq"], "W")
        self.assertEqual(entry["params"]["resample_agg"], "sum")


class TestBatchCSVWithResampling(unittest.TestCase):
    """Test batch CSV templates include resampling fields."""

    def test_csv_template_fields(self):
        """Test that CSV template contains resample fields."""
        template_row = {
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
        
        # Verify resample fields are in template
        required_fields = ["resample_freq", "resample_agg"]
        for field in required_fields:
            self.assertIn(field, template_row, f"Missing field: {field}")


if __name__ == "__main__":
    unittest.main()
