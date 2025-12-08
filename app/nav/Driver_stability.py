import os
import pandas as pd
import numpy as np
import streamlit as st
import plotly.express as px

EXPORT_DIR = "/Users/fethullahertugrul/Downloads/robyn_v3_fr_export/"

FILE_XAGG = "xDecompAgg.parquet"
FILE_HYP = "resultHypParam.parquet"
FILE_MEDIA = "mediaVecCollect.parquet"
FILE_XVEC = "xDecompVecCollect.parquet"

st.set_page_config(page_title="Driver Stability Explorer", layout="wide")
st.title("📊 Driver Stability — Robyn Model Explorer")

# ---------------------------------------------------------------------
# Load files
# ---------------------------------------------------------------------
@st.cache_data
def load_df(path: str) -> pd.DataFrame:
    return pd.read_parquet(path)

path_xagg = os.path.join(EXPORT_DIR, FILE_XAGG)
path_hyp = os.path.join(EXPORT_DIR, FILE_HYP)
path_media = os.path.join(EXPORT_DIR, FILE_MEDIA)
path_xvec = os.path.join(EXPORT_DIR, FILE_XVEC)

missing_files = [p for p in [path_xagg, path_hyp, path_media, path_xvec] if not os.path.exists(p)]
if missing_files:
    st.error(f"Parquet export not found — please check paths:\n{missing_files}")
    st.stop()

xAgg = load_df(path_xagg)
hyp = load_df(path_hyp)
media = load_df(path_media)
xVec = load_df(path_xvec)

# ---------------------------------------------------------------------
# Basic validation
# ---------------------------------------------------------------------
for col in ["solID", "rn"]:
    if col not in xAgg.columns:
        st.error(f"xDecompAgg is missing required column: {col}")
        st.stop()

if "solID" not in media.columns:
    st.error("mediaVecCollect is missing column 'solID'")
    st.stop()

if "solID" not in xVec.columns or "ds" not in xVec.columns:
    st.error("xDecompVecCollect is missing required columns ('solID', 'ds').")
    st.stop()

# Detect contribution column (name is version-dependent)
CAND_VAL = ["xDecompAgg", "xDecomp", "xDecomp_total"]
val_col = next((c for c in CAND_VAL if c in xAgg.columns), None)
if val_col is None:
    st.error(f"No contribution column found in xDecompAgg; tried {CAND_VAL}")
    st.stop()

# ---------------------------------------------------------------------
# Sidebar: model quality thresholds
# ---------------------------------------------------------------------
st.sidebar.header("Model selection thresholds")

rsq_min = st.sidebar.slider("Min R² (validation)", 0.0, 1.0, 0.45, 0.01)
nrmse_max = st.sidebar.slider("Max NRMSE (validation)", 0.0, 1.0, 0.12, 0.01)
decomp_max = st.sidebar.slider("Max decomp.rssd", 0.0, 0.5, 0.025, 0.001)

req_cols = ["solID", "rsq_val", "nrmse_val", "decomp.rssd"]
missing = [c for c in req_cols if c not in hyp.columns]
if missing:
    st.error(f"resultHypParam missing columns: {missing}")
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

# ---------------------------------------------------------------------
# xDecompAgg: contribution shares per model/driver
# ---------------------------------------------------------------------
df = xAgg[xAgg["solID"].isin(good_models)].copy()

totals = (
    df.groupby("solID")[val_col]
    .sum()
    .rename("total_response")
    .reset_index()
)
df = df.merge(totals, on="solID", how="left")

df["share"] = np.where(
    df["total_response"] > 0,
    df[val_col] / df["total_response"],
    np.nan,
)

df["driver"] = df["rn"]
drivers = sorted(df["driver"].unique())

# ---------------------------------------------------------------------
# mediaVecCollect melted once + paid-media heuristic
# ---------------------------------------------------------------------
id_vars_media = [c for c in ["ds", "solID", "type"] if c in media.columns]
value_cols_media = [c for c in media.columns if c not in id_vars_media]

media_long_all = media.melt(
    id_vars=id_vars_media,
    value_vars=value_cols_media,
    var_name="driver",
    value_name="spend",
)
media_long_all = media_long_all[media_long_all["solID"].isin(good_models)]

def is_paid_like(name: str) -> bool:
    u = name.upper()
    return any(k in u for k in ["COST", "SPEND", "_EUR", "_USD"])

paid_like_cols = [c for c in value_cols_media if is_paid_like(c)]

# ---------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------
tab_summary, tab_over_time = st.tabs(["Summary", "Over time"])

# ============================
# TAB 1: SUMMARY
# ============================
with tab_summary:
    st.subheader("Driver selection (for this tab)")

    default_drivers = [d for d in drivers if "COST" in d][:6] or drivers[:6]
    sel_drivers = st.multiselect(
        "Select drivers for contribution and ROAS boxplots",
        options=drivers,
        default=default_drivers,
    )

    if not sel_drivers:
        st.stop()

    plot_df = df[df["driver"].isin(sel_drivers)]

    # --- Contribution share boxplot ---
    st.subheader("📦 Contribution Share — Stability Across Models")

    fig_share = px.box(
        plot_df,
        x="driver",
        y="share",
        color="driver",
        points="all",
        title="Driver Contribution Share Distribution Across Selected Models",
    )
    fig_share.update_layout(showlegend=False)
    st.plotly_chart(fig_share, use_container_width=True)

    # --- Summary (share) ---
    st.subheader("Summary statistics (contribution share)")

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
    st.dataframe(summary_share, use_container_width=True)

    # --- ROAS across models (paid media only) ---
    st.subheader("💰 ROAS — Stability Across Models (paid media only)")

    roas_drivers = [d for d in sel_drivers if d in paid_like_cols]
    if not roas_drivers:
        st.info("None of the selected drivers are recognized as paid media (COST/SPEND/EUR/USD).")
    else:
        media_long = media_long_all[media_long_all["driver"].isin(roas_drivers)]

        spend = (
            media_long.groupby(["solID", "driver"], as_index=False)["spend"]
            .sum()
            .rename(columns={"spend": "total_spend"})
        )

        contrib = (
            df[df["driver"].isin(roas_drivers)]
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
            st.info("No valid ROAS values (spend is zero or missing for selected paid drivers/models).")
        else:
            fig_roas = px.box(
                roas_plot_df,
                x="driver",
                y="roas",
                color="driver",
                points="all",
                title="ROAS Distribution Across Selected Models (paid media)",
            )
            fig_roas.update_layout(showlegend=False)
            st.plotly_chart(fig_roas, use_container_width=True)

            st.subheader("Summary statistics (ROAS)")

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
            st.dataframe(summary_roas, use_container_width=True)

# ============================
# TAB 2: OVER TIME
# ============================
with tab_over_time:
    st.subheader("⏱ Over time analysis")

    # shared frequency selector for both sub-sections
    freq = st.selectbox(
        "Time aggregation",
        options=["Monthly", "Weekly", "Daily"],
        index=0,
        key="freq_over_time",
    )

    if freq == "Monthly":
        def bucket_fn(s):
            return s.dt.to_period("M").dt.to_timestamp()
    elif freq == "Weekly":
        def bucket_fn(s):
            return s.dt.to_period("W").dt.to_timestamp()
    else:
        def bucket_fn(s):
            return s.dt.normalize()

    # ------------------------
    # 2a) Contribution over time (any driver)
    # ------------------------
    st.markdown("### Contribution over time (per driver)")

    drivers_ts_candidates = [d for d in drivers if d in xVec.columns]
    if not drivers_ts_candidates:
        st.info("No drivers from xDecompAgg found as columns in xDecompVecCollect.")
    else:
        driver_contrib_ts = st.selectbox(
            "Driver for contribution over time",
            options=drivers_ts_candidates,
            index=0,
            key="driver_contrib_ts",
        )

        xvec_sub = xVec[xVec["solID"].isin(good_models)].copy()
        if driver_contrib_ts not in xvec_sub.columns:
            st.info(f"Driver '{driver_contrib_ts}' not found in xDecompVecCollect.")
        else:
            xvec_sub["bucket"] = bucket_fn(pd.to_datetime(xvec_sub["ds"]))
            contrib_ts = (
                xvec_sub.groupby(["bucket", "solID"], as_index=False)[driver_contrib_ts]
                .sum()
                .rename(columns={driver_contrib_ts: "contrib_bucket"})
            )

            if contrib_ts.empty:
                st.info("No contribution data over time for this driver.")
            else:
                fig_cts = px.box(
                    contrib_ts,
                    x="bucket",
                    y="contrib_bucket",
                    points="all",
                    title=f"Contribution over time (per {freq.lower()}, across models) — {driver_contrib_ts}",
                )
                fig_cts.update_layout(
                    xaxis_title="Period",
                    yaxis_title="Contribution",
                )
                st.plotly_chart(fig_cts, use_container_width=True)

    st.markdown("---")

    # ------------------------
    # 2b) ROAS over time (paid media only)
    # ------------------------
    st.markdown("### ROAS over time (paid media only)")

    paid_ts_candidates = [d for d in paid_like_cols if d in xVec.columns]
    if not paid_ts_candidates:
        st.info("No paid-media columns (COST/SPEND/EUR/USD) found in both mediaVecCollect and xDecompVecCollect.")
    else:
        driver_roas_ts = st.selectbox(
            "Paid media driver for ROAS over time",
            options=paid_ts_candidates,
            index=0,
            key="driver_roas_ts",
        )

        # contribution time series for this paid driver
        xvec_sub2 = xVec[xVec["solID"].isin(good_models)].copy()
        if driver_roas_ts not in xvec_sub2.columns:
            st.info(f"Driver '{driver_roas_ts}' not found in xDecompVecCollect.")
        else:
            xvec_sub2["bucket"] = bucket_fn(pd.to_datetime(xvec_sub2["ds"]))
            contrib_ts2 = (
                xvec_sub2.groupby(["bucket", "solID"], as_index=False)[driver_roas_ts]
                .sum()
                .rename(columns={driver_roas_ts: "contrib_bucket"})
            )

            # spend time series for this driver
            media_ts = media_long_all[media_long_all["driver"] == driver_roas_ts].copy()
            if "ds" not in media_ts.columns:
                st.info("mediaVecCollect has no 'ds' column for time-based ROAS analysis.")
            else:
                media_ts["bucket"] = bucket_fn(pd.to_datetime(media_ts["ds"]))
                spend_ts2 = (
                    media_ts.groupby(["bucket", "solID"], as_index=False)["spend"]
                    .sum()
                    .rename(columns={"spend": "spend_bucket"})
                )

                ts = contrib_ts2.merge(spend_ts2, on=["bucket", "solID"], how="left")
                ts["roas_bucket"] = np.where(
                    (ts["spend_bucket"] > 0) & np.isfinite(ts["spend_bucket"]),
                    ts["contrib_bucket"] / ts["spend_bucket"],
                    np.nan,
                )
                ts = ts.dropna(subset=["roas_bucket"])

                if ts.empty:
                    st.info("No valid ROAS values over time for this driver (spend zero or missing).")
                else:
                    agg_ts = (
                        ts.groupby("bucket", as_index=False)
                        .agg(
                            median_roas=("roas_bucket", "median"),
                            p25_roas=("roas_bucket", lambda x: np.nanpercentile(x, 25)),
                            p75_roas=("roas_bucket", lambda x: np.nanpercentile(x, 75)),
                            min_roas=("roas_bucket", "min"),
                            max_roas=("roas_bucket", "max"),
                            mean_spend=("spend_bucket", "mean"),
                        )
                        .sort_values("bucket")
                    )

                    # ROAS distribution over time
                    st.markdown(f"**ROAS distribution over time — {driver_roas_ts}**")
                    fig_roas_time = px.box(
                        ts,
                        x="bucket",
                        y="roas_bucket",
                        points="all",
                        title=f"ROAS over time (per {freq.lower()}, across models) — {driver_roas_ts}",
                    )
                    fig_roas_time.update_layout(
                        xaxis_title="Period",
                        yaxis_title="ROAS",
                    )
                    st.plotly_chart(fig_roas_time, use_container_width=True)

                    # Spend line
                    st.markdown(f"**Average spend over time — {driver_roas_ts}**")
                    fig_spend = px.line(
                        agg_ts,
                        x="bucket",
                        y="mean_spend",
                        markers=True,
                        title=f"Mean spend over time (per {freq.lower()}) — {driver_roas_ts}",
                    )
                    fig_spend.update_layout(
                        xaxis_title="Period",
                        yaxis_title="Mean spend",
                    )
                    st.plotly_chart(fig_spend, use_container_width=True)

                    # Summary table by period
                    st.subheader("ROAS over time — summary by period")
                    roas_time_summary = agg_ts.rename(columns={"bucket": "period"})
                    st.dataframe(roas_time_summary, use_container_width=True)