"""
Prepare Training Data Page

This page guides users through preparing training data in 4 steps:
1. Select Data
2. Ensure good data quality
3. Prepare paid media spends & media response
4. Select strongest drivers and reduce noise
"""

import io
import json
import os
import tempfile
import warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import streamlit as st
from app_shared import (
    GCS_BUCKET,
    build_meta_views,
    build_plat_map_df,
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
    render_sidebar,
    require_login_and_domain,
    resample_numeric,
    resolve_meta_blob_from_selection,
    total_with_prev,
    upload_to_gcs,
    validate_against_metadata,
)
from google.cloud import storage
from scipy import stats
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, r2_score

# Authentication
require_login_and_domain()

st.title("Prepare Training Data")

# Session state defaults
st.session_state.setdefault("country", "de")
st.session_state.setdefault("picked_data_ts", "Latest")
st.session_state.setdefault("picked_meta_ts", "Latest")
st.session_state.setdefault("selected_columns_for_training", [])
st.session_state.setdefault("selected_paid_spends", [])
st.session_state.setdefault("selected_goal", None)

# =============================
# Step 1: Select Data
# =============================
with st.expander("Step 1) Select Data", expanded=False):
    st.markdown("### Select country and data versions to analyze")
    
    # Check if we have preselected values from Map Your Data
    if "country" in st.session_state and st.session_state.get("country"):
        st.info(f"Using country from Map Your Data: **{st.session_state['country'].upper()}**")
    
    c1, c2, c3, c4 = st.columns([1.2, 1, 1, 0.6])

    country = (
        c1.text_input("Country", value=st.session_state["country"])
        .strip()
        .lower()
    )
    if country:
        st.session_state["country"] = country

    refresh_clicked = c4.button("↻ Refresh Lists", key="refresh_step1")
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

    load_clicked = st.button("Select & Load", type="primary", key="load_step1")

    if load_clicked:
        try:
            # Resolve DATA path
            from app_shared import data_blob, data_latest_blob
            
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
                    use_container_width=True,
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


# Sidebar for timeframe selection
GOAL, sel_countries, TIMEFRAME_LABEL, RANGE, agg_label, FREQ = render_sidebar(
    meta, df, nice_title, goal_cols
)

# Country filter
if sel_countries and "COUNTRY" in df.columns:
    df = df[df["COUNTRY"].astype(str).isin(sel_countries)].copy()

# Target, spend, platforms
target = (
    GOAL
    if (GOAL and GOAL in df.columns)
    else (goal_cols[0] if goal_cols else None)
)

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

# Timeframe & resample
df_r = filter_range(df.copy(), DATE_COL, RANGE)
df_prev = previous_window(df, df_r, DATE_COL, RANGE)


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
    return dict(
        non_null=nn,
        nulls=na,
        nulls_pct=na / n if n else np.nan,
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
    sums = (
        df_window[vm["col"].tolist()]
        .melt(value_name="spend")
        .dropna(subset=["spend"])
        .groupby(vm.reset_index(drop=True)["platform"])["spend"]
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
    mapped_cols = set(data_types_map.keys()) | set(channels_map.keys())
    
    # Other columns are those in the dataframe but not in metadata
    known_set = (
        set(paid_spend)
        | set(paid_vars)
        | set(organic_vars)
        | set(context_vars)
        | set(factor_vars)
        | {DATE_COL}
        | {"COUNTRY"}
    )
    other_cols = [c for c in prof_df.columns if c not in mapped_cols]

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
            dict(Use=True, Column=col, Type=col_type, Dist=dist_vals, **stats)
        )
    prof_all = pd.DataFrame(rows)

    # Automated Cleaning section (expanded by default)
    with st.expander("Automated Cleaning", expanded=True):
        # Preselect first 3 checkboxes
        drop_all_null = st.checkbox("Drop all-null columns", value=True)
        drop_all_zero = st.checkbox(
            "Drop all-zero (numeric) columns", value=True
        )
        drop_constant = st.checkbox(
            "Drop constant (distinct == 1)", value=True
        )
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

    if reset_clean:
        st.session_state["dq_dropped_cols"] = set()
        st.session_state["dq_clean_note"] = ""
        st.session_state["dq_last_dropped"] = []
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
                keep_one = by_p["std"].astype(float).idxmax()
                to_drop.discard(keep_one)
                warnings_list.append(
                    f"{p}: kept '{keep_one}' to retain at least one var for active spend."
                )

        st.session_state["dq_dropped_cols"] |= set(to_drop)
        st.session_state["dq_last_dropped"] = sorted(to_drop)
        note = f"Dropped {len(to_drop)} column(s)."
        if warnings_list:
            note += " Guards applied — " + "; ".join(warnings_list)
        st.session_state["dq_clean_note"] = note

    if st.session_state["dq_clean_note"]:
        st.info(st.session_state["dq_clean_note"])
        if st.session_state["dq_last_dropped"]:
            with st.expander("Dropped columns (last Apply)"):
                st.write(", ".join(st.session_state["dq_last_dropped"]))

    # Apply dropped flags to 'Use' default
    dropped = st.session_state["dq_dropped_cols"]
    prof_all["Use"] = ~prof_all["Column"].isin(dropped)

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
        if title == "Other" and len(subset):
            st.caption(
                "Unmapped columns (in data but not in metadata): "
                + ", ".join(sorted(subset["Column"].astype(str).tolist()))
            )
        if subset.empty:
            st.info("No columns in this category.")
            return

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
            use_container_width=True,
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
                    "Non-Null", format="%,.0f"
                ),
                "nulls": st.column_config.NumberColumn("Nulls", format="%,.0f"),
                "nulls_pct": st.column_config.NumberColumn(
                    "Nulls %", format="%.1f%%"
                ),
                "zeros": st.column_config.NumberColumn("Zeros", format="%,.0f"),
                "zeros_pct": st.column_config.NumberColumn(
                    "Zeros %", format="%.1f%%"
                ),
                "distinct": st.column_config.NumberColumn(
                    "Distinct", format="%,.0f"
                ),
                "MinDisp": st.column_config.TextColumn("Min"),
                "p10": st.column_config.NumberColumn("P10", format="%,.2f"),
                "median": st.column_config.NumberColumn(
                    "Median", format="%,.2f"
                ),
                "mean": st.column_config.NumberColumn("Mean", format="%,.2f"),
                "p90": st.column_config.NumberColumn("P90", format="%,.2f"),
                "MaxDisp": st.column_config.TextColumn("Max"),
                "std": st.column_config.NumberColumn("Std", format="%,.2f"),
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

    # Aggregate final selection across all tables
    final_use = {
        row["Column"]: bool(row["Use"]) for _, row in prof_all.iterrows()
    }
    final_use.update(use_overrides)

    selected_cols = [c for c, u in final_use.items() if u]
    st.session_state["selected_columns_for_training"] = selected_cols

    # Export Selected Columns button
    st.markdown("---")
    st.markdown("### Export Selected Columns")
    
    if st.button("Export Selected Columns", type="primary", key="export_selected_cols"):
        try:
            # Create CSV of selected columns
            selected_data = prof_df[selected_cols].copy()
            
            # Save to GCS
            country = st.session_state.get("country", "de")
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            
            # Create temporary file
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".csv", delete=False
            ) as tmp:
                tmp_path = tmp.name
                selected_data.to_csv(tmp_path, index=False)
            
            try:
                # Upload to GCS
                gcs_path = f"training_data/{country}/{timestamp}/selected_columns.csv"
                upload_to_gcs(GCS_BUCKET, tmp_path, gcs_path)
                
                st.success(f"✅ Exported selected columns to gs://{GCS_BUCKET}/{gcs_path}")
                st.session_state["last_exported_columns_path"] = gcs_path
            finally:
                # Clean up temp file
                import os
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
        except Exception as e:
            st.error(f"Failed to export selected columns: {e}")


# =============================
# Step 3: Prepare paid media spends & media response
# =============================
with st.expander("Step 3) Prepare paid media spends & media response", expanded=False):
    st.markdown("### 3.1 What output do you want to predict?")
    
    # Get goals from metadata
    goals_list = meta.get("goals", []) or []
    if not goals_list:
        st.warning("No goals defined in metadata. Please configure goals in Map Your Data.")
    else:
        goal_vars = [g.get("var") for g in goals_list if g.get("var")]
        selected_goal = st.selectbox(
            "Select goal to predict",
            options=goal_vars,
            index=0 if goal_vars else None,
            key="selected_goal_dropdown"
        )
        st.session_state["selected_goal"] = selected_goal
    
    # 3.2 Select Paid Media Spends to optimize
    st.markdown("---")
    st.markdown("### 3.2 Select Paid Media Spends to optimize:")
    
    selected_cols_step2 = st.session_state.get("selected_columns_for_training", [])
    
    # Filter paid media spends from selected columns
    available_paid_spends = [
        c for c in paid_spend_cols if c in selected_cols_step2
    ]
    
    if not available_paid_spends:
        st.info("No paid media spend columns available. Please select columns in Step 2.")
    elif not st.session_state.get("selected_goal"):
        st.info("Please select a goal in section 3.1 above.")
    else:
        selected_goal = st.session_state["selected_goal"]
        
        # Calculate metrics for each paid spend column
        metrics_data = []
        for spend_col in available_paid_spends:
            if spend_col in df_r.columns and selected_goal in df_r.columns:
                # Prepare data for correlation
                temp_df = df_r[[spend_col, selected_goal]].dropna()
                
                if len(temp_df) > 1:
                    X = temp_df[[spend_col]].values
                    y = temp_df[selected_goal].values
                    
                    # Calculate R2
                    model = LinearRegression()
                    model.fit(X, y)
                    y_pred = model.predict(X)
                    r2 = r2_score(y, y_pred)
                    
                    # Calculate NMAE (Normalized Mean Absolute Error)
                    mae = mean_absolute_error(y, y_pred)
                    y_range = y.max() - y.min() if y.max() != y.min() else 1.0
                    nmae = mae / y_range if y_range > 0 else np.nan
                    
                    # Calculate Spearman's rho
                    spearman_rho, _ = stats.spearmanr(
                        temp_df[spend_col], temp_df[selected_goal]
                    )
                else:
                    r2 = np.nan
                    nmae = np.nan
                    spearman_rho = np.nan
                
                metrics_data.append({
                    "Select": False,
                    "Paid Media Spend": spend_col,
                    "R²": r2,
                    "NMAE": nmae,
                    "Spearman's ρ": spearman_rho,
                })
        
        if metrics_data:
            metrics_df = pd.DataFrame(metrics_data)
            
            # Display editable table
            edited_metrics = st.data_editor(
                metrics_df,
                hide_index=True,
                use_container_width=True,
                num_rows="fixed",
                column_config={
                    "Select": st.column_config.CheckboxColumn(required=True),
                    "Paid Media Spend": st.column_config.TextColumn(
                        "Paid Media Spend", disabled=True
                    ),
                    "R²": st.column_config.NumberColumn("R²", format="%.4f"),
                    "NMAE": st.column_config.NumberColumn("NMAE", format="%.4f"),
                    "Spearman's ρ": st.column_config.NumberColumn(
                        "Spearman's ρ", format="%.4f"
                    ),
                },
                key="paid_spends_metrics_table"
            )
            
            # Store selected paid spends
            selected_paid_spends = edited_metrics[edited_metrics["Select"]]["Paid Media Spend"].tolist()
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
            
            if not corresponding_vars:
                st.info(f"No media response variables mapped for {spend_col}")
                continue
            
            # Filter vars that are available in the data
            available_vars = [
                v for v in corresponding_vars if v in df_r.columns
            ]
            
            if not available_vars:
                st.info(f"Mapped variables not found in data for {spend_col}")
                continue
            
            # Calculate metrics for each media var
            var_metrics_data = []
            for var_col in available_vars:
                if var_col in df_r.columns and spend_col in df_r.columns:
                    temp_df = df_r[[spend_col, var_col]].dropna()
                    
                    if len(temp_df) > 1:
                        X = temp_df[[spend_col]].values
                        y = temp_df[var_col].values
                        
                        # Calculate R2
                        model = LinearRegression()
                        model.fit(X, y)
                        y_pred = model.predict(X)
                        r2 = r2_score(y, y_pred)
                        
                        # Calculate NMAE
                        mae = mean_absolute_error(y, y_pred)
                        y_range = y.max() - y.min() if y.max() != y.min() else 1.0
                        nmae = mae / y_range if y_range > 0 else np.nan
                        
                        # Calculate Spearman's rho
                        spearman_rho, _ = stats.spearmanr(
                            temp_df[spend_col], temp_df[var_col]
                        )
                    else:
                        r2 = np.nan
                        nmae = np.nan
                        spearman_rho = np.nan
                    
                    var_metrics_data.append({
                        "Media Response Variable": var_col,
                        "R²": r2,
                        "NMAE": nmae,
                        "Spearman's ρ": spearman_rho,
                    })
            
            if var_metrics_data:
                var_metrics_df = pd.DataFrame(var_metrics_data)
                
                # Dropdown to select the media response variable
                st.selectbox(
                    f"Select media response variable for {spend_col}",
                    options=var_metrics_df["Media Response Variable"].tolist(),
                    key=f"media_var_select_{spend_col}"
                )
                
                # Display metrics table
                st.dataframe(
                    var_metrics_df,
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Media Response Variable": st.column_config.TextColumn(
                            "Media Response Variable"
                        ),
                        "R²": st.column_config.NumberColumn("R²", format="%.4f"),
                        "NMAE": st.column_config.NumberColumn("NMAE", format="%.4f"),
                        "Spearman's ρ": st.column_config.NumberColumn(
                            "Spearman's ρ", format="%.4f"
                        ),
                    },
                )


# =============================
# Step 4: Select strongest drivers and reduce noise
# =============================
with st.expander("Step 4) Select strongest drivers and reduce noise", expanded=False):
    st.markdown("### Coming soon")
    st.info("This step will help you identify and select the strongest drivers while reducing noise in your model.")
