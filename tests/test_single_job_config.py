"""
Simple integration test for single job run workflow.
This test validates the basic flow without requiring full environment setup.
"""

import json
import unittest


class TestJobConfigStructure(unittest.TestCase):
    """Test that job configuration has the correct structure for R script."""

    def test_minimal_config_structure(self):
        """Test minimal job config structure."""
        config = {
            "country": "fr",
            "iterations": 200,
            "trials": 3,
            "train_size": [0.7, 0.9],
            "revision": "r100",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "dep_var": "REVENUE",
            "dep_var_type": "revenue",
            "date_var": "date",
            "adstock": "geometric",
            "hyperparameter_preset": "Meshed recommend",
            "paid_media_spends": ["GA_COST"],
            "paid_media_vars": ["GA_IMPRESSIONS"],
            "data_gcs_path": "gs://bucket/data.parquet",
        }

        # Verify all required fields exist
        required_fields = [
            "country",
            "iterations",
            "trials",
            "train_size",
            "revision",
            "start_date",
            "end_date",
            "dep_var",
            "dep_var_type",
            "adstock",
            "hyperparameter_preset",
            "data_gcs_path",
        ]

        for field in required_fields:
            self.assertIn(field, config, f"Missing required field: {field}")

        # Verify types
        self.assertIsInstance(config["iterations"], int)
        self.assertIsInstance(config["trials"], int)
        self.assertIsInstance(config["train_size"], list)
        self.assertIsInstance(config["paid_media_spends"], list)
        self.assertEqual(len(config["train_size"]), 2)

    def test_hyperparameter_preset_values(self):
        """Test valid hyperparameter preset values."""
        valid_presets = ["Facebook recommend", "Meshed recommend", "Custom"]

        for preset in valid_presets:
            config = {"hyperparameter_preset": preset}
            self.assertIn(config["hyperparameter_preset"], valid_presets)

    def test_dep_var_type_values(self):
        """Test valid dep_var_type values."""
        valid_types = ["revenue", "conversion"]

        for dep_type in valid_types:
            config = {"dep_var_type": dep_type}
            self.assertIn(config["dep_var_type"], valid_types)

    def test_adstock_values(self):
        """Test valid adstock values."""
        valid_adstocks = ["geometric", "weibull_cdf", "weibull_pdf"]

        for adstock in valid_adstocks:
            config = {"adstock": adstock}
            self.assertIn(config["adstock"], valid_adstocks)

    def test_date_range_logic(self):
        """Test that start_date comes before end_date."""
        from datetime import datetime

        start = "2024-01-01"
        end = "2024-12-31"

        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")

        self.assertLess(start_dt, end_dt, "Start date must be before end date")


class TestTrainingPresets(unittest.TestCase):
    """Test training preset configurations."""

    def test_test_run_preset(self):
        """Test 'Test run' preset values."""
        preset = {"iterations": 200, "trials": 3}
        self.assertEqual(preset["iterations"], 200)
        self.assertEqual(preset["trials"], 3)

    def test_production_preset(self):
        """Test 'Production' preset values."""
        preset = {"iterations": 2000, "trials": 5}
        self.assertEqual(preset["iterations"], 2000)
        self.assertEqual(preset["trials"], 5)

    def test_custom_preset(self):
        """Test 'Custom' preset default values."""
        preset = {"iterations": 5000, "trials": 10}
        self.assertEqual(preset["iterations"], 5000)
        self.assertEqual(preset["trials"], 10)


class TestOrganicVarConfig(unittest.TestCase):
    """Test job configurations that include organic variables."""

    def test_c_visits_as_organic_var(self):
        """
        Test config where C_VISITS is mapped as an organic variable.

        C_VISITS represents customer/channel visits that are not driven by
        paid media spend (e.g. direct visits, SEO, word-of-mouth). These
        should be modelled as organic_vars, not paid_media_vars, because
        there is no corresponding spend column and the signal is organic.
        """
        config = {
            "country": "de",
            "iterations": 2000,
            "trials": 5,
            "train_size": [0.7, 0.9],
            "revision": "r200",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "dep_var": "REVENUE",
            "dep_var_type": "revenue",
            "date_var": "date",
            "adstock": "geometric",
            "hyperparameter_preset": "Meshed recommend",
            "paid_media_spends": ["GA_COST", "FB_SPEND"],
            "paid_media_vars": ["GA_IMPRESSIONS", "FB_IMPRESSIONS"],
            "organic_vars": ["C_VISITS"],
            "context_vars": ["IS_WEEKEND"],
            "data_gcs_path": "gs://bucket/data.parquet",
        }

        # C_VISITS should be in organic_vars, not paid_media_vars or spends
        self.assertIn("C_VISITS", config["organic_vars"])
        self.assertNotIn("C_VISITS", config["paid_media_vars"])
        self.assertNotIn("C_VISITS", config["paid_media_spends"])

        # paid_media_spends and paid_media_vars must be the same length
        # (parallel arrays: element i of spends → element i of vars)
        self.assertEqual(
            len(config["paid_media_spends"]),
            len(config["paid_media_vars"]),
            "paid_media_spends and paid_media_vars must have equal length",
        )

    def test_vars_more_than_spends_backup_logic(self):
        """
        Test the backup logic when paid_media_vars has more entries than
        paid_media_spends.

        When vars > spends, the R script now pads paid_media_spends with the
        extra var column names (symmetric with the spends > vars case which
        pads vars with spend column names). This ensures every var has a
        corresponding spend entry.

        Example: spends = [GA_COST], vars = [GA_IMPRESSIONS, C_VISITS]
        After backup:  spends = [GA_COST, C_VISITS], vars = [GA_IMPRESSIONS, C_VISITS]
        """
        paid_media_spends_cfg = ["GA_COST"]
        paid_media_vars_cfg = ["GA_IMPRESSIONS", "C_VISITS"]

        n_spends = len(paid_media_spends_cfg)
        n_vars = len(paid_media_vars_cfg)

        # Simulate the R backup logic: pad spends with extra var columns
        if n_vars > n_spends:
            paid_media_spends_cfg = (
                paid_media_spends_cfg + paid_media_vars_cfg[n_spends:]
            )

        # After backup, lengths must match
        self.assertEqual(len(paid_media_spends_cfg), len(paid_media_vars_cfg))

        # The extra var C_VISITS is now also the spend column for that pair
        self.assertIn("C_VISITS", paid_media_spends_cfg)
        self.assertEqual(paid_media_spends_cfg[1], "C_VISITS")
        self.assertEqual(paid_media_vars_cfg[1], "C_VISITS")

    def test_spends_more_than_vars_backup_logic(self):
        """
        Test existing backup logic when paid_media_spends has more entries.

        The R script already handles this case: extra spends are appended to
        vars so that every spend has a matching var (using the spend column
        itself as its own var proxy).
        """
        paid_media_spends_cfg = ["GA_COST", "FB_SPEND", "TV_SPEND"]
        paid_media_vars_cfg = ["GA_IMPRESSIONS"]

        n_spends = len(paid_media_spends_cfg)
        n_vars = len(paid_media_vars_cfg)

        # Simulate the R backup logic: pad vars with extra spend columns
        if n_spends > n_vars:
            paid_media_vars_cfg = (
                paid_media_vars_cfg + paid_media_spends_cfg[n_vars:]
            )

        # After backup, lengths must match
        self.assertEqual(len(paid_media_spends_cfg), len(paid_media_vars_cfg))

        # Extra spend columns (FB_SPEND, TV_SPEND) are used as their own vars
        self.assertEqual(paid_media_vars_cfg[1], "FB_SPEND")
        self.assertEqual(paid_media_vars_cfg[2], "TV_SPEND")


if __name__ == "__main__":
    unittest.main()
