"""
Tests for Prepare Training Data statistics functions.

Note: The _num_stats function is duplicated here because it's defined inside
a Streamlit page file (Prepare_Training_Data.py) which has dependencies on
Streamlit session state and imports that make it difficult to import directly.
This is a common pattern for testing embedded functions in UI code.
"""

import unittest

import numpy as np
import pandas as pd


def _num_stats(s: pd.Series) -> dict:
    """
    Calculate numeric statistics for a pandas Series.

    Returns:
        dict with statistics: non_null, nulls, nulls_pct, zeros,
        zeros_pct, distinct, min, p10, median, mean, p90, max, std
    """
    s = pd.to_numeric(s, errors="coerce")
    n = len(s)
    nn = int(s.notna().sum())
    na = n - nn
    if nn == 0:
        return dict(
            non_null=nn,
            nulls=na,
            nulls_pct=np.nan,
            zeros=0,
            zeros_pct=np.nan,
            distinct=0,
            min=np.nan,
            p10=np.nan,
            median=np.nan,
            mean=np.nan,
            p90=np.nan,
            max=np.nan,
            std=np.nan,
        )
    s2 = s.dropna()
    z = int((s2 == 0).sum())
    return dict(
        non_null=nn,
        nulls=na,
        nulls_pct=(na / n * 100) if n else np.nan,
        zeros=z,
        zeros_pct=(z / nn * 100) if nn else np.nan,
        distinct=int(s2.nunique(dropna=True)),
        min=float(s2.min()) if not s2.empty else np.nan,
        p10=float(np.percentile(s2, 10)) if not s2.empty else np.nan,
        median=float(s2.median()) if not s2.empty else np.nan,
        mean=float(s2.mean()) if not s2.empty else np.nan,
        p90=float(np.percentile(s2, 90)) if not s2.empty else np.nan,
        max=float(s2.max()) if not s2.empty else np.nan,
        std=float(s2.std(ddof=1)) if s2.size > 1 else np.nan,
    )


class TestNumStats(unittest.TestCase):
    """Tests for _num_stats function."""

    def test_zeros_not_counted_for_nulls(self):
        """Test that zeros count excludes null values."""
        # 5 values: 1 null, 2 zeros, 2 non-zero values
        s = pd.Series([0, 1, np.nan, 0, 2])
        stats = _num_stats(s)

        # Only 2 actual zeros (not counting the null)
        self.assertEqual(stats["zeros"], 2)
        # 4 non-null values
        self.assertEqual(stats["non_null"], 4)
        # 1 null value
        self.assertEqual(stats["nulls"], 1)
        # Nulls percentage: 1/5 = 20%
        self.assertAlmostEqual(stats["nulls_pct"], 20.0)
        # Zeros percentage: 2/4 = 50% (of non-null values)
        self.assertAlmostEqual(stats["zeros_pct"], 50.0)

    def test_all_nulls(self):
        """Test handling of all-null series."""
        s = pd.Series([np.nan, np.nan, np.nan])
        stats = _num_stats(s)

        self.assertEqual(stats["non_null"], 0)
        self.assertEqual(stats["nulls"], 3)
        self.assertEqual(stats["zeros"], 0)
        self.assertTrue(np.isnan(stats["nulls_pct"]))
        self.assertTrue(np.isnan(stats["zeros_pct"]))

    def test_no_nulls_no_zeros(self):
        """Test series with no nulls and no zeros."""
        s = pd.Series([1, 2, 3, 4, 5])
        stats = _num_stats(s)

        self.assertEqual(stats["non_null"], 5)
        self.assertEqual(stats["nulls"], 0)
        self.assertEqual(stats["zeros"], 0)
        self.assertAlmostEqual(stats["nulls_pct"], 0.0)
        self.assertAlmostEqual(stats["zeros_pct"], 0.0)

    def test_all_zeros(self):
        """Test series with all zeros."""
        s = pd.Series([0, 0, 0, 0])
        stats = _num_stats(s)

        self.assertEqual(stats["non_null"], 4)
        self.assertEqual(stats["nulls"], 0)
        self.assertEqual(stats["zeros"], 4)
        self.assertAlmostEqual(stats["zeros_pct"], 100.0)

    def test_percentiles_and_stats(self):
        """Test percentile and statistical calculations."""
        s = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        stats = _num_stats(s)

        self.assertEqual(stats["non_null"], 10)
        self.assertEqual(stats["distinct"], 10)
        self.assertAlmostEqual(stats["min"], 1.0)
        self.assertAlmostEqual(stats["max"], 10.0)
        self.assertAlmostEqual(stats["mean"], 5.5)
        self.assertAlmostEqual(stats["median"], 5.5)
        # P10 of 1-10 should be 1.9
        self.assertAlmostEqual(stats["p10"], 1.9)
        # P90 of 1-10 should be 9.1
        self.assertAlmostEqual(stats["p90"], 9.1)


class TestDatetimeColumnFiltering(unittest.TestCase):
    """Tests for datetime column filtering in metrics calculation."""

    def test_is_numeric_dtype_excludes_datetime(self):
        """Test that datetime columns are correctly identified as non-numeric."""
        # Create test series
        datetime_series = pd.Series(pd.date_range("2024-01-01", periods=5))
        float_series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        int_series = pd.Series([1, 2, 3, 4, 5])

        # datetime64 should NOT be numeric (for our regression purposes)
        self.assertFalse(pd.api.types.is_numeric_dtype(datetime_series))
        # float64 should be numeric
        self.assertTrue(pd.api.types.is_numeric_dtype(float_series))
        # int64 should be numeric
        self.assertTrue(pd.api.types.is_numeric_dtype(int_series))

    def test_datetime_column_in_mixed_dataframe(self):
        """Test that datetime columns are excluded from numeric column list."""
        df = pd.DataFrame(
            {
                "date_col": pd.date_range("2024-01-01", periods=5),
                "float_col": [1.0, 2.0, 3.0, 4.0, 5.0],
                "int_col": [1, 2, 3, 4, 5],
            }
        )

        # Filter numeric columns like the production code does
        var_cols = ["date_col", "float_col", "int_col"]
        valid_cols = [
            c
            for c in var_cols
            if c in df.columns and pd.api.types.is_numeric_dtype(df[c])
        ]

        # datetime column should be excluded
        self.assertNotIn("date_col", valid_cols)
        # numeric columns should be included
        self.assertIn("float_col", valid_cols)
        self.assertIn("int_col", valid_cols)
        self.assertEqual(len(valid_cols), 2)


class TestDuplicateColumnHandling(unittest.TestCase):
    """Tests for handling duplicate column names in DataFrame selection."""

    def test_duplicate_column_selection_returns_dataframe(self):
        """
        Test that selecting the same column twice creates duplicate columns.

        This documents the pandas behavior that causes the TypeError when
        pd.to_numeric is called on a DataFrame instead of a Series.
        """
        df = pd.DataFrame({"col_a": [1.0, 2.0, 3.0], "col_b": [4.0, 5.0, 6.0]})

        # Selecting the same column twice creates duplicate column names
        col_name = "col_a"
        temp_df = df[[col_name, col_name]].copy()

        # This creates a DataFrame with duplicate column names
        self.assertEqual(list(temp_df.columns), ["col_a", "col_a"])

        # Accessing by column name returns a DataFrame (not Series!)
        result = temp_df[col_name]
        self.assertIsInstance(result, pd.DataFrame)

        # This is why pd.to_numeric fails - it expects a Series
        with self.assertRaises(TypeError):
            pd.to_numeric(result, errors="coerce")

    def test_single_column_selection_returns_series(self):
        """
        Test that selecting a single column returns a Series.

        This is the correct behavior when spend_col == var_col.
        """
        df = pd.DataFrame({"col_a": [1.0, 2.0, 3.0], "col_b": [4.0, 5.0, 6.0]})

        # Selecting a single column works correctly
        col_name = "col_a"
        temp_df = df[[col_name]].copy()

        # Accessing by column name returns a Series
        result = temp_df[col_name]
        self.assertIsInstance(result, pd.Series)

        # pd.to_numeric works on a Series
        numeric_result = pd.to_numeric(result, errors="coerce")
        self.assertIsInstance(numeric_result, pd.Series)


class TestOtherColumnFiltering(unittest.TestCase):
    """Tests for filtering organic variables from the 'Other' category."""

    def test_other_cols_excludes_already_categorized_columns(self):
        """
        Test that columns already in other categories are excluded from Other.

        This tests the logic that filters out organic_vars (and other category
        vars) from the 'other_cols' list to prevent duplicates.
        """
        # Simulate the data_types and channels maps from metadata
        data_types_map = {
            "date_col": "datetime",
            "spend_col": "numeric",
            "organic_col": "numeric",  # This is in organic_vars
            "context_col": "numeric",  # This is in context_vars
            "unknown_col": "numeric",  # This should be in Other
        }
        channels_map = {
            "spend_col": {"platform": "google"},
        }

        # Simulate the category lists from mapping
        paid_spend = ["spend_col"]
        paid_vars = []
        organic_vars = ["organic_col"]
        context_vars = ["context_col"]
        factor_vars = []

        # Simulate the columns present in the dataframe
        prof_df_columns = [
            "date_col",
            "spend_col",
            "organic_col",
            "context_col",
            "unknown_col",
        ]

        # Replicate the logic from the actual code
        all_categorized_cols = set(
            paid_spend + paid_vars + organic_vars + context_vars + factor_vars
        )

        other_cols = [
            c
            for c in data_types_map.keys()
            if c not in channels_map
            and c in prof_df_columns
            and c not in all_categorized_cols
        ]

        # 'unknown_col' should be in Other (not in channels, not categorized)
        self.assertIn("unknown_col", other_cols)

        # 'organic_col' should NOT be in Other (already in organic_vars)
        self.assertNotIn("organic_col", other_cols)

        # 'context_col' should NOT be in Other (already in context_vars)
        self.assertNotIn("context_col", other_cols)

        # 'spend_col' should NOT be in Other (it's in channels_map)
        self.assertNotIn("spend_col", other_cols)

        # 'date_col' should be in Other (not in channels, not categorized)
        self.assertIn("date_col", other_cols)

        # Only date_col and unknown_col should be in Other
        self.assertEqual(set(other_cols), {"date_col", "unknown_col"})


class TestVIFCalculation(unittest.TestCase):
    """Tests for VIF calculation with sparse data."""

    def test_vif_with_sparse_data(self):
        """
        Test that VIF calculation handles sparse data by filling NaN with means.

        With 30% NaN per column across 13 columns, dropna() would leave only
        1 complete row, which is insufficient for VIF calculation. The fix
        fills NaN with column means to preserve more rows.
        """
        from statsmodels.stats.outliers_influence import (
            variance_inflation_factor,
        )

        # Create sparse data: 50 rows, 13 columns, 30% NaN per column
        np.random.seed(42)
        n_rows = 50
        n_cols = 13

        data = {}
        for i in range(n_cols):
            col_data = np.random.randn(n_rows).tolist()
            # Set 30% of values to NaN
            nan_indices = np.random.choice(
                n_rows, size=int(n_rows * 0.3), replace=False
            )
            for idx in nan_indices:
                col_data[idx] = np.nan
            data[f"col_{i}"] = col_data

        df = pd.DataFrame(data)

        # Using old approach (dropna): only 1 row remains
        complete_cases = len(df.dropna())
        self.assertEqual(complete_cases, 1)

        # Using new approach (fillna with mean): all 50 rows preserved
        df_filled = df.fillna(df.mean())
        self.assertEqual(len(df_filled), 50)

        # VIF should be calculable with filled data
        vif_array = np.asarray(df_filled.values, dtype=np.float64)
        for i in range(n_cols):
            vif = variance_inflation_factor(vif_array, i)
            self.assertFalse(np.isnan(vif))
            self.assertTrue(vif >= 1.0)  # VIF is always >= 1

    def test_vif_with_all_nan_column(self):
        """
        Test that columns that are entirely NaN are dropped before VIF calc.
        """
        # Create data with one column entirely NaN
        np.random.seed(42)
        n_rows = 50
        n_cols = 5

        data = {f"col_{i}": np.random.randn(n_rows) for i in range(n_cols)}
        data["col_2"] = [np.nan] * n_rows  # Entirely NaN
        df = pd.DataFrame(data)

        # Drop columns that are all NaN, fill remaining NaN with mean
        df_processed = df.dropna(axis=1, how="all").fillna(df.mean())

        # col_2 should be dropped
        self.assertNotIn("col_2", df_processed.columns)

        # Other columns should remain
        self.assertEqual(len(df_processed.columns), n_cols - 1)

    def test_vif_with_duplicate_column_names(self):
        """
        Test that duplicate column names are removed before VIF calculation.

        When the same media response variable is selected for multiple spend
        columns, the column list will have duplicates. These must be removed
        to avoid pandas creating a DataFrame with duplicate column names,
        which causes pd.to_numeric to fail.
        """
        from statsmodels.stats.outliers_influence import (
            variance_inflation_factor,
        )

        # Create test data
        np.random.seed(42)
        n_rows = 100
        df = pd.DataFrame(
            {
                "col_a": np.random.randn(n_rows),
                "col_b": np.random.randn(n_rows),
                "col_c": np.random.randn(n_rows),
            }
        )

        # Simulate duplicate column names in input list
        cols_with_dupes = ["col_a", "col_b", "col_c", "col_a"]  # col_a is dupe

        # Use the deduplication logic from the fix
        seen = set()
        valid_cols = []
        for c in cols_with_dupes:
            if c not in seen and c in df.columns:
                seen.add(c)
                valid_cols.append(c)

        # Should have only unique columns
        self.assertEqual(len(valid_cols), 3)
        self.assertEqual(valid_cols, ["col_a", "col_b", "col_c"])

        # VIF should be calculable with deduplicated columns
        vif_df = df[valid_cols]
        vif_array = np.asarray(vif_df.values, dtype=np.float64)
        for i in range(len(valid_cols)):
            vif = variance_inflation_factor(vif_array, i)
            self.assertFalse(np.isnan(vif))
            self.assertFalse(np.isinf(vif))  # No inf due to duplicates


class TestFilterLeadingZeros(unittest.TestCase):
    """Tests for filtering leading zero rows from media response variables."""

    def test_filter_leading_zeros_basic(self):
        """Test basic filtering of leading zero rows."""
        # Create test data with leading zeros in media columns
        df = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=6),
                "media_a": [0, 0, 10, 20, 30, 40],
                "media_b": [0, 0, 5, 15, 25, 35],
                "other_col": [1, 2, 3, 4, 5, 6],
            }
        )

        # Replicate the filter logic from the page
        media_var_cols = ["media_a", "media_b"]
        valid_cols = [c for c in media_var_cols if c in df.columns]

        any_nonzero = df[valid_cols].apply(lambda row: (row != 0).any(), axis=1)

        first_nonzero_idx = any_nonzero.idxmax() if any_nonzero.any() else None

        if first_nonzero_idx is not None:
            result = df.loc[first_nonzero_idx:].reset_index(drop=True)
        else:
            result = df

        # Should have 4 rows (from index 2 onwards)
        self.assertEqual(len(result), 4)
        # First row should have media_a = 10
        self.assertEqual(result.iloc[0]["media_a"], 10)
        # other_col should also be filtered (to preserve timeline)
        self.assertEqual(result.iloc[0]["other_col"], 3)

    def test_filter_leading_zeros_no_leading_zeros(self):
        """Test filtering when there are no leading zeros."""
        df = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=4),
                "media_a": [10, 20, 30, 40],
                "media_b": [5, 15, 25, 35],
            }
        )

        media_var_cols = ["media_a", "media_b"]
        valid_cols = [c for c in media_var_cols if c in df.columns]

        any_nonzero = df[valid_cols].apply(lambda row: (row != 0).any(), axis=1)

        first_nonzero_idx = any_nonzero.idxmax() if any_nonzero.any() else None

        if first_nonzero_idx is not None:
            result = df.loc[first_nonzero_idx:].reset_index(drop=True)
        else:
            result = df

        # Should have all 4 rows (no leading zeros)
        self.assertEqual(len(result), 4)
        self.assertEqual(result.iloc[0]["media_a"], 10)

    def test_filter_leading_zeros_all_zeros(self):
        """Test filtering when all rows are zeros."""
        df = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=4),
                "media_a": [0, 0, 0, 0],
                "media_b": [0, 0, 0, 0],
            }
        )

        media_var_cols = ["media_a", "media_b"]
        valid_cols = [c for c in media_var_cols if c in df.columns]

        any_nonzero = df[valid_cols].apply(lambda row: (row != 0).any(), axis=1)

        # When all zeros, any_nonzero.any() is False
        if not any_nonzero.any():
            result = df
        else:
            first_nonzero_idx = any_nonzero.idxmax()
            result = df.loc[first_nonzero_idx:].reset_index(drop=True)

        # Should keep original data when all zeros
        self.assertEqual(len(result), 4)

    def test_filter_leading_zeros_mixed_columns(self):
        """Test filtering when one column has zeros but another has values."""
        df = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=5),
                "media_a": [0, 0, 10, 20, 30],
                "media_b": [0, 5, 10, 15, 20],  # First non-zero at index 1
            }
        )

        media_var_cols = ["media_a", "media_b"]
        valid_cols = [c for c in media_var_cols if c in df.columns]

        any_nonzero = df[valid_cols].apply(lambda row: (row != 0).any(), axis=1)

        first_nonzero_idx = any_nonzero.idxmax() if any_nonzero.any() else None

        if first_nonzero_idx is not None:
            result = df.loc[first_nonzero_idx:].reset_index(drop=True)
        else:
            result = df

        # Should have 4 rows (from index 1 onwards)
        self.assertEqual(len(result), 4)
        # First row should be at original index 1
        self.assertEqual(result.iloc[0]["media_a"], 0)
        self.assertEqual(result.iloc[0]["media_b"], 5)


if __name__ == "__main__":
    unittest.main()
