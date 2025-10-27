# streamlit_app.py — Streamlit front-end for launching & monitoring Robyn training jobs on Cloud Run Jobs
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

# Instantiate shared resources
data_processor = get_data_processor()
job_manager = get_job_manager()

TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED", "COMPLETED", "ERROR"}

# ─────────────────────────────
# Page & logging setup
# ─────────────────────────────
st.set_page_config(page_title="Robyn MMM Trainer", layout="wide")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Call it once, near the top of your app before any other UI
require_login_and_domain()

query_params = st.query_params
logger.info(
    "Starting app/streamlit_app.py",
    extra={"query_params": dict(query_params)},
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
# streamlit_app.py


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
    Returns exec_info dict with execution_name, timestamp, gcs_prefix, etc.
    """
    # 0) Validate & resolve SQL
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
            data_gcs_path = upload_to_gcs(gcs_bucket, parquet_path, data_blob)

        # Optional annotations (batch: pass a gs:// in params)
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
            # "latest" copy (job reads this)
            _ = upload_to_gcs(
                gcs_bucket,
                config_path,
                "training-configs/latest/job_config.json",
            )

        # 5) Launch job (Cloud Run Jobs)
        with timed_step("Launch training job", timings):
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
res = handle_queue_tick_from_query_params(
    st.query_params,
    st.session_state.get("gcs_bucket", GCS_BUCKET),
    launcher=prepare_and_launch_job,
)
if isinstance(res, dict) and res:
    st.json(res)
    st.stop()


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


def render_jobs_job_history(key_prefix: str = "single") -> None:
    with st.expander("📚 Job History (from GCS)", expanded=False):
        # Refresh control first (button triggers a rerun)
        if st.button(
            "🔁 Refresh job_history", key=f"refresh_job_history_{key_prefix}"
        ):
            # bump a nonce so the dataframe widget key changes and re-renders
            st.session_state["job_history_nonce"] = (
                st.session_state.get("job_history_nonce", 0) + 1
            )
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
            width="stretch",
            use_container_width=True,
            hide_index=True,
            key=f"job_history_view_{key_prefix}_{st.session_state.get('job_history_nonce', 0)}",
        )


def render_job_status_monitor(key_prefix: str = "single") -> None:
    """Status UI usable in both tabs, even without a session job."""
    st.subheader("📊 Job Status Monitor")

    # Prefer the latest session execution if present; allow manual input always.
    default_exec = (
        (st.session_state.job_executions[-1]["execution_name"])
        if st.session_state.get("job_executions")
        else ""
    )
    exec_name = st.text_input(
        "Execution resource name (paste one to check any run)",
        value=default_exec,
        key=f"exec_input_{key_prefix}",
    )

    if st.button("🔍 Check Status", key=f"check_status_{key_prefix}"):
        if not exec_name:
            st.warning("Paste an execution resource name to check.")
        else:
            try:
                status_info = job_manager.get_execution_status(exec_name)
                st.json(status_info)
            except Exception as e:
                st.error(f"Status check failed: {e}")

    # Quick results/log viewer driven by the job_history (no execution name required)
    with st.expander("📁 View Results (pick from job_history)", expanded=False):
        try:
            df_led = read_job_history_from_gcs(
                st.session_state.get("gcs_bucket", GCS_BUCKET)
            )
        except Exception as e:
            st.error(f"Failed to read job_history: {e}")
            df_led = None

        if df_led is None or df_led.empty or "gcs_prefix" not in df_led.columns:
            st.info("No job_history entries with results yet.")
        else:
            df_led = df_led.copy()

            # Build readable labels
            def _label(r):
                return f"[{r.get('state','?')}] {r.get('country','?')}/{r.get('revision','?')} · {r.get('gcs_prefix','—')}"

            df_led["__label__"] = df_led.apply(_label, axis=1)
            idx = st.selectbox(
                "Pick a job",
                options=list(df_led.index),
                format_func=lambda i: df_led.loc[i, "__label__"],
                key=f"job_history_pick_{key_prefix}",
            )

            # ...
            row = df_led.loc[idx]

            # Sanitize bucket and prefix values to avoid pd.NA truthiness
            bucket_view = row.get(
                "bucket", st.session_state.get("gcs_bucket", GCS_BUCKET)
            )
            if pd.isna(bucket_view) or not str(bucket_view).strip():
                bucket_view = st.session_state.get("gcs_bucket", GCS_BUCKET)

            gcs_prefix_view = row.get("gcs_prefix")
            if pd.isna(gcs_prefix_view) or not str(gcs_prefix_view).strip():
                gcs_prefix_view = None

            if gcs_prefix_view is not None:
                st.info(
                    f"Results location: gs://{bucket_view}/{gcs_prefix_view}/"
                )
                try:
                    client = storage.Client()
                    bucket_obj = client.bucket(bucket_view)
                    log_blob = bucket_obj.blob(
                        f"{gcs_prefix_view}/robyn_console.log"
                    )
                    if log_blob.exists():
                        log_bytes = log_blob.download_as_bytes()
                        tail = (
                            log_bytes[-2000:]
                            if len(log_bytes) > 2000
                            else log_bytes
                        )
                        st.text_area(
                            "Training Log (last 2000 chars):",
                            value=tail.decode("utf-8", errors="replace"),
                            height=240,
                            key=f"log_tail_{key_prefix}",
                        )
                        st.download_button(
                            "Download full training log",
                            data=log_bytes,
                            file_name=f"robyn_training_{row.get('job_id','')}.log",
                            mime="text/plain",
                            key=f"dl_log_{key_prefix}",
                        )
                    else:
                        st.info("Training log not yet available for this job.")
                except Exception as e:
                    st.warning(f"Could not fetch training log: {e}")
            # ...


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
    country="fr",
    iterations=200,
    trials=5,
    train_size="0.7,0.9",
    revision="r100",
    date_input=time.strftime("%Y-%m-%d"),
    dep_var="UPLOAD_VALUE",
    date_var="date",
    adstock="geometric",
    resample_freq="none",
    resample_agg="sum",  # NEW
    gcs_bucket=st.session_state.get("gcs_bucket", GCS_BUCKET),
)


def _make_normalizer(defaults: dict):
    def _normalize_row(row: pd.Series) -> dict:
        def _g(v, default):
            return row.get(v) if (v in row and pd.notna(row[v])) else default

        return {
            "country": str(_g("country", defaults["country"])),
            "revision": str(_g("revision", defaults["revision"])),
            "date_input": str(_g("date_input", defaults["date_input"])),
            "iterations": (
                int(float(_g("iterations", defaults["iterations"])))
                if str(_g("iterations", defaults["iterations"])).strip()
                else int(defaults["iterations"])
            ),
            "trials": (
                int(float(_g("trials", defaults["trials"])))
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
            "table": str(_g("table", "")),
            "query": str(_g("query", "")),
            "dep_var": str(_g("dep_var", defaults["dep_var"])),
            "date_var": str(_g("date_var", defaults["date_var"])),
            "adstock": str(_g("adstock", defaults["adstock"])),
            "resample_freq": _normalize_resample_freq(
                str(_g("resample_freq", defaults["resample_freq"]))
            ),
            "resample_agg": _normalize_resample_agg(
                str(_g("resample_agg", defaults["resample_agg"]))
            ),
            "annotations_gcs_path": str(_g("annotations_gcs_path", "")),
        }

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
    res = queue_tick_once_headless(
        st.session_state.queue_name,
        st.session_state.get("gcs_bucket", GCS_BUCKET),
        launcher=prepare_and_launch_job,
    )

    # Always refresh local from GCS after a tick
    maybe_refresh_queue_from_gcs(force=True)

    # Sweep finished jobs into history and remove them from queue
    q = st.session_state.job_queue or []
    if not q:
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
        else:
            remaining.append(entry)

    if moved:
        # Persist trimmed queue
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


def _auto_refresh_and_tick(interval_ms: int = 2000):
    """
    If the queue is marked as running, perform one tick and schedule a client-side
    refresh so the page re-runs and we tick again.
    """
    if not st.session_state.get("queue_running"):
        return

    # If there’s nothing left, stop auto-refreshing.
    q = st.session_state.get("job_queue") or []
    if len(q) == 0:
        st.session_state.queue_running = False
        return

    # Advance the queue once
    _queue_tick()

    # Schedule a client-side refresh
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
        by=col, ascending=asc, na_position="last", kind="mergesort"
    )
    return sorted_df, st.session_state.get(nonce_key, 0)


# ─────────────────────────────
# UI layout
# ─────────────────────────────
st.title("Robyn MMM Trainer")
tab_conn, tab_single, tab_queue = st.tabs(
    ["1) Snowflake Connection", "2) Single Job Training", "3) Queue Training"]
)

_init_sf_once()

# Auto-load persisted queue once per session (per queue_name)
if not st.session_state.queue_loaded_from_gcs:
    try:
        payload = load_queue_from_gcs(st.session_state.queue_name)
        st.session_state.job_queue = payload.get("entries", [])
        st.session_state.queue_running = payload.get("queue_running", True)
        st.session_state.queue_saved_at = payload.get("saved_at")
        st.session_state.queue_loaded_from_gcs = True
    except Exception as e:
        st.warning(f"Could not auto-load queue from GCS: {e}")

# ============= TAB 1: Snowflake Connection =============
with tab_conn:
    st.subheader("Connect to Snowflake (persists for this session)")
    with st.form("sf_connect_form", clear_on_submit=False):
        c1, c2 = st.columns(2)
        with c1:
            sf_user = st.text_input(
                "User",
                value=(st.session_state.sf_params or {}).get("user", "")
                or os.getenv("SF_USER"),
            )
            sf_account = st.text_input(
                "Account",
                value=(st.session_state.sf_params or {}).get("account", "")
                or os.getenv("SF_ACCOUNT"),
            )
            sf_wh = st.text_input(
                "Warehouse",
                value=(st.session_state.sf_params or {}).get("warehouse", "")
                or os.getenv("SF_WAREHOUSE"),
            )

            st.markdown(
                "**Private key (PEM)** — paste or upload one of the two below:"
            )
            sf_pk_pem = st.text_area(
                "Paste PEM key",
                value="",
                placeholder="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----",
                help="This stays only in your browser session. Not stored on server.",
                height=120,
            )
            sf_pk_file = st.file_uploader(
                "…or upload a .pem file", type=["pem", "key", "p8"]
            )

        with c2:
            sf_schema = st.text_input(
                "Schema",
                value=(st.session_state.sf_params or {}).get("schema", "")
                or os.getenv("SF_SCHEMA"),
            )
            sf_role = st.text_input(
                "Role",
                value=(st.session_state.sf_params or {}).get("role", "")
                or os.getenv("SF_ROLE"),
            )
            sf_db = st.text_input(
                "Database",
                value=(st.session_state.sf_params or {}).get("database", "")
                or os.getenv("SF_DATABASE"),
            )

            # ✅ NEW: default MMM_RAW; allow fully-qualified or relative to DB/SCHEMA above
            preview_table = st.text_input(
                "Preview table after connect",
                value=st.session_state.get("sf_preview_table", "MMM_RAW"),
                help="Use DB.SCHEMA.TABLE or a table in the selected Database/Schema.",
            )

        submitted = st.form_submit_button("🔌 Connect")

    if submitted:
        try:
            # choose source: uploaded file wins if provided
            if sf_pk_file is not None:
                pem = sf_pk_file.read().decode("utf-8", errors="replace")
            else:
                pem = (sf_pk_pem or "").strip()
            if not pem:
                raise ValueError("Provide a Snowflake private key (PEM).")

            # Convert PEM -> PKCS#8 DER bytes (what the Snowflake connector needs)
            key = serialization.load_pem_private_key(
                pem.encode("utf-8"), password=None, backend=default_backend()
            )
            pk_der = key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )

            # Build connection using the provided key (no Secret Manager)
            conn = _connect_snowflake(
                user=sf_user,
                account=sf_account,
                warehouse=sf_wh,
                database=sf_db,
                schema=sf_schema,
                role=sf_role,
                private_key=pk_der,
            )

            # Store non-sensitive params and keep key bytes only in-session
            st.session_state["sf_params"] = dict(
                user=sf_user,
                account=sf_account,
                warehouse=sf_wh,
                database=sf_db,
                schema=sf_schema,
                role=sf_role,
            )
            st.session_state["_sf_private_key_bytes"] = pk_der  # <— in memory
            st.session_state["sf_conn"] = conn
            st.session_state["sf_connected"] = True
            st.success(
                f"Connected to Snowflake as `{sf_user}` on `{sf_account}`."
            )
            st.session_state["sf_preview_table"] = preview_table
            if preview_table.strip():
                try:
                    df_prev = run_sql(f"SELECT * FROM {preview_table} LIMIT 20")
                    st.caption(f"Preview: first 20 rows of `{preview_table}`")
                    st.dataframe(df_prev, width="stretch", hide_index=True)
                except Exception as e:
                    st.warning(
                        f"Could not preview table `{preview_table}`: {e}"
                    )

        except Exception as e:
            st.session_state["sf_connected"] = False
            st.error(f"Connection failed: {e}")

    if st.session_state.sf_connected:
        with st.container(border=True):
            st.markdown("**Status:** ✅ Connected")
            c1, c2, c3 = st.columns(3)
            c1.write(
                f"**Warehouse:** `{st.session_state.sf_params.get('warehouse','')}`"
            )
            c2.write(
                f"**Database:** `{st.session_state.sf_params.get('database','')}`"
            )
            c3.write(
                f"**Schema:** `{st.session_state.sf_params.get('schema','')}`"
            )
            dc1, dc2 = st.columns(2)
            if dc1.button("🔄 Reconnect"):
                try:
                    ensure_sf_conn()
                    st.success("Reconnected.")
                except Exception as e:
                    st.error(f"Reconnect failed: {e}")
            if dc2.button("⏏️ Disconnect"):
                try:
                    conn = st.session_state.get("sf_conn")
                    if conn:
                        conn.close()
                finally:
                    st.session_state["sf_conn"] = None
                    st.session_state["sf_connected"] = False
                    st.session_state.pop("_sf_private_key_bytes", None)  # <—
                    st.success("Disconnected.")

    else:
        st.info("Not connected. Fill the form above and click **Connect**.")

# ============= TAB 2: Configure & Train =============
with tab_single:
    st.subheader("Robyn configuration & training")
    if not st.session_state.sf_connected:
        st.warning("Please connect to Snowflake in tab 1 first.")
    if st.session_state.sf_connected:
        # Data selection
        with st.expander("Data selection"):
            table = st.text_input("Table (DB.SCHEMA.TABLE)")
            query = st.text_area("Custom SQL (optional)")
            if st.button("Test connection & preview 5 rows"):
                sql_eff = effective_sql(table, query)
                if not sql_eff:
                    st.warning("Provide a table or a SQL query.")
                else:
                    try:
                        preview_sql = f"SELECT * FROM ({sql_eff}) t LIMIT 5"
                        df_prev = run_sql(preview_sql)
                        st.success("Connection OK")
                        st.dataframe(df_prev, width="stretch")
                    except Exception as e:
                        st.error(f"Preview failed: {e}")

        # Robyn config
        with st.expander("Robyn configuration"):
            country = st.text_input("Country", value="fr")
            iterations = st.number_input("Iterations", value=200, min_value=50)
            trials = st.number_input("Trials", value=5, min_value=1)
            train_size = st.text_input("Train size", value="0.7,0.9")
            revision = st.text_input("Revision tag", value="r100")
            date_input = st.text_input(
                "Date tag", value=time.strftime("%Y-%m-%d")
            )
            dep_var = st.text_input(
                "dep_var",
                value="UPLOAD_VALUE",
                help="Dependent variable column in your data (e.g., UPLOAD_VALUE)",
            )
            date_var = st.text_input(
                "date_var",
                value="date",
                help="Date column in your data (e.g., date)",
            )
            adstock = st.selectbox(
                "adstock",
                options=["geometric", "weibull_cdf", "weibull_pdf"],
                index=0,
                help="Robyn adstock function",
            )

            # NEW: optional resampling
            c_rs1, c_rs2 = st.columns([1, 1])
            resample_freq_label = c_rs1.selectbox(
                "Resample input data (optional)",
                ["None", "Weekly (W)", "Monthly (M)"],
                index=0,
                help="Aggregates the input before training.",
            )
            resample_freq = {
                "None": "none",
                "Weekly (W)": "W",
                "Monthly (M)": "M",
            }[resample_freq_label]
            resample_agg_label = c_rs2.selectbox(
                "Aggregation for metrics (when resampling)",
                ["sum", "avg (mean)", "max", "min"],
                index=0,
                help="Applied to numeric columns during resample.",
            )
            resample_agg = {
                "sum": "sum",
                "avg (mean)": "mean",
                "max": "max",
                "min": "min",
            }[resample_agg_label]

        # Variables
        with st.expander("Variable mapping"):
            paid_media_spends = st.text_input(
                "paid_media_spends (comma-separated)",
                value="GA_SUPPLY_COST, GA_DEMAND_COST, BING_DEMAND_COST, META_DEMAND_COST, TV_COST, PARTNERSHIP_COSTS",
            )
            paid_media_vars = st.text_input(
                "paid_media_vars (comma-separated)",
                value="GA_SUPPLY_COST, GA_DEMAND_COST, BING_DEMAND_COST, META_DEMAND_COST, TV_COST, PARTNERSHIP_COSTS",
            )
            context_vars = st.text_input(
                "context_vars", value="IS_WEEKEND,TV_IS_ON"
            )
            factor_vars = st.text_input(
                "factor_vars", value="IS_WEEKEND,TV_IS_ON"
            )
            organic_vars = st.text_input(
                "organic_vars", value="ORGANIC_TRAFFIC"
            )

        # Outputs
        with st.expander("Outputs"):
            gcs_bucket = st.text_input(
                "GCS bucket for outputs", value=st.session_state["gcs_bucket"]
            )
            st.session_state["gcs_bucket"] = gcs_bucket
            ann_file = st.file_uploader(
                "Optional: enriched_annotations.csv", type=["csv"]
            )

        # =============== Single-run button ===============
        def create_job_config_single(
            data_gcs_path: str,
            timestamp: str,
            annotations_gcs_path: Optional[str],
        ) -> Dict[str, Any]:
            return build_job_config_from_params(
                params_from_ui(
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
                ),
                data_gcs_path,
                timestamp,
                annotations_gcs_path,
            )

        if st.button("🚀 Start Training Job", type="primary"):
            if not all([PROJECT_ID, REGION, TRAINING_JOB_NAME]):
                st.error(
                    "Missing configuration. Check environment variables on the web service."
                )
                st.stop()

            timestamp = datetime.utcnow().strftime("%m%d_%H%M%S")
            gcs_prefix = f"robyn/{revision}/{country}/{timestamp}"
            timings: List[Dict[str, float]] = []

            try:
                with st.spinner("Preparing and launching training job..."):
                    with tempfile.TemporaryDirectory() as td:
                        sql_eff = effective_sql(table, query)
                        data_gcs_path = None
                        annotations_gcs_path = None

                        if not sql_eff:
                            st.error(
                                "Provide a table or SQL query to prepare training data."
                            )
                            st.stop()

                        # 1) Query Snowflake
                        with timed_step("Query Snowflake", timings):
                            df = run_sql(sql_eff)
                        # NEW: optional resample (single job)
                        # with timed_step(
                        #    "Optional resample (single job)", timings
                        # ):
                        #    df = _maybe_resample_df(
                        #        df, date_var, resample_freq, resample_agg
                        #    )
                        # 2) Convert to Parquet
                        with timed_step("Convert to Parquet", timings):
                            parquet_path = os.path.join(
                                td, "input_data.parquet"
                            )
                            data_processor.csv_to_parquet(df, parquet_path)

                        # 3) Upload data to GCS
                        with timed_step("Upload data to GCS", timings):
                            data_blob = (
                                f"training-data/{timestamp}/input_data.parquet"
                            )
                            data_gcs_path = upload_to_gcs(
                                gcs_bucket, parquet_path, data_blob
                            )

                        if ann_file is not None:
                            with timed_step(
                                "Upload annotations to GCS", timings
                            ):
                                annotations_path = os.path.join(
                                    td, "enriched_annotations.csv"
                                )
                                with open(annotations_path, "wb") as f:
                                    f.write(ann_file.read())
                                annotations_blob = f"training-data/{timestamp}/enriched_annotations.csv"
                                annotations_gcs_path = upload_to_gcs(
                                    gcs_bucket,
                                    annotations_path,
                                    annotations_blob,
                                )

                        # 4) Create job config
                        with timed_step("Create job configuration", timings):
                            job_config = create_job_config_single(
                                data_gcs_path, timestamp, annotations_gcs_path
                            )
                            config_path = os.path.join(td, "job_config.json")
                            with open(config_path, "w") as f:
                                json.dump(job_config, f, indent=2)
                            config_blob = (
                                f"training-configs/{timestamp}/job_config.json"
                            )
                            config_gcs_path = upload_to_gcs(
                                gcs_bucket, config_path, config_blob
                            )
                            _ = upload_to_gcs(
                                gcs_bucket,
                                config_path,
                                "training-configs/latest/job_config.json",
                            )

                        # 5) Launch Cloud Run Job
                        with timed_step("Launch training job", timings):
                            execution_name = job_manager.create_execution(
                                TRAINING_JOB_NAME
                            )
                            exec_info = {
                                "execution_name": execution_name,
                                "timestamp": timestamp,
                                "status": "LAUNCHED",
                                "config_path": config_gcs_path,
                                "data_path": data_gcs_path,
                                "revision": revision,
                                "country": country,
                                "gcs_prefix": gcs_prefix,
                                "gcs_bucket": gcs_bucket,
                            }
                            st.session_state.job_executions.append(exec_info)
                            st.success("🎉 Training job launched!")
                            st.info(
                                f"**Execution ID**: `{execution_name.split('/')[-1]}`"
                            )

            finally:
                if timings:
                    df_times = pd.DataFrame(timings)
                    try:
                        client = storage.Client()
                        dest_blob = f"{gcs_prefix}/timings.csv"
                        blob = client.bucket(gcs_bucket).blob(dest_blob)
                        if not blob.exists():
                            with tempfile.NamedTemporaryFile(
                                mode="w", suffix=".csv", delete=False
                            ) as tmp:
                                df_times.to_csv(tmp.name, index=False)
                                upload_to_gcs(gcs_bucket, tmp.name, dest_blob)
                            st.success(
                                f"Timings CSV uploaded to gs://{gcs_bucket}/{dest_blob}"
                            )
                        else:
                            st.info(
                                "`timings.csv` already exists — job will append the R row."
                            )
                    except Exception as e:
                        st.warning(f"Failed to upload timings: {e}")

                st.session_state.last_timings = {
                    "df": pd.DataFrame(timings),
                    "timestamp": timestamp,
                    "revision": revision,
                    "country": country,
                    "gcs_bucket": gcs_bucket,
                }

        render_jobs_job_history(key_prefix="single")

    # ===================== BATCH QUEUE (CSV) =====================

_queue_tick()

with tab_queue:
    if st.session_state.get("queue_running") and not (
        st.session_state.get("job_queue") or []
    ):
        st.session_state.queue_running = False

    st.subheader(
        "Batch queue (CSV) — queue & run multiple jobs sequentially",
    )
    with st.expander(
        "📚 Batch queue (CSV) — queue & run multiple jobs sequentially",
        expanded=False,
    ):
        _render_flash("batch_dupes")
        maybe_refresh_queue_from_gcs()
        # Queue name + Load/Save
        cqn1, cqn2, cqn3 = st.columns([2, 1, 1])
        new_qname = cqn1.text_input(
            "Queue name",
            value=st.session_state["queue_name"],
            help="Persists to GCS under robyn-queues/<name>/queue.json",
        )
        if new_qname != st.session_state["queue_name"]:
            st.session_state["queue_name"] = new_qname

        if cqn2.button("⬇️ Load from GCS"):
            payload = load_queue_payload(st.session_state.queue_name)
            st.session_state.job_queue = payload["entries"]
            st.session_state.queue_running = payload.get("queue_running", False)
            st.session_state.queue_saved_at = payload.get("saved_at")
            st.success(f"Loaded queue '{st.session_state.queue_name}' from GCS")

        if cqn3.button("⬆️ Save to GCS"):
            st.session_state.queue_saved_at = save_queue_to_gcs(
                st.session_state.queue_name,
                st.session_state.job_queue,
                queue_running=st.session_state.queue_running,
            )
            st.success(f"Saved queue '{st.session_state.queue_name}' to GCS")

        st.markdown(
            """
Upload a CSV where each row defines a training run. **Supported columns** (all optional except `country`, `revision`, and data source):

- `country`, `revision`, `date_input`, `iterations`, `trials`, `train_size`
- `paid_media_spends`, `paid_media_vars`, `context_vars`, `factor_vars`, `organic_vars`
- `dep_var`, `date_var`, `adstock`
- `resample_freq` (none|W|M)
- `resample_agg` (sum|mean|max|min) – used when resampling
- `gcs_bucket` (optional override per row)
- **Data**: one of `query` **or** `table`
- `annotations_gcs_path` (optional gs:// path)
            """
        )

        # Template & Example CSVs
        template = pd.DataFrame(
            [
                {
                    "country": "fr",
                    "revision": "r100",
                    "date_input": time.strftime("%Y-%m-%d"),
                    "iterations": 200,
                    "trials": 5,
                    "train_size": "0.7,0.9",
                    "paid_media_spends": "GA_SUPPLY_COST, GA_DEMAND_COST, BING_DEMAND_COST, META_DEMAND_COST, TV_COST, PARTNERSHIP_COSTS",
                    "paid_media_vars": "GA_SUPPLY_COST, GA_DEMAND_COST, BING_DEMAND_COST, META_DEMAND_COST, TV_COST, PARTNERSHIP_COSTS",
                    "context_vars": "IS_WEEKEND,TV_IS_ON",
                    "factor_vars": "IS_WEEKEND,TV_IS_ON",
                    "organic_vars": "ORGANIC_TRAFFIC",
                    "gcs_bucket": st.session_state["gcs_bucket"],
                    "table": "",
                    "query": "SELECT * FROM MESHED_BUYCYCLE.GROWTH.SOME_TABLE",
                    "dep_var": "UPLOAD_VALUE",
                    "date_var": "date",
                    "adstock": "geometric",
                    "resample_freq": "none",
                    "resample_agg": "sum",
                    "annotations_gcs_path": "",
                }
            ]
        )

        example = pd.DataFrame(
            [
                {
                    "country": "fr",
                    "revision": "r101",
                    "date_input": time.strftime("%Y-%m-%d"),
                    "iterations": 300,
                    "trials": 3,
                    "train_size": "0.7,0.9",
                    "paid_media_spends": "GA_SUPPLY_COST, GA_DEMAND_COST, META_DEMAND_COST, TV_COST",
                    "paid_media_vars": "GA_SUPPLY_COST, GA_DEMAND_COST, META_DEMAND_COST, TV_COST",
                    "context_vars": "IS_WEEKEND,TV_IS_ON",
                    "factor_vars": "IS_WEEKEND,TV_IS_ON",
                    "organic_vars": "ORGANIC_TRAFFIC",
                    "gcs_bucket": st.session_state["gcs_bucket"],
                    "table": "MESHED_BUYCYCLE.GROWTH.MMM_RAW",
                    "query": "",
                    "dep_var": "UPLOAD_VALUE",
                    "date_var": "date",
                    "adstock": "geometric",
                    "resample_freq": "none",
                    "resample_agg": "sum",
                    "annotations_gcs_path": "",
                },
                {
                    "country": "de",
                    "revision": "r102",
                    "date_input": time.strftime("%Y-%m-%d"),
                    "iterations": 200,
                    "trials": 5,
                    "train_size": "0.75,0.9",
                    "paid_media_spends": "BING_DEMAND_COST, META_DEMAND_COST, TV_COST, PARTNERSHIP_COSTS",
                    "paid_media_vars": "BING_DEMAND_COST, META_DEMAND_COST, TV_COST, PARTNERSHIP_COSTS",
                    "context_vars": "IS_WEEKEND",
                    "factor_vars": "IS_WEEKEND",
                    "organic_vars": "ORGANIC_TRAFFIC",
                    "gcs_bucket": st.session_state["gcs_bucket"],
                    "table": "",
                    "query": "SELECT * FROM MESHED_BUYCYCLE.GROWTH.TABLE_B WHERE COUNTRY='DE'",
                    "dep_var": "UPLOAD_VALUE",
                    "date_var": "date",
                    "adstock": "geometric",
                    "resample_freq": "none",
                    "resample_agg": "sum",
                    "annotations_gcs_path": "",
                },
            ]
        )

        col_dl1, col_dl2 = st.columns(2)
        col_dl1.download_button(
            "Download CSV template",
            data=template.to_csv(index=False),
            file_name="robyn_batch_template.csv",
            mime="text/csv",
        )
        col_dl2.download_button(
            "Download example CSV (2 jobs)",
            data=example.to_csv(index=False),
            file_name="robyn_batch_example.csv",
            mime="text/csv",
        )

        # --- CSV upload (editable, persistent, deletable) ---
        up = st.file_uploader("Upload batch CSV", type=["csv"], key="batch_csv")

        # Session scaffolding
        if "uploaded_df" not in st.session_state:
            st.session_state.uploaded_df = pd.DataFrame()
        if "uploaded_fingerprint" not in st.session_state:
            st.session_state.uploaded_fingerprint = None

        # Load only when the *file changes* (never reload just because the table is empty)
        if up is not None:
            fingerprint = f"{getattr(up, 'name', '')}:{getattr(up, 'size', '')}"
            if st.session_state.uploaded_fingerprint != fingerprint:
                try:
                    st.session_state.uploaded_df = pd.read_csv(up)
                    st.session_state.uploaded_fingerprint = fingerprint
                    st.success(
                        f"Loaded {len(st.session_state.uploaded_df)} rows from CSV"
                    )
                except Exception as e:
                    st.error(f"Failed to parse CSV: {e}")
        else:
            # If user clears the file input, allow re-uploading the same file later
            st.session_state.uploaded_fingerprint = None

        # ===== Uploaded CSV (FORM) =====
        st.markdown("#### 📥 Uploaded CSV (editable)")
        st.caption(
            "Edits in this table are committed when you press a button below. "
            "Use **Append uploaded rows to builder** here to move unique rows into the builder."
        )

        if st.session_state.uploaded_df.empty:
            st.caption("No uploaded CSV yet (or it has 0 rows).")
        else:
            uploaded_view = st.session_state.uploaded_df.copy()
            if "Delete" not in uploaded_view.columns:
                uploaded_view.insert(0, "Delete", False)

            uploaded_view, up_nonce = _sorted_with_controls(
                uploaded_view, prefix="uploaded"
            )

            with st.form("uploaded_csv_form"):
                # Show editable grid with a Delete column (like queue builder)

                uploaded_edited = st.data_editor(
                    uploaded_view,
                    key=f"uploaded_editor_{up_nonce}",  # <= bump key when sort changes
                    num_rows="dynamic",
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "Delete": st.column_config.CheckboxColumn(
                            "Delete", help="Mark to remove from uploaded table"
                        )
                    },
                )

                u1, u2, u3 = st.columns([1, 1, 1])

                append_uploaded_clicked = u1.form_submit_button(
                    "Append uploaded rows to builder",
                    disabled=uploaded_edited.drop(
                        columns="Delete", errors="ignore"
                    ).empty,
                )
                delete_uploaded_clicked = u2.form_submit_button(
                    "🗑 Delete selected"
                )
                clear_uploaded_clicked = u3.form_submit_button(
                    "🧹 Clear uploaded table"
                )

            # ---- Handle CSV form actions ----

            if delete_uploaded_clicked:
                keep_mask = (
                    ~uploaded_edited.get("Delete", False)
                    .fillna(False)
                    .astype(bool)
                )
                st.session_state.uploaded_df = (
                    uploaded_edited.loc[keep_mask]
                    .drop(columns="Delete", errors="ignore")
                    .reset_index(drop=True)
                )
                st.success("Deleted selected uploaded rows.")
                st.rerun()

            if clear_uploaded_clicked:
                st.session_state.uploaded_df = pd.DataFrame()
                st.session_state.uploaded_fingerprint = None
                st.success("Cleared uploaded table.")
                st.rerun()

            if append_uploaded_clicked:
                # Canonical, edited upload table as seen in the UI (including any user sorting)
                up_base = (
                    uploaded_edited.drop(columns="Delete", errors="ignore")
                    .copy()
                    .reset_index(drop=True)
                )

                # Columns the builder expects (preserve your existing behavior)
                need_cols = (
                    list(st.session_state.qb_df.columns)
                    if "qb_df" in st.session_state
                    else []
                )

                # Helpers
                def _sig_from_params_dict(d: dict) -> str:
                    return json.dumps(d, sort_keys=True)

                # Build signature sets against which we dedupe
                builder_sigs = {
                    _sig_from_params_dict(_normalize_row(r))
                    for _, r in (
                        st.session_state.qb_df
                        if "qb_df" in st.session_state
                        else pd.DataFrame()
                    ).iterrows()
                }

                queue_sigs = set()
                for e in st.session_state.job_queue:
                    try:
                        queue_sigs.add(
                            _sig_from_params_dict(
                                _normalize_row(pd.Series(e.get("params", {})))
                            )
                        )
                    except Exception:
                        pass

                # JOB_HISTORY sigs (SUCCEEDED/FAILED)
                try:
                    df_led = read_job_history_from_gcs(
                        st.session_state.get("gcs_bucket", GCS_BUCKET)
                    )
                except Exception:
                    df_led = pd.DataFrame()

                job_history_sigs = set()
                if not df_led.empty and "state" in df_led.columns:
                    df_led = df_led[
                        df_led["state"].isin(["SUCCEEDED", "FAILED"])
                    ].copy()
                    cols_like = need_cols or df_led.columns
                    for _, r in df_led.iterrows():
                        params_like = {c: r.get(c, "") for c in cols_like}
                        params_like = _normalize_row(pd.Series(params_like))
                        job_history_sigs.add(_sig_from_params_dict(params_like))

                # Decide row-by-row whether to append (True) or keep in upload (False)
                dup = {
                    "in_builder": [],
                    "in_queue": [],
                    "in_job_history": [],
                    "missing_data_source": [],
                }
                to_append_mask = []

                for i, r in up_base.iterrows():
                    params = _normalize_row(r)
                    if not (params.get("query") or params.get("table")):
                        dup["missing_data_source"].append(i + 1)
                        to_append_mask.append(False)
                        continue
                    sig = _sig_from_params_dict(params)
                    if sig in builder_sigs:
                        dup["in_builder"].append(i + 1)
                        to_append_mask.append(False)
                        continue
                    if sig in queue_sigs:
                        dup["in_queue"].append(i + 1)
                        to_append_mask.append(False)
                        continue
                    if sig in job_history_sigs:
                        dup["in_job_history"].append(i + 1)
                        to_append_mask.append(False)
                        continue
                    to_append_mask.append(True)

                to_append_mask = pd.Series(to_append_mask, index=up_base.index)
                added_count = int(to_append_mask.sum())

                _toast_dupe_summary(
                    "Append to builder", dup, added_count=added_count
                )

                if added_count > 0:
                    # Append to builder (use builder schema)
                    to_append = up_base.loc[to_append_mask]
                    if need_cols:
                        to_append = to_append.reindex(
                            columns=need_cols, fill_value=""
                        )
                    st.session_state.qb_df = pd.concat(
                        [st.session_state.qb_df, to_append], ignore_index=True
                    )

                    # Keep only rows NOT appended in the upload table (i.e., the duplicates / invalid ones)
                    st.session_state.uploaded_df = up_base.loc[
                        ~to_append_mask
                    ].reset_index(drop=True)

                    st.success(
                        f"Appended {added_count} row(s) to the builder. "
                        f"Remaining in upload: {len(st.session_state.uploaded_df)} duplicate/invalid row(s)."
                    )
                    st.rerun()

        # Seed once from current GCS queue (do NOT re-seed on every rerun)
        # ===== Queue Builder (parameters only, editable) =====
        payload = load_queue_payload(st.session_state.queue_name)
        existing_entries = payload.get("entries", [])

        def _as_csv(v):
            if isinstance(v, (list, tuple)):
                return ", ".join(str(x) for x in v if str(x).strip())
            return "" if v is None else str(v)

        def _entry_to_row(e: dict) -> dict:
            p = e.get("params", {}) or {}
            return {
                "country": p.get("country", ""),
                "revision": p.get("revision", ""),
                "date_input": p.get("date_input", ""),
                "iterations": p.get("iterations", ""),
                "trials": p.get("trials", ""),
                "train_size": _as_csv(p.get("train_size", "")),
                "paid_media_spends": _as_csv(p.get("paid_media_spends", "")),
                "paid_media_vars": _as_csv(p.get("paid_media_vars", "")),
                "context_vars": _as_csv(p.get("context_vars", "")),
                "factor_vars": _as_csv(p.get("factor_vars", "")),
                "organic_vars": _as_csv(p.get("organic_vars", "")),
                "gcs_bucket": p.get(
                    "gcs_bucket", st.session_state["gcs_bucket"]
                ),
                "table": p.get("table", ""),
                "query": p.get("query", ""),
                "dep_var": p.get("dep_var", ""),
                "date_var": p.get("date_var", ""),
                "adstock": p.get("adstock", ""),
                "resample_freq": p.get("resample_freq", "none"),
                "resample_agg": p.get("resample_agg", "sum"),
                "annotations_gcs_path": p.get("annotations_gcs_path", ""),
            }

        seed_df = pd.DataFrame([_entry_to_row(e) for e in existing_entries])
        if seed_df.empty:
            seed_df = seed_df.reindex(
                columns=[
                    "country",
                    "revision",
                    "date_input",
                    "iterations",
                    "trials",
                    "train_size",
                    "paid_media_spends",
                    "paid_media_vars",
                    "context_vars",
                    "factor_vars",
                    "organic_vars",
                    "gcs_bucket",
                    "table",
                    "query",
                    "dep_var",
                    "date_var",
                    "adstock",
                    "resample_freq",
                    "resample_agg",
                    "annotations_gcs_path",
                ]
            )
            seed_df.loc[0] = [""] * len(seed_df.columns)

        if "qb_df" not in st.session_state:
            st.session_state.qb_df = seed_df.copy()

        st.markdown("#### ✏️ Queue Builder (editable)")
        st.caption(
            "Starts with your current GCS queue (params only). "
            "Edit cells, add rows, or append from the uploaded CSV. "
            "Use the buttons below; edits are committed on submit."
        )

        # Use a FORM so editor commits the last active cell before any button logic
        builder_src = st.session_state.qb_df.copy()

        # Add Delete checkbox column (not persisted) and enable sorting/nonce
        if "Delete" not in builder_src.columns:
            builder_src.insert(0, "Delete", False)

        builder_src, qb_nonce = _sorted_with_controls(builder_src, prefix="qb")

        with st.form("queue_builder_form"):
            # Editable builder (params), plus a Delete column just for selection
            builder_edited = st.data_editor(
                builder_src,
                num_rows="dynamic",
                width="stretch",
                key=f"queue_builder_editor_{qb_nonce}",  # <= bump key when sort changes
                hide_index=True,
                column_config={
                    "Delete": st.column_config.CheckboxColumn(
                        "Delete", help="Mark to remove from builder"
                    )
                },
            )

            # Persist edits to builder params (drop Delete column)
            st.session_state.qb_df = builder_edited.drop(
                columns="Delete", errors="ignore"
            ).reset_index(drop=True)

            # Actions for the builder table only – now includes Delete selected
            bb0, bb1, bb2, bb3 = st.columns(4)

            save_builder_clicked = bb0.form_submit_button(
                "💾 Save builder edits"
            )

            delete_builder_clicked = bb1.form_submit_button("🗑 Delete selected")
            reset_clicked = bb2.form_submit_button(
                "Reset builder to current GCS queue"
            )
            clear_builder_clicked = bb3.form_submit_button(
                "Clear builder (empty table)"
            )

            # Enqueue & clear queue
            bc1, bc2 = st.columns(2)
            enqueue_clicked = bc1.form_submit_button("➕ Enqueue all rows")

            clear_queue_clicked = bc2.form_submit_button("🧹 Clear queue")

        # ----- Handle form actions (after form so we have latest editor state) -----
        if delete_builder_clicked:
            keep_mask = (
                (~builder_edited.get("Delete", False))
                .fillna(False)
                .astype(bool)
            )
            st.session_state.qb_df = (
                builder_edited.loc[keep_mask]
                .drop(columns="Delete", errors="ignore")
                .reset_index(drop=True)
            )
            st.success("Deleted selected builder rows.")
            st.rerun()

        if save_builder_clicked:
            # Persist ONLY when explicitly saving (or do this in “any submit” branches)
            st.session_state.qb_df = builder_edited.drop(
                columns="Delete", errors="ignore"
            ).reset_index(drop=True)
            st.success("Builder saved.")
            st.rerun()

        if reset_clicked:
            st.session_state.qb_df = seed_df.copy()
            st.info("Builder reset to current GCS queue.")
            st.rerun()

        if clear_builder_clicked:
            st.session_state.qb_df = seed_df.iloc[0:0].copy()
            st.info("Builder cleared.")
            st.rerun()

        if clear_queue_clicked:
            st.session_state["job_queue"] = []
            st.session_state["queue_running"] = False
            save_queue_to_gcs(st.session_state.queue_name, [])
            st.success("Queue cleared & saved to GCS.")
            st.rerun()

        # Build helper here so it’s shared by both append & enqueue
        def _sig_from_params_dict(d: dict) -> str:
            return json.dumps(d, sort_keys=True)

        need_cols = list(st.session_state.qb_df.columns)

        if enqueue_clicked:
            # Build separate sets so we can categorize reasons
            if st.session_state.qb_df.dropna(how="all").empty:
                st.warning(
                    "No rows to enqueue. Add at least one non-empty row."
                )
            else:
                queue_sigs_existing = set()
                for e in st.session_state.job_queue:
                    try:
                        norm_existing = _normalize_row(
                            pd.Series(e.get("params", {}))
                        )
                        queue_sigs_existing.add(
                            json.dumps(norm_existing, sort_keys=True)
                        )
                    except Exception:
                        pass

                try:
                    df_led = read_job_history_from_gcs(
                        st.session_state.get("gcs_bucket", GCS_BUCKET)
                    )
                except Exception:
                    df_led = pd.DataFrame()

                job_history_sigs_existing = set()
                if not df_led.empty and "state" in df_led.columns:
                    df_led = df_led[
                        df_led["state"].isin(["SUCCEEDED", "FAILED"])
                    ].copy()
                    for _, r in df_led.iterrows():
                        params_like = {c: r.get(c, "") for c in need_cols}
                        params_like = _normalize_row(pd.Series(params_like))
                        job_history_sigs_existing.add(
                            json.dumps(params_like, sort_keys=True)
                        )

                next_id = (
                    max(
                        [e["id"] for e in st.session_state.job_queue], default=0
                    )
                    + 1
                )

                new_entries, enqueued_sigs = [], set()
                dup = {
                    "in_queue": [],
                    "in_job_history": [],
                    "missing_data_source": [],
                }

                for i, row in st.session_state.qb_df.iterrows():
                    params = _normalize_row(row)
                    if not (params.get("query") or params.get("table")):
                        dup["missing_data_source"].append(i + 1)
                        continue
                    sig = json.dumps(params, sort_keys=True)
                    if sig in queue_sigs_existing:
                        dup["in_queue"].append(i + 1)
                        continue
                    if sig in job_history_sigs_existing:
                        dup["in_job_history"].append(i + 1)
                        continue

                    new_entries.append(
                        {
                            "id": next_id + len(new_entries),
                            "params": params,
                            "status": "PENDING",
                            "timestamp": None,
                            "execution_name": None,
                            "gcs_prefix": None,
                            "message": "",
                        }
                    )
                    enqueued_sigs.add(sig)

                # Toaster (and zero-add info if applicable)
                _toast_dupe_summary(
                    "Enqueue", dup, added_count=len(new_entries)
                )

                if not new_entries:
                    # nothing to enqueue
                    pass
                else:
                    st.session_state.job_queue.extend(new_entries)
                    st.session_state.queue_saved_at = save_queue_to_gcs(
                        st.session_state.queue_name,
                        st.session_state.job_queue,
                        queue_running=st.session_state.queue_running,
                    )

                    # Remove only the rows that were enqueued from the builder
                    def _row_sig(r: pd.Series) -> str:
                        return json.dumps(_normalize_row(r), sort_keys=True)

                    keep_mask = ~st.session_state.qb_df.apply(
                        _row_sig, axis=1
                    ).isin(enqueued_sigs)
                    st.session_state.qb_df = st.session_state.qb_df.loc[
                        keep_mask
                    ].reset_index(drop=True)

                    st.success(
                        f"Enqueued {len(new_entries)} new job(s), saved to GCS, and removed them from the builder."
                    )
                    st.rerun()

        # Queue controls
        st.caption(
            f"Queue status: {'▶️ RUNNING' if st.session_state.queue_running else '⏸️ STOPPED'} · "
            f"{sum(e['status'] in ('RUNNING','LAUNCHING') for e in st.session_state.job_queue)} running"
        )

        if st.button("🔁 Refresh from GCS"):
            maybe_refresh_queue_from_gcs(force=True)
            st.success("Refreshed from GCS.")
            st.rerun()

        qc1, qc2, qc3, qc4 = st.columns(4)
        if qc1.button(
            "▶️ Start Queue", disabled=(len(st.session_state.job_queue) == 0)
        ):
            set_queue_running(st.session_state.queue_name, True)
            st.success("Queue set to RUNNING.")
            st.rerun()
        if qc2.button("⏸️ Stop Queue"):
            set_queue_running(st.session_state.queue_name, False)
            st.info("Queue paused.")
            st.rerun()
        if qc3.button("⏭️ Process Next Step"):
            _queue_tick()
            st.toast("Ticked queue")
            st.rerun()

        if qc4.button("💾 Save now"):
            save_queue_to_gcs(
                st.session_state.queue_name, st.session_state.job_queue
            )
            st.success("Queue saved to GCS.")

        _auto_refresh_and_tick(interval_ms=2000)

        # Queue table
        maybe_refresh_queue_from_gcs()
        st.caption(
            f"GCS saved_at: {st.session_state.get('queue_saved_at') or '—'} · "
            f"{sum(e['status']=='PENDING' for e in st.session_state.job_queue)} pending · "
            f"{sum(e['status']=='RUNNING' for e in st.session_state.job_queue)} running · "
            f"Queue is {'RUNNING' if st.session_state.queue_running else 'STOPPED'}"
        )

        if st.session_state.job_queue:
            df_queue = pd.DataFrame(
                [
                    {
                        "ID": e["id"],
                        "Status": e["status"],
                        "Country": e["params"]["country"],
                        "Revision": e["params"]["revision"],
                        "Timestamp": e.get("timestamp", ""),
                        "Exec": (e.get("execution_name", "") or "").split("/")[
                            -1
                        ],
                        "Msg": e.get("message", ""),
                    }
                    for e in st.session_state.job_queue
                ]
            )

            # Add Delete checkbox column (not persisted) and enable sorting/nonce
            if "Delete" not in df_queue.columns:
                df_queue.insert(0, "Delete", False)

            df_queue_view, q_nonce = _sorted_with_controls(
                df_queue, prefix="queue"
            )

            # Build per-column config so everything is read-only EXCEPT Delete
            q_cfg = {}
            for c in df_queue_view.columns:
                if c == "Delete":
                    q_cfg[c] = st.column_config.CheckboxColumn(
                        "Delete", help="Mark to remove from queue"
                    )
                elif c in ("ID",):
                    q_cfg[c] = st.column_config.NumberColumn(c, disabled=True)
                else:
                    q_cfg[c] = st.column_config.TextColumn(c, disabled=True)

            # Form so the checkbox state is committed before deleting
            with st.form("queue_table_form"):
                edited = st.data_editor(
                    df_queue_view,
                    key=f"queue_editor_{q_nonce}",  # <= bump key when sort changes
                    hide_index=True,
                    width="stretch",
                    column_config=q_cfg,
                )

                delete_queue_clicked = st.form_submit_button(
                    "🗑 Delete selected (PENDING/ERROR only)"
                )

            # Deletion logic identical to before, but reading from the form-edited frame
            ids_to_delete = set()
            if "Delete" in edited.columns:
                ids_to_delete = set(
                    edited.loc[edited["Delete"] == True, "ID"]
                    .astype(int)
                    .tolist()
                )

            if delete_queue_clicked:
                new_q, blocked = [], []
                for e in st.session_state.job_queue:
                    if e["id"] in ids_to_delete:
                        if e.get("status") in (
                            "PENDING",
                            "ERROR",
                            "CANCELLED",
                            "FAILED",
                        ):
                            continue  # drop it
                        else:
                            blocked.append(e["id"])
                            new_q.append(e)
                    else:
                        new_q.append(e)

                st.session_state.job_queue = new_q
                st.session_state.queue_saved_at = save_queue_to_gcs(
                    st.session_state.queue_name,
                    st.session_state.job_queue,
                    queue_running=st.session_state.queue_running,
                )
                if blocked:
                    st.warning(
                        f"Did not delete non-deletable entries: {sorted(blocked)}"
                    )
                st.success("Queue updated.")
                st.rerun()

    render_jobs_job_history(key_prefix="queue")
    render_job_status_monitor(key_prefix="queue")

    # ─────────────────────────────
    # Execution timeline & timings.csv (single latest)
    # ─────────────────────────────
    if st.session_state.last_timings:
        with st.expander("⏱️ Execution Timeline", expanded=False):
            df_times = st.session_state.last_timings["df"]
            total = (
                float(df_times["Time (s)"].sum()) if not df_times.empty else 0.0
            )
            if total > 0:
                df_times = df_times.copy()
                df_times["% of total"] = (
                    df_times["Time (s)"] / total * 100
                ).round(1)
            st.markdown("**Setup steps (this session)**")
            st.dataframe(df_times, width="stretch")
            st.write(f"**Total setup time:** {_fmt_secs(total)}")
            st.write(
                "**Note**: Training runs asynchronously in Cloud Run Jobs."
            )
