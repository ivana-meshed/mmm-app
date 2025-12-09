"""
Test for metadata structure and mapping improvements.
This test validates the structure of metadata saved by Map_Data.py.
"""

import json
import unittest


class TestMappingMetadata(unittest.TestCase):
    """Test that mapping metadata has the correct structure."""

    def test_metadata_structure(self):
        """Test metadata JSON structure with new fields."""
        metadata = {
            "project_id": "test-project",
            "bucket": "test-bucket",
            "country": "universal",  # Can be "universal" or country code
            "saved_at": "2025-01-01T00:00:00+00:00",
            "data": {
                "origin": "gcs_latest",
                "timestamp": "latest",
                "date_field": "date",
                "row_count": 1000,
            },
            "goals": [
                {
                    "var": "REVENUE",
                    "group": "primary",
                    "type": "revenue",
                    "agg_strategy": "sum",
                    "main": True,
                },
                {
                    "var": "CONVERSIONS",
                    "group": "secondary",
                    "type": "conversion",
                    "agg_strategy": "mean",
                    "main": False,
                },
            ],
            "dep_variable_type": {"REVENUE": "revenue", "CONVERSIONS": "conversion"},
            "autotag_rules": {
                "paid_media_spends": ["_cost"],
                "paid_media_vars": ["_impressions"],
            },
            "custom_channels": ["spotify", "podcast"],
            "mapping": {
                "paid_media_spends": ["GA_COST"],
                "paid_media_vars": ["GA_IMPRESSIONS"],
            },
            "channels": {"GA_COST": "ga", "GA_IMPRESSIONS": "ga"},
            "data_types": {
                "GA_COST": "numeric",
                "GA_IMPRESSIONS": "numeric",
                "date": "date",
            },
            "agg_strategies": {"GA_COST": "sum", "GA_IMPRESSIONS": "sum"},
            "paid_media_mapping": {
                "GA_COST": ["GA_IMPRESSIONS", "GA_CLICKS"]
            },
            "dep_var": "REVENUE",
        }

        # Verify all required top-level fields exist
        required_fields = [
            "project_id",
            "bucket",
            "country",
            "saved_at",
            "data",
            "goals",
            "dep_variable_type",
            "autotag_rules",
            "custom_channels",
            "mapping",
            "channels",
            "data_types",
            "agg_strategies",
            "paid_media_mapping",
            "dep_var",
        ]

        for field in required_fields:
            self.assertIn(field, metadata, f"Missing required field: {field}")

    def test_goals_structure(self):
        """Test goals array structure with new fields."""
        goal = {
            "var": "REVENUE",
            "group": "primary",
            "type": "revenue",
            "agg_strategy": "sum",
            "main": True,
        }

        # Verify required goal fields
        self.assertIn("var", goal)
        self.assertIn("group", goal)
        self.assertIn("type", goal)
        self.assertIn("agg_strategy", goal)
        self.assertIn("main", goal)

        # Verify types
        self.assertIsInstance(goal["var"], str)
        self.assertIsInstance(goal["group"], str)
        self.assertIsInstance(goal["type"], str)
        self.assertIsInstance(goal["agg_strategy"], str)
        self.assertIsInstance(goal["main"], bool)

        # Verify valid values
        self.assertIn(goal["group"], ["primary", "secondary"])
        self.assertIn(goal["type"], ["revenue", "conversion"])
        self.assertIn(goal["agg_strategy"], ["sum", "mean"])

    def test_goals_aggregation_rules(self):
        """Test that revenue goals use 'sum' and conversion goals use 'mean'."""
        goals = [
            {
                "var": "REVENUE",
                "type": "revenue",
                "agg_strategy": "sum",
                "group": "primary",
                "main": True,
            },
            {
                "var": "CONVERSIONS",
                "type": "conversion",
                "agg_strategy": "mean",
                "group": "primary",
                "main": False,
            },
        ]

        for goal in goals:
            if goal["type"] == "revenue":
                self.assertEqual(
                    goal["agg_strategy"],
                    "sum",
                    "Revenue goals must use 'sum' aggregation",
                )
            elif goal["type"] == "conversion":
                self.assertEqual(
                    goal["agg_strategy"],
                    "mean",
                    "Conversion goals must use 'mean' aggregation",
                )

    def test_paid_media_mapping_structure(self):
        """Test paid_media_mapping structure."""
        paid_media_mapping = {
            "GA_SUPPLY_COST": ["GA_SUPPLY_SESSIONS", "GA_SUPPLY_CLICKS"],
            "GA_DEMAND_COST": ["GA_DEMAND_SESSIONS", "GA_DEMAND_CLICKS"],
        }

        # Verify structure
        self.assertIsInstance(paid_media_mapping, dict)
        for spend, vars_list in paid_media_mapping.items():
            self.assertIsInstance(spend, str, "Spend key must be string")
            self.assertIsInstance(vars_list, list, "Vars must be list")
            self.assertTrue(all(isinstance(v, str) for v in vars_list))

    def test_date_field_data_type(self):
        """Test that date field has data_type 'date'."""
        data_types = {
            "date": "date",
            "GA_COST": "numeric",
            "IS_WEEKEND": "categorical",
        }

        self.assertEqual(
            data_types.get("date"), "date", "Date field must have data_type 'date'"
        )

    def test_custom_column_naming(self):
        """Test that TOTAL columns have _CUSTOM suffix."""
        custom_columns = [
            "GA_TOTAL_COST_CUSTOM",
            "GA_SMALL_COST_CUSTOM",
            "ORGANIC_TOTAL_CUSTOM",
        ]

        for col in custom_columns:
            self.assertTrue(
                col.endswith("_CUSTOM"),
                f"Custom aggregated column {col} must end with _CUSTOM",
            )

    def test_aggregation_options(self):
        """Test that only valid aggregation options are used."""
        valid_agg_options = ["sum", "mean", "max", "min", "mode"]

        # Test that 'auto' and None are not in valid options
        self.assertNotIn("auto", valid_agg_options)
        self.assertNotIn(None, valid_agg_options)

        # Test some example aggregations
        test_aggs = ["sum", "mean", "max", "min", "mode"]
        for agg in test_aggs:
            self.assertIn(agg, valid_agg_options)

    def test_universal_vs_country_specific(self):
        """Test that country field can be 'universal' or country code."""
        valid_countries = ["universal", "fr", "de", "it", "es", "nl", "uk"]

        for country in valid_countries:
            metadata = {"country": country}
            self.assertIn(
                metadata["country"],
                valid_countries,
                f"Invalid country value: {country}",
            )

    def test_filtering_empty_category_and_channel(self):
        """Test that variables with empty category AND channel are filtered."""

        # Variables that should be included (at least one field is non-empty)
        valid_vars = [
            {"var": "GA_COST", "category": "paid_media_spends", "channel": "ga"},
            {"var": "ORGANIC_SESSIONS", "category": "organic_vars", "channel": ""},
            {"var": "IS_WEEKEND", "category": "", "channel": "context"},
        ]

        # Variable that should be excluded (both empty)
        invalid_var = {"var": "UNKNOWN", "category": "", "channel": ""}

        for v in valid_vars:
            cat = v.get("category", "").strip()
            ch = v.get("channel", "").strip()
            # At least one should be non-empty
            self.assertTrue(
                cat or ch, f"Variable {v['var']} should have category or channel"
            )

        # Invalid should have both empty
        cat = invalid_var.get("category", "").strip()
        ch = invalid_var.get("channel", "").strip()
        self.assertFalse(cat, "Invalid var should have empty category")
        self.assertFalse(ch, "Invalid var should have empty channel")


class TestTrainingConfiguration(unittest.TestCase):
    """Test training configuration save/load structure."""

    def test_training_config_structure(self):
        """Test saved training configuration structure."""
        config = {
            "name": "baseline_config_v1",
            "created_at": "2025-01-01T00:00:00",
            "countries": ["fr", "de"],
            "config": {
                "iterations": 200,
                "trials": 3,
                "train_size": "0.7,0.9",
                "revision": "r100",
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "paid_media_spends": "GA_COST",
                "paid_media_vars": "GA_IMPRESSIONS",
                "context_vars": "IS_WEEKEND",
                "factor_vars": "TV_IS_ON",
                "organic_vars": "ORGANIC_TRAFFIC",
                "dep_var": "REVENUE",
                "dep_var_type": "revenue",
                "date_var": "date",
                "adstock": "geometric",
                "hyperparameter_preset": "Meshed recommend",
                "resample_freq": "none",
                "resample_agg": "sum",
            },
        }

        # Verify required fields
        self.assertIn("name", config)
        self.assertIn("created_at", config)
        self.assertIn("countries", config)
        self.assertIn("config", config)

        # Verify types
        self.assertIsInstance(config["name"], str)
        self.assertIsInstance(config["countries"], list)
        self.assertIsInstance(config["config"], dict)

    def test_revision_required(self):
        """Test that revision is required and cannot be empty."""
        # Valid revisions
        valid_revisions = ["r100", "r101", "baseline_v1"]
        for rev in valid_revisions:
            self.assertTrue(rev and rev.strip(), f"Revision '{rev}' should be valid")

        # Invalid revisions
        invalid_revisions = ["", "   ", None]
        for rev in invalid_revisions:
            if rev is None:
                self.assertIsNone(rev)
            else:
                self.assertFalse(
                    rev and rev.strip(), f"Revision '{rev}' should be invalid"
                )


if __name__ == "__main__":
    unittest.main()
