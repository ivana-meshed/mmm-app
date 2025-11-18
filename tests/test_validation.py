"""
Tests for validation utilities.
"""

import unittest
from datetime import datetime

import pandas as pd

from app.utils.validation import (
    validate_column_types,
    validate_data_completeness,
    validate_dataframe_schema,
    validate_date_range,
    validate_numeric_range,
    validate_training_config,
)


class TestDataFrameValidation(unittest.TestCase):
    """Tests for DataFrame validation functions."""

    def test_validate_dataframe_schema_valid(self):
        """Test schema validation with valid DataFrame."""
        df = pd.DataFrame({"col1": [1, 2, 3], "col2": ["a", "b", "c"]})
        is_valid, msg = validate_dataframe_schema(df, ["col1", "col2"])
        self.assertTrue(is_valid)
        self.assertEqual(msg, "")

    def test_validate_dataframe_schema_missing_columns(self):
        """Test schema validation with missing columns."""
        df = pd.DataFrame({"col1": [1, 2, 3]})
        is_valid, msg = validate_dataframe_schema(df, ["col1", "col2", "col3"])
        self.assertFalse(is_valid)
        self.assertIn("col2", msg)
        self.assertIn("col3", msg)

    def test_validate_dataframe_schema_empty(self):
        """Test schema validation with empty DataFrame."""
        df = pd.DataFrame()
        is_valid, msg = validate_dataframe_schema(df, ["col1"])
        self.assertFalse(is_valid)
        self.assertIn("empty", msg.lower())


class TestDateValidation(unittest.TestCase):
    """Tests for date validation functions."""

    def test_validate_date_range_valid(self):
        """Test date range validation with valid dates."""
        is_valid, msg = validate_date_range("2024-01-01", "2024-12-31")
        self.assertTrue(is_valid)
        self.assertEqual(msg, "")

    def test_validate_date_range_invalid_order(self):
        """Test date range validation with invalid order."""
        is_valid, msg = validate_date_range("2024-12-31", "2024-01-01")
        self.assertFalse(is_valid)
        self.assertIn("before", msg.lower())

    def test_validate_date_range_invalid_format(self):
        """Test date range validation with invalid format."""
        is_valid, msg = validate_date_range("2024/01/01", "2024-12-31")
        self.assertFalse(is_valid)
        self.assertIn("format", msg.lower())


class TestNumericValidation(unittest.TestCase):
    """Tests for numeric validation functions."""

    def test_validate_numeric_range_valid(self):
        """Test numeric range validation with valid value."""
        is_valid, msg = validate_numeric_range(5, min_value=0, max_value=10)
        self.assertTrue(is_valid)
        self.assertEqual(msg, "")

    def test_validate_numeric_range_below_min(self):
        """Test numeric range validation below minimum."""
        is_valid, msg = validate_numeric_range(-5, min_value=0, max_value=10)
        self.assertFalse(is_valid)
        self.assertIn("below", msg.lower())

    def test_validate_numeric_range_above_max(self):
        """Test numeric range validation above maximum."""
        is_valid, msg = validate_numeric_range(15, min_value=0, max_value=10)
        self.assertFalse(is_valid)
        self.assertIn("exceed", msg.lower())

    def test_validate_numeric_range_none_allowed(self):
        """Test numeric range validation with None when allowed."""
        is_valid, msg = validate_numeric_range(None, allow_none=True)
        self.assertTrue(is_valid)

    def test_validate_numeric_range_none_not_allowed(self):
        """Test numeric range validation with None when not allowed."""
        is_valid, msg = validate_numeric_range(None, allow_none=False)
        self.assertFalse(is_valid)
        self.assertIn("none", msg.lower())


class TestDataCompleteness(unittest.TestCase):
    """Tests for data completeness validation."""

    def test_validate_data_completeness_valid(self):
        """Test completeness validation with complete data."""
        df = pd.DataFrame({"col1": [1, 2, 3], "col2": [4, 5, 6]})
        result = validate_data_completeness(df, ["col1", "col2"])
        self.assertTrue(result["is_valid"])
        self.assertEqual(len(result["issues"]), 0)

    def test_validate_data_completeness_missing_values(self):
        """Test completeness validation with missing values."""
        df = pd.DataFrame({"col1": [1, None, 3], "col2": [4, 5, 6]})
        result = validate_data_completeness(
            df, ["col1", "col2"], max_missing_pct=0.2
        )
        # 1/3 = 33% missing is above 20% threshold
        self.assertFalse(result["is_valid"])
        self.assertGreater(len(result["issues"]), 0)

    def test_validate_data_completeness_missing_column(self):
        """Test completeness validation with missing column."""
        df = pd.DataFrame({"col1": [1, 2, 3]})
        result = validate_data_completeness(df, ["col1", "col2"])
        self.assertFalse(result["is_valid"])
        self.assertTrue(any("col2" in issue for issue in result["issues"]))


class TestColumnTypes(unittest.TestCase):
    """Tests for column type validation."""

    def test_validate_column_types_valid(self):
        """Test type validation with correct types."""
        df = pd.DataFrame(
            {
                "num": [1, 2, 3],
                "str": ["a", "b", "c"],
                "date": pd.date_range("2024-01-01", periods=3),
            }
        )
        is_valid, errors = validate_column_types(
            df, {"num": "numeric", "str": "string", "date": "date"}
        )
        self.assertTrue(is_valid)
        self.assertEqual(len(errors), 0)

    def test_validate_column_types_invalid(self):
        """Test type validation with incorrect types."""
        df = pd.DataFrame({"col1": ["a", "b", "c"]})
        is_valid, errors = validate_column_types(df, {"col1": "numeric"})
        self.assertFalse(is_valid)
        self.assertGreater(len(errors), 0)


class TestTrainingConfigValidation(unittest.TestCase):
    """Tests for training configuration validation."""

    def test_validate_training_config_valid(self):
        """Test config validation with valid configuration."""
        config = {
            "country": "fr",
            "iterations": 2000,
            "trials": 5,
            "dep_var": "REVENUE",
            "paid_media_spends": ["GA_COST"],
            "paid_media_vars": ["GA_IMPRESSIONS"],
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
        }
        is_valid, msg = validate_training_config(config)
        self.assertTrue(is_valid)
        self.assertEqual(msg, "")

    def test_validate_training_config_missing_field(self):
        """Test config validation with missing required field."""
        config = {
            "country": "fr",
            "iterations": 2000,
            # Missing trials
            "dep_var": "REVENUE",
            "paid_media_spends": ["GA_COST"],
            "paid_media_vars": ["GA_IMPRESSIONS"],
        }
        is_valid, msg = validate_training_config(config)
        self.assertFalse(is_valid)
        self.assertIn("trials", msg.lower())

    def test_validate_training_config_invalid_iterations(self):
        """Test config validation with invalid iterations."""
        config = {
            "country": "fr",
            "iterations": -100,  # Invalid
            "trials": 5,
            "dep_var": "REVENUE",
            "paid_media_spends": ["GA_COST"],
            "paid_media_vars": ["GA_IMPRESSIONS"],
        }
        is_valid, msg = validate_training_config(config)
        self.assertFalse(is_valid)
        self.assertIn("iterations", msg.lower())

    def test_validate_training_config_invalid_date_range(self):
        """Test config validation with invalid date range."""
        config = {
            "country": "fr",
            "iterations": 2000,
            "trials": 5,
            "dep_var": "REVENUE",
            "paid_media_spends": ["GA_COST"],
            "paid_media_vars": ["GA_IMPRESSIONS"],
            "start_date": "2024-12-31",
            "end_date": "2024-01-01",  # End before start
        }
        is_valid, msg = validate_training_config(config)
        self.assertFalse(is_valid)
        self.assertIn("date", msg.lower())


if __name__ == "__main__":
    unittest.main()
