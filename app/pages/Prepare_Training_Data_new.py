import os
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score, mean_absolute_error
from scipy import stats
import streamlit as st

from app_shared import (
    # GCS & versions
    list_data_versions,
    list_meta_versions,
    download_parquet_from_gcs_cached,
    download_json_from_gcs_cached,
    data_blob,
    data_latest_blob,
    # meta & utilities
    build_meta_views,
    build_plat_map_df,
    validate_against_metadata,
    parse_date,
    pretty,
    # auth / routing
    resolve_meta_blob_from_selection,
    require_login_and_domain,
)

# =====================================
# App setup
# =====================================
st.set_page_config(page_title="Prepare Training Data for Experimentation", layout="wide")
require_login_and_domain()
st.title("Prepare Training Data for Experimentation")

GCS_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")

# =====================================
# Session defaults
# =====================================
st.session_state.setdefault("country", "de")
st.session_state.setdefault("picked_data_ts", "Latest")
st.session_state.setdefault("picked_meta_ts", "Latest")
st.session_state.setdefault("dq_dropped_cols", set())
st.session_state.setdefault("dq_clean_note", "")
st.session_state.setdefault("dq_last_dropped", [])
st.session_state.setdefault("selected_profile_columns", [])
st.session_state.setdefault("paid_mapping_overrides", {})  # spend -> var
st.session_state.setdefault("selected_goal", None)
st.session_state.setdefault("final_export_columns", [])
st.session_state.setdefault("final_paid_mapping", {})

# =====================================
# Tabs
# =====================================
tab_load, tab_quality, tab_mapping = st.tabs(
    ["Select Data To Analyze", "Data Quality", "Paid Media Mapping"]
)

# =====================================
# TAB 0 — LOAD DATA & METADATA
# =====================================
with tab_load:
    st.markdown("### Select country and data versions to analyze")
    c1, c2, c3, c4 = st.columns([1.2, 1, 1, 0.6])

    country = c1.text_input("Country", value=st.session_state["country"]).strip().lower()
    if country:
        st.session_state["country"] = country

    refresh_clicked = c4.button("↻ Refresh Lists")
    refresh_key = str(pd.Timestamp.utcnow().value) if refresh_clicked else ""

    data_versions = list_data_versions(GCS_BUCKET, country, refresh_key) if country else ["Latest"]
    meta_versions = list_meta_versions(GCS_BUCKET, country, refresh_key) if country else ["Latest"]

    data_ts = c2.selectbox("Data version", options=data_versions, index=0, key="picked_data_ts")
    meta_ts = c3.selectbox("Metadata version", options=meta_versions, index=0, key="picked_meta_ts")

    load_clicked = st.button("Select & Load", type="primary")

    if load_clicked:
        try:
            # Resolve GCS paths
            db = data_latest_blob(country) if data_ts == "Latest" else data_blob(country, str(data_ts))
            mb = resolve_meta_blob_from_selection(GCS_BUCKET, country, str(meta_ts))

            # Download
            df = download_parquet_from_gcs_cached(GCS_BUCKET, db)
            meta = download_json_from_gcs_cached(GCS_BUCKET, mb)

            # Parse dates per metadata
            df, date_col = parse_date(df, meta)

            # Persist in session
            st.session_state["df"] = df
            st.session_state["meta"] = meta
            st.session_state["date_col"] = date_col
            st.session_state["channels_map"] = meta.get("channels", {}) or {}

            # Reset downstream selections on reload
            st.session_state["dq_dropped_cols"] = set()
            st.session_state["dq_clean_note"] = ""
            st.session_state["dq_last_dropped"] = []
            st.session_state["selected_profile_columns"] = []
            st.session_state["paid_mapping_overrides"] = {}
            st.session_state["final_export_columns"] = []
            st.session_state["final_paid_mapping"] = {}
            st.session_state["selected_goal"] = None

            # Validate metadata vs data
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
                st.dataframe(report["type_mismatches"], use_container_width=True, hide_index=True)
            else:
                st.caption("No type mismatches detected (coarse check).")
        except Exception as e:
            st.error(f"Load failed: {e}")

# =====================================
# Common state after load
# =====================================
df = st.session_state.get("df", pd.DataFrame())
meta = st.session_state.get("meta", {}) or {}
DATE_COL = st.session_state.get("date_col", "DATE")
CHANNELS_MAP = st.session_state.get("channels_map", {}) or {}

if df.empty or not meta:
    st.stop()

# =====================================
# Meta helpers & categories
# =====================================
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

# Present columns only
paid_spend_cols = [c for c in (mapping.get("paid_media_spends", []) or []) if c in df.columns]
paid_var_cols   = [c for c in (mapping.get("paid_media_vars",   []) or []) if c in df.columns]
organic_cols    = [c for c in (mapping.get("organic_vars",      []) or []) if c in df.columns]
context_cols    = [c for c in (mapping.get("context_vars",      []) or []) if c in df.columns]
factor_cols     = [c for c in (mapping.get("factor_vars",       []) or []) if c in df.columns]
# Factor > Context precedence
context_cols = [c for c in context_cols if c not in set(factor_cols)]
goals_list = [g for g in (goal_cols or []) if g in df.columns]

# Protected columns: DATE, COUNTRY, goals (hidden from editor; always exported)
def _protect_columns_set(date_col: str, goal_cols_list: list[str]) -> set[str]:
    prot = {str(date_col), "COUNTRY"}
    prot |= set(goal_cols_list or [])
    return set(p.upper() for p in prot)
_PROTECTED_UP = _protect_columns_set(DATE_COL, goals_list)

# Platform mapping for spend columns (used for candidate grouping)
plat_map_df, platforms, PLATFORM_COLORS = build_plat_map_df(
    present_spend=paid_spend_cols,
    df=df,
    meta=meta,
    m=m,
    COL="column_name",
    PLAT="platform",
    CHANNELS_MAP=CHANNELS_MAP,
)

def _var_platform(col: str, platforms_list: list[str]) -> str | None:
    cu = str(col).upper()
    for p in platforms_list:
        if p.upper() in cu:
            return p
    return None

# =====================================
# Profiling helpers
# =====================================
def _num_stats(s: pd.Series) -> dict:
    q = pd.to_numeric(s, errors="coerce")
    n = int(len(q))
    nn = int(q.notna().sum())
    na = n - nn
    if nn == 0:
        return dict(
            non_null=0, nulls=na, nulls_pct=(na / n if n else np.nan),
            zeros=0, zeros_pct=np.nan, distinct=0,
            min=np.nan, p10=np.nan, median=np.nan, mean=np.nan,
            p90=np.nan, max=np.nan, std=np.nan
        )
    z = int(q.eq(0).sum())
    s2 = q.dropna()
    return dict(
        non_null=nn,
        nulls=na,
        nulls_pct=(na / n if n else np.nan),      # % of all rows
        zeros=z,
        zeros_pct=(z / nn if nn else np.nan),     # % of non-null rows
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
    n = int(len(s))
    nn = int(s.notna().sum())
    na = n - nn
    s2 = s.dropna()
    return dict(
        non_null=nn,
        nulls=na,
        nulls_pct=(na / n if n else np.nan),
        zeros=np.nan,
        zeros_pct=np.nan,
        distinct=int(s2.nunique(dropna=True)) if nn else 0,
        min=np.nan, p10=np.nan, median=np.nan, mean=np.nan,
        p90=np.nan, max=np.nan, std=np.nan,
    )

def _distribution_values(s: pd.Series, *, numeric_bins: int = 10, cat_topk: int = 5) -> list[float]:
    try:
        if pd.api.types.is_numeric_dtype(s):
            q = pd.to_numeric(s, errors="coerce").dropna()
            if q.empty: return []
            hist, _ = np.histogram(q, bins=numeric_bins)
            tot = hist.sum()
            return (hist / tot).tolist() if tot else []
        if pd.api.types.is_datetime64_any_dtype(s):
            q = pd.to_datetime(s, errors="coerce").dropna()
            if q.empty: return []
            vc = q.dt.to_period("M").value_counts().sort_index()
            tot = vc.sum()
            return (vc / tot).tolist() if tot else []
        q = s.dropna().astype("object")
        if q.empty: return []
        vc = q.value_counts().head(cat_topk)
        tot = vc.sum()
        return (vc / tot).tolist() if tot else []
    except Exception:
        return []

def _cv(mean_val: float, std_val: float) -> float:
    if pd.isna(mean_val) or pd.isna(std_val): return np.nan
    if abs(mean_val) < 1e-12: return np.inf
    return float(abs(std_val) / abs(mean_val))

# =====================================
# TAB 1 — DATA QUALITY (GLOBAL, FULL WINDOW)
# =====================================
with tab_quality:
    st.caption(":information_source: Using the full modeling window (no date filtering).")
    prof_df = df.copy()
    if prof_df.empty:
        st.info("No data to profile.")
        st.stop()

    # Category lists (for grouping only)
    categories = [
        ("Paid Spend",  paid_spend_cols),
        ("Paid Vars",   paid_var_cols),
        ("Organic",     organic_cols),
        ("Context",     context_cols),
        ("Factor",      factor_cols),
        ("Goals",       goals_list),
    ]
    known_set = set().union(*[set(c) for _, c in categories])
    other_cols = [c for c in prof_df.columns if c not in known_set]
    categories.append(("Other", other_cols))

    # Build full profile
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
        rows.append(dict(Use=True, Column=col, Type=col_type, Dist=_distribution_values(s), **stats))
    prof_all = pd.DataFrame(rows)

    # ---- Cleaning controls (global; applies to ALL categories) ----
    with st.expander("Automated cleaning (optional)", expanded=False):
        drop_all_null  = st.checkbox("Drop all-null columns", value=False)
        drop_all_zero  = st.checkbox("Drop all-zero (numeric) columns", value=False)
        drop_constant  = st.checkbox("Drop constant (distinct == 1)", value=False)
        drop_low_var   = st.checkbox("Drop low variance (CV < threshold)", value=False)
        cv_thr = st.slider("Low-variance threshold (CV % of mean)", 0.1, 100.0, 3.0, 0.1)
        cA, cB = st.columns([1, 1])
        apply_clean = cA.button("Apply cleaning")
        reset_clean = cB.button("Reset cleaning")

    if reset_clean:
        st.session_state["dq_dropped_cols"] = set()
        st.session_state["dq_clean_note"] = ""
        st.session_state["dq_last_dropped"] = []
        st.rerun()

    if apply_clean and (drop_all_null or drop_all_zero or drop_constant or drop_low_var):
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
            stds  = by_col["std"]
            cv_series = pd.Series([_cv(means.get(i), stds.get(i)) for i in by_col.index], index=by_col.index)
            mask_low_cv = (cv_series * 100.0) < cv_thr
            to_drop |= set(cv_series[mask_low_cv.fillna(False)].index)

        # Never drop protected (DATE, COUNTRY, goals)
        to_drop = {c for c in to_drop if str(c).upper() not in _PROTECTED_UP}

        # (Important change) Allow dropping PAID SPEND / PAID VARS if unusable
        # i.e., DO NOT pull them out of to_drop; they can be removed here.
        st.session_state["dq_dropped_cols"] |= set(to_drop)
        st.session_state["dq_last_dropped"] = sorted(to_drop)
        st.session_state["dq_clean_note"] = f"Dropped {len(to_drop)} column(s)."

    if st.session_state["dq_clean_note"]:
        st.info(st.session_state["dq_clean_note"])
        if st.session_state["dq_last_dropped"]:
            with st.expander("Dropped columns (last Apply)"):
                st.write(", ".join(st.session_state["dq_last_dropped"]))

    # Hide protected from the editor view
    prof_all["Use"] = ~prof_all["Column"].isin(st.session_state["dq_dropped_cols"])
    use_overrides: dict[str, bool] = {}

    def _fmt_num(val) -> str:
        return "–" if pd.isna(val) else f"{float(val):.2f}"

    def _fmt_dt_from_seconds(val) -> str:
        if pd.isna(val): return "–"
        try:
            return pd.to_datetime(float(val), unit="s").strftime("%Y-%m-%d")
        except Exception:
            return "–"

    def _render_cat_table(title: str, cols: list[str], key_suffix: str):
        subset = prof_all[prof_all["Column"].isin(cols)].copy()
        # Remove protected rows from the view
        subset = subset[~subset["Column"].astype(str).str.upper().isin(_PROTECTED_UP)]
        st.markdown(f"### {title} ({len(subset)})")
        if title == "Goals":
            st.caption("Goal columns are hidden here but always kept and exported.")
        if subset.empty:
            st.info("No columns in this category.")
            return

        # Display-only min/max strings
        is_dt = subset["Type"].eq("datetime64")
        subset["MinDisp"] = np.where(is_dt, subset["min"].map(_fmt_dt_from_seconds), subset["min"].map(_fmt_num))
        subset["MaxDisp"] = np.where(is_dt, subset["max"].map(_fmt_dt_from_seconds), subset["max"].map(_fmt_num))

        # Percent display (0–100)
        subset["nulls_pct_disp"] = subset["nulls_pct"] * 100.0
        subset["zeros_pct_disp"] = subset["zeros_pct"] * 100.0

        show_cols = [
            "Use", "Column", "Type", "Dist",
            "non_null", "nulls", "nulls_pct_disp",
            "zeros", "zeros_pct_disp",
            "distinct",
            "MinDisp", "p10", "median", "mean", "p90", "MaxDisp", "std",
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
                "non_null":        st.column_config.NumberColumn("Non-Null", format="%.0f"),
                "nulls":           st.column_config.NumberColumn("Nulls", format="%.0f"),
                "nulls_pct_disp":  st.column_config.NumberColumn("Nulls %", format="%.1f%%"),
                "zeros":           st.column_config.NumberColumn("Zeros", format="%.0f"),
                "zeros_pct_disp":  st.column_config.NumberColumn("Zeros %", format="%.1f%%"),
                "distinct":        st.column_config.NumberColumn("Distinct", format="%.0f"),
                "MinDisp":         st.column_config.TextColumn("Min"),
                "p10":             st.column_config.NumberColumn("P10", format="%.2f"),
                "median":          st.column_config.NumberColumn("Median", format="%.2f"),
                "mean":            st.column_config.NumberColumn("Mean", format="%.2f"),
                "p90":             st.column_config.NumberColumn("P90", format="%.2f"),
                "MaxDisp":         st.column_config.TextColumn("Max"),
                "std":             st.column_config.NumberColumn("Std", format="%.2f"),
            },
            key=f"dq_editor_{key_suffix}",
        )
        for _, r in edited.iterrows():
            use_overrides[str(r["Column"])] = bool(r["Use"])

    for title, cols in categories:
        _render_cat_table(title, cols, key_suffix=title.replace(" ", "_").lower())

    # Aggregate Use selections
    final_use = {row["Column"]: bool(row["Use"]) for _, row in prof_all.iterrows()}
    final_use.update(use_overrides)

    # Enforce protected to True
    for c in list(final_use.keys()):
        if str(c).upper() in _PROTECTED_UP:
            final_use[c] = True

    selected_cols = [c for c, u in final_use.items() if u]
    st.session_state["selected_profile_columns"] = selected_cols

    st.markdown("---")
    st.caption("Click the next tab when ready.")
    st.button("Continue to Paid Media Mapping ➜", type="primary")

# =====================================
# TAB 2 — PAID MEDIA MAPPING (vs GOAL)
# =====================================
with tab_mapping:
    st.caption(":information_source: Uses the cleaned set from **Data Quality** (full window).")
    if not st.session_state.get("selected_profile_columns"):
        st.warning("Finish the Data Quality step first, then click 'Continue'.")
        st.stop()

    # Goal selector (required for scoring)
    if not goals_list:
        st.error("No goal columns found in metadata. Add a goal in your metadata to proceed.")
        st.stop()

    default_goal = st.session_state.get("selected_goal") or goals_list[0]
    selected_goal = st.selectbox("Goal variable", options=goals_list, index=goals_list.index(default_goal) if default_goal in goals_list else 0, key="selected_goal")

    selected_cols = set(st.session_state["selected_profile_columns"])
    # Keep only selected columns (plus protected) for metrics
    df_sel = df[[c for c in df.columns if (c in selected_cols) or (str(c).upper() in _PROTECTED_UP)]].copy()

    # Ensure goal is present
    if selected_goal not in df_sel.columns:
        df_sel[selected_goal] = df[selected_goal].copy() if selected_goal in df.columns else np.nan

    # Candidate discovery per spend (include spend/cost as fallback candidate)
    paid_map_meta = (meta.get("paid_media_mapping") or {})
    spend_to_candidates: dict[str, list[str]] = {}

    # Quick platform guess per spend
    spend_platforms = {}
    if not plat_map_df.empty:
        tmp = plat_map_df.dropna(subset=["col", "platform"]).set_index("col")["platform"].astype(str)
        for c in paid_spend_cols:
            spend_platforms[c] = tmp.get(c, _var_platform(c, platforms))
    else:
        for c in paid_spend_cols:
            spend_platforms[c] = _var_platform(c, platforms)

    for spend in paid_spend_cols:
        if spend not in df_sel.columns:
            continue
        # start with metadata mapping if provided
        meta_cands = [v for v in (paid_map_meta.get(spend) or []) if v in paid_var_cols and v in df_sel.columns]
        if meta_cands:
            cands = list(dict.fromkeys(meta_cands))
        else:
            plat = spend_platforms.get(spend)
            if plat:
                cands = [v for v in paid_var_cols if (plat.upper() in v.upper()) and (v in df_sel.columns)]
            else:
                cands = [v for v in paid_var_cols if v in df_sel.columns]

        # add spend itself as fallback candidate if present
        if spend in df_sel.columns and spend not in cands:
            cands.append(spend)

        spend_to_candidates[spend] = cands

    # Metrics vs GOAL
    def _metrics_vs_goal(x: pd.Series, y: pd.Series) -> dict:
        """
        x = candidate media variable, y = goal
        """
        X = pd.to_numeric(x, errors="coerce")
        Y = pd.to_numeric(y, errors="coerce")
        mask_pair = X.notna() & Y.notna()
        if mask_pair.sum() < 5:
            return dict(
                R2=np.nan,
                MAE_rel=np.nan,
                rho=np.nan,
                p=np.nan,
                n_xpos_pair=int((mask_pair & (X > 0)).sum()),
                AvgX=float(np.nan),
                RelVarX=np.nan,
            )
        Xp = X[mask_pair]
        Yp = Y[mask_pair]

        # R² from linear regression Y ~ X
        try:
            lr = LinearRegression()
            lr.fit(Xp.values.reshape(-1, 1), Yp.values)
            yhat = lr.predict(Xp.values.reshape(-1, 1))
            R2 = float(r2_score(Yp.values, yhat))
            MAE = float(mean_absolute_error(Yp.values, yhat))
        except Exception:
            R2, MAE = np.nan, np.nan

        # MAE (rel): relative to mean(|Y|) to be scale-free
        denom = float(np.nanmean(np.abs(Yp.values))) if np.isfinite(np.nanmean(np.abs(Yp.values))) else np.nan
        MAE_rel = (MAE / denom) if (denom and denom > 0) else np.nan

        # Spearman ρ (monotonicity)
        try:
            rho, p = stats.spearmanr(Xp.values, Yp.values)
            rho = float(rho) if rho is not None else np.nan
            p = float(p) if p is not None else np.nan
        except Exception:
            rho, p = np.nan, np.nan

        # n(X>0, pair) and Avg(X) on paired
        n_xpos_pair = int((Xp > 0).sum())
        AvgX = float(np.nanmean(Xp.values)) if Xp.size else np.nan

        # RelVar(X): coefficient of variation of X on paired rows
        mx = float(np.nanmean(Xp.values)) if Xp.size else np.nan
        sx = float(np.nanstd(Xp.values, ddof=1)) if Xp.size > 1 else np.nan
        if mx is None or np.isnan(mx) or abs(mx) < 1e-12:
            RelVarX = np.inf
        else:
            RelVarX = abs(sx) / abs(mx) if (sx is not None and not np.isnan(sx)) else np.nan

        return dict(R2=R2, MAE_rel=MAE_rel, rho=rho, p=p, n_xpos_pair=n_xpos_pair, AvgX=AvgX, RelVarX=RelVarX)

    # Build per-spend tables + recommendations
    st.markdown("### Candidate metrics per paid spend (vs goal)")
    recommended = {}
    overrides = st.session_state.get("paid_mapping_overrides", {})

    for spend, cand_vars in spend_to_candidates.items():
        st.markdown(f"**{spend}**")
        if not cand_vars:
            st.write("No candidates.")
            st.markdown("---")
            recommended[spend] = None
            continue

        rows = []
        for var in cand_vars:
            met = _metrics_vs_goal(df_sel[var], df_sel[selected_goal])
            # screening: require at least some positive coverage and variation
            ok = True
            if met["n_xpos_pair"] < 3:  # too few positive observations
                ok = False
            if pd.notna(met["RelVarX"]) and met["RelVarX"] == 0:
                ok = False

            # simple score to rank (favor positive rho)
            r2 = met["R2"] if pd.notna(met["R2"]) else 0.0
            rho_pos = met["rho"] if (pd.notna(met["rho"]) and met["rho"] > 0) else 0.0
            inv_mae_rel = (1.0 / (met["MAE_rel"] + 1e-9)) if pd.notna(met["MAE_rel"]) else 0.0
            score = (0.5 * r2) + (0.3 * rho_pos) + (0.2 * inv_mae_rel)
            if not ok:
                score = -1.0

            rows.append(
                dict(
                    Variable=var,
                    **{
                        "MAE (rel)": met["MAE_rel"],
                        "Spearman ρ": met["rho"],
                        'n (X>0, pair)': met["n_xpos_pair"],
                        "Avg(X)": met["AvgX"],
                        "R²": met["R2"],
                        "RelVar(X)": met["RelVarX"],
                        "p": met["p"],      # keep p available though not in your preferred display columns
                        "_score": score,    # internal for sorting
                    },
                )
            )

        table = pd.DataFrame(rows).sort_values(by=["_score", "R²"], ascending=[False, False], na_position="last")
        auto_pick = table.iloc[0]["Variable"] if not table.empty else (cand_vars[0] if cand_vars else None)
        pick = overrides.get(spend, auto_pick)

        # Display with your column labels
        display_cols = ["Variable", "MAE (rel)", "Spearman ρ", 'n (X>0, pair)', "Avg(X)", "R²", "RelVar(X)"]
        if table.empty:
            st.write("No candidates after screening.")
        else:
            st.dataframe(
                table[display_cols].style.format(
                    {
                        "MAE (rel)": "{:.3f}",
                        "Spearman ρ": "{:.3f}",
                        "Avg(X)": "{:.3f}",
                        "R²": "{:.3f}",
                        "RelVar(X)": "{:.3f}",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

        # Override control
        if cand_vars:
            sel = st.selectbox(
                f"Select variable for {spend}",
                options=cand_vars,
                index=(cand_vars.index(pick) if pick in cand_vars else 0),
                key=f"map_sel_{spend}",
            )
            recommended[spend] = sel
        else:
            recommended[spend] = None

        st.markdown("---")

    # persist overrides
    st.session_state["paid_mapping_overrides"] = recommended

    # Build final export set:
    # protected + paid_spend + mapped paid_vars + organic/context/factor that survived Data Quality
    mapped_vars = [v for v in recommended.values() if v is not None]
    final_cols = set()

    # Always include protected
    final_cols |= set([c for c in df.columns if str(c).upper() in _PROTECTED_UP])
    # Paid spend + chosen vars
    final_cols |= set([c for c in paid_spend_cols if c in df_sel.columns])
    final_cols |= set([c for c in mapped_vars if c in df_sel.columns])
    # Organic / context / factor (only those that passed Data Quality)
    dq_drop = set(st.session_state.get("dq_dropped_cols", set()))
    final_cols |= {c for c in organic_cols if (c in df_sel.columns and c not in dq_drop)}
    final_cols |= {c for c in context_cols if (c in df_sel.columns and c not in dq_drop)}
    final_cols |= {c for c in factor_cols  if (c in df_sel.columns and c not in dq_drop)}
    # Goals (ensure included)
    final_cols |= set(goals_list)

    final_cols_sorted = sorted(final_cols)
    st.session_state["final_export_columns"] = final_cols_sorted
    st.session_state["final_paid_mapping"] = {k: v for k, v in recommended.items() if v is not None}

    st.success(f"Selected {len(final_cols_sorted)} columns for next steps (PCA/VIF later).")
    st.write(", ".join(final_cols_sorted))

    # Downloads
    col_csv = pd.DataFrame({"column": final_cols_sorted}).to_csv(index=False).encode("utf-8")
    map_df = (
        pd.DataFrame([{"spend": s, "var": v} for s, v in st.session_state["final_paid_mapping"].items()])
        if st.session_state["final_paid_mapping"] else pd.DataFrame(columns=["spend", "var"])
    )
    map_csv = map_df.to_csv(index=False).encode("utf-8")

    c1, c2 = st.columns([1, 1])
    with c1:
        st.download_button(
            "Download selected columns (CSV)",
            data=col_csv,
            file_name="qualified_columns.csv",
            mime="text/csv",
        )
    with c2:
        st.download_button(
            "Download spend→var mapping (CSV)",
            data=map_csv,
            file_name="paid_spend_var_mapping.csv",
            mime="text/csv",
        )