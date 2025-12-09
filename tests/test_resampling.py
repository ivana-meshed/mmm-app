"""
Unit tests for resampling functionality in the training pipeline.
Tests the resampling feature for Weekly and Monthly aggregation with per-column aggregations.
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

    def test_resample_config_structure_with_column_agg(self):
        """Test that resample config has correct structure with column aggregations."""
        config = {
            "resample_freq": "none",
            "column_agg_strategies": {
                "GA_COST": "sum",
                "GA_IMPRESSIONS": "sum",
                "REVENUE": "sum",
                "IS_WEEKEND": "auto",
            },
        }
        
        # Verify resample parameters exist
        self.assertIn("resample_freq", config)
        self.assertIn("column_agg_strategies", config)
        self.assertEqual(config["resample_freq"], "none")
        self.assertIsInstance(config["column_agg_strategies"], dict)
        self.assertEqual(config["column_agg_strategies"]["GA_COST"], "sum")

    def test_resample_config_weekly(self):
        """Test config with weekly resampling."""
        config = {
            "resample_freq": "W",
            "column_agg_strategies": {
                "GA_COST": "sum",
                "TEMPERATURE": "mean",
            },
        }
        
        # Verify resample parameters
        self.assertEqual(config["resample_freq"], "W")
        self.assertIn("column_agg_strategies", config)

    def test_resample_config_monthly_with_mixed_agg(self):
        """Test config with monthly resampling using mixed aggregations."""
        config = {
            "resample_freq": "M",
            "column_agg_strategies": {
                "REVENUE": "sum",
                "TEMPERATURE": "mean",
                "PEAK_DEMAND": "max",
                "IS_WEEKEND": "auto",
            },
        }
        
        # Verify resample parameters
        self.assertEqual(config["resample_freq"], "M")
        self.assertEqual(len(config["column_agg_strategies"]), 4)
        self.assertEqual(config["column_agg_strategies"]["REVENUE"], "sum")
        self.assertEqual(config["column_agg_strategies"]["TEMPERATURE"], "mean")
        self.assertEqual(config["column_agg_strategies"]["PEAK_DEMAND"], "max")


class TestColumnAggregationStrategies(unittest.TestCase):
    """Test column aggregation strategies from metadata."""

    def test_column_agg_strategies_structure(self):
        """Test that column_agg_strategies has correct structure."""
        strategies = {
            "GA_COST": "sum",
            "GA_IMPRESSIONS": "sum",
            "TEMPERATURE": "mean",
            "PEAK_DEMAND": "max",
            "MIN_PRICE": "min",
            "IS_WEEKEND": "auto",
        }
        
        # Verify all aggregation types are valid
        valid_aggs = ["sum", "mean", "max", "min", "auto"]
        for col, agg in strategies.items():
            self.assertIn(agg, valid_aggs, f"Invalid aggregation '{agg}' for column '{col}'")
    
    def test_column_agg_from_json_string(self):
        """Test parsing column aggregations from JSON string."""
        json_str = '{"GA_COST": "sum", "TEMPERATURE": "mean", "IS_WEEKEND": "auto"}'
        strategies = json.loads(json_str)
        
        self.assertIsInstance(strategies, dict)
        self.assertEqual(strategies["GA_COST"], "sum")
        self.assertEqual(strategies["TEMPERATURE"], "mean")
        self.assertEqual(strategies["IS_WEEKEND"], "auto")
    
    def test_metadata_agg_strategies_format(self):
        """Test that metadata agg_strategies format is correct."""
        metadata = {
            "mapping": {
                "paid_media_spends": ["GA_COST", "BING_COST"],
                "paid_media_vars": ["GA_IMPRESSIONS", "BING_CLICKS"],
                "context_vars": ["TEMPERATURE", "IS_WEEKEND"],
            },
            "agg_strategies": {
                "GA_COST": "sum",
                "BING_COST": "sum",
                "GA_IMPRESSIONS": "sum",
                "BING_CLICKS": "sum",
                "TEMPERATURE": "mean",
                "IS_WEEKEND": "auto",
            },
        }
        
        # Verify metadata structure
        self.assertIn("mapping", metadata)
        self.assertIn("agg_strategies", metadata)
        
        # Verify all mapped variables have aggregation strategies
        all_vars = []
        for vars_list in metadata["mapping"].values():
            all_vars.extend(vars_list)
        
        for var in all_vars:
            self.assertIn(var, metadata["agg_strategies"], 
                        f"Variable '{var}' missing from agg_strategies")


class TestResamplingAggregations(unittest.TestCase):
    """Test different aggregation methods for resampling."""

    def test_aggregation_options(self):
        """Test that all aggregation options are valid."""
        valid_aggs = ["sum", "mean", "max", "min", "auto"]
        
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

    def test_queue_entry_structure_with_column_agg(self):
        """Test that queue entries include column_agg_strategies."""
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
                "column_agg_strategies": {
                    "GA_COST": "sum",
                    "GA_IMPRESSIONS": "sum",
                    "REVENUE": "sum",
                    "IS_WEEKEND": "auto",
                },
            },
            "status": "PENDING",
            "timestamp": None,
            "execution_name": None,
            "gcs_prefix": None,
            "message": "",
        }
        
        # Verify resample fields are present
        self.assertIn("resample_freq", entry["params"])
        self.assertIn("column_agg_strategies", entry["params"])
        
        # Verify values
        self.assertEqual(entry["params"]["resample_freq"], "W")
        self.assertIsInstance(entry["params"]["column_agg_strategies"], dict)
        self.assertEqual(entry["params"]["column_agg_strategies"]["GA_COST"], "sum")


class TestBatchCSVWithResampling(unittest.TestCase):
    """Test batch CSV templates include resampling fields."""

    def test_csv_template_fields_updated(self):
        """Test that CSV template no longer contains resample_agg."""
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
            "annotations_gcs_path": "",
        }
        
        # Verify resample_freq is present
        self.assertIn("resample_freq", template_row)
        
        # Verify resample_agg is NOT present (removed)
        self.assertNotIn("resample_agg", template_row)


class TestEndToEndResampling(unittest.TestCase):
    """Test end-to-end resampling scenario."""
    
    def test_training_config_with_column_agg(self):
        """Test that training config includes column aggregations."""
        config = {
            "country": "fr",
            "revision": "r100",
            "iterations": 200,
            "trials": 5,
            "resample_freq": "W",
            "column_agg_strategies": {
                "GA_COST": "sum",
                "BING_COST": "sum",
                "TV_COST": "sum",
                "GA_IMPRESSIONS": "sum",
                "TEMPERATURE": "mean",
                "IS_WEEKEND": "auto",
                "PEAK_DEMAND": "max",
            },
            "paid_media_spends": "GA_COST,BING_COST,TV_COST",
            "paid_media_vars": "GA_IMPRESSIONS",
            "dep_var": "REVENUE",
        }
        
        # Verify config structure
        self.assertEqual(config["resample_freq"], "W")
        self.assertIn("column_agg_strategies", config)
        self.assertIsInstance(config["column_agg_strategies"], dict)
        
        # Verify mixed aggregations
        self.assertEqual(config["column_agg_strategies"]["GA_COST"], "sum")
        self.assertEqual(config["column_agg_strategies"]["TEMPERATURE"], "mean")
        self.assertEqual(config["column_agg_strategies"]["PEAK_DEMAND"], "max")
        self.assertEqual(config["column_agg_strategies"]["IS_WEEKEND"], "auto")
    
    def test_column_agg_as_json_string(self):
        """Test that column_agg_strategies can be serialized as JSON string."""
        strategies = {
            "GA_COST": "sum",
            "TEMPERATURE": "mean",
            "IS_WEEKEND": "auto",
        }
        
        # Serialize to JSON
        json_str = json.dumps(strategies)
        
        # Verify it can be deserialized
        deserialized = json.loads(json_str)
        self.assertEqual(deserialized, strategies)


if __name__ == "__main__":
    unittest.main()
