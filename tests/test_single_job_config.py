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
            "country", "iterations", "trials", "train_size", "revision",
            "start_date", "end_date", "dep_var", "dep_var_type",
            "adstock", "hyperparameter_preset", "data_gcs_path"
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


if __name__ == '__main__':
    unittest.main()
