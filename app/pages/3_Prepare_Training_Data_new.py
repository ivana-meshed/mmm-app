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

st.set_page_config(
    page_title="Prepare Training Data for Experimentation", layout="wide"
)

require_login_and_domain()

st.title("Prepare Training Data for Experimentation")

GCS_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")

# -----------------------------
# Session defaults
# -----------------------------
st.session_state.setdefault("country", "de")
st.session_state.setdefault("picked_data_ts", "Latest")
st.session_state.setdefault("picked_meta_ts", "Latest")

# -----------------------------
# TABS
# -----------------------------
tab_load, tab_quality = st.tabs(
    [
        "Select Data To Analyze",
        "Data Quality"
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

# ===== Data Quality helpers (add above TAB 1) =====
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
        
def _var_platform(col: str, platforms: list[str]) -> str | None:
    """Best-effort platform detection for a var column by token match."""
    cu = str(col).upper()
    for p in platforms:
        if p.upper() in cu:
            return p
    return None

def _active_spend_platforms(df_window: pd.DataFrame, plat_map_df: pd.DataFrame) -> set[str]:
    """Platforms with >0 spend in the selected timeframe."""
    if df_window.empty or plat_map_df.empty:
        return set()
    vm = plat_map_df.copy()
    vm = vm.dropna(subset=["col", "platform"])
    vm = vm[vm["col"].isin(df_window.columns)]
    if vm.empty:
        return set()
    sums = (
        df_window[vm["col"].tolist()]
        .melt(value_name="spend")
        .dropna(subset=["spend"])
        .groupby(vm.reset_index(drop=True)["platform"])["spend"]
        .sum(min_count=1)
    )
    return set(sums[sums.fillna(0) > 0].index.astype(str))

def _cv(mean_val: float, std_val: float) -> float:
    """Coefficient of variation; returns np.inf if mean is 0 (so it won't be flagged low-variance)."""
    if pd.isna(mean_val) or pd.isna(std_val):
        return np.nan
    if abs(mean_val) < 1e-12:
        return np.inf
    return float(abs(std_val) / abs(mean_val))

def _protect_columns_set(date_col: str, goal_cols: list[str]) -> set[str]:
    """Case-insensitive protected names (DATE, COUNTRY, all goals)."""
    prot = {str(date_col), "COUNTRY"}
    prot |= set(goal_cols or [])
    return set(p.upper() for p in prot)



# =============================
# TAB 1 — Data Quality
# =============================
with tab_quality:
    st.subheader(f"Data Quality — {TIMEFRAME_LABEL}")

    # Timeframe-adjusted frame
    prof_df = df_r.copy()
    if prof_df.empty:
        st.info("No data in the selected timeframe to profile.")
        st.stop()

    # ---- Build metadata categories strictly from metadata ----
    paid_spend   = [c for c in (mapping.get("paid_media_spends", []) or []) if c in prof_df.columns]
    paid_vars    = [c for c in (mapping.get("paid_media_vars",   []) or []) if c in prof_df.columns]
    organic_vars = [c for c in (mapping.get("organic_vars",      []) or []) if c in prof_df.columns]
    context_vars = [c for c in (mapping.get("context_vars",      []) or []) if c in prof_df.columns]
    factor_vars  = [c for c in (mapping.get("factor_vars",       []) or []) if c in prof_df.columns]

    known_set = set(paid_spend) | set(paid_vars) | set(organic_vars) | set(context_vars) | set(factor_vars)
    other_cols = [c for c in prof_df.columns if c not in known_set]

    categories = [
        ("Paid Spend",  paid_spend),
        ("Paid Vars",   paid_vars),
        ("Organic",     organic_vars),
        ("Context",     context_vars),
        ("Factor",      factor_vars),
        ("Other",       other_cols),
    ]

    # ---- One full profile table (we'll display per-category slices) ----
    rows = []
    for col in prof_df.columns:
        s = prof_df[col]
        col_type = str(s.dtype)

        if pd.api.types.is_numeric_dtype(s):
            stats = _num_stats(s)
        elif pd.api.types.is_datetime64_any_dtype(s):
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
    prof_all = pd.DataFrame(rows)

    # ---- Cleaning controls ----
    with st.expander("Automated cleaning (optional)", expanded=False):
        rules = st.multiselect(
            "Choose cleaning rules to APPLY:",
            [
                "Drop all-null columns",
                "Drop all-zero (numeric) columns",
                "Drop constant (distinct==1)",
                "Drop low variance (CV < threshold)",
            ],
            default=[],
        )
        cv_thr = st.slider("Low-variance threshold (CV)", 0.1, 15.0, 3.0, 0.1)
        c1, c2 = st.columns([1, 1])
        apply_clean = c1.button("Apply cleaning")
        reset_clean = c2.button("Reset cleaning")

    # Session state for cleaning persistence
    st.session_state.setdefault("dq_dropped_cols", set())
    st.session_state.setdefault("dq_clean_note", "")

    if reset_clean:
        st.session_state["dq_dropped_cols"] = set()
        st.session_state["dq_clean_note"] = ""
        st.experimental_rerun()

    # ---- Apply cleaning when requested ----
    if apply_clean and rules:
        to_drop = set()

        # Vectorized helpers from prof_all:
        by_col = prof_all.set_index("Column")

        # Rule: all-null
        if "Drop all-null columns" in rules:
            mask_all_null = by_col["non_null"].fillna(0).eq(0)
            to_drop |= set(by_col[mask_all_null].index)

        # Rule: all-zero (numeric)
        if "Drop all-zero (numeric) columns" in rules:
            # non_null>0 AND zeros == non_null
            nn = by_col["non_null"].fillna(0)
            zz = by_col["zeros"].fillna(-1)
            mask_all_zero = (nn > 0) & (zz == nn)
            to_drop |= set(by_col[mask_all_zero].index)

        # Rule: constant (distinct==1)
        if "Drop constant (distinct==1)" in rules:
            mask_const = by_col["distinct"].fillna(0).eq(1)
            to_drop |= set(by_col[mask_const].index)

        # Rule: low variance by CV (numeric)
        if "Drop low variance (CV < threshold)" in rules:
            means = by_col["mean"]
            stds  = by_col["std"]
            # Compute CV; treat non-numeric rows as NaN in mean/std
            cv_series = pd.Series(
                [_cv(means.get(i), stds.get(i)) for i in by_col.index],
                index=by_col.index
            )
            mask_low_cv = cv_series < (cv_thr / 100.0)  # slider is in %, convert to ratio
            to_drop |= set(cv_series[mask_low_cv.fillna(False)].index)

        # Never drop protected
        protected = _protect_columns_set(DATE_COL, goal_cols)
        to_drop = {c for c in to_drop if str(c).upper() not in protected}

        # ---- Guard: keep at least one paid var per platform that has >0 spend ----
        # Identify active spend platforms
        active_plats = _active_spend_platforms(prof_df, plat_map_df)
        # Map var -> platform (best-effort)
        var_map = {v: _var_platform(v, platforms) for v in paid_vars}
        # For each platform with spend, ensure at least one var remains
        warnings_list = []
        for p in active_plats:
            vars_for_p = [v for v, vp in var_map.items() if vp == p]
            if not vars_for_p:
                continue
            # Are we about to drop them all?
            dropping_all = all(v in to_drop for v in vars_for_p)
            if dropping_all:
                # keep the "least-bad" one (largest std as proxy for signal)
                by_p = by_col.loc[[v for v in vars_for_p if v in by_col.index]]
                keep_one = by_p["std"].astype(float).idxmax()
                to_drop.discard(keep_one)
                warnings_list.append(f"{p}: kept '{keep_one}' to retain at least one var for active spend.")

        # Update session dropped set
        st.session_state["dq_dropped_cols"] |= set(to_drop)

        note = f"Dropped {len(to_drop)} column(s)."
        if warnings_list:
            note += " Guards applied — " + "; ".join(warnings_list)
        st.session_state["dq_clean_note"] = note

    if st.session_state["dq_clean_note"]:
        st.info(st.session_state["dq_clean_note"])

    # ---- Apply dropped flags to 'Use' default ----
    dropped = st.session_state["dq_dropped_cols"]
    prof_all["Use"] = ~prof_all["Column"].isin(dropped)

    # ---- Human-friendly display formats ----
    # Keep numeric as numeric (so sorting works), just format with commas.
    is_dt_present = prof_all["Type"].eq("datetime64").any()
    disp_all = prof_all.copy()
    if is_dt_present:
        mask_dt = disp_all["Type"].eq("datetime64")
        def _fmt_dt(num_ts):
            if pd.isna(num_ts):
                return "–"
            try:
                return pd.to_datetime(num_ts, unit="s").strftime("%Y-%m-%d")
            except Exception:
                return "–"
        disp_all.loc[mask_dt, "min"] = disp_all.loc[mask_dt, "min"].map(_fmt_dt)
        disp_all.loc[mask_dt, "max"] = disp_all.loc[mask_dt, "max"].map(_fmt_dt)

    # ---- Render 6 tables, aggregate selection across them ----
    use_overrides: dict[str, bool] = {}

    def _render_cat_table(title: str, cols: list[str], key_suffix: str):
        subset = disp_all[disp_all["Column"].isin(cols)].copy()
        st.markdown(f"### {title} ({len(subset)})")
        if title == "Other" and len(subset):
            st.caption("Unmapped columns (in data but not in metadata): " + ", ".join(sorted(subset["Column"].astype(str).tolist())))
        if subset.empty:
            st.info("No columns in this category.")
            return

        # Dynamic config for min/max
        min_cfg = st.column_config.TextColumn("Min") if is_dt_present else st.column_config.NumberColumn("Min", format="%,.2f")
        max_cfg = st.column_config.TextColumn("Max") if is_dt_present else st.column_config.NumberColumn("Max", format="%,.2f")

        show_cols = [
            "Use", "Column", "Type", "Dist",
            "non_null", "nulls", "nulls_pct",
            "zeros", "zeros_pct",
            "distinct",
            "min", "p10", "median", "mean", "p90", "max", "std",
        ]
        show_cols = [c for c in show_cols if c in subset.columns]

        edited = st.data_editor(
            subset[show_cols],
            hide_index=True,
            use_container_width=True,
            num_rows="fixed",
            column_config={
                "Use": st.column_config.CheckboxColumn(required=True),
                "Dist": st.column_config.BarChartColumn(
                    "Distribution",
                    help="Numeric: histogram · Categorical: top-k share · Datetime: monthly buckets",
                    y_min=0.0, y_max=1.0,
                ),
                "non_null":  st.column_config.NumberColumn("Non-Null", format="%,.0f"),
                "nulls":     st.column_config.NumberColumn("Nulls", format="%,.0f"),
                "nulls_pct": st.column_config.NumberColumn("Nulls %", format="%.1f%%"),
                "zeros":     st.column_config.NumberColumn("Zeros", format="%,.0f"),
                "zeros_pct": st.column_config.NumberColumn("Zeros %", format="%.1f%%"),
                "distinct":  st.column_config.NumberColumn("Distinct", format="%,.0f"),
                "min":       min_cfg,
                "p10":       st.column_config.NumberColumn("P10", format="%,.2f"),
                "median":    st.column_config.NumberColumn("Median", format="%,.2f"),
                "mean":      st.column_config.NumberColumn("Mean",   format="%,.2f"),
                "p90":       st.column_config.NumberColumn("P90", format="%,.2f"),
                "max":       max_cfg,
                "std":       st.column_config.NumberColumn("Std", format="%,.2f"),
            },
            key=f"dq_editor_{key_suffix}",
        )
        for _, r in edited.iterrows():
            use_overrides[str(r["Column"])] = bool(r["Use"])

    # Render all categories
    for title, cols in categories:
        _render_cat_table(title, cols, key_suffix=title.replace(" ", "_").lower())

    # ---- Aggregate final selection across all tables ----
    final_use = {row["Column"]: bool(row["Use"]) for _, row in prof_all.iterrows()}
    final_use.update(use_overrides)  # apply user edits

    selected_cols = [c for c, u in final_use.items() if u]
    st.session_state["selected_profile_columns"] = selected_cols

    # ---- Export buttons (combined selection) ----
    st.markdown("---")
    cL, cR = st.columns([1.2, 1])
    with cL:
        sel_prof = prof_all[prof_all["Column"].isin(selected_cols)].copy()
        # Remove the spark data from export
        csv = sel_prof.drop(columns=["Dist"], errors="ignore").to_csv(index=False).encode("utf-8")
        st.download_button(
            "Export profile (CSV — selected)",
            data=csv,
            file_name="data_profile_selected.csv",
            mime="text/csv",
        )
    with cR:
        cols_csv = pd.DataFrame({"column": selected_cols}).to_csv(index=False).encode("utf-8")
        st.download_button(
            "Export selected column names (CSV)",
            data=cols_csv,
            file_name="selected_columns.csv",
            mime="text/csv",
        )