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
    # colors (if exported; otherwise define locally)
    GREEN,
    RED,
)

st.title("Review Business- & Marketing Data")

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
tab_load, tab_biz, tab_mkt = st.tabs(
    [
        "Select Data To Analyze",
        "Business Data",
        "Marketing Data",
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
    st.markdown("#### Outcomes (Goals)")
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
                    (f"{'+' if (d_imps or 0)>=0 else ''}{fmt_num(d_imps)}")
                    if d_imps is not None
                    else None
                ),
                good_when="up",
            ),
            dict(
                title="Total Clicks",
                value=fmt_num(cur_clicks),
                delta=(
                    (f"{'+' if (d_clicks or 0)>=0 else ''}{fmt_num(d_clicks)}")
                    if d_clicks is not None
                    else None
                ),
                good_when="up",
            ),
            dict(
                title="Total Sessions",
                value=fmt_num(cur_sessions),
                delta=(
                    (
                        f"{'+' if (d_sessions or 0)>=0 else ''}{fmt_num(d_sessions)}"
                    )
                    if d_sessions is not None
                    else None
                ),
                good_when="up",
            ),
        ],
        per_row=3,
    )


    # ----- KPI — Spend (TOTALS + per-platform tiles) -----
    st.markdown("#### Spend Overview")
    cur_spend, d_spend = total_with_prev_local(["_TOTAL_SPEND"])
    spend_boxes = [
        dict(
            title="Total Spend",
            value=fmt_num(cur_spend),
            delta=(
                (f"{'+' if (d_spend or 0)>=0 else ''}{fmt_num(d_spend)}")
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

    # ─────────────────────────────────────────────────────────────
    # Channel KPIs (Outcomes & Costs)  +  Channel breakdown  (SIDE BY SIDE)
    # ─────────────────────────────────────────────────────────────
    import re

    def _mk_long_marketing(frame: pd.DataFrame) -> pd.DataFrame:
        """
        Parse wide marketing columns shaped like:
          <channel>_<subchannel>_<metric>
        where metric ∈ {cost, impressions|impr, clicks, sessions} (case-insensitive).
        Returns long DF: [channel, subchannel, metric, value] summed over the selected window.
        """
        cols = []
        pat = re.compile(
            r"^(?P<channel>[A-Za-z0-9]+)_(?P<sub>[A-Za-z0-9]+)_(?P<m>cost|impressions?|impr|clicks?|sessions?)$",
            re.IGNORECASE,
        )
        for c in df_r.columns:
            m = pat.match(str(c))
            if m:
                d = m.groupdict()
                met = d["m"].lower()
                met = (
                    "impressions"
                    if met in ("impression", "impressions", "impr")
                    else met
                )
                met = "clicks" if met in ("click", "clicks") else met
                met = "sessions" if met in ("session", "sessions") else met
                cols.append((c, d["channel"].lower(), d["sub"].lower(), met))
        if not cols:
            return pd.DataFrame(
                columns=["channel", "subchannel", "metric", "value"]
            )
        data = []
        for col, ch, sub, met in cols:
            s = pd.to_numeric(df_r[col], errors="coerce").fillna(0.0)
            data.append((ch, sub, met, float(s.sum())))
        return pd.DataFrame(
            data, columns=["channel", "subchannel", "metric", "value"]
        )

    def _pivot_kpis(df_long: pd.DataFrame, by: list[str]) -> pd.DataFrame:
        base_cols = ["cost", "impressions", "clicks", "sessions"]
        if df_long.empty:
            return pd.DataFrame(
                columns=by
                + base_cols
                + ["CPM", "CPC", "CPS", "Impr→Click", "Click→Session"]
            )
        pvt = df_long.pivot_table(
            index=by,
            columns="metric",
            values="value",
            aggfunc="sum",
            fill_value=0.0,
        )
        for need in base_cols:
            if need not in pvt.columns:
                pvt[need] = 0.0
        pvt = pvt.reset_index()
        # Derived KPIs
        pvt["CPM"] = (pvt["cost"] / pvt["impressions"] * 1000).replace(
            [np.inf, -np.inf], np.nan
        )
        pvt["CPC"] = (pvt["cost"] / pvt["clicks"]).replace(
            [np.inf, -np.inf], np.nan
        )
        pvt["CPS"] = (pvt["cost"] / pvt["sessions"]).replace(
            [np.inf, -np.inf], np.nan
        )
        pvt["Impr→Click"] = (pvt["clicks"] / pvt["impressions"]).replace(
            [np.inf, -np.inf], np.nan
        )
        pvt["Click→Session"] = (pvt["sessions"] / pvt["clicks"]).replace(
            [np.inf, -np.inf], np.nan
        )
        return pvt[
            by
            + base_cols
            + ["CPM", "CPC", "CPS", "Impr→Click", "Click→Session"]
        ]

    # Build parsed long (then filter to paid channels only)
    mkt_long_all = _mk_long_marketing(df_r)
    paid_tokens = {p.lower() for p in platforms}
    mkt_long = mkt_long_all[mkt_long_all["channel"].isin(paid_tokens)].copy()

    # Override/ensure COST using plat_map_df (fixes TV etc.)
    paid_spend_total = {}
    if not plat_map_df.empty and not df_r.empty:
        sp = (
            df_r.melt(
                id_vars=[DATE_COL],
                value_vars=plat_map_df["col"].tolist(),
                var_name="col",
                value_name="spend",
            )
            .merge(plat_map_df, on="col", how="left")
            .dropna(subset=["spend"])
        )
        paid_spend_total = (
            sp.groupby(sp["platform"].str.lower())["spend"].sum().to_dict()
        )
    # LEFT / RIGHT columns
    st.markdown("### Channels")
    colL, colR = st.columns([1, 1])

    # LEFT: Channel KPIs — Outcomes & Costs
    with colL:
        st.markdown("#### Channel KPIs")
        if mkt_long.empty and not paid_spend_total:
            st.info(
                "No paid channel metrics found (expected `<channel>_<subchannel>_(cost|impressions|clicks|sessions)`)."
            )
        else:
            ch_kpis = _pivot_kpis(mkt_long, by=["channel"]).copy()
            # Apply spend override from plat map (case-insensitive)
            if not ch_kpis.empty:
                ch_kpis["channel_lc"] = ch_kpis["channel"].str.lower()
                ch_kpis["cost"] = ch_kpis.apply(
                    lambda r: paid_spend_total.get(r["channel_lc"], r["cost"]),
                    axis=1,
                )
                ch_kpis.drop(columns=["channel_lc"], inplace=True)
            else:
                # If only spend exists (no parsed metrics), build from spend map
                ch_kpis = pd.DataFrame(
                    [
                        {
                            "channel": k,
                            "cost": v,
                            "impressions": 0.0,
                            "clicks": 0.0,
                            "sessions": 0.0,
                        }
                        for k, v in paid_spend_total.items()
                    ]
                )
                ch_kpis = _pivot_kpis(
                    ch_kpis.melt(
                        id_vars=["channel"],
                        var_name="metric",
                        value_name="value",
                    ).assign(subchannel="total"),
                    by=["channel"],
                )

            disp = ch_kpis.copy()
            disp["channel"] = disp["channel"].str.upper()

            # Format large numbers with thousand separators
            def _fmt_int(x):
                return "–" if pd.isna(x) else f"{x:,.0f}"
            def _fmt_float(x):
                return "–" if pd.isna(x) else f"{x:,.2f}"
            def _fmt_pct(x):
                return "–" if pd.isna(x) else f"{x:.2%}"

            num_cols = [
                "cost", "impressions", "clicks", "sessions",
                "CPM", "CPC", "CPS", "Impr→Click", "Click→Session",
            ]
            for c in [col for col in num_cols if c in disp]:
                if c in ["Impr→Click", "Click→Session"]:
                    disp[c] = disp[c].map(_fmt_pct)
                elif c in ["CPM", "CPC", "CPS"]:
                    disp[c] = disp[c].map(_fmt_float)
                else:
                    disp[c] = disp[c].map(_fmt_int)

            st.dataframe(
                disp.rename(
                    columns={
                        "channel": "Channel",
                        "cost": "Spend",
                        "impressions": "Impressions",
                        "clicks": "Clicks",
                        "sessions": "Sessions",
                        "CPM": "Cost per 1 k Impr",
                        "CPC": "Cost per Click",
                        "CPS": "Cost per Session",
                        "Impr→Click": "Impr→Click Rate",
                        "Click→Session": "Click→Session Rate",
                    }
                ),
                use_container_width=True,
                hide_index=True,
                key="marketing_channel_kpis_v4",
            )

    # RIGHT: Channel breakdown (paid only)
    with colR:
        st.markdown("#### Channel breakdown")
        channels_parsed = sorted(mkt_long["channel"].unique().tolist())
        if not channels_parsed and paid_spend_total:
            channels_parsed = sorted(paid_spend_total.keys())
        if not channels_parsed:
            st.info("No paid channels found for breakdown.")
        else:
            sel_breakdown_channel = st.selectbox(
                "Channel (breakdown)",
                options=[c.upper() for c in channels_parsed],
                index=0,
                key="marketing_channel_selector_v4",
            )
            sel_lc = sel_breakdown_channel.lower()
            sub_long = mkt_long[mkt_long["channel"].eq(sel_lc)]
            sub_kpis = _pivot_kpis(sub_long, by=["channel", "subchannel"]).copy()

            if sub_kpis.empty:
                st.info("No subchannel metrics found for this channel.")
            else:
                sub_disp = sub_kpis.drop(columns=["channel"]).copy()
                sub_disp["subchannel"] = sub_disp["subchannel"].str.upper()

                # Apply same formatting
                for c in [col for col in num_cols if c in sub_disp]:
                    if c in ["Impr→Click", "Click→Session"]:
                        sub_disp[c] = sub_disp[c].map(_fmt_pct)
                    elif c in ["CPM", "CPC", "CPS"]:
                        sub_disp[c] = sub_disp[c].map(_fmt_float)
                    else:
                        sub_disp[c] = sub_disp[c].map(_fmt_int)

                st.dataframe(
                    sub_disp.rename(
                        columns={
                            "subchannel": "Subchannel",
                            "cost": "Spend",
                            "impressions": "Impressions",
                            "clicks": "Clicks",
                            "sessions": "Sessions",
                            "CPM": "Cost per 1 k Impr",
                            "CPC": "Cost per Click",
                            "CPS": "Cost per Session",
                            "Impr→Click": "Impr→Click Rate",
                            "Click→Session": "Click→Session Rate",
                        }
                    ),
                    use_container_width=True,
                    hide_index=True,
                    key="marketing_channel_breakdown_v4",
                )
    

    st.markdown("---")

    # ===== View selector (kept) =====
    st.markdown("#### View")
    channel_options = ["All channels"] + platforms
    view_sel = st.selectbox("Channel view", channel_options, index=0)

    # --- helpers ---
    def _is_total_col(col: str, plat: str | None = None) -> bool:
        if not isinstance(col, str):
            return False
        c = col.upper()
        if plat:
            p = str(plat).upper()
            if c.startswith(p + "_"):
                c = c[len(p) + 1 :]
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

    # A unified long builder for Channel Mix by selected metric
    metric_label_map = {
        "Costs": "spend",
        "Impressions": "impressions",
        "Clicks": "clicks",
        "Sessions": "sessions",
    }
    mix_metric = st.selectbox(
        "Metric for Channel Mix", list(metric_label_map.keys()), index=0
    )
    mix_field = metric_label_map[mix_metric]

    def metric_long_filtered(dataframe: pd.DataFrame) -> pd.DataFrame:
        """
        Returns long df: [DATE_COL, 'value', 'platform'] for the selected metric.
        - Costs: uses spend columns from plat_map_df (paid only)
        - Other metrics: map columns to platform by token in ALL_COLS_UP
          and exclude *_TOTAL* inside a platform.
        Also adds 'ORGANIC' bucket if such metric columns exist and do not belong to any paid platform.
        """
        if dataframe.empty:
            return pd.DataFrame(columns=[DATE_COL, "value", "platform"])

        if mix_field == "spend":
            if plat_map_df.empty:
                return pd.DataFrame(columns=[DATE_COL, "value", "platform"])
            vm = plat_map_df.copy()
            if view_sel != "All channels":
                vm = vm[vm["platform"] == view_sel]
                vm = vm[~vm["col"].map(lambda c: _is_total_col(c, view_sel))]
            if vm.empty:
                return pd.DataFrame(columns=[DATE_COL, "value", "platform"])
            return (
                dataframe.melt(
                    id_vars=[DATE_COL],
                    value_vars=vm["col"].tolist(),
                    var_name="col",
                    value_name="value",
                )
                .merge(vm, on="col", how="left")
                .dropna(subset=["value"])
                .rename(columns={"platform": "platform"})[
                    [DATE_COL, "value", "platform"]
                ]
            )
        else:
            # Map candidate columns for the metric
            metric_cols = {
                "impressions": IMPR_COLS,
                "clicks": CLICK_COLS,
                "sessions": SESSION_COLS,
            }[mix_field]

            rows = []
            paid_up = [p.upper() for p in platforms]
            used_cols = set()

            # Paid platforms
            for p in platforms:
                pu = p.upper()
                cols_p = [
                    c
                    for c in metric_cols
                    if pu in ALL_COLS_UP.get(c, c.upper())
                    and not _is_total_col(c, p)
                ]
                if cols_p:
                    used_cols.update(cols_p)
                    tmp = dataframe[[DATE_COL] + cols_p].melt(
                        id_vars=[DATE_COL],
                        value_vars=cols_p,
                        var_name="col",
                        value_name="value",
                    )
                    tmp["platform"] = p
                    rows.append(tmp[[DATE_COL, "value", "platform"]])

            # Organic bucket (if present & requested to include in pie)
            organic_cols = [
                c
                for c in metric_cols
                if ("ORGANIC" in ALL_COLS_UP.get(c, c.upper()))
                and c not in used_cols
            ]
            if organic_cols:
                tmp = dataframe[[DATE_COL] + organic_cols].melt(
                    id_vars=[DATE_COL],
                    value_vars=organic_cols,
                    var_name="col",
                    value_name="value",
                )
                tmp["platform"] = "Organic"
                rows.append(tmp[[DATE_COL, "value", "platform"]])

            if not rows:
                return pd.DataFrame(columns=[DATE_COL, "value", "platform"])
            out = pd.concat(rows, ignore_index=True)
            if view_sel != "All channels":
                out = out[out["platform"] == view_sel]
            return out

    long_cur_view = metric_long_filtered(df_r)
    long_prev_view = (
        metric_long_filtered(df_prev) if not df_prev.empty else pd.DataFrame()
    )

    # ----- Change vs Previous — Waterfall (fixed) -----
    st.markdown("#### Change vs Previous — Waterfall")

    def _metric_cols_for_field(field: str) -> list[str]:
        if field == "spend":
            # handled separately via plat_map_df
            return []
        return {
            "impressions": IMPR_COLS,
            "clicks": CLICK_COLS,
            "sessions": SESSION_COLS,
        }[field]

    def _long_for_view(frame: pd.DataFrame, field: str, view: str) -> pd.DataFrame:
        """
        Returns long df with columns: [series, value]
        - All channels: series = platform
        - Single channel: series = subchannel (derived)
        """
        if frame.empty:
            return pd.DataFrame(columns=["series", "value"])

        if view == "All channels":
            # Use already-built long_cur_view/long_prev_view (platform-level)
            return (
                frame.groupby("platform")["value"]
                .sum()
                .reset_index()
                .rename(columns={"platform": "series", "value": "value"})
            )

        # Single channel:
        sel_platform = view
        if field == "spend":
            # Use plat_map_df to get spend columns for this platform, exclude totals
            vm = plat_map_df.copy()
            vm = vm[vm["platform"] == sel_platform]
            vm = vm[~vm["col"].map(lambda c: _is_total_col(c, sel_platform))]
            if vm.empty:
                return pd.DataFrame(columns=["series", "value"])
            tmp = (
                frame.melt(
                    id_vars=[DATE_COL],
                    value_vars=vm["col"].tolist(),
                    var_name="col",
                    value_name="value",
                )
                .merge(vm, on="col", how="left")
                .dropna(subset=["value"])
            )
            tmp["series"] = tmp["col"].map(lambda c: _sub_label(c, sel_platform))
            tmp = tmp[tmp["series"].str.upper() != "TOTAL"]
            return tmp.groupby("series")["value"].sum().reset_index()
        else:
            # Non-spend metrics: impressions/clicks/sessions
            metric_cols = _metric_cols_for_field(field)
            pu = sel_platform.upper()
            # Pick only columns that belong to this platform and are not *_TOTAL*
            cols_p = [
                c for c in metric_cols
                if pu in ALL_COLS_UP.get(c, c.upper())
                and not _is_total_col(c, sel_platform)
            ]
            if not cols_p:
                return pd.DataFrame(columns=["series", "value"])
            tmp = frame[[DATE_COL] + cols_p].melt(
                id_vars=[DATE_COL],
                value_vars=cols_p,
                var_name="col",
                value_name="value",
            )
            tmp["series"] = tmp["col"].map(lambda c: _sub_label(c, sel_platform))
            tmp = tmp[tmp["series"].str.upper() != "TOTAL"]
            return tmp.groupby("series")["value"].sum().reset_index()

    if not long_cur_view.empty:
        # Build current & previous grouped series (platforms or subchannels)
        cur_series = _long_for_view(
            long_cur_view if view_sel == "All channels" else df_r,
            mix_field,
            view_sel,
        )
        if not long_prev_view.empty:
            prev_series = _long_for_view(
                long_prev_view if view_sel == "All channels" else df_prev,
                mix_field,
                view_sel,
            )
        else:
            prev_series = pd.DataFrame(columns=["series", "value"])

        if cur_series.empty and prev_series.empty:
            st.info("No data for the selected view/metric.")
        else:
            # Align keys
            cur_map = dict(zip(cur_series["series"], cur_series["value"]))
            prev_map = dict(zip(prev_series["series"], prev_series["value"]))
            all_keys = sorted(
                set(cur_map) | set(prev_map),
                key=lambda k: cur_map.get(k, 0.0),
                reverse=True,
            )

            steps, total_delta = [], 0.0
            for k in all_keys:
                dv = cur_map.get(k, 0.0) - prev_map.get(k, 0.0)
                total_delta += dv
                steps.append(dict(name=k, measure="relative", y=float(dv)))
            start_total = float(sum(prev_map.values()))
            steps.insert(0, dict(name="Start (Prev Total)", measure="absolute", y=start_total))
            steps.append(dict(name="End (Current Total)", measure="total", y=float(start_total + total_delta)))

            suffix = "by Platform" if view_sel == "All channels" else f"{view_sel} — by Sub-Channel"
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
                title=f"{mix_metric} Change — Waterfall ({suffix})",
                showlegend=False,
            )
            st.plotly_chart(fig_w, use_container_width=True)
    else:
        st.info(f"No data for the selected view/metric ({mix_metric}).")
    st.markdown("---")


    # ----- Channel Mix: PIE (overall) + STACKED (time) (fixed) -----
    st.markdown("#### Channel Mix")

    def _long_time_series(frame: pd.DataFrame, field: str, view: str) -> pd.DataFrame:
        """
        Returns long df with columns: [DATE_PERIOD, PERIOD_LABEL, value, series]
        series = platform (All channels) or subchannel (single channel).
        """
        if frame.empty:
            return pd.DataFrame(columns=["DATE_PERIOD", "PERIOD_LABEL", "value", "series"])

        if view == "All channels":
            # already have platform-level long in long_cur_view
            tmp = (
                frame.set_index(DATE_COL)
                .groupby("platform")["value"]
                .resample(RULE)
                .sum(min_count=1)
                .reset_index()
                .rename(columns={DATE_COL: "DATE_PERIOD", "platform": "series"})
            )
            tmp["PERIOD_LABEL"] = period_label(tmp["DATE_PERIOD"], RULE)
            return tmp[["DATE_PERIOD", "PERIOD_LABEL", "value", "series"]]

        sel_platform = view
        if field == "spend":
            vm = plat_map_df.copy()
            vm = vm[vm["platform"] == sel_platform]
            vm = vm[~vm["col"].map(lambda c: _is_total_col(c, sel_platform))]
            if vm.empty:
                return pd.DataFrame(columns=["DATE_PERIOD", "PERIOD_LABEL", "value", "series"])
            melted = (
                df_r.melt(
                    id_vars=[DATE_COL],
                    value_vars=vm["col"].tolist(),
                    var_name="col",
                    value_name="value",
                )
                .merge(vm, on="col", how="left")
                .dropna(subset=["value"])
            )
            melted["series"] = melted["col"].map(lambda c: _sub_label(c, sel_platform))
        else:
            metric_cols = _metric_cols_for_field(field)
            pu = sel_platform.upper()
            cols_p = [
                c for c in metric_cols
                if pu in ALL_COLS_UP.get(c, c.upper())
                and not _is_total_col(c, sel_platform)
            ]
            if not cols_p:
                return pd.DataFrame(columns=["DATE_PERIOD", "PERIOD_LABEL", "value", "series"])
            melted = df_r[[DATE_COL] + cols_p].melt(
                id_vars=[DATE_COL],
                value_vars=cols_p,
                var_name="col",
                value_name="value",
            )
            melted["series"] = melted["col"].map(lambda c: _sub_label(c, sel_platform))

        # resample by series
        out = (
            melted.set_index(DATE_COL)
            .groupby("series")["value"]
            .resample(RULE)
            .sum(min_count=1)
            .reset_index()
            .rename(columns={DATE_COL: "DATE_PERIOD"})
        )
        out["PERIOD_LABEL"] = period_label(out["DATE_PERIOD"], RULE)
        return out[["DATE_PERIOD", "PERIOD_LABEL", "value", "series"]]

    if not long_cur_view.empty:
        # PIE — overall mix
        if view_sel == "All channels":
            tot = (
                long_cur_view.groupby("platform")["value"]
                .sum()
                .reset_index()
                .rename(columns={"platform": "series", "value": "total"})
            )
            pie_title = f"Overall {mix_metric} Mix — {TIMEFRAME_LABEL}"
            color_map = PLATFORM_COLORS  # keep your platform colors
        else:
            ser = _long_for_view(df_r, mix_field, view_sel)
            tot = ser.rename(columns={"series": "series", "value": "total"})
            pie_title = f"Overall {mix_metric} Mix — {view_sel} (Sub-Channels)"
            color_map = None  # let Plotly choose for subchannels

        if tot.empty or tot["total"].sum() == 0:
            st.info("No data to plot for Channel Mix (pie).")
        else:
            fig_pie = px.pie(
                tot,
                names="series",
                values="total",
                title=pie_title,
                color="series",
                color_discrete_map=(color_map or {}),
            )
            st.plotly_chart(fig_pie, use_container_width=True)

        # STACKED — by period
        freq_df = _long_time_series(df_r if view_sel != "All channels" else long_cur_view, mix_field, view_sel)
        if freq_df.empty:
            st.info("No data to plot for Channel Mix (stacked).")
        else:
            order = (
                freq_df.groupby("series")["value"]
                .sum()
                .sort_values(ascending=False)
                .index.tolist()
            )
            title_suffix = "by Platform" if view_sel == "All channels" else f"{view_sel} — by Sub-Channel"
            fig2 = px.bar(
                freq_df,
                x="PERIOD_LABEL",
                y="value",
                color="series",
                category_orders={"series": order},
                color_discrete_map=(PLATFORM_COLORS if view_sel == "All channels" else {}),
                title=f"{mix_metric} — {title_suffix} — {TIMEFRAME_LABEL}, {agg_label}",
            )
            fig2.update_layout(
                barmode="stack",
                xaxis_title="Date",
                yaxis_title=mix_metric,
                legend=dict(orientation="h"),
            )
            st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info(f"No data for Channel Mix ({mix_metric}).")
    st.markdown("---")    

    # ----- Channel Funnels (unchanged) -----
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
