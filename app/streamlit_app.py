# app.py — Streamlit front-end for launching & monitoring Robyn training jobs on Cloud Run Jobs
import io
import json
import logging
import os
import re
import time
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

import pandas as pd
import snowflake.connector as sf
import streamlit as st
from google.cloud import run_v2, storage

from data_processor import DataProcessor
from app_shared import (
    PROJECT_ID, REGION, TRAINING_JOB_NAME, GCS_BUCKET,
    timed_step, parse_train_size, effective_sql, _sf_params_from_env,
    ensure_sf_conn, run_sql, upload_to_gcs, read_status_json,
    build_job_config_from_params, _sanitize_queue_name, _queue_blob_path,
    load_queue_from_gcs, save_queue_to_gcs, load_queue_payload, queue_tick_once_headless
)

# ─────────────────────────────
# Page & logging setup
# ─────────────────────────────
st.set_page_config(page_title="Robyn MMM Trainer", layout="wide")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

query_params = st.query_params
logger.info(
    "Starting app/streamlit_app.py", extra={"query_params": dict(query_params)}
)

# Health check endpoint (returns JSON, does not render UI)
if query_params.get("health") == "true":
    try:
        from health import health_checker  # optional module

        st.json(health_checker.check_container_health())
    except Exception as e:
        st.json(
            {
                "status": "error",
                "timestamp": datetime.now().isoformat(),
                "error": str(e),
            }
        )
    st.stop()


# ─────────────────────────────
# Environment / constants
# ─────────────────────────────
PROJECT_ID = os.getenv("PROJECT_ID")
REGION = os.getenv("REGION", "europe-west1")
TRAINING_JOB_NAME = os.getenv("TRAINING_JOB_NAME")  # short or FQN
GCS_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")

# When queueing, wait until the current job is RUNNING before writing the next "latest" config
SAFE_LAG_SECONDS_AFTER_RUNNING = int(
    os.getenv("SAFE_LAG_SECONDS_AFTER_RUNNING", "5")
)

# ─────────────────────────────
# Persistent queue in GCS
# ─────────────────────────────
QUEUE_ROOT = os.getenv(
    "QUEUE_ROOT", "robyn-queues"
)  # gs://<bucket>/robyn-queues/<name>/queue.json
DEFAULT_QUEUE_NAME = os.getenv("DEFAULT_QUEUE_NAME", "default")

# Session defaults
st.session_state.setdefault("job_executions", [])
st.session_state.setdefault("gcs_bucket", GCS_BUCKET)
st.session_state.setdefault("last_timings", None)
st.session_state.setdefault("auto_refresh", False)

# Persistent Snowflake session objects/params
st.session_state.setdefault("sf_params", None)
st.session_state.setdefault("sf_connected", False)
st.session_state.setdefault("sf_conn", None)

# Batch queue state
st.session_state.setdefault("job_queue", [])  # list of dicts (entries below)
st.session_state.setdefault("queue_running", False)

# Persistent queue session vars
st.session_state.setdefault("queue_name", DEFAULT_QUEUE_NAME)
st.session_state.setdefault("queue_loaded_from_gcs", False)

# Queue entry shape:
# {
#   "id": int,
#   "params": {...},            # fields matching single-run config
#   "status": "PENDING|RUNNING|SUCCEEDED|FAILED|CANCELLED|ERROR",
#   "timestamp": None|str,
#   "execution_name": None|str,
#   "gcs_prefix": None|str,
#   "message": str
# }


# ─────────────────────────────
# Small utils
# ─────────────────────────────





def get_data_processor():
    return DataProcessor()


@st.cache_resource
def get_job_manager():
    return CloudRunJobManager(PROJECT_ID, REGION)


data_processor = get_data_processor()
job_manager = get_job_manager()


# ─────────────────────────────
# Snowflake connection (persistent)
# ─────────────────────────────
def _connect_snowflake(
    user, password, account, warehouse, database, schema, role
):
    return sf.connect(
        user=user,
        password=password,
        account=account,
        warehouse=warehouse,
        database=database,
        schema=schema,
        role=role if role else None,
    )


# --- Snowflake env-fallback (headless) ---




def prepare_and_launch_job(params: dict) -> dict:
    """
    One complete job: query SF -> parquet -> upload -> write config (timestamped + latest) -> run Cloud Run Job.
    Returns exec_info dict.
    """
    # Required fields
    sql_eff = params.get("query") or effective_sql(
        params.get("table", ""), params.get("query", "")
    )
    if not sql_eff:
        raise ValueError("Missing SQL/Table for job.")

    gcs_bucket = params.get("gcs_bucket") or st.session_state["gcs_bucket"]
    timestamp = datetime.utcnow().strftime("%m%d_%H%M%S")
    gcs_prefix = f"robyn/{params['revision']}/{params['country']}/{timestamp}"

    with tempfile.TemporaryDirectory() as td:
        timings: List[dict] = []
        # 1) Query Snowflake
        with timed_step("Query Snowflake", timings):
            df = run_sql(sql_eff)
        # 2) Parquet
        with timed_step("Convert to Parquet", timings):
            parquet_path = os.path.join(td, "input_data.parquet")
            data_processor.csv_to_parquet(df, parquet_path)
        # 3) Upload data
        with timed_step("Upload data to GCS", timings):
            data_blob = f"training-data/{timestamp}/input_data.parquet"
            data_gcs_path = upload_to_gcs(gcs_bucket, parquet_path, data_blob)

        # Optional annotations: use row override if present, else uploaded file not supported in batch
        annotations_gcs_path = params.get("annotations_gcs_path") or None

        # 4) Create config (timestamped + latest)
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
            # latest copy (the running job will read this)
            _ = upload_to_gcs(
                gcs_bucket,
                config_path,
                "training-configs/latest/job_config.json",
            )

        # 5) Launch job
        with timed_step("Launch training job", timings):
            execution_name = job_manager.create_execution(TRAINING_JOB_NAME)

        # Seed timings.csv (web-side steps)
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

    exec_info = {
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
    return exec_info


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
    }






def set_queue_running(
    queue_name: str, running: bool, bucket_name: Optional[str] = None
) -> None:
    """
    Toggle the persisted queue_running flag and update session.
    """
    # Load to ensure we don't clobber entries
    doc = load_queue_from_gcs(queue_name, bucket_name=bucket_name)
    st.session_state.queue_running = bool(running)
    # Save existing entries + new flag
    save_queue_to_gcs(
        queue_name,
        entries=doc.get("entries", []),
        queue_running=running,
        bucket_name=bucket_name,
    )


# Track when we last loaded the queue
st.session_state.setdefault("queue_saved_at", None)



def maybe_refresh_queue_from_gcs(force: bool = False):
    """Refresh local session state from GCS if remote changed (or force=True)."""
    payload = load_queue_payload(st.session_state.queue_name)
    remote_saved_at = payload.get("saved_at")
    if force or (
        remote_saved_at
        and remote_saved_at != st.session_state.get("queue_saved_at")
    ):
        st.session_state.job_queue = payload.get("entries", [])
        # keep UI toggle in sync too (useful when multiple sessions are open)
        st.session_state.queue_running = payload.get(
            "queue_running", st.session_state.get("queue_running", False)
        )
        st.session_state.queue_saved_at = remote_saved_at


