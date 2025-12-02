"""
Prepare Training Data Page

This page guides users through preparing training data in 4 steps:
1. Select Data
2. Ensure good data quality
3. Prepare paid media spends & media response
4. Select strongest drivers and reduce noise
"""

import json
import math
import os
import tempfile
import warnings
from datetime import datetime, timezone
from typing import List, Union

import numpy as np
import pandas as pd
import streamlit as st
from app_shared import (
    GCS_BUCKET,
    build_meta_views,
    build_plat_map_df,
    data_blob,
    data_latest_blob,
    download_json_from_gcs_cached,
    download_parquet_from_gcs_cached,
    filter_range,
    freq_to_rule,
    list_data_versions,
    list_meta_versions,
    parse_date,
    period_label,
    pretty,
    previous_window,
    require_login_and_domain,
    resample_numeric,
    resolve_meta_blob_from_selection,
    total_with_prev,
    upload_to_gcs,
    validate_against_metadata,
)
from scipy import stats
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score
from statsmodels.stats.outliers_influence import variance_inflation_factor


def _safe_float(value: Union[float, np.ndarray, None]) -> float:
    """Safely convert a value to float, handling numpy arrays and NaN values."""
    if value is None:
        return np.nan
    # Handle numpy arrays - take scalar value if single element, else NaN
    if isinstance(value, np.ndarray):
        if value.size == 1:
            value = value.item()
        else:
            return np.nan
    # Now check if it's a valid number
    try:
        f = float(value)
        # Use math.isnan and math.isinf for Python floats (avoids numpy type issues)
        if math.isnan(f) or math.isinf(f):
            return np.nan
        return f
    except (TypeError, ValueError):
        return np.nan


# Authentication
require_login_and_domain()
ensure_session_defaults()

st.title("Prepare Training Data")

# Constants
TRAINING_DATA_PATH_TEMPLATE = (
    "training_data/{country}/{timestamp}/selected_columns.json"
)

# Session state defaults
st.session_state.setdefault("country", "de")
st.session_state.setdefault("picked_data_ts", "Latest")
st.session_state.setdefault("picked_meta_ts", "Latest")
st.session_state.setdefault("selected_columns_for_training", [])
st.session_state.setdefault("selected_paid_spends", [])
st.session_state.setdefault("selected_goal", None)
# Track paid spend selections in session state to persist across reruns
st.session_state.setdefault("paid_spend_selections", {})
# Track paid vars selections to sync with paid spends
st.session_state.setdefault("paid_var_selections", {})

# =============================
# Step 1: Select Data
# =============================
with st.expander("Step 1) Select Data", expanded=False):
    st.markdown("### Select country and data versions to analyze")

    # Check if we have preselected values from Map Your Data
    if "country" in st.session_state and st.session_state.get("country"):
        st.info(
            f"Using country from Map Your Data: **{st.session_state['country'].upper()}**"
        )

    c1, c2, c3, c4 = st.columns([1.2, 1, 1, 0.6])

    country = (
        c1.text_input("Country", value=st.session_state["country"])
        .strip()
        .lower()
    )
    if country:
        st.session_state["country"] = country

    refresh_clicked = c4.button("↻ Refresh Lists", key="refresh_step1")
    refresh_key = (
        str(int(datetime.now(timezone.utc).timestamp() * 1e9))
        if refresh_clicked
        else ""
    )

    data_versions = (
        list_data_versions(GCS_BUCKET, country, refresh_key)
        if country
        else ["Latest"]
    )
    meta_versions = (
        list_meta_versions(GCS_BUCKET, country, refresh_key)
        if country
        else ["Latest"]
    )

    data_ts = c2.selectbox(
        "Data version", options=data_versions, index=0, key="picked_data_ts"
    )
    meta_ts = c3.selectbox(
        "Metadata version", options=meta_versions, index=0, key="picked_meta_ts"
    )

    load_clicked = st.button("Select & Load", type="primary", key="load_step1")

    if load_clicked:
        try:
            # Resolve DATA path
            db = (
                data_latest_blob(country)
                if data_ts == "Latest"
                else data_blob(country, str(data_ts))
            )

            # Resolve META path
            mb = resolve_meta_blob_from_selection(
                GCS_BUCKET, country, str(meta_ts)
            )

            # Download
            df = download_parquet_from_gcs_cached(GCS_BUCKET, db)
            meta = download_json_from_gcs_cached(GCS_BUCKET, mb)

            # Parse dates using metadata
            df, date_col = parse_date(df, meta)

            # Persist in session
            st.session_state["df"] = df
            st.session_state["meta"] = meta
            st.session_state["date_col"] = date_col
            st.session_state["channels_map"] = meta.get("channels", {}) or {}

            # Validate & notify
            report = validate_against_metadata(df, meta)
            st.success(
                f"Loaded {len(df):,} rows from gs://{GCS_BUCKET}/{db} and metadata gs://{GCS_BUCKET}/{mb}"
            )

            c_extra, _ = st.columns([1, 1])
            with c_extra:
                st.markdown("**Columns in data but not in metadata**")
                st.write(report["extra_in_df"] or "— none —")

            if not report["type_mismatches"].empty:
                st.warning("Declared vs observed type mismatches:")
                st.dataframe(
                    report["type_mismatches"],
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.caption("No type mismatches detected (coarse check).")
        except Exception as e:
            st.error(f"Load failed: {e}")

# Get data from session state
df = st.session_state.get("df", pd.DataFrame())
meta = st.session_state.get("meta", {}) or {}
DATE_COL = st.session_state.get("date_col", "DATE")
CHANNELS_MAP = st.session_state.get("channels_map", {}) or {}

if df.empty or not meta:
    st.info("Please load data in Step 1 to continue.")
    st.stop()

# Build metadata views
(
    display_map,
    nice,
    goal_cols,
    mapping,
    m,
    ALL_COLS_UP,
    IMPR_COLS,
    CLICK_COLS,
    SESSION_COLS,
    INSTALL_COLS,
) = build_meta_views(meta, df)


def nice_title(col: str) -> str:
    """
    Coalesce-style label resolver:
      1) try metadata-based nice(col)
      2) fallback to pretty(col) when missing/identical
    """
    raw = str(col)
    try:
        nice_val = nice(col)
        if (
            isinstance(nice_val, str)
            and nice_val.strip()
            and nice_val.strip().upper() != raw.strip().upper()
        ):
            return nice_val.strip()
        return pretty(raw)
    except Exception:
        return pretty(raw)


# Sidebar for timeframe and aggregation selection (no Goal selector for this page)
# Countries
if "COUNTRY" in df.columns:
    country_list = sorted(df["COUNTRY"].dropna().astype(str).unique().tolist())
    default_countries = country_list or []
    sel_countries = st.sidebar.multiselect(
        "Country", country_list, default=default_countries
    )
else:
    sel_countries = []
    st.sidebar.caption("Dataset has no COUNTRY column — showing all rows.")

# Timeframe - default to ALL for this page
tf_label_map = {
    "ALL": "all",
    "LAST 6 MONTHS": "6m",
    "LAST 12 MONTHS": "12m",
    "CURRENT YEAR": "cy",
    "LAST YEAR": "ly",
    "LAST 2 YEARS": "2y",
}
TIMEFRAME_LABEL = st.sidebar.selectbox(
    "Timeframe", list(tf_label_map.keys()), index=0
)
RANGE = tf_label_map[TIMEFRAME_LABEL]

# Aggregation
agg_map = {
    "Daily": "D",
    "Weekly": "W",
    "Monthly": "M",
    "Quarterly": "Q",
    "Yearly": "YE",
}
agg_label = st.sidebar.selectbox("Aggregation", list(agg_map.keys()), index=0)
FREQ = agg_map[agg_label]

# Country filter
if sel_countries and "COUNTRY" in df.columns:
    df = df[df["COUNTRY"].astype(str).isin(sel_countries)].copy()

# Target, spend, platforms
target = goal_cols[0] if goal_cols else None

paid_spend_cols = [
    c for c in (mapping.get("paid_media_spends", []) or []) if c in df.columns
]
paid_var_cols = [
    c for c in (mapping.get("paid_media_vars", []) or []) if c in df.columns
]
organic_cols = [
    c for c in (mapping.get("organic_vars", []) or []) if c in df.columns
]
context_cols = [
    c for c in (mapping.get("context_vars", []) or []) if c in df.columns
]
factor_cols = [
    c for c in (mapping.get("factor_vars", []) or []) if c in df.columns
]

RULE = freq_to_rule(FREQ)

plat_map_df, platforms, PLATFORM_COLORS = build_plat_map_df(
    present_spend=paid_spend_cols,
    df=df,
    meta=meta,
    m=m,
    COL="column_name",
    PLAT="platform",
    CHANNELS_MAP=CHANNELS_MAP,
)

# Timeframe filter
df_r = filter_range(df.copy(), DATE_COL, RANGE)
df_prev = previous_window(df, df_r, DATE_COL, RANGE)


# Apply aggregation/resampling if not daily
def _resample_df(data: pd.DataFrame, date_col: str, rule: str) -> pd.DataFrame:
    """Resample dataframe by the given frequency rule."""
    if data.empty or rule == "D":
        return data

    # Convert date column to datetime
    data = data.copy()
    data[date_col] = pd.to_datetime(data[date_col], errors="coerce")
    data = data.dropna(subset=[date_col])
    if data.empty:
        return data

    # Separate numeric and non-numeric columns
    num_cols = data.select_dtypes(include=[np.number]).columns.tolist()
    non_num_cols = [
        c for c in data.columns if c not in num_cols and c != date_col
    ]

    # Handle edge case where there are no columns to resample
    if not num_cols and not non_num_cols:
        return data

    # First, get the count of rows per period to know which periods have data
    # Use any available column to count rows per period
    count_col = num_cols[0] if num_cols else non_num_cols[0]
    period_counts = (
        data.set_index(date_col)[[count_col]]
        .resample(rule)
        .count()
        .reset_index()
    )
    period_counts.columns = [date_col, "_row_count"]
    periods_with_data = period_counts[period_counts["_row_count"] > 0][
        date_col
    ].tolist()

    # Resample numeric columns using sum (appropriate for costs/counts)
    # Note: sum() returns 0 for empty periods, but we filter those out below
    if num_cols:
        res = (
            data.set_index(date_col)[num_cols]
            .resample(rule)
            .sum()
            .reset_index()
        )
    else:
        res = (
            data[[date_col]]
            .set_index(date_col)
            .resample(rule)
            .size()
            .reset_index(name="_count")
        )
        res = res.drop(columns=["_count"])

    # For non-numeric columns, take the first value in each period
    for col in non_num_cols:
        first_vals = (
            data.set_index(date_col)[[col]].resample(rule).first().reset_index()
        )
        res = res.merge(first_vals, on=date_col, how="left")

    # Only keep periods that actually had data in the original dataset
    res = res[res[date_col].isin(periods_with_data)].reset_index(drop=True)

    return res


# Apply resampling based on selected aggregation
df_r = _resample_df(df_r, DATE_COL, RULE)


# =============================
# Step 2: Ensure good data quality
# =============================
def _num_stats(s: pd.Series) -> dict:
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


def _cat_stats(s: pd.Series) -> dict:
    n = len(s)
    nn = int(s.notna().sum())
    na = n - nn
    s2 = s.dropna()
    return dict(
        non_null=nn,
        nulls=na,
        nulls_pct=(na / n * 100) if n else np.nan,
        zeros=np.nan,
        zeros_pct=np.nan,
        distinct=int(s2.nunique(dropna=True)) if nn else 0,
        min=np.nan,
        p10=np.nan,
        median=np.nan,
        mean=np.nan,
        p90=np.nan,
        max=np.nan,
        std=np.nan,
    )


def _distribution_values(
    s: pd.Series, *, numeric_bins: int = 10, cat_topk: int = 5
) -> list[float]:
    try:
        if pd.api.types.is_numeric_dtype(s):
            q = pd.to_numeric(s, errors="coerce").dropna()
            if q.empty:
                return []
            hist, _ = np.histogram(q, bins=numeric_bins)
            total = hist.sum()
            return (hist / total).tolist() if total else []
        if pd.api.types.is_datetime64_any_dtype(s):
            q = pd.to_datetime(s, errors="coerce").dropna()
            if q.empty:
                return []
            vc = q.dt.to_period("M").value_counts().sort_index()
            total = vc.sum()
            return (vc / total).tolist() if total else []
        q = s.dropna().astype("object")
        if q.empty:
            return []
        vc = q.value_counts().head(cat_topk)
        total = vc.sum()
        return (vc / total).tolist() if total else []
    except Exception:
        return []


def _var_platform(col: str, platforms: list[str]) -> str | None:
    cu = str(col).upper()
    for p in platforms:
        if p.upper() in cu:
            return p
    return None


def _active_spend_platforms(
    df_window: pd.DataFrame, plat_map_df: pd.DataFrame
) -> set[str]:
    if df_window.empty or plat_map_df.empty:
        return set()
    vm = plat_map_df.copy()
    vm = vm.dropna(subset=["col", "platform"])
    vm = vm[vm["col"].isin(df_window.columns)]
    if vm.empty:
        return set()

    # Create column->platform mapping
    col_to_plat = dict(zip(vm["col"], vm["platform"]))

    # Melt and add platform column
    melted = df_window[vm["col"].tolist()].melt(value_name="spend")
    melted["platform"] = melted["variable"].map(col_to_plat)

    sums = (
        melted.dropna(subset=["spend"])
        .groupby("platform")["spend"]
        .sum(min_count=1)
    )
    return set(sums[sums.fillna(0) > 0].index.astype(str))


def _cv(mean_val: float, std_val: float) -> float:
    if pd.isna(mean_val) or pd.isna(std_val):
        return np.nan
    if abs(mean_val) < 1e-12:
        return np.inf
    return float(abs(std_val) / abs(mean_val))


def _protect_columns_set(date_col: str, goal_cols: list[str]) -> set[str]:
    prot = {str(date_col), "COUNTRY"}
    prot |= set(goal_cols or [])
    return set(p.upper() for p in prot)


def _sync_paid_vars_with_spends(
    use_overrides: dict[str, bool],
    paid_spend: list[str],
    paid_vars: list[str],
    paid_media_mapping: dict[str, list[str]],
    user_selections: dict[str, bool],
) -> None:
    """
    Sync Paid Vars selections based on Paid Spend selections.

    When a Paid Spend column is selected/deselected, update the corresponding
    Paid Vars columns to match.

    Args:
        use_overrides: Dict of column -> selection state (modified in place)
        paid_spend: List of paid spend column names
        paid_vars: List of paid vars column names
        paid_media_mapping: Dict mapping spend columns to their var columns
        user_selections: Session state dict for user selections (modified)
    """
    for spend_col, is_selected in list(use_overrides.items()):
        if spend_col in paid_spend:
            # This is a Paid Spend column - sync corresponding Paid Vars
            corresponding_vars = paid_media_mapping.get(spend_col, [])
            for var_col in corresponding_vars:
                if var_col in paid_vars:
                    use_overrides[var_col] = is_selected
                    user_selections[var_col] = is_selected


with st.expander("Step 2) Ensure good data quality", expanded=False):
    st.subheader(f"Data Quality")

    prof_df = df_r.copy()
    if prof_df.empty:
        st.info("No data in the selected timeframe to profile.")
        st.stop()

    # Build metadata categories
    paid_spend = [
        c
        for c in (mapping.get("paid_media_spends", []) or [])
        if c in prof_df.columns
    ]
    paid_vars = [
        c
        for c in (mapping.get("paid_media_vars", []) or [])
        if c in prof_df.columns
    ]
    organic_vars = [
        c
        for c in (mapping.get("organic_vars", []) or [])
        if c in prof_df.columns
    ]
    context_vars = [
        c
        for c in (mapping.get("context_vars", []) or [])
        if c in prof_df.columns
    ]
    factor_vars = [
        c
        for c in (mapping.get("factor_vars", []) or [])
        if c in prof_df.columns
    ]

    # Get all columns mapped in metadata
    data_types_map = meta.get("data_types", {}) or {}
    channels_map = meta.get("channels", {}) or {}

    # Collect all columns already in other categories to exclude from "Other"
    all_categorized_cols = set(
        paid_spend + paid_vars + organic_vars + context_vars + factor_vars
    )

    # Other columns are those in data_types but NOT in channels
    # AND not already in any of the category lists
    # Filter to only those present in the dataframe for display
    other_cols = [
        c
        for c in data_types_map.keys()
        if c not in channels_map
        and c in prof_df.columns
        and c not in all_categorized_cols
    ]

    categories = [
        ("Paid Spend", paid_spend),
        ("Paid Vars", paid_vars),
        ("Organic", organic_vars),
        ("Context", context_vars),
        ("Factor", factor_vars),
        ("Other", other_cols),
    ]

    # Build profile table
    rows = []
    for col in prof_df.columns:
        s = prof_df[col]
        col_type = str(s.dtype)

        if pd.api.types.is_numeric_dtype(s):
            col_stats = _num_stats(s)
        elif pd.api.types.is_datetime64_any_dtype(s):
            col_stats = _cat_stats(s)
            ss = pd.to_datetime(s, errors="coerce").dropna()
            col_stats["min"] = ss.min().timestamp() if not ss.empty else np.nan
            col_stats["max"] = ss.max().timestamp() if not ss.empty else np.nan
            col_type = "datetime64"
        else:
            col_stats = _cat_stats(s)

        dist_vals = _distribution_values(s)

        rows.append(
            dict(
                Use=True, Column=col, Type=col_type, Dist=dist_vals, **col_stats
            )
        )
    prof_all = pd.DataFrame(rows)

    # Automated Cleaning section (expanded by default)
    with st.expander("Automated Cleaning", expanded=True):
        # Preselect first 3 checkboxes
        drop_all_null = st.checkbox("Drop all-null columns", value=True)
        drop_all_zero = st.checkbox(
            "Drop all-zero (numeric) columns", value=True
        )
        drop_constant = st.checkbox("Drop constant (distinct == 1)", value=True)
        drop_low_var = st.checkbox(
            "Drop low variance (CV < threshold)", value=False
        )
        cv_thr = st.slider(
            "Low-variance threshold (CV %)", 0.1, 100.0, 3.0, 0.1
        )
        c1, c2 = st.columns([1, 1])
        apply_clean = c1.button("Apply cleaning", key="apply_clean_step2")
        reset_clean = c2.button("Reset cleaning", key="reset_clean_step2")

    # Session state for cleaning persistence
    st.session_state.setdefault("dq_dropped_cols", set())
    st.session_state.setdefault("dq_clean_note", "")
    st.session_state.setdefault("dq_last_dropped", [])
    # Session state for user column selections (persists manual checkbox changes)
    st.session_state.setdefault("dq_user_selections", {})

    if reset_clean:
        st.session_state["dq_dropped_cols"] = set()
        st.session_state["dq_clean_note"] = ""
        st.session_state["dq_last_dropped"] = []
        st.session_state["dq_user_selections"] = {}
        st.rerun()

    # Apply cleaning when requested
    if apply_clean and (
        drop_all_null or drop_all_zero or drop_constant or drop_low_var
    ):
        to_drop = set()
        by_col = prof_all.set_index("Column")

        if drop_all_null:
            mask_all_null = by_col["non_null"].fillna(0).eq(0)
            to_drop |= set(by_col[mask_all_null].index)

        if drop_all_zero:
            nn = by_col["non_null"].fillna(0)
            zz = by_col["zeros"].fillna(-1)
            mask_all_zero = (nn > 0) & (zz == nn)
            to_drop |= set(by_col[mask_all_zero].index)

        if drop_constant:
            mask_const = by_col["distinct"].fillna(0).eq(1)
            to_drop |= set(by_col[mask_const].index)

        if drop_low_var:
            means = by_col["mean"]
            stds = by_col["std"]
            cv_series = pd.Series(
                [_cv(means.get(i), stds.get(i)) for i in by_col.index],
                index=by_col.index,
            )
            mask_low_cv = (cv_series * 100.0) < cv_thr
            to_drop |= set(cv_series[mask_low_cv.fillna(False)].index)

        # Never drop protected
        protected = _protect_columns_set(DATE_COL, goal_cols)
        to_drop = {c for c in to_drop if str(c).upper() not in protected}

        # Guard: keep at least one paid var per active spend platform
        active_plats = _active_spend_platforms(prof_df, plat_map_df)
        var_map = {v: _var_platform(v, platforms) for v in paid_vars}
        warnings_list = []
        for p in active_plats:
            vars_for_p = [v for v, vp in var_map.items() if vp == p]
            if not vars_for_p:
                continue
            dropping_all = all(v in to_drop for v in vars_for_p)
            if dropping_all:
                by_p = by_col.loc[[v for v in vars_for_p if v in by_col.index]]
                std_values = by_p["std"].astype(float).dropna()
                if not std_values.empty:
                    keep_one = std_values.idxmax()
                    to_drop.discard(keep_one)
                    warnings_list.append(
                        f"{p}: kept '{keep_one}' to retain at least one var for active spend."
                    )
                # If all stds are NaN, just keep the first one
                elif not by_p.empty:
                    keep_one = by_p.index[0]
                    to_drop.discard(keep_one)
                    warnings_list.append(
                        f"{p}: kept '{keep_one}' (first available) to retain at least one var for active spend."
                    )

        st.session_state["dq_dropped_cols"] |= set(to_drop)
        st.session_state["dq_last_dropped"] = sorted(to_drop)
        note = f"Dropped {len(to_drop)} column(s)."
        if warnings_list:
            note += " Guards applied — " + "; ".join(warnings_list)
        st.session_state["dq_clean_note"] = note

        # Mark dropped columns as Use=False in user selections
        user_sel = st.session_state.get("dq_user_selections", {})
        user_sel.update({col: False for col in to_drop})
        st.session_state["dq_user_selections"] = user_sel

        # Clear data editor widget states so they refresh with correct values
        # This is needed because Streamlit caches widget state by key
        for key in list(st.session_state.keys()):
            if key.startswith("dq_editor_"):
                del st.session_state[key]

        st.rerun()

    if st.session_state["dq_clean_note"]:
        st.info(st.session_state["dq_clean_note"])
        if st.session_state["dq_last_dropped"]:
            with st.expander("Dropped columns (last Apply)"):
                st.write(", ".join(st.session_state["dq_last_dropped"]))

    # Apply dropped flags to 'Use' default
    dropped = st.session_state["dq_dropped_cols"]
    user_selections = st.session_state["dq_user_selections"]

    # Initialize Use column: start with True for non-dropped
    prof_all["Use"] = ~prof_all["Column"].isin(dropped)

    # Apply any saved user selections (persisted from previous interactions)
    # but only for columns that are not in the dropped set
    if user_selections:
        # Filter out dropped columns from user selections to apply
        valid_selections = {
            col: val
            for col, val in user_selections.items()
            if col not in dropped
        }
        if valid_selections:
            mask = prof_all["Column"].isin(valid_selections.keys())
            prof_all.loc[mask, "Use"] = prof_all.loc[mask, "Column"].map(
                valid_selections
            )

    # Render tables
    use_overrides: dict[str, bool] = {}

    def _fmt_num(val) -> str:
        return "–" if pd.isna(val) else f"{float(val):,.2f}"

    def _fmt_dt_from_seconds(val) -> str:
        if pd.isna(val):
            return "–"
        try:
            return pd.to_datetime(float(val), unit="s").strftime("%Y-%m-%d")
        except Exception:
            return "–"

    def _render_cat_table(title: str, cols: list[str], key_suffix: str):
        subset = prof_all[prof_all["Column"].isin(cols)].copy()
        st.markdown(f"### {title} ({len(subset)})")

        if subset.empty:
            st.info("No columns in this category.")
            return

        # For "Other" section, add caption explaining what it shows
        if title == "Other":
            st.caption(
                "Columns in data_types but not in channels: "
                + ", ".join(sorted(subset["Column"].astype(str).tolist()))
            )

        # Build display-only columns for Min/Max as strings
        is_dt = subset["Type"].eq("datetime64")
        subset["MinDisp"] = np.where(
            is_dt,
            subset["min"].map(_fmt_dt_from_seconds),
            subset["min"].map(_fmt_num),
        )
        subset["MaxDisp"] = np.where(
            is_dt,
            subset["max"].map(_fmt_dt_from_seconds),
            subset["max"].map(_fmt_num),
        )

        show_cols = [
            "Use",
            "Column",
            "Type",
            "Dist",
            "non_null",
            "nulls",
            "nulls_pct",
            "zeros",
            "zeros_pct",
            "distinct",
            "MinDisp",
            "p10",
            "median",
            "mean",
            "p90",
            "MaxDisp",
            "std",
        ]
        show_cols = [c for c in show_cols if c in subset.columns]

        edited = st.data_editor(
            subset[show_cols],
            hide_index=True,
            width="stretch",
            num_rows="fixed",
            column_config={
                "Use": st.column_config.CheckboxColumn(required=True),
                "Dist": st.column_config.BarChartColumn(
                    "Distribution",
                    help="Numeric: histogram · Categorical: top-k share · Datetime: monthly buckets",
                    y_min=0.0,
                    y_max=1.0,
                ),
                "non_null": st.column_config.NumberColumn(
                    "Non-Null", format="%d"
                ),
                "nulls": st.column_config.NumberColumn("Nulls", format="%d"),
                "nulls_pct": st.column_config.NumberColumn(
                    "Nulls %", format="%.1f%%"
                ),
                "zeros": st.column_config.NumberColumn("Zeros", format="%d"),
                "zeros_pct": st.column_config.NumberColumn(
                    "Zeros %", format="%.1f%%"
                ),
                "distinct": st.column_config.NumberColumn(
                    "Distinct", format="%d"
                ),
                "MinDisp": st.column_config.TextColumn("Min"),
                "p10": st.column_config.NumberColumn("P10", format="%.2f"),
                "median": st.column_config.NumberColumn(
                    "Median", format="%.2f"
                ),
                "mean": st.column_config.NumberColumn("Mean", format="%.2f"),
                "p90": st.column_config.NumberColumn("P90", format="%.2f"),
                "MaxDisp": st.column_config.TextColumn("Max"),
                "std": st.column_config.NumberColumn("Std", format="%.2f"),
            },
            key=f"dq_editor_{key_suffix}",
        )
        for _, r in edited.iterrows():
            use_overrides[str(r["Column"])] = bool(r["Use"])

    # Render all categories
    for title, cols in categories:
        _render_cat_table(
            title, cols, key_suffix=title.replace(" ", "_").lower()
        )

    # Sync Paid Spend selections with Paid Vars (Requirement 4b)
    paid_media_mapping = meta.get("paid_media_mapping", {}) or {}
    _sync_paid_vars_with_spends(
        use_overrides,
        paid_spend,
        paid_vars,
        paid_media_mapping,
        st.session_state["dq_user_selections"],
    )

    # Aggregate final selection across all tables
    final_use = {
        row["Column"]: bool(row["Use"]) for _, row in prof_all.iterrows()
    }
    final_use.update(use_overrides)

    # Persist user selections to session state for next rerun
    st.session_state["dq_user_selections"].update(use_overrides)

    selected_cols = [c for c, u in final_use.items() if u]
    st.session_state["selected_columns_for_training"] = selected_cols

    # Store categories for later export
    st.session_state["column_categories"] = {
        "paid_media_spends": [c for c in paid_spend if c in selected_cols],
        "paid_media_vars": [c for c in paid_vars if c in selected_cols],
        "organic_vars": [c for c in organic_vars if c in selected_cols],
        "context_vars": [c for c in context_vars if c in selected_cols],
        "factor_vars": [c for c in factor_vars if c in selected_cols],
        "other": [c for c in other_cols if c in selected_cols],
    }


# =============================
# Step 3: Prepare paid media spends & media response
# =============================
with st.expander(
    "Step 3) Prepare paid media spends & media response", expanded=False
):
    st.markdown("### 3.1 What output do you want to predict?")

    # Get goals from metadata
    goals_list = meta.get("goals", []) or []
    if not goals_list:
        st.warning(
            "No goals defined in metadata. Please configure goals in Map Your Data."
        )
    else:
        goal_vars = [g.get("var") for g in goals_list if g.get("var")]

        # Determine default index based on prefill from Map Data or main goal
        prefill_goal = st.session_state.get("prefill_goal")
        default_idx = 0
        if prefill_goal and prefill_goal in goal_vars:
            default_idx = goal_vars.index(prefill_goal)
        else:
            # Fallback: use main goal from metadata
            for i, g in enumerate(goals_list):
                if g.get("main", False):
                    if g.get("var") in goal_vars:
                        default_idx = goal_vars.index(g.get("var"))
                        break

        selected_goal = st.selectbox(
            "Select goal to predict",
            options=goal_vars,
            index=default_idx if goal_vars else None,
            key="selected_goal_dropdown",
        )
        st.session_state["selected_goal"] = selected_goal

    # 3.2 Select Paid Media Spends to optimize
    st.markdown("---")
    st.markdown("### 3.2 Select Paid Media Spends to optimize:")

    selected_cols_step2 = st.session_state.get(
        "selected_columns_for_training", []
    )

    # Filter paid media spends from selected columns
    available_paid_spends = [
        c for c in paid_spend_cols if c in selected_cols_step2
    ]

    # Get prefilled paid media spends from Map Data (if any)
    prefill_paid_spends = st.session_state.get("prefill_paid_media_spends", [])

    if not available_paid_spends:
        st.info(
            "No paid media spend columns available. Please select columns in Step 2."
        )
    elif not st.session_state.get("selected_goal"):
        st.info("Please select a goal in section 3.1 above.")
    else:
        selected_goal = st.session_state["selected_goal"]

        # Calculate metrics for each paid spend column
        metrics_data = []
        for spend_col in available_paid_spends:
            if spend_col in df_r.columns and selected_goal in df_r.columns:
                # Skip if spend_col == selected_goal (comparing to itself)
                if spend_col == selected_goal:
                    continue
                # Prepare data for correlation - ensure numeric types
                temp_df = df_r[[spend_col, selected_goal]].copy()
                temp_df[spend_col] = pd.to_numeric(
                    temp_df[spend_col], errors="coerce"
                )
                temp_df[selected_goal] = pd.to_numeric(
                    temp_df[selected_goal], errors="coerce"
                )
                temp_df = temp_df.dropna()

                r2 = np.nan
                nmae = np.nan
                spearman_rho = np.nan

                if len(temp_df) > 1:
                    try:
                        X = np.asarray(
                            temp_df[[spend_col]].values, dtype=np.float64
                        )
                        y = np.asarray(
                            temp_df[selected_goal].values, dtype=np.float64
                        )

                        # Calculate R2
                        model = LinearRegression()
                        model.fit(X, y)
                        y_pred = model.predict(X)
                        r2 = r2_score(y, y_pred)

                        # Calculate NMAE (Normalized Mean Absolute Error)
                        mae = mean_absolute_error(y, y_pred)
                        y_min, y_max = float(y.min()), float(y.max())
                        if (
                            pd.notna(y_min)
                            and pd.notna(y_max)
                            and y_max > y_min
                        ):
                            y_range = y_max - y_min
                            nmae = mae / y_range
                        else:
                            # If range is zero, NaN, or invalid, set NMAE to NaN
                            nmae = np.nan
                    except Exception:
                        pass

                    # Calculate Spearman's rho (suppress warning for constant input)
                    try:
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore")
                            rho, _ = stats.spearmanr(
                                temp_df[spend_col].values,
                                temp_df[selected_goal].values,
                            )
                        spearman_rho = _safe_float(rho)
                    except Exception:
                        spearman_rho = np.nan

                # Use session state selection if available, else use prefill
                if spend_col in st.session_state["paid_spend_selections"]:
                    is_selected = st.session_state["paid_spend_selections"][
                        spend_col
                    ]
                elif prefill_paid_spends:
                    is_selected = spend_col in prefill_paid_spends
                else:
                    is_selected = True  # Default to selected

                metrics_data.append(
                    {
                        "Select": is_selected,
                        "Paid Media Spend": spend_col,
                        "R²": _safe_float(r2),
                        "NMAE": _safe_float(nmae),
                        "Spearman's ρ": _safe_float(spearman_rho),
                    }
                )

        if metrics_data:
            metrics_df = pd.DataFrame(metrics_data)

            # Display editable table
            edited_metrics = st.data_editor(
                metrics_df,
                hide_index=True,
                width="stretch",
                num_rows="fixed",
                column_config={
                    "Select": st.column_config.CheckboxColumn(required=True),
                    "Paid Media Spend": st.column_config.TextColumn(
                        "Paid Media Spend", disabled=True
                    ),
                    "R²": st.column_config.NumberColumn("R²", format="%.4f"),
                    "NMAE": st.column_config.NumberColumn(
                        "NMAE", format="%.4f"
                    ),
                    "Spearman's ρ": st.column_config.NumberColumn(
                        "Spearman's ρ", format="%.4f"
                    ),
                },
                key="paid_spends_metrics_table",
            )

            # Update session state with current selections
            for _, row in edited_metrics.iterrows():
                spend_col = row["Paid Media Spend"]
                is_selected = bool(row["Select"])
                st.session_state["paid_spend_selections"][
                    spend_col
                ] = is_selected

            # Store selected paid spends
            selected_paid_spends = edited_metrics[edited_metrics["Select"]][
                "Paid Media Spend"
            ].tolist()
            st.session_state["selected_paid_spends"] = selected_paid_spends

    # 3.3 Select Media Response Variables
    st.markdown("---")
    st.markdown("### 3.3 Select Media Response Variables")

    selected_paid_spends = st.session_state.get("selected_paid_spends", [])

    if not selected_paid_spends:
        st.info("Please select paid media spends in section 3.2 above.")
    else:
        # Get paid_media_mapping from metadata
        paid_media_mapping = meta.get("paid_media_mapping", {}) or {}

        for spend_col in selected_paid_spends:
            st.markdown(f"#### {spend_col}")

            # Get corresponding paid media vars
            corresponding_vars = paid_media_mapping.get(spend_col, [])

            # Build options list: include the spend column itself plus mapped vars
            # Always include the spend column as an option (Requirement 5)
            options_list = [spend_col]  # Start with the spend column itself

            # Add mapped vars that are available in the data
            available_vars = [
                v for v in corresponding_vars if v in df_r.columns
            ]
            options_list.extend(available_vars)

            # Remove duplicates while preserving order using dict.fromkeys()
            unique_options = list(dict.fromkeys(options_list))

            if len(unique_options) <= 1 and spend_col not in df_r.columns:
                st.info(
                    f"No media response variables available for {spend_col}"
                )
                continue

            # Calculate metrics for each option
            var_metrics_data = []
            for var_col in unique_options:
                if var_col in df_r.columns and spend_col in df_r.columns:
                    # For spend column itself, add it with N/A metrics
                    if spend_col == var_col:
                        var_metrics_data.append(
                            {
                                "Media Response Variable": f"{var_col} (spend)",
                                "R²": np.nan,
                                "NMAE": np.nan,
                                "Spearman's ρ": np.nan,
                            }
                        )
                        continue
                    temp_df = df_r[[spend_col, var_col]].copy()
                    temp_df[spend_col] = pd.to_numeric(
                        temp_df[spend_col], errors="coerce"
                    )
                    temp_df[var_col] = pd.to_numeric(
                        temp_df[var_col], errors="coerce"
                    )
                    temp_df = temp_df.dropna()

                    r2 = np.nan
                    nmae = np.nan
                    spearman_rho = np.nan

                    if len(temp_df) > 1:
                        try:
                            X = np.asarray(
                                temp_df[[spend_col]].values, dtype=np.float64
                            )
                            y = np.asarray(
                                temp_df[var_col].values, dtype=np.float64
                            )

                            # Calculate R2
                            model = LinearRegression()
                            model.fit(X, y)
                            y_pred = model.predict(X)
                            r2 = r2_score(y, y_pred)

                            # Calculate NMAE
                            mae = mean_absolute_error(y, y_pred)
                            y_min, y_max = float(y.min()), float(y.max())
                            if (
                                pd.notna(y_min)
                                and pd.notna(y_max)
                                and y_max > y_min
                            ):
                                y_range = y_max - y_min
                                nmae = mae / y_range
                            else:
                                # If range is zero, NaN, or invalid, set NMAE to NaN
                                nmae = np.nan
                        except Exception:
                            pass

                        # Calculate Spearman's rho (suppress warning for constant input)
                        try:
                            with warnings.catch_warnings():
                                warnings.simplefilter("ignore")
                                rho, _ = stats.spearmanr(
                                    temp_df[spend_col].values,
                                    temp_df[var_col].values,
                                )
                            spearman_rho = _safe_float(rho)
                        except Exception:
                            spearman_rho = np.nan

                    # Mark if this is the original spend column
                    label = (
                        f"{var_col} (spend)"
                        if var_col == spend_col
                        else var_col
                    )
                    var_metrics_data.append(
                        {
                            "Media Response Variable": label,
                            "R²": _safe_float(r2),
                            "NMAE": _safe_float(nmae),
                            "Spearman's ρ": _safe_float(spearman_rho),
                        }
                    )

            if var_metrics_data:
                var_metrics_df = pd.DataFrame(var_metrics_data)

                # Dropdown to select the media response variable
                st.selectbox(
                    f"Select media response variable for {spend_col}",
                    options=var_metrics_df["Media Response Variable"].tolist(),
                    key=f"media_var_select_{spend_col}",
                )

                # Display metrics table
                st.dataframe(
                    var_metrics_df,
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "Media Response Variable": st.column_config.TextColumn(
                            "Media Response Variable"
                        ),
                        "R²": st.column_config.NumberColumn(
                            "R²", format="%.4f"
                        ),
                        "NMAE": st.column_config.NumberColumn(
                            "NMAE", format="%.4f"
                        ),
                        "Spearman's ρ": st.column_config.NumberColumn(
                            "Spearman's ρ", format="%.4f"
                        ),
                    },
                )

    # Export Selected Columns button (after section 3.4)
    st.markdown("---")
    st.markdown("### Export Selected Columns")

    if st.button(
        "Export Selected Columns", type="primary", key="export_selected_cols"
    ):
        tmp_path = None
        try:
            # Get column categories from session state
            column_categories = st.session_state.get("column_categories", {})
            selected_cols = st.session_state.get(
                "selected_columns_for_training", []
            )

            # Build JSON with columns per category
            export_data = {
                "paid_media_spends": column_categories.get(
                    "paid_media_spends", []
                ),
                "paid_media_vars": column_categories.get("paid_media_vars", []),
                "organic_vars": column_categories.get("organic_vars", []),
                "context_vars": column_categories.get("context_vars", []),
                "factor_vars": column_categories.get("factor_vars", []),
                "other": column_categories.get("other", []),
                "selected_goal": st.session_state.get("selected_goal"),
                "selected_paid_spends": st.session_state.get(
                    "selected_paid_spends", []
                ),
            }

            # Save to GCS
            country = st.session_state.get("country", "de")
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

            # Create temporary file
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as tmp:
                tmp_path = tmp.name
                json.dump(export_data, tmp, indent=2)

            # Upload to GCS
            gcs_path = TRAINING_DATA_PATH_TEMPLATE.format(
                country=country, timestamp=timestamp
            )
            upload_to_gcs(GCS_BUCKET, tmp_path, gcs_path)

            # Count only the column category fields, not metadata fields
            column_category_keys = [
                "paid_media_spends",
                "paid_media_vars",
                "organic_vars",
                "context_vars",
                "factor_vars",
                "other",
            ]
            total_cols = sum(
                len(export_data.get(k, [])) for k in column_category_keys
            )
            st.success(
                f"✅ Exported {total_cols} columns by category "
                f"to gs://{GCS_BUCKET}/{gcs_path}"
            )
            st.session_state["last_exported_columns_path"] = gcs_path
        except Exception as e:
            st.error(f"Failed to export selected columns: {e}")
        finally:
            # Clean up temp file
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)


# =============================
# Step 4: Select strongest drivers and reduce noise
# =============================
with st.expander(
    "Step 4) Select strongest drivers and reduce noise", expanded=False
):
    st.markdown("### Variable Analysis with VIF")
    st.caption(
        "Analyze selected variables for multicollinearity using Variance "
        "Inflation Factor (VIF). Use checkboxes to include/exclude variables. "
        "VIF is calculated within each category table separately. "
        "VIF > 10 indicates high multicollinearity (🔴), VIF 5-10 is "
        "moderate (🟡), VIF < 5 is good (🟢)."
    )

    # Get selected goal for correlation calculations
    selected_goal_step4 = st.session_state.get("selected_goal")

    # Get selected paid media spends from section 3.2
    selected_paid_spends_3_2 = st.session_state.get("selected_paid_spends", [])

    # Get selected media response variables from section 3.3 dropdowns
    SPEND_SUFFIX = " (spend)"
    selected_media_vars_3_3 = []
    for spend_col in selected_paid_spends_3_2:
        media_var = st.session_state.get(f"media_var_select_{spend_col}")
        if media_var:
            # Remove "(spend)" suffix if it's the spend column itself
            if media_var.endswith(SPEND_SUFFIX):
                media_var = media_var[: -len(SPEND_SUFFIX)]
            # Only add non-empty variable names
            if media_var:
                selected_media_vars_3_3.append(media_var)

    # Initialize session state for VIF selections (all preselected by default)
    st.session_state.setdefault("vif_selections", {})
    # Initialize session state for global VIF selections
    st.session_state.setdefault("global_vif_selections", {})

    def _calculate_vif_band(vif_value: float) -> str:
        """Return VIF band indicator based on VIF value."""
        try:
            v = float(vif_value)
            if math.isnan(v) or math.isinf(v):
                return "⚪"  # Unknown/invalid
            elif v >= 10:
                return "🔴"  # High multicollinearity
            elif v >= 5:
                return "🟡"  # Moderate multicollinearity
            else:
                return "🟢"  # Good (low multicollinearity)
        except (TypeError, ValueError):
            return "⚪"  # Unknown/invalid

    def _calculate_condition_number(cols: List[str]) -> float:
        """Calculate the condition number for multicollinearity assessment.

        Condition Number is an overall measure of multicollinearity:
        - < 10: No/low multicollinearity
        - 10-30: Moderate multicollinearity
        - > 30: High multicollinearity
        """
        if len(cols) < 2:
            return np.nan

        try:
            # Filter to valid numeric columns and remove duplicates
            seen = set()
            valid_cols = []
            for c in cols:
                if (
                    c not in seen
                    and c in df_r.columns
                    and pd.api.types.is_numeric_dtype(df_r[c])
                ):
                    seen.add(c)
                    valid_cols.append(c)

            if len(valid_cols) < 2:
                return np.nan

            cond_df = df_r[valid_cols].copy()
            for col in cond_df.columns:
                cond_df[col] = pd.to_numeric(cond_df[col], errors="coerce")

            # Drop columns that are entirely NaN
            cond_df = cond_df.dropna(axis=1, how="all")
            if cond_df.shape[1] < 2:
                return np.nan

            # Fill NaN with column means
            col_means = cond_df.mean().fillna(0)
            cond_df = cond_df.fillna(col_means)

            if len(cond_df) < 2:
                return np.nan

            # Calculate condition number using correlation matrix
            corr_matrix = cond_df.corr()
            eigenvalues = np.linalg.eigvals(corr_matrix.values)
            eigenvalues = np.abs(eigenvalues)
            eigenvalues = eigenvalues[eigenvalues > 1e-10]  # Filter near-zero

            if len(eigenvalues) < 2:
                return np.nan

            condition_number = np.sqrt(eigenvalues.max() / eigenvalues.min())
            return float(condition_number)
        except Exception:
            return np.nan

    def _calculate_collinearity_band(condition_number: float) -> tuple:
        """Return collinearity traffic light based on condition number."""
        try:
            cn = float(condition_number)
            if math.isnan(cn) or math.isinf(cn):
                return "⚪", "Unknown"
            elif cn > 30:
                return "🔴", "High"
            elif cn >= 10:
                return "🟡", "Moderate"
            else:
                return "🟢", "Good"
        except (TypeError, ValueError):
            return "⚪", "Unknown"

    def _calculate_driver_ratio_band(
        n_drivers: int, n_observations: int
    ) -> tuple:
        """Calculate driver ratio traffic light based on Robyn guidelines.

        Robyn recommends at least 10 observations per driver (1:10 ratio).
        - Ratio >= 10: Good (🟢)
        - Ratio 5-10: Moderate/OK (🟡)
        - Ratio < 5: Bad (🔴)
        """
        if n_drivers == 0 or n_observations == 0:
            return "⚪", "N/A", np.nan

        ratio = n_observations / n_drivers

        if ratio >= 10:
            return "🟢", "Good", ratio
        elif ratio >= 5:
            return "🟡", "OK", ratio
        else:
            return "🔴", "Bad", ratio

    def _calculate_vif_for_columns_step4(cols: List[str]) -> dict:
        """Calculate VIF values for a list of columns.

        Handles missing values by filling NaN with column means to avoid
        losing too many rows when data is sparse across multiple columns.
        """
        vif_values = {}
        if len(cols) < 2:
            return vif_values

        try:
            # Filter to valid numeric columns and remove duplicates
            seen = set()
            valid_cols = []
            for c in cols:
                if (
                    c not in seen
                    and c in df_r.columns
                    and pd.api.types.is_numeric_dtype(df_r[c])
                ):
                    seen.add(c)
                    valid_cols.append(c)

            if len(valid_cols) < 2:
                return vif_values

            vif_df = df_r[valid_cols].copy()
            for col in vif_df.columns:
                vif_df[col] = pd.to_numeric(vif_df[col], errors="coerce")

            # Drop columns that are entirely NaN
            vif_df = vif_df.dropna(axis=1, how="all")
            if vif_df.shape[1] < 2:
                return vif_values

            # Fill remaining NaN values with column means
            col_means = vif_df.mean().fillna(0)
            vif_df = vif_df.fillna(col_means)

            # Update valid_cols to match remaining columns after processing
            valid_cols = vif_df.columns.tolist()

            if len(vif_df) > len(valid_cols) + 1:
                try:
                    vif_array = np.asarray(vif_df.values, dtype=np.float64)
                except (ValueError, TypeError):
                    return vif_values

                if vif_array is not None and vif_array.size > 0:
                    for i, col in enumerate(valid_cols):
                        try:
                            vif_values[col] = variance_inflation_factor(
                                vif_array, i
                            )
                        except Exception:
                            vif_values[col] = np.nan
        except Exception:
            pass
        return vif_values

    def _calculate_variable_metrics_step4(
        var_cols: List[str],
        goal_col: str,
        selected_only_cols: List[str],
    ) -> pd.DataFrame:
        """Calculate R², NMAE, Spearman's ρ, VIF for a list of variables."""
        if not var_cols or not goal_col:
            return pd.DataFrame()

        # Filter to columns that exist in df_r and are numeric
        valid_cols = [
            c
            for c in var_cols
            if c in df_r.columns and pd.api.types.is_numeric_dtype(df_r[c])
        ]
        if not valid_cols:
            return pd.DataFrame()

        metrics_data = []

        # Calculate VIF only for selected columns within this table
        vif_values = _calculate_vif_for_columns_step4(selected_only_cols)

        # Calculate metrics for each variable against the goal
        for var_col in valid_cols:
            if var_col not in df_r.columns or goal_col not in df_r.columns:
                continue

            # Skip if var_col == goal_col (comparing to itself)
            if var_col == goal_col:
                continue

            temp_df = df_r[[var_col, goal_col]].copy()
            temp_df[var_col] = pd.to_numeric(temp_df[var_col], errors="coerce")
            temp_df[goal_col] = pd.to_numeric(
                temp_df[goal_col], errors="coerce"
            )
            temp_df = temp_df.dropna()

            r2 = np.nan
            nmae = np.nan
            spearman_rho = np.nan

            if len(temp_df) > 1:
                try:
                    X = np.asarray(temp_df[[var_col]].values, dtype=np.float64)
                    y = np.asarray(temp_df[goal_col].values, dtype=np.float64)
                except (ValueError, TypeError):
                    X = None
                    y = None

                if X is not None and y is not None:
                    try:
                        model = LinearRegression()
                        model.fit(X, y)
                        y_pred = model.predict(X)
                        r2 = r2_score(y, y_pred)

                        mae = mean_absolute_error(y, y_pred)
                        y_min, y_max = float(y.min()), float(y.max())
                        if (
                            pd.notna(y_min)
                            and pd.notna(y_max)
                            and y_max > y_min
                        ):
                            nmae = mae / (y_max - y_min)
                    except Exception:
                        pass

                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        rho, _ = stats.spearmanr(
                            temp_df[var_col].values, temp_df[goal_col].values
                        )
                    spearman_rho = _safe_float(rho)
                except Exception:
                    pass

            # Get VIF value (only if column is selected)
            vif = vif_values.get(var_col, np.nan)
            vif_band = _calculate_vif_band(vif)

            # Determine default Use value from session state or default True
            use_val = st.session_state["vif_selections"].get(var_col, True)

            metrics_data.append(
                {
                    "Use": use_val,
                    "Variable": var_col,
                    "R²": _safe_float(r2),
                    "NMAE": _safe_float(nmae),
                    "Spearman's ρ": _safe_float(spearman_rho),
                    "VIF": _safe_float(vif),
                    "VIF Band": vif_band,
                }
            )

        return pd.DataFrame(metrics_data)

    @st.fragment
    def _render_vif_table_fragment(
        title: str, var_list: List[str], key_suffix: str
    ):
        """Fragment wrapper for VIF table that enables partial reruns.

        Updates session state with selections which persists outside fragment.
        """
        if not var_list or not selected_goal_step4:
            return

        # Get currently selected columns for this table from session state
        selected_cols_for_vif = [
            c
            for c in var_list
            if st.session_state["vif_selections"].get(c, True)
            and c in df_r.columns
            and pd.api.types.is_numeric_dtype(df_r[c])
        ]

        df_metrics = _calculate_variable_metrics_step4(
            var_list, selected_goal_step4, selected_cols_for_vif
        )

        if df_metrics.empty:
            st.info(f"No {title.lower()} available in the data.")
            return

        st.markdown(f"#### {title} ({len(df_metrics)})")

        editor_key = f"vif_editor_step4_{key_suffix}"

        edited = st.data_editor(
            df_metrics,
            hide_index=True,
            width="stretch",
            num_rows="fixed",
            column_config={
                "Use": st.column_config.CheckboxColumn(
                    "Use",
                    help="Include variable in VIF calculation",
                    required=True,
                    default=True,
                ),
                "Variable": st.column_config.TextColumn(
                    "Variable", disabled=True
                ),
                "R²": st.column_config.NumberColumn(
                    "R²", format="%.4f", disabled=True
                ),
                "NMAE": st.column_config.NumberColumn(
                    "NMAE", format="%.4f", disabled=True
                ),
                "Spearman's ρ": st.column_config.NumberColumn(
                    "Spearman's ρ", format="%.4f", disabled=True
                ),
                "VIF": st.column_config.NumberColumn(
                    "VIF", format="%.2f", disabled=True
                ),
                "VIF Band": st.column_config.TextColumn(
                    "VIF Band",
                    help="🟢 = Good (< 5), 🟡 = Moderate (5-10), 🔴 = High (> 10)",
                    disabled=True,
                ),
            },
            key=editor_key,
        )

        # Check if any selections changed and update session state
        needs_rerun = False
        for _, row in edited.iterrows():
            var_name = str(row["Variable"])
            use_val = bool(row["Use"])
            old_val = st.session_state["vif_selections"].get(var_name, True)
            if old_val != use_val:
                needs_rerun = True
            st.session_state["vif_selections"][var_name] = use_val

        # If selections changed, trigger a fragment rerun only
        if needs_rerun:
            st.rerun(scope="fragment")

    def _get_selected_vars_from_session(var_list: List[str]) -> List[str]:
        """Get selected variables from session state for a given var list."""
        return [
            v
            for v in var_list
            if st.session_state["vif_selections"].get(v, True)
            and v in df_r.columns
            and pd.api.types.is_numeric_dtype(df_r[v])
        ]

    # Get column categories from Step 2
    column_categories_step4 = st.session_state.get("column_categories", {})
    selected_organic_step4 = column_categories_step4.get("organic_vars", [])
    selected_context_step4 = column_categories_step4.get("context_vars", [])
    selected_factor_step4 = column_categories_step4.get("factor_vars", [])
    selected_other_step4 = column_categories_step4.get("other", [])

    # Track all selected variables across tables for VIF
    all_selected_vars_step4: List[str] = []

    # Helper function to get all currently selected vars across all categories
    def _get_all_selected_vars_step4() -> List[str]:
        """Get all selected variables from VIF tables."""
        all_vars = []
        # Media vars
        for v in selected_media_vars_3_3:
            if (
                st.session_state["vif_selections"].get(v, True)
                and v in df_r.columns
                and pd.api.types.is_numeric_dtype(df_r[v])
            ):
                all_vars.append(v)
        # Organic vars
        for v in selected_organic_step4:
            if (
                st.session_state["vif_selections"].get(v, True)
                and v in df_r.columns
                and pd.api.types.is_numeric_dtype(df_r[v])
            ):
                all_vars.append(v)
        # Context vars
        for v in selected_context_step4:
            if (
                st.session_state["vif_selections"].get(v, True)
                and v in df_r.columns
                and pd.api.types.is_numeric_dtype(df_r[v])
            ):
                all_vars.append(v)
        # Factor vars
        for v in selected_factor_step4:
            if (
                st.session_state["vif_selections"].get(v, True)
                and v in df_r.columns
                and pd.api.types.is_numeric_dtype(df_r[v])
            ):
                all_vars.append(v)
        # Other vars
        for v in selected_other_step4:
            if (
                st.session_state["vif_selections"].get(v, True)
                and v in df_r.columns
                and pd.api.types.is_numeric_dtype(df_r[v])
            ):
                all_vars.append(v)
        # Remove duplicates while preserving order
        return list(dict.fromkeys(all_vars))

    if not selected_goal_step4:
        st.info(
            "Please select a goal in Step 3 section 3.1 to calculate variable "
            "metrics."
        )
    elif not selected_media_vars_3_3:
        st.info("Please select media response variables in Step 3 section 3.3.")
    else:
        # =====================================================
        # Model Quality Indicators Section (MOVED ABOVE VIF tables)
        # This section updates when VIF selections change (full page rerun)
        # =====================================================
        def _render_model_quality_indicators():
            """Render Model Quality Indicators - updates on VIF changes."""
            # Get all currently selected variables
            final_selected_vars = _get_all_selected_vars_step4()

            st.markdown("#### Model Quality Indicators")
            st.caption(
                "Traffic light indicators based on Robyn MMM best practices. "
                "Updates when you change driver selections below."
            )

            col_ratio, col_collin = st.columns(2)

            # Indicator 1: Ratio of drivers to timeframe
            with col_ratio:
                n_drivers = len(final_selected_vars)
                n_observations = len(df_r) if not df_r.empty else 0

                ratio_light, ratio_label, ratio_value = (
                    _calculate_driver_ratio_band(n_drivers, n_observations)
                )

                st.markdown("##### Driver Ratio")
                st.caption(
                    "Robyn recommends ≥10 observations per driver (1:10)."
                )

                ratio_col1, ratio_col2 = st.columns([1, 2])
                with ratio_col1:
                    st.metric(
                        label="Status",
                        value=f"{ratio_light} {ratio_label}",
                    )
                with ratio_col2:
                    if pd.notna(ratio_value):
                        st.metric(
                            label="Ratio",
                            value=f"{ratio_value:.1f}:1",
                            help=f"{n_observations} obs / {n_drivers} drivers",
                        )
                    else:
                        st.metric(label="Ratio", value="N/A")

                st.caption(f"📊 {n_drivers} drivers, {n_observations} obs")

            # Indicator 2: Collinearity (Condition Number)
            with col_collin:
                # Calculate condition number for selected variables
                if len(final_selected_vars) >= 2:
                    condition_number = _calculate_condition_number(
                        final_selected_vars
                    )
                else:
                    condition_number = np.nan

                collin_light, collin_label = _calculate_collinearity_band(
                    condition_number
                )

                st.markdown("##### Collinearity")
                st.caption(
                    "Condition Number: <10 good, 10-30 moderate, >30 high"
                )

                collin_col1, collin_col2 = st.columns([1, 2])
                with collin_col1:
                    st.metric(
                        label="Status",
                        value=f"{collin_light} {collin_label}",
                    )
                with collin_col2:
                    if pd.notna(condition_number):
                        st.metric(
                            label="CN",
                            value=f"{condition_number:.2f}",
                            help="Calculated from correlation matrix",
                        )
                    else:
                        st.metric(label="CN", value="N/A")

                if pd.notna(condition_number) and condition_number > 30:
                    st.warning(
                        "High collinearity. Consider removing variables."
                    )

        # Render Model Quality Indicators at the top
        _render_model_quality_indicators()

        # Add Refresh button to update indicators after VIF selection changes
        if st.button(
            "🔄 Refresh Quality Indicators",
            help="Click to recalculate indicators after changing driver selections in the VIF tables below",
            key="refresh_quality_indicators",
        ):
            st.rerun()

        st.markdown("---")

        # =====================================================
        # VIF Tables Section
        # =====================================================
        # Render tables with fragment for partial rerun on checkbox change
        _render_vif_table_fragment(
            "Selected Media Response Variables",
            selected_media_vars_3_3,
            "media_vars",
        )
        # Collect selected vars from session state
        all_selected_vars_step4.extend(
            _get_selected_vars_from_session(selected_media_vars_3_3)
        )

        # Render tables for Step 2 category selections
        if selected_organic_step4:
            _render_vif_table_fragment(
                "Selected Organic Variables", selected_organic_step4, "organic"
            )
            all_selected_vars_step4.extend(
                _get_selected_vars_from_session(selected_organic_step4)
            )

        if selected_context_step4:
            _render_vif_table_fragment(
                "Selected Context Variables", selected_context_step4, "context"
            )
            all_selected_vars_step4.extend(
                _get_selected_vars_from_session(selected_context_step4)
            )

        if selected_factor_step4:
            _render_vif_table_fragment(
                "Selected Factor Variables", selected_factor_step4, "factor"
            )
            all_selected_vars_step4.extend(
                _get_selected_vars_from_session(selected_factor_step4)
            )

        if selected_other_step4:
            _render_vif_table_fragment(
                "Selected Other Variables", selected_other_step4, "other"
            )
            all_selected_vars_step4.extend(
                _get_selected_vars_from_session(selected_other_step4)
            )

        # =====================================================
        # Global VIF Section - COMMENTED OUT per requirement
        # =====================================================
        # # Display global VIF across ALL selected variables from all tables
        # st.markdown("---")
        # st.markdown("#### Global VIF (All Selected Variables)")
        # st.caption(
        #     "VIF calculated across all selected variables from all tables "
        #     "above. Use checkboxes to include/exclude variables from the "
        #     "global VIF calculation."
        # )
        #
        # # Remove duplicates while preserving order
        # all_selected_unique_step4 = list(dict.fromkeys(all_selected_vars_step4))
        #
        # if len(all_selected_unique_step4) < 2:
        #     st.info(
        #         "Select at least 2 variables across all tables to calculate "
        #         "global VIF."
        #     )
        # else:
        #     # Get global VIF selections from session state
        #     # Initialize with all selected if not set
        #     for var in all_selected_unique_step4:
        #         if var not in st.session_state["global_vif_selections"]:
        #             st.session_state["global_vif_selections"][var] = True
        #
        #     @st.fragment
        #     def _render_global_vif_fragment():
        #         """Fragment for Global VIF table to enable partial reruns."""
        #         # Filter to only globally selected variables
        #         globally_selected_vars = [
        #             v
        #             for v in all_selected_unique_step4
        #             if st.session_state["global_vif_selections"].get(v, True)
        #         ]
        #
        #         global_vif = _calculate_vif_for_columns_step4(
        #             globally_selected_vars
        #         )
        #
        #         if global_vif or all_selected_unique_step4:
        #             global_vif_data = []
        #             for var_col in all_selected_unique_step4:
        #                 use_val = st.session_state[
        #                     "global_vif_selections"
        #                 ].get(var_col, True)
        #                 vif = (
        #                     global_vif.get(var_col, np.nan)
        #                     if use_val
        #                     else np.nan
        #                 )
        #                 global_vif_data.append(
        #                     {
        #                         "Use": use_val,
        #                         "Variable": var_col,
        #                         "Global VIF": _safe_float(vif),
        #                         "VIF Band": (
        #                             _calculate_vif_band(vif)
        #                             if use_val
        #                             else "⚪"
        #                         ),
        #                     }
        #                 )
        #
        #             global_vif_df = pd.DataFrame(global_vif_data)
        #
        #             edited_global = st.data_editor(
        #                 global_vif_df,
        #                 hide_index=True,
        #                 width="stretch",
        #                 num_rows="fixed",
        #                 column_config={
        #                     "Use": st.column_config.CheckboxColumn(
        #                         "Use",
        #                         help="Include in global VIF calculation",
        #                         required=True,
        #                         default=True,
        #                     ),
        #                     "Variable": st.column_config.TextColumn(
        #                         "Variable", disabled=True
        #                     ),
        #                     "Global VIF": st.column_config.NumberColumn(
        #                         "Global VIF", format="%.2f", disabled=True
        #                     ),
        #                     "VIF Band": st.column_config.TextColumn(
        #                         "VIF Band",
        #                         help="🟢 = Good, 🟡 = Moderate, 🔴 = High",
        #                         disabled=True,
        #                     ),
        #                 },
        #                 key="global_vif_table_step4",
        #             )
        #
        #             # Check if selections changed and update session state
        #             needs_rerun = False
        #             for _, row in edited_global.iterrows():
        #                 var_name = str(row["Variable"])
        #                 use_val = bool(row["Use"])
        #                 old_val = st.session_state[
        #                     "global_vif_selections"
        #                 ].get(var_name, True)
        #                 if old_val != use_val:
        #                     needs_rerun = True
        #                 st.session_state["global_vif_selections"][
        #                     var_name
        #                 ] = use_val
        #
        #             # Fragment rerun only
        #             if needs_rerun:
        #                 st.rerun(scope="fragment")
        #         else:
        #             st.info(
        #                 "Unable to calculate global VIF for selected variables."
        #             )
        #
        #     # Call the fragment
        #     _render_global_vif_fragment()

        # =====================================================
        # Export to Training Page Button
        # =====================================================
        st.markdown("---")
        st.markdown("### Export to Training Page")
        st.caption(
            "Export your current selections to prefill the Training page "
            "with data, metadata, and selected drivers."
        )

        if st.button(
            "🚀 Export to Training Page",
            type="primary",
            key="export_to_training_page",
        ):
            # Get final selected variables from VIF selections
            final_vars = _get_all_selected_vars_step4()

            # Get VIF-selected media response variables
            # These are the media vars selected in Step 3.3 AND kept via VIF
            selected_media_response_vars = [
                v
                for v in selected_media_vars_3_3
                if st.session_state["vif_selections"].get(v, True)
                and v in df_r.columns
                and pd.api.types.is_numeric_dtype(df_r[v])
            ]

            # Get paid_media_mapping from metadata to find corresponding spends
            paid_media_mapping = meta.get("paid_media_mapping", {}) or {}

            # Build reverse mapping: var -> spend
            var_to_spend_mapping = {}
            for spend_col, var_list in paid_media_mapping.items():
                for var_col in var_list:
                    var_to_spend_mapping[var_col] = spend_col
            # Also map spend columns to themselves (when spend is used as var)
            for spend_col in paid_media_mapping.keys():
                if spend_col not in var_to_spend_mapping:
                    var_to_spend_mapping[spend_col] = spend_col

            # Find corresponding spends for the VIF-selected media response vars
            selected_spends_from_vif = []
            var_to_spend_export = {}
            for media_var in selected_media_response_vars:
                spend_col = var_to_spend_mapping.get(media_var)
                if spend_col:
                    if spend_col not in selected_spends_from_vif:
                        selected_spends_from_vif.append(spend_col)
                    var_to_spend_export[media_var] = spend_col

            # Store selections in session state for Training page
            st.session_state["training_prefill"] = {
                "country": st.session_state.get("country", "de"),
                "selected_goal": st.session_state.get("selected_goal"),
                "paid_media_spends": selected_spends_from_vif,
                "paid_media_vars": selected_media_response_vars,
                "var_to_spend_mapping": var_to_spend_export,
                "organic_vars": [
                    v for v in selected_organic_step4 if v in final_vars
                ],
                "context_vars": [
                    v for v in selected_context_step4 if v in final_vars
                ],
                "factor_vars": [
                    v for v in selected_factor_step4 if v in final_vars
                ],
                "all_selected_drivers": final_vars,
                "data_version": st.session_state.get(
                    "picked_data_ts", "Latest"
                ),
                "meta_version": st.session_state.get(
                    "picked_meta_ts", "Latest"
                ),
            }

            # Store flag to indicate prefill is ready
            st.session_state["training_prefill_ready"] = True

            st.success(
                f"✅ Exported {len(final_vars)} selected drivers to Training page!\n\n"
                f"**Paid Media Response Vars (VIF selected):** {len(selected_media_response_vars)}\n"
                f"**Corresponding Paid Media Spends:** {len(selected_spends_from_vif)}\n"
                f"**Organic Vars:** {len([v for v in selected_organic_step4 if v in final_vars])}\n"
                f"**Context Vars:** {len([v for v in selected_context_step4 if v in final_vars])}\n"
                f"**Factor Vars:** {len([v for v in selected_factor_step4 if v in final_vars])}\n\n"
                "👉 Navigate to **Experiment** page to see prefilled values."
            )
