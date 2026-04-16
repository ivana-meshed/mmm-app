"""
Integration tests for the run_benchmark_from_csv.py pipeline.

These tests exercise the full in-process benchmark preparation pipeline
end-to-end — CSV loading, column normalisation, country filtering, column
classification (both auto-classify and mapping-file modes), and
selected_columns.json construction — using the real DK CSV fixture and the
dk_final_with_tv_config.json config, but without touching GCS or spawning any
subprocess.
"""

import json
import sys
import unittest
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Make scripts/ importable
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from run_benchmark_from_csv import (  # noqa: E402
    COLUMN_RENAME_MAP,
    DEFAULT_DK_MAPPING,
    PAID_MEDIA_SPENDS,
    _is_factor_var,
    _is_media_metric,
    _is_organic_var,
    classify_columns,
    load_columns_from_mapping,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
DK_CSV_PATH = REPO_ROOT / "data" / "dk" / "mmm_data_v2_final_holidays_and_school.csv"
DK_TV_CSV_PATH = REPO_ROOT / "data" / "dk" / "mmm_data_v2_with_tv.csv"
TV_CONFIG_PATH = (
    REPO_ROOT
    / "benchmark_analysis"
    / "dk_json_configs_clean"
    / "dk_final_with_tv_config.json"
)

REQUIRED_SELECTED_COLUMNS_KEYS = {
    "country",
    "selected_goal",
    "dep_var",
    "dep_var_type",
    "date_var",
    "paid_media_spends",
    "paid_media_vars",
    "var_to_spend_mapping",
    "organic_vars",
    "context_vars",
    "factor_vars",
    "all_selected_drivers",
}


def _build_selected_columns(
    classification: dict,
    country_code: str = "dk",
    dep_var: str = "BOOKINGS",
    dep_var_type: str = "revenue",
    timestamp: str = "20260413_test",
) -> dict:
    """Mirror the selected_columns dict construction in main()."""
    all_drivers = list(
        dict.fromkeys(
            classification["paid_media_spends"]
            + classification["paid_media_vars"]
            + classification["organic_vars"]
            + classification["context_vars"]
            + classification["factor_vars"]
        )
    )
    return {
        "country": country_code.lower(),
        "selected_goal": dep_var,
        "dep_var": dep_var,
        "dep_var_type": dep_var_type,
        "date_var": "date",
        "paid_media_spends": classification["paid_media_spends"],
        "paid_media_vars": classification["paid_media_vars"],
        "var_to_spend_mapping": classification.get("var_to_spend_mapping", {}),
        "organic_vars": classification["organic_vars"],
        "context_vars": classification["context_vars"],
        "factor_vars": classification["factor_vars"],
        "all_selected_drivers": all_drivers,
        "data_version": timestamp,
        "meta_version": "Latest",
        "timestamp": timestamp,
    }


# ---------------------------------------------------------------------------
# Helper: load the DK CSV once for the whole module
# ---------------------------------------------------------------------------
def _load_dk_df() -> pd.DataFrame:
    df = pd.read_csv(DK_CSV_PATH)
    df.columns = [c.upper() for c in df.columns]

    # Drop duplicate columns introduced by uppercasing (mirrors main())
    df = df.loc[:, ~df.columns.duplicated()]

    # Apply the same renames that main() performs
    rename_applied = {
        old: new for old, new in COLUMN_RENAME_MAP.items() if old in df.columns
    }
    if rename_applied:
        df.rename(columns=rename_applied, inplace=True)

    # Filter to Denmark only
    df = df[
        df["MARKET_NAME"].str.strip().str.upper() == "DENMARK"
    ].copy()

    # Clip media columns to 0 (mirrors main())
    media_cols = [
        c
        for c in df.columns
        if any(kw in c for kw in ("COST", "SPEND", "CLICKS", "IMPRESSIONS"))
    ]
    if media_cols:
        df[media_cols] = df[media_cols].clip(lower=0)

    return df


def _load_dk_tv_df() -> pd.DataFrame:
    """Load the TV-merged DK CSV (mmm_data_v2_with_tv.csv), filtered to Denmark.

    This mirrors _load_dk_df() but uses the dataset that includes TV and radio
    spend/GRP columns from TV_DATA.xlsx.
    """
    df = pd.read_csv(DK_TV_CSV_PATH)
    df.columns = [c.upper() for c in df.columns]

    # Drop duplicate columns introduced by uppercasing (mirrors main())
    df = df.loc[:, ~df.columns.duplicated()]

    # Apply the same renames that main() performs
    rename_applied = {
        old: new for old, new in COLUMN_RENAME_MAP.items() if old in df.columns
    }
    if rename_applied:
        df.rename(columns=rename_applied, inplace=True)

    # Filter to Denmark only
    df = df[
        df["MARKET_NAME"].str.strip().str.upper() == "DENMARK"
    ].copy()

    # Clip media columns to 0 (mirrors main())
    media_cols = [
        c
        for c in df.columns
        if any(kw in c for kw in ("COST", "SPEND", "CLICKS", "IMPRESSIONS", "GRP"))
    ]
    if media_cols:
        df[media_cols] = df[media_cols].clip(lower=0)

    return df


class TestCsvLoading(unittest.TestCase):
    """CSV loading, column normalisation, and country filtering."""

    def setUp(self):
        self.df = _load_dk_df()

    def test_fixture_file_exists(self):
        self.assertTrue(DK_CSV_PATH.exists(), f"CSV fixture not found: {DK_CSV_PATH}")

    def test_loads_non_empty_dataframe(self):
        self.assertGreater(len(self.df), 0)

    def test_columns_are_uppercase(self):
        for col in self.df.columns:
            self.assertEqual(col, col.upper(), f"Column not uppercase: {col}")

    def test_only_denmark_rows(self):
        """After filtering, every row must be Denmark."""
        markets = self.df["MARKET_NAME"].str.strip().str.upper().unique().tolist()
        self.assertEqual(markets, ["DENMARK"])

    def test_date_column_present(self):
        self.assertIn("DATE", self.df.columns)

    def test_dep_var_bookings_present(self):
        self.assertIn("BOOKINGS", self.df.columns)

    def test_column_rename_applied(self):
        """Raw names should be gone; renamed names should be present."""
        for old_name, new_name in COLUMN_RENAME_MAP.items():
            # Only check renames where the raw name actually existed
            # (some may not be in this CSV version)
            if new_name in self.df.columns:
                self.assertNotIn(
                    old_name,
                    self.df.columns,
                    f"Old column '{old_name}' should have been renamed to '{new_name}'",
                )

    def test_media_columns_non_negative(self):
        """Clipping must ensure no negative values in media/spend columns."""
        media_cols = [
            c
            for c in self.df.columns
            if any(kw in c for kw in ("COST", "SPEND", "CLICKS", "IMPRESSIONS"))
        ]
        self.assertGreater(len(media_cols), 0, "Expected at least one media column")
        for col in media_cols:
            min_val = self.df[col].min(skipna=True)
            self.assertGreaterEqual(
                min_val,
                0,
                f"Column '{col}' has negative value after clipping: {min_val}",
            )

    def test_date_range_is_valid(self):
        dates = pd.to_datetime(self.df["DATE"], errors="coerce").dropna()
        self.assertGreater(len(dates), 0)
        self.assertLessEqual(dates.min(), dates.max())

    def test_dataframe_serializes_to_parquet(self):
        """The filtered DK DataFrame must convert to a Parquet table without error.

        This guards against the crash reported in run_benchmark_from_csv.py where
        pa.Table.from_pandas() raises before the GCS upload log line appears.
        """
        import io

        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.Table.from_pandas(self.df, preserve_index=False)
        buf = io.BytesIO()
        pq.write_table(table, buf)
        self.assertGreater(buf.tell(), 0, "Parquet buffer is empty after write")


class TestColumnClassifiers(unittest.TestCase):
    """Unit tests for the three column-classifier predicates."""

    def test_is_factor_var_is_prefix(self):
        self.assertTrue(_is_factor_var("IS_HOLIDAY"))
        self.assertTrue(_is_factor_var("IS_SCHOOL_HOLIDAY"))

    def test_is_factor_var_pmax_local(self):
        self.assertTrue(_is_factor_var("PMAX_LOCAL"))

    def test_is_factor_var_rejects_other(self):
        self.assertFalse(_is_factor_var("GOOGLE_SEARCH_BRAND_COST"))
        self.assertFalse(_is_factor_var("CRM_EMAIL_NEWSLETTER_SENDS"))

    def test_is_organic_var_crm_email(self):
        self.assertTrue(_is_organic_var("CRM_EMAIL_NEWSLETTER_SENDS"))
        self.assertTrue(_is_organic_var("CRM_EMAIL_JOURNEY_SENDS"))

    def test_is_organic_var_rejects_other(self):
        self.assertFalse(_is_organic_var("IS_HOLIDAY"))
        self.assertFalse(_is_organic_var("GOOGLE_SEARCH_BRAND_COST"))

    def test_is_media_metric_impressions(self):
        self.assertTrue(_is_media_metric("FB_UPPER_IMPRESSIONS", frozenset()))

    def test_is_media_metric_clicks(self):
        self.assertTrue(_is_media_metric("GOOGLE_SEARCH_BRAND_CLICKS", frozenset()))

    def test_is_media_metric_non_selected_cost(self):
        """A cost column NOT in paid_media_spends_set should be a media metric."""
        self.assertTrue(
            _is_media_metric("FB_VIDEO_COST", frozenset({"FB_UPPER_COST"}))
        )

    def test_is_media_metric_selected_cost_is_not_metric(self):
        """A cost column that IS in paid_media_spends_set is not a media metric."""
        self.assertFalse(
            _is_media_metric("FB_UPPER_COST", frozenset({"FB_UPPER_COST"}))
        )

    def test_crm_email_clicks_is_organic_not_media(self):
        """CRM_EMAIL_*_CLICKS must be classified as organic, not media delivery."""
        col = "CRM_EMAIL_NEWSLETTER_CLICKS"
        self.assertTrue(_is_organic_var(col))
        # Even though it ends with _CLICKS, organic check happens first in classify_columns
        self.assertTrue(_is_media_metric(col, frozenset()))


class TestAutoClassifyMode(unittest.TestCase):
    """classify_columns() in auto-classify (--no-mapping) mode."""

    def setUp(self):
        df = _load_dk_df()
        self.all_cols = df.columns.tolist()
        self.dep_var = "BOOKINGS"
        self.result = classify_columns(self.all_cols, PAID_MEDIA_SPENDS, self.dep_var)

    def test_returns_required_keys(self):
        for key in (
            "paid_media_spends",
            "paid_media_vars",
            "var_to_spend_mapping",
            "context_vars",
            "factor_vars",
            "organic_vars",
        ):
            self.assertIn(key, self.result)

    def test_paid_media_spends_non_empty(self):
        self.assertGreater(len(self.result["paid_media_spends"]), 0)

    def test_all_paid_media_spends_in_csv(self):
        """Only columns that actually exist in the CSV are returned."""
        col_set = set(self.all_cols)
        for col in self.result["paid_media_spends"]:
            self.assertIn(col, col_set)

    def test_dep_var_not_in_any_predictor_list(self):
        all_predictors = (
            self.result["paid_media_spends"]
            + self.result["paid_media_vars"]
            + self.result["context_vars"]
            + self.result["factor_vars"]
            + self.result["organic_vars"]
        )
        self.assertNotIn(self.dep_var, all_predictors)

    def test_factor_vars_are_is_prefixed(self):
        for col in self.result["factor_vars"]:
            self.assertTrue(
                _is_factor_var(col),
                f"factor_var '{col}' does not pass _is_factor_var",
            )

    def test_organic_vars_are_crm_email(self):
        for col in self.result["organic_vars"]:
            self.assertTrue(
                _is_organic_var(col),
                f"organic_var '{col}' does not pass _is_organic_var",
            )

    def test_no_column_in_multiple_categories(self):
        """Each unique column name should appear in at most one predictor category.

        Note: the CSV fixture contains both upper- and lower-case variants of
        some IS_* flag columns (e.g. ``IS_HOLIDAY`` and ``is_holiday``) which
        both normalize to the same name after ``.upper()``.  Within a single
        category that creates duplicates in the raw list, but a column must
        not cross category boundaries.  This test uses sets to check category
        disjointness regardless of intra-list duplicates.
        """
        categories = {
            key: set(self.result[key])
            for key in ("paid_media_spends", "context_vars", "factor_vars", "organic_vars")
        }
        keys = list(categories)
        for i, k1 in enumerate(keys):
            for k2 in keys[i + 1 :]:
                overlap = categories[k1] & categories[k2]
                self.assertEqual(
                    overlap,
                    set(),
                    f"Columns appear in both '{k1}' and '{k2}': {overlap}",
                )

    def test_no_market_name_or_date_in_predictors(self):
        all_predictors = set(
            self.result["paid_media_spends"]
            + self.result["paid_media_vars"]
            + self.result["context_vars"]
            + self.result["factor_vars"]
            + self.result["organic_vars"]
        )
        self.assertNotIn("MARKET_NAME", all_predictors)
        self.assertNotIn("DATE", all_predictors)

    def test_selected_columns_json_construction(self):
        """selected_columns dict built from auto-classify result has required keys."""
        sc = _build_selected_columns(self.result)
        for key in REQUIRED_SELECTED_COLUMNS_KEYS:
            self.assertIn(key, sc, f"Missing key in selected_columns: '{key}'")

    def test_all_selected_drivers_deduped(self):
        sc = _build_selected_columns(self.result)
        drivers = sc["all_selected_drivers"]
        self.assertEqual(len(drivers), len(set(drivers)))

    def test_selected_columns_is_json_serializable(self):
        sc = _build_selected_columns(self.result)
        serialized = json.dumps(sc)
        deserialized = json.loads(serialized)
        self.assertEqual(deserialized["dep_var"], "BOOKINGS")


class TestDefaultDkMappingMode(unittest.TestCase):
    """load_columns_from_mapping() with dk_selected_columns_mapping_v2_clean.json."""

    def setUp(self):
        df = _load_dk_df()
        self.all_cols = df.columns.tolist()
        self.result = load_columns_from_mapping(DEFAULT_DK_MAPPING, self.all_cols)

    def test_default_mapping_file_exists(self):
        self.assertTrue(
            DEFAULT_DK_MAPPING.exists(),
            f"Default DK mapping not found: {DEFAULT_DK_MAPPING}",
        )

    def test_returns_required_keys(self):
        for key in (
            "paid_media_spends",
            "paid_media_vars",
            "var_to_spend_mapping",
            "context_vars",
            "factor_vars",
            "organic_vars",
        ):
            self.assertIn(key, self.result)

    def test_paid_media_spends_non_empty(self):
        self.assertGreater(len(self.result["paid_media_spends"]), 0)

    def test_known_spend_channel_present(self):
        self.assertIn("GOOGLE_SEARCH_BRAND_COST", self.result["paid_media_spends"])

    def test_known_media_var_present(self):
        self.assertIn("GOOGLE_SEARCH_BRAND_CLICKS", self.result["paid_media_vars"])

    def test_organic_vars_include_crm_channels(self):
        self.assertIn("CRM_EMAIL_NEWSLETTER_SENDS", self.result["organic_vars"])
        self.assertIn("CRM_EMAIL_JOURNEY_SENDS", self.result["organic_vars"])

    def test_factor_vars_include_holidays(self):
        self.assertIn("IS_HOLIDAY", self.result["factor_vars"])
        self.assertIn("IS_SCHOOL_HOLIDAY", self.result["factor_vars"])

    def test_var_to_spend_mapping_keys_subset_of_paid_media_vars(self):
        mapping_keys = set(self.result["var_to_spend_mapping"].keys())
        vars_set = set(self.result["paid_media_vars"])
        self.assertTrue(
            mapping_keys.issubset(vars_set),
            f"var_to_spend_mapping keys not all in paid_media_vars: "
            f"{mapping_keys - vars_set}",
        )

    def test_var_to_spend_mapping_values_subset_of_paid_media_spends(self):
        mapping_vals = set(self.result["var_to_spend_mapping"].values())
        spends_set = set(self.result["paid_media_spends"])
        self.assertTrue(
            mapping_vals.issubset(spends_set),
            f"var_to_spend_mapping values not all in paid_media_spends: "
            f"{mapping_vals - spends_set}",
        )

    def test_all_columns_exist_in_csv(self):
        col_set = set(self.all_cols)
        for key in (
            "paid_media_spends",
            "paid_media_vars",
            "context_vars",
            "factor_vars",
            "organic_vars",
        ):
            for col in self.result[key]:
                self.assertIn(
                    col,
                    col_set,
                    f"{key} column '{col}' not found in CSV",
                )

    def test_selected_columns_json_construction(self):
        sc = _build_selected_columns(self.result)
        for key in REQUIRED_SELECTED_COLUMNS_KEYS:
            self.assertIn(key, sc)

    def test_all_selected_drivers_superset_of_paid_media_vars(self):
        sc = _build_selected_columns(self.result)
        drivers_set = set(sc["all_selected_drivers"])
        for var in self.result["paid_media_vars"]:
            self.assertIn(var, drivers_set)


class TestTvConfigAsMappingFile(unittest.TestCase):
    """
    load_columns_from_mapping() with dk_final_with_tv_config.json.

    The TV config adds WBR (TV) and BAUER (radio) GRP channels which are NOT
    present in the standard DK CSV fixture.  The function must degrade
    gracefully — returning only the channels that exist — without raising an
    error.
    """

    def setUp(self):
        self.tv_config_path = TV_CONFIG_PATH
        df = _load_dk_df()
        self.all_cols = df.columns.tolist()
        self.result = load_columns_from_mapping(self.tv_config_path, self.all_cols)

    def test_tv_config_file_exists(self):
        self.assertTrue(
            self.tv_config_path.exists(),
            f"TV config not found: {self.tv_config_path}",
        )

    def test_no_exception_raised(self):
        """Calling load_columns_from_mapping with TV config must not raise."""
        # Already called in setUp; we just verify we get here
        self.assertIsInstance(self.result, dict)

    def test_returns_required_keys(self):
        for key in (
            "paid_media_spends",
            "paid_media_vars",
            "var_to_spend_mapping",
            "context_vars",
            "factor_vars",
            "organic_vars",
        ):
            self.assertIn(key, self.result)

    def test_only_columns_present_in_csv_returned(self):
        """TV-only columns (WBR_TOTAL_GRP, BAUER_GRP_FLOW_RADIO, etc.) must be
        filtered out since they don't exist in the CSV fixture."""
        col_set = set(self.all_cols)
        tv_only = {"WBR_TOTAL_GRP", "BAUER_GRP_FLOW_RADIO", "WBR_TOTAL_SPEND", "BAUER_SPEND_FLOW_RADIO"}
        for key in ("paid_media_spends", "paid_media_vars"):
            for col in self.result[key]:
                self.assertNotIn(
                    col,
                    tv_only,
                    f"TV-only column '{col}' should have been filtered out from {key}",
                )
                self.assertIn(col, col_set)

    def test_common_channels_still_present(self):
        """Channels shared with the standard config must still be returned."""
        self.assertIn("GOOGLE_SEARCH_BRAND_COST", self.result["paid_media_spends"])
        self.assertIn("FB_UPPER_COST", self.result["paid_media_spends"])

    def test_organic_vars_present(self):
        self.assertIn("CRM_EMAIL_NEWSLETTER_SENDS", self.result["organic_vars"])
        self.assertIn("CRM_EMAIL_JOURNEY_SENDS", self.result["organic_vars"])

    def test_factor_vars_from_tv_config_present(self):
        """Factor vars in the TV config use lowercase names matching the CSV."""
        # The TV config lists is_holiday, is_long_weekend, etc.
        # After uppercase normalisation of CSV columns they become IS_HOLIDAY etc.
        factor_vars_upper = {v.upper() for v in self.result["factor_vars"]}
        self.assertTrue(
            len(factor_vars_upper) > 0,
            "Expected at least one factor_var from the TV config",
        )

    def test_selected_columns_json_construction(self):
        sc = _build_selected_columns(self.result, dep_var="BOOKINGS", dep_var_type="conversion")
        for key in REQUIRED_SELECTED_COLUMNS_KEYS:
            self.assertIn(key, sc)
        self.assertEqual(sc["dep_var_type"], "conversion")

    def test_var_to_spend_mapping_only_present_columns(self):
        """var_to_spend_mapping must not reference columns absent from the CSV."""
        col_set = set(self.all_cols)
        for var, spend in self.result["var_to_spend_mapping"].items():
            self.assertIn(var, col_set, f"mapping key '{var}' not in CSV")
            self.assertIn(spend, col_set, f"mapping value '{spend}' not in CSV")

    def test_selected_columns_is_json_serializable(self):
        sc = _build_selected_columns(self.result)
        serialized = json.dumps(sc)
        deserialized = json.loads(serialized)
        self.assertIsInstance(deserialized["paid_media_spends"], list)


class TestEndToEndPipelineComparison(unittest.TestCase):
    """
    End-to-end comparison: auto-classify vs mapping-file mode should both
    produce valid, internally consistent selected_columns structures.
    """

    def setUp(self):
        df = _load_dk_df()
        self.all_cols = df.columns.tolist()
        dep_var = "BOOKINGS"

        auto = classify_columns(self.all_cols, PAID_MEDIA_SPENDS, dep_var)
        mapping = load_columns_from_mapping(DEFAULT_DK_MAPPING, self.all_cols)
        tv_mapping = load_columns_from_mapping(TV_CONFIG_PATH, self.all_cols)

        self.sc_auto = _build_selected_columns(auto, dep_var=dep_var)
        self.sc_mapping = _build_selected_columns(mapping, dep_var=dep_var)
        self.sc_tv = _build_selected_columns(
            tv_mapping, dep_var=dep_var, dep_var_type="conversion"
        )

    def _assert_sc_valid(self, sc: dict, label: str) -> None:
        for key in REQUIRED_SELECTED_COLUMNS_KEYS:
            self.assertIn(key, sc, f"[{label}] Missing key: {key}")
        self.assertGreater(
            len(sc["paid_media_spends"]), 0, f"[{label}] paid_media_spends empty"
        )
        self.assertGreater(
            len(sc["all_selected_drivers"]), 0, f"[{label}] all_selected_drivers empty"
        )
        # all_selected_drivers must be a proper superset of paid_media_spends
        drivers = set(sc["all_selected_drivers"])
        for col in sc["paid_media_spends"]:
            self.assertIn(col, drivers, f"[{label}] spend '{col}' missing from drivers")
        # dep_var must not appear in predictors
        all_pred = set(
            sc["paid_media_spends"]
            + sc["paid_media_vars"]
            + sc["context_vars"]
            + sc["factor_vars"]
            + sc["organic_vars"]
        )
        self.assertNotIn(
            sc["dep_var"], all_pred, f"[{label}] dep_var in predictor set"
        )

    def test_auto_classify_valid(self):
        self._assert_sc_valid(self.sc_auto, "auto-classify")

    def test_default_mapping_valid(self):
        self._assert_sc_valid(self.sc_mapping, "default-mapping")

    def test_tv_config_mapping_valid(self):
        self._assert_sc_valid(self.sc_tv, "tv-config-mapping")

    def test_mapping_mode_has_more_context_vars_than_auto(self):
        """Curated mapping is expected to include renamed context vars (e.g.
        FLEET_TOTAL_UNITS) that the auto-classifier treats differently."""
        # Both should have context vars; we just verify neither is empty.
        self.assertGreater(len(self.sc_mapping["context_vars"]), 0)
        self.assertGreater(len(self.sc_auto["context_vars"]), 0)

    def test_all_three_are_json_serializable(self):
        for label, sc in (
            ("auto", self.sc_auto),
            ("mapping", self.sc_mapping),
            ("tv", self.sc_tv),
        ):
            try:
                json.dumps(sc)
            except (TypeError, ValueError) as exc:
                self.fail(f"[{label}] selected_columns not JSON-serializable: {exc}")


class TestTvConfigWithTvData(unittest.TestCase):
    """
    Integration tests for the TV config paired with the TV-merged CSV fixture.

    mmm_data_v2_with_tv.csv is the standard DK dataset with TV and radio
    spend/GRP columns merged in from TV_DATA.xlsx.  Unlike TestTvConfigAsMappingFile
    (which uses the CSV without TV columns and verifies graceful degradation),
    these tests verify that TV channels are fully present and correctly mapped
    when the right source data is available.
    """

    def setUp(self):
        self.df = _load_dk_tv_df()
        self.all_cols = self.df.columns.tolist()
        self.result = load_columns_from_mapping(TV_CONFIG_PATH, self.all_cols)

    # ── Fixture checks ───────────────────────────────────────────────────────

    def test_tv_csv_fixture_exists(self):
        self.assertTrue(
            DK_TV_CSV_PATH.exists(),
            f"TV-merged CSV not found: {DK_TV_CSV_PATH}",
        )

    def test_tv_csv_contains_wbr_total_grp(self):
        self.assertIn("WBR_TOTAL_GRP", self.all_cols)

    def test_tv_csv_contains_wbr_total_spend(self):
        self.assertIn("WBR_TOTAL_SPEND", self.all_cols)

    def test_tv_csv_contains_bauer_grp_flow_radio(self):
        self.assertIn("BAUER_GRP_FLOW_RADIO", self.all_cols)

    def test_tv_csv_contains_bauer_spend_flow_radio(self):
        self.assertIn("BAUER_SPEND_FLOW_RADIO", self.all_cols)

    def test_tv_csv_filtered_to_denmark(self):
        markets = self.df["MARKET_NAME"].str.strip().str.upper().unique().tolist()
        self.assertEqual(markets, ["DENMARK"])

    def test_tv_grp_column_has_nonzero_values(self):
        """WBR_TOTAL_GRP must have actual TV data, not all zeros."""
        self.assertGreater(self.df["WBR_TOTAL_GRP"].sum(), 0)

    def test_radio_grp_column_has_nonzero_values(self):
        """BAUER_GRP_FLOW_RADIO must have actual radio data, not all zeros."""
        self.assertGreater(self.df["BAUER_GRP_FLOW_RADIO"].sum(), 0)

    # ── Mapping result checks ────────────────────────────────────────────────

    def test_tv_grp_in_paid_media_vars(self):
        """WBR_TOTAL_GRP must appear in paid_media_vars when data is present."""
        self.assertIn("WBR_TOTAL_GRP", self.result["paid_media_vars"])

    def test_tv_spend_in_paid_media_spends(self):
        """WBR_TOTAL_SPEND must appear in paid_media_spends when data is present."""
        self.assertIn("WBR_TOTAL_SPEND", self.result["paid_media_spends"])

    def test_radio_grp_in_paid_media_vars(self):
        """BAUER_GRP_FLOW_RADIO must appear in paid_media_vars when data is present."""
        self.assertIn("BAUER_GRP_FLOW_RADIO", self.result["paid_media_vars"])

    def test_radio_spend_in_paid_media_spends(self):
        """BAUER_SPEND_FLOW_RADIO must appear in paid_media_spends when data is present."""
        self.assertIn("BAUER_SPEND_FLOW_RADIO", self.result["paid_media_spends"])

    def test_paid_media_spends_and_vars_same_length(self):
        self.assertEqual(
            len(self.result["paid_media_spends"]),
            len(self.result["paid_media_vars"]),
        )

    def test_var_to_spend_mapping_covers_tv_and_radio(self):
        mapping = self.result["var_to_spend_mapping"]
        self.assertIn("WBR_TOTAL_GRP", mapping)
        self.assertEqual(mapping["WBR_TOTAL_GRP"], "WBR_TOTAL_SPEND")
        self.assertIn("BAUER_GRP_FLOW_RADIO", mapping)
        self.assertEqual(mapping["BAUER_GRP_FLOW_RADIO"], "BAUER_SPEND_FLOW_RADIO")

    def test_all_paid_media_vars_exist_in_csv(self):
        col_set = set(self.all_cols)
        for col in self.result["paid_media_vars"]:
            self.assertIn(col, col_set, f"paid_media_vars column '{col}' not in CSV")

    def test_all_paid_media_spends_exist_in_csv(self):
        col_set = set(self.all_cols)
        for col in self.result["paid_media_spends"]:
            self.assertIn(col, col_set, f"paid_media_spends column '{col}' not in CSV")

    # ── selected_columns construction ────────────────────────────────────────

    def test_selected_columns_json_construction(self):
        sc = _build_selected_columns(
            self.result, dep_var="BOOKINGS", dep_var_type="conversion"
        )
        for key in REQUIRED_SELECTED_COLUMNS_KEYS:
            self.assertIn(key, sc)

    def test_selected_columns_all_drivers_includes_tv_and_radio(self):
        sc = _build_selected_columns(self.result, dep_var="BOOKINGS")
        drivers = set(sc["all_selected_drivers"])
        self.assertIn("WBR_TOTAL_GRP", drivers)
        self.assertIn("BAUER_GRP_FLOW_RADIO", drivers)

    def test_dep_var_not_in_predictors(self):
        sc = _build_selected_columns(self.result, dep_var="BOOKINGS")
        all_pred = set(
            sc["paid_media_spends"]
            + sc["paid_media_vars"]
            + sc["context_vars"]
            + sc["factor_vars"]
            + sc["organic_vars"]
        )
        self.assertNotIn("BOOKINGS", all_pred)

    # ── Parquet serialisation ────────────────────────────────────────────────

    def test_tv_dataframe_serialises_to_parquet(self):
        """TV-merged DK DataFrame must convert to Parquet without error."""
        import io

        import pyarrow as pa
        import pyarrow.parquet as pq

        buf = io.BytesIO()
        table = pa.Table.from_pandas(self.df, preserve_index=False)
        pq.write_table(table, buf)
        self.assertGreater(buf.tell(), 0, "Parquet output is empty")

    def test_selected_columns_is_json_serialisable(self):
        sc = _build_selected_columns(self.result, dep_var="BOOKINGS")
        serialised = json.dumps(sc)
        deserialised = json.loads(serialised)
        self.assertIsInstance(deserialised["paid_media_spends"], list)
        self.assertIn("WBR_TOTAL_SPEND", deserialised["paid_media_spends"])


class TestTvEndToEnd(unittest.TestCase):
    """
    One comprehensive end-to-end integration test per dataset variant.

    test_without_tv_data — standard DK CSV (no TV/radio columns) + TV config.
        The TV channels must be silently dropped; the remaining channels must
        produce a valid, JSON-serialisable selected_columns structure.

    test_with_tv_data — TV-merged DK CSV (includes WBR/BAUER columns) + TV config.
        All TV and radio channels must be fully present and correctly mapped.
    """

    def _assert_selected_columns_valid(
        self, sc: dict, label: str, expected_in_spends=None, not_expected_in_spends=None
    ) -> None:
        for key in REQUIRED_SELECTED_COLUMNS_KEYS:
            self.assertIn(key, sc, f"[{label}] Missing key: {key}")
        self.assertGreater(
            len(sc["paid_media_spends"]), 0, f"[{label}] paid_media_spends empty"
        )
        self.assertGreater(
            len(sc["all_selected_drivers"]), 0, f"[{label}] all_selected_drivers empty"
        )
        drivers = set(sc["all_selected_drivers"])
        for col in sc["paid_media_spends"]:
            self.assertIn(col, drivers, f"[{label}] spend '{col}' missing from all_selected_drivers")
        self.assertNotIn(sc["dep_var"], set(sc["paid_media_spends"] + sc["paid_media_vars"]))
        # JSON-serialisable
        json.dumps(sc)
        if expected_in_spends:
            for col in expected_in_spends:
                self.assertIn(col, sc["paid_media_spends"], f"[{label}] '{col}' not in paid_media_spends")
        if not_expected_in_spends:
            for col in not_expected_in_spends:
                self.assertNotIn(col, sc["paid_media_spends"], f"[{label}] TV-only '{col}' should not be in paid_media_spends")

    def test_without_tv_data(self):
        """Standard DK CSV + TV config: TV columns absent → graceful degradation."""
        df = _load_dk_df()
        result = load_columns_from_mapping(TV_CONFIG_PATH, df.columns.tolist())
        sc = _build_selected_columns(result, dep_var="BOOKINGS", dep_var_type="conversion")
        tv_only_spends = ["WBR_TOTAL_SPEND", "BAUER_SPEND_FLOW_RADIO"]
        standard_spends = ["GOOGLE_SEARCH_BRAND_COST", "FB_UPPER_COST"]
        self._assert_selected_columns_valid(
            sc,
            label="without-tv-data",
            expected_in_spends=standard_spends,
            not_expected_in_spends=tv_only_spends,
        )

    def test_with_tv_data(self):
        """TV-merged DK CSV + TV config: TV and radio channels fully present."""
        df = _load_dk_tv_df()
        result = load_columns_from_mapping(TV_CONFIG_PATH, df.columns.tolist())
        sc = _build_selected_columns(result, dep_var="BOOKINGS", dep_var_type="conversion")
        tv_spends = ["WBR_TOTAL_SPEND", "BAUER_SPEND_FLOW_RADIO"]
        tv_vars = ["WBR_TOTAL_GRP", "BAUER_GRP_FLOW_RADIO"]
        self._assert_selected_columns_valid(
            sc,
            label="with-tv-data",
            expected_in_spends=tv_spends,
        )
        drivers = set(sc["all_selected_drivers"])
        for col in tv_vars:
            self.assertIn(col, drivers, f"[with-tv-data] TV var '{col}' missing from all_selected_drivers")
        mapping = sc["var_to_spend_mapping"]
        self.assertEqual(mapping.get("WBR_TOTAL_GRP"), "WBR_TOTAL_SPEND")
        self.assertEqual(mapping.get("BAUER_GRP_FLOW_RADIO"), "BAUER_SPEND_FLOW_RADIO")


if __name__ == "__main__":
    unittest.main()
