import datetime as dt
import os
import re

import numpy as np
import pandas as pd
import plotly.express as px  # kept (used for bar)
import plotly.graph_objects as go
import streamlit as st
from app_shared import (
    download_json_from_gcs_cached,
    download_parquet_from_gcs_cached,
    require_login_and_domain,
    safe_read_parquet,
)
from app_split_helpers import ensure_session_defaults
from google.cloud import storage
from plotly.subplots import make_subplots

require_login_and_domain()
ensure_session_defaults()

# ---------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------
GCS_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")
DEFAULT_PREFIX = "robyn/"

FILE_XAGG = "xDecompAgg.parquet"
FILE_HYP = "resultHypParam.parquet"
FILE_MEDIA = "mediaVecCollect.parquet"
FILE_XVEC = "xDecompVecCollect.parquet"

# Raw spend parquet (business ROAS denominator)
# Can be a GCS path (gs://bucket/path) or local path
# Example: gs://mmm-app-output/datasets/fr/20251208_115448/raw.parquet
RAW_SPEND_PARQUET = os.getenv(
    "RAW_SPEND_PARQUET",
    "",  # Empty by default - user must configure via env var or GCS
)

st.set_page_config(page_title="Review Model Stability", layout="wide")
st.title("Review Model Stability")

# Add helpful documentation at the top
with st.expander("ℹ️ About This Page", expanded=False):
    st.markdown(
        """
        ### Purpose
        This page analyzes the **stability of your Robyn MMM models** across multiple model iterations 
        within a single training run. It helps you understand:
        
        - **Driver Share Stability**: How consistent are the contribution shares across models?
        - **ROAS Stability** (optional): How stable are Return on Ad Spend metrics?
        - **Model Quality Distribution**: Distribution of key metrics across the model ensemble
        
        ### How to Use
        1. **Select Your Model Run**: Choose experiment name, country, and optionally a specific timestamp
        2. **Set Quality Filters**: Use the sidebar to filter models by quality thresholds
        3. **Review Stability**: Analyze driver shares and ROAS across the filtered models
        
        ### Configuration
        
        **Required:**
        - Select a model run using the dropdowns below (Experiment Name, Country, Timestamp)
        
        **Optional - For ROAS Analysis:**
        - Set the `RAW_SPEND_PARQUET` environment variable to enable ROAS metrics
        - This should point to the raw spend data used for training
        - Format: `gs://mmm-app-output/datasets/{country}/{timestamp}/raw.parquet`
        - If not configured, driver share analysis will still work
        
        ### Model Quality Filters
        Use the sidebar to filter models by:
        - **R² (coefficient of determination)**: Model fit quality
        - **NRMSE (normalized root mean squared error)**: Prediction accuracy
        - **decomp.rssd**: Decomposition residual sum of squares
        
        Choose from presets (Good, Acceptable, All) or set custom thresholds.
        """
    )

st.markdown("---")


# ---------------------------------------------------------------------
# GCS helpers (adapted from View_Results.py)
# ---------------------------------------------------------------------
@st.cache_resource
def gcs_client():
    return storage.Client()


client = gcs_client()


def list_blobs(bucket_name: str, prefix: str):
    """List blobs in GCS with the given prefix."""
    try:
        bucket = client.bucket(bucket_name)
        return list(client.list_blobs(bucket_or_name=bucket, prefix=prefix))
    except Exception as e:
        st.error(f"❌ Failed to list gs://{bucket_name}/{prefix} — {e}")
        return []


def parse_path(name: str):
    """Parse GCS blob path into components (revision, country, stamp, file)."""
    # Expected format: robyn/<TAG_NUMBER>/<country>/<stamp>/file...
    parts = name.split("/")
    if len(parts) >= 5 and parts[0] == "robyn":
        rev_part = parts[1]
        if "_" in rev_part:
            # New format with TAG_NUMBER
            tag_num_parts = rev_part.rsplit("_", 1)
            rev = rev_part  # Keep full TAG_NUMBER as rev
            tag = tag_num_parts[0]
            try:
                number = int(tag_num_parts[1])
            except (ValueError, IndexError):
                number = None
        else:
            # Old format (e.g., "r100")
            rev = rev_part
            tag = rev_part
            number = None

        return {
            "rev": rev,
            "tag": tag,
            "number": number,
            "country": parts[2],
            "stamp": parts[3],
            "file": "/".join(parts[4:]),
        }
    return None


def group_runs(blobs):
    """Group blobs by (revision, country, stamp)."""
    runs = {}
    for b in blobs:
        info = parse_path(b.name)
        if not info or not info["file"]:
            continue
        key = (info["rev"], info["country"], info["stamp"])
        runs.setdefault(key, []).append(b)
    return runs


def run_has_required_files(run_blobs, required_files=None):
    """
    Check if a run has all required model output files.
    
    Args:
        run_blobs: List of blobs for a specific run
        required_files: List of required filenames. Defaults to the standard Robyn output files.
    
    Returns:
        bool: True if all required files are present, False otherwise
    """
    if required_files is None:
        required_files = [FILE_XAGG, FILE_HYP, FILE_MEDIA, FILE_XVEC]
    
    # Extract filenames from blob paths
    blob_files = set()
    for blob in run_blobs:
        # Extract filename from path (e.g., "output_models_data/xDecompAgg.parquet" -> "xDecompAgg.parquet")
        parts = blob.name.split("/")
        if len(parts) >= 2 and parts[-2] == "output_models_data":
            blob_files.add(parts[-1])
    
    # Check if all required files are present
    return all(f in blob_files for f in required_files)


def parse_stamp(stamp: str):
    """Parse timestamp string."""
    try:
        return dt.datetime.strptime(stamp, "%m%d_%H%M%S")
    except Exception:
        # Return datetime.min for unparseable stamps so they sort to the end
        return dt.datetime.min


def parse_rev_key(rev: str):
    """Parse revision for sorting."""
    m = re.search(r"(\d+)$", (rev or "").strip())
    if m:
        return (0, int(m.group(1)))
    return (1, (rev or "").lower())


def extract_goal_from_config(bucket_name: str, stamp: str, run_key=None):
    """Extract the goal (dep_var) from job_config.json or model_summary.json for a given timestamp.

    Args:
        bucket_name: GCS bucket name
        stamp: Timestamp string
        run_key: Optional tuple of (rev, country, stamp) for fallback to robyn folder

    Returns:
        str or None: The goal/dep_var if found
    """
    import json

    # First try: training-configs/{stamp}/job_config.json
    try:
        config_blob_path = f"training-configs/{stamp}/job_config.json"
        client_instance = gcs_client()
        blob = client_instance.bucket(bucket_name).blob(config_blob_path)
        if blob.exists():
            config_data = blob.download_as_bytes()
            config = json.loads(config_data.decode("utf-8"))
            goal = config.get("dep_var")
            if goal:
                return goal
    except Exception:
        pass

    # Second try: robyn/{rev}/{country}/{stamp}/model_summary.json
    if run_key:
        try:
            rev, country, _ = run_key
            model_summary_path = (
                f"robyn/{rev}/{country}/{stamp}/model_summary.json"
            )
            client_instance = gcs_client()
            blob = client_instance.bucket(bucket_name).blob(model_summary_path)
            if blob.exists():
                summary_data = blob.download_as_bytes()
                summary = json.loads(summary_data.decode("utf-8"))
                # Extract dep_var from input_metadata
                if (
                    "input_metadata" in summary
                    and "dep_var" in summary["input_metadata"]
                ):
                    return summary["input_metadata"]["dep_var"]
        except Exception:
            pass

    return None


def get_goals_for_runs(bucket_name: str, run_keys):
    """Extract goals for a set of runs. Returns dict mapping (rev, country, stamp) to goal.

    Tries two methods:
    1. training-configs/{stamp}/job_config.json (for runs with training configs)
    2. robyn/{rev}/{country}/{stamp}/model_summary.json (for runs without training configs)
    """
    goals_map = {}

    for key in run_keys:
        rev, country, stamp = key
        # Try to extract goal with fallback to model_summary.json
        goal = extract_goal_from_config(bucket_name, stamp, run_key=key)
        if goal:
            goals_map[key] = goal

    return goals_map


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
@st.cache_data
def load_parquet_from_gcs(blob_path: str) -> pd.DataFrame:
    return download_parquet_from_gcs_cached(GCS_BUCKET, blob_path)


@st.cache_data
def load_raw_spend(path: str) -> pd.DataFrame | None:
    """
    Load raw spend data from either GCS (gs://...) or local filesystem.

    Args:
        path: Either a GCS URI (gs://bucket/path) or local file path

    Returns:
        DataFrame with raw spend data, or None if path is empty or file not found
    """
    if not path or not path.strip():
        return None

    path = path.strip()

    # Handle GCS paths (gs://bucket/path)
    if path.startswith("gs://"):
        try:
            # Parse gs://bucket/path into bucket and blob_path
            path_without_prefix = path[5:]  # Remove 'gs://'
            if "/" not in path_without_prefix:
                st.error(f"Invalid GCS path format: {path}")
                return None

            bucket_name, blob_path = path_without_prefix.split("/", 1)
            df = download_parquet_from_gcs_cached(bucket_name, blob_path)

            if "DATE" in df.columns:
                df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
            return df
        except FileNotFoundError:
            st.warning(
                f"Raw spend file not found in GCS: {path}\n\n"
                "ROAS analysis will be disabled."
            )
            return None
        except Exception as e:
            st.error(f"Failed to load raw spend from GCS: {path}\n\nError: {e}")
            return None

    # Handle local filesystem paths
    if not os.path.exists(path):
        st.warning(
            f"Raw spend file not found locally: {path}\n\n"
            "ROAS analysis will be disabled."
        )
        return None

    try:
        df = safe_read_parquet(path)
        if "DATE" in df.columns:
            df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
        return df
    except Exception as e:
        st.error(
            f"Failed to load raw spend from local file: {path}\n\nError: {e}"
        )
        return None


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


def build_share_summary(
    contrib_driver: pd.DataFrame, drivers: list[str]
) -> pd.DataFrame:
    """
    Per-driver stats with missing drivers per model treated as 0 share.
    If you include ALL drivers, sum(mean_share) ~= 1.
    """
    mat = (
        contrib_driver[contrib_driver["driver"].isin(drivers)]
        .pivot_table(
            index="solID", columns="driver", values="share", aggfunc="sum"
        )
        .fillna(0.0)
    )

    out = []
    for d in mat.columns:
        s = mat[d].values.astype(float)
        out.append(
            {
                "driver": d,
                "mean_share": float(np.mean(s)) if len(s) else np.nan,
                "median_share": float(np.median(s)) if len(s) else np.nan,
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


def pick_total_col_for_share(xvec: pd.DataFrame) -> tuple[str, bool]:
    """
    Returns (total_col_name, is_temp_created).
    Preference:
      1) yhat
      2) dep_var
      3) y
      4) fallback: sum numeric cols excluding id/time-ish columns (creates _temp_total)
    """
    for c in ["yhat", "dep_var", "y"]:
        if c in xvec.columns and pd.api.types.is_numeric_dtype(xvec[c]):
            return c, False

    exclude = {"ds", "solID", "type", "date", "ts"}
    num_cols = [
        c
        for c in xvec.columns
        if c not in exclude and pd.api.types.is_numeric_dtype(xvec[c])
    ]
    if not num_cols:
        return "", False

    xvec["_temp_total"] = xvec[num_cols].sum(axis=1)
    return "_temp_total", True


# ---------------------------------------------------------------------
# Configuration: Select Model Run (similar to View_Results.py)
# ---------------------------------------------------------------------
# Load and cache runs from GCS
if (
    "model_stability_runs_cache" not in st.session_state
    or st.session_state.get("model_stability_last_bucket") != GCS_BUCKET
    or st.session_state.get("model_stability_last_prefix") != DEFAULT_PREFIX
):
    with st.spinner("Loading available model runs from GCS..."):
        blobs = list_blobs(GCS_BUCKET, DEFAULT_PREFIX)
        runs = group_runs(blobs)
        st.session_state["model_stability_runs_cache"] = runs
        st.session_state["model_stability_last_bucket"] = GCS_BUCKET
        st.session_state["model_stability_last_prefix"] = DEFAULT_PREFIX
else:
    runs = st.session_state["model_stability_runs_cache"]

if not runs:
    st.info(
        f"No model runs found under gs://{GCS_BUCKET}/{DEFAULT_PREFIX}. "
        "Run some experiments first to analyze model stability."
    )
    st.stop()

# Sort runs by revision and timestamp (newest first)
keys_sorted = sorted(
    runs.keys(),
    key=lambda k: (parse_rev_key(k[0]), parse_stamp(k[2])),
    reverse=True,
)

# Default to the newest run
seed_key = keys_sorted[0]
default_rev = seed_key[0]

# UI: All filters in a single row
all_revs = sorted({k[0] for k in runs.keys()}, key=parse_rev_key, reverse=True)

# Restore previous selection if available
if (
    "model_stability_revision_value" in st.session_state
    and st.session_state["model_stability_revision_value"] in all_revs
):
    default_rev_index = all_revs.index(
        st.session_state["model_stability_revision_value"]
    )
else:
    default_rev_index = (
        all_revs.index(default_rev) if default_rev in all_revs else 0
    )

# Create 4 columns for filters
col1, col2, col3, col4 = st.columns(4)

# Column 1: Experiment Name
with col1:
    rev = st.selectbox(
        "Experiment Name",
        all_revs,
        index=default_rev_index,
        key="model_stability_revision",
        help="Tag & number, e.g. gmv001",
    )

# Store selection
if rev != st.session_state.get("model_stability_revision_value"):
    st.session_state["model_stability_revision_value"] = rev

# Countries available in this revision
rev_keys = [k for k in runs.keys() if k[0] == rev]
rev_countries = sorted({k[1] for k in rev_keys})

# Default country = newest run in this revision
rev_keys_sorted = sorted(
    rev_keys, key=lambda k: parse_stamp(k[2]), reverse=True
)
default_country_in_rev = rev_keys_sorted[0][1] if rev_keys_sorted else ""

# Restore previous selection if available
if "model_stability_country_value" in st.session_state:
    current_country = st.session_state["model_stability_country_value"]
    default_country = (
        current_country
        if current_country in rev_countries
        else (
            default_country_in_rev
            if default_country_in_rev in rev_countries
            else rev_countries[0] if rev_countries else None
        )
    )
else:
    default_country = default_country_in_rev

# Column 2: Country
with col2:
    country_index = (
        rev_countries.index(default_country)
        if default_country in rev_countries
        else 0
    )
    countries_sel = st.selectbox(
        "Country",
        rev_countries,
        index=country_index,
        key="model_stability_countries",
    )

# Store selection
if countries_sel != st.session_state.get("model_stability_country_value"):
    st.session_state["model_stability_country_value"] = countries_sel

if not countries_sel:
    st.info("Select a country.")
    st.stop()

# Convert to list for compatibility
countries_sel = [countries_sel]

# Goals available for selected revision and country
# Extract goals from configs for the filtered runs
rev_country_keys = [
    k for k in runs.keys() if k[0] == rev and k[1] in countries_sel
]

# Get goals for all runs (cached in session state to avoid repeated GCS calls)
cache_key = f"goals_cache_stability_{rev}_{'_'.join(sorted(countries_sel))}"
if cache_key not in st.session_state:
    with st.spinner("Loading goal information..."):
        goals_map = get_goals_for_runs(GCS_BUCKET, rev_country_keys)
        st.session_state[cache_key] = goals_map
else:
    goals_map = st.session_state[cache_key]

# Get unique goals for the filtered runs
rev_country_goals = sorted(
    {goals_map.get(k) for k in rev_country_keys if goals_map.get(k)}
)

# Determine default goal for selectbox
if "model_stability_goal_value" in st.session_state:
    # User has saved selection - validate and preserve
    current_goal = st.session_state["model_stability_goal_value"]
    default_goal = (
        current_goal
        if current_goal in rev_country_goals
        else (rev_country_goals[0] if rev_country_goals else None)
    )
else:
    # First time - use first available goal
    default_goal = rev_country_goals[0] if rev_country_goals else None

# Column 3: Goal
if rev_country_goals:
    with col3:
        goal_index = (
            rev_country_goals.index(default_goal)
            if default_goal in rev_country_goals
            else 0
        )
        goals_sel = st.selectbox(
            "Goal (dep_var)",
            rev_country_goals,
            index=goal_index,
            help="Filter by goal variable used in model training",
            key="model_stability_goals",
        )

    # Store selection
    if goals_sel != st.session_state.get("model_stability_goal_value"):
        st.session_state["model_stability_goal_value"] = goals_sel

    if not goals_sel:
        st.info("Select a goal.")
        st.stop()

    # Convert to list for compatibility
    goals_sel = [goals_sel]

    # Filter runs by selected goal
    rev_country_keys = [
        k for k in rev_country_keys if goals_map.get(k) in goals_sel
    ]

    # Filter countries_sel to only include countries that have runs with selected goals
    countries_with_goals = sorted({k[1] for k in rev_country_keys})
    countries_sel = [c for c in countries_sel if c in countries_with_goals]

    if not countries_sel:
        st.info("No countries have runs with the selected goals.")
        st.stop()
else:
    # No goal information available - proceed without goal filtering
    goals_sel = None
    st.warning(
        "Goal information not available for some runs. Showing all runs."
    )

# Timestamps available for selected revision and countries
# Filter to only include timestamps with complete model outputs
all_stamps_raw = sorted(
    {k[2] for k in rev_country_keys}, key=parse_stamp, reverse=True
)

# Filter out timestamps that don't have complete model outputs
all_stamps = []
incomplete_stamps = []
for stamp in all_stamps_raw:
    # Check if any run with this timestamp has complete outputs
    stamp_runs = [k for k in rev_country_keys if k[2] == stamp]
    has_valid_run = any(run_has_required_files(runs.get(k, [])) for k in stamp_runs)
    if has_valid_run:
        all_stamps.append(stamp)
    else:
        incomplete_stamps.append(stamp)

# Inform user if some timestamps were filtered out
if incomplete_stamps:
    st.info(
        f"ℹ️ Note: {len(incomplete_stamps)} timestamp(s) excluded from dropdown "
        f"due to incomplete model outputs. "
        f"Only showing {len(all_stamps)} timestamp(s) with complete data."
    )

# Restore previous selection if available
stamp_options = [""] + all_stamps
if "model_stability_timestamp_value" in st.session_state:
    saved_timestamp = st.session_state["model_stability_timestamp_value"]
    default_stamp_index = (
        stamp_options.index(saved_timestamp)
        if saved_timestamp in stamp_options
        else 0
    )
else:
    default_stamp_index = 0

# Column 4: Timestamp
with col4:
    stamp_sel = st.selectbox(
        "Timestamp (optional)",
        stamp_options,
        index=default_stamp_index,
        help="Leave empty to use the latest run. Select a specific timestamp if needed.",
        key="model_stability_timestamp",
    )

# Store selection
if stamp_sel != st.session_state.get("model_stability_timestamp_value"):
    st.session_state["model_stability_timestamp_value"] = stamp_sel

# Determine which run(s) to use for analysis
# For stability analysis, we typically want to use a specific timestamp
# If no timestamp selected, use the latest for the first selected country
if stamp_sel:
    # Specific timestamp selected - use it for all selected countries
    selected_runs = [(rev, c, stamp_sel) for c in countries_sel]
    # Filter to only runs that exist
    selected_runs = [r for r in selected_runs if r in runs]
    if not selected_runs:
        st.error(
            f"No runs found for {rev}/{', '.join(countries_sel)}/{stamp_sel}"
        )
        st.stop()
    
    # Validate that the selected run has all required files
    analysis_key = selected_runs[0]
    if not run_has_required_files(runs.get(analysis_key, [])):
        st.error(
            f"❌ **Incomplete model outputs for selected run**\n\n"
            f"**Run:** {analysis_key[0]} / {analysis_key[1]} / {analysis_key[2]}\n\n"
            "The selected run exists but is missing required output files.\n\n"
            "**Required files:**\n"
            f"- {FILE_XAGG}\n"
            f"- {FILE_HYP}\n"
            f"- {FILE_MEDIA}\n"
            f"- {FILE_XVEC}\n\n"
            "**Troubleshooting:**\n"
            "- Check that the model training completed successfully\n"
            "- Verify the output files were uploaded to GCS\n"
            "- Try selecting a different timestamp"
        )
        st.stop()
else:
    # No timestamp - use latest for first country that has valid model outputs
    country_runs = [k for k in rev_country_keys if k[1] == countries_sel[0]]
    if not country_runs:
        st.error(f"No runs found for {rev}/{countries_sel[0]}")
        st.stop()
    
    # Filter to only runs that have all required output files
    valid_runs = [k for k in country_runs if run_has_required_files(runs.get(k, []))]
    
    if not valid_runs:
        st.error(
            f"❌ **No valid model runs found for {rev}/{countries_sel[0]}**\n\n"
            f"Found {len(country_runs)} run(s), but none have complete model outputs.\n\n"
            "**Required files:**\n"
            f"- {FILE_XAGG}\n"
            f"- {FILE_HYP}\n"
            f"- {FILE_MEDIA}\n"
            f"- {FILE_XVEC}\n\n"
            "**Troubleshooting:**\n"
            "- Check that the model training completed successfully\n"
            "- Verify the output files were uploaded to GCS\n"
            "- Try selecting a specific timestamp from the dropdown"
        )
        st.stop()
    
    # Sort valid runs by timestamp (newest first)
    valid_runs_sorted = sorted(
        valid_runs, key=lambda k: parse_stamp(k[2]), reverse=True
    )
    analysis_key = valid_runs_sorted[0]

# Construct GCS_PREFIX from selected run
GCS_PREFIX = f"robyn/{analysis_key[0]}/{analysis_key[1]}/{analysis_key[2]}/output_models_data"

# Display selected configuration
st.info(
    f"📊 Analyzing: **{analysis_key[0]}** / **{analysis_key[1]}** / **{analysis_key[2]}**\n\n"
    f"Path: `gs://{GCS_BUCKET}/{GCS_PREFIX}`"
)

# Best model id text file is in the RUN folder, NOT output_models_data/
RUN_PREFIX = GCS_PREFIX.rsplit("/output_models_data", 1)[0]
BEST_MODEL_BLOB = f"{RUN_PREFIX}/best_model_id.txt"


# ---------------------------------------------------------------------
# Auto-discover raw spend data from job config
# ---------------------------------------------------------------------
def try_auto_discover_raw_spend_from_config(
    bucket: str, timestamp: str
) -> str | None:
    """
    Try to automatically find raw spend data by reading the job_config.json
    file from training-configs folder.

    This is the most reliable method as it uses the exact data path that
    was used during model training.

    Args:
        bucket: GCS bucket name
        timestamp: Model timestamp (e.g., "0812_163049")

    Returns:
        GCS URI if found, None otherwise
    """
    try:
        # Path to job config for this training run
        config_path = f"training-configs/{timestamp}/job_config.json"

        # Try to read the job config
        try:
            job_config = download_json_from_gcs_cached(bucket, config_path)
        except Exception:
            # Config not found, return None
            return None

        # Extract data_gcs_path from config
        data_gcs_path = job_config.get("data_gcs_path")

        if data_gcs_path:
            # data_gcs_path is typically a full gs:// URI or just the path
            if data_gcs_path.startswith("gs://"):
                return data_gcs_path
            else:
                # If it's just a path, prepend the bucket
                return f"gs://{bucket}/{data_gcs_path}"

        return None

    except Exception as e:
        # Log error but don't fail - just return None
        return None


# Try to auto-discover raw spend data if not configured
if not RAW_SPEND_PARQUET:
    with st.spinner("🔍 Reading training configuration..."):
        discovered_path = try_auto_discover_raw_spend_from_config(
            GCS_BUCKET, analysis_key[2]
        )
        if discovered_path:
            RAW_SPEND_PARQUET = discovered_path
            st.success(
                f"✅ Found raw spend data from training config: `{discovered_path}`"
            )
        else:
            st.info(
                "ℹ️ **Raw Spend Data Not Found**\n\n"
                "Could not find raw spend data from training configuration. "
                "ROAS analysis will be disabled.\n\n"
                "**What was checked:**\n"
                f"- Job config: `training-configs/{analysis_key[2]}/job_config.json`\n"
                f"- Looking for `data_gcs_path` field in the config\n\n"
                "**To manually configure:**\n"
                "Set the `RAW_SPEND_PARQUET` environment variable to point to your raw spend parquet file.\n\n"
                "Driver share analysis will still work without raw spend data."
            )

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
        "❌ **Failed to load Robyn model outputs from GCS**\n\n"
        f"**Bucket:** `{GCS_BUCKET}`\n\n"
        f"**Prefix:** `{GCS_PREFIX}`\n\n"
        f"**Error:** {e}\n\n"
        "---\n\n"
        "**Troubleshooting:**\n"
        "- Verify the path exists in GCS by checking the [View Results](View_Results) page\n"
        "- Ensure the path ends with `/output_models_data`\n"
        "- Check that the model run completed successfully\n"
        "- Confirm you have access to the GCS bucket"
    )
    st.stop()

raw_spend = load_raw_spend(RAW_SPEND_PARQUET)
if raw_spend is None or raw_spend.empty:
    if RAW_SPEND_PARQUET:
        # Path was provided (either manually or auto-discovered) but failed to load
        # Error already shown by load_raw_spend function
        pass

best_model_id, best_dbg = try_read_best_model_id()
if not best_model_id:
    st.warning(
        "best_model_id.txt could not be auto-read.\n"
        f"- attempted: gs://{GCS_BUCKET}/{BEST_MODEL_BLOB}\n"
        f"- debug: {best_dbg}\n"
        "Fix: ensure the blob exists + your env can read GCS (gcsfs or google-cloud-storage + auth)."
    )

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
        st.error(
            "xDecompVecCollect is missing required columns ('solID', 'ds')."
        )
        st.stop()

if "solID" not in media.columns:
    st.error("mediaVecCollect is missing required column: solID")
    st.stop()

val_col = detect_val_col(xAgg)

# ---------------------------------------------------------------------
# Sidebar: model selection (time window ALWAYS ON now)
# ---------------------------------------------------------------------
st.sidebar.header("Model selection")

mode = st.sidebar.selectbox(
    "Model Quality:", ["Good", "Acceptable", "All", "Custom"], index=1
)

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
        f"Thresholds: R² ≥ {rsq_min}, NRMSE ≤ {nrmse_max}, decomp.rssd ≤ {decomp_max}"
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

# Apply filtering based on mode
if mode == "All":
    # In "All" mode, include all models without filtering
    good_models = hyp_f["solID"].astype(str).unique()
else:
    # Apply threshold filters for other modes
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
st.write(
    f"Selected **{len(good_models)} / {len(hyp)}** models (mode: **{mode}**)"
)

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

# Model window (for validation + spend clipping)
model_min = xVec_gm["ds"].min()
model_max = xVec_gm["ds"].max()

# ---------------------------------------------------------------------
# Data validation block (model window vs spend window) + clipped spend for ROAS
# ---------------------------------------------------------------------
raw_spend_roas = raw_spend

if (
    raw_spend is not None
    and not raw_spend.empty
    and "DATE" in raw_spend.columns
    and pd.notna(model_min)
    and pd.notna(model_max)
):
    spend_min = raw_spend["DATE"].min()
    spend_max = raw_spend["DATE"].max()

    total_spend_days = raw_spend["DATE"].dropna().nunique()
    overlap_days = (
        raw_spend[
            (raw_spend["DATE"] >= model_min) & (raw_spend["DATE"] <= model_max)
        ]["DATE"]
        .dropna()
        .nunique()
    )

    overlap_pct = (
        (overlap_days / total_spend_days * 100) if total_spend_days > 0 else 0.0
    )

    # Always clip to model window for ROAS correctness
    raw_spend_roas = raw_spend[
        (raw_spend["DATE"] >= model_min) & (raw_spend["DATE"] <= model_max)
    ].copy()

    # Only surface validation if it's actually a problem
    if overlap_pct < 95:
        st.warning(
            f"⚠️ Raw spend timeframe differs from model timeframe.\n\n"
            f"- Model window: {model_min.date()} → {model_max.date()}\n"
            f"- Spend window: {spend_min.date()} → {spend_max.date()}\n"
            f"- Overlap: {overlap_pct:.1f}% of spend days\n\n"
            "ROAS is automatically clipped to the model window."
        )

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
if raw_spend_roas is not None and not raw_spend_roas.empty:
    spend_cols = [
        c for c in raw_spend_roas.columns if c != "DATE" and is_paid_like(c)
    ]
    spend_root_map = {c: canonical_media_name(c) for c in spend_cols}

    for d in media_drivers:
        d_root = canonical_media_name(d)
        match = next(
            (sc for sc, sr in spend_root_map.items() if sr == d_root), None
        )
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

    default_drivers = [d for d in all_drivers if "COST" in d.upper()][
        :6
    ] or all_drivers[:6]
    sel_drivers = st.multiselect(
        "Select Drivers",
        options=all_drivers,
        default=default_drivers,
        key="drivers_share",
    )
    if not sel_drivers:
        st.stop()

    plot_df = contrib_driver[contrib_driver["driver"].isin(sel_drivers)].copy()

    st.caption(
        f"Note: Best model is highlighted with larger dots: **{best_model_id}**"
    )
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
            st.info(
                "None of your selected drivers are in mediaVecCollect (paid pool)."
            )
        else:
            paid_contrib = contrib_driver[
                contrib_driver["driver"].isin(media_drivers)
            ].copy()
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
                .pivot_table(
                    index="solID",
                    columns="driver",
                    values="share",
                    aggfunc="sum",
                )
                .fillna(0.0)
            )
            mat_paid = (
                paid_contrib[paid_contrib["driver"].isin(paid_plot)]
                .pivot_table(
                    index="solID",
                    columns="driver",
                    values="share_of_paid",
                    aggfunc="sum",
                )
                .fillna(0.0)
            )

            rows = []
            for d in paid_plot:
                rows.append(
                    {
                        "driver": d,
                        "mean_share_of_total": (
                            float(mat_total[d].mean())
                            if d in mat_total.columns
                            else 0.0
                        ),
                        "mean_share_of_paid": (
                            float(mat_paid[d].mean())
                            if d in mat_paid.columns
                            else 0.0
                        ),
                        "sd_share_of_paid": (
                            float(mat_paid[d].std(ddof=1))
                            if d in mat_paid.columns and len(mat_paid[d]) > 1
                            else 0.0
                        ),
                    }
                )
            paid_summary = pd.DataFrame(rows).sort_values(
                "mean_share_of_paid", ascending=False
            )
            st.dataframe(paid_summary, use_container_width=True)
            st.caption(
                "mean_share_of_total = share of TOTAL modeled outcome (paid driver’s average contribution share). "
                "mean_share_of_paid = share WITHIN PAID ONLY (normalized across paid drivers to ~1)."
            )

    st.markdown("---")
    st.subheader("Driver contribution over time")

    freq = st.selectbox(
        "Time aggregation", ["Monthly", "Quarterly", "Yearly"], index=0
    )
    bucket_fn = make_bucket_fn(freq)

    drivers_ts_candidates = [d for d in all_drivers if d in xVec_gm.columns]
    if not drivers_ts_candidates:
        st.info(
            "No drivers from xDecompAgg found as columns in xDecompVecCollect."
        )
    else:
        driver_ts = st.selectbox(
            "Driver (time series)", options=drivers_ts_candidates, index=0
        )

        xsub = xVec_gm[["ds", "solID", driver_ts]].copy()
        xsub["bucket"] = bucket_fn(xsub["ds"])

        # numerator: driver contribution per (bucket, solID)
        ts_units = (
            xsub.groupby(["bucket", "solID"], as_index=False)[driver_ts]
            .sum()
            .rename(columns={driver_ts: "contrib_bucket"})
        )

        # denominator: modeled outcome per (bucket, solID)
        total_col, created_temp = pick_total_col_for_share(xVec_gm)
        if not total_col:
            st.info(
                "Could not compute share-over-time denominator (no yhat/dep_var/y and no numeric fallback)."
            )
        else:
            denom = xVec_gm[["ds", "solID", total_col]].copy()
            denom["bucket"] = bucket_fn(denom["ds"])
            ts_total = (
                denom.groupby(["bucket", "solID"], as_index=False)[total_col]
                .sum()
                .rename(columns={total_col: "total_bucket"})
            )

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
                fig_units.update_layout(
                    xaxis_title="Period",
                    yaxis_title="Total Contribution (outcome units)",
                )
                st.plotly_chart(fig_units, use_container_width=True)

            with c2:
                fig_share_ts = px.box(
                    ts.dropna(subset=["share_bucket"]),
                    x="bucket",
                    y="share_bucket",
                    points="all",
                    title=f"Percentage Contribution over time — {driver_ts}",
                )
                fig_share_ts.update_layout(
                    xaxis_title="Period",
                    yaxis_title=f"Pct Contribution ({total_col})",
                )
                st.plotly_chart(fig_share_ts, use_container_width=True)

            if created_temp:
                st.caption(
                    "Note: share denominator fallback used a numeric-column sum (no yhat/dep_var/y found)."
                )

# =====================================================================
# ROAS TAB
# =====================================================================
with tab_roas:
    st.subheader("ROAS stability")

    if raw_spend_roas is None or raw_spend_roas.empty:
        st.info(
            "⚠️ Raw spend data not available (or empty after clipping to model window).\n\n"
            "To enable ROAS analysis, set the `RAW_SPEND_PARQUET` environment variable:\n"
            "- For GCS: `gs://bucket/path/to/raw.parquet`\n"
            "- For local: `/path/to/raw.parquet`"
        )
        st.stop()

    if "DATE" not in raw_spend_roas.columns:
        st.error(
            "Raw spend parquet must have a DATE column for over-time analysis."
        )
        st.stop()

    if not paid_like_drivers:
        st.info(
            "No paid drivers could be mapped to raw spend columns (cost/spend/EUR/USD/BUDGET)."
        )
        st.stop()

    # deterministic order + default selection
    paid_like_drivers = sorted(paid_like_drivers)
    default_roas = sorted(
        [d for d in paid_like_drivers if "COST" in d.upper()]
        or paid_like_drivers
    )[:6]

    st.caption(
        f"Note: Best model is highlighted with larger dots: **{best_model_id}**"
    )
    sel_roas_drivers = st.multiselect(
        "Select Paid Drivers",
        options=paid_like_drivers,
        default=default_roas,
        key="roas_drivers",
    )
    if not sel_roas_drivers:
        st.stop()
    sel_roas_drivers = sorted(sel_roas_drivers)

    # Contribution per (solID, driver) over model window (xVec_gm already defines the model window)
    contrib = (
        contrib_driver[contrib_driver["driver"].isin(sel_roas_drivers)]
        .groupby(["solID", "driver"], as_index=False)["contrib"]
        .sum()
    )

    # Total spend per driver in MODEL WINDOW (raw_spend_roas already clipped)
    spend_rows = []
    excluded_for_spend = []
    for d in sel_roas_drivers:
        spend_col = driver_to_spend.get(d)
        if not spend_col or spend_col not in raw_spend_roas.columns:
            excluded_for_spend.append((d, "no spend mapping/column"))
            continue
        s = raw_spend_roas[spend_col]
        total_spend = float(s.sum(skipna=True))
        if not np.isfinite(total_spend) or total_spend <= 0:
            excluded_for_spend.append((d, "no positive spend in model window"))
            continue
        spend_rows.append({"driver": d, "total_spend_raw": total_spend})
    spend_totals = pd.DataFrame(spend_rows)

    if excluded_for_spend:
        st.warning(
            "Excluded drivers from ROAS (spend issue):\n"
            + "\n".join([f"- {d}: {why}" for d, why in excluded_for_spend])
        )

    if spend_totals.empty:
        st.info(
            "No selected drivers have positive spend in the model window after mapping."
        )
        st.stop()

    roas_df = contrib.merge(spend_totals, on="driver", how="inner")
    roas_df["roas"] = roas_df["contrib"] / roas_df["total_spend_raw"]
    roas_plot = (
        roas_df.replace([np.inf, -np.inf], np.nan)
        .dropna(subset=["roas"])
        .copy()
    )

    if roas_plot.empty:
        st.info("No valid ROAS values after spend filtering.")
        st.stop()

    # align ordering across ROAS + spend charts (alphabetical by driver name)
    drivers_show = sorted(spend_totals["driver"].unique().tolist())
    roas_plot["driver"] = pd.Categorical(
        roas_plot["driver"], categories=drivers_show, ordered=True
    )
    roas_plot = roas_plot.sort_values("driver")

    spend_show = (
        spend_totals.set_index("driver").reindex(drivers_show).reset_index()
    )

    c1, c2 = st.columns(2)

    with c1:
        fig_roas = box_with_best_dot(
            df_plot=roas_plot,
            x="driver",
            y="roas",
            best_id=best_model_id,
            title="ROAS distribution across selected models (model window)",
            y_title="ROAS",
        )
        st.plotly_chart(fig_roas, use_container_width=True)

    with c2:
        fig_sp = px.bar(
            spend_show,
            x="driver",
            y="total_spend_raw",
            title="Total spend by channel (model window)",
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
    st.subheader("ROAS and Spend over time")

    freq2 = st.selectbox(
        "Time aggregation",
        ["Monthly", "Quarterly", "Yearly"],
        index=0,
        key="roas_ot_freq",
    )
    bucket_fn2 = make_bucket_fn(freq2)

    ts_candidates = sorted(
        [
            d
            for d in drivers_show
            if d in xVec_gm.columns
            and (driver_to_spend.get(d) in raw_spend_roas.columns)
        ]
    )
    if not ts_candidates:
        st.info(
            "None of the selected paid drivers exist in xDecompVecCollect AND map to a raw spend column."
        )
        st.stop()

    driver_ot = st.selectbox(
        "Driver", options=ts_candidates, index=0, key="roas_ot_driver"
    )
    spend_col = driver_to_spend[driver_ot]

    # Contributions per bucket per model
    xsub = xVec_gm[["ds", "solID", driver_ot]].copy()
    xsub["bucket"] = bucket_fn2(xsub["ds"])
    contrib_ot = (
        xsub.groupby(["bucket", "solID"], as_index=False)[driver_ot]
        .sum()
        .rename(columns={driver_ot: "contrib_bucket"})
    )

    # Spend per bucket (already clipped to model window via raw_spend_roas)
    rs = raw_spend_roas.copy()
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
        contrib_ot = contrib_ot[
            (contrib_ot["bucket"] >= min_b) & (contrib_ot["bucket"] <= max_b)
        ].copy()
        spend_ot = spend_ot[
            (spend_ot["bucket"] >= min_b) & (spend_ot["bucket"] <= max_b)
        ].copy()

    ts = contrib_ot.merge(spend_ot, on="bucket", how="left")
    ts["roas_bucket"] = np.where(
        (ts["spend_bucket_raw"] > 0) & np.isfinite(ts["spend_bucket_raw"]),
        ts["contrib_bucket"] / ts["spend_bucket_raw"],
        np.nan,
    )
    ts = (
        ts.replace([np.inf, -np.inf], np.nan)
        .dropna(subset=["roas_bucket"])
        .copy()
    )

    if ts.empty:
        st.info(
            "No valid ROAS values over time (zero/missing spend in buckets)."
        )
        st.stop()

    # mark best model for over-time highlighting
    ts["is_best"] = False
    if best_model_id:
        ts["is_best"] = ts["solID"].astype(str) == str(best_model_id)

    spend_ot = spend_ot.sort_values("bucket").copy()
    spend_ot["period"] = spend_ot["bucket"].dt.strftime("%Y-%m-%d")
    ts["period"] = ts["bucket"].dt.strftime("%Y-%m-%d")

    buckets_sorted = list(pd.unique(ts.sort_values("bucket")["bucket"]))
    periods_sorted = [
        pd.Timestamp(b).strftime("%Y-%m-%d") for b in buckets_sorted
    ]

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
        title=f"ROAS and Spend over time — {driver_ot} ({freq2}) (model window)",
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
        .merge(
            spend_ot[["bucket", "spend_bucket_raw"]], on="bucket", how="left"
        )
        .sort_values("bucket")
        .rename(columns={"bucket": "period"})
    )
    roas_ot_summary["period"] = pd.to_datetime(
        roas_ot_summary["period"]
    ).dt.strftime("%Y-%m-%d")
    st.dataframe(roas_ot_summary, use_container_width=True)
