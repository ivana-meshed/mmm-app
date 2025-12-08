import os
from typing import List

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------
EXPORT_DIR = "/Users/fethullahertugrul/Downloads/robyn_v3_fr_export/"

FILE_XAGG = "xDecompAgg.parquet"
FILE_HYP = "resultHypParam.parquet"
FILE_MEDIA = "mediaVecCollect.parquet"
FILE_XVEC = "xDecompVecCollect.parquet"
FILE_ALLOCATOR = "allocator_scenarios.parquet"  # optional, Robyn allocator export

st.set_page_config(page_title="Robyn – CMO Optimization Dashboard", layout="wide")
st.title("📊 Robyn – Channel & Budget Optimization Explorer")

# ---------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------
@st.cache_data
def load_df(path: str) -> pd.DataFrame:
    return pd.read_parquet(path)


def detect_val_col(xagg: pd.DataFrame) -> str:
    candidates = ["xDecompAgg", "xDecomp", "xDecomp_total"]
    for c in candidates:
        if c in xagg.columns:
            return c
    st.error(f"No contribution column found in xDecompAgg: {candidates}")
    st.stop()


def to_ts(series: pd.Series) -> pd.Series:
    """Robust datetime conversion."""
    return pd.to_datetime(series, errors="coerce")


# ---------------------------------------------------------------------
# LOAD DATA
# ---------------------------------------------------------------------
paths = {
    "xagg": os.path.join(EXPORT_DIR, FILE_XAGG),
    "hyp": os.path.join(EXPORT_DIR, FILE_HYP),
    "media": os.path.join(EXPORT_DIR, FILE_MEDIA),
    "xvec": os.path.join(EXPORT_DIR, FILE_XVEC),
    "alloc": os.path.join(EXPORT_DIR, FILE_ALLOCATOR),
}
missing = [p for p in paths.values() if not os.path.exists(p) and "alloc" not in p]
if missing:
    st.error(f"Parquet export not found — please check paths:\n{missing}")
    st.stop()

xAgg = load_df(paths["xagg"])
hyp = load_df(paths["hyp"])
media = load_df(paths["media"])
xVec = load_df(paths["xvec"])

allocator_df = None
if os.path.exists(paths["alloc"]):
    allocator_df = load_df(paths["alloc"])

# Basic validations
for col in ["solID", "rn"]:
    if col not in xAgg.columns:
        st.error(f"xDecompAgg is missing required column: {col}")
        st.stop()

for col in ["solID"]:
    if col not in media.columns:
        st.error("mediaVecCollect is missing required column 'solID'")
        st.stop()

for col in ["solID", "ds"]:
    if col not in xVec.columns:
        st.error("xDecompVecCollect is missing required columns ('solID', 'ds').")
        st.stop()

if "solID" not in hyp.columns:
    st.error("resultHypParam is missing 'solID'.")
    st.stop()

val_col = detect_val_col(xAgg)

# ---------------------------------------------------------------------
# SIDEBAR – MODEL QUALITY FILTERS
# ---------------------------------------------------------------------
st.sidebar.header("Model selection thresholds")

rsq_min = st.sidebar.slider("Min R² (validation)", 0.0, 1.0, 0.45, 0.01)
nrmse_max = st.sidebar.slider("Max NRMSE (validation)", 0.0, 1.0, 0.12, 0.01)
decomp_max = st.sidebar.slider("Max decomp.rssd", 0.0, 0.5, 0.025, 0.001)

req_cols = ["solID", "rsq_val", "nrmse_val", "decomp.rssd"]
missing_hyp = [c for c in req_cols if c not in hyp.columns]
if missing_hyp:
    st.error(f"resultHypParam missing columns: {missing_hyp}")
    st.stop()

good_models = hyp[
    (hyp["rsq_val"].fillna(0) >= rsq_min)
    & (hyp["nrmse_val"].fillna(1) <= nrmse_max)
    & (hyp["decomp.rssd"].fillna(0) <= decomp_max)
]["solID"].unique()

st.write(f"### Selected {len(good_models)} / {len(hyp)} models based on thresholds")

if len(good_models) == 0:
    st.warning("No models match the thresholds.")
    st.stop()

# Filter all dataframes to selected models
xAgg_gm = xAgg[xAgg["solID"].isin(good_models)].copy()
hyp_gm = hyp[hyp["solID"].isin(good_models)].copy()
media_gm = media[media["solID"].isin(good_models)].copy()
xVec_gm = xVec[xVec["solID"].isin(good_models)].copy()

# ---------------------------------------------------------------------
# SHARED DERIVATIONS
# ---------------------------------------------------------------------

# 1) Contribution share per driver & model (from xDecompAgg)
totals = (
    xAgg_gm.groupby("solID")[val_col]
    .sum()
    .rename("total_response")
    .reset_index()
)
xAgg_gm = xAgg_gm.merge(totals, on="solID", how="left")

xAgg_gm["share"] = np.where(
    xAgg_gm["total_response"] > 0,
    xAgg_gm[val_col] / xAgg_gm["total_response"],
    np.nan,
)
xAgg_gm["driver"] = xAgg_gm["rn"]

all_drivers = sorted(xAgg_gm["driver"].unique())

# 2) mediaVecCollect long format (spend per driver)
id_vars_media = [c for c in ["ds", "solID", "type"] if c in media_gm.columns]
value_cols_media = [c for c in media_gm.columns if c not in id_vars_media]

media_long_all = media_gm.melt(
    id_vars=id_vars_media,
    value_vars=value_cols_media,
    var_name="driver",
    value_name="spend",
)

media_long_all["ds"] = to_ts(media_long_all["ds"])
xVec_gm["ds"] = to_ts(xVec_gm["ds"])

# "Paid" drivers ~ anything that appears in mediaVecCollect
media_drivers = sorted(media_long_all["driver"].unique())

# ---------------------------------------------------------------------
# TABS
# ---------------------------------------------------------------------
tab_summary, tab_saturation, tab_adstock, tab_baseline, tab_elasticity, tab_allocator = st.tabs(
    [
        "1️⃣ Driver & ROAS Summary",
        "2️⃣ Saturation & Diminishing Returns",
        "3️⃣ Adstock & Persistence",
        "4️⃣ Baseline & Seasonality",
        "5️⃣ Relative Effectiveness",
        "6️⃣ Allocator Scenarios",
    ]
)

# =====================================================================
# 1) SUMMARY TAB – DRIVER STABILITY & ROAS
# =====================================================================
with tab_summary:
    st.subheader("📦 Contribution Stability Across Models")

    default_share_drivers = [d for d in all_drivers if "COST" in d][:6] or all_drivers[:6]
    sel_drivers_share = st.multiselect(
        "Drivers for contribution analysis",
        options=all_drivers,
        default=default_share_drivers,
    )

    if sel_drivers_share:
        plot_df = xAgg_gm[xAgg_gm["driver"].isin(sel_drivers_share)].copy()

        fig_share = px.box(
            plot_df,
            x="driver",
            y="share",
            color="driver",
            points="all",
            title="Contribution share distribution across selected drivers & models",
        )
        fig_share.update_layout(showlegend=False)
        st.plotly_chart(fig_share, use_container_width=True)

        summary_share = (
            plot_df.groupby("driver")
            .agg(
                n_models=("solID", "nunique"),
                mean_share=("share", "mean"),
                sd_share=("share", "std"),
                min_share=("share", "min"),
                max_share=("share", "max"),
            )
            .reset_index()
            .sort_values("mean_share", ascending=False)
        )
        st.subheader("Summary statistics (contribution share)")
        st.dataframe(summary_share, use_container_width=True)
    else:
        st.info("Select at least one driver to see contribution stability.")

    st.markdown("---")
    st.subheader("💰 ROAS Stability Across Models (media variables)")

    default_roas_drivers = [d for d in media_drivers if "COST" in d][:6] or media_drivers[:6]
    sel_drivers_roas = st.multiselect(
        "Media drivers for ROAS analysis",
        options=media_drivers,
        default=default_roas_drivers,
    )

    if sel_drivers_roas:
        media_long = media_long_all[media_long_all["driver"].isin(sel_drivers_roas)].copy()

        # total spend per model & driver
        spend = (
            media_long.groupby(["solID", "driver"], as_index=False)["spend"]
            .sum()
            .rename(columns={"spend": "total_spend"})
        )

        # contribution per model & driver
        contrib = (
            xAgg_gm[xAgg_gm["driver"].isin(sel_drivers_roas)]
            .groupby(["solID", "driver"], as_index=False)[val_col]
            .sum()
            .rename(columns={val_col: "contrib"})
        )

        roas_df = contrib.merge(spend, on=["solID", "driver"], how="left")
        roas_df["roas"] = np.where(
            (roas_df["total_spend"] > 0) & np.isfinite(roas_df["total_spend"]),
            roas_df["contrib"] / roas_df["total_spend"],
            np.nan,
        )
        roas_plot_df = roas_df.dropna(subset=["roas"])

        if roas_plot_df.empty:
            st.info("No valid ROAS values (spend zero or missing for selected drivers/models).")
        else:
            fig_roas = px.box(
                roas_plot_df,
                x="driver",
                y="roas",
                color="driver",
                points="all",
                title="ROAS distribution across selected media drivers & models",
            )
            fig_roas.update_layout(showlegend=False)
            st.plotly_chart(fig_roas, use_container_width=True)

            summary_roas = (
                roas_plot_df.groupby("driver")
                .agg(
                    n_models=("solID", "nunique"),
                    mean_roas=("roas", "mean"),
                    median_roas=("roas", "median"),
                    sd_roas=("roas", "std"),
                    min_roas=("roas", "min"),
                    max_roas=("roas", "max"),
                )
                .reset_index()
                .sort_values("mean_roas", ascending=False)
            )
            st.subheader("Summary statistics (ROAS)")
            st.dataframe(summary_roas, use_container_width=True)
    else:
        st.info("Select at least one media driver to see ROAS stability.")

# =====================================================================
# 2) SATURATION TAB – DIMINISHING RETURNS APPROX
# =====================================================================
with tab_saturation:
    st.subheader("📉 Saturation & Diminishing Returns (per channel)")

    if not media_drivers:
        st.info("No media drivers detected in mediaVecCollect.")
    else:
        driver_sat = st.selectbox(
            "Driver for saturation analysis",
            options=media_drivers,
            index=0,
        )

        # ---- Spend (per model)
        media_sat = media_long_all[media_long_all["driver"] == driver_sat].copy()
        spend_sat = (
            media_sat.groupby(["solID"], as_index=False)["spend"]
            .sum()
            .rename(columns={"spend": "total_spend"})
        )

        # ---- Contribution (per model) from xDecompAgg (filtered = xAgg_gm)
        contrib_sat = (
            xAgg_gm[xAgg_gm["driver"] == driver_sat]
            .groupby(["solID"], as_index=False)[val_col]
            .sum()
            .rename(columns={val_col: "contrib"})
        )

        # ---- Merge spend + contrib
        sat_df_valid = contrib_sat.merge(spend_sat, on="solID", how="left")

        sat_df_valid["roas"] = np.where(
            (sat_df_valid["total_spend"] > 0) & np.isfinite(sat_df_valid["total_spend"]),
            sat_df_valid["contrib"] / sat_df_valid["total_spend"],
            np.nan,
        )

        sat_df_valid = sat_df_valid.dropna(subset=["total_spend", "contrib"])

        if sat_df_valid.empty:
            st.info("No saturation data available for this driver.")
        else:
            # ---------- Layout ----------
            c1, c2 = st.columns(2)

            # ==========================
            #   1) Contribution vs Spend
            # ==========================
            with c1:
                fig_scatter = px.scatter(
                    sat_df_valid,
                    x="total_spend",
                    y="contrib",
                    trendline="lowess",
                    title=f"Total contribution vs. total spend — {driver_sat}",
                )
                fig_scatter.update_layout(
                    xaxis_title="Total spend",
                    yaxis_title="Total contribution"
                )
                st.plotly_chart(fig_scatter, use_container_width=True)

            # ==========================
            #   2) ROAS vs Spend (Bins)
            # ==========================
            with c2:
                # Create spend quantile bins
                sat_df_valid["spend_bin"] = pd.qcut(
                    sat_df_valid["total_spend"],
                    q=min(5, sat_df_valid["total_spend"].nunique()),
                    duplicates="drop",
                )

                # Aggregate stats per bin
                bin_stats = (
                    sat_df_valid.groupby("spend_bin", as_index=False)
                    .agg(
                        median_roas=("roas", "median"),
                        mean_roas=("roas", "mean"),
                        n_models=("solID", "nunique"),
                    )
                )

                # Convert interval objects → strings for Plotly JSON
                bin_stats["spend_bin"] = bin_stats["spend_bin"].astype(str)

                fig_bins = px.bar(
                    bin_stats,
                    x="spend_bin",
                    y="median_roas",
                    title=f"Median ROAS by spend bin — {driver_sat}",
                )
                fig_bins.update_layout(
                    xaxis_title="Spend bin",
                    yaxis_title="Median ROAS",
                )
                st.plotly_chart(fig_bins, use_container_width=True)

            # ==========================
            #   3) Table — Per Model
            # ==========================
            st.subheader("Per-model summary")
            st.dataframe(
                sat_df_valid.sort_values("total_spend"),
                use_container_width=True
            )
            
# =====================================================================
# 3) ADSTOCK TAB – PERSISTENCE OF EFFECTS
# =====================================================================
with tab_adstock:
    st.subheader("⏳ Adstock & Persistence Parameters")

    # melt *_thetas, *_gammas, *_alphas
    def melt_param(df: pd.DataFrame, suffix: str, allowed_channels: List[str]) -> pd.DataFrame:
        cols = [c for c in df.columns if c.endswith(suffix)]
        if not cols:
            return pd.DataFrame()
        m = df.melt(id_vars=["solID"], value_vars=cols, var_name="param", value_name="value")
        m["driver"] = m["param"].str.replace(f"_{suffix}$", "", regex=True)
        if allowed_channels:
            m = m[m["driver"].isin(allowed_channels)]
        return m

    hyp_gm = hyp_gm.copy()
    hyp_gm["solID"] = hyp_gm["solID"].astype(str)

    # restrict to media drivers where possible
    adstock_allowed = [d for d in media_drivers if any(d in c for c in hyp_gm.columns)]
    if not adstock_allowed:
        st.info("No matching media drivers found in resultHypParam for adstock parameters.")
    else:
        theta_df = melt_param(hyp_gm, "thetas", adstock_allowed)
        gamma_df = melt_param(hyp_gm, "gammas", adstock_allowed)
        alpha_df = melt_param(hyp_gm, "alphas", adstock_allowed)

        driver_ad = st.selectbox(
            "Driver for adstock parameter inspection",
            options=sorted(set(theta_df["driver"].unique()) | set(gamma_df["driver"].unique())),
            index=0,
        )

        c1, c2, c3 = st.columns(3)

        theta_sel = theta_df[theta_df["driver"] == driver_ad]
        gamma_sel = gamma_df[gamma_df["driver"] == driver_ad]
        alpha_sel = alpha_df[alpha_df["driver"] == driver_ad]

        if not theta_sel.empty:
            with c1:
                fig_theta = px.box(
                    theta_sel,
                    y="value",
                    points="all",
                    title=f"θ (carryover) — {driver_ad}",
                )
                fig_theta.update_layout(yaxis_title="theta")
                st.plotly_chart(fig_theta, use_container_width=True)

        if not gamma_sel.empty:
            with c2:
                fig_gamma = px.box(
                    gamma_sel,
                    y="value",
                    points="all",
                    title=f"γ (decay shape) — {driver_ad}",
                )
                fig_gamma.update_layout(yaxis_title="gamma")
                st.plotly_chart(fig_gamma, use_container_width=True)

        if not alpha_sel.empty:
            with c3:
                fig_alpha = px.box(
                    alpha_sel,
                    y="value",
                    points="all",
                    title=f"α (saturation exponent) — {driver_ad}",
                )
                fig_alpha.update_layout(yaxis_title="alpha")
                st.plotly_chart(fig_alpha, use_container_width=True)

        # quick table
        st.subheader("Parameter summary (median & spread)")
        def summarize_param(df_param: pd.DataFrame, name: str) -> pd.DataFrame:
            if df_param.empty:
                return pd.DataFrame()
            return (
                df_param.groupby("driver")
                .agg(
                    median=( "value", "median"),
                    p25=("value", lambda x: np.nanpercentile(x, 25)),
                    p75=("value", lambda x: np.nanpercentile(x, 75)),
                )
                .rename(columns={"median": f"{name}_median", "p25": f"{name}_p25", "p75": f"{name}_p75"})
            )

        s_theta = summarize_param(theta_df, "theta")
        s_gamma = summarize_param(gamma_df, "gamma")
        s_alpha = summarize_param(alpha_df, "alpha")

        if not s_theta.empty or not s_gamma.empty or not s_alpha.empty:
            merged = s_theta.join(s_gamma, how="outer").join(s_alpha, how="outer")
            merged = merged.reset_index().rename(columns={"index": "driver"})
            st.dataframe(merged.sort_values("driver"), use_container_width=True)

# =====================================================================
# 4) BASELINE & SEASONALITY TAB
# =====================================================================
with tab_baseline:
    st.subheader("🏛 Baseline vs Media & Seasonality Over Time")

    # Identify media columns in xVec (intersection with media drivers)
    media_cols_in_xvec = sorted(set(media_drivers) & set(xVec_gm.columns))

    if not media_cols_in_xvec:
        st.info("No media drivers found as columns in xDecompVecCollect.")
    else:
        # compute per-row media vs baseline contributions
        xvec_copy = xVec_gm.copy()
        xvec_copy["ds"] = to_ts(xvec_copy["ds"])

        xvec_copy["media_contrib"] = xvec_copy[media_cols_in_xvec].sum(axis=1)

        if "depVarHat" in xvec_copy.columns:
            xvec_copy["baseline_contrib"] = xvec_copy["depVarHat"] - xvec_copy["media_contrib"]
        else:
            # fall back: sum of all non-media drivers as "baseline"
            non_media_cols = [
                c for c in xvec_copy.columns
                if c not in media_cols_in_xvec + ["ds", "solID", "dep_var"]
            ]
            xvec_copy["baseline_contrib"] = xvec_copy[non_media_cols].sum(axis=1)

        # aggregate across models by date (mean)
        ts_base = (
            xvec_copy.groupby("ds", as_index=False)
            .agg(
                media_contrib=("media_contrib", "mean"),
                baseline_contrib=("baseline_contrib", "mean"),
                dep_var=("dep_var", "mean") if "dep_var" in xvec_copy.columns else ("media_contrib", "mean"),
            )
            .sort_values("ds")
        )

        fig_stack = px.area(
            ts_base,
            x="ds",
            y=["baseline_contrib", "media_contrib"],
            title="Baseline vs media contribution over time (average across models)",
        )
        fig_stack.update_layout(
            xaxis_title="Date",
            yaxis_title="Contribution (model scale)",
            legend_title="Component",
        )
        st.plotly_chart(fig_stack, use_container_width=True)

        # seasonality/trend if available
        season_cols = [c for c in ["trend", "season", "holiday"] if c in xVec_gm.columns]
        if season_cols:
            ts_season = (
                xVec_gm.groupby("ds", as_index=False)[season_cols].mean().sort_values("ds")
            )
            fig_season = px.line(
                ts_season,
                x="ds",
                y=season_cols,
                title="Trend / Seasonality / Holiday components (avg across models)",
            )
            fig_season.update_layout(xaxis_title="Date", yaxis_title="Contribution")
            st.plotly_chart(fig_season, use_container_width=True)

# =====================================================================
# 5) RELATIVE EFFECTIVENESS TAB – EFFECT SHARE VS SPEND SHARE
# =====================================================================
with tab_elasticity:
    st.subheader("📈 Relative Effectiveness (Effect Share vs Spend Share)")

    if media_long_all.empty:
        st.info("No media spend data available.")
    else:
        # total spend per model
        total_spend_model = (
            media_long_all.groupby("solID", as_index=False)["spend"]
            .sum()
            .rename(columns={"spend": "total_spend_model"})
        )

        # spend per model & driver
        spend_drv = (
            media_long_all.groupby(["solID", "driver"], as_index=False)["spend"]
            .sum()
            .rename(columns={"spend": "total_spend_driver"})
        )
        spend_drv = spend_drv.merge(total_spend_model, on="solID", how="left")
        spend_drv["spend_share"] = np.where(
            spend_drv["total_spend_model"] > 0,
            spend_drv["total_spend_driver"] / spend_drv["total_spend_model"],
            np.nan,
        )

        # effect share per model & driver from xAgg_gm
        contrib_drv = (
            xAgg_gm.groupby(["solID", "driver"], as_index=False)[val_col]
            .sum()
            .rename(columns={val_col: "contrib"})
        )
        contrib_drv = contrib_drv.merge(totals, on="solID", how="left")
        contrib_drv["effect_share"] = np.where(
            contrib_drv["total_response"] > 0,
            contrib_drv["contrib"] / contrib_drv["total_response"],
            np.nan,
        )

        eff = spend_drv.merge(contrib_drv, on=["solID", "driver"], how="inner")

        eff["eff_index"] = np.where(
            (eff["spend_share"] > 0) & np.isfinite(eff["spend_share"]),
            eff["effect_share"] / eff["spend_share"],
            np.nan,
        )

        eff = eff[eff["driver"].isin(media_drivers)]
        eff_valid = eff.dropna(subset=["eff_index", "spend_share", "effect_share"])

        if eff_valid.empty:
            st.info("No valid effectiveness data (effect/spend shares).")
        else:
            default_eff_drivers = [d for d in media_drivers if "COST" in d][:6] or media_drivers[:6]
            sel_eff_drivers = st.multiselect(
                "Media drivers for effectiveness comparison",
                options=media_drivers,
                default=default_eff_drivers,
            )

            eff_plot = eff_valid[eff_valid["driver"].isin(sel_eff_drivers)].copy()

            if eff_plot.empty:
                st.info("No data for selected drivers.")
            else:
                c1, c2 = st.columns(2)

                with c1:
                    fig_eff = px.box(
                        eff_plot,
                        x="driver",
                        y="eff_index",
                        color="driver",
                        points="all",
                        title="Relative effectiveness index (effect_share / spend_share)",
                    )
                    fig_eff.update_layout(showlegend=False, yaxis_title="Effectiveness index")
                    st.plotly_chart(fig_eff, use_container_width=True)

                with c2:
                    fig_sc = px.scatter(
                        eff_plot,
                        x="spend_share",
                        y="effect_share",
                        color="driver",
                        title="Effect vs spend share per model",
                    )
                    fig_sc.update_layout(
                        xaxis_title="Spend share",
                        yaxis_title="Effect share",
                    )
                    st.plotly_chart(fig_sc, use_container_width=True)

                st.subheader("Summary by driver")
                eff_summary = (
                    eff_plot.groupby("driver", as_index=False)
                    .agg(
                        n_models=("solID", "nunique"),
                        mean_eff=("eff_index", "mean"),
                        median_eff=("eff_index", "median"),
                        p25_eff=("eff_index", lambda x: np.nanpercentile(x, 25)),
                        p75_eff=("eff_index", lambda x: np.nanpercentile(x, 75)),
                        mean_spend_share=("spend_share", "mean"),
                    )
                    .sort_values("mean_eff", ascending=False)
                )
                st.dataframe(eff_summary, use_container_width=True)

# =====================================================================
# 6) ALLOCATOR TAB – SCENARIO PLACEHOLDER
# =====================================================================
with tab_allocator:
    st.subheader("🧮 Allocator Scenarios (Placeholder)")

    if allocator_df is None or allocator_df.empty:
        st.info(
            "Allocator outputs not found.\n\n"
            "Export Robyn allocator results (e.g. as Parquet) and place them as "
            f"`{FILE_ALLOCATOR}` in `{EXPORT_DIR}` to enable scenario charts."
        )
    else:
        st.write("Allocator data detected. Implement scenario views based on your schema here.")
        st.dataframe(allocator_df.head(), use_container_width=True)