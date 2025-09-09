# app_shared.py — shared helpers for Robyn Streamlit app
import os, io, json, time
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
# Cloud Run Jobs client wrapper
# ─────────────────────────────
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
    queue_name: str, bucket_name: Optional[str] = None
) -> dict:
    """
    Advance the queue by at most one transition; safe to call from Scheduler.
    Uses only GCS state (stateless), so it survives UI reloads.
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

    # 1) Update RUNNING job if any
    run_idx = next(
        (i for i, e in enumerate(q) if e.get("status") == "RUNNING"), None
    )
    if run_idx is not None:
        entry = q[run_idx]
        try:
            status_info = job_manager.get_execution_status(
                entry["execution_name"]
            )
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

    entry = q[pend_idx]
    try:
        exec_info = prepare_and_launch_job(entry["params"])
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
) -> Optional[dict]:
    """
    If ?queue_tick=1 is present, process one headless queue tick and return the result.
    Otherwise return None. Safe to call early in a Streamlit page.
    """
    if not query_params:
        return None
    # Streamlit's st.query_params is a Mapping; normalize to plain dict[str,str]
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
    bkt = bucket_name or st.session_state.get("gcs_bucket", GCS_BUCKET)
    try:
        return queue_tick_once_headless(qname, bkt)
    except Exception as e:
        logger.exception("queue_tick handler failed: %s", e)
        return {"ok": False, "error": str(e)}


# ─────────────────────────────
# UI layout: two tabs
# ─────────────────────────────
st.title("Robyn MMM Trainer")
tab_conn, tab_train = st.tabs(
    ["1) Snowflake Connection", "2) Configure & Train Models"]
)

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
            sf_db = st.text_input(
                "Database",
                value=(st.session_state.sf_params or {}).get("database", "")
                or os.getenv("SF_DATABASE"),
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
            sf_password = st.text_input("Password", type="password")

        submitted = st.form_submit_button("🔌 Connect")
        if submitted:
            try:
                conn = _connect_snowflake(
                    user=sf_user,
                    password=sf_password,
                    account=sf_account,
                    warehouse=sf_wh,
                    database=sf_db,
                    schema=sf_schema,
                    role=sf_role,
                )
                st.session_state["sf_params"] = dict(
                    user=sf_user,
                    account=sf_account,
                    warehouse=sf_wh,
                    database=sf_db,
                    schema=sf_schema,
                    role=sf_role,
                )
                st.session_state["sf_conn"] = conn
                st.session_state["sf_connected"] = True
                st.success(
                    f"Connected to Snowflake as `{sf_user}` on `{sf_account}`."
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
                    st.session_state["sf_conn"] = None
                    st.session_state["sf_connected"] = False
                    st.success("Disconnected.")
                except Exception as e:
                    st.error(f"Disconnect error: {e}")
        with st.expander("🧪 Query Runner (optional)"):
            adhoc_sql = st.text_area(
                "Enter SQL to preview (SELECT only)",
                value="SELECT CURRENT_TIMESTAMP;",
            )
            if st.button("Run query", key="run_adhoc"):
                try:
                    df_prev = run_sql(adhoc_sql)
                    st.dataframe(df_prev, use_container_width=True)
                except Exception as e:
                    st.error(f"Query failed: {e}")
    else:
        st.info("Not connected. Fill the form above and click **Connect**.")

# ============= TAB 2: Configure & Train =============
with tab_train:
    st.subheader("Robyn configuration & training")
    if not st.session_state.sf_connected:
        st.warning("Please connect to Snowflake in tab 1 first.")
        st.stop()

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
                    st.dataframe(df_prev, use_container_width=True)
                except Exception as e:
                    st.error(f"Preview failed: {e}")

    # Robyn config
    with st.expander("Robyn configuration"):
        country = st.text_input("Country", value="fr")
        iterations = st.number_input("Iterations", value=200, min_value=50)
        trials = st.number_input("Trials", value=5, min_value=1)
        train_size = st.text_input("Train size", value="0.7,0.9")
        revision = st.text_input("Revision tag", value="r100")
        date_input = st.text_input("Date tag", value=time.strftime("%Y-%m-%d"))

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
        factor_vars = st.text_input("factor_vars", value="IS_WEEKEND,TV_IS_ON")
        organic_vars = st.text_input("organic_vars", value="ORGANIC_TRAFFIC")

    # Outputs
    with st.expander("Outputs"):
        gcs_bucket = st.text_input(
            "GCS bucket for outputs", value=st.session_state["gcs_bucket"]
        )
        st.session_state["gcs_bucket"] = gcs_bucket
        ann_file = st.file_uploader(
            "Optional: enriched_annotations.csv", type=["csv"]
        )

    # =============== Single-run button (unchanged) ===============
    def create_job_config_single(
        data_gcs_path: str, timestamp: str, annotations_gcs_path: Optional[str]
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
        timings: list[dict[str, float]] = []

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

                    # 2) Convert to Parquet
                    with timed_step("Convert to Parquet", timings):
                        parquet_path = os.path.join(td, "input_data.parquet")
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
                        with timed_step("Upload annotations to GCS", timings):
                            annotations_path = os.path.join(
                                td, "enriched_annotations.csv"
                            )
                            with open(annotations_path, "wb") as f:
                                f.write(ann_file.read())
                            annotations_blob = f"training-data/{timestamp}/enriched_annotations.csv"
                            annotations_gcs_path = upload_to_gcs(
                                gcs_bucket, annotations_path, annotations_blob
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

    # ===================== BATCH QUEUE (CSV) =====================
    with st.expander(
        "📚 Batch queue (CSV) — queue & run multiple jobs sequentially",
        expanded=False,
    ):
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
                    "trials": 6,
                    "train_size": "0.7,0.9",
                    "paid_media_spends": "GA_SUPPLY_COST, GA_DEMAND_COST, META_DEMAND_COST, TV_COST",
                    "paid_media_vars": "GA_SUPPLY_COST, GA_DEMAND_COST, META_DEMAND_COST, TV_COST",
                    "context_vars": "IS_WEEKEND,TV_IS_ON",
                    "factor_vars": "IS_WEEKEND,TV_IS_ON",
                    "organic_vars": "ORGANIC_TRAFFIC",
                    "gcs_bucket": st.session_state["gcs_bucket"],
                    "table": "MESHED_BUYCYCLE.GROWTH.TABLE_A",
                    "query": "",  # either table or query
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

        up = st.file_uploader("Upload batch CSV", type=["csv"], key="batch_csv")
        parsed_df = None
        if up:
            try:
                parsed_df = pd.read_csv(up)
                st.success(f"Loaded {len(parsed_df)} rows")
                st.dataframe(parsed_df.head(), use_container_width=True)
            except Exception as e:
                st.error(f"Failed to parse CSV: {e}")

        def _normalize_row(row: pd.Series) -> dict:
            def _g(v, default):
                return (
                    row.get(v) if (v in row and pd.notna(row[v])) else default
                )

            return {
                "country": str(_g("country", country)),
                "revision": str(_g("revision", revision)),
                "date_input": str(_g("date_input", date_input)),
                "iterations": int(_g("iterations", iterations)),
                "trials": int(_g("trials", trials)),
                "train_size": str(_g("train_size", train_size)),
                "paid_media_spends": str(
                    _g("paid_media_spends", paid_media_spends)
                ),
                "paid_media_vars": str(_g("paid_media_vars", paid_media_vars)),
                "context_vars": str(_g("context_vars", context_vars)),
                "factor_vars": str(_g("factor_vars", factor_vars)),
                "organic_vars": str(_g("organic_vars", organic_vars)),
                "gcs_bucket": str(
                    _g("gcs_bucket", st.session_state["gcs_bucket"])
                ),
                "table": str(_g("table", table or "")),
                "query": str(_g("query", query or "")),
                "annotations_gcs_path": str(_g("annotations_gcs_path", "")),
            }

        c_left, c_right = st.columns(2)
        if c_left.button("➕ Enqueue all rows", disabled=(parsed_df is None)):
            if parsed_df is not None:
                # next id after current max
                next_id = (
                    max(
                        [e["id"] for e in st.session_state.job_queue], default=0
                    )
                    + 1
                )
                new_entries = []
                for i, row in parsed_df.iterrows():
                    params = _normalize_row(row)
                    if not (params.get("query") or params.get("table")):
                        continue
                    new_entries.append(
                        {
                            "id": next_id + i,
                            "params": params,
                            "status": "PENDING",
                            "timestamp": None,
                            "execution_name": None,
                            "gcs_prefix": None,
                            "message": "",
                        }
                    )
                st.session_state.job_queue.extend(new_entries)
                save_queue_to_gcs(
                    st.session_state.queue_name, st.session_state.job_queue
                )
                st.success(
                    f"Enqueued {len(new_entries)} job(s) and saved to GCS."
                )

        if c_right.button("🧹 Clear queue"):
            st.session_state["job_queue"] = []
            st.session_state["queue_running"] = False
            save_queue_to_gcs(st.session_state.queue_name, [])
            st.success("Queue cleared & saved to GCS.")

        # Queue controls
        st.caption(
            f"Queue status: {'▶️ RUNNING' if st.session_state.queue_running else '⏸️ STOPPED'} · "
            f"{sum(e['status']=='PENDING' for e in st.session_state.job_queue)} pending · "
            f"{sum(e['status']=='RUNNING' for e in st.session_state.job_queue)} running"
        )
        # doc = load_queue_from_gcs(queue_name, bucket_name=bucket_name)
        # st.write(f"Last saved at (GCS): {doc.get('saved_at','—')}")
        if st.button("🔁 Refresh from GCS"):
            _ = load_queue_from_gcs(st.session_state.queue_name)
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
            res = queue_tick_once_headless(
                st.session_state.queue_name, st.session_state["gcs_bucket"]
            )
            st.toast(res.get("message", "tick"))
            maybe_refresh_queue_from_gcs(force=True)
            st.rerun()
        if qc4.button("💾 Save now"):
            save_queue_to_gcs(
                st.session_state.queue_name, st.session_state.job_queue
            )
            st.success("Queue saved to GCS.")

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
            if "Delete" not in df_queue.columns:
                df_queue.insert(0, "Delete", False)

            edited = st.data_editor(
                df_queue,
                key="queue_editor",  # keep widget state stable across reruns
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Delete": st.column_config.CheckboxColumn(
                        "Delete", help="Mark to remove from queue"
                    )
                },
            )

            # Safe read: if user or Streamlit removes the column, don't crash
            ids_to_delete = set()
            if "Delete" in edited.columns:
                ids_to_delete = set(
                    edited.loc[edited["Delete"] == True, "ID"]
                    .astype(int)
                    .tolist()
                )

            if st.button("🗑 Delete selected (PENDING/ERROR only)"):
                new_q, blocked = [], []
                for e in st.session_state.job_queue:
                    if e["id"] in ids_to_delete:
                        if e.get("status") in (
                            "PENDING",
                            "ERROR",
                            "CANCELLED",
                            "FAILED",
                        ):
                            # drop it
                            continue
                        else:
                            # don't delete RUNNING/SUCCEEDED
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
                # st.dataframe(df_queue, use_container_width=True)

    # ─────────────────────────────
    # Queue worker (state machine)
    # ─────────────────────────────
    def _queue_tick():
        maybe_refresh_queue_from_gcs()
        q = st.session_state.job_queue
        if not q:
            return

        changed = False

        # 1) Update RUNNING job status (if any)
        running = [e for e in q if e["status"] == "RUNNING"]
        if running:
            entry = running[0]
            try:
                status_info = job_manager.get_execution_status(
                    entry["execution_name"]
                )
                s = status_info.get("overall_status")
                if s in (
                    "SUCCEEDED",
                    "FAILED",
                    "CANCELLED",
                    "COMPLETED",
                    "ERROR",
                ):
                    entry["status"] = (
                        "SUCCEEDED" if s in ("SUCCEEDED", "COMPLETED") else s
                    )
                    entry["message"] = status_info.get("error", "") or s
                    changed = True
                return (
                    save_queue_to_gcs(st.session_state.queue_name, q)
                    if changed
                    else None
                )
            except Exception as e:
                entry["status"] = "ERROR"
                entry["message"] = str(e)
                changed = True
                return save_queue_to_gcs(st.session_state.queue_name, q)

        # 2) If no RUNNING job and queue_running, launch next PENDING
        if st.session_state.queue_running:
            pending = [e for e in q if e["status"] == "PENDING"]
            if not pending:
                return
            entry = pending[0]
            try:
                exec_info = prepare_and_launch_job(entry["params"])
                time.sleep(SAFE_LAG_SECONDS_AFTER_RUNNING)
                entry["execution_name"] = exec_info["execution_name"]
                entry["timestamp"] = exec_info["timestamp"]
                entry["gcs_prefix"] = exec_info["gcs_prefix"]
                entry["status"] = "RUNNING"
                entry["message"] = "Launched"
                st.session_state.job_executions.append(exec_info)
                changed = True
            except Exception as e:
                entry["status"] = "ERROR"
                entry["message"] = f"launch failed: {e}"
                changed = True

        if changed:
            st.session_state.queue_saved_at = save_queue_to_gcs(
                st.session_state.queue_name,
                q,
                queue_running=st.session_state.queue_running,
            )

    # Tick the queue on every rerun
    _queue_tick()

    # ─────────────────────────────
    # Job Status Monitor (latest single-run)
    # ─────────────────────────────
    st.subheader("📊 Job Status Monitor")
    if st.session_state.job_executions:
        latest_job = st.session_state.job_executions[-1]
        execution_name = latest_job["execution_name"]

        if st.button("🔍 Check Status"):
            status_info = job_manager.get_execution_status(execution_name)
            st.json(status_info)
            latest_job["status"] = status_info.get("overall_status", "UNKNOWN")
            latest_job["last_checked"] = datetime.now().isoformat()

        if st.button("📁 View Results"):
            gcs_prefix_view = latest_job.get("gcs_prefix")
            bucket_view = latest_job.get("gcs_bucket", GCS_BUCKET)
            st.info(f"Check results at: gs://{bucket_view}/{gcs_prefix_view}/")
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
                    )
                    st.download_button(
                        "Download full training log",
                        data=log_bytes,
                        file_name=f"robyn_training_{latest_job.get('timestamp','')}.log",
                        mime="text/plain",
                        key=f"dl_log_{latest_job.get('timestamp','')}",
                    )
                else:
                    st.info(
                        "Training log not yet available. Check again after job completes."
                    )
            except Exception as e:
                st.warning(f"Could not fetch training log: {e}")

        if st.button("📋 Show All Jobs"):
            df_jobs = pd.DataFrame(
                [
                    {
                        "Timestamp": job.get("timestamp", ""),
                        "Status": job.get("status", "UNKNOWN"),
                        "Execution": job.get("execution_name", "").split("/")[
                            -1
                        ],
                        "Last Checked": job.get("last_checked", "Never"),
                        "Revision": job.get("revision", ""),
                        "Country": job.get("country", ""),
                    }
                    for job in st.session_state.job_executions
                ]
            )
            st.dataframe(df_jobs, use_container_width=True)
    else:
        st.info(
            "No jobs launched yet in this session. Use single-run or batch queue above."
        )

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
            st.dataframe(df_times, use_container_width=True)
            st.write(f"**Total setup time:** {_fmt_secs(total)}")
            st.write(
                "**Note**: Training runs asynchronously in Cloud Run Jobs."
            )

# ─────────────────────────────
# Sidebar: system info + auto-refresh
# ─────────────────────────────
with st.sidebar:
    st.subheader("🔧 System Info")
    st.write(f"**Project ID**: {PROJECT_ID}")
    st.write(f"**Region**: {REGION}")
    st.write(f"**Training Job**: {TRAINING_JOB_NAME}")
    st.write(f"**GCS Bucket**: {GCS_BUCKET}")
    try:
        import psutil

        memory = psutil.virtual_memory()
        st.write(f"**Available Memory**: {memory.available / 1024**3:.1f} GB")
        st.write(f"**Memory Usage**: {memory.percent:.1f}%")
    except ImportError:
        st.write("**Memory Info**: psutil not available")

    if st.session_state["job_executions"]:
        st.subheader("📋 Recent Jobs")
        for i, exec_info in enumerate(st.session_state.job_executions[-3:]):
            status = exec_info.get("status", "UNKNOWN")
            ts = exec_info.get("timestamp", "")
            st.write(f"**Job {i+1}**: {status}")
            st.write(f"*{ts}*")

    # Manual auto-refresh toggle (simple rerun)
    if st.toggle(
        "Auto-refresh status",
        value=st.session_state["auto_refresh"],
        key="auto_refresh",
    ):
        st.rerun()


@st.cache_resource
def get_data_processor():
    return DataProcessor()


@st.cache_resource
def get_job_manager():
    return CloudRunJobManager(PROJECT_ID, REGION)
