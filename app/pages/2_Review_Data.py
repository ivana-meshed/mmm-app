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
from app_shared import (
    # GCS & versions
    list_data_versions, list_meta_versions,
    load_data_from_gcs, download_parquet_from_gcs_cached, download_json_from_gcs_cached,
    data_blob, data_latest_blob, meta_blob, meta_latest_blob,
    # meta & utilities
    build_meta_views, build_plat_map_df, validate_against_metadata, parse_date,
    pretty, fmt_num, freq_to_rule, period_label, safe_eff,
    kpi_box, kpi_grid, kpi_grid_fixed,
    BASE_PLATFORM_COLORS, build_platform_colors,
    # sidebar + filters
    render_sidebar, filter_range, previous_window, resample_numeric, total_with_prev,
    # colors (if exported; otherwise define locally)
    GREEN, RED
)

st.set_page_config(page_title="Marketing Overview & Analytics", layout="wide")
st.title("Marketing Overview & Analytics")

GCS_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")

# -----------------------------
# Session defaults
# -----------------------------
st.session_state.setdefault("country", "de")
st.session_state.setdefault("picked_data_ts", "Latest")
st.session_state.setdefault("picked_meta_ts", "Latest")

# -----------------------------
# Tabs
# -----------------------------
tab_load, tab_biz, tab_reg, tab_mkt = st.tabs(
    [
        "Select Data",
        "Business, Overview",
        "Business, Regional",
        "Marketing",
    ]
)

# =============================
# TAB 0 — DATA & METADATA LOADER
# =============================
with tab_load:
    st.markdown("### 📥 Load dataset & metadata from GCS")
    c1, c2, c3, c4 = st.columns([1.2, 1, 1, 0.6])

    country = (c1.text_input("Country (ISO2)", value=st.session_state["country"]).strip().lower())
    if country:
        st.session_state["country"] = country

    refresh_clicked = c4.button("↻ Refresh")
    refresh_key = str(pd.Timestamp.utcnow().value) if refresh_clicked else ""

    data_versions = list_data_versions(GCS_BUCKET, country, refresh_key) if country else ["Latest"]
    meta_versions = list_meta_versions(GCS_BUCKET, country, refresh_key) if country else ["Latest"]

    data_ts = c2.selectbox("Data version", options=data_versions, index=0, key="picked_data_ts")
    meta_ts = c3.selectbox("Metadata version", options=meta_versions, index=0, key="picked_meta_ts")

    load_clicked = st.button("Load from GCS", type="primary")

    if load_clicked:
        try:
            db = data_latest_blob(country) if data_ts == "Latest" else data_blob(country, str(data_ts))
            mb = meta_latest_blob(country) if meta_ts == "Latest" else meta_blob(country, str(meta_ts))

            df = download_parquet_from_gcs_cached(GCS_BUCKET, db)
            meta = download_json_from_gcs_cached(GCS_BUCKET, mb)
            df, date_col = parse_date(df, meta)

            st.session_state["df"] = df
            st.session_state["meta"] = meta
            st.session_state["date_col"] = date_col
            st.session_state["channels_map"] = meta.get("channels", {}) or {}

            report = validate_against_metadata(df, meta)
            st.success(f"Loaded {len(df):,} rows from gs://{GCS_BUCKET}/{db} and metadata gs://{GCS_BUCKET}/{mb}")

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
display_map, nice, goal_cols, mapping, m, ALL_COLS_UP, IMPR_COLS, CLICK_COLS, SESSION_COLS, INSTALL_COLS = build_meta_views(meta, df)

# -----------------------------
# Sidebar
# -----------------------------
GOAL, sel_countries, TIMEFRAME_LABEL, RANGE, agg_label, FREQ = render_sidebar(meta, df, nice, goal_cols)
st.session_state["RANGE"] = RANGE
st.session_state["FREQ"]  = FREQ
# Country filter
if sel_countries and "COUNTRY" in df:
    df = df[df["COUNTRY"].astype(str).isin(sel_countries)].copy()

# -----------------------------
# Target, spend, platforms
# -----------------------------
target = GOAL if (GOAL and GOAL in df.columns) else (goal_cols[0] if goal_cols else None)

# Spend + variables from metadata, restricted to columns present in df
paid_spend_cols = [c for c in (mapping.get("paid_media_spends", []) or []) if c in df.columns]
df["_TOTAL_SPEND"] = df[paid_spend_cols].sum(axis=1) if paid_spend_cols else 0.0

# REPLACE the old 'paid_var_cols = metadata.get("Paid Media Variables", [])' with:
paid_var_cols = [c for c in (mapping.get("paid_media_vars", []) or []) if c in df.columns]
organic_cols  = [c for c in (mapping.get("organic_vars",    []) or []) if c in df.columns]
context_cols  = [c for c in (mapping.get("context_vars",    []) or []) if c in df.columns]
factor_cols   = [c for c in (mapping.get("factor_vars",     []) or []) if c in df.columns]

# Convenience unions used later
present_spend = paid_spend_cols
present_vars  = paid_var_cols + organic_cols + context_cols + factor_cols

RULE = freq_to_rule(FREQ)
spend_label = ((meta.get("labels", {}) or {}).get("spend", "Spend") if isinstance(meta, dict) else "Spend")

plat_map_df, platforms, PLATFORM_COLORS = build_plat_map_df(
    present_spend=paid_spend_cols, df=df, meta=meta, m=m,
    COL="column_name", PLAT="platform", CHANNELS_MAP=CHANNELS_MAP
)

# -----------------------------
# Timeframe & resample
# ----------------------------- 
df_r = filter_range(df.copy(), DATE_COL, RANGE)
df_prev = previous_window(df, df_r, DATE_COL, RANGE)
# --- Backward-compat shim for total_with_prev calls expecting 1 arg ---
def total_with_prev_shim(collist):
    return total_with_prev(df_r, df_prev, collist)

res = resample_numeric(df_r, DATE_COL, RULE, ensure_cols=[target, "_TOTAL_SPEND"])
res["PERIOD_LABEL"] = period_label(res["DATE_PERIOD"], RULE)

def total_with_prev_local(collist):
    return total_with_prev(df_r, df_prev, collist)

# =============================
# TAB 1 — BUSINESS OVERVIEW
# =============================
with tab_biz:
    st.markdown("## KPI Overview")

    has_prev = not df_prev.empty

    # --- KPI block: totals per goal ---
    if goal_cols:
        kpis = []
        for g in goal_cols:
            cur = df_r[g].sum() if g in df_r else np.nan
            prev = df_prev[g].sum() if (has_prev and g in df_prev) else np.nan
            delta_txt = None
            if pd.notna(prev):
                diff = cur - prev
                delta_txt = f"{'+' if diff >= 0 else ''}{fmt_num(diff)}"
            kpis.append(
                dict(
                    title=f"{nice(g)}",
                    value=fmt_num(cur),
                    delta=delta_txt,
                    good_when="up",
                )
            )
        kpi_grid(kpis, per_row=5)
        st.markdown("---")

        # --- KPI block: avg efficiency (goal per spend, or ROAS for GMV) ---
        kpis2 = []
        for g in goal_cols:
            cur_eff = safe_eff(df_r, g)
            prev_eff = safe_eff(df_prev, g) if has_prev else np.nan
            delta_txt = None
            if pd.notna(cur_eff) and pd.notna(prev_eff):
                diff = cur_eff - prev_eff
                delta_txt = f"{'+' if diff >= 0 else ''}{diff:.2f}"
            eff_title = "ROAS" if str(g).upper() == "GMV" else f"{nice(g)} / {spend_label}"
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

    # --- Goal vs Spend (bar + line) ---
    st.markdown("## Goal vs Spend")
    cA, cB = st.columns(2)

    with cA:
        fig1 = go.Figure()
        if target and target in res:
            fig1.add_bar(x=res["PERIOD_LABEL"], y=res[target], name=nice(target))
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
            title=f"{nice(target) if target else 'Goal'} vs Total {spend_label} — {TIMEFRAME_LABEL}, {agg_label}",
            xaxis=dict(title="Date", title_standoff=8),
            yaxis=dict(title=nice(target) if target else "Goal"),
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
            fig2e.add_bar(x=eff_t["PERIOD_LABEL"], y=eff_t[target], name=nice(target))
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
            title=f"{nice(target) if target else 'Goal'} & {label_eff} Over Time — {TIMEFRAME_LABEL}, {agg_label}",
            xaxis=dict(title="Date", title_standoff=8),
            yaxis=dict(title=nice(target) if target else "Goal"),
            yaxis2=dict(title=label_eff, overlaying="y", side="right"),
            bargap=0.15,
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
            margin=dict(b=60),
        )
        st.plotly_chart(fig2e, use_container_width=True)

    st.markdown("---")

     # --- Custom metric over time (optional spend overlay) ---
    st.markdown("## Custom Metric Over Time")

    numeric_candidates = df_r.select_dtypes(include=[np.number]).columns.tolist()
    metrics = [c for c in numeric_candidates if c != "_TOTAL_SPEND"]

    if not metrics:
        st.info("No numeric columns available to plot.")
    else:
        # Build simple, collision-safe labels (no brackets)
        from collections import Counter
        base_labels = [(nice(c), c) for c in metrics]
        counts = Counter(lbl for (lbl, _) in base_labels)
        labels = []
        for (lbl, col) in base_labels:
            final = lbl if counts[lbl] == 1 else f"{lbl} · {col}"
            labels.append((final, col))

        labels_sorted = sorted([l for (l, _) in labels], key=lambda s: s.lower())
        label_to_col = {l: c for (l, c) in labels}

        # default to current target if available
        default_label = next((l for l, c in label_to_col.items() if c == target), None) or labels_sorted[0]

        c_sel, c_spend = st.columns([2, 1])
        picked_label = c_sel.selectbox("Metric", labels_sorted, index=labels_sorted.index(default_label))
        picked_col = label_to_col[picked_label]

        # Only show overlay toggle if we actually have _TOTAL_SPEND after resampling
        can_overlay = True  # will verify after res_plot is built
        want_overlay = c_spend.checkbox(f"Overlay Total {spend_label}", value=True)

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

        can_overlay = "_TOTAL_SPEND" in res_plot.columns

        fig_custom = go.Figure()
        fig_custom.add_bar(
            x=res_plot["PERIOD_LABEL"],
            y=res_plot[picked_col],
            name=nice(picked_col),
        )

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
                yaxis=dict(title=nice(picked_col)),
                yaxis2=dict(title=spend_label, overlaying="y", side="right"),
            )
        else:
            fig_custom.update_layout(
                yaxis=dict(title=nice(picked_col)),
            )

        fig_custom.update_layout(
            title=f"{nice(picked_col)} Over Time — {TIMEFRAME_LABEL}, {agg_label}",
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
# TAB 2 — REGIONAL COMPARISON 
# =============================
with tab_reg:
    st.subheader("Regional Comparison")

    # ---- Goal by country over time (stacked) ----
    if "COUNTRY" in df_r.columns and target and target in df_r:
        agg = (
            df_r.set_index(DATE_COL)
            .groupby("COUNTRY")[target]
            .resample(RULE)
            .sum(min_count=1)
            .reset_index()
            .rename(columns={DATE_COL: "DATE_PERIOD"})
        )
        agg["PERIOD_LABEL"] = period_label(agg["DATE_PERIOD"], RULE)
        tot = (
            agg.groupby("COUNTRY")[target]
            .sum()
            .sort_values(ascending=False)
            .index.tolist()
        )
        fig_cty = px.bar(
            agg,
            x="PERIOD_LABEL",
            y=target,
            color="COUNTRY",
            category_orders={"COUNTRY": tot},
            title=f"{nice(target)} by Country — Stacked Over Time",
            barmode="stack",
        )
        fig_cty.update_layout(
            xaxis_title="Date",
            yaxis_title=nice(target),
            legend=dict(orientation="h"),
        )
        st.plotly_chart(fig_cty, use_container_width=True)

    # -----------------------------
    # Channel mapping (from metadata)
    # -----------------------------
    if CHANNELS_MAP:
        ch_map_all = (
            pd.DataFrame(
                [(col, CHANNELS_MAP[col]) for col in df.columns if col in CHANNELS_MAP],
                columns=["col", "channel"],
            )
            .dropna()
        )
        ch_map_all["channel"] = ch_map_all["channel"].astype(str).str.upper()
    else:
        ch_map_all = pd.DataFrame(columns=["col", "channel"])

    ch_map_spend = (
        ch_map_all[ch_map_all["col"].isin(paid_spend_cols)].copy()
        if not ch_map_all.empty
        else pd.DataFrame(columns=["col", "channel"])
    )

    # -----------------------------
    # Country comparison table (outcomes, spend & conversions)
    # -----------------------------
    if "COUNTRY" in df_r:
        g = df_r.groupby("COUNTRY", dropna=False)
        spend_s = g["_TOTAL_SPEND"].sum()
        target_s = g[target].sum() if (target in df_r.columns) else spend_s

        imps_row = df_r[IMPR_COLS].sum(axis=1) if IMPR_COLS else pd.Series(0.0, index=df_r.index)
        clicks_row = df_r[CLICK_COLS].sum(axis=1) if CLICK_COLS else pd.Series(0.0, index=df_r.index)
        sessions_row = df_r[SESSION_COLS].sum(axis=1) if SESSION_COLS else pd.Series(0.0, index=df_r.index)

        imps_s = imps_row.groupby(df_r["COUNTRY"]).sum()
        clicks_s = clicks_row.groupby(df_r["COUNTRY"]).sum()
        sessions_s = sessions_row.groupby(df_r["COUNTRY"]).sum()

        by_cty = pd.DataFrame(
            {
                "spend": spend_s,
                "target": target_s,
                "imps": imps_s.reindex(spend_s.index, fill_value=0.0),
                "clicks": clicks_s.reindex(spend_s.index, fill_value=0.0),
                "sessions": sessions_s.reindex(spend_s.index, fill_value=0.0),
            }
        ).reset_index()

        by_cty["Impression→Click"] = by_cty["clicks"] / by_cty["imps"].replace(0, np.nan)
        by_cty["Click→Session"] = by_cty["sessions"] / by_cty["clicks"].replace(0, np.nan)
        by_cty["Efficiency"] = by_cty["target"] / by_cty["spend"].replace(0, np.nan)

        disp = by_cty.sort_values("spend", ascending=False)
        disp_fmt = disp.copy()
        disp_fmt["Impressions"] = disp["imps"].map(fmt_num)
        disp_fmt["Clicks"] = disp["clicks"].map(fmt_num)
        disp_fmt["Sessions"] = disp["sessions"].map(fmt_num)
        disp_fmt["Total Spend"] = disp["spend"].map(fmt_num)
        disp_fmt[f"Total {nice(target)}"] = disp["target"].map(fmt_num)
        disp_fmt["Impression→Click"] = disp["Impression→Click"].map(lambda x: f"{x:.2%}" if pd.notna(x) else "–")
        disp_fmt["Click→Session"] = disp["Click→Session"].map(lambda x: f"{x:.2%}" if pd.notna(x) else "–")
        perf_col_name = "ROAS" if target == "GMV" else f"{nice(target)}/Spend"
        disp_fmt[perf_col_name] = disp["Efficiency"].map(lambda x: f"{x:.2f}" if pd.notna(x) else "–")

        st.markdown("### Country Comparison — Outcomes, Spend & Conversions")
        st.dataframe(
            disp_fmt[
                [
                    "COUNTRY",
                    "Impressions",
                    "Clicks",
                    "Sessions",
                    "Total Spend",
                    f"Total {nice(target)}",
                    "Impression→Click",
                    "Click→Session",
                    perf_col_name,
                ]
            ].rename(columns={"COUNTRY": "Country"}),
            use_container_width=True,
        )
    st.markdown("---")

    # -----------------------------
    # Channel KPIs — Outcomes & Costs (totals, unchanged)
    # -----------------------------
    if not ch_map_all.empty:
        ch_to_cols = ch_map_all.groupby("channel")["col"].apply(list).to_dict()

        def _sum_cols(cols: list[str]) -> float:
            return df_r[cols].sum().sum() if cols else 0.0

        # Precompute per-channel spend totals from mapped spend cols
        if not ch_map_spend.empty:
            spend_long = (
                df_r.melt(
                    id_vars=[DATE_COL],
                    value_vars=ch_map_spend["col"].tolist(),
                    var_name="col",
                    value_name="spend",
                )
                .merge(ch_map_spend, on="col", how="left")
            )
            ch_spend_tot = spend_long.groupby("channel")["spend"].sum()
        else:
            ch_spend_tot = pd.Series(dtype=float)

        rows = []
        channels = sorted(ch_to_cols.keys())
        for ch in channels:
            cols_all = ch_to_cols.get(ch, [])
            ch_impr = [c for c in IMPR_COLS if c in cols_all]
            ch_clicks = [c for c in CLICK_COLS if c in cols_all]
            ch_sessions = [c for c in SESSION_COLS if c in cols_all]
            ch_installs = [c for c in INSTALL_COLS if c in cols_all]

            imps = _sum_cols(ch_impr)
            clicks = _sum_cols(ch_clicks)
            sessions = _sum_cols(ch_sessions)
            installs = _sum_cols(ch_installs)
            spend_total = float(ch_spend_tot.get(ch, 0.0))

            cpm = (spend_total / imps * 1000) if imps > 0 else np.nan
            cpc = (spend_total / clicks) if clicks > 0 else np.nan
            cps = (spend_total / sessions) if sessions > 0 else np.nan
            cpi = (spend_total / installs) if installs > 0 else np.nan

            rows.append([ch, imps, clicks, sessions, spend_total, cpm, cpc, cps, cpi])

        ch_cmp = pd.DataFrame(
            rows,
            columns=[
                "Channel",
                "Impressions",
                "Clicks",
                "Sessions",
                "Spend",
                "Cost per 1k Impressions",
                "Cost per Click",
                "Cost per Session",
                "Cost per Install",
            ],
        ).sort_values("Spend", ascending=False)

        ch_disp = ch_cmp.copy()
        for c in ["Impressions", "Clicks", "Sessions", "Spend"]:
            ch_disp[c] = ch_disp[c].map(fmt_num)
        for c in [
            "Cost per 1k Impressions",
            "Cost per Click",
            "Cost per Session",
            "Cost per Install",
        ]:
            ch_disp[c] = ch_cmp[c].map(lambda x: f"{x:.2f}" if pd.notna(x) else "–")

        st.markdown("### Channel KPIs — Outcomes & Costs")
        st.dataframe(ch_disp, use_container_width=True)

        # -----------------------------
        # Channel breakdown (selector → component table with same columns)
        # -----------------------------
        st.markdown("### Channel breakdown - PLACEHOLDER")
        if not channels:
            st.info("No channels found in metadata mapping.")
        else:
            sel_ch = st.selectbox("Channel", channels, index=0)

            # Component spend cols mapped to the selected channel
            comp_spend_cols = ch_map_spend.loc[ch_map_spend["channel"] == sel_ch, "col"].tolist()

            if not comp_spend_cols:
                st.caption("No component spend columns mapped for the selected channel.")
            else:
                # For component-level non-spend metrics, use a simple token heuristic:
                # take the spend column's leading token (before first '_') to match IMPR/CLICK/SESSION cols.
                def leading_token(colname: str) -> str:
                    m0 = re.match(r"([A-Za-z0-9]+)_", str(colname))
                    return (m0.group(1) if m0 else str(colname)).upper()

                ch_cols_all = ch_to_cols.get(sel_ch, [])
                ch_impr_all = [c for c in IMPR_COLS if c in ch_cols_all]
                ch_click_all = [c for c in CLICK_COLS if c in ch_cols_all]
                ch_sess_all = [c for c in SESSION_COLS if c in ch_cols_all]
                ch_inst_all = [c for c in INSTALL_COLS if c in ch_cols_all]

                rows_comp = []
                for spend_col in comp_spend_cols:
                    tok = leading_token(spend_col)
                    spend_total = df_r[spend_col].sum()

                    imps = df_r[[c for c in ch_impr_all if tok in c.upper()]].sum().sum() if ch_impr_all else 0.0
                    clicks = df_r[[c for c in ch_click_all if tok in c.upper()]].sum().sum() if ch_click_all else 0.0
                    sessions = df_r[[c for c in ch_sess_all if tok in c.upper()]].sum().sum() if ch_sess_all else 0.0
                    installs = df_r[[c for c in ch_inst_all if tok in c.upper()]].sum().sum() if ch_inst_all else 0.0

                    cpm = (spend_total / imps * 1000) if imps > 0 else np.nan
                    cpc = (spend_total / clicks) if clicks > 0 else np.nan
                    cps = (spend_total / sessions) if sessions > 0 else np.nan
                    cpi = (spend_total / installs) if installs > 0 else np.nan

                    rows_comp.append([spend_col, imps, clicks, sessions, spend_total, cpm, cpc, cps, cpi])

                comp_df = pd.DataFrame(
                    rows_comp,
                    columns=[
                        "Column",
                        "Impressions",
                        "Clicks",
                        "Sessions",
                        "Spend",
                        "Cost per 1k Impressions",
                        "Cost per Click",
                        "Cost per Session",
                        "Cost per Install",
                    ],
                ).sort_values("Spend", ascending=False)

                comp_disp = comp_df.copy()
                for c in ["Impressions", "Clicks", "Sessions", "Spend"]:
                    comp_disp[c] = comp_disp[c].map(fmt_num)
                for c in ["Cost per 1k Impressions", "Cost per Click", "Cost per Session", "Cost per Install"]:
                    comp_disp[c] = comp_df[c].map(lambda x: f"{x:.2f}" if pd.notna(x) else "–")

                st.dataframe(comp_disp, use_container_width=True)
    else:
        st.info("No channel mapping available in metadata.")

    st.markdown("---")

    # -----------------------------
    # Channel × Country Spend Matrix
    # -----------------------------
    st.markdown("### Channel × Country Spend Matrix")
    if not ch_map_spend.empty and "COUNTRY" in df_r.columns:
        spend_long_cty = (
            df_r.melt(
                id_vars=[DATE_COL, "COUNTRY"],
                value_vars=ch_map_spend["col"].tolist(),
                var_name="col",
                value_name="spend",
            )
            .merge(ch_map_spend, on="col", how="left")
            .dropna(subset=["spend"])
        )
        mat = (
            spend_long_cty.groupby(["channel", "COUNTRY"])["spend"]
            .sum()
            .unstack("COUNTRY")
            .fillna(0.0)
        )
        # Order channels (rows) by total spend desc, then transpose to Country x Channel
        mat = mat.loc[mat.sum(axis=1).sort_values(ascending=False).index]
        mat_t = mat.T  # TRANSPOSE: Countries as rows, Channels as columns
        st.dataframe(mat_t.applymap(fmt_num), use_container_width=True)
    else:
        st.info("Channel mapping or COUNTRY column not available.")

# =============================
# TAB 3 — MARKETING OVERVIEW 
# =============================
with tab_mkt:
    st.subheader(f"Spend & Channels — {TIMEFRAME_LABEL} · {agg_label}")

    # ----- KPI — Outcomes (TOTALS only) -----
    st.markdown("#### Outcomes (Total)")
    cur_imps, d_imps = total_with_prev_local(IMPR_COLS)
    cur_clicks, d_clicks = total_with_prev_local(CLICK_COLS)
    cur_sessions, d_sessions = total_with_prev_local(SESSION_COLS)
    cur_installs, d_installs = total_with_prev_local(INSTALL_COLS)
    cur_spend, d_spend = total_with_prev_shim(["_TOTAL_SPEND"])
    kpi_grid_fixed(
        [
            dict(title="Total Impressions", value=fmt_num(cur_imps),
                 delta=(f"{'+' if (d_imps or 0)>=0 else ''}{fmt_num(d_imps)}" if d_imps is not None else None),
                 good_when="up"),
            dict(title="Total Clicks", value=fmt_num(cur_clicks),
                 delta=(f"{'+' if (d_clicks or 0)>=0 else ''}{fmt_num(d_clicks)}" if d_clicks is not None else None),
                 good_when="up"),
            dict(title="Total Sessions", value=fmt_num(cur_sessions),
                 delta=(f"{'+' if (d_sessions or 0)>=0 else ''}{fmt_num(d_sessions)}" if d_sessions is not None else None),
                 good_when="up"),
        ],
        per_row=3,
    )

    # ----- KPI — Spend (TOTALS + per-platform tiles) -----
    st.markdown("#### Spend (Total)")
    cur_spend, d_spend = total_with_prev_local(["_TOTAL_SPEND"])
    spend_boxes = [
        dict(title="Total Spend", value=fmt_num(cur_spend),
             delta=(f"{'+' if (d_spend or 0)>=0 else ''}{fmt_num(d_spend)}" if d_spend is not None else None),
             good_when="down")
    ]

    if not plat_map_df.empty and not df_r.empty:
        long_sp = (
            df_r.melt(id_vars=[DATE_COL], value_vars=plat_map_df["col"].tolist(),
                      var_name="col", value_name="spend")
            .merge(plat_map_df, on="col", how="left")
            .dropna(subset=["spend"])
        )
        cur_by_p_tiles = long_sp.groupby("platform")["spend"].sum().sort_values(ascending=False)

        if not df_prev.empty:
            long_prev = (
                df_prev.melt(id_vars=[DATE_COL], value_vars=plat_map_df["col"].tolist(),
                             var_name="col", value_name="spend")
                .merge(plat_map_df, on="col", how="left")
                .dropna(subset=["spend"])
            )
            prev_by_p_tiles = long_prev.groupby("platform")["spend"].sum()
        else:
            prev_by_p_tiles = pd.Series(dtype=float)

        for p, v in cur_by_p_tiles.items():
            dv = v - prev_by_p_tiles.get(p, 0.0) if p in prev_by_p_tiles else None
            delta = (f"{'+' if (dv or 0)>=0 else ''}{fmt_num(dv)}") if dv is not None else None
            spend_boxes.append(dict(title=f"{p} Spend", value=fmt_num(v), delta=delta, good_when="down"))

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
    long_prev_view = spend_long_filtered(df_prev) if not df_prev.empty else pd.DataFrame()

    # ----- Waterfall — platform (all) OR sub-channel (single) -----
    st.markdown("#### Change vs Previous — Waterfall")
    if not long_cur_view.empty:
        if view_sel == "All channels":
            cur_grp = long_cur_view.groupby("platform")["spend"].sum()
            prev_grp = long_prev_view.groupby("platform")["spend"].sum() if not long_prev_view.empty else pd.Series(dtype=float)
            name_series = cur_grp
            title_suffix = "by Platform"
        else:
            sel_platform = view_sel
            cur_sub = long_cur_view.copy()
            cur_sub["sub"] = cur_sub["col"].map(lambda c: _sub_label(c, sel_platform))
            # exclude any 'Total' bucket that might still slip through
            cur_sub = cur_sub[cur_sub["sub"].str.upper() != "TOTAL"]
            cur_grp = cur_sub.groupby("sub")["spend"].sum()

            if not long_prev_view.empty:
                prev_sub = long_prev_view.copy()
                prev_sub["sub"] = prev_sub["col"].map(lambda c: _sub_label(c, sel_platform))
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
        steps.insert(0, dict(name="Start (Prev Total)", measure="absolute", y=float(prev_grp.sum())))
        steps.append(dict(name="End (Current Total)", measure="total", y=float(prev_grp.sum() + total_delta)))

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
            chart_title = f"{spend_label} by Platform — {TIMEFRAME_LABEL}, {agg_label}"
        else:
            sel_platform = view_sel
            sub_df = long_cur_view.copy()
            sub_df["sub"] = sub_df["col"].map(lambda c: _sub_label(c, sel_platform))
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
            spend_cols = plat_map_df.loc[plat_map_df["platform"] == plat, "col"].tolist()
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
                if imps > 0: steps.append(("Impressions", imps))
                if clicks > 0: steps.append(("Clicks", clicks))
                if sessions > 0: steps.append(("Sessions", sessions))
                if installs > 0: steps.append(("Installs", installs))
                if steps:
                    labels = [s[0] for s in steps]
                    values = [s[1] for s in steps]
                    figf = go.Figure(go.Funnel(
                        y=labels, x=values,
                        text=[fmt_num(v, nd=2) for v in values],
                        textinfo="text+percent previous",
                        hovertemplate="%{label}: %{value:,}",
                    ))
                    figf.update_layout(margin=dict(l=40, r=20, t=10, b=20))
                    st.plotly_chart(figf, use_container_width=True)
                else:
                    st.info("No funnel metrics found.")
            with col_right:
                tbl = pd.DataFrame({
                    "Metric": [
                        "Total Spend", "Impressions", "Clicks", "Sessions",
                        "Cost per 1k Impressions", "Cost per Click", "Cost per Session",
                        "Impression→Click rate", "Click→Session rate",
                    ],
                    "Value": [
                        fmt_num(spend_total), fmt_num(imps), fmt_num(clicks), fmt_num(sessions),
                        (f"{cpm:.2f}" if pd.notna(cpm) else "–"),
                        (f"{cpc:.2f}" if pd.notna(cpc) else "–"),
                        (f"{cps:.2f}" if pd.notna(cps) else "–"),
                        (f"{(clicks/imps):.2%}" if imps > 0 else "–"),
                        (f"{(sessions/clicks):.2%}" if clicks > 0 else "–"),
                    ],
                })
                st.dataframe(tbl, hide_index=True, use_container_width=True)
            st.markdown("---")
    else:
        st.info("Platform mapping not available.")