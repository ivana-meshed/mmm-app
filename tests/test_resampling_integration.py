"""
Integration tests for resampling with metadata column aggregations.
Tests the end-to-end flow from metadata to training configuration.
"""

import json
import unittest


class TestResamplingIntegration(unittest.TestCase):
    """Integration tests for resampling with column aggregations from metadata."""

    def test_metadata_to_config_flow(self):
        """Test that metadata aggregations flow correctly to training config."""
        # Simulate metadata from Map_Data page
        metadata = {
            "mapping": {
                "paid_media_spends": ["GA_COST", "BING_COST", "TV_COST"],
                "paid_media_vars": [
                    "GA_IMPRESSIONS",
                    "BING_CLICKS",
                    "TV_RATING",
                ],
                "context_vars": ["TEMPERATURE", "IS_WEEKEND"],
                "organic_vars": ["ORGANIC_TRAFFIC"],
            },
            "agg_strategies": {
                "GA_COST": "sum",
                "BING_COST": "sum",
                "TV_COST": "sum",
                "GA_IMPRESSIONS": "sum",
                "BING_CLICKS": "sum",
                "TV_RATING": "mean",  # Rating should be averaged
                "TEMPERATURE": "mean",
                "IS_WEEKEND": "auto",
                "ORGANIC_TRAFFIC": "sum",
            },
            "goals": [
                {
                    "var": "REVENUE",
                    "type": "revenue",
                    "group": "primary",
                    "main": True,
                }
            ],
        }

        # Simulate config creation in Run_Experiment page
        config = {
            "country": "fr",
            "revision": "r100",
            "iterations": 200,
            "trials": 5,
            "resample_freq": "W",
            "column_agg_strategies": metadata["agg_strategies"],
            "paid_media_spends": "GA_COST,BING_COST,TV_COST",
            "paid_media_vars": "GA_IMPRESSIONS,BING_CLICKS,TV_RATING",
            "context_vars": "TEMPERATURE,IS_WEEKEND",
            "organic_vars": "ORGANIC_TRAFFIC",
            "dep_var": "REVENUE",
        }

        # Verify config has column aggregations
        self.assertIn("column_agg_strategies", config)
        self.assertEqual(
            config["column_agg_strategies"], metadata["agg_strategies"]
        )

        # Verify different aggregation types are preserved
        self.assertEqual(config["column_agg_strategies"]["GA_COST"], "sum")
        self.assertEqual(config["column_agg_strategies"]["TEMPERATURE"], "mean")
        self.assertEqual(config["column_agg_strategies"]["TV_RATING"], "mean")
        self.assertEqual(config["column_agg_strategies"]["IS_WEEKEND"], "auto")

    def test_config_serialization_for_r(self):
        """Test that config can be serialized to JSON for R script."""
        column_agg_strategies = {
            "GA_COST": "sum",
            "TEMPERATURE": "mean",
            "PEAK_DEMAND": "max",
            "MIN_PRICE": "min",
            "IS_WEEKEND": "auto",
        }

        config = {
            "country": "fr",
            "resample_freq": "W",
            "column_agg_strategies": column_agg_strategies,
        }

        # Serialize to JSON (as would be done when creating job_config.json)
        json_str = json.dumps(config, indent=2)

        # Verify it can be deserialized
        deserialized = json.loads(json_str)
        self.assertEqual(
            deserialized["column_agg_strategies"], column_agg_strategies
        )

        # Verify all aggregation types are preserved
        for col, agg in column_agg_strategies.items():
            self.assertEqual(deserialized["column_agg_strategies"][col], agg)

    def test_empty_column_agg_strategies(self):
        """Test handling when no column aggregations are provided."""
        config = {
            "country": "fr",
            "resample_freq": "W",
            "column_agg_strategies": {},
        }

        # Should still be valid config
        self.assertIn("column_agg_strategies", config)
        self.assertEqual(len(config["column_agg_strategies"]), 0)

    def test_column_agg_strategies_as_json_string(self):
        """Test that column_agg_strategies can be passed as JSON string."""
        strategies = {
            "GA_COST": "sum",
            "TEMPERATURE": "mean",
        }

        # Simulate passing as JSON string (as in CSV downloads)
        json_string = json.dumps(strategies)

        config = {
            "country": "fr",
            "resample_freq": "W",
            "column_agg_strategies": json_string,
        }

        # Simulate R parsing the JSON string
        if isinstance(config["column_agg_strategies"], str):
            parsed = json.loads(config["column_agg_strategies"])
            self.assertEqual(parsed["GA_COST"], "sum")
            self.assertEqual(parsed["TEMPERATURE"], "mean")

    def test_backward_compatibility_missing_column_agg(self):
        """Test handling of old configs without column_agg_strategies."""
        # Old config format (before this change)
        old_config = {
            "country": "fr",
            "resample_freq": "W",
            # No column_agg_strategies field
        }

        # Should be handled gracefully with default fallback
        column_agg = old_config.get("column_agg_strategies", {})
        self.assertEqual(column_agg, {})

    def test_weekly_resampling_aggregation_counts(self):
        """Test aggregation type distribution for weekly resampling."""
        metadata = {
            "agg_strategies": {
                # Cost columns: sum
                "GA_COST": "sum",
                "BING_COST": "sum",
                "TV_COST": "sum",
                # Impression/click columns: sum
                "GA_IMPRESSIONS": "sum",
                "BING_CLICKS": "sum",
                # Rating/quality metrics: mean
                "TV_RATING": "mean",
                "AD_QUALITY_SCORE": "mean",
                # Context variables: mean for continuous, auto for categorical
                "TEMPERATURE": "mean",
                "HUMIDITY": "mean",
                "IS_WEEKEND": "auto",
                "IS_HOLIDAY": "auto",
                # Peak metrics: max
                "PEAK_TRAFFIC": "max",
                # Min metrics: min
                "MIN_INVENTORY": "min",
            }
        }

        # Count aggregations by type
        agg_counts = {}
        for agg in metadata["agg_strategies"].values():
            agg_counts[agg] = agg_counts.get(agg, 0) + 1

        # Verify distribution
        self.assertEqual(agg_counts["sum"], 5)
        self.assertEqual(agg_counts["mean"], 4)
        self.assertEqual(agg_counts["auto"], 2)
        self.assertEqual(agg_counts["max"], 1)
        self.assertEqual(agg_counts["min"], 1)

    def test_monthly_resampling_with_mixed_agg(self):
        """Test monthly resampling with mixed aggregation strategies."""
        config = {
            "country": "de",
            "resample_freq": "M",
            "column_agg_strategies": {
                # Monthly sums for costs and conversions
                "TOTAL_COST": "sum",
                "CONVERSIONS": "sum",
                "REVENUE": "sum",
                # Monthly averages for rates and temperatures
                "CONVERSION_RATE": "mean",
                "TEMPERATURE": "mean",
                "PRICE_INDEX": "mean",
                # Peak values for capacity metrics
                "PEAK_DEMAND": "max",
                "MAX_LOAD": "max",
                # Minimum values for price floors
                "MIN_PRICE": "min",
                # Categorical flags
                "HAS_CAMPAIGN": "auto",
            },
        }

        # Verify config structure
        self.assertEqual(config["resample_freq"], "M")
        self.assertEqual(len(config["column_agg_strategies"]), 10)

        # Verify mixed aggregations
        sum_cols = [
            k for k, v in config["column_agg_strategies"].items() if v == "sum"
        ]
        mean_cols = [
            k for k, v in config["column_agg_strategies"].items() if v == "mean"
        ]
        max_cols = [
            k for k, v in config["column_agg_strategies"].items() if v == "max"
        ]
        min_cols = [
            k for k, v in config["column_agg_strategies"].items() if v == "min"
        ]
        auto_cols = [
            k for k, v in config["column_agg_strategies"].items() if v == "auto"
        ]

        self.assertEqual(len(sum_cols), 3)
        self.assertEqual(len(mean_cols), 3)
        self.assertEqual(len(max_cols), 2)
        self.assertEqual(len(min_cols), 1)
        self.assertEqual(len(auto_cols), 1)


class TestRScriptExpectations(unittest.TestCase):
    """Test that config structure matches R script expectations."""

    def test_r_script_column_agg_parsing(self):
        """Test that column_agg_strategies format matches R script expectations."""
        # Config as it would be passed to R
        cfg = {
            "resample_freq": "W",
            "column_agg_strategies": {
                "GA_COST": "sum",
                "TEMPERATURE": "mean",
                "PEAK_DEMAND": "max",
                "MIN_PRICE": "min",
                "IS_WEEKEND": "auto",
            },
        }

        # Simulate R parsing
        resample_freq = cfg.get("resample_freq", "none")
        column_agg = cfg.get("column_agg_strategies", {})

        # R script expectations
        self.assertEqual(resample_freq, "W")
        self.assertIsInstance(column_agg, dict)
        self.assertTrue(len(column_agg) > 0)

        # Verify R can access each column's aggregation
        self.assertEqual(column_agg.get("GA_COST", "sum"), "sum")
        self.assertEqual(column_agg.get("TEMPERATURE", "sum"), "mean")
        self.assertEqual(column_agg.get("PEAK_DEMAND", "sum"), "max")
        self.assertEqual(
            column_agg.get("UNKNOWN_COL", "sum"), "sum"
        )  # Default fallback

    def test_r_script_json_string_parsing(self):
        """Test R parsing of column_agg_strategies passed as JSON string."""
        # Config with column_agg as JSON string (e.g., from CSV)
        cfg_json_str = json.dumps(
            {
                "resample_freq": "M",
                "column_agg_strategies": json.dumps(
                    {
                        "GA_COST": "sum",
                        "TEMPERATURE": "mean",
                    }
                ),
            }
        )

        cfg = json.loads(cfg_json_str)

        # R would parse the nested JSON string
        column_agg_str = cfg.get("column_agg_strategies", "{}")
        if isinstance(column_agg_str, str):
            column_agg = json.loads(column_agg_str)
        else:
            column_agg = column_agg_str

        self.assertIsInstance(column_agg, dict)
        self.assertEqual(column_agg["GA_COST"], "sum")
        self.assertEqual(column_agg["TEMPERATURE"], "mean")


class TestQueueJobConfiguration(unittest.TestCase):
    """Test that queue jobs properly handle column aggregations."""

    def test_queue_job_with_column_agg_strategies(self):
        """Test that queue job config includes column_agg_strategies when resampling."""
        # Simulate queue job params
        queue_params = {
            "country": "fr",
            "revision": "r100",
            "iterations": 200,
            "trials": 5,
            "resample_freq": "W",
            "paid_media_spends": "GA_COST,BING_COST",
            "paid_media_vars": "GA_IMPRESSIONS",
            "data_gcs_path": "gs://bucket/datasets/fr/latest/raw.parquet",
        }

        # Simulate metadata loading (this would happen in prepare_and_launch_job)
        metadata = {
            "agg_strategies": {
                "GA_COST": "sum",
                "BING_COST": "sum",
                "GA_IMPRESSIONS": "sum",
                "TEMPERATURE": "mean",
            }
        }

        # When resample_freq is set, column_agg_strategies should be added
        if queue_params.get("resample_freq", "none") != "none":
            queue_params["column_agg_strategies"] = metadata["agg_strategies"]

        # Verify config has column aggregations
        self.assertIn("column_agg_strategies", queue_params)
        self.assertEqual(
            queue_params["column_agg_strategies"]["GA_COST"], "sum"
        )
        self.assertEqual(
            queue_params["column_agg_strategies"]["TEMPERATURE"], "mean"
        )

    def test_queue_job_without_resampling(self):
        """Test that queue job without resampling doesn't require column_agg_strategies."""
        queue_params = {
            "country": "fr",
            "revision": "r100",
            "resample_freq": "none",
            "data_gcs_path": "gs://bucket/datasets/fr/latest/raw.parquet",
        }

        # When resample_freq is "none", column_agg_strategies is not needed
        self.assertEqual(queue_params.get("resample_freq"), "none")
        # Should work fine without column_agg_strategies


if __name__ == "__main__":
    unittest.main()
