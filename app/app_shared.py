# app_shared.py — shared helpers for Robyn Streamlit app
import os, io, json, time, re
from datetime import datetime, timezone

# add to the existing imports at the top of app_shared.py
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)

import pandas as pd
import streamlit as st
import snowflake.connector as sf
from google.cloud import storage, run_v2

from data_processor import DataProcessor

# Environment constants
PROJECT_ID = os.getenv("PROJECT_ID")
REGION = os.getenv("REGION", "europe-west1")
TRAINING_JOB_NAME = os.getenv("TRAINING_JOB_NAME")
GCS_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")
QUEUE_ROOT = os.getenv("QUEUE_ROOT", "robyn-queues")
DEFAULT_QUEUE_NAME = os.getenv("DEFAULT_QUEUE_NAME", "default")
SAFE_LAG_SECONDS_AFTER_RUNNING = int(
    os.getenv("SAFE_LAG_SECONDS_AFTER_RUNNING", "5")
)


@st.cache_resource
def get_data_processor():
    return DataProcessor()


@st.cache_resource
def get_job_manager():
    return CloudRunJobManager(PROJECT_ID, REGION)


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
        role=role or None,
    )


class CloudRunJobManager:
    """Manages Cloud Run Job executions."""

    def __init__(self, project_id: str, region: str):
        self.project_id = project_id
        self.region = region
        self.client = run_v2.JobsClient()
        self.executions_client = run_v2.ExecutionsClient()

    def _job_fqn(self, job_name: str) -> str:
        if job_name.startswith("projects/"):
            return job_name
        return f"projects/{self.project_id}/locations/{self.region}/jobs/{job_name}"

    def create_execution(self, job_name: str) -> str:
        """
        Kick off a job and return the execution name quickly (non-blocking).
        NOTE: run_job() does not accept per-run overrides; pass dynamic data via GCS (latest/).
        """
        job_path = self._job_fqn(job_name)
        _ = self.client.run_job(name=job_path)  # fire-and-forget

        # Heuristic: fetch most recent execution
        execution_name = None
        deadline = time.time() + 20
        while time.time() < deadline and not execution_name:
            try:
                execs = list(
                    self.executions_client.list_executions(parent=job_path)
                )
                if execs:
                    execs.sort(
                        key=lambda e: getattr(e, "create_time", None),
                        reverse=True,
                    )
                    execution_name = execs[0].name
                    break
            except Exception:
                pass
            time.sleep(1)
        return execution_name or f"{job_path}/executions/unknown"

    def get_execution_status(self, execution_name: str) -> Dict[str, Any]:
        def _ts(dtobj):
            try:
                return dtobj.isoformat() if dtobj else None
            except Exception:
                return str(dtobj) if dtobj is not None else None

        try:
            execution = self.executions_client.get_execution(
                name=execution_name
            )
            status = {
                "name": execution.name,
                "uid": getattr(execution, "uid", None),
                "create_time": _ts(getattr(execution, "create_time", None)),
                "start_time": _ts(getattr(execution, "start_time", None)),
                "completion_time": _ts(
                    getattr(execution, "completion_time", None)
                ),
                "running_count": getattr(execution, "running_count", None),
                "succeeded_count": getattr(execution, "succeeded_count", None),
                "failed_count": getattr(execution, "failed_count", None),
                "cancelled_count": getattr(execution, "cancelled_count", None),
            }
            if getattr(execution, "completion_time", None):
                if (getattr(execution, "succeeded_count", 0) or 0) > 0:
                    status["overall_status"] = "SUCCEEDED"
                elif (getattr(execution, "failed_count", 0) or 0) > 0:
                    status["overall_status"] = "FAILED"
                elif (getattr(execution, "cancelled_count", 0) or 0) > 0:
                    status["overall_status"] = "CANCELLED"
                else:
                    status["overall_status"] = "COMPLETED"
            elif (getattr(execution, "running_count", 0) or 0) > 0 or getattr(
                execution, "start_time", None
            ):
                status["overall_status"] = "RUNNING"
            else:
                status["overall_status"] = "PENDING"
            return status
        except Exception as e:
            logger.error(f"Error getting execution status: {e}", exc_info=True)
            return {"overall_status": "ERROR", "error": str(e)}


# ─────────────────────────────
# GCS helpers
# ─────────────────────────────


def _fmt_secs(s: float) -> str:
    if s < 60:
        return f"{s:.2f}s"
    m, sec = divmod(s, 60)
    return f"{int(m)}m {sec:.1f}s"


@contextmanager
def timed_step(name: str, bucket: list):
    start = time.perf_counter()
    ph = st.empty()
    ph.info(f"⏳ {name}…")
    try:
        yield
    finally:
        dt = time.perf_counter() - start
        ph.success(f"✅ {name} – {_fmt_secs(dt)}")
        bucket.append({"Step": name, "Time (s)": round(dt, 2)})
        logger.info(f"Step '{name}' completed in {dt:.2f}s")


def parse_train_size(txt: str):
    try:
        vals = [float(x.strip()) for x in txt.split(",") if x.strip()]
        if len(vals) == 2:
            return vals
    except Exception:
        pass
    return [0.7, 0.9]


# ─────────────────────────────
# GCS helpers
# ─────────────────────────────


def effective_sql(table: str, query: str) -> Optional[str]:
    if query and query.strip():
        return query.strip()
    if table and table.strip():
        return f"SELECT * FROM {table.strip()}"
    return None


def _sf_params_from_env() -> Optional[dict]:
    u = os.getenv("SF_USER")
    p = os.getenv("SF_PASSWORD")
    a = os.getenv("SF_ACCOUNT")
    w = os.getenv("SF_WAREHOUSE")
    d = os.getenv("SF_DATABASE")
    s = os.getenv("SF_SCHEMA")
    r = os.getenv("SF_ROLE", "")
    if all([u, p, a, w, d, s]):
        return dict(
            user=u,
            password=p,
            account=a,
            warehouse=w,
            database=d,
            schema=s,
            role=r,
        )
    return None


def ensure_sf_conn() -> sf.SnowflakeConnection:
    conn = st.session_state.get("sf_conn")
    params = st.session_state.get("sf_params") or {}
    if not params:
        envp = _sf_params_from_env()
        if envp:
            params = envp
            # store redacted copy so code paths relying on sf_params don't crash
            st.session_state["sf_params"] = {
                k: v for k, v in envp.items() if k != "password"
            }
        else:
            raise RuntimeError(
                "No Snowflake params. Use UI once or set SF_* env vars."
            )
    if conn is not None:
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchall()
            cur.close()
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            st.session_state["sf_conn"] = None
    if not params:
        raise RuntimeError(
            "No Snowflake connection parameters found. Please connect first."
        )
    conn = _connect_snowflake(**params)
    st.session_state["sf_conn"] = conn
    st.session_state["sf_connected"] = True
    return conn


def run_sql(sql: str) -> pd.DataFrame:
    conn = ensure_sf_conn()
    cur = conn.cursor()
    try:
        cur.execute(sql)
        return cur.fetch_pandas_all()
    finally:
        try:
            cur.close()
        except Exception:
            pass


# ─────────────────────────────
# Shared helpers (single + batch)
# ─────────────────────────────


def upload_to_gcs(bucket_name: str, local_path: str, dest_blob: str) -> str:
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(dest_blob)
    blob.upload_from_filename(local_path)
    return f"gs://{bucket_name}/{dest_blob}"


def read_status_json(bucket_name: str, prefix: str) -> Optional[dict]:
    try:
        client = storage.Client()
        b = client.bucket(bucket_name)
        blob = b.blob(f"{prefix}/status.json")
        if not blob.exists():
            return None
        return json.loads(blob.download_as_text())
    except Exception:
        return None


# ─────────────────────────────
# Data processor & job manager
# ─────────────────────────────
@st.cache_resource
def build_job_config_from_params(
    params: dict,
    data_gcs_path: str,
    timestamp: str,
    annotations_gcs_path: Optional[str],
) -> dict:
    return {
        "country": params["country"],
        "iterations": int(params["iterations"]),
        "trials": int(params["trials"]),
        "train_size": (
            parse_train_size(str(params["train_size"]))
            if isinstance(params["train_size"], str)
            else params["train_size"]
        ),
        "revision": params["revision"],
        "date_input": params.get("date_input") or time.strftime("%Y-%m-%d"),
        "gcs_bucket": params.get("gcs_bucket")
        or st.session_state["gcs_bucket"],
        "data_gcs_path": data_gcs_path,
        "annotations_gcs_path": annotations_gcs_path,
        "paid_media_spends": [
            s.strip()
            for s in str(params["paid_media_spends"]).split(",")
            if s.strip()
        ],
        "paid_media_vars": [
            s.strip()
            for s in str(params["paid_media_vars"]).split(",")
            if s.strip()
        ],
        "context_vars": [
            s.strip()
            for s in str(params.get("context_vars", "")).split(",")
            if s.strip()
        ],
        "factor_vars": [
            s.strip()
            for s in str(params.get("factor_vars", "")).split(",")
            if s.strip()
        ],
        "organic_vars": [
            s.strip()
            for s in str(params.get("organic_vars", "")).split(",")
            if s.strip()
        ],
        "timestamp": timestamp,
        "use_parquet": True,
        "parallel_processing": True,
        "max_cores": 8,
    }


def _sanitize_queue_name(name: str) -> str:
    name = (name or "default").strip().lower()
    # keep alnum, dash, underscore; replace others with '-'
    return re.sub(r"[^a-z0-9_\-]+", "-", name) or "default"


def _queue_blob_path(queue_name: str) -> str:
    q = _sanitize_queue_name(queue_name)
    return f"{QUEUE_ROOT}/{q}/queue.json"


def load_queue_from_gcs(
    queue_name: str, bucket_name: Optional[str] = None
) -> dict:
    """
    Load the full queue document: {version, saved_at, entries: [...], queue_running: bool}.
    If the blob doesn't exist, return an empty running queue by default.
    Also refresh st.session_state.job_queue and st.session_state.queue_running.
    """
    bucket_name = bucket_name or st.session_state.get("gcs_bucket", GCS_BUCKET)
    bucket = storage.Client().bucket(bucket_name)
    blob = bucket.blob(_queue_blob_path(queue_name))

    if not blob.exists():
        doc = {
            "version": 1,
            "saved_at": datetime.utcnow().isoformat() + "Z",
            "entries": [],
            "queue_running": True,  # default to running
        }
        st.session_state.job_queue = []
        st.session_state.queue_running = True
        return doc

    try:
        payload = json.loads(blob.download_as_text())
        # Back-compat: if payload is a list, wrap it as a doc and assume running
        if isinstance(payload, list):
            payload = {
                "version": 1,
                "saved_at": datetime.utcnow().isoformat() + "Z",
                "entries": payload,
                "queue_running": True,
            }
        # Defaults
        payload.setdefault("version", 1)
        payload.setdefault("queue_running", True)
        # Refresh session
        st.session_state.job_queue = payload.get("entries", [])
        st.session_state.queue_running = payload.get("queue_running", True)
        return payload
    except Exception as e:
        logger.warning("Failed to load queue doc from GCS: %s", e)
        # Safe default: show empty queue but running
        st.session_state.job_queue = []
        st.session_state.queue_running = True
        return {
            "version": 1,
            "saved_at": datetime.utcnow().isoformat() + "Z",
            "entries": [],
            "queue_running": True,
        }


def save_queue_to_gcs(
    queue_name: str,
    entries: Optional[list[dict]] = None,
    queue_running: Optional[bool] = None,
    bucket_name: Optional[str] = None,
) -> str:
    """
    Save the full queue doc back to GCS. Returns saved_at timestamp.
    """
    bucket_name = bucket_name or st.session_state.get("gcs_bucket", GCS_BUCKET)
    bucket = storage.Client().bucket(bucket_name)
    blob = bucket.blob(_queue_blob_path(queue_name))

    # Use session defaults if not provided
    entries = (
        entries
        if entries is not None
        else st.session_state.get("job_queue", [])
    )
    if queue_running is None:
        queue_running = st.session_state.get("queue_running", True)

    saved_at = datetime.utcnow().isoformat() + "Z"
    payload = {
        "version": 1,
        "saved_at": saved_at,
        "entries": entries,
        "queue_running": bool(queue_running),
    }
    blob.upload_from_string(
        json.dumps(payload, indent=2), content_type="application/json"
    )
    return saved_at


def load_queue_payload(
    queue_name: str, bucket_name: Optional[str] = None
) -> dict:
    """Return {'version':1, 'saved_at': str|None, 'queue_running': bool, 'entries': list}."""
    bucket_name = bucket_name or st.session_state.get("gcs_bucket", GCS_BUCKET)
    bucket = storage.Client().bucket(bucket_name)
    blob = bucket.blob(_queue_blob_path(queue_name))
    if not blob.exists():
        return {
            "version": 1,
            "saved_at": None,
            "queue_running": False,
            "entries": [],
        }
    try:
        payload = json.loads(blob.download_as_text())
        if isinstance(payload, list):  # legacy format
            return {
                "version": 1,
                "saved_at": None,
                "queue_running": False,
                "entries": payload,
            }
        # ensure fields exist
        payload.setdefault("queue_running", False)
        payload.setdefault("entries", [])
        payload.setdefault("saved_at", None)
        return payload
    except Exception as e:
        logger.warning("Failed to load queue payload from GCS: %s", e)
        return {
            "version": 1,
            "saved_at": None,
            "queue_running": False,
            "entries": [],
        }


def queue_tick_once_headless(
    queue_name: str,
    bucket_name: Optional[str] = None,
    launcher: Optional[callable] = None,
) -> dict:
    """
    Advance the queue by at most one transition; safe to call from Scheduler/webhook.
    Uses only GCS state (stateless). If `launcher` is provided, it will be used to
    launch the next PENDING job; otherwise the function will only update RUNNING status.
    """
    bucket_name = bucket_name or GCS_BUCKET
    doc = load_queue_from_gcs(queue_name, bucket_name=bucket_name)
    q = doc.get("entries", [])
    running_flag = doc.get("queue_running", True)

    if not q:
        return {"ok": True, "message": "empty queue", "changed": False}

    if not running_flag:
        return {"ok": True, "message": "queue is paused", "changed": False}

    changed = False
    jm = CloudRunJobManager(PROJECT_ID, REGION)

    # 1) Update RUNNING job if any
    run_idx = next(
        (i for i, e in enumerate(q) if e.get("status") == "RUNNING"), None
    )
    if run_idx is not None:
        entry = q[run_idx]
        try:
            status_info = jm.get_execution_status(entry["execution_name"])
            s = status_info.get("overall_status")
            if s in ("SUCCEEDED", "FAILED", "CANCELLED", "COMPLETED", "ERROR"):
                entry["status"] = (
                    "SUCCEEDED" if s in ("SUCCEEDED", "COMPLETED") else s
                )
                entry["message"] = status_info.get("error", "") or s
                changed = True
        except Exception as e:
            entry["status"] = "ERROR"
            entry["message"] = str(e)
            changed = True

        if changed:
            save_queue_to_gcs(
                queue_name,
                entries=q,
                queue_running=running_flag,
                bucket_name=bucket_name,
            )
            return {"ok": True, "message": "updated running", "changed": True}

    # 2) Launch next PENDING if none running
    pend_idx = next(
        (i for i, e in enumerate(q) if e.get("status") == "PENDING"), None
    )
    if pend_idx is None:
        return {"ok": True, "message": "no pending", "changed": False}

    if not launcher:
        return {
            "ok": False,
            "message": "launcher not provided",
            "changed": False,
        }

    entry = q[pend_idx]
    try:
        exec_info = launcher(entry["params"])
        time.sleep(SAFE_LAG_SECONDS_AFTER_RUNNING)
        entry["execution_name"] = exec_info["execution_name"]
        entry["timestamp"] = exec_info["timestamp"]
        entry["gcs_prefix"] = exec_info["gcs_prefix"]
        entry["status"] = "RUNNING"
        entry["message"] = "Launched"
        changed = True
    except Exception as e:
        entry["status"] = "ERROR"
        entry["message"] = f"launch failed: {e}"
        changed = True

    if changed:
        save_queue_to_gcs(
            queue_name,
            entries=q,
            queue_running=running_flag,
            bucket_name=bucket_name,
        )
    return {"ok": True, "message": entry["message"], "changed": True}


# ─────────────────────────────
# Stateless queue tick endpoint (AFTER defs/constants)
# ─────────────────────────────


def handle_queue_tick_from_query_params(
    query_params: Dict[str, Any],
    bucket_name: Optional[str] = None,
    launcher: Optional[callable] = None,
) -> Optional[dict]:
    """
    If ?queue_tick=1 is present, process one headless queue tick and return the result.
    Otherwise return None. Safe to call early in a Streamlit page.
    """
    if not query_params:
        return None
    try:
        qp = {
            k: (v[0] if isinstance(v, list) else v)
            for k, v in dict(query_params).items()
        }
    except Exception:
        qp = dict(query_params)

    if qp.get("queue_tick") != "1":
        return None

    qname = qp.get("name") or DEFAULT_QUEUE_NAME
    bkt = bucket_name or GCS_BUCKET
    try:
        return queue_tick_once_headless(qname, bkt, launcher=launcher)
    except Exception as e:
        logger.exception("queue_tick handler failed: %s", e)
        return {"ok": False, "error": str(e)}


# ─────────────────────────────
