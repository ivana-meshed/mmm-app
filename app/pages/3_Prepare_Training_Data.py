import os
import re
import numpy as np
import pandas as pd
from sklearn.preprocessing import PolynomialFeatures
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score, mean_absolute_error
from scipy import stats
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import warnings
 
st.set_page_config(
    page_title="Prepare Training Data for Experimentation", layout="wide"
)

from app_shared import (
    # GCS & versions
    list_data_versions,
    list_meta_versions,
    load_data_from_gcs,
    download_parquet_from_gcs_cached,
    download_json_from_gcs_cached,
    data_blob,
    data_latest_blob,
    meta_blob,
    meta_latest_blob,
    # meta & utilities
    build_meta_views,
    build_plat_map_df,
    validate_against_metadata,
    parse_date,
    pretty,
    fmt_num,
    freq_to_rule,
    period_label,
    safe_eff,
    kpi_box,
    kpi_grid,
    kpi_grid_fixed,
    BASE_PLATFORM_COLORS,
    build_platform_colors,
    # sidebar + filters
    render_sidebar,
    filter_range,
    previous_window,
    resample_numeric,
    total_with_prev,
    resolve_meta_blob_from_selection,
    require_login_and_domain,
    # colors (if exported; otherwise define locally)
    GREEN,
    RED,
)

require_login_and_domain()

st.title("Review Business- & Marketing Data")

GCS_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")

# -----------------------------
# Session defaults
# -----------------------------
st.session_state.setdefault("country", "de")
st.session_state.setdefault("picked_data_ts", "Latest")
st.session_state.setdefault("picked_meta_ts", "Latest")

# ---------- Data Profile helpers ----------
def _num_stats(s: pd.Series) -> dict:
    s = pd.to_numeric(s, errors="coerce")
    n = len(s)
    nn = int(s.notna().sum())
    na = n - nn
    if nn == 0:
        return dict(
            non_null=nn, nulls=na, nulls_pct=np.nan, zeros=0, zeros_pct=np.nan,
            distinct=0, min=np.nan, p10=np.nan, median=np.nan, mean=np.nan,
            p90=np.nan, max=np.nan, std=np.nan
        )
    z = int((s.fillna(0) == 0).sum())
    s2 = s.dropna()
    return dict(
        non_null=nn,
        nulls=na,
        nulls_pct=na / n if n else np.nan,
        zeros=z,
        zeros_pct=z / n if n else np.nan,
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
    # “zeros” not meaningful for cats; leave blank
    return dict(
        non_null=nn,
        nulls=na,
        nulls_pct=na / n if n else np.nan,
        zeros=np.nan,
        zeros_pct=np.nan,
        distinct=int(s2.nunique(dropna=True)) if nn else 0,
        min=np.nan, p10=np.nan, median=np.nan, mean=np.nan,
        p90=np.nan, max=np.nan, std=np.nan,
    )

def _distribution_values(s: pd.Series, *, numeric_bins: int = 10, cat_topk: int = 5) -> list[float]:
    """
    Return a normalized list of values suitable for Streamlit's BarChartColumn.
    - Numeric: histogram shares over `numeric_bins`
    - Datetime: monthly bucket shares
    - Categorical/bool: top-k value shares
    """
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

        # categorical / bool
        q = s.dropna().astype("object")
        if q.empty:
            return []
        vc = q.value_counts().head(cat_topk)
        total = vc.sum()
        return (vc / total).tolist() if total else []
    except Exception:
        return []
        
# -----------------------------
# TABS
# -----------------------------
tab_load, tab_biz, tab_mkt, tab_profile = st.tabs(
    [
        "Select Data To Analyze",
        "Data Profile"
    ]
)

# =============================
# TAB 0 — DATA & METADATA LOADING
# =============================
with tab_load:
    st.markdown("### Select country and data versions to analyze")
    c1, c2, c3, c4 = st.columns([1.2, 1, 1, 0.6])

    country = (
        c1.text_input("Country", value=st.session_state["country"])
        .strip()
        .lower()
    )
    if country:
        st.session_state["country"] = country

    refresh_clicked = c4.button("↻ Refresh Lists")
    refresh_key = str(pd.Timestamp.utcnow().value) if refresh_clicked else ""

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

    load_clicked = st.button("Select & Load", type="primary")

    if load_clicked:
        try:
            # Resolve DATA path (unchanged)
            db = (
                data_latest_blob(country)
                if data_ts == "Latest"
                else data_blob(country, str(data_ts))
            )

            # Resolve META path from the UI label safely:
            # "Latest", "Universal - <ts>", "<CC> - <ts>", or bare "<ts>"
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
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.caption("No type mismatches detected (coarse check).")
        except Exception as e:
            st.error(f"Load failed: {e}")

# -----------------------------
# State
# -----------------------------
df = st.session_state.get("df", pd.DataFrame())
meta = st.session_state.get("meta", {}) or {}
DATE_COL = st.session_state.get("date_col", "DATE")
CHANNELS_MAP = st.session_state.get("channels_map", {}) or {}

if df.empty or not meta:
    st.stop()

# -----------------------------
# Meta helpers
# -----------------------------
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

# ---- Nice label resolver (coalesce: metadata nice() -> pretty()) ----
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

# -----------------------------
# Sidebar
# -----------------------------
GOAL, sel_countries, TIMEFRAME_LABEL, RANGE, agg_label, FREQ = render_sidebar(
    meta, df, nice_title, goal_cols
)

# Country filter
if sel_countries and "COUNTRY" in df.columns:
    df = df[df["COUNTRY"].astype(str).isin(sel_countries)].copy()

# -----------------------------
# Target, spend, platforms
# -----------------------------
target = (
    GOAL
    if (GOAL and GOAL in df.columns)
    else (goal_cols[0] if goal_cols else None)
)

paid_spend_cols = [
    c for c in (mapping.get("paid_media_spends", []) or []) if c in df.columns
]
df["_TOTAL_SPEND"] = df[paid_spend_cols].sum(axis=1) if paid_spend_cols else 0.0

# REPLACE the old 'paid_var_cols = metadata.get("Paid Media Variables", [])' with:
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

# Convenience unions used later
present_spend = paid_spend_cols
present_vars = paid_var_cols + organic_cols + context_cols + factor_cols

RULE = freq_to_rule(FREQ)
spend_label = (
    (meta.get("labels", {}) or {}).get("spend", "Spend")
    if isinstance(meta, dict)
    else "Spend"
)

plat_map_df, platforms, PLATFORM_COLORS = build_plat_map_df(
    present_spend=paid_spend_cols,
    df=df,
    meta=meta,
    m=m,
    COL="column_name",
    PLAT="platform",
    CHANNELS_MAP=CHANNELS_MAP,
)

# -----------------------------
# Timeframe & resample
# -----------------------------
df_r = filter_range(df.copy(), DATE_COL, RANGE)
df_prev = previous_window(df, df_r, DATE_COL, RANGE)


# --- Back-compat shim for total_with_prev (expects df_r, df_prev, collist) ---
def total_with_prev_local(collist):
    return total_with_prev(df_r, df_prev, collist)

res = resample_numeric(
    df_r, DATE_COL, RULE, ensure_cols=[target, "_TOTAL_SPEND"]
)
res["PERIOD_LABEL"] = period_label(res["DATE_PERIOD"], RULE)


# =============================
# TAB 1 — DATA PROFILE
# =============================
with tab_profile:
    st.subheader(f"Data Profile — {TIMEFRAME_LABEL}")

    # Timeframe-adjusted frame
    prof_df = df_r.copy()
    if prof_df.empty:
        st.info("No data in the selected timeframe to profile.")
        st.stop()

    # ----- Category filter from metadata (first) -----
    paid_spend   = set(mapping.get("paid_media_spends", []) or [])
    paid_vars    = set(mapping.get("paid_media_vars", []) or [])
    organic_vars = set(mapping.get("organic_vars", []) or [])
    context_vars = set(mapping.get("context_vars", []) or [])
    factor_vars  = set(mapping.get("factor_vars", []) or [])

    known = paid_spend | paid_vars | organic_vars | context_vars | factor_vars
    other = set(prof_df.columns) - known

    category_map = {
        "Paid Spend": paid_spend,
        "Paid Vars": paid_vars,
        "Organic": organic_vars,
        "Context": context_vars,
        "Factor": factor_vars,
        "Other": other,
    }

    cat_pick = st.multiselect(
        "Metadata categories",
        options=list(category_map.keys()),
        default=list(category_map.keys()),
        help="Filter columns by metadata category first."
    )

    # If no category picked, no columns are in-scope
    if not cat_pick:
        st.info("Pick at least one metadata category.")
        st.stop()

    wanted_cat_cols = set().union(*(category_map[c] for c in cat_pick))

    # ----- Optional: also filter by column type -----
    filter_by_type = st.checkbox(
        "Also filter by column type", value=False,
        help="Check to additionally restrict the profile to selected data types."
    )

    numeric_cols  = prof_df.select_dtypes(include=[np.number]).columns.tolist()
    datetime_cols = prof_df.select_dtypes(include=["datetime", "datetimetz"]).columns.tolist()
    bool_cols     = prof_df.select_dtypes(include=["bool"]).columns.tolist()
    object_cols   = [c for c in prof_df.columns if c not in numeric_cols + datetime_cols + bool_cols]

    type_opts = {
        "Numeric": numeric_cols,
        "Categorical": object_cols + bool_cols,
        "Datetime": datetime_cols,
    }

    if filter_by_type:
        type_pick = st.multiselect(
            "Column types to include",
            ["Numeric", "Categorical", "Datetime"],
            default=["Numeric", "Categorical", "Datetime"],
            help="Profile only selected types (on top of the category filter)."
        )
        wanted_type_cols = set().union(*(type_opts.get(t, []) for t in type_pick)) if type_pick else set()
    else:
        type_pick = []
        wanted_type_cols = set(prof_df.columns)  # no type restriction

    # Build final wanted columns (preserve DF order)
    wanted_cols = [c for c in prof_df.columns if (c in wanted_cat_cols) and (c in wanted_type_cols)]

    # Optional text filter
    col_filter = st.text_input("Filter columns (contains)", value="").strip().lower()
    if col_filter:
        wanted_cols = [c for c in wanted_cols if col_filter in str(c).lower()]

    if not wanted_cols:
        st.info("No columns match the current filters.")
        st.stop()

    # ----- Build profile rows -----
    rows = []
    for col in wanted_cols:
        s = prof_df[col]
        col_type = str(s.dtype)

        if pd.api.types.is_numeric_dtype(s):
            stats = _num_stats(s)
        elif pd.api.types.is_datetime64_any_dtype(s):
            # datetime summary: reuse cat base + min/max timestamps
            stats = _cat_stats(s)
            ss = pd.to_datetime(s, errors="coerce").dropna()
            stats["min"] = ss.min().timestamp() if not ss.empty else np.nan
            stats["max"] = ss.max().timestamp() if not ss.empty else np.nan
            col_type = "datetime64"
        else:
            stats = _cat_stats(s)

        dist_vals = _distribution_values(s)

        rows.append(
            dict(
                Use=True,
                Column=col,
                Type=col_type,
                Dist=dist_vals,
                **stats,
            )
        )

    prof_table = pd.DataFrame(rows)

    # ----- Quick header metrics -----
    total_rows = len(prof_df)
    total_cols = len(prof_df.columns)
    left, right = st.columns([1, 2])
    with left:
        st.metric("Rows (selected timeframe)", f"{total_rows:,}")
        st.metric("Columns", f"{total_cols:,}")
    with right:
        high_null  = (prof_table["nulls_pct"] >= 0.5).fillna(False)
        constant   = (prof_table["distinct"] <= 1).fillna(False)
        zeros_only = (prof_table["zeros_pct"] >= 0.99).fillna(False) & prof_table["non_null"].fillna(0).gt(0)
        st.caption(
            f"Potential issues — High-null: {int(high_null.sum()):,} · "
            f"Constant: {int(constant.sum()):,} · "
            f"All/Mostly Zero (numeric): {int(zeros_only.sum()):,}"
        )

    # ----- Prepare display frame -----
    disp = prof_table.copy()

    # Format datetime min/max to readable strings *only when any datetime present*,
    # so the dtype stays float when no datetime is in the current selection.
    is_dt_present = disp["Type"].eq("datetime64").any()
    if is_dt_present:
        def _fmt_dt(num_ts):
            if pd.isna(num_ts):
                return "–"
            try:
                return pd.to_datetime(num_ts, unit="s").strftime("%Y-%m-%d")
            except Exception:
                return "–"
        mask_dt = disp["Type"].eq("datetime64")
        disp.loc[mask_dt, "min"] = disp.loc[mask_dt, "min"].map(_fmt_dt)
        disp.loc[mask_dt, "max"] = disp.loc[mask_dt, "max"].map(_fmt_dt)

    # ----- Interactive grid -----
    # Put Distribution right after Type as requested
    grid_cols = [
        "Use", "Column", "Type", "Dist",
        "non_null", "nulls", "nulls_pct",
        "zeros", "zeros_pct",
        "distinct",
        "min", "p10", "median", "mean", "p90", "max", "std",
    ]
    show_cols = [c for c in grid_cols if c in disp.columns]

    # Column config: choose Text vs Number for min/max dynamically to avoid type conflicts
    min_cfg = (
        st.column_config.TextColumn("Min")
        if is_dt_present else
        st.column_config.NumberColumn("Min", format="%.2f")
    )
    max_cfg = (
        st.column_config.TextColumn("Max")
        if is_dt_present else
        st.column_config.NumberColumn("Max", format="%.2f")
    )

    edited = st.data_editor(
        disp[show_cols],
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        column_config={
            "Use": st.column_config.CheckboxColumn(required=True),
            "Dist": st.column_config.BarChartColumn(
                "Distribution",
                help="For numeric: histogram; for categorical: top-k share; for datetime: monthly buckets.",
                y_min=0.0, y_max=1.0,
            ),
            "non_null":  st.column_config.NumberColumn("Non-Null", format="%.0f"),
            "nulls":     st.column_config.NumberColumn("Nulls", format="%.0f"),
            "nulls_pct": st.column_config.NumberColumn("Nulls %", format="%.1f%%"),
            "zeros":     st.column_config.NumberColumn("Zeros", format="%.0f"),
            "zeros_pct": st.column_config.NumberColumn("Zeros %", format="%.1f%%"),
            "distinct":  st.column_config.NumberColumn("Distinct", format="%.0f"),
            "min":       min_cfg,
            "p10":       st.column_config.NumberColumn("P10", format="%.2f"),
            "median":    st.column_config.NumberColumn("Median", format="%.2f"),
            "mean":      st.column_config.NumberColumn("Mean", format="%.2f"),
            "p90":       st.column_config.NumberColumn("P90", format="%.2f"),
            "max":       max_cfg,
            "std":       st.column_config.NumberColumn("Std", format="%.2f"),
        },
        key="data_profile_editor_v2",
    )

    # Persist selected cols for later views
    selected_cols = edited.loc[edited["Use"] == True, "Column"].tolist() if not edited.empty else []
    st.session_state["selected_profile_columns"] = selected_cols

    # ----- Downloads (clear names) -----
    prof_out = prof_table[prof_table["Column"].isin(selected_cols)].copy() if selected_cols else prof_table.copy()
    csv = prof_out.drop(columns=["Dist"], errors="ignore").to_csv(index=False).encode("utf-8")
    st.download_button(
        "Export profile (CSV, selected rows)",
        data=csv,
        file_name="data_profile_selected.csv",
        mime="text/csv",
    )

    if selected_cols:
        cols_csv = pd.DataFrame({"column": selected_cols}).to_csv(index=False).encode("utf-8")
        st.download_button(
            "Export selected column names (CSV)",
            data=cols_csv,
            file_name="selected_columns.csv",
            mime="text/csv",
        )