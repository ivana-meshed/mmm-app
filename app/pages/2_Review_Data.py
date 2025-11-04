# streamlit_app_overview.py (v2.23) — fixed top-of-file wiring
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
    page_title="Review Business- & Marketing Data", layout="wide"
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

# -----------------------------
# TABS
# -----------------------------
tab_load, tab_biz, tab_mkt, tab_profile = st.tabs(
    [
        "Select Data To Analyze",
        "Business Data",
        "Marketing Data",
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
# TAB 1 — BUSINESS OVERVIEW
# =============================
with tab_biz:
    # Small helper to guarantee a nice label even if metadata is incomplete
    st.markdown("## KPI Overview")

    has_prev = not df_prev.empty

    # Build a stable (nice -> raw col) mapping for goals so we can show friendly labels everywhere
    if goal_cols:
        goal_label_map = {nice_title(g): g for g in goal_cols}
        goal_labels_sorted = sorted(goal_label_map.keys(), key=lambda s: s.lower())
        target = GOAL if (GOAL and GOAL in df.columns) else (goal_cols[0] if goal_cols else None)
    else:
        goal_label_map = {}
        goal_labels_sorted = []
        target = None

    # ----- KPI — Outcomes (TOTALS only) -----
    st.markdown("### Outcomes (Goals)")
    if goal_cols:
        kpis = []
        for g in goal_cols:
            cur = df_r[g].sum() if (g in df_r.columns) else np.nan
            prev = (
                df_prev[g].sum()
                if (has_prev and g in df_prev.columns)
                else np.nan
            )
            delta_txt = None
            if pd.notna(prev):
                diff = cur - prev
                delta_txt = f"{'+' if diff >= 0 else ''}{fmt_num(diff)}"
            kpis.append(
                dict(
                    title=nice_title(g),
                    value=fmt_num(cur),
                    delta=delta_txt,
                    good_when="up",
                )
            )
        kpi_grid(kpis, per_row=5)
        st.markdown("---")

        # ----- KPI — Goal Efficiency -----
        st.markdown("### Goal Efficiency")
        kpis2 = []
        for g in goal_cols:
            cur_eff = safe_eff(df_r, g)
            prev_eff = safe_eff(df_prev, g) if has_prev else np.nan
            delta_txt = None
            if pd.notna(cur_eff) and pd.notna(prev_eff):
                diff = cur_eff - prev_eff
                delta_txt = f"{'+' if diff >= 0 else ''}{diff:.2f}"
            # ROAS only for GMV explicitly; otherwise <NiceGoal> / <Spend label>
            eff_title = "ROAS" if str(g).upper() == "GMV" else f"{nice_title(g)} / {spend_label}"
            kpis2.append(
                dict(
                    title=eff_title,
                    value=("–" if pd.isna(cur_eff) else f"{cur_eff:.2f}"),
                    delta=delta_txt,
                    good_when="up",
                )
            )
        kpi_grid(kpis2, per_row=5)
        st.markdown("---")

    # -----------------------------
    # Goal vs Spend (bar + line)
    # -----------------------------
    st.markdown("## Goal vs Spend")
    cA, cB = st.columns(2)

    with cA:
        fig1 = go.Figure()
        if target and target in res:
            fig1.add_bar(x=res["PERIOD_LABEL"], y=res[target], name=nice_title(target))
        fig1.add_trace(
            go.Scatter(
                x=res["PERIOD_LABEL"],
                y=res["_TOTAL_SPEND"],
                name=f"Total {spend_label}",
                yaxis="y2",
                mode="lines+markers",
                line=dict(color=RED),
            )
        )
        fig1.update_layout(
            title=f"{nice_title(target) if target else 'Goal'} vs Total {spend_label} — {TIMEFRAME_LABEL}, {agg_label}",
            xaxis=dict(title="Date", title_standoff=8),
            yaxis=dict(title=nice_title(target) if target else "Goal"),
            yaxis2=dict(title=spend_label, overlaying="y", side="right"),
            bargap=0.15,
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(b=60),
        )
        st.plotly_chart(fig1, use_container_width=True)

    with cB:
        eff_t = res.copy()
        label_eff = "ROAS" if (target and str(target).upper() == "GMV") else "Efficiency"
        if target and target in eff_t.columns and "_TOTAL_SPEND" in eff_t:
            eff_t["EFF"] = np.where(
                eff_t["_TOTAL_SPEND"] > 0,
                eff_t[target] / eff_t["_TOTAL_SPEND"],
                np.nan,
            )
        else:
            eff_t["EFF"] = np.nan
        fig2e = go.Figure()
        if target and target in eff_t:
            fig2e.add_bar(x=eff_t["PERIOD_LABEL"], y=eff_t[target], name=nice_title(target))
        fig2e.add_trace(
            go.Scatter(
                x=eff_t["PERIOD_LABEL"],
                y=eff_t["EFF"],
                name=label_eff,
                yaxis="y2",
                mode="lines+markers",
                line=dict(color=GREEN),
            )
        )
        fig2e.update_layout(
            title=f"{nice_title(target) if target else 'Goal'} & {label_eff} Over Time — {TIMEFRAME_LABEL}, {agg_label}",
            xaxis=dict(title="Date", title_standoff=8),
            yaxis=dict(title=nice_title(target) if target else "Goal"),
            yaxis2=dict(title=label_eff, overlaying="y", side="right"),
            bargap=0.15,
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(b=60),
        )
        st.plotly_chart(fig2e, use_container_width=True)

    st.markdown("---")

    # -----------------------------
    # Explore Any Metric Over Time
    # -----------------------------
    st.markdown("## Explore Any Metric Over Time")

    numeric_candidates = df_r.select_dtypes(include=[np.number]).columns.tolist()
    metrics = [c for c in numeric_candidates if c != "_TOTAL_SPEND"]

    if not metrics:
        st.info("No numeric columns available to plot.")
    else:
        from collections import Counter

        # Build label list using nice() and disambiguate duplicates by appending the raw col
        base_labels = [(nice_title(c), c) for c in metrics]
        counts = Counter(lbl for (lbl, _) in base_labels)
        labels = []
        for lbl, col in base_labels:
            final = lbl if counts[lbl] == 1 else f"{lbl} · {col}"
            labels.append((final, col))

        labels_sorted = sorted([l for (l, _) in labels], key=lambda s: s.lower())
        label_to_col = {l: c for (l, c) in labels}

        # default to current target if available
        default_label = (
            next((l for l, c in label_to_col.items() if c == target), None)
            or labels_sorted[0]
        )

        c_sel, c_spend = st.columns([2, 1])
        picked_label = c_sel.selectbox("Metric", labels_sorted, index=labels_sorted.index(default_label))
        picked_col = label_to_col[picked_label]

        # ensure selected metric is in res; if not, add via same resample rule
        if picked_col not in res.columns and picked_col in df_r.columns:
            add = (
                df_r.set_index(DATE_COL)[[picked_col]]
                .resample(RULE)
                .sum(min_count=1)
                .reset_index()
                .rename(columns={DATE_COL: "DATE_PERIOD"})
            )
            res_plot = res.merge(add, on="DATE_PERIOD", how="left")
            res_plot["PERIOD_LABEL"] = period_label(res_plot["DATE_PERIOD"], RULE)
        else:
            res_plot = res

        want_overlay = c_spend.checkbox(f"Overlay Total {spend_label}", value=True)
        can_overlay = "_TOTAL_SPEND" in res_plot.columns

        fig_custom = go.Figure()
        fig_custom.add_bar(x=res_plot["PERIOD_LABEL"], y=res_plot[picked_col], name=nice_title(picked_col))

        if want_overlay and can_overlay:
            fig_custom.add_trace(
                go.Scatter(
                    x=res_plot["PERIOD_LABEL"],
                    y=res_plot["_TOTAL_SPEND"],
                    name=f"Total {spend_label}",
                    yaxis="y2",
                    mode="lines+markers",
                    line=dict(color=RED),
                )
            )
            fig_custom.update_layout(
                yaxis=dict(title=nice_title(picked_col)),
                yaxis2=dict(title=spend_label, overlaying="y", side="right"),
            )
        else:
            fig_custom.update_layout(yaxis=dict(title=nice_title(picked_col)))

        fig_custom.update_layout(
            title=f"{nice_title(picked_col)} Over Time — {TIMEFRAME_LABEL}, {agg_label}",
            xaxis=dict(title="Date", title_standoff=8),
            bargap=0.15,
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(b=60),
        )
        st.plotly_chart(fig_custom, use_container_width=True)

        if want_overlay and not can_overlay:
            st.caption(f"ℹ️ Overlay disabled: '_TOTAL_SPEND' not available for this selection.")


# =============================
# TAB 2 — MARKETING OVERVIEW
# =============================
with tab_mkt:
    st.subheader(f"Spend & Channels — {TIMEFRAME_LABEL} · {agg_label}")

    # ----- KPI — Outcomes (TOTALS only) -----
    st.markdown("#### Outcomes (Total)")
    cur_imps, d_imps = total_with_prev_local(IMPR_COLS)
    cur_clicks, d_clicks = total_with_prev_local(CLICK_COLS)
    cur_sessions, d_sessions = total_with_prev_local(SESSION_COLS)
    cur_installs, d_installs = total_with_prev_local(INSTALL_COLS)
    cur_spend, d_spend = total_with_prev_local(["_TOTAL_SPEND"])
    kpi_grid_fixed(
        [
            dict(
                title="Total Impressions",
                value=fmt_num(cur_imps),
                delta=(
                    f"{'+' if (d_imps or 0)>=0 else ''}{fmt_num(d_imps)}"
                    if d_imps is not None
                    else None
                ),
                good_when="up",
            ),
            dict(
                title="Total Clicks",
                value=fmt_num(cur_clicks),
                delta=(
                    f"{'+' if (d_clicks or 0)>=0 else ''}{fmt_num(d_clicks)}"
                    if d_clicks is not None
                    else None
                ),
                good_when="up",
            ),
            dict(
                title="Total Sessions",
                value=fmt_num(cur_sessions),
                delta=(
                    f"{'+' if (d_sessions or 0)>=0 else ''}{fmt_num(d_sessions)}"
                    if d_sessions is not None
                    else None
                ),
                good_when="up",
            ),
        ],
        per_row=3,
    )

    # ----- KPI — Spend (TOTALS + per-platform tiles) -----
    st.markdown("#### Spend (Total)")
    cur_spend, d_spend = total_with_prev_local(["_TOTAL_SPEND"])
    spend_boxes = [
        dict(
            title="Total Spend",
            value=fmt_num(cur_spend),
            delta=(
                f"{'+' if (d_spend or 0)>=0 else ''}{fmt_num(d_spend)}"
                if d_spend is not None
                else None
            ),
            good_when="down",
        )
    ]

    if not plat_map_df.empty and not df_r.empty:
        long_sp = (
            df_r.melt(
                id_vars=[DATE_COL],
                value_vars=plat_map_df["col"].tolist(),
                var_name="col",
                value_name="spend",
            )
            .merge(plat_map_df, on="col", how="left")
            .dropna(subset=["spend"])
        )
        cur_by_p_tiles = (
            long_sp.groupby("platform")["spend"]
            .sum()
            .sort_values(ascending=False)
        )

        if not df_prev.empty:
            long_prev = (
                df_prev.melt(
                    id_vars=[DATE_COL],
                    value_vars=plat_map_df["col"].tolist(),
                    var_name="col",
                    value_name="spend",
                )
                .merge(plat_map_df, on="col", how="left")
                .dropna(subset=["spend"])
            )
            prev_by_p_tiles = long_prev.groupby("platform")["spend"].sum()
        else:
            prev_by_p_tiles = pd.Series(dtype=float)

        for p, v in cur_by_p_tiles.items():
            dv = (
                v - prev_by_p_tiles.get(p, 0.0)
                if p in prev_by_p_tiles
                else None
            )
            delta = (
                (f"{'+' if (dv or 0)>=0 else ''}{fmt_num(dv)}")
                if dv is not None
                else None
            )
            spend_boxes.append(
                dict(
                    title=f"{p} Spend",
                    value=fmt_num(v),
                    delta=delta,
                    good_when="down",
                )
            )

    kpi_grid_fixed(spend_boxes, per_row=4)
    st.markdown("---")

    # ===== View selector =====
    st.markdown("#### View")
    channel_options = ["All channels"] + platforms
    view_sel = st.selectbox("Channel view", channel_options, index=0)

    # --- helpers to handle TOTAL columns cleanly ---
    def _is_total_col(col: str, plat: str | None = None) -> bool:
        if not isinstance(col, str):
            return False
        c = col.upper()
        if plat:
            p = str(plat).upper()
            if c.startswith(p + "_"):
                c = c[len(p) + 1 :]
        # treat any *_TOTAL*, *_TOTAL_COST, *_TOTAL_SPEND and leading TOTAL as totals
        if c.startswith("TOTAL"):
            return True
        total_suffixes = ["_TOTAL", "_TOTAL_COST", "_TOTAL_SPEND"]
        if any(c.endswith(suf) for suf in total_suffixes):
            return True
        if "_TOTAL_" in c:
            return True
        return False

    def _sub_label(col: str, plat: str) -> str:
        if not isinstance(col, str):
            return "Other"
        c = col.upper()
        p = str(plat).upper()
        if c.startswith(p + "_"):
            c = c[len(p) + 1 :]
        for suf in ["_TOTAL_COST", "_TOTAL_SPEND", "_COST", "_SPEND", "_TOTAL"]:
            if c.endswith(suf):
                c = c[: -len(suf)]
        c = c.split("_")[0].strip() or "Other"
        return c.title()

    # Prepare a filtered long df for charts based on view (exclude TOTAL sub-columns for single-channel view)
    def spend_long_filtered(dataframe: pd.DataFrame) -> pd.DataFrame:
        if plat_map_df.empty or dataframe.empty:
            return pd.DataFrame(columns=[DATE_COL, "col", "spend", "platform"])
        vm = plat_map_df.copy()
        if view_sel != "All channels":
            vm = vm[vm["platform"] == view_sel]
            # drop total columns inside the chosen platform
            vm = vm[~vm["col"].map(lambda c: _is_total_col(c, view_sel))]
        if vm.empty:
            return pd.DataFrame(columns=[DATE_COL, "col", "spend", "platform"])
        return (
            dataframe.melt(
                id_vars=[DATE_COL],
                value_vars=vm["col"].tolist(),
                var_name="col",
                value_name="spend",
            )
            .merge(vm, on="col", how="left")
            .dropna(subset=["spend"])
        )

    long_cur_view = spend_long_filtered(df_r)
    long_prev_view = (
        spend_long_filtered(df_prev) if not df_prev.empty else pd.DataFrame()
    )

    # ----- Waterfall — platform (all) OR sub-channel (single) -----
    st.markdown("#### Change vs Previous — Waterfall")
    if not long_cur_view.empty:
        if view_sel == "All channels":
            cur_grp = long_cur_view.groupby("platform")["spend"].sum()
            prev_grp = (
                long_prev_view.groupby("platform")["spend"].sum()
                if not long_prev_view.empty
                else pd.Series(dtype=float)
            )
            name_series = cur_grp
            title_suffix = "by Platform"
        else:
            sel_platform = view_sel
            cur_sub = long_cur_view.copy()
            cur_sub["sub"] = cur_sub["col"].map(
                lambda c: _sub_label(c, sel_platform)
            )
            # exclude any 'Total' bucket that might still slip through
            cur_sub = cur_sub[cur_sub["sub"].str.upper() != "TOTAL"]
            cur_grp = cur_sub.groupby("sub")["spend"].sum()

            if not long_prev_view.empty:
                prev_sub = long_prev_view.copy()
                prev_sub["sub"] = prev_sub["col"].map(
                    lambda c: _sub_label(c, sel_platform)
                )
                prev_sub = prev_sub[prev_sub["sub"].str.upper() != "TOTAL"]
                prev_grp = prev_sub.groupby("sub")["spend"].sum()
            else:
                prev_grp = pd.Series(dtype=float)

            name_series = cur_grp
            title_suffix = f"{sel_platform} — by Sub-Channel"

        all_keys = sorted(
            set(cur_grp.index).union(prev_grp.index),
            key=lambda x: name_series.get(x, 0.0),
            reverse=True,
        )

        steps, total_delta = [], 0.0
        for k in all_keys:
            dv = cur_grp.get(k, 0.0) - prev_grp.get(k, 0.0)
            total_delta += dv
            steps.append(dict(name=k, measure="relative", y=float(dv)))
        steps.insert(
            0,
            dict(
                name="Start (Prev Total)",
                measure="absolute",
                y=float(prev_grp.sum()),
            ),
        )
        steps.append(
            dict(
                name="End (Current Total)",
                measure="total",
                y=float(prev_grp.sum() + total_delta),
            )
        )

        fig_w = go.Figure(
            go.Waterfall(
                name="Delta",
                orientation="v",
                measure=[s["measure"] for s in steps],
                x=[s["name"] for s in steps],
                y=[s["y"] for s in steps],
            )
        )
        fig_w.update_layout(
            title=f"Spend Change — Waterfall ({title_suffix})",
            showlegend=False,
        )
        st.plotly_chart(fig_w, use_container_width=True)
    else:
        st.info("No spend data for the selected view.")
    st.markdown("---")

    # ----- Channel Mix (stacked) — platform (all) OR sub-channel (single) -----
    st.markdown("#### Channel Mix")
    if not long_cur_view.empty:
        if view_sel == "All channels":
            freq_df = (
                long_cur_view.set_index(DATE_COL)
                .groupby("platform")["spend"]
                .resample(RULE)
                .sum(min_count=1)
                .reset_index()
                .rename(columns={DATE_COL: "DATE_PERIOD"})
            )
            freq_df["series"] = freq_df["platform"]
            chart_title = (
                f"{spend_label} by Platform — {TIMEFRAME_LABEL}, {agg_label}"
            )
        else:
            sel_platform = view_sel
            sub_df = long_cur_view.copy()
            sub_df["sub"] = sub_df["col"].map(
                lambda c: _sub_label(c, sel_platform)
            )
            # exclude TOTAL bucket
            sub_df = sub_df[sub_df["sub"].str.upper() != "TOTAL"]
            freq_df = (
                sub_df.set_index(DATE_COL)
                .groupby("sub")["spend"]
                .resample(RULE)
                .sum(min_count=1)
                .reset_index()
                .rename(columns={DATE_COL: "DATE_PERIOD"})
            )
            freq_df["series"] = freq_df["sub"]
            chart_title = f"{spend_label} by Sub-Channel ({sel_platform}) — {TIMEFRAME_LABEL}, {agg_label}"

        freq_df["PERIOD_LABEL"] = period_label(freq_df["DATE_PERIOD"], RULE)
        order = (
            freq_df.groupby("series")["spend"]
            .sum()
            .sort_values(ascending=False)
            .index.tolist()
        )

        fig2 = px.bar(
            freq_df,
            x="PERIOD_LABEL",
            y="spend",
            color="series",
            category_orders={"series": order},
            color_discrete_map=PLATFORM_COLORS,  # OK if some series use fallback colors
            title=chart_title,
        )
        fig2.update_layout(
            barmode="stack",
            xaxis_title="Date",
            yaxis_title=spend_label,
            legend=dict(orientation="h"),
        )
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No spend data for the selected view.")
    st.markdown("---")

    # ----- Funnels (INDEPENDENT from selector; always per platform) -----
    st.markdown("#### Channel Funnels")
    if not plat_map_df.empty:

        def find_metric_cols(token: str, keyword: str):
            kw = keyword.upper()
            return [c for c, u in ALL_COLS_UP.items() if token in u and kw in u]

        for plat in platforms:
            token = plat.upper()
            spend_cols = plat_map_df.loc[
                plat_map_df["platform"] == plat, "col"
            ].tolist()
            spend_total = df_r[spend_cols].sum().sum() if spend_cols else 0.0
            sess_cols = find_metric_cols(token, "SESSION")
            click_cols = find_metric_cols(token, "CLICK")
            impr_cols = find_metric_cols(token, "IMPRESSION")
            installs_cols = [c for c in INSTALL_COLS if token in c.upper()]

            sessions = df_r[sess_cols].sum().sum() if sess_cols else 0.0
            clicks = df_r[click_cols].sum().sum() if click_cols else 0.0
            imps = df_r[impr_cols].sum().sum() if impr_cols else 0.0
            installs = df_r[installs_cols].sum().sum() if installs_cols else 0.0

            cpm = (spend_total / imps * 1000) if imps > 0 else np.nan
            cpc = (spend_total / clicks) if clicks > 0 else np.nan
            cps = (spend_total / sessions) if sessions > 0 else np.nan

            col_left, col_right = st.columns([2, 1])
            with col_left:
                st.markdown(f"**{plat} Funnel**")
                steps = []
                if imps > 0:
                    steps.append(("Impressions", imps))
                if clicks > 0:
                    steps.append(("Clicks", clicks))
                if sessions > 0:
                    steps.append(("Sessions", sessions))
                if installs > 0:
                    steps.append(("Installs", installs))
                if steps:
                    labels = [s[0] for s in steps]
                    values = [s[1] for s in steps]
                    figf = go.Figure(
                        go.Funnel(
                            y=labels,
                            x=values,
                            text=[fmt_num(v, nd=2) for v in values],
                            textinfo="text+percent previous",
                            hovertemplate="%{label}: %{value:,}",
                        )
                    )
                    figf.update_layout(margin=dict(l=40, r=20, t=10, b=20))
                    st.plotly_chart(figf, use_container_width=True)
                else:
                    st.info("No funnel metrics found.")
            with col_right:
                tbl = pd.DataFrame(
                    {
                        "Metric": [
                            "Total Spend",
                            "Impressions",
                            "Clicks",
                            "Sessions",
                            "Cost per 1k Impressions",
                            "Cost per Click",
                            "Cost per Session",
                            "Impression→Click rate",
                            "Click→Session rate",
                        ],
                        "Value": [
                            fmt_num(spend_total),
                            fmt_num(imps),
                            fmt_num(clicks),
                            fmt_num(sessions),
                            (f"{cpm:.2f}" if pd.notna(cpm) else "–"),
                            (f"{cpc:.2f}" if pd.notna(cpc) else "–"),
                            (f"{cps:.2f}" if pd.notna(cps) else "–"),
                            (f"{(clicks/imps):.2%}" if imps > 0 else "–"),
                            (f"{(sessions/clicks):.2%}" if clicks > 0 else "–"),
                        ],
                    }
                )
                st.dataframe(tbl, hide_index=True, use_container_width=True)
            st.markdown("---")
    else:
        st.info("Platform mapping not available.")

# =============================
# TAB 3 — DATA PROFILE
# =============================

# ==== Data Profile helpers ====

def _num_stats(s: pd.Series) -> dict:
    ss = pd.to_numeric(s, errors="coerce")
    n = len(ss)
    non_null = int(ss.notna().sum())
    nulls = int(ss.isna().sum())
    zeros = int((ss == 0).sum(skipna=True))
    distinct = int(ss.nunique(dropna=True))
    stats = {
        "non_null": non_null,
        "nulls": nulls,
        "nulls_pct": (nulls / n) if n else np.nan,
        "zeros": zeros,
        "zeros_pct": (zeros / non_null) if non_null else np.nan,
        "distinct": distinct,
    }
    if non_null:
        q = ss.dropna()
        stats.update(
            dict(
                min=float(np.nanmin(q)),
                p10=float(np.nanpercentile(q, 10)),
                median=float(np.nanmedian(q)),
                mean=float(np.nanmean(q)),
                p90=float(np.nanpercentile(q, 90)),
                max=float(np.nanmax(q)),
                std=float(np.nanstd(q, ddof=1)) if q.size > 1 else 0.0,
            )
        )
    else:
        stats.update(dict(min=np.nan, p10=np.nan, median=np.nan, mean=np.nan, p90=np.nan, max=np.nan, std=np.nan))
    return stats


def _cat_stats(s: pd.Series) -> dict:
    # Generic non-numeric summary
    ss = s.astype("object")
    n = len(ss)
    non_null = int(ss.notna().sum())
    nulls = int(ss.isna().sum())
    distinct = int(ss.nunique(dropna=True))
    return dict(
        non_null=non_null,
        nulls= nulls,
        nulls_pct=(nulls / n) if n else np.nan,
        zeros=np.nan,          # not applicable for categorical/bool
        zeros_pct=np.nan,      # not applicable
        distinct=distinct,
        min=np.nan, p10=np.nan, median=np.nan, mean=np.nan, p90=np.nan, max=np.nan, std=np.nan,
    )


def _distribution_values(s: pd.Series, *, numeric_bins: int = 10, cat_topk: int = 5) -> list[float]:
    """Return a normalized list of values suitable for BarChartColumn."""
    if pd.api.types.is_numeric_dtype(s):
        q = pd.to_numeric(s, errors="coerce").dropna()
        if q.empty:
            return []
        hist, _ = np.histogram(q, bins=numeric_bins)
        total = hist.sum()
        return (hist / total).tolist() if total else []
    elif pd.api.types.is_datetime64_any_dtype(s):
        q = pd.to_datetime(s, errors="coerce").dropna()
        if q.empty:
            return []
        # bucket by month for a quick sense of spread
        vc = q.dt.to_period("M").value_counts().sort_index()
        total = vc.sum()
        return (vc / total).tolist() if total else []
    else:
        q = s.dropna().astype("object")
        if q.empty:
            return []
        vc = q.value_counts().head(cat_topk)
        total = vc.sum()
        return (vc / total).tolist() if total else []

# ==== Data Profile tab ====
# =============================
# TAB 3 — DATA PROFILE
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