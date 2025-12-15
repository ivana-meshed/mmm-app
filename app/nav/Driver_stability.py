import os
import pandas as pd
import numpy as np
import streamlit as st
import plotly.express as px

from app_shared import download_parquet_from_gcs_cached

# ---------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------
GCS_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")
GCS_PREFIX = "robyn/fethu_beta_run_1/fr/1208_124644/output_models_data"

FILE_XAGG = "xDecompAgg.parquet"
FILE_HYP = "resultHypParam.parquet"
FILE_MEDIA = "mediaVecCollect.parquet"
FILE_XVEC = "xDecompVecCollect.parquet"

# Raw spend Parquet (model input, still local for now)
RAW_SPEND_PARQUET = "/Users/fethullahertugrul/Downloads/datasets_fr_20251208_115448_raw.parquet"

st.set_page_config(page_title="Driver Stability Explorer", layout="wide")
st.title("Driver Stability — Robyn Model Explorer")

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
@st.cache_data
def load_parquet_from_gcs(blob_path: str) -> pd.DataFrame:
    """Load a Parquet file from GCS via app_shared helper (cached)."""
    return download_parquet_from_gcs_cached(GCS_BUCKET, blob_path)


@st.cache_data
def load_raw_spend(path: str) -> pd.DataFrame | None:
    """Load raw spend from local Parquet, if present."""
    if not os.path.exists(path):
        return None
    df = pd.read_parquet(path)
    if "DATE" in df.columns:
        df["DATE"] = pd.to_datetime(df["DATE"])
    return df


def is_paid_like(name: str) -> bool:
    """Heuristic: treat cost/spend/EUR/USD/BUDGET-like columns as paid media (raw spend side)."""
    u = str(name).upper()
    return any(k in u for k in ["COST", "SPEND", "_EUR", "_USD", "BUDGET"])


def canonical_media_name(name: str) -> str:
    """
    Canonical channel root: use first two underscore-separated tokens.

    Examples:
      GA_SUPPLY_COST              -> GA_SUPPLY
      GA_SUPPLY_SESSIONS          -> GA_SUPPLY
      GA_SUPPLY_DAILY_SESSIONS    -> GA_SUPPLY
      META_FEED_IMPR              -> META_FEED
      TV_COSTS_SPOT               -> TV_COSTS
    """
    u = str(name).upper()
    parts = [p for p in u.split("_") if p]
    if len(parts) >= 2:
        return "_".join(parts[:2])
    return u


def make_bucket_fn(freq: str):
    """Return a function that buckets a datetime Series to M / Q / Y."""
    if freq == "Monthly":
        def bucket_fn(s: pd.Series) -> pd.Series:
            return s.dt.to_period("M").dt.to_timestamp()
    elif freq == "Quarterly":
        def bucket_fn(s: pd.Series) -> pd.Series:
            return s.dt.to_period("Q").dt.to_timestamp()
    else:  # Yearly
        def bucket_fn(s: pd.Series) -> pd.Series:
            return s.dt.to_period("Y").dt.to_timestamp()
    return bucket_fn


# ---------------------------------------------------------------------
# Load Robyn exports from GCS
# ---------------------------------------------------------------------
blob_xagg = f"{GCS_PREFIX}/{FILE_XAGG}"
blob_hyp = f"{GCS_PREFIX}/{FILE_HYP}"
blob_media = f"{GCS_PREFIX}/{FILE_MEDIA}"
blob_xvec = f"{GCS_PREFIX}/{FILE_XVEC}"

try:
    xAgg = load_parquet_from_gcs(blob_xagg)
    hyp = load_parquet_from_gcs(blob_hyp)
    media = load_parquet_from_gcs(blob_media)
    xVec = load_parquet_from_gcs(blob_xvec)
except Exception as e:
    st.error(
        "Failed to load Robyn Parquet exports from GCS.\n\n"
        f"Bucket: {GCS_BUCKET}\nPrefix: {GCS_PREFIX}\n\n"
        f"Error: {e}"
    )
    st.stop()

# Raw spend Parquet (for ROAS)
raw_spend = load_raw_spend(RAW_SPEND_PARQUET)
if raw_spend is None:
    st.warning(
        f"Raw spend data not found at:\n{RAW_SPEND_PARQUET}\n\n"
        "ROAS calculations will be disabled; contribution stability still works."
    )

# ---------------------------------------------------------------------
# Basic validation
# ---------------------------------------------------------------------
for col in ["solID", "rn"]:
    if col not in xAgg.columns:
        st.error(f"xDecompAgg is missing required column: {col}")
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
# Sidebar: model selection thresholds (Good / Acceptable / Poor)
# ---------------------------------------------------------------------
st.sidebar.header("Model selection")

quality_level = st.sidebar.select_slider(
    "Minimum quality band",
    options=["Poor", "Acceptable", "Good"],
    value="Acceptable",
)

# Base thresholds per band
if quality_level == "Good":
    rsq_min = 0.70        # R² >= 0.7
    nrmse_max = 0.15      # NRMSE <= 0.15
    decomp_max = 0.10     # decomp.rssd <= 0.1
elif quality_level == "Acceptable":
    rsq_min = 0.50        # R² >= 0.5
    nrmse_max = 0.25      # NRMSE <= 0.25
    decomp_max = 0.20     # decomp.rssd <= 0.2
else:  # "Poor" -> basically no filtering beyond >0
    rsq_min = 0.0
    nrmse_max = 1.0
    decomp_max = 1.0

# Required columns for at least a basic filter
req_cols = ["solID", "rsq_val", "nrmse_val", "decomp.rssd"]
missing = [c for c in req_cols if c not in hyp.columns]
if missing:
    st.error(f"resultHypParam missing columns: {missing}")
    st.stop()

# NaN handling: treat missing metrics as "bad" and exclude those models
hyp_f = hyp.copy()
for c in hyp_f.columns:
    cu = c.lower()
    if cu.startswith("rsq_"):
        hyp_f[c] = hyp_f[c].fillna(0.0)
    elif cu.startswith("nrmse_"):
        hyp_f[c] = hyp_f[c].fillna(1.0)
    elif cu.startswith("decomp.rssd"):
        hyp_f[c] = hyp_f[c].fillna(1.0)

mask = pd.Series(True, index=hyp_f.index)

# Apply thresholds to all train/val/test metrics that exist
for c in hyp_f.columns:
    cu = c.lower()
    if cu.startswith("rsq_"):
        mask &= hyp_f[c] >= rsq_min
    elif cu.startswith("nrmse_"):
        mask &= hyp_f[c] <= nrmse_max
    elif cu.startswith("decomp.rssd"):
        mask &= hyp_f[c] <= decomp_max

good_models = hyp_f.loc[mask, "solID"].unique()

st.write(
    f"Selected {len(good_models)} / {len(hyp)} models "
    f"(quality band: {quality_level} — R² ≥ {rsq_min}, NRMSE ≤ {nrmse_max}, decomp.rssd ≤ {decomp_max})"
)

if len(good_models) == 0:
    st.warning("No models match the selected quality band.")
    st.stop()

# ---------------------------------------------------------------------
# xDecompAgg: contribution shares per model/driver
# ---------------------------------------------------------------------
df = xAgg[xAgg["solID"].isin(good_models)].copy()

# total modeled outcome per model (incl. baseline / non-media drivers)
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
# Paid-media detection: dynamic mapping driver -> spend column
# ---------------------------------------------------------------------
driver_to_spend: dict[str, str] = {}

if (raw_spend is not None) and (not raw_spend.empty):
    spend_cols_raw = [c for c in raw_spend.columns if is_paid_like(c)]
    spend_root_map = {c: canonical_media_name(c) for c in spend_cols_raw}

    for d in drivers:
        d_root = canonical_media_name(d)
        if not d_root:
            continue
        match = next(
            (sc for sc, sr in spend_root_map.items() if sr == d_root),
            None,
        )
        if match is not None:
            driver_to_spend[d] = match

paid_like_drivers = sorted(driver_to_spend.keys())

# ---------------------------------------------------------------------
# Tabs: Drivers vs ROAS
# ---------------------------------------------------------------------
tab_drivers, tab_roas = st.tabs(["Drivers", "ROAS"])

# ============================
# TAB: DRIVERS
# ============================
with tab_drivers:
    st.subheader("Driver contribution — overall")

    # default: any driver that looks like paid-ish
    default_drivers = [
        d for d in drivers
        if any(
            k in d.upper()
            for k in ["COST", "SPEND", "SESS", "SESSION", "IMPR", "CLICK"]
        )
    ][:6] or drivers[:6]

    sel_drivers = st.multiselect(
        "Drivers to show",
        options=drivers,
        default=default_drivers,
        key="drivers_sel_overall",
    )

    if not sel_drivers:
        st.stop()

    plot_df = df[df["driver"].isin(sel_drivers)].copy()

    # Contribution share boxplot (share of total modeled outcome)
    fig_share = px.box(
        plot_df,
        x="driver",
        y="share",
        color="driver",
        points="all",
        title="Driver contribution share (of total outcome) across selected models",
    )
    fig_share.update_layout(showlegend=False)
    st.plotly_chart(fig_share, use_container_width=True)

    # Summary (share of total)
    st.subheader("Summary statistics (contribution share of total)")

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

    # Paid-only share (share of paid pool)
    paid_plot_drivers = [d for d in sel_drivers if d in paid_like_drivers]
    if paid_plot_drivers:
        st.subheader("Paid-media shares — of total vs of paid-only")

        df_paid = df[df["driver"].isin(paid_like_drivers)].copy()
        totals_paid = (
            df_paid.groupby("solID")[val_col]
            .sum()
            .rename("total_response_paid")
            .reset_index()
        )
        df_paid = df_paid.merge(totals_paid, on="solID", how="left")
        df_paid["share_of_paid"] = np.where(
            df_paid["total_response_paid"] > 0,
            df_paid[val_col] / df_paid["total_response_paid"],
            np.nan,
        )

        summary_share_paid = (
            df_paid[df_paid["driver"].isin(paid_plot_drivers)]
            .groupby("driver")
            .agg(
                n_models=("solID", "nunique"),
                mean_share_of_total=("share", "mean"),
                mean_share_of_paid=("share_of_paid", "mean"),
                sd_share_of_paid=("share_of_paid", "std"),
            )
            .reset_index()
            .sort_values("mean_share_of_paid", ascending=False)
        )
        st.dataframe(summary_share_paid, use_container_width=True)
    else:
        st.info("No selected drivers are classified as paid media for share-of-paid view.")

    # ------------------------
    # Contribution over time
    # ------------------------
    st.subheader("Driver contribution over time")

    freq_drivers = st.selectbox(
        "Time aggregation",
        options=["Monthly", "Quarterly", "Yearly"],
        index=0,
        key="freq_drivers",
    )
    bucket_fn_drivers = make_bucket_fn(freq_drivers)

    drivers_ts_candidates = [d for d in drivers if d in xVec.columns]
    if not drivers_ts_candidates:
        st.info("No drivers from xDecompAgg found as columns in xDecompVecCollect.")
    else:
        driver_contrib_ts = st.selectbox(
            "Driver",
            options=drivers_ts_candidates,
            index=0,
            key="driver_contrib_ts",
        )

        xvec_sub = xVec[xVec["solID"].isin(good_models)].copy()
        if driver_contrib_ts not in xvec_sub.columns:
            st.info(f"Driver '{driver_contrib_ts}' not found in xDecompVecCollect.")
        else:
            xvec_sub["bucket"] = bucket_fn_drivers(pd.to_datetime(xvec_sub["ds"]))
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
                    title=(
                        f"Contribution over time (per {freq_drivers.lower()}, across models) "
                        f"— {driver_contrib_ts}"
                    ),
                )
                fig_cts.update_layout(
                    xaxis_title="Period",
                    yaxis_title="Contribution",
                )
                st.plotly_chart(fig_cts, use_container_width=True)

# ============================
# TAB: ROAS
# ============================
with tab_roas:
    st.subheader("ROAS and mROAS — overall")

    if raw_spend is None or raw_spend.empty or not paid_like_drivers:
        st.info("ROAS requires raw spend and at least one paid-media driver.")
    else:
        sel_roas_drivers = st.multiselect(
            "Paid drivers to show",
            options=paid_like_drivers,
            default=paid_like_drivers[:6] if len(paid_like_drivers) > 0 else [],
            key="drivers_sel_roas",
        )

        if sel_roas_drivers:
            # Contributions per (solID, driver) over full calibration period
            contrib = (
                df[df["driver"].isin(sel_roas_drivers)]
                .groupby(["solID", "driver"], as_index=False)[val_col]
                .sum()
                .rename(columns={val_col: "contrib"})
            )

            # Total raw spend per driver
            rows = []
            for d in sel_roas_drivers:
                spend_col = driver_to_spend.get(d)
                if spend_col is None or spend_col not in raw_spend.columns:
                    continue
                total_spend = raw_spend[spend_col].sum()
                rows.append({"driver": d, "total_spend_raw": total_spend})
            spend_totals_raw = pd.DataFrame(rows)

            roas_df = contrib.merge(spend_totals_raw, on="driver", how="left")
            roas_df["roas"] = np.where(
                (roas_df["total_spend_raw"] > 0) & np.isfinite(roas_df["total_spend_raw"]),
                roas_df["contrib"] / roas_df["total_spend_raw"],
                np.nan,
            )

            # Placeholder mROAS: currently same as ROAS until saturation is wired in
            # Conceptually: this is ROAS at "average historical spend" for the channel.
            roas_df["mroas"] = roas_df["roas"]

            roas_plot_df = roas_df.dropna(subset=["roas"])

            if roas_plot_df.empty:
                st.info("No valid ROAS values (raw spend is zero or missing for selected drivers).")
            else:
                # ROAS boxplot
                fig_roas = px.box(
                    roas_plot_df,
                    x="driver",
                    y="roas",
                    color="driver",
                    points="all",
                    title="ROAS distribution across selected models (paid media, raw spend)",
                )
                fig_roas.update_layout(showlegend=False)
                st.plotly_chart(fig_roas, use_container_width=True)

                # mROAS boxplot (same values for now, but separate visual channel)
                fig_mroas = px.box(
                    roas_plot_df,
                    x="driver",
                    y="mroas",
                    color="driver",
                    points="all",
                    title="mROAS distribution across selected models (proxy = ROAS for now)",
                )
                fig_mroas.update_layout(showlegend=False)
                st.plotly_chart(fig_mroas, use_container_width=True)

                # Summary (ROAS + proxy mROAS)
                st.subheader("Summary statistics (ROAS and proxy mROAS)")

                summary_roas = (
                    roas_plot_df.groupby("driver")
                    .agg(
                        n_models=("solID", "nunique"),
                        mean_roas=("roas", "mean"),
                        median_roas=("roas", "median"),
                        sd_roas=("roas", "std"),
                        min_roas=("roas", "min"),
                        max_roas=("roas", "max"),
                        mean_mroas=("mroas", "mean"),
                        median_mroas=("mroas", "median"),
                    )
                    .reset_index()
                    .sort_values("mean_roas", ascending=False)
                )
                st.dataframe(summary_roas, use_container_width=True)

                st.caption(
                    "mROAS is currently a proxy (equal to ROAS at average historical spend). "
                    "Once saturation parameters are exported into Parquet, this can be replaced "
                    "by true marginal ROAS."
                )

        # ------------------------
        # ROAS over time
        # ------------------------
        st.subheader("ROAS and mROAS over time")

        freq_roas = st.selectbox(
            "Time aggregation",
            options=["Monthly", "Quarterly", "Yearly"],
            index=0,
            key="freq_roas",
        )
        bucket_fn_roas = make_bucket_fn(freq_roas)

        paid_ts_candidates = [d for d in paid_like_drivers if d in xVec.columns]
        if not paid_ts_candidates:
            st.info(
                "No paid-media drivers found that are present both in xDecompVecCollect and raw spend."
            )
        else:
            driver_roas_ts = st.selectbox(
                "Paid media driver",
                options=paid_ts_candidates,
                index=0,
                key="driver_roas_ts",
            )

            xvec_sub2 = xVec[xVec["solID"].isin(good_models)].copy()
            if driver_roas_ts not in xvec_sub2.columns:
                st.info(f"Driver '{driver_roas_ts}' not found in xDecompVecCollect.")
            else:
                xvec_sub2["bucket"] = bucket_fn_roas(pd.to_datetime(xvec_sub2["ds"]))
                contrib_ts2 = (
                    xvec_sub2.groupby(["bucket", "solID"], as_index=False)[driver_roas_ts]
                    .sum()
                    .rename(columns={driver_roas_ts: "contrib_bucket"})
                )

                if "DATE" not in raw_spend.columns:
                    st.info("Raw spend Parquet has no 'DATE' column for time-based ROAS analysis.")
                else:
                    raw_spend_bucketed = raw_spend.copy()
                    raw_spend_bucketed["bucket"] = bucket_fn_roas(raw_spend_bucketed["DATE"])

                    spend_col = driver_to_spend.get(driver_roas_ts)
                    if not spend_col or spend_col not in raw_spend_bucketed.columns:
                        st.info(
                            f"No matching spend column found for driver '{driver_roas_ts}' in raw_spend."
                        )
                    else:
                        spend_ts2 = (
                            raw_spend_bucketed
                            .groupby("bucket", as_index=False)[spend_col]
                            .sum()
                            .rename(columns={spend_col: "spend_bucket_raw"})
                        )

                        ts = contrib_ts2.merge(spend_ts2, on="bucket", how="left")
                        ts["roas_bucket"] = np.where(
                            (ts["spend_bucket_raw"] > 0) & np.isfinite(ts["spend_bucket_raw"]),
                            ts["contrib_bucket"] / ts["spend_bucket_raw"],
                            np.nan,
                        )
                        # placeholder mROAS over time (same as ROAS for now)
                        ts["mroas_bucket"] = ts["roas_bucket"]

                        ts = ts.dropna(subset=["roas_bucket"])

                        if ts.empty:
                            st.info("No valid ROAS values over time for this driver (raw spend zero or missing).")
                        else:
                            agg_ts = (
                                ts.groupby("bucket", as_index=False)
                                .agg(
                                    median_roas=("roas_bucket", "median"),
                                    p25_roas=("roas_bucket", lambda x: np.nanpercentile(x, 25)),
                                    p75_roas=("roas_bucket", lambda x: np.nanpercentile(x, 75)),
                                    min_roas=("roas_bucket", "min"),
                                    max_roas=("roas_bucket", "max"),
                                    median_mroas=("mroas_bucket", "median"),
                                    mean_spend=("spend_bucket_raw", "mean"),
                                )
                                .sort_values("bucket")
                            )

                            # ROAS over time — boxplot across models
                            fig_roas_time = px.box(
                                ts,
                                x="bucket",
                                y="roas_bucket",
                                points="all",
                                title=(
                                    f"ROAS over time (per {freq_roas.lower()}, across models) "
                                    f"— {driver_roas_ts}"
                                ),
                            )
                            fig_roas_time.update_layout(
                                xaxis_title="Period",
                                yaxis_title="ROAS",
                            )
                            st.plotly_chart(fig_roas_time, use_container_width=True)

                            # mROAS over time — median line (same values for now)
                            fig_mroas_time = px.line(
                                agg_ts,
                                x="bucket",
                                y="median_mroas",
                                markers=True,
                                title=(
                                    f"Median mROAS over time (per {freq_roas.lower()}) "
                                    f"— {driver_roas_ts}"
                                ),
                            )
                            fig_mroas_time.update_layout(
                                xaxis_title="Period",
                                yaxis_title="Median mROAS",
                            )
                            st.plotly_chart(fig_mroas_time, use_container_width=True)

                            # Spend line
                            fig_spend = px.line(
                                agg_ts,
                                x="bucket",
                                y="mean_spend",
                                markers=True,
                                title=(
                                    f"Mean raw spend over time (per {freq_roas.lower()}) "
                                    f"— {driver_roas_ts}"
                                ),
                            )
                            fig_spend.update_layout(
                                xaxis_title="Period",
                                yaxis_title="Mean spend",
                            )
                            st.plotly_chart(fig_spend, use_container_width=True)

                            # Summary table by period
                            st.subheader("ROAS and mROAS over time — summary by period")
                            roas_time_summary = agg_ts.rename(columns={"bucket": "period"})
                            st.dataframe(roas_time_summary, use_container_width=True)

                            st.caption(
                                "mROAS columns are currently proxies (equal to ROAS at bucket-level spend). "
                                "They can be replaced by true marginal ROAS once saturation parameters "
                                "are exported into Parquet."
                            )