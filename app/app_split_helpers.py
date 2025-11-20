# Connect_Data.py — Streamlit front-end for launching & monitoring Robyn training jobs on Cloud Run Jobs
import json
import logging
import os
import tempfile
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import snowflake.connector as sf
import streamlit as st
from app_shared import _queue_blob_path  # (kept for parity; not used below)
from app_shared import _safe_tick_once  # (kept for parity; not used below
from app_shared import _sanitize_queue_name  # (kept for parity; not used below)
from app_shared import (
    get_snowflake_connection,  # use shared connector for consistency with ensure_sf_conn
)
from app_shared import read_status_json  # (kept for parity; not used below)
from app_shared import (  # Env / constants (already read from env in app_shared); Helpers
    DEFAULT_QUEUE_NAME,
    GCS_BUCKET,
    JOB_HISTORY_COLUMNS,
    PROJECT_ID,
    REGION,
    SAFE_LAG_SECONDS_AFTER_RUNNING,
    TRAINING_JOB_NAME,
    _connect_snowflake,
    _fmt_secs,
    _maybe_resample_df,
    _normalize_resample_agg,
    _normalize_resample_freq,
    _sf_params_from_env,
    append_row_to_job_history,
    build_job_config_from_params,
    effective_sql,
    ensure_sf_conn,
    get_data_processor,
    get_job_manager,
    handle_queue_tick_from_query_params,
    load_queue_from_gcs,
    load_queue_payload,
    parse_train_size,
    queue_tick_once_headless,
    read_job_history_from_gcs,
    require_login_and_domain,
    run_sql,
    save_job_history_to_gcs,
    save_queue_to_gcs,
    timed_step,
    upload_to_gcs,
)
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from google.cloud import storage

__all__ = [
    # public constants & classes...
    "PROJECT_ID",
    "REGION",
    "TRAINING_JOB_NAME",
    "GCS_BUCKET",
    "DEFAULT_QUEUE_NAME",
    "SAFE_LAG_SECONDS_AFTER_RUNNING",
    "JOB_HISTORY_COLUMNS",
    # public helpers...
    "timed_step",
    "parse_train_size",
    "effective_sql",
    "_sf_params_from_env",
    "ensure_sf_conn",
    "run_sql",
    "upload_to_gcs",
    "read_status_json",
    "build_job_config_from_params",
    "_sanitize_queue_name",
    "_queue_blob_path",
    "load_queue_from_gcs",
    "save_queue_to_gcs",
    "load_queue_payload",
    "queue_tick_once_headless",
    "handle_queue_tick_from_query_params",
    "handle_queue_tick_if_requested",
    "get_job_manager",
    "get_data_processor",
    "_fmt_secs",
    "_connect_snowflake",
    "get_snowflake_connection",
    "read_job_history_from_gcs",
    "save_job_history_to_gcs",
    "append_row_to_job_history",
    "require_login_and_domain",
    "_safe_tick_once",
    "_maybe_resample_df",
    "_normalize_resample_freq",
    "_normalize_resample_agg",
    "params_from_ui",
    # private-but-needed by pages (export them anyway)
    "prepare_and_launch_job",
    "render_jobs_job_history",
    "render_job_status_monitor",
    "set_queue_running",
    "maybe_refresh_queue_from_gcs",
    "_make_normalizer",
    "_normalize_row",
    "_set_flash",
    "_render_flash",
    "_toast_dupe_summary",
    "_hydrate_times_from_status",
    "_queue_tick",
    "_auto_refresh_and_tick",
    "_sorted_with_controls",
    "ensure_session_defaults",
    "data_processor",
    "job_manager",
]

# Instantiate shared resources
data_processor = get_data_processor()
job_manager = get_job_manager()

TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED", "COMPLETED", "ERROR"}

# ─────────────────────────────
# Page & logging setup
# ─────────────────────────────
# st.set_page_config(page_title="Robyn MMM Trainer", layout="wide")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ─────────────────────────────
# Session defaults
# ─────────────────────────────
st.session_state.setdefault("job_executions", [])
st.session_state.setdefault("gcs_bucket", GCS_BUCKET)
st.session_state.setdefault("last_timings", None)
st.session_state.setdefault("auto_refresh", False)

# Persistent Snowflake session objects/params
st.session_state.setdefault("sf_params", None)
st.session_state.setdefault("sf_connected", False)
st.session_state.setdefault("sf_conn", None)

# Batch queue state
st.session_state.setdefault("job_queue", [])  # list of dicts
st.session_state.setdefault("queue_running", False)

# Persistent queue session vars
st.session_state.setdefault("queue_name", DEFAULT_QUEUE_NAME)
st.session_state.setdefault("queue_loaded_from_gcs", False)
st.session_state.setdefault("queue_saved_at", None)


# ─────────────────────────────
# One-time Snowflake init for this Streamlit session
# ─────────────────────────────
# Connect_Data.py


def _init_sf_once():
    """
    Manual mode: do not auto-connect. If a connection exists, optionally ping it.
    """
    if st.session_state.get("sf_connected") and st.session_state.get("sf_conn"):
        try:
            # optional light ping to keep alive
            from app_shared import keepalive_ping

            keepalive_ping(st.session_state["sf_conn"])
        except Exception:
            # if ping fails, mark disconnected but do not auto-reconnect
            st.session_state["sf_conn"] = None
            st.session_state["sf_connected"] = False
    # else: do nothing; user must click Connect


# ─────────────────────────────
# Launcher used by queue tick
# ─────────────────────────────
def prepare_and_launch_job(params: dict) -> dict:
    """
    One complete job: query SF -> parquet -> upload -> write config (timestamped + latest) -> run Cloud Run Job.
    For GCS-based workflows, if data_gcs_path is provided, skip Snowflake query and use existing data.
    Returns exec_info dict with execution_name, timestamp, gcs_prefix, etc.
    """
    gcs_bucket = params.get("gcs_bucket") or st.session_state["gcs_bucket"]
    timestamp = datetime.utcnow().strftime("%m%d_%H%M%S")
    gcs_prefix = f"robyn/{params['revision']}/{params['country']}/{timestamp}"

    # Check if data already exists in GCS (Issue #4 GCS-based workflow)
    data_gcs_path_provided = params.get("data_gcs_path")

    if data_gcs_path_provided:
        # GCS-based workflow: data already exists, no Snowflake query needed
        logger.info(f"Using existing data from GCS: {data_gcs_path_provided}")
        data_gcs_path = data_gcs_path_provided
    else:
        # Snowflake-based workflow: validate & query
        sql_eff = params.get("query") or effective_sql(
            params.get("table", ""), params.get("query", "")
        )
        if not sql_eff:
            raise ValueError("Missing SQL/Table for job.")

        with tempfile.TemporaryDirectory() as td:
            timings: List[dict] = []

            # 1) Query Snowflake
            with timed_step("Query Snowflake", timings):
                df = run_sql(sql_eff)
            """with timed_step("Optional resample (queue job)", timings):
                df = _maybe_resample_df(
                    df,
                    params.get("date_var"),
                    params.get("resample_freq", "none"),
                    params.get("resample_agg", "sum"),
                )"""

            # 2) Parquet
            with timed_step("Convert to Parquet", timings):
                parquet_path = os.path.join(td, "input_data.parquet")
                data_processor.csv_to_parquet(df, parquet_path)

            # 3) Upload data
            with timed_step("Upload data to GCS", timings):
                data_blob = f"training-data/{timestamp}/input_data.parquet"
                data_gcs_path = upload_to_gcs(
                    gcs_bucket, parquet_path, data_blob
                )

            # Seed timings.csv (web-side steps) if not present
            if timings:
                df_times = pd.DataFrame(timings)
                dest_blob = f"{gcs_prefix}/timings.csv"
                client = storage.Client()
                blob = client.bucket(gcs_bucket).blob(dest_blob)
                if not blob.exists():
                    with tempfile.NamedTemporaryFile(
                        mode="w", suffix=".csv", delete=False
                    ) as tmp:
                        df_times.to_csv(tmp.name, index=False)
                        upload_to_gcs(gcs_bucket, tmp.name, dest_blob)

    # Optional annotations (batch: pass a gs:// in params)
    annotations_gcs_path = params.get("annotations_gcs_path") or None

    # Load metadata to get column_agg_strategies for resampling
    # This ensures queue jobs use per-column aggregations from metadata
    if params.get("resample_freq", "none") != "none":
        try:
            country = params.get("country", "").lower()
            # Try to load latest metadata for this country
            client = storage.Client()
            bucket = client.bucket(gcs_bucket)
            metadata_blob_path = f"metadata/{country}/latest/mapping.json"
            blob = bucket.blob(metadata_blob_path)

            if blob.exists():
                metadata = json.loads(blob.download_as_bytes())
                column_agg_strategies = metadata.get("agg_strategies", {})
                if column_agg_strategies:
                    params["column_agg_strategies"] = column_agg_strategies
                    logger.info(
                        f"Loaded {len(column_agg_strategies)} column aggregation strategies from metadata for country {country}"
                    )
                else:
                    logger.warning(
                        f"No column_agg_strategies found in metadata for country {country}"
                    )
            else:
                logger.warning(
                    f"Metadata not found at {metadata_blob_path}, using default aggregations"
                )
        except Exception as e:
            logger.warning(
                f"Could not load metadata for column aggregations: {e}"
            )
            # Continue without column_agg_strategies - R script will default to sum

    # 4) Create config (timestamped + latest)
    with tempfile.TemporaryDirectory() as td:
        timings: List[dict] = []
        with timed_step("Create job configuration", timings):
            job_config = build_job_config_from_params(
                params, data_gcs_path, timestamp, annotations_gcs_path
            )
            config_path = os.path.join(td, "job_config.json")
            with open(config_path, "w") as f:
                json.dump(job_config, f, indent=2)
            # timestamped copy
            config_blob = f"training-configs/{timestamp}/job_config.json"
            config_gcs_path = upload_to_gcs(
                gcs_bucket, config_path, config_blob
            )
            # "latest" copy (job reads this)
            _ = upload_to_gcs(
                gcs_bucket,
                config_path,
                "training-configs/latest/job_config.json",
            )

        # 5) Launch job (Cloud Run Jobs)
        with timed_step("Launch training job", timings):
            assert TRAINING_JOB_NAME is not None, "TRAINING_JOB_NAME is not set"
            execution_name = job_manager.create_execution(TRAINING_JOB_NAME)

        # Seed timings.csv (web-side steps) if not present
        if timings:
            df_times = pd.DataFrame(timings)
            dest_blob = f"{gcs_prefix}/timings.csv"
            client = storage.Client()
            blob = client.bucket(gcs_bucket).blob(dest_blob)
            if not blob.exists():
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".csv", delete=False
                ) as tmp:
                    df_times.to_csv(tmp.name, index=False)
                    upload_to_gcs(gcs_bucket, tmp.name, dest_blob)

    return {
        "execution_name": execution_name,
        "timestamp": timestamp,
        "status": "LAUNCHED",
        "config_path": config_gcs_path,
        "data_path": data_gcs_path,
        "revision": params["revision"],
        "country": params["country"],
        "gcs_prefix": gcs_prefix,
        "gcs_bucket": gcs_bucket,
    }


# ─────────────────────────────
# Early stateless tick endpoint (?queue_tick=1)
# ─────────────────────────────
def handle_queue_tick_if_requested():
    """
    Handle queue tick endpoint if requested via query params.
    Call this explicitly in your main app or pages that need it.
    Returns True if tick was handled (and st.stop() was called), False otherwise.
    """
    res = handle_queue_tick_from_query_params(
        st.query_params,  # type: ignore
        st.session_state.get("gcs_bucket", GCS_BUCKET),
        launcher=prepare_and_launch_job,
    )
    if isinstance(res, dict) and res:
        st.json(res)
        st.stop()
        return True
    return False


# ─────────────────────────────
# Small UI helpers
# ─────────────────────────────
def params_from_ui(
    country,
    iterations,
    trials,
    train_size,
    revision,
    date_input,
    paid_media_spends,
    paid_media_vars,
    context_vars,
    factor_vars,
    organic_vars,
    gcs_bucket,
    table,
    query,
    dep_var,
    date_var,
    adstock,
    resample_freq,  # NEW
    resample_agg,  # NEW
) -> dict:
    return {
        "country": country,
        "iterations": int(iterations),
        "trials": int(trials),
        "train_size": parse_train_size(train_size),
        "revision": revision,
        "date_input": date_input,
        "paid_media_spends": paid_media_spends,
        "paid_media_vars": paid_media_vars,
        "context_vars": context_vars,
        "factor_vars": factor_vars,
        "organic_vars": organic_vars,
        "gcs_bucket": gcs_bucket,
        "table": table,
        "query": query,
        "dep_var": dep_var,
        "date_var": date_var,
        "adstock": adstock,
        "resample_freq": resample_freq,  # NEW
        "resample_agg": resample_agg,  # NEW
    }


def _empty_job_history_df() -> pd.DataFrame:
    # Matches fields written by run_all.R::append_to_job_history()
    cols = JOB_HISTORY_COLUMNS
    return pd.DataFrame(columns=cols)


@st.fragment
def render_jobs_job_history(key_prefix: str = "single") -> None:
    with st.expander("📚 Job History (from GCS)", expanded=False):
        # Refresh control first (button triggers full rerun to reload data)
        if st.button(
            "🔁 Refresh job_history", key=f"refresh_job_history_{key_prefix}"
        ):
            # bump a nonce so the dataframe widget key changes and re-renders
            st.session_state["job_history_nonce"] = (
                st.session_state.get("job_history_nonce", 0) + 1
            )
            # Use full rerun instead of fragment rerun to force data reload
            st.rerun()

        try:
            df_job_history = read_job_history_from_gcs(
                st.session_state.get("gcs_bucket", GCS_BUCKET)
            )
        except Exception as e:
            st.error(f"Failed to read job_history from GCS: {e}")
            return

        df_job_history = df_job_history.reindex(columns=JOB_HISTORY_COLUMNS)

        st.caption(
            "JOB_HISTORY entries are view-only and auto-updated when jobs finish."
        )
        st.dataframe(
            df_job_history,
            use_container_width=True,
            hide_index=True,
            key=f"job_history_view_{key_prefix}_{st.session_state.get('job_history_nonce', 0)}",
        )


def render_job_status_monitor(key_prefix: str = "single") -> None:
    """Status UI showing all currently running jobs as a table."""
    st.subheader("📊 Job Status Monitor")

    # Collect all running jobs from two sources:
    # 1. Queue jobs (RUNNING/LAUNCHING status)
    # 2. Single run jobs from session state
    all_running_jobs = []

    # Add queue jobs
    queue = st.session_state.get("job_queue", [])
    for job in queue:
        if job.get("status") in ("RUNNING", "LAUNCHING"):
            all_running_jobs.append(
                {
                    "Source": "Queue",
                    "Job ID": str(job.get("id", "?")),
                    "Status": job.get("status", "UNKNOWN"),
                    "Country": job.get("params", {}).get("country", "N/A"),
                    "Revision": job.get("params", {}).get("revision", "N/A"),
                    "Iterations": str(
                        job.get("params", {}).get("iterations", "N/A")
                    ),
                    "Trials": str(job.get("params", {}).get("trials", "N/A")),
                    "GCS Prefix": job.get("gcs_prefix", ""),
                    "Execution Details": job.get("execution_name", ""),
                }
            )

    # Add single run jobs from session state
    for exec_info in st.session_state.get("job_executions", []):
        exec_name = exec_info.get("execution_name", "")
        if exec_name:
            all_running_jobs.append(
                {
                    "Source": "Single",
                    "Job ID": f"single-{exec_info.get('timestamp', '?')}",
                    "Status": "RUNNING",
                    "Country": exec_info.get("country", "N/A"),
                    "Revision": exec_info.get("revision", "N/A"),
                    "Iterations": str(exec_info.get("iterations", "N/A")),
                    "Trials": str(exec_info.get("trials", "N/A")),
                    "GCS Prefix": exec_info.get("gcs_prefix", ""),
                    "Execution Details": exec_name,
                }
            )

    # Refresh button - force a full rerun to get latest status
    if st.button("🔄 Refresh Status", key=f"refresh_status_table_{key_prefix}"):
        # Clear any cached data
        st.session_state["status_refresh_timestamp"] = (
            datetime.utcnow().timestamp()
        )
        st.rerun()

    if not all_running_jobs:
        st.info("ℹ️ No jobs currently running")
    else:
        # Create DataFrame for display
        df = pd.DataFrame(all_running_jobs)

        # Display table with clickable links
        st.write(f"**{len(all_running_jobs)} job(s) currently running:**")

        # Create display dataframe with proper URLs
        display_df = df.copy()

        # Convert GCS Prefix to clickable URLs
        gcs_bucket = st.session_state.get("gcs_bucket", GCS_BUCKET)
        display_df["GCS Prefix"] = display_df["GCS Prefix"].apply(
            lambda x: (
                f"https://console.cloud.google.com/storage/browser/{gcs_bucket}/{x}"
                if x
                else ""
            )
        )

        # For execution details, create links to Cloud Run console
        if PROJECT_ID and REGION:
            display_df["Execution Details"] = display_df[
                "Execution Details"
            ].apply(
                lambda x: (
                    f"https://console.cloud.google.com/run/jobs/executions/details/{REGION}/{x.split('/')[-1]}?project={PROJECT_ID}"
                    if x and "/" in x
                    else x
                )
            )

        # Apply color coding to Status column using styled dataframe
        def color_status(val):
            if val == "RUNNING":
                return "background-color: #90EE90"  # Light green
            elif val == "LAUNCHING":
                return "background-color: #FFD700"  # Gold
            elif val == "SUCCEEDED":
                return "background-color: #32CD32"  # Lime green
            elif val in ["FAILED", "ERROR"]:
                return "background-color: #FF6B6B"  # Light red
            else:
                return ""

        # Use st.dataframe with column configuration for clickable links
        column_config = {
            "GCS Prefix": st.column_config.LinkColumn(
                "GCS Prefix",
                help="Click to open GCS folder in new tab",
                display_text="Open GCS ↗",
            ),
            "Execution Details": st.column_config.LinkColumn(
                "Execution Details",
                help="Click to open Cloud Run execution details in new tab",
                display_text="Open Execution ↗",
            ),
        }

        st.dataframe(
            display_df.style.applymap(color_status, subset=["Status"]),
            column_config=column_config,
            use_container_width=True,
            hide_index=True,
        )

    # Manual job status checker (for any execution)
    with st.expander("🔍 Manual Status Check", expanded=False):
        st.write("Check status of any job by entering the execution ID:")

        # Build the prefix from environment variables
        exec_prefix = ""
        if PROJECT_ID and REGION and TRAINING_JOB_NAME:
            exec_prefix = f"projects/{PROJECT_ID}/locations/{REGION}/jobs/{TRAINING_JOB_NAME}/executions/"

        # Get default execution ID (last part only)
        default_exec_id = ""
        if st.session_state.get("job_executions"):
            last_exec = st.session_state.job_executions[-1]["execution_name"]
            if "/executions/" in last_exec:
                default_exec_id = last_exec.split("/executions/")[-1]

        exec_id = st.text_input(
            "Execution ID (short form)",
            value=default_exec_id,
            key=f"exec_id_manual_{key_prefix}",
            help="Enter just the execution ID (e.g., 'mmm-app-dev-training-abc123'). The full path will be constructed automatically.",
        )

        if exec_prefix:
            st.caption(
                f"Full path: `{exec_prefix}{exec_id or '<execution-id>'}`"
            )

        if st.button(
            "🔍 Check Status", key=f"check_status_manual_{key_prefix}"
        ):
            if not exec_id:
                st.warning("Please enter an execution ID.")
            elif not exec_prefix:
                st.error(
                    "⚠️ Configuration Error: Missing required environment variables."
                )
                st.info(
                    "**Required environment variables:**\n"
                    "- `PROJECT_ID`\n"
                    "- `REGION`\n"
                    "- `TRAINING_JOB_NAME`\n\n"
                    "Please ensure these are set in the web service environment."
                )
            else:
                exec_name = exec_prefix + exec_id
                try:
                    with st.spinner("Fetching status..."):
                        status_info = job_manager.get_execution_status(
                            exec_name
                        )
                        st.json(status_info)
                except Exception as e:
                    error_msg = str(e)
                    if "403" in error_msg or "Permission denied" in error_msg:
                        st.error(
                            "⚠️ Permission Error: The service account doesn't have "
                            "permission to view job execution status."
                        )
                        st.info(
                            "**To fix this issue:**\n"
                            "1. The web service account needs Cloud Run permissions\n"
                            "2. Ensure it has 'roles/run.developer' or 'roles/run.admin'\n"
                            "3. Check the terraform configuration in infra/terraform/main.tf"
                        )
                        with st.expander("Technical Details"):
                            st.code(error_msg)
                    else:
                        st.error(f"Status check failed: {e}")


def set_queue_running(
    queue_name: str, running: bool, bucket_name: Optional[str] = None
) -> None:
    """Toggle the persisted queue_running flag and update session without dropping entries."""
    st.session_state.queue_running = bool(running)
    st.session_state.queue_saved_at = save_queue_to_gcs(
        queue_name,
        entries=st.session_state.get("job_queue", []),
        queue_running=running,
        bucket_name=bucket_name,
    )


def maybe_refresh_queue_from_gcs(force: bool = False):
    """Refresh local session state from GCS if remote changed (or force=True)."""
    payload = load_queue_payload(st.session_state.queue_name)
    remote_saved_at = payload.get("saved_at")
    if force or (
        remote_saved_at
        and remote_saved_at != st.session_state.get("queue_saved_at")
    ):
        st.session_state.job_queue = payload.get("entries", [])
        st.session_state.queue_running = payload.get(
            "queue_running", st.session_state.get("queue_running", False)
        )
        st.session_state.queue_saved_at = remote_saved_at


# ---- Builder defaults independent of Tab 2 ----
_builder_defaults = dict(
    country="de",
    iterations=200,
    trials=5,
    train_size="0.7,0.9",
    revision="r100",
    date_input=time.strftime("%Y-%m-%d"),  # Keep for backward compatibility
    start_date="2024-01-01",  # New field
    end_date=time.strftime("%Y-%m-%d"),  # New field
    dep_var="UPLOAD_VALUE",
    dep_var_type="revenue",  # New field
    date_var="date",
    adstock="geometric",
    hyperparameter_preset="Meshed recommend",  # New field
    resample_freq="none",
    resample_agg="sum",
    gcs_bucket=st.session_state.get("gcs_bucket", GCS_BUCKET),
)


def _make_normalizer(defaults: dict):
    def _normalize_row(row: pd.Series) -> dict:
        def _g(v, default):
            return row.get(v) if (v in row and pd.notna(row[v])) else default  # type: ignore

        # Support backward compatibility: if start_date/end_date not present, use date_input
        start_date_val = _g(
            "start_date", defaults.get("start_date", "2024-01-01")
        )
        end_date_val = _g(
            "end_date", defaults.get("end_date", time.strftime("%Y-%m-%d"))
        )
        date_input_val = _g(
            "date_input", defaults.get("date_input", time.strftime("%Y-%m-%d"))
        )

        # If neither start_date nor end_date are provided, fall back to date_input
        if not str(start_date_val).strip():
            start_date_val = "2024-01-01"
        if not str(end_date_val).strip():
            end_date_val = date_input_val

        # Parse custom hyperparameters if hyperparameter_preset is "Custom"
        hyperparameter_preset_val = str(
            _g(
                "hyperparameter_preset",
                defaults.get("hyperparameter_preset", "Meshed recommend"),
            )
        )
        custom_hyperparameters = {}

        if hyperparameter_preset_val == "Custom":
            # Check for per-variable hyperparameter columns (new format)
            # Pattern: {VAR_NAME}_alphas, {VAR_NAME}_gammas, {VAR_NAME}_thetas, etc.
            for col in row.index:
                if pd.notna(row[col]) and str(row[col]).strip():
                    # Check if column matches per-variable pattern
                    if col.endswith(
                        ("_alphas", "_gammas", "_thetas", "_shapes", "_scales")
                    ):
                        try:
                            # Parse the value - could be a list string like "[1.0, 3.0]" or comma-separated "1.0, 3.0"
                            val_str = str(row[col]).strip()
                            if val_str.startswith("[") and val_str.endswith(
                                "]"
                            ):
                                # JSON-like list format
                                import json

                                custom_hyperparameters[col] = json.loads(
                                    val_str
                                )
                            elif "," in val_str:
                                # Comma-separated format
                                custom_hyperparameters[col] = [
                                    float(x.strip()) for x in val_str.split(",")
                                ]
                            else:
                                # Single value or other format - skip
                                pass
                        except (ValueError, TypeError, json.JSONDecodeError):
                            pass

            # Backward compatibility: check for old global format (alphas_min/max, etc.)
            if not custom_hyperparameters:
                for param in [
                    "alphas_min",
                    "alphas_max",
                    "gammas_min",
                    "gammas_max",
                    "thetas_min",
                    "thetas_max",
                    "shapes_min",
                    "shapes_max",
                    "scales_min",
                    "scales_max",
                ]:
                    val = _g(param, "")
                    if val and str(val).strip():
                        try:
                            custom_hyperparameters[param] = float(val)
                        except (ValueError, TypeError):
                            pass

        # Support new revision format (revision_tag + revision_number) or old revision field
        # Priority: if revision_tag and revision_number exist, use them to build revision
        # Otherwise, use revision field directly
        revision_tag_val = _g("revision_tag", "")
        revision_number_val = _g("revision_number", "")

        if (
            revision_tag_val
            and str(revision_tag_val).strip()
            and revision_number_val
        ):
            # New format: construct revision from tag and number
            try:
                revision_val = f"{str(revision_tag_val).strip()}_{int(float(revision_number_val))}"
            except (ValueError, TypeError):
                # Fall back to old revision field if number parsing fails
                revision_val = str(_g("revision", defaults["revision"]))
        else:
            # Old format: use revision field directly
            revision_val = str(_g("revision", defaults["revision"]))

        result = {
            "country": str(_g("country", defaults["country"])),
            "revision": revision_val,
            "date_input": str(
                date_input_val
            ),  # Keep for backward compatibility
            "start_date": str(start_date_val),  # New field
            "end_date": str(end_date_val),  # New field
            "iterations": (
                int(float(_g("iterations", defaults["iterations"])))  # type: ignore
                if str(_g("iterations", defaults["iterations"])).strip()
                else int(defaults["iterations"])
            ),
            "trials": (
                int(float(_g("trials", defaults["trials"])))  # type: ignore
                if str(_g("trials", defaults["trials"])).strip()
                else int(defaults["trials"])
            ),
            "train_size": str(_g("train_size", defaults["train_size"])),
            "paid_media_spends": str(_g("paid_media_spends", "")),
            "paid_media_vars": str(_g("paid_media_vars", "")),
            "context_vars": str(_g("context_vars", "")),
            "factor_vars": str(_g("factor_vars", "")),
            "organic_vars": str(_g("organic_vars", "")),
            "gcs_bucket": str(_g("gcs_bucket", defaults["gcs_bucket"])),
            "data_gcs_path": str(_g("data_gcs_path", "")),  # New field
            "table": str(_g("table", "")),
            "query": str(_g("query", "")),
            "dep_var": str(_g("dep_var", defaults["dep_var"])),
            "dep_var_type": str(
                _g("dep_var_type", defaults.get("dep_var_type", "revenue"))
            ),  # New field
            "date_var": str(_g("date_var", defaults["date_var"])),
            "adstock": str(_g("adstock", defaults["adstock"])),
            "hyperparameter_preset": hyperparameter_preset_val,
            "resample_freq": _normalize_resample_freq(
                str(_g("resample_freq", defaults["resample_freq"]))
            ),
            "resample_agg": _normalize_resample_agg(
                str(_g("resample_agg", defaults["resample_agg"]))
            ),
            "annotations_gcs_path": str(_g("annotations_gcs_path", "")),
        }

        # Add custom_hyperparameters if present
        if custom_hyperparameters:
            result["custom_hyperparameters"] = custom_hyperparameters

        return result

    return _normalize_row


_normalize_row = _make_normalizer(_builder_defaults)


# ── Flash helpers: show a persistent banner for N seconds (until dismissed)
def _set_flash(slot: str, msg: str, kind: str = "warning", ttl_sec: int = 10):
    st.session_state.setdefault("_flash_store", {})
    st.session_state["_flash_store"][slot] = {
        "msg": msg,
        "kind": kind,
        "expires": time.time() + ttl_sec,
    }


def _render_flash(slot: str):
    store = st.session_state.get("_flash_store", {})
    info = store.get(slot)
    if not info:
        return
    if time.time() > info["expires"]:
        store.pop(slot, None)
        return
    fn = {
        "info": st.info,
        "warning": st.warning,
        "success": st.success,
        "error": st.error,
    }.get(info["kind"], st.info)
    c1, c2 = st.columns([0.97, 0.03])
    with c1:
        fn(info["msg"])
    with c2:
        if st.button("✕", key=f"dismiss_{slot}"):
            store.pop(slot, None)
            st.rerun()


def _toast_dupe_summary(stage: str, reasons: dict, added_count: int = 0):
    """
    stage: 'Append to builder' | 'Enqueue'
    reasons: { key: [row_indexes...] }
    """
    name_map = {
        "in_builder": "already in builder",
        "in_queue": "already in queue",
        "in_job_history": "already finished (job_history)",
        "missing_data_source": "missing table/query",
    }
    total_skipped = sum(len(v) for v in reasons.values())
    if total_skipped:
        parts = [f"{len(v)} {name_map[k]}" for k, v in reasons.items() if v]
        msg = (
            f"{stage}: added {added_count} new, skipped {total_skipped} — "
            + ", ".join(parts)
        )
        st.toast(f"⚠️ {msg}")
        # Persist longer only for the mix case (some added AND some skipped)
        if added_count > 0:
            _set_flash("batch_dupes", f"⚠️ {msg}", kind="warning", ttl_sec=20)

    if added_count == 0:
        st.info(
            f"No new rows to {stage.lower()} (duplicates or missing data source)."
        )


def _hydrate_times_from_status(entry: dict) -> dict:
    """
    Returns dict possibly containing start_time, end_time, duration_minutes
    by reading <gcs_prefix>/status.json. Empty dict if unavailable.
    """
    try:
        bucket = entry.get("gcs_bucket") or st.session_state.get(
            "gcs_bucket", GCS_BUCKET
        )
        gcs_prefix = entry.get("gcs_prefix")
        if not bucket or not gcs_prefix:
            return {}
        client = storage.Client()
        blob = client.bucket(bucket).blob(f"{gcs_prefix}/status.json")
        if not blob.exists():
            return {}
        data = json.loads(blob.download_as_bytes())
        out = {}
        if isinstance(data, dict):
            if data.get("start_time"):
                out["start_time"] = data.get("start_time")
            if data.get("end_time"):
                out["end_time"] = data.get("end_time")
            if data.get("duration_minutes") is not None:
                out["duration_minutes"] = data.get("duration_minutes")
        return out
    except Exception:
        # Swallow errors; we’ll just use fallbacks below.
        return {}


def _queue_tick():
    # Advance the queue atomically (lease/launch OR update running)
    logger.info("Starting queue tick")
    try:
        res = queue_tick_once_headless(
            st.session_state.queue_name,
            st.session_state.get("gcs_bucket", GCS_BUCKET),
            launcher=prepare_and_launch_job,
        )
        logger.info(f"Queue tick result: {res}")

        # Log launch failures prominently
        if not res.get("ok") and "launch failed" in res.get("message", ""):
            logger.error(f"[QUEUE_ERROR] Launch failure detected in queue tick")
            logger.error(f"[QUEUE_ERROR] Error message: {res.get('message')}")
            logger.error(f"[QUEUE_ERROR] Queue: {st.session_state.queue_name}")

    except Exception as e:
        logger.exception(f"Queue tick_once_headless failed: {e}")
        raise

    # Always refresh local from GCS after a tick
    maybe_refresh_queue_from_gcs(force=True)

    # Sweep finished jobs into history and remove them from queue
    q = st.session_state.job_queue or []
    logger.info(f"After tick: {len(q)} jobs in queue")
    if not q:
        logger.info("Queue is now empty after tick")
        return

    remaining = []
    moved = 0
    for entry in q:
        status = (entry.get("status") or "").upper()
        if status in {"SUCCEEDED", "FAILED", "CANCELLED", "COMPLETED", "ERROR"}:
            final_state = (
                "SUCCEEDED" if status in {"SUCCEEDED", "COMPLETED"} else status
            )
            exec_full = entry.get("execution_name") or ""
            exec_short = exec_full.split("/")[-1] if exec_full else ""
            p = entry.get("params", {}) or {}

            times = _hydrate_times_from_status(entry)
            start_time = (
                entry.get("start_time")
                or times.get("start_time")
                or entry.get(
                    "timestamp"
                )  # fallback to launch timestamp if that’s all we have
            )
            end_time = (
                times.get("end_time")
                or datetime.utcnow().isoformat(timespec="seconds") + "Z"
            )
            duration_minutes = times.get("duration_minutes")

            append_row_to_job_history(
                {
                    "job_id": entry.get("gcs_prefix") or entry.get("id"),
                    "state": final_state,
                    # All queue/builder params:
                    "country": p.get("country"),
                    "revision": p.get("revision"),
                    "date_input": p.get("date_input"),
                    "iterations": p.get("iterations"),
                    "trials": p.get("trials"),
                    "train_size": p.get("train_size"),
                    "paid_media_spends": p.get("paid_media_spends"),
                    "paid_media_vars": p.get("paid_media_vars"),
                    "context_vars": p.get("context_vars"),
                    "factor_vars": p.get("factor_vars"),
                    "organic_vars": p.get("organic_vars"),
                    "gcs_bucket": p.get("gcs_bucket"),
                    "table": p.get("table"),
                    "query": p.get("query"),
                    "dep_var": p.get("dep_var"),
                    "date_var": p.get("date_var"),
                    "adstock": p.get("adstock"),
                    # Exec/times (hydrated)
                    "start_time": start_time,
                    "end_time": end_time,
                    "duration_minutes": duration_minutes,
                    "gcs_prefix": entry.get("gcs_prefix"),
                    "bucket": entry.get("gcs_bucket")
                    or st.session_state.get("gcs_bucket", GCS_BUCKET),
                    "exec_name": exec_short,
                    "execution_name": exec_full,
                    # Queue message
                    "message": entry.get("message", ""),
                },
                st.session_state.get("gcs_bucket", GCS_BUCKET),
            )
            moved += 1

            # Enhanced logging for job history movement
            error_msg = entry.get("message", "")
            if final_state in ("ERROR", "FAILED", "CANCELLED"):
                logger.error(
                    f"[QUEUE_ERROR] Moved job {entry.get('id')} to history with status {final_state}"
                )
                logger.error(
                    f"[QUEUE_ERROR] Job details - Country: {p.get('country')}, "
                    f"Revision: {p.get('revision')}, GCS: {entry.get('gcs_prefix')}"
                )
                logger.error(f"[QUEUE_ERROR] Error message: {error_msg}")
            else:
                logger.info(
                    f"Moved job {entry.get('id')} to history with status {final_state}"
                )
        else:
            remaining.append(entry)

    if moved:
        # Persist trimmed queue
        logger.info(
            f"Moved {moved} finished job(s) to history, {len(remaining)} remaining in queue"
        )
        st.session_state.job_queue = remaining
        st.session_state.queue_saved_at = save_queue_to_gcs(
            st.session_state.queue_name,
            st.session_state.job_queue,
            queue_running=st.session_state.queue_running,
        )
        # bump nonce so job history table re-renders
        st.session_state["job_history_nonce"] = (
            st.session_state.get("job_history_nonce", 0) + 1
        )

        # If jobs were completed and there are still PENDING jobs with no RUNNING jobs,
        # immediately launch the next one
        if remaining:
            pending_count = sum(
                1 for e in remaining if e.get("status") == "PENDING"
            )
            running_count = sum(
                1
                for e in remaining
                if e.get("status") in ("RUNNING", "LAUNCHING")
            )

            if (
                pending_count > 0
                and running_count == 0
                and st.session_state.get("queue_running")
            ):
                logger.info(
                    f"[QUEUE] Auto-launching next job: {pending_count} pending, {running_count} running"
                )
                # Recursively tick again to launch the next PENDING job
                try:
                    res = queue_tick_once_headless(
                        st.session_state.queue_name,
                        st.session_state.get("gcs_bucket", GCS_BUCKET),
                        launcher=prepare_and_launch_job,
                    )
                    logger.info(f"[QUEUE] Auto-launch tick result: {res}")
                    # Refresh queue again after the auto-launch
                    maybe_refresh_queue_from_gcs(force=True)
                except Exception as e:
                    logger.exception(f"[QUEUE] Auto-launch tick failed: {e}")


def _auto_refresh_and_tick(interval_ms: int = 2000):
    """
    If the queue is marked as running, perform one tick and schedule a client-side
    refresh so the page re-runs and we tick again.
    """
    if not st.session_state.get("queue_running"):
        logger.info("[QUEUE] Auto-refresh skipped: queue_running is False")
        return

    # Check queue before tick
    q = st.session_state.get("job_queue") or []
    logger.info(f"[QUEUE] Auto-refresh: queue has {len(q)} entries before tick")

    if len(q) == 0:
        logger.info("[QUEUE] Auto-refresh stopping: queue is empty")
        st.session_state.queue_running = False
        return

    # Log job statuses before tick
    pending_count = sum(1 for e in q if e.get("status") == "PENDING")
    running_count = sum(
        1 for e in q if e.get("status") in ("RUNNING", "LAUNCHING")
    )
    completed_count = sum(
        1
        for e in q
        if e.get("status")
        in ("SUCCEEDED", "FAILED", "ERROR", "CANCELLED", "COMPLETED")
    )
    logger.info(
        f"[QUEUE] Before tick: {pending_count} pending, {running_count} running, {completed_count} completed"
    )

    # Advance the queue once
    _queue_tick()

    # Check queue after tick
    q_after = st.session_state.get("job_queue") or []
    logger.info(f"[QUEUE] After tick: queue has {len(q_after)} entries")

    # If queue is now empty, stop running
    if len(q_after) == 0:
        logger.info(
            "[QUEUE] Queue is now empty after tick, stopping auto-refresh"
        )
        st.session_state.queue_running = False
        return

    # Schedule a client-side refresh
    logger.info(f"[QUEUE] Scheduling page refresh in {interval_ms}ms")
    st.markdown(
        f"<script>setTimeout(function(){{window.location.reload();}}, {interval_ms});</script>",
        unsafe_allow_html=True,
    )


def _sorted_with_controls(
    df: pd.DataFrame, prefix: str, exclude_cols=("Delete",)
):
    """
    Render sorting controls (outside forms), return (sorted_df, nonce).
    Bumps a nonce when sort params change so st.data_editor re-renders.
    """
    cols = [c for c in df.columns if c not in exclude_cols]
    if not cols:
        return df, 0

    sort_col_key = f"{prefix}_sort_col"
    sort_asc_key = f"{prefix}_sort_asc"
    prev_key = f"{prefix}_sort_prev"
    nonce_key = f"{prefix}_sort_nonce"

    # Initialize only if not present
    st.session_state.setdefault(sort_col_key, cols[0])
    if st.session_state[sort_col_key] not in cols:
        st.session_state[sort_col_key] = cols[0]
    st.session_state.setdefault(sort_asc_key, True)

    c1, c2 = st.columns([3, 1])
    with c1:
        col = st.selectbox(
            "Sort by", options=cols, key=sort_col_key
        )  # no index/value
    with c2:
        asc = st.toggle("Ascending", key=sort_asc_key)  # no value=

    prev = st.session_state.get(prev_key)
    cur = (col, asc)
    if prev != cur:
        st.session_state[prev_key] = cur
        st.session_state[nonce_key] = st.session_state.get(nonce_key, 0) + 1

    sorted_df = df.sort_values(
        by=col, ascending=asc, na_position="last", kind="mergesort"  # type: ignore
    )
    return sorted_df, st.session_state.get(nonce_key, 0)


# --- session defaults (safe across all pages) ---
def ensure_session_defaults():
    ss = st.session_state
    # Snowflake
    ss.setdefault("sf_connected", False)
    ss.setdefault("sf_params", {})  # user/account/warehouse/...
    ss.setdefault("_sf_private_key_bytes", None)  # key bytes when provided

    # Buckets (use whatever your environment provides)
    ss.setdefault(
        "gcs_bucket",
        os.getenv("GCS_BUCKET") or os.getenv("BUCKET") or "robyn-demo-bucket",
    )

    # Single-run bookkeeping
    ss.setdefault("job_executions", [])  # list of execution dicts
    ss.setdefault("last_timings", {})  # {'df':..., 'timestamp':...}

    # Queue defaults
    ss.setdefault("queue_name", "default")
    ss.setdefault("job_queue", [])
    ss.setdefault("queue_running", False)
    ss.setdefault("queue_saved_at", None)

    # Upload/editor scaffolding
    ss.setdefault("uploaded_df", pd.DataFrame())
    ss.setdefault("uploaded_fingerprint", None)

    # Builder table for queue
    if "qb_df" not in ss:
        ss.qb_df = pd.DataFrame()


# --- end session defaults ---

# ─────────────────────────────
# UI layout
# ─────────────────────────────
