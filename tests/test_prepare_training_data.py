"""
Tests for Prepare Training Data statistics functions.
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
        zeros_pct=(z / n * 100) if n else np.nan,
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
        # Zeros percentage: 2/5 = 40% (of total, not just non-null)
        self.assertAlmostEqual(stats["zeros_pct"], 40.0)

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


if __name__ == "__main__":
    unittest.main()
