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

# Stateless queue tick endpoint: /?queue_tick=1&name=<queue>&bucket=<bucket>
if query_params.get("queue_tick") == "1":
    qname = query_params.get("name") or DEFAULT_QUEUE_NAME
    bkt = query_params.get("bucket") or GCS_BUCKET
    res = queue_tick_once_headless(qname, bucket_name=bkt)
    st.json(res)
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


def effective_sql(table: str, query: str) -> Optional[str]:
    if query and query.strip():
        return query.strip()
    if table and table.strip():
        return f"SELECT * FROM {table.strip()}"
    return None


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


def _sanitize_queue_name(name: str) -> str:
    name = (name or "default").strip().lower()
    # keep alnum, dash, underscore; replace others with '-'
    return re.sub(r"[^a-z0-9_\-]+", "-", name) or "default"


def _queue_blob_path(queue_name: str) -> str:
    q = _sanitize_queue_name(queue_name)
    return f"{QUEUE_ROOT}/{q}/queue.json"


def load_queue_from_gcs(
    queue_name: str, bucket_name: Optional[str] = None
) -> list[dict]:
    bucket_name = bucket_name or st.session_state.get("gcs_bucket", GCS_BUCKET)
    bucket = storage.Client().bucket(bucket_name)
    blob = bucket.blob(_queue_blob_path(queue_name))
    if not blob.exists():
        return []
    try:
        payload = json.loads(blob.download_as_text())
        if isinstance(payload, dict) and "entries" in payload:
            return payload["entries"]
        if isinstance(payload, list):
            return payload
    except Exception as e:
        logger.warning("Failed to load queue from GCS: %s", e)
    return []


def save_queue_to_gcs(
    queue_name: str, entries: list[dict], bucket_name: Optional[str] = None
) -> None:
    bucket_name = bucket_name or st.session_state.get("gcs_bucket", GCS_BUCKET)
    bucket = storage.Client().bucket(bucket_name)
    blob = bucket.blob(_queue_blob_path(queue_name))
    payload = {
        "version": 1,
        "saved_at": datetime.utcnow().isoformat() + "Z",
        "entries": entries,
    }
    blob.upload_from_string(
        json.dumps(payload, indent=2), content_type="application/json"
    )


def queue_tick_once_headless(
    queue_name: str, bucket_name: Optional[str] = None
) -> dict:
    """Advance the queue by at most one transition; safe to call from Scheduler."""
    bucket_name = bucket_name or GCS_BUCKET
    q = load_queue_from_gcs(queue_name, bucket_name=bucket_name)
    if not q:
        return {"ok": True, "message": "empty queue", "changed": False}

    changed = False

    # 1) Update RUNNING job
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
            save_queue_to_gcs(queue_name, q, bucket_name=bucket_name)
            return {"ok": True, "message": "updated running", "changed": True}

    # 2) Launch next PENDING if not running
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
        save_queue_to_gcs(queue_name, q, bucket_name=bucket_name)
    return {"ok": True, "message": entry["message"], "changed": True}


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
        st.session_state.job_queue = load_queue_from_gcs(
            st.session_state.queue_name
        )
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
                "User", value=(st.session_state.sf_params or {}).get("user", "")
            )
            sf_account = st.text_input(
                "Account",
                value=(st.session_state.sf_params or {}).get("account", ""),
            )
            sf_wh = st.text_input(
                "Warehouse",
                value=(st.session_state.sf_params or {}).get("warehouse", ""),
            )
            sf_db = st.text_input(
                "Database",
                value=(st.session_state.sf_params or {}).get("database", ""),
            )
        with c2:
            sf_schema = st.text_input(
                "Schema",
                value=(st.session_state.sf_params or {}).get("schema", ""),
            )
            sf_role = st.text_input(
                "Role", value=(st.session_state.sf_params or {}).get("role", "")
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
            st.session_state.job_queue = load_queue_from_gcs(
                st.session_state.queue_name
            )
            st.success(f"Loaded queue '{st.session_state.queue_name}' from GCS")

        if cqn3.button("⬆️ Save to GCS"):
            save_queue_to_gcs(
                st.session_state.queue_name, st.session_state.job_queue
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
        qc1, qc2, qc3, qc4 = st.columns(4)
        if qc1.button(
            "▶️ Start Queue", disabled=(len(st.session_state.job_queue) == 0)
        ):
            st.session_state["queue_running"] = True
            save_queue_to_gcs(
                st.session_state.queue_name, st.session_state.job_queue
            )
        if qc2.button("⏸️ Stop Queue"):
            st.session_state["queue_running"] = False
            save_queue_to_gcs(
                st.session_state.queue_name, st.session_state.job_queue
            )
        if qc3.button("⏭️ Process Next Step"):
            res = queue_tick_once_headless(
                st.session_state.queue_name, st.session_state["gcs_bucket"]
            )
            st.toast(res.get("message", "tick"))
            st.rerun()  # tick happens below
        if qc4.button("💾 Save now"):
            save_queue_to_gcs(
                st.session_state.queue_name, st.session_state.job_queue
            )
            st.success("Queue saved to GCS.")

        # Queue table
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
            st.dataframe(df_queue, use_container_width=True)

    # ─────────────────────────────
    # Queue worker (state machine)
    # ─────────────────────────────
    def _queue_tick():
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
            save_queue_to_gcs(st.session_state.queue_name, q)

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
