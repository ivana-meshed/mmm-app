import os
import re
import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px  # kept (used for bar)
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from app_shared import download_parquet_from_gcs_cached,require_login_and_domain
from app_split_helpers import ensure_session_defaults


#require_login_and_domain()
ensure_session_defaults()

# ---------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------
GCS_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")
GCS_PREFIX = "robyn/fethu_beta_run_1/fr/1208_124644/output_models_data"

FILE_XAGG = "xDecompAgg.parquet"
FILE_HYP = "resultHypParam.parquet"
FILE_MEDIA = "mediaVecCollect.parquet"
FILE_XVEC = "xDecompVecCollect.parquet"

# Raw spend parquet (business ROAS denominator)
RAW_SPEND_PARQUET = os.getenv(
    "RAW_SPEND_PARQUET",
    "/Users/fethullahertugrul/Downloads/datasets_fr_20251208_115448_raw.parquet",
)

# Best model id text file is in the RUN folder, NOT output_models_data/
# GCS_PREFIX = robyn/.../1208_124644/output_models_data
RUN_PREFIX = GCS_PREFIX.rsplit("/output_models_data", 1)[0]
BEST_MODEL_BLOB = f"{RUN_PREFIX}/best_model_id.txt"

st.set_page_config(page_title="Review Model Stability", layout="wide")
st.title("Review Model Stability")

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
@st.cache_data
def load_parquet_from_gcs(blob_path: str) -> pd.DataFrame:
    return download_parquet_from_gcs_cached(GCS_BUCKET, blob_path)


@st.cache_data
def load_raw_spend(path: str) -> pd.DataFrame | None:
    if not path or not os.path.exists(path):
        return None
    df = pd.read_parquet(path)
    if "DATE" in df.columns:
        df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
    return df


def to_ts(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


def detect_val_col(xagg: pd.DataFrame) -> str:
    for c in ["xDecompAgg", "xDecomp", "xDecomp_total", "xDecompAggRF"]:
        if c in xagg.columns:
            return c
    st.error(
        "No contribution column found in xDecompAgg "
        "(tried xDecompAgg/xDecomp/xDecomp_total/xDecompAggRF)."
    )
    st.stop()


def make_bucket_fn(freq: str):
    if freq == "Monthly":
        return lambda s: s.dt.to_period("M").dt.to_timestamp()
    if freq == "Quarterly":
        return lambda s: s.dt.to_period("Q").dt.to_timestamp()
    return lambda s: s.dt.to_period("Y").dt.to_timestamp()


def canonical_media_name(name: str) -> str:
    u = str(name).upper()
    parts = [p for p in u.split("_") if p]
    return "_".join(parts[:2]) if len(parts) >= 2 else u


def is_paid_like(name: str) -> bool:
    u = str(name).upper()
    return any(k in u for k in ["COST", "SPEND", "_EUR", "_USD", "BUDGET"])


@st.cache_data
def load_text_from_gcs(blob_path: str) -> str:
    # Try gcsfs first
    try:
        import gcsfs  # type: ignore

        fs = gcsfs.GCSFileSystem()
        with fs.open(f"{GCS_BUCKET}/{blob_path}", "r") as f:
            return f.read()
    except Exception:
        pass

    # Fallback: google-cloud-storage
    try:
        from google.cloud import storage  # type: ignore

        client = storage.Client()
        bucket = client.bucket(GCS_BUCKET)
        blob = bucket.blob(blob_path)
        return blob.download_as_text()
    except Exception as e:
        raise RuntimeError(f"Could not read gs://{GCS_BUCKET}/{blob_path}: {e}")


@st.cache_data
def try_read_best_model_id() -> tuple[str, str]:
    """
    Returns (best_model_id, debug_message).
    best_model_id = "" if not readable.
    """
    try:
        txt = load_text_from_gcs(BEST_MODEL_BLOB)
        if not txt:
            return "", f"Empty file at gs://{GCS_BUCKET}/{BEST_MODEL_BLOB}"
        first = txt.splitlines()[0].strip()
        m = re.match(r"^\s*([0-9]+_[0-9]+_[0-9]+)\s*$", first)
        best = m.group(1) if m else first
        return best, f"Read OK from gs://{GCS_BUCKET}/{BEST_MODEL_BLOB}"
    except Exception as e:
        return "", f"Failed reading gs://{GCS_BUCKET}/{BEST_MODEL_BLOB} — {e}"


def build_share_summary(contrib_driver: pd.DataFrame, drivers: list[str]) -> pd.DataFrame:
    """
    Per-driver stats with missing drivers per model treated as 0 share.
    If you include ALL drivers, sum(mean_share) ~= 1.
    """
    mat = (
        contrib_driver[contrib_driver["driver"].isin(drivers)]
        .pivot_table(index="solID", columns="driver", values="share", aggfunc="sum")
        .fillna(0.0)
    )

    out = []
    for d in mat.columns:
        s = mat[d].values.astype(float)
        out.append(
            {
                "driver": d,
                "mean_share": float(np.mean(s)),
                "median_share": float(np.median(s)),
                "sd_share": float(np.std(s, ddof=1)) if len(s) > 1 else 0.0,
                "min_share": float(np.min(s)) if len(s) else np.nan,
                "max_share": float(np.max(s)) if len(s) else np.nan,
            }
        )
    return pd.DataFrame(out).sort_values("mean_share", ascending=False)


def box_with_best_dot(
    df_plot: pd.DataFrame,
    x: str,
    y: str,
    best_id: str,
    title: str,
    y_title: str,
    x_title: str | None = None,
):
    """
    Box per category + all points + highlighted best-model points.
    No legend.
    """
    if df_plot.empty:
        return go.Figure()

    fig = go.Figure()

    cats = list(pd.unique(df_plot[x]))
    for cat in cats:
        s = df_plot.loc[df_plot[x] == cat, y].dropna()
        if len(s) == 0:
            continue
        fig.add_trace(
            go.Box(
                x=[cat] * len(s),
                y=s,
                name=str(cat),
                boxpoints=False,
                showlegend=False,
            )
        )

    fig.add_trace(
        go.Scatter(
            x=df_plot[x],
            y=df_plot[y],
            mode="markers",
            marker=dict(size=6, opacity=0.35),
            showlegend=False,
            hovertemplate=f"{x}=%{{x}}<br>{y}=%{{y}}<br>solID=%{{customdata}}<extra></extra>",
            customdata=df_plot["solID"].astype(str),
        )
    )

    if best_id:
        best_pts = df_plot[df_plot["solID"].astype(str) == str(best_id)]
        if not best_pts.empty:
            fig.add_trace(
                go.Scatter(
                    x=best_pts[x],
                    y=best_pts[y],
                    mode="markers",
                    marker=dict(size=12, opacity=1.0),
                    showlegend=False,
                    hovertemplate=f"BEST<br>{x}=%{{x}}<br>{y}=%{{y}}<br>solID=%{{customdata}}<extra></extra>",
                    customdata=best_pts["solID"].astype(str),
                )
            )

    fig.update_layout(
        title=title,
        xaxis_title=(x_title or x),
        yaxis_title=y_title,
        showlegend=False,
    )
    return fig


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

raw_spend = load_raw_spend(RAW_SPEND_PARQUET)
if raw_spend is None or raw_spend.empty:
    st.warning(
        f"Raw spend data not found at:\n{RAW_SPEND_PARQUET}\n\n"
        "Business ROAS (raw currency) will be disabled. Driver shares still work."
    )

best_model_id, best_dbg = try_read_best_model_id()
if not best_model_id:
    st.warning(
        "best_model_id.txt could not be auto-read.\n"
        f"- attempted: gs://{GCS_BUCKET}/{BEST_MODEL_BLOB}\n"
        f"- debug: {best_dbg}\n"
        "Fix: ensure the blob exists + your env can read GCS (gcsfs or google-cloud-storage + auth)."
    )
else:
    st.caption(f"Best model auto-highlight: **{best_model_id}**")

# ---------------------------------------------------------------------
# Basic validation
# ---------------------------------------------------------------------
for col in ["solID", "rn"]:
    if col not in xAgg.columns:
        st.error(f"xDecompAgg is missing required column: {col}")
        st.stop()

if "solID" not in hyp.columns:
    st.error("resultHypParam is missing required column: solID")
    st.stop()

for col in ["solID", "ds"]:
    if col not in xVec.columns:
        st.error("xDecompVecCollect is missing required columns ('solID', 'ds').")
        st.stop()

if "solID" not in media.columns:
    st.error("mediaVecCollect is missing required column: solID")
    st.stop()

val_col = detect_val_col(xAgg)

# ---------------------------------------------------------------------
# Sidebar: model selection (time window ALWAYS ON now)
# ---------------------------------------------------------------------
st.sidebar.header("Model selection")

mode = st.sidebar.selectbox("Model Quality:", ["Good", "Acceptable", "All", "Custom"], index=1)

preset = {
    "Good": {"rsq_min": 0.70, "nrmse_max": 0.15, "decomp_max": 0.10},
    "Acceptable": {"rsq_min": 0.50, "nrmse_max": 0.25, "decomp_max": 0.20},
    "All": {"rsq_min": 0.00, "nrmse_max": 1.00, "decomp_max": 1.00},
}

if mode == "Custom":
    rsq_min = st.sidebar.slider("Min R²", 0.0, 1.0, 0.50, 0.01)
    nrmse_max = st.sidebar.slider("Max NRMSE", 0.0, 1.0, 0.25, 0.01)
    decomp_max = st.sidebar.slider("Max decomp.rssd", 0.0, 1.0, 0.20, 0.01)
else:
    rsq_min = preset[mode]["rsq_min"]
    nrmse_max = preset[mode]["nrmse_max"]
    decomp_max = preset[mode]["decomp_max"]
    st.sidebar.caption(
        f"Preset thresholds: R² ≥ {rsq_min}, NRMSE ≤ {nrmse_max}, decomp.rssd ≤ {decomp_max}"
    )

# ALWAYS ON
limit_to_spend_window = True

# Fill NaNs consistently
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
for c in hyp_f.columns:
    cu = c.lower()
    if cu.startswith("rsq_"):
        mask &= hyp_f[c] >= rsq_min
    elif cu.startswith("nrmse_"):
        mask &= hyp_f[c] <= nrmse_max
    elif cu.startswith("decomp.rssd"):
        mask &= hyp_f[c] <= decomp_max

good_models = hyp_f.loc[mask, "solID"].astype(str).unique()
st.write(f"Selected **{len(good_models)} / {len(hyp)}** models (mode: **{mode}**)")

if len(good_models) == 0:
    st.warning("No models match the selected thresholds.")
    st.stop()

# ---------------------------------------------------------------------
# Filter to selected models
# ---------------------------------------------------------------------
xAgg_gm = xAgg[xAgg["solID"].astype(str).isin(good_models)].copy()
media_gm = media[media["solID"].astype(str).isin(good_models)].copy()
xVec_gm = xVec[xVec["solID"].astype(str).isin(good_models)].copy()

xVec_gm["ds"] = to_ts(xVec_gm["ds"])

# ---------------------------------------------------------------------
# Core derivations
# ---------------------------------------------------------------------
xAgg_gm["solID"] = xAgg_gm["solID"].astype(str)
xAgg_gm["driver"] = xAgg_gm["rn"].astype(str)

contrib_driver = (
    xAgg_gm.groupby(["solID", "driver"], as_index=False)[val_col]
    .sum()
    .rename(columns={val_col: "contrib"})
)

total_resp = (
    contrib_driver.groupby("solID", as_index=False)["contrib"]
    .sum()
    .rename(columns={"contrib": "total_response"})
)

contrib_driver = contrib_driver.merge(total_resp, on="solID", how="left")
contrib_driver["share"] = np.where(
    contrib_driver["total_response"] > 0,
    contrib_driver["contrib"] / contrib_driver["total_response"],
    0.0,
)

all_drivers = sorted(contrib_driver["driver"].unique())

# mediaVecCollect long (defines "paid pool"; not ROAS denom)
id_vars_media = [c for c in ["ds", "solID", "type"] if c in media_gm.columns]
value_cols_media = [c for c in media_gm.columns if c not in id_vars_media]

media_long = media_gm.melt(
    id_vars=id_vars_media,
    value_vars=value_cols_media,
    var_name="driver",
    value_name="media_value",
)
media_long["solID"] = media_long["solID"].astype(str)
media_drivers = sorted(media_long["driver"].unique())

# Raw spend mapping driver -> spend column
driver_to_spend: dict[str, str] = {}
if raw_spend is not None and not raw_spend.empty:
    spend_cols = [c for c in raw_spend.columns if c != "DATE" and is_paid_like(c)]
    spend_root_map = {c: canonical_media_name(c) for c in spend_cols}

    for d in media_drivers:
        d_root = canonical_media_name(d)
        match = next((sc for sc, sr in spend_root_map.items() if sr == d_root), None)
        if match:
            driver_to_spend[d] = match

paid_like_drivers = sorted(driver_to_spend.keys())

# ---------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------
tab_drivers, tab_roas = st.tabs(["Drivers", "ROAS"])

# =====================================================================
# DRIVERS TAB
# =====================================================================
with tab_drivers:
    st.subheader("Driver share stability across models")

    default_drivers = [d for d in all_drivers if "COST" in d.upper()][:6] or all_drivers[:6]
    sel_drivers = st.multiselect(
        "Select Drivers",
        options=all_drivers,
        default=default_drivers,
        key="drivers_share",
    )
    if not sel_drivers:
        st.stop()

    plot_df = contrib_driver[contrib_driver["driver"].isin(sel_drivers)].copy()

    if len(sel_drivers) == len(all_drivers):
        chk = (
            contrib_driver.groupby("solID", as_index=False)["share"]
            .sum()
            .rename(columns={"share": "sum_share"})
        )
    st.caption(f"Note: Best model is highlighted with larger dots: **{best_model_id}**")
    fig_share = box_with_best_dot(
        df_plot=plot_df,
        x="driver",
        y="share",
        best_id=best_model_id,
        title="Driver share across selected models",
        y_title="Share",
    )
    st.plotly_chart(fig_share, use_container_width=True)

    c1, c2 = st.columns(2)

    with c1:
        st.subheader("Driver Summary - % Total Contribution")
        summary_share = build_share_summary(contrib_driver, sel_drivers)
        st.dataframe(summary_share, use_container_width=True)

    with c2:
        st.subheader("Paid-media Summary - Total vs Paid Effect")
        paid_plot = [d for d in sel_drivers if d in set(media_drivers)]
        if not paid_plot:
            st.info("None of your selected drivers are in mediaVecCollect (paid pool).")
        else:
            paid_contrib = contrib_driver[contrib_driver["driver"].isin(media_drivers)].copy()
            paid_tot = (
                paid_contrib.groupby("solID", as_index=False)["contrib"]
                .sum()
                .rename(columns={"contrib": "paid_total_response"})
            )
            paid_contrib = paid_contrib.merge(paid_tot, on="solID", how="left")
            paid_contrib["share_of_paid"] = np.where(
                paid_contrib["paid_total_response"] > 0,
                paid_contrib["contrib"] / paid_contrib["paid_total_response"],
                0.0,
            )

            mat_total = (
                paid_contrib[paid_contrib["driver"].isin(paid_plot)]
                .pivot_table(index="solID", columns="driver", values="share", aggfunc="sum")
                .fillna(0.0)
            )
            mat_paid = (
                paid_contrib[paid_contrib["driver"].isin(paid_plot)]
                .pivot_table(index="solID", columns="driver", values="share_of_paid", aggfunc="sum")
                .fillna(0.0)
            )

            rows = []
            for d in paid_plot:
                rows.append(
                    {
                        "driver": d,
                        "mean_share_of_total": float(mat_total[d].mean()) if d in mat_total.columns else 0.0,
                        "mean_share_of_paid": float(mat_paid[d].mean()) if d in mat_paid.columns else 0.0,
                        "sd_share_of_paid": float(mat_paid[d].std(ddof=1)) if d in mat_paid.columns and len(mat_paid[d]) > 1 else 0.0,
                    }
                )
            paid_summary = pd.DataFrame(rows).sort_values("mean_share_of_paid", ascending=False)
            st.dataframe(paid_summary, use_container_width=True)

    st.markdown("---")
    st.subheader("Driver contribution over time")

    freq = st.selectbox("Time aggregation", ["Monthly", "Quarterly", "Yearly"], index=0)
    bucket_fn = make_bucket_fn(freq)

    drivers_ts_candidates = [d for d in all_drivers if d in xVec_gm.columns]
    if not drivers_ts_candidates:
        st.info("No drivers from xDecompAgg found as columns in xDecompVecCollect.")
    else:
        driver_ts = st.selectbox("Driver (time series)", options=drivers_ts_candidates, index=0)

        xsub = xVec_gm[["ds", "solID", driver_ts]].copy()
        xsub["bucket"] = bucket_fn(xsub["ds"])

        # numerator: driver contribution per (bucket, solID)
        ts_units = (
            xsub.groupby(["bucket", "solID"], as_index=False)[driver_ts]
            .sum()
            .rename(columns={driver_ts: "contrib_bucket"})
        )

        # denominator: total modeled outcome per (bucket, solID) from xVec (sum over all driver columns)
        # note: exclude id/time cols; keep only numeric contribution columns
        id_cols = {"ds", "solID", "dep_var", "y", "yhat", "ts", "date"}
        candidate_cols = [c for c in xVec_gm.columns if c not in id_cols]

        # only sum numeric cols (defensive)
        numeric_cols = [c for c in candidate_cols if pd.api.types.is_numeric_dtype(xVec_gm[c])]
        denom = xVec_gm[["ds", "solID"] + numeric_cols].copy()
        denom["bucket"] = bucket_fn(denom["ds"])

        ts_total = (
            denom.groupby(["bucket", "solID"], as_index=False)[numeric_cols]
            .sum()
        )
        ts_total["total_bucket"] = ts_total[numeric_cols].sum(axis=1)
        ts_total = ts_total[["bucket", "solID", "total_bucket"]]

        ts = ts_units.merge(ts_total, on=["bucket", "solID"], how="left")
        ts["share_bucket"] = np.where(
            ts["total_bucket"] > 0,
            ts["contrib_bucket"] / ts["total_bucket"],
            np.nan,
        )

        c1, c2 = st.columns(2)

        with c1:
            fig_units = px.box(
                ts,
                x="bucket",
                y="contrib_bucket",
                points="all",
                title=f"Total Contribution over time — {driver_ts}",
            )
            fig_units.update_layout(xaxis_title="Period", yaxis_title="Contribution (outcome units)")
            st.plotly_chart(fig_units, use_container_width=True)

        with c2:
            fig_share = px.box(
                ts.dropna(subset=["share_bucket"]),
                x="bucket",
                y="share_bucket",
                points="all",
                title=f"Percentage Contribution over time — {driver_ts}",
            )
            fig_share.update_layout(xaxis_title="Period", yaxis_title="Share of modeled outcome")
            st.plotly_chart(fig_share, use_container_width=True)

# =====================================================================
# ROAS TAB (RAW SPEND)
# =====================================================================
with tab_roas:
    st.subheader("ROAS stability")
    if raw_spend is None or raw_spend.empty:
        st.info("Raw spend parquet not available. Set RAW_SPEND_PARQUET env var or update RAW_SPEND_PARQUET path.")
        st.stop()

    if "DATE" not in raw_spend.columns:
        st.error("Raw spend parquet must have a DATE column for over-time analysis.")
        st.stop()

    if not paid_like_drivers:
        st.info("No paid drivers could be mapped to raw spend columns (cost/spend/EUR/USD/BUDGET).")
        st.stop()

    # enforce deterministic order + default selection
    paid_like_drivers = sorted(paid_like_drivers)
    default_roas = sorted([d for d in paid_like_drivers if "COST" in d.upper()] or paid_like_drivers)[:6]
    st.caption(f"Note: Best model is highlighted with larger dots: **{best_model_id}**")
    sel_roas_drivers = st.multiselect(
        "Select Paid Drivers",
        options=paid_like_drivers,
        default=default_roas,
        key="roas_drivers",
    )
    if not sel_roas_drivers:
        st.stop()
    sel_roas_drivers = sorted(sel_roas_drivers)

    contrib = (
        contrib_driver[contrib_driver["driver"].isin(sel_roas_drivers)]
        .groupby(["solID", "driver"], as_index=False)["contrib"]
        .sum()
    )

    spend_rows = []
    for d in sel_roas_drivers:
        spend_col = driver_to_spend.get(d)
        if not spend_col or spend_col not in raw_spend.columns:
            continue
        spend_rows.append({"driver": d, "total_spend_raw": float(raw_spend[spend_col].sum())})
    spend_totals = pd.DataFrame(spend_rows)

    roas_df = contrib.merge(spend_totals, on="driver", how="left")
    roas_df["roas"] = np.where(
        (roas_df["total_spend_raw"] > 0) & np.isfinite(roas_df["total_spend_raw"]),
        roas_df["contrib"] / roas_df["total_spend_raw"],
        np.nan,
    )
    roas_plot = roas_df.dropna(subset=["roas"]).copy()

    if roas_plot.empty:
        st.info("No valid ROAS values (missing/zero spend after mapping).")
        st.stop()

    # align ordering across ROAS + spend charts
    roas_plot["driver"] = pd.Categorical(roas_plot["driver"], categories=sel_roas_drivers, ordered=True)
    roas_plot = roas_plot.sort_values("driver")

    spend_show = (
        spend_totals.set_index("driver")
        .reindex(sel_roas_drivers)
        .reset_index()
    )

    c1, c2 = st.columns(2)

    with c1:
        fig_roas = box_with_best_dot(
            df_plot=roas_plot,
            x="driver",
            y="roas",
            best_id=best_model_id,
            title="ROAS distribution across selected models",
            y_title="ROAS",
        )
        st.plotly_chart(fig_roas, use_container_width=True)

    with c2:
        # alphabetical order to match ROAS chart
        fig_sp = px.bar(
            spend_show,
            x="driver",
            y="total_spend_raw",
            title="Total spend by channel",
        )
        fig_sp.update_layout(xaxis_title="Driver", yaxis_title="Total spend")
        st.plotly_chart(fig_sp, use_container_width=True)

    st.subheader("ROAS Summary")
    summary_roas = (
        roas_plot.groupby("driver", as_index=False)
        .agg(
            mean_roas=("roas", "mean"),
            median_roas=("roas", "median"),
            sd_roas=("roas", "std"),
            min_roas=("roas", "min"),
            max_roas=("roas", "max"),
        )
        .sort_values("mean_roas", ascending=False)
    )
    st.dataframe(summary_roas, use_container_width=True)

    st.markdown("---")
    st.subheader("ROAS and Spend over time ")

    freq2 = st.selectbox("Time aggregation", ["Monthly", "Quarterly", "Yearly"], index=0, key="roas_ot_freq")
    bucket_fn2 = make_bucket_fn(freq2)

    ts_candidates = sorted(
        [
            d
            for d in sel_roas_drivers
            if d in xVec_gm.columns and (driver_to_spend.get(d) in raw_spend.columns)
        ]
    )
    if not ts_candidates:
        st.info("None of the selected paid drivers exist in xDecompVecCollect AND map to a raw spend column.")
        st.stop()

    driver_ot = st.selectbox("Driver", options=ts_candidates, index=0, key="roas_ot_driver")
    spend_col = driver_to_spend[driver_ot]

    xsub = xVec_gm[["ds", "solID", driver_ot]].copy()
    xsub["bucket"] = bucket_fn2(xsub["ds"])
    contrib_ot = (
        xsub.groupby(["bucket", "solID"], as_index=False)[driver_ot]
        .sum()
        .rename(columns={driver_ot: "contrib_bucket"})
    )

    rs = raw_spend.copy()
    rs["bucket"] = bucket_fn2(to_ts(rs["DATE"]))
    spend_ot = (
        rs.groupby("bucket", as_index=False)[spend_col]
        .sum()
        .rename(columns={spend_col: "spend_bucket_raw"})
        .sort_values("bucket")
    )

    # ALWAYS trim x-axis to spend-active window
    spend_active = spend_ot[spend_ot["spend_bucket_raw"] > 0].copy()
    if not spend_active.empty:
        min_b = spend_active["bucket"].min()
        max_b = spend_active["bucket"].max()
        contrib_ot = contrib_ot[(contrib_ot["bucket"] >= min_b) & (contrib_ot["bucket"] <= max_b)].copy()
        spend_ot = spend_ot[(spend_ot["bucket"] >= min_b) & (spend_ot["bucket"] <= max_b)].copy()

    ts = contrib_ot.merge(spend_ot, on="bucket", how="left")
    ts["roas_bucket"] = np.where(
        (ts["spend_bucket_raw"] > 0) & np.isfinite(ts["spend_bucket_raw"]),
        ts["contrib_bucket"] / ts["spend_bucket_raw"],
        np.nan,
    )
    ts = ts.dropna(subset=["roas_bucket"]).copy()

    if ts.empty:
        st.info("No valid ROAS values over time (zero/missing spend in buckets).")
        st.stop()

    # mark best model for over-time highlighting
    ts["is_best"] = False
    if best_model_id:
        ts["is_best"] = ts["solID"].astype(str) == str(best_model_id)

    spend_ot = spend_ot.sort_values("bucket").copy()
    spend_ot["period"] = spend_ot["bucket"].dt.strftime("%Y-%m-%d")
    ts["period"] = ts["bucket"].dt.strftime("%Y-%m-%d")

    buckets_sorted = list(pd.unique(ts.sort_values("bucket")["bucket"]))
    periods_sorted = [pd.Timestamp(b).strftime("%Y-%m-%d") for b in buckets_sorted]

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    for b, p in zip(buckets_sorted, periods_sorted):
        df_b = ts[ts["bucket"] == b]

        # box across models
        fig.add_trace(
            go.Box(
                name=p,
                y=df_b["roas_bucket"],
                boxpoints=False,
                showlegend=False,
            ),
            secondary_y=False,
        )

        # all model dots (light)
        fig.add_trace(
            go.Scatter(
                x=[p] * len(df_b),
                y=df_b["roas_bucket"],
                mode="markers",
                marker=dict(size=5, opacity=0.25),
                showlegend=False,
                customdata=df_b["solID"].astype(str),
                hovertemplate="period=%{x}<br>roas=%{y}<br>solID=%{customdata}<extra></extra>",
            ),
            secondary_y=False,
        )

        # best model dot (highlight)
        best_b = df_b[df_b["is_best"]]
        if not best_b.empty:
            fig.add_trace(
                go.Scatter(
                    x=[p],
                    y=[best_b["roas_bucket"].iloc[0]],
                    mode="markers",
                    marker=dict(size=12, opacity=1.0),
                    showlegend=False,
                    customdata=[str(best_model_id)],
                    hovertemplate="BEST<br>period=%{x}<br>roas=%{y}<br>solID=%{customdata}<extra></extra>",
                ),
                secondary_y=False,
            )

    # Spend line (raw)
    fig.add_trace(
        go.Scatter(
            x=spend_ot["period"],
            y=spend_ot["spend_bucket_raw"],
            mode="lines+markers",
            showlegend=False,
        ),
        secondary_y=True,
    )

    fig.update_layout(
        title=f"ROAS and Spend over time — {driver_ot} ({freq2})",
        xaxis_title="Period",
        showlegend=False,
    )
    fig.update_yaxes(title_text="ROAS", secondary_y=False)
    fig.update_yaxes(title_text="Spend (raw)", secondary_y=True)
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("ROAS over time summary")
    roas_ot_summary = (
        ts.groupby("bucket", as_index=False)
        .agg(
            median_roas=("roas_bucket", "median"),
            p25_roas=("roas_bucket", lambda x: np.nanpercentile(x, 25)),
            p75_roas=("roas_bucket", lambda x: np.nanpercentile(x, 75)),
            min_roas=("roas_bucket", "min"),
            max_roas=("roas_bucket", "max"),
        )
        .merge(spend_ot[["bucket", "spend_bucket_raw"]], on="bucket", how="left")
        .sort_values("bucket")
        .rename(columns={"bucket": "period"})
    )
    roas_ot_summary["period"] = pd.to_datetime(roas_ot_summary["period"]).dt.strftime("%Y-%m-%d")
    st.dataframe(roas_ot_summary, use_container_width=True)
