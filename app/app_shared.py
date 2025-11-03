# app_shared.py — shared helpers for Robyn Streamlit app
import base64
import io
import json
import logging
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from uuid import uuid4

import numpy as np
import pandas as pd
import plotly.express as px
import snowflake.connector as sf
import streamlit as st
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from data_processor import DataProcessor
from google.api_core.exceptions import PreconditionFailed
from google.cloud import run_v2, secretmanager, storage

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

# Canonical job_history schema & normalization
JOB_HISTORY_COLUMNS = [
    "job_id",  # canonical id: gcs_prefix; queue id can go into 'queue_id' (optional)
    "state",  # SUCCEEDED | FAILED | CANCELLED | ERROR
    "country",
    "revision",
    "date_input",
    "iterations",
    "trials",
    "train_size",
    "dep_var",
    "adstock",
    "start_time",  # ISO 8601 UTC
    "end_time",  # ISO 8601 UTC
    "duration_minutes",
    "gcs_prefix",
    "bucket",
    "exec_name",  # short execution id (last path segment)
    "execution_name",  # full resource path (optional, for debugging)
]

# Columns that come from the Queue Builder / params
QUEUE_PARAM_COLUMNS = [
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
    "gcs_bucket",  # param override bucket
    "table",
    "query",
    "dep_var",
    "date_var",
    "adstock",
    "annotations_gcs_path",
]

# Canonical job_history schema (builder params + exec/info)
JOB_HISTORY_COLUMNS = (
    ["job_id", "state"]
    + QUEUE_PARAM_COLUMNS
    + [
        "start_time",  # ISO 8601 UTC
        "end_time",  # ISO 8601 UTC
        "duration_minutes",
        "gcs_prefix",
        "bucket",  # output bucket actually used
        "exec_name",  # short execution id
        "execution_name",  # full execution resource
        "message",  # <- Msg from queue
    ]
)


def _short_exec_name(x: str) -> str:
    if not isinstance(x, str) or not x:
        return ""
    # Accept either full resource path or already-short ids
    if "/executions/" in x:
        return x.split("/executions/")[-1]
    return x.split("/")[-1]


def _iso_utc(s) -> str:
    # Parse any reasonable timestamp and write as UTC ISO seconds
    import pandas as pd

    ts = pd.to_datetime(s, utc=True, errors="coerce")
    if pd.isna(ts):
        return ""
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_job_history_df() -> pd.DataFrame:
    # Matches fields written by run_all.R::append_to_job_history()
    cols = JOB_HISTORY_COLUMNS
    return pd.DataFrame(columns=cols)


def _safe_tick_once(
    queue_name: str,
    bucket_name: Optional[str] = None,
    launcher: Optional[callable] = None,  # type: ignore
    max_retries: int = 3,
) -> dict:
    """
    Single safe tick with optimistic concurrency on GCS:
    - If a RUNNING/LAUNCHING job exists: update its status (or promote LAUNCHING→RUNNING) and persist guarded.
    - Else lease the first PENDING job by writing LAUNCHING with an if_generation_match precondition,
      then launch outside the critical section, and persist the RUNNING/ERROR result with another guarded write.
    Returns {ok, message, changed}.
    """
    bucket_name = bucket_name or GCS_BUCKET
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(_queue_blob_path(queue_name))

    def _init_doc() -> dict:
        return {
            "version": 1,
            "saved_at": datetime.utcnow().isoformat() + "Z",
            "entries": [],
            "queue_running": True,
        }

    for _ in range(max_retries):
        # Ensure the blob exists, then load doc + current generation
        if not blob.exists():
            try:
                blob.upload_from_string(
                    json.dumps(_init_doc(), indent=2),
                    content_type="application/json",
                    if_generation_match=0,  # create-if-not-exists
                )
            except PreconditionFailed:
                # Someone created it concurrently; continue to normal path
                pass

        blob.reload()  # get current generation
        gen = int(blob.generation)  # type: ignore
        try:
            doc = json.loads(blob.download_as_text())
        except Exception:
            doc = _init_doc()

        q = doc.get("entries", [])
        running_flag = doc.get("queue_running", True)

        if not q:
            return {"ok": True, "message": "empty queue", "changed": False}
        if not running_flag:
            return {"ok": True, "message": "queue is paused", "changed": False}

        jm = CloudRunJobManager(PROJECT_ID, REGION)  # type: ignore

        # 1) Update RUNNING/LAUNCHING job if any
        run_idx = next(
            (
                i
                for i, e in enumerate(q)
                if e.get("status") in ("RUNNING", "LAUNCHING")
            ),
            None,
        )
        if run_idx is not None:
            entry = q[run_idx]
            changed = False
            message = "tick"

            try:
                status_info = jm.get_execution_status(
                    entry.get("execution_name", "")
                )
                s = (status_info.get("overall_status") or "").upper()
                if s in (
                    "SUCCEEDED",
                    "FAILED",
                    "CANCELLED",
                    "COMPLETED",
                    "ERROR",
                ):
                    final_state = (
                        "SUCCEEDED" if s in ("SUCCEEDED", "COMPLETED") else s
                    )
                    entry["status"] = final_state
                    entry["message"] = (
                        status_info.get("error", "") or final_state
                    )
                    message = entry["message"]
                    changed = True
                elif entry.get("status") == "LAUNCHING":
                    # Visible execution, promote to RUNNING
                    entry["status"] = "RUNNING"
                    message = "running"
                    changed = True
            except Exception as e:
                entry["status"] = "ERROR"
                entry["message"] = str(e)
                message = entry["message"]
                changed = True

            if changed:
                doc["saved_at"] = datetime.utcnow().isoformat() + "Z"
                try:
                    blob.upload_from_string(
                        json.dumps(doc, indent=2),
                        content_type="application/json",
                        if_generation_match=gen,  # guarded write
                    )
                    return {"ok": True, "message": message, "changed": True}
                except PreconditionFailed:
                    # Lost the race; retry with fresh doc/generation
                    continue

            return {"ok": True, "message": "no change", "changed": False}

        # 2) Lease & launch next PENDING
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

        # --- Critical section: lease it (LAUNCHING) guarded by generation match ---
        entry["status"] = "LAUNCHING"
        entry["message"] = "Launching..."
        doc["saved_at"] = datetime.utcnow().isoformat() + "Z"
        try:
            blob.upload_from_string(
                json.dumps(doc, indent=2),
                content_type="application/json",
                if_generation_match=gen,  # only one process can acquire the lease
            )
        except PreconditionFailed:
            # Another process leased it; retry from the top
            continue

        # --- Outside critical section: perform the actual launch ---
        message = "Launched"
        try:
            exec_info = launcher(entry["params"])
            time.sleep(SAFE_LAG_SECONDS_AFTER_RUNNING)
            entry["execution_name"] = exec_info.get("execution_name")
            entry["timestamp"] = exec_info.get("timestamp")
            entry["gcs_prefix"] = exec_info.get("gcs_prefix")
            entry["status"] = "RUNNING"
            entry["message"] = "Launched"
        except Exception as e:
            entry["status"] = "ERROR"
            entry["message"] = f"launch failed: {e}"
            message = entry["message"]

        # Persist the post-launch state with another guarded write
        blob.reload()
        gen2 = int(blob.generation)  # type: ignore
        doc["saved_at"] = datetime.utcnow().isoformat() + "Z"
        try:
            blob.upload_from_string(
                json.dumps(doc, indent=2),
                content_type="application/json",
                if_generation_match=gen2,
            )
            return {"ok": True, "message": message, "changed": True}
        except PreconditionFailed:
            # A concurrent status update happened (e.g., another tick promoted LAUNCHING→RUNNING).
            # Retry loop merges on next pass.
            continue

    return {"ok": False, "message": "contention: retry later", "changed": False}


def normalize_job_history_df(df: "pd.DataFrame"):
    import pandas as pd

    df = (df if isinstance(df, pd.DataFrame) else pd.DataFrame()).copy()

    # Backward compat renames
    if "status" in df.columns and "state" not in df.columns:
        df = df.rename(columns={"status": "state"})
    if "gcs_bucket" in df.columns and "bucket" not in df.columns:
        # do not rename; we now keep both 'gcs_bucket' (param) and 'bucket' (output)
        pass

    # Ensure all expected columns exist
    for c in JOB_HISTORY_COLUMNS:
        if c not in df.columns:
            df[c] = pd.NA

    # Exec fields present & normalized
    df["execution_name"] = df["execution_name"].fillna("").astype(str)
    df["exec_name"] = df["exec_name"].fillna("").astype(str)

    # Backfill exec_name from execution_name when missing; always short form
    mask = df["exec_name"].str.strip().eq("") & df[
        "execution_name"
    ].str.strip().ne("")
    if mask.any():
        df.loc[mask, "exec_name"] = df.loc[mask, "execution_name"].apply(
            _short_exec_name
        )
    df["exec_name"] = df["exec_name"].apply(_short_exec_name)

    # Normalize times
    df["start_time"] = df["start_time"].apply(_iso_utc)
    df["end_time"] = df["end_time"].apply(_iso_utc)

    # Canonical job_id
    def _canon_job_id(row):
        jid_raw = row.get("job_id")
        gpref_raw = row.get("gcs_prefix")

        # Treat pd.NA/NaN/None as empty strings, then cast
        jid = "" if pd.isna(jid_raw) else str(jid_raw)
        gpref = "" if pd.isna(gpref_raw) else str(gpref_raw)

        # Prefer gcs_prefix when job_id is a numeric queue id
        if jid.isdigit() and gpref:
            return gpref
        return jid if jid else gpref

    df["job_id"] = df.apply(_canon_job_id, axis=1)

    # Coerce builder/param columns to string, with nice CSV-ish join for lists
    def _csvish(x):
        if isinstance(x, (list, tuple)):
            return ", ".join(str(v) for v in x if str(v).strip())
        return "" if (x is pd.NA or x is None) else str(x)

    for c in QUEUE_PARAM_COLUMNS:
        df[c] = df[c].apply(_csvish)

    # Backfill duration
    st_ts = pd.to_datetime(df["start_time"], utc=True, errors="coerce")
    et_ts = pd.to_datetime(df["end_time"], utc=True, errors="coerce")
    need = df["duration_minutes"].isna() & st_ts.notna() & et_ts.notna()
    if need.any():
        df.loc[need, "duration_minutes"] = (
            et_ts - st_ts
        ).dt.total_seconds() / 60.0

    # Order & (optionally) de-dup by job_id, keeping first non-empty values
    df = df[JOB_HISTORY_COLUMNS]

    if "job_id" in df.columns and not df.empty:

        def _first_non_empty(series):
            for x in series:
                if pd.notna(x) and (not isinstance(x, str) or x.strip() != ""):
                    return x
            return pd.NA

        df = df.groupby("job_id", as_index=False, dropna=False).agg(
            _first_non_empty
        )
        df = df[JOB_HISTORY_COLUMNS]

    # Final sort
    df = df.sort_values(
        ["end_time", "start_time"], ascending=False, na_position="last"
    ).reset_index(drop=True)
    return df


@st.cache_resource
def get_data_processor():
    return DataProcessor()


@st.cache_resource
def get_job_manager():
    return CloudRunJobManager(PROJECT_ID, REGION)  # type: ignore


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
                        key=lambda e: getattr(e, "create_time", None),  # type: ignore
                        reverse=True,
                    )  # type: ignore
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


def _gsm_secret_latest_resource(secret_id: str) -> str:
    # Accept "sf-private-key" or full resource path; always return .../versions/latest
    if not secret_id:
        raise RuntimeError("SF_PRIVATE_KEY_SECRET is not set")
    if secret_id.startswith("projects/"):
        base = secret_id
    else:
        if not PROJECT_ID:
            raise RuntimeError("PROJECT_ID env is not set")
        base = f"projects/{PROJECT_ID}/secrets/{secret_id}"
    return base + "/versions/latest"


def _load_private_key_bytes_from_gsm(secret_id: str) -> bytes:
    """
    Reads a PEM (or already PKCS#8 DER) private key from GSM and returns PKCS#8 DER bytes,
    as required by snowflake-connector-python (private_key=...).
    """
    client = secretmanager.SecretManagerServiceClient()
    name = _gsm_secret_latest_resource(secret_id)
    payload = client.access_secret_version(name=name).payload.data

    # Try PEM first
    try:
        pem = payload.decode("utf-8")
        key = serialization.load_pem_private_key(
            pem.encode("utf-8"),
            password=None,  # set if your key is passphrase-protected
            backend=default_backend(),
        )
        return key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    except Exception:
        # If not PEM, maybe it’s already DER PKCS#8
        return bytes(payload)


def _sf_params_from_env() -> Optional[dict]:
    u = os.getenv("SF_USER")
    a = os.getenv("SF_ACCOUNT")
    w = os.getenv("SF_WAREHOUSE")
    d = os.getenv("SF_DATABASE")
    s = os.getenv("SF_SCHEMA")
    r = os.getenv("SF_ROLE", "")
    secret_id = os.getenv("SF_PRIVATE_KEY_SECRET")  # e.g. "sf-private-key"

    if all([u, a, w, d, s]) and secret_id:
        pkb = _load_private_key_bytes_from_gsm(secret_id)
        return dict(
            user=u,
            account=a,
            warehouse=w,
            database=d,
            schema=s,
            role=r or None,
            private_key=pkb,  # <- key-pair auth
        )

    # (Optional) fall back to password if explicitly present
    p = os.getenv("SF_PASSWORD")
    if all([u, p, a, w, d, s]):
        return dict(
            user=u,
            password=p,
            account=a,
            warehouse=w,
            database=d,
            schema=s,
            role=r or None,
        )
    return None


def _connect_snowflake(
    user,
    account,
    warehouse,
    database,
    schema,
    role,
    password=None,
    private_key=None,
):
    if private_key is not None:
        return sf.connect(
            user=user,
            account=account,
            warehouse=warehouse,
            database=database,
            schema=schema,
            role=role or None,
            private_key=private_key,  # <- crucial
            client_session_keep_alive=True,
            session_parameters={"CLIENT_SESSION_KEEP_ALIVE": True},
        )
    else:
        return sf.connect(
            user=user,
            password=password,
            account=account,
            warehouse=warehouse,
            database=database,
            schema=schema,
            role=role or None,
            client_session_keep_alive=True,
            session_parameters={"CLIENT_SESSION_KEEP_ALIVE": True},
        )


def ensure_sf_conn() -> sf.SnowflakeConnection:
    conn = st.session_state.get("sf_conn")
    params = st.session_state.get("sf_params") or {}

    if conn is not None:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchall()
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            st.session_state["sf_conn"] = None

    # Try to get key bytes from session state, or load from Secret Manager
    pk_bytes = st.session_state.get("_sf_private_key_bytes")

    # If no key in session but we have params, try loading from persistent storage
    if not pk_bytes and params:
        try:
            from gcp_secrets import access_secret

            PERSISTENT_KEY_SECRET_ID = os.getenv(
                "SF_PERSISTENT_KEY_SECRET", "sf-private-key-persistent"
            )
            pem_bytes = access_secret(PERSISTENT_KEY_SECRET_ID, PROJECT_ID)
            if pem_bytes:
                # Convert PEM -> PKCS#8 DER bytes
                key = serialization.load_pem_private_key(
                    pem_bytes, password=None, backend=default_backend()
                )
                pk_bytes = key.private_bytes(
                    encoding=serialization.Encoding.DER,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
                st.session_state["_sf_private_key_bytes"] = pk_bytes
        except Exception:
            pass  # Fall through to other methods

    if pk_bytes and params:
        conn = _connect_snowflake(
            user=params["user"],
            account=params["account"],
            warehouse=params["warehouse"],
            database=params["database"],
            schema=params["schema"],
            role=params.get("role"),
            private_key=pk_bytes,
        )
        st.session_state["sf_conn"] = conn
        st.session_state["sf_connected"] = True
        return conn

    # fall back to env/Secret Manager (old behavior), if you still keep it
    envp = _sf_params_from_env()
    if envp:
        conn = _connect_snowflake(**envp)
        st.session_state["sf_conn"] = conn
        st.session_state["sf_connected"] = True
        # don't store private bytes in sf_params
        st.session_state["sf_params"] = {
            k: v for k, v in envp.items() if k != "private_key"
        }
        return conn

    raise RuntimeError("No Snowflake params. Use the UI to connect.")


# Pick ONE of these depending on your stack:
USE_CONNECTOR = True  # set False if you use Snowpark Session

if USE_CONNECTOR:
    import snowflake.connector as sf
else:
    # Avoid hard dependency on Snowpark when not used; import lazily only if you switch.
    Session = None  # placeholder; do `from snowflake.snowpark import Session` at call site if needed

KEEPALIVE_SECONDS = 10 * 60  # ping every 10 min of inactivity


def _fetch_private_key_from_secret(project_id: str, secret_id: str) -> str:
    """Fetch RSA private key from Secret Manager at runtime."""
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
    try:
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")  # PEM string
    except Exception as e:
        logger.error(f"Failed to fetch SF private key: {e}")
        raise ValueError("SF private key fetch failed—check IAM")


def _load_private_key(private_key_str: str) -> bytes:
    """Load private key from PEM string."""
    if not private_key_str:
        raise ValueError("Private key not configured")
    # Decode if base64 (if stored that way; adjust if plain PEM)
    if "\n" not in private_key_str:
        private_key_str = base64.b64decode(private_key_str).decode("utf-8")
    return serialization.load_pem_private_key(
        private_key_str.encode("utf-8"),
        password=None,  # No passphrase
    )  # type: ignore


# @st.cache_resource(show_spinner=False)
def get_snowflake_connection(**kwargs):
    """Prefer key-pair auth via Secret Manager; fall back to password only if explicitly present."""
    project_id = kwargs.pop("project_id", PROJECT_ID)
    secret_id = kwargs.pop(
        "sf_private_key_secret",
        os.getenv("SF_PRIVATE_KEY_SECRET", "sf-private-key"),
    )

    # Key-pair path (preferred)
    if secret_id:
        try:
            pk_bytes = _load_private_key_bytes_from_gsm(
                secret_id
            )  # DER PKCS#8 bytes
            st.write("Loaded key bytes:", len(pk_bytes))
            return _connect_snowflake(
                user=kwargs["user"],
                account=kwargs["account"],
                warehouse=kwargs["warehouse"],
                database=kwargs["database"],
                schema=kwargs["schema"],
                role=kwargs.get("role"),
                private_key=pk_bytes,
            )
        except Exception as e:
            # Optional: keep this if you want a visible hint in the UI
            st.warning(
                f"Key-pair auth failed ({e}). Falling back to password if provided."
            )

    # Optional password fallback (only if SF_PASSWORD env is defined, otherwise just raise)
    p = os.getenv("SF_PASSWORD")
    if p:
        return _connect_snowflake(
            user=kwargs["user"],
            account=kwargs["account"],
            warehouse=kwargs["warehouse"],
            database=kwargs["database"],
            schema=kwargs["schema"],
            role=kwargs.get("role"),
            password=p,
        )

    raise RuntimeError(
        "Could not establish Snowflake connection (key-pair failed and no password fallback)."
    )


def keepalive_ping(conn):
    """
    Run a lightweight query if we've been idle for a bit.
    Avoids extra chatter yet prevents idle timeouts.
    """
    now = time.time()
    last = st.session_state.get("_sf_last_ping", 0)
    if now - last < KEEPALIVE_SECONDS:
        return

    try:
        if USE_CONNECTOR:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchall()
        else:
            # Snowpark
            conn.sql("SELECT 1").collect()
        st.session_state["_sf_last_ping"] = now
    except Exception:
        # Attempt a transparent reconnect once
        st.session_state.pop("_sf_last_ping", None)
        # Rebuild connection with the SAME params used originally
        # If you pass kwargs into get_snowflake_connection, capture them in session_state at login.
        params = st.session_state.get("_sf_conn_kwargs", {})
        new_conn = get_snowflake_connection(**params)
        # one immediate ping
        try:
            if USE_CONNECTOR:
                with new_conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchall()
            else:
                new_conn.sql("SELECT 1").collect()  # type: ignore
            st.session_state["_sf_last_ping"] = time.time()
        except Exception as e:
            # Surface a concise error while letting the page continue rendering
            st.error(f"Snowflake connection could not be restored: {e}")
            raise


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


# app_shared.py

import re


def _norm_blob_path(p: str) -> str:
    # remove any leading slashes and collapse multiple slashes
    p = (p or "").strip()
    p = re.sub(r"^/+", "", p)  # strip leading /
    p = re.sub(r"/{2,}", "/", p)  # collapse // -> /
    return p


def upload_to_gcs(bucket_name: str, local_path: str, dest_blob: str) -> str:
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob_path = _norm_blob_path(dest_blob)  # <-- normalize here
    blob = bucket.blob(blob_path)
    blob.upload_from_filename(local_path)
    return f"gs://{bucket_name}/{blob_path}"


def _get_job_history_object() -> str:
    return os.getenv("JOBS_JOB_HISTORY_OBJECT", "robyn-jobs/job_history.csv")


def read_job_history_from_gcs(bucket_name: str) -> pd.DataFrame:
    from google.cloud import storage

    client = storage.Client()
    blob = client.bucket(bucket_name).blob("robyn-jobs/job_history.csv")
    if not blob.exists():
        return _empty_job_history_df()  # your function with JOB_HISTORY_COLUMNS

    raw = blob.download_as_bytes()
    if not raw:
        return _empty_job_history_df()

    df = pd.read_csv(io.BytesIO(raw))
    if df is None or df.empty:
        return _empty_job_history_df()

    # (optional) normalize columns/order here if you want
    return normalize_job_history_df(df)


def save_job_history_to_gcs(df, bucket_name: str):
    import io

    from google.cloud import storage

    df = normalize_job_history_df(df)
    b = io.BytesIO()
    df.to_csv(b, index=False)
    b.seek(0)
    client = storage.Client()
    blob = client.bucket(bucket_name).blob("robyn-jobs/job_history.csv")
    blob.upload_from_file(b, content_type="text/csv")
    return True


def append_row_to_job_history(row_dict: dict, bucket_name: str):
    import pandas as pd

    # Ensure all expected keys exist
    for c in JOB_HISTORY_COLUMNS:
        row_dict.setdefault(c, pd.NA)

    # If exec_name is missing but we have execution_name, derive it
    if (not row_dict.get("exec_name")) and row_dict.get("execution_name"):
        row_dict["exec_name"] = _short_exec_name(
            str(row_dict["execution_name"])
        )

    # New row (normalized)
    df_new = normalize_job_history_df(pd.DataFrame([row_dict]))

    # Existing job_history (may be empty)
    df_old = read_job_history_from_gcs(bucket_name)
    if df_old is None or df_old.empty:
        return save_job_history_to_gcs(df_new, bucket_name)

    # Merge by job_id: fill missing values in existing row with new values (combine_first)
    df_old = df_old.set_index("job_id")
    df_new = df_new.set_index("job_id")
    for jid, s in df_new.iterrows():
        if jid in df_old.index:
            df_old.loc[jid] = df_old.loc[jid].combine_first(s)  # type: ignore
        else:
            df_old.loc[jid] = s  # type: ignore

    df_merged = df_old.reset_index()
    df_merged = normalize_job_history_df(df_merged)
    return save_job_history_to_gcs(df_merged, bucket_name)


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

# app_shared.py


def _normalize_gs_uri(uri: str) -> str:
    import re

    s = (uri or "").strip()
    if not s.startswith("gs://"):
        return s
    # split into bucket + object and normalize the object part
    s2 = s[5:]
    if "/" not in s2:
        return s  # bucket only
    bucket, obj = s2.split("/", 1)
    obj = re.sub(r"^/+", "", obj)
    obj = re.sub(r"/{2,}", "/", obj)
    return f"gs://{bucket}/{obj}"


@st.cache_resource
def build_job_config_from_params(
    params: dict,
    data_gcs_path: str,
    timestamp: str,
    annotations_gcs_path: Optional[str],
) -> dict:
    config = {
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
        "start_date": params.get("start_date", "2024-01-01"),  # NEW
        "end_date": params.get("end_date", time.strftime("%Y-%m-%d")),  # NEW
        "gcs_bucket": params.get("gcs_bucket")
        or st.session_state["gcs_bucket"],
        "data_gcs_path": _normalize_gs_uri(data_gcs_path),
        "annotations_gcs_path": _normalize_gs_uri(annotations_gcs_path),  # type: ignore
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
        "dep_var": str(params.get("dep_var", "UPLOAD_VALUE")),  # NEW
        "dep_var_type": str(params.get("dep_var_type", "revenue")),  # NEW
        "date_var": str(params.get("date_var", "date")),  # NEW
        "adstock": str(params.get("adstock", "geometric")),  # NEW
        "hyperparameter_preset": str(
            params.get("hyperparameter_preset", "Meshed recommend")
        ),  # NEW
        "resample_freq": str(
            params.get("resample_freq", "none")
        ),  # Resampling frequency
        "use_parquet": True,
        "parallel_processing": True,
        "max_cores": 8,
    }

    # Add custom_hyperparameters if present
    if "custom_hyperparameters" in params and params["custom_hyperparameters"]:
        config["custom_hyperparameters"] = params["custom_hyperparameters"]

    # Add column_agg_strategies if present (for resampling)
    if "column_agg_strategies" in params and params["column_agg_strategies"]:
        config["column_agg_strategies"] = params["column_agg_strategies"]

    return config


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
    launcher: Optional[callable] = None,  # type: ignore
) -> dict:
    return _safe_tick_once(queue_name, bucket_name, launcher)


# ─────────────────────────────
# Stateless queue tick endpoint (AFTER defs/constants)
# ─────────────────────────────


def handle_queue_tick_from_query_params(
    query_params: Dict[str, Any],
    bucket_name: Optional[str] = None,
    launcher: Optional[callable] = None,  # type: ignore
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
# Resampling helpers
# ─────────────────────────────


def _normalize_resample_freq(freq: str) -> str:
    f = (freq or "").strip().lower()
    if f in ("none", "", "no"):
        return "none"
    if f.startswith("w"):
        return "W"  # weekly
    if f.startswith("m"):
        return "M"  # monthly
    return "none"


def _normalize_resample_agg(agg: str) -> str:
    a = (agg or "").strip().lower()
    return {
        "sum": "sum",
        "mean": "mean",
        "avg": "mean",
        "max": "max",
        "min": "min",
    }.get(a, "sum")


def _is_bool_like(series: pd.Series) -> bool:
    """Detect 0/1-like numeric columns robustly."""
    s = series.dropna()
    if s.empty:
        return False
    try:
        u = pd.unique(s.astype(float).round(0))
        return set(map(float, u)).issubset({0.0, 1.0})
    except Exception:
        return False


def require_login_and_domain(allowed_domain: str = "mesheddata.com") -> None:
    """
    Hard-stops the current Streamlit run unless the user is logged in
    with a Google account from the allowed domain.
    Call this at the very top of *every* page.
    """
    # Allow lightweight health checks to pass through if you use them on pages too
    q = getattr(st, "query_params", {})
    if q.get("health") == "true":
        return  # let the page handle its health endpoint and st.stop() later if needed

    is_logged_in = getattr(st.user, "is_logged_in", False)  # type: ignore
    if not is_logged_in:
        st.set_page_config(page_title="Sign in", layout="centered")
        st.title("Robyn MMM")
        st.write(
            f"Sign in with your {allowed_domain} Google account to continue."
        )
        if st.button("Sign in with Google"):
            st.login()  # type: ignore # flat [auth] config → no provider arg
        st.stop()

    email = (getattr(st.user, "email", "") or "").lower().strip()  # type: ignore
    if not email.endswith(f"@{allowed_domain}"):
        st.set_page_config(page_title="Access restricted", layout="centered")
        st.error(f"This app is restricted to @{allowed_domain} accounts.")
        if st.button("Sign out"):
            st.logout()  # type: ignore # flat [auth] config → no provider arg
        st.stop()


def _maybe_resample_df(
    df: pd.DataFrame,
    date_col: str | None,  # type: ignore
    freq: str,  # 'none' | 'W' | 'M'
    agg: str,  # 'sum' | 'mean' | 'max' | 'min'
    cat_strategy: str = "auto",  # 'auto' | 'mean' | 'sum' | 'max' | 'mode'
    topk_for_nominal: int = 8,
) -> pd.DataFrame:
    """
    - Numeric non-binary: aggregated with `agg`.
    - Binary 0/1 numeric: aggregated using `cat_strategy` (default 'mean' → share).
    - Nominal categoricals (object/category): one-hot per level (Top-K), then
      aggregate one-hot with:
        auto/mean → mean (share) | sum → count | max → any
      If cat_strategy == 'mode', add an extra '{col}_mode' label column
      and still use mean on the one-hots (shares) for numeric modeling.
    - Ordinal categoricals (ordered pd.Categorical): average of codes ('*_mean_code').
    """
    freq = _normalize_resample_freq(freq)
    agg = _normalize_resample_agg(agg)
    if freq == "none" or not date_col or date_col not in df.columns:
        return df

    data = df.copy()
    data[date_col] = pd.to_datetime(data[date_col], errors="coerce")
    data = data.dropna(subset=[date_col])
    if data.empty:
        return df

    rule = "W" if freq == "W" else "M"

    # Split columns
    num_cols = data.select_dtypes(include="number").columns.tolist()
    bool_like = [c for c in num_cols if _is_bool_like(data[c])]
    num_non_bool = [c for c in num_cols if c not in bool_like]

    cat_cols = data.select_dtypes(
        include=["object", "category", "bool"]
    ).columns.tolist()
    cat_cols = [c for c in cat_cols if c != date_col]

    # 1) Numeric, non-binary via global numeric aggregator
    res_parts = []
    if num_non_bool:
        res_num = data.set_index(date_col)[num_non_bool].resample(rule).agg(agg)
        res_parts.append(res_num)

    # 2) Binary (0/1) numeric via cat_strategy (overwrites original column names)
    if bool_like:
        if cat_strategy in ("auto", "mean"):
            res_bool = data.set_index(date_col)[bool_like].resample(rule).mean()
        elif cat_strategy == "sum":
            res_bool = data.set_index(date_col)[bool_like].resample(rule).sum()
        elif cat_strategy == "max":
            res_bool = data.set_index(date_col)[bool_like].resample(rule).max()
        elif cat_strategy == "mode":
            # Majority value per period (0/1). Keep it numeric.
            res_bool = (
                data.set_index(date_col)[bool_like]
                .resample(rule)
                .apply(
                    lambda s: (
                        s.dropna().astype(float).round(0).mode().iloc[0]
                        if len(s.dropna())
                        else np.nan
                    )
                )
            )
        else:
            res_bool = data.set_index(date_col)[bool_like].resample(rule).mean()
        res_parts.append(res_bool)

    # 3) Categorical columns
    for c in cat_cols:
        s = data[[date_col, c]].dropna()
        if s.empty:
            continue

        # Ordinal category → mean code
        if str(s[c].dtype).startswith("category") and getattr(
            s[c].dtype, "ordered", False
        ):
            codes = s.copy()
            codes[c] = s[c].cat.codes.replace(-1, np.nan)
            res_ord = (
                codes.set_index(date_col)[c]
                .resample(rule)
                .mean()
                .to_frame(name=f"{c}_mean_code")
            )
            res_parts.append(res_ord)
        else:
            # Nominal → one-hot Top-K + aggregate
            top_levels = (
                s[c].value_counts().head(topk_for_nominal).index.tolist()
            )
            oh = pd.get_dummies(s[c], prefix=c)
            keep_cols = [
                col for col in oh.columns if col.split("_", 1)[-1] in top_levels
            ]
            if keep_cols:
                oh_df = pd.concat(
                    [
                        s[[date_col]].reset_index(drop=True),
                        oh[keep_cols].reset_index(drop=True),
                    ],
                    axis=1,
                )
                # Decide aggregation for one-hot
                oh_agg = {
                    "auto": "mean",
                    "mean": "mean",
                    "sum": "sum",
                    "max": "max",
                    "mode": "mean",  # still produce shares for modeling
                }.get(cat_strategy, "mean")
                res_oh = (
                    oh_df.set_index(date_col)[keep_cols]
                    .resample(rule)
                    .agg(oh_agg)
                )
                res_parts.append(res_oh)

            # If user explicitly chose 'mode', also emit a label column
            if cat_strategy == "mode":
                res_mode = (
                    s.set_index(date_col)[c]
                    .resample(rule)
                    .agg(lambda x: x.mode().iloc[0] if len(x) else np.nan)
                    .to_frame(name=f"{c}_mode")
                )  # type: ignore
                res_parts.append(res_mode)

    # Merge all parts
    if not res_parts:
        return df

    res = res_parts[0]
    for p in res_parts[1:]:
        res = res.join(p, how="outer")

    out = res.reset_index()
    if out.columns[0] != date_col:
        out.rename(columns={out.columns[0]: date_col}, inplace=True)
    return out


def _require_sf_session():
    if not (
        st.session_state.get("sf_connected")
        and st.session_state.get("_sf_private_key_bytes")
    ):
        st.error(
            "Please connect to Snowflake in Tab 1 and provide a private key."
        )
        st.stop()


## FE additions below

# =========================
# Date Parser (unchanged)
# =========================
def parse_date(df: pd.DataFrame, meta: dict) -> Tuple[pd.DataFrame, str]:
    wanted = str(meta.get("data", {}).get("date_field") or "DATE")
    cols = list(map(str, df.columns))
    if wanted in cols:
        date_col = wanted
    else:
        lower_map = {c.lower(): c for c in cols}
        date_col = lower_map.get(wanted.lower(), wanted)

    if date_col in df.columns:
        # Works for tz-aware and tz-naive inputs
        s = pd.to_datetime(
            df[date_col], errors="coerce", utc=True
        ).dt.tz_convert(None)
        df[date_col] = s
        df = df.sort_values(date_col).reset_index(drop=True)
    return df, date_col


# =========================
# GCS: paths, listing, download
# =========================
def data_root(country: str) -> str:
    return f"datasets/{country.lower().strip()}"


def data_blob(country: str, ts: str) -> str:
    return f"{data_root(country)}/{ts}/raw.parquet"


def data_latest_blob(country: str) -> str:
    return f"{data_root(country)}/latest/raw.parquet"

# --- metadata paths (country + universal)
def meta_blob(country: str, ts: str) -> str:
    """Country-scoped metadata path."""
    return f"metadata/{country.lower().strip()}/{ts}/mapping.json"

def meta_blob_universal(ts: str) -> str:
    """Universal metadata path."""
    return f"metadata/universal/{ts}/mapping.json"

def meta_latest_blob(country: str) -> str:
    """Country-scoped 'latest' pointer."""
    return f"metadata/{country.lower().strip()}/latest/mapping.json"

def meta_latest_blob_universal() -> str:
    """Universal 'latest' pointer."""
    return "metadata/universal/latest/mapping.json"

# --- small helper
def _blob_exists(bucket: str, blob_path: str) -> bool:
    client = storage.Client()
    blob = client.bucket(bucket).blob(blob_path)
    return blob.exists()

@st.cache_data(show_spinner=False)
def sorted_versions_newest_first(ts_list: List[str]) -> List[str]:
    cleaned = [str(t).strip() for t in ts_list if str(t).strip()]
    cleaned = [t for t in cleaned if t.lower() != "latest"]
    # numeric?
    try:
        nums = [int(t) for t in cleaned]
        return [t for _, t in sorted(zip(nums, cleaned), reverse=True)]
    except Exception:
        pass
    # datetime-like?
    try:
        from dateutil import parser

        parsed = [parser.parse(t) for t in cleaned]
        return [t for _, t in sorted(zip(parsed, cleaned), reverse=True)]
    except Exception:
        pass
    # fallback
    return sorted(cleaned, reverse=True)


@st.cache_data(show_spinner=False)
def list_data_versions(
    bucket: str, country: str, refresh_key: str = ""
) -> List[str]:
    client = storage.Client()
    prefix = f"{data_root(country)}/"
    blobs = client.list_blobs(bucket, prefix=prefix)
    ts = set()
    for b in blobs:
        parts = b.name.split("/")
        if len(parts) >= 4 and parts[-1] == "raw.parquet":
            ts.add(parts[-2])
    out = sorted_versions_newest_first(list(ts))
    return ["Latest"] + out


@st.cache_data(show_spinner=False)
def list_meta_versions(bucket: str, country: str, refresh_key: str = "") -> List[str]:
    """
    Union of country-scoped and universal metadata versions.
    If a version exists in both, we still show one entry (the version string).
    """
    client = storage.Client()
    country_prefix = f"metadata/{country.lower().strip()}/"
    universal_prefix = "metadata/universal/"

    ts = set()

    # country-scoped
    for b in client.list_blobs(bucket, prefix=country_prefix):
        parts = b.name.split("/")
        if len(parts) >= 4 and parts[-1] == "mapping.json":
            ts.add(parts[-2])

    # universal
    for b in client.list_blobs(bucket, prefix=universal_prefix):
        parts = b.name.split("/")
        if len(parts) >= 4 and parts[-1] == "mapping.json":
            ts.add(parts[-2])

    out = sorted_versions_newest_first(list(ts))
    return ["Latest"] + out


def _download_parquet_from_gcs(bucket: str, blob_path: str) -> pd.DataFrame:
    client = storage.Client()
    blob = client.bucket(bucket).blob(blob_path)
    if not blob.exists():
        raise FileNotFoundError(f"gs://{bucket}/{blob_path} not found")
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
        blob.download_to_filename(tmp.name)
        return pd.read_parquet(tmp.name)


def _download_json_from_gcs(bucket: str, blob_path: str) -> dict:
    client = storage.Client()
    blob = client.bucket(bucket).blob(blob_path)
    if not blob.exists():
        raise FileNotFoundError(f"gs://{bucket}/{blob_path} not found")
    return json.loads(blob.download_as_bytes())


@st.cache_data(show_spinner=False)
def download_parquet_from_gcs_cached(
    bucket: str, blob_path: str
) -> pd.DataFrame:
    return _download_parquet_from_gcs(bucket, blob_path)


@st.cache_data(show_spinner=False)
def download_json_from_gcs_cached(bucket: str, blob_path: str) -> dict:
    return _download_json_from_gcs(bucket, blob_path)


@st.cache_data(show_spinner=False)
def load_data_from_gcs(bucket: str, country: str, data_ts: str, meta_ts: str) -> Tuple[pd.DataFrame, dict, str]:
    """
    Data: unchanged (country only).
    Meta: try country first; if missing, fallback to universal.
          For 'Latest', prefer country latest if exists, else universal latest.
    """
    # data
    db = data_latest_blob(country) if data_ts == "Latest" else data_blob(country, str(data_ts))
    df = _download_parquet_from_gcs(bucket, db)

    # meta resolution (country → universal fallback)
    if meta_ts == "Latest":
        mb_country = meta_latest_blob(country)
        mb_universal = meta_latest_blob_universal()
        if _blob_exists(bucket, mb_country):
            mb = mb_country
        elif _blob_exists(bucket, mb_universal):
            mb = mb_universal
        else:
            # As a last resort, scan versions and pick newest across both scopes
            # (keeps behavior robust if 'latest/' symlink wasn't created)
            versions = list_meta_versions(bucket, country)
            # versions includes "Latest" + sorted versions
            if len(versions) > 1:
                chosen = versions[1]  # newest explicit ts
                mb = meta_blob(country, chosen) if _blob_exists(bucket, meta_blob(country, chosen)) else meta_blob_universal(chosen)
            else:
                raise FileNotFoundError("No metadata mapping.json found in country or universal scope.")
    else:
        # explicit ts: prefer country path; fallback to universal
        ts_str = str(meta_ts)
        mb_country = meta_blob(country, ts_str)
        if _blob_exists(bucket, mb_country):
            mb = mb_country
        else:
            mb_universal = meta_blob_universal(ts_str)
            if _blob_exists(bucket, mb_universal):
                mb = mb_universal
            else:
                raise FileNotFoundError(
                    f"Metadata not found for ts='{ts_str}' in either "
                    f"gs://{bucket}/{meta_blob(country, ts_str)} or gs://{bucket}/{mb_universal}"
                )

    meta = _download_json_from_gcs(bucket, mb)
    df, date_col = parse_date(df, meta)
    return df, meta, date_col


# =========================
# KPI tiles
# =========================
GREEN = "#2ca02c"
RED = "#d62728"
GREY = "#777"
BORDER = "#eee"


def kpi_box(
    title: str, value: str, delta: str | None = None, good_when: str = "up"
):
    color_pos, color_neg, color_neu = "#2e7d32", "#a94442", GREY
    delta_color = ""
    if delta:
        is_up = delta.strip().startswith("+")
        if good_when == "up":
            delta_color = color_pos if is_up else color_neg
        elif good_when == "down":
            delta_color = color_pos if not is_up else color_neg
        else:
            delta_color = color_neu
    st.markdown(
        f"""
        <div style="border:1px solid {BORDER};border-radius:10px;padding:12px;">
          <div style="font-size:12px;color:{GREY};">{title}</div>
          <div style="font-size:24px;font-weight:700;">{value}</div>
          <div style="font-size:12px;color:{delta_color};">{delta or ""}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def kpi_grid(boxes: list, per_row: int = 3):
    if not boxes:
        return
    for i in range(0, len(boxes), per_row):
        row = boxes[i : i + per_row]
        cols = st.columns(len(row))
        for c, b in zip(cols, row):
            with c:
                kpi_box(
                    b.get("title", ""),
                    b.get("value", "–"),
                    b.get("delta"),
                    b.get("good_when", "up"),
                )


def kpi_grid_fixed(boxes: list, per_row: int = 3):
    if not boxes:
        return
    rem = len(boxes) % per_row
    if rem:
        boxes = boxes + [dict(title="", value="")] * (per_row - rem)
    for i in range(0, len(boxes), per_row):
        row = boxes[i : i + per_row]
        cols = st.columns(per_row)
        for c, b in zip(cols, row):
            with c:
                if b.get("title") == "" and b.get("value") == "":
                    st.markdown(
                        '<div style="height:0.01px;"></div>',
                        unsafe_allow_html=True,
                    )
                else:
                    kpi_box(
                        b.get("title", ""),
                        b.get("value", "–"),
                        b.get("delta"),
                        b.get("good_when", "up"),
                    )


# =========================
# Formatting & small utils
# =========================


def pretty(s: str) -> str:
    if s is None:
        return "–"
    return s if s.isupper() else s.replace("_", " ").title()


def fmt_num(x, nd=2):
    if pd.isna(x):
        return "–"
    a = abs(x)
    if a >= 1e9:
        return f"{x/1e9:.{nd}f}B"
    if a >= 1e6:
        return f"{x/1e6:.{nd}f}M"
    if a >= 1e3:
        return f"{x/1e3:.{nd}f}k"
    return f"{x:.0f}"


def freq_to_rule(freq: str):
    return {"D": "D", "W": "W-MON", "M": "MS", "Q": "QS-DEC", "YE": "YS"}[freq]


def period_label(series: pd.Series, freq_code: str) -> pd.Series:
    dt = pd.to_datetime(series)
    if freq_code == "YE":
        return dt.dt.year.astype(str)
    if freq_code == "Q":
        q = ((dt.dt.month - 1) // 3 + 1).astype(str)
        return dt.dt.year.astype(str) + " Q" + q
    if freq_code == "M":
        return dt.dt.strftime("%b %Y")
    if freq_code in ("W-MON", "W"):
        return dt.dt.strftime("W%U %Y")
    return dt.dt.strftime("%Y-%m-%d")


def safe_eff(frame: pd.DataFrame, tgt: str):
    if frame is None or frame.empty or "_TOTAL_SPEND" not in frame:
        return np.nan
    s = frame["_TOTAL_SPEND"].sum()
    v = frame[tgt].sum() if tgt in frame else np.nan
    return (v / s) if s > 0 else np.nan


# =========================
# Platform colors + mapping
# =========================

BASE_PLATFORM_COLORS = {
    "GA": "#1f77b4",
    "META": "#e377c2",
    "BING": "#2ca02c",
    "TV": "#ff7f0e",
    "PARTNERSHIP": "#17becf",
    "OTHER": "#7f7f7f",
}
QUAL_PALETTE = (
    px.colors.qualitative.D3
    + px.colors.qualitative.Bold
    + px.colors.qualitative.Safe
    + px.colors.qualitative.Set2
)


def build_platform_colors(platforms: list):
    cmap = {}
    for p in platforms:
        p_u = str(p).upper()
        for k, col in BASE_PLATFORM_COLORS.items():
            if k in p_u:
                cmap[p] = col
                break
    i = 0
    for p in platforms:
        if p not in cmap:
            cmap[p] = QUAL_PALETTE[i % len(QUAL_PALETTE)]
            i += 1
    return cmap


def build_plat_map_df(
    present_spend: List[str],
    df: pd.DataFrame,
    meta: dict,
    m: pd.DataFrame,
    COL: str,
    PLAT: str,
    CHANNELS_MAP: Dict[str, str],
) -> Tuple[pd.DataFrame, list, dict]:
    plat_map_df = pd.DataFrame(columns=["col", "platform"])
    if present_spend:
        pm_json = (
            meta.get("platform_map", {}) if isinstance(meta, dict) else {}
        ) or {}
        rows = [(c, str(pm_json[c])) for c in present_spend if pm_json.get(c)]
        if rows:
            plat_map_df = pd.DataFrame(rows, columns=["col", "platform"])

        if plat_map_df.empty and (PLAT in m.columns) and (COL in m.columns):
            pm = m.loc[m[COL].isin(present_spend), [COL, PLAT]].dropna()
            if not pm.empty:
                plat_map_df = pm.rename(
                    columns={COL: "col", PLAT: "platform"}
                ).copy()

        if plat_map_df.empty:
            derived = []
            for c in present_spend:
                m0 = re.match(r"([A-Za-z0-9]+)_", c)
                plat = m0.group(1).upper() if m0 else "OTHER"
                derived.append((c, plat))
            plat_map_df = pd.DataFrame(derived, columns=["col", "platform"])

    if not plat_map_df.empty and CHANNELS_MAP:
        _norm = {str(k).upper(): str(v) for k, v in CHANNELS_MAP.items()}
        plat_map_df["platform"] = (
            plat_map_df["platform"]
            .astype(str)
            .map(lambda x: _norm.get(x.upper(), x))
        )

    platforms = (
        plat_map_df["platform"].dropna().astype(str).unique().tolist()
        if not plat_map_df.empty
        else []
    )
    palette = build_platform_colors(platforms)
    return plat_map_df, platforms, palette


# =========================
# Meta & feature helpers
# =========================


def build_meta_views(meta: dict, df: pd.DataFrame):
    """
    Returns:
      display_map, nice_fn, goal_cols, mapping_dict, m_df, ALL_COLS_UP,
      IMPR_COLS, CLICK_COLS, SESSION_COLS, INSTALL_COLS
    """
    display_map = meta.get("display_name_map", {}) or {}

    def nice(colname: str) -> str:
        alias = (display_map.get(colname) or "").strip()
        return alias if alias else pretty(colname)

    mapping = meta.get("mapping", {}) if isinstance(meta, dict) else {}
    mapping = mapping or {}
    goal_vars = [
        g.get("var")
        for g in (meta.get("goals", []) if isinstance(meta, dict) else [])
        if g.get("var")
    ]
    goal_cols = [c for c in goal_vars if c in df.columns]

    # Compatibility frame "m"
    rows = []
    bucket_to_cat = {
        "paid_media_spends": "paid_media_spends",
        "paid_media_vars": "paid_media_vars",
        "organic_vars": "organic_vars",
        "context_vars": "context_vars",
    }
    for k, vals in (mapping or {}).items():
        cat = bucket_to_cat.get(k)
        if not cat:
            continue
        for v in vals or []:
            rows.append(dict(column_name=str(v), main_category=cat))

    for g in meta.get("goals") or []:
        v = g.get("var")
        if not v:
            continue
        grp = (g.get("group") or "").strip().lower()
        cat = (
            "goal"
            if grp in ("primary", "main", "goal", "")
            else "secondary_goal"
        )
        rows.append(dict(column_name=str(v), main_category=cat))

    platform_map = (
        meta.get("platform_map", {}) if isinstance(meta, dict) else {}
    )
    platform_map = platform_map or {}

    seen, rows_dedup = set(), []
    for r in rows:
        key = str(r["column_name"])
        if key in seen:
            continue
        seen.add(key)
        r["platform"] = platform_map.get(key)
        r["display_name"] = display_map.get(key)
        rows_dedup.append(r)

    m = pd.DataFrame(
        rows_dedup,
        columns=["column_name", "main_category", "platform", "display_name"],
    )
    m.columns = [c.strip().lower() for c in m.columns]

    ALL_COLS_UP = {c: str(c).upper() for c in df.columns}

    def cols_like(keyword: str):
        kw = keyword.upper()
        return [c for c, u in ALL_COLS_UP.items() if kw in u]

    IMPR_COLS = cols_like("IMPRESSION")
    CLICK_COLS = cols_like("CLICK")
    SESSION_COLS = cols_like("SESSION")
    INSTALL_COLS = [c for c in cols_like("INSTALL") + cols_like("APP_INSTALL")]

    return (
        display_map,
        nice,
        goal_cols,
        mapping,
        m,
        ALL_COLS_UP,
        IMPR_COLS,
        CLICK_COLS,
        SESSION_COLS,
        INSTALL_COLS,
    )


# =========================
# Sidebar builder
# =========================


def render_sidebar(meta: dict, df: pd.DataFrame, nice, goal_cols: List[str]):
    # Countries
    if "COUNTRY" in df.columns:
        country_list = sorted(
            df["COUNTRY"].dropna().astype(str).unique().tolist()
        )
        default_countries = country_list or []
        sel_countries = st.sidebar.multiselect("Country", country_list, default=default_countries)
    else:
        sel_countries = []
        st.sidebar.caption("Dataset has no COUNTRY column — showing all rows.")

    # Goals
    if not goal_cols:
        st.sidebar.error("No goals found in metadata.")
        GOAL = None
    else:
        group_fallback = {
            g.get("var"): (g.get("group") or "primary")
            for g in (meta.get("goals") or [])
            if g.get("var")
        }

        def _goal_tag_for(col: str) -> str:
            g = (group_fallback.get(col, "") or "").strip().lower()
            return (
                "Secondary"
                if g in ("secondary", "alt", "secondary_goal")
                else "Main"
            )

        items = []
        for col in goal_cols:
            tag = _goal_tag_for(col)
            base = (nice(col) or str(col)).strip() or str(col)
            items.append((f"{base} · {tag}", col, tag, base.lower()))

        from collections import Counter

        counts = Counter([lbl for (lbl, _, _, _) in items])
        fixed = []
        for lbl, col, tag, base_lower in items:
            if counts[lbl] > 1:
                base_name = lbl.split(" · ")[0]
                lbl = f"{base_name} [{col}] · {tag}"
            fixed.append((lbl, col, tag, base_lower))
        fixed.sort(key=lambda x: (0 if x[2] == "Main" else 1, x[3]))

        labels = [lbl for (lbl, _, _, _) in fixed]
        label_to_col = {lbl: col for (lbl, col, _, _) in fixed}

        dep_var = (meta.get("dep_var") or "").strip()
        default_col = (
            dep_var
            if dep_var in goal_cols
            else ("GMV" if "GMV" in goal_cols else goal_cols[0])
        )
        default_label = next(
            (l for l, c in label_to_col.items() if c == default_col), labels[0]
        )
        GOAL = label_to_col[
            st.sidebar.selectbox(
                "Goal", labels, index=labels.index(default_label)
            )
        ]

    # Timeframe
    tf_label_map = {
        "LAST 6 MONTHS": "6m",
        "LAST 12 MONTHS": "12m",
        "CURRENT YEAR": "cy",
        "LAST YEAR": "ly",
        "LAST 2 YEARS": "2y",
        "ALL": "all",
    }
    TIMEFRAME_LABEL = st.sidebar.selectbox(
        "Timeframe", list(tf_label_map.keys()), index=0
    )
    RANGE = tf_label_map[TIMEFRAME_LABEL]

    # Aggregation
    agg_map = {
        "Daily": "D",
        "Weekly": "W",
        "Monthly": "M",
        "Quarterly": "Q",
        "Yearly": "YE",
    }
    agg_label = st.sidebar.selectbox(
        "Aggregation", list(agg_map.keys()), index=2
    )
    FREQ = agg_map[agg_label]

    return GOAL, sel_countries, TIMEFRAME_LABEL, RANGE, agg_label, FREQ


# =========================
# Time filters / resampling
# =========================


def filter_range(df: pd.DataFrame, date_col: str, RANGE: str) -> pd.DataFrame:
    if df.empty:
        return df
    date_max = df[date_col].max()
    if RANGE == "all":
        return df
    if RANGE == "2y":
        return df[df[date_col] >= (date_max - pd.DateOffset(years=2))]
    if RANGE == "ly":
        today = pd.Timestamp.today().normalize()
        start = pd.Timestamp(year=today.year - 1, month=1, day=1)
        end = pd.Timestamp(
            year=today.year - 1, month=12, day=31, hour=23, minute=59, second=59
        )
        return df[(df[date_col] >= start) & (df[date_col] <= end)]
    if RANGE == "12m":
        today = pd.Timestamp.today().normalize()
        start_of_this_month = pd.Timestamp(
            year=today.year, month=today.month, day=1
        )
        start = start_of_this_month - pd.DateOffset(months=11)
        return df[df[date_col] >= start]
    if RANGE == "cy":
        start = pd.Timestamp(year=pd.Timestamp.today().year, month=1, day=1)
        return df[df[date_col] >= start]
    if RANGE == "6m":
        today = pd.Timestamp.today().normalize()
        start_of_this_month = pd.Timestamp(
            year=today.year, month=today.month, day=1
        )
        start = start_of_this_month - pd.DateOffset(months=5)
        return df[df[date_col] >= start]
    # legacy 1y:
    if RANGE == "1y":
        return df[df[date_col] >= (date_max - pd.DateOffset(years=1))]
    return df


def previous_window(
    full_df: pd.DataFrame, current_df: pd.DataFrame, date_col: str, RANGE: str
) -> pd.DataFrame:
    if current_df.empty:
        return full_df.iloc[0:0]
    cur_start, cur_end = current_df[date_col].min(), current_df[date_col].max()
    span = cur_end - cur_start
    if RANGE == "cy":
        this_year = pd.Timestamp.today().year
        start_prev = pd.Timestamp(year=this_year - 1, month=1, day=1)
        same_day_prev_year = pd.Timestamp(
            year=this_year - 1, month=cur_end.month, day=cur_end.day
        )
        end_prev = min(same_day_prev_year, full_df[date_col].max())
        return full_df[
            (full_df[date_col] >= start_prev) & (full_df[date_col] <= end_prev)
        ].copy()
    if RANGE == "all":
        return full_df.iloc[0:0]
    prev_end = cur_start - pd.Timedelta(days=1)
    prev_start = prev_end - span
    return full_df[
        (full_df[date_col] >= prev_start) & (full_df[date_col] <= prev_end)
    ].copy()


def resample_numeric(
    df_r: pd.DataFrame, date_col: str, RULE: str, ensure_cols: List[str] = None
) -> pd.DataFrame:
    ensure_cols = ensure_cols or []
    num_cols = df_r.select_dtypes(include=[np.number]).columns
    res = (
        df_r.set_index(date_col)[num_cols]
        .resample(RULE)
        .sum(min_count=1)
        .reset_index()
        .rename(columns={date_col: "DATE_PERIOD"})
    )
    for must in ensure_cols:
        if (
            (must is not None)
            and (must in df_r.columns)
            and (must not in res.columns)
        ):
            add = (
                df_r.set_index(date_col)[[must]]
                .resample(RULE)
                .sum(min_count=1)
                .reset_index()
                .rename(columns={date_col: "DATE_PERIOD"})
            )
            res = res.merge(add, on="DATE_PERIOD", how="left")
    return res


def total_with_prev(
    df_r: pd.DataFrame, df_prev: pd.DataFrame, collist: List[str]
):
    cur = df_r[collist].sum().sum() if collist else np.nan
    prev = (
        df_prev[collist].sum().sum()
        if (not df_prev.empty and all(c in df_prev.columns for c in collist))
        else np.nan
    )
    return cur, (cur - prev) if pd.notna(prev) else None


def validate_against_metadata(df: pd.DataFrame, meta: dict) -> dict:
    if not isinstance(meta, dict):
        meta = {}

    mapping = meta.get("mapping") or {}
    goals = meta.get("goals") or []
    data_types: Dict[str, str] = meta.get("data_types") or {}
    channels_map: Dict[str, str] = meta.get("channels") or {}
    agg_strat: Dict[str, str] = meta.get("agg_strategies") or {}
    date_declared = str(meta.get("data", {}).get("date_field") or "DATE")

    declared_vars: List[str] = []
    for _, arr in (mapping or {}).items():
        if arr:
            declared_vars.extend(map(str, arr))
    declared_vars.extend(
        [str(g.get("var")) for g in goals if g and g.get("var")]
    )
    declared_vars.append(date_declared)
    declared_vars.extend(map(str, (data_types or {}).keys()))
    declared_vars.extend(map(str, (channels_map or {}).keys()))
    declared_vars.extend(map(str, (agg_strat or {}).keys()))
    # de-dup preserve order
    declared_vars = [
        v
        for i, v in enumerate(declared_vars)
        if v and v not in declared_vars[:i]
    ]

    meta_vars_norm = {v.strip().lower() for v in declared_vars}
    df_cols = list(map(str, df.columns))
    df_cols_norm = {c.strip().lower() for c in df_cols}

    # show only "extra in df"
    extra_in_df = sorted(
        [c for c in df_cols if c.strip().lower() not in meta_vars_norm]
    )

    # declared types (force date)
    declared_types: Dict[str, str] = {
        str(k): str(t or "").strip().lower()
        for k, t in (data_types or {}).items()
    }
    declared_types[date_declared] = "date"

    def _observed_kind(colname: str) -> str:
        if colname not in df.columns:
            return "missing"
        s = df[colname]
        if pd.api.types.is_datetime64_any_dtype(s):
            return "date"
        if pd.api.types.is_numeric_dtype(s):
            return "numeric"
        # if declared as date, attempt parse
        for dk, dt in declared_types.items():
            if dk.lower() == colname.lower() and dt == "date":
                probe = pd.to_datetime(s, errors="coerce")
                if probe.notna().any():
                    return "date"
        return "categorical"

    to_check = sorted(
        set(list(declared_types.keys()) + declared_vars), key=str.lower
    )
    rows = []
    for v in to_check:
        actual = next((c for c in df.columns if c.lower() == v.lower()), None)
        declared = declared_types.get(v, "") or "numeric"
        observed = _observed_kind(actual) if actual is not None else "missing"
        if observed != "missing" and declared != observed:
            rows.append(
                {"variable": v, "declared": declared, "observed": observed}
            )

    type_mismatches = pd.DataFrame(
        rows, columns=["variable", "declared", "observed"]
    )

    return {
        "missing_in_df": [],  # intentionally empty
        "extra_in_df": extra_in_df,
        "type_mismatches": type_mismatches,
        "channels_map": channels_map,
    }
