# app_shared.py — shared helpers for Robyn Streamlit app
import os, io, json, time, re
from datetime import datetime, timezone

# add to the existing imports at the top of app_shared.py
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
import logging
import tempfile

logger = logging.getLogger(__name__)

import pandas as pd
import streamlit as st
import snowflake.connector as sf
from google.cloud import storage, run_v2

from data_processor import DataProcessor
from uuid import uuid4
from google.api_core.exceptions import PreconditionFailed

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

# Canonical ledger schema & normalization
LEDGER_COLUMNS = [
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

# Canonical ledger schema (builder params + exec/info)
LEDGER_COLUMNS = (
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


def _empty_ledger_df() -> pd.DataFrame:
    # Matches fields written by run_all.R::append_to_ledger()
    cols = LEDGER_COLUMNS
    return pd.DataFrame(columns=cols)


def _safe_tick_once(
    queue_name: str,
    bucket_name: Optional[str] = None,
    launcher: Optional[callable] = None,
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
        gen = int(blob.generation)
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

        jm = CloudRunJobManager(PROJECT_ID, REGION)

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
        gen2 = int(blob.generation)
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


def normalize_ledger_df(df: "pd.DataFrame"):
    import pandas as pd

    df = (df if isinstance(df, pd.DataFrame) else pd.DataFrame()).copy()

    # Backward compat renames
    if "status" in df.columns and "state" not in df.columns:
        df = df.rename(columns={"status": "state"})
    if "gcs_bucket" in df.columns and "bucket" not in df.columns:
        df = df.rename(columns={"gcs_bucket": "bucket"})

    # Ensure all expected columns exist
    for c in LEDGER_COLUMNS:
        if c not in df.columns:
            df[c] = pd.NA

    # --- Exec fields present & normalized ---
    df["execution_name"] = df["execution_name"].fillna("").astype(str)

    if "exec_name" not in df.columns:
        df["exec_name"] = ""
    df["exec_name"] = df["exec_name"].fillna("").astype(str)

    # Backfill exec_name from execution_name when missing
    mask = df["exec_name"].str.strip().eq("") & df[
        "execution_name"
    ].str.strip().ne("")
    if mask.any():
        df.loc[mask, "exec_name"] = df.loc[mask, "execution_name"].apply(
            _short_exec_name
        )

    # Normalize any non-empty exec_name to short form
    df["exec_name"] = df["exec_name"].apply(_short_exec_name)

    # Normalize times to UTC ISO seconds
    df["start_time"] = df["start_time"].apply(_iso_utc)
    df["end_time"] = df["end_time"].apply(_iso_utc)

    # Canonical job_id
    def _canon_job_id(row):
        jid = str(row.get("job_id") or "")
        gpref = row.get("gcs_prefix") or ""
        return gpref if (jid.isdigit() and gpref) else (jid or gpref)

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

    # Reorder to canonical columns before coalescing
    df = df[LEDGER_COLUMNS]

    # Coalesce duplicates by job_id (keep first non-null/non-empty per column)
    if "job_id" in df.columns and not df.empty:

        def _first_non_empty(s):
            for x in s:
                if pd.notna(x) and (not isinstance(x, str) or x.strip() != ""):
                    return x
            return pd.NA

        df = df.groupby("job_id", as_index=False, dropna=False).agg(
            _first_non_empty
        )
        # Enforce column order again after groupby
        df = df[LEDGER_COLUMNS]

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


def _get_ledger_object() -> str:
    return os.getenv("JOBS_LEDGER_OBJECT", "robyn-jobs/ledger.csv")


def read_ledger_from_gcs(bucket_name: str) -> pd.DataFrame:
    from google.cloud import storage

    client = storage.Client()
    blob = client.bucket(bucket_name).blob("robyn-jobs/ledger.csv")
    if not blob.exists():
        return _empty_ledger_df()  # your function with LEDGER_COLUMNS

    raw = blob.download_as_bytes()
    if not raw:
        return _empty_ledger_df()

    df = pd.read_csv(io.BytesIO(raw))
    if df is None or df.empty:
        return _empty_ledger_df()

    # (optional) normalize columns/order here if you want
    return normalize_ledger_df(df)


def save_ledger_to_gcs(df, bucket_name: str):
    import io
    from google.cloud import storage

    df = normalize_ledger_df(df)
    b = io.BytesIO()
    df.to_csv(b, index=False)
    b.seek(0)
    client = storage.Client()
    blob = client.bucket(bucket_name).blob("robyn-jobs/ledger.csv")
    blob.upload_from_file(b, content_type="text/csv")
    return True


def append_row_to_ledger(row_dict: dict, bucket_name: str):
    import pandas as pd

    # Ensure all expected keys exist
    for c in LEDGER_COLUMNS:
        row_dict.setdefault(c, pd.NA)

    # If exec_name is missing but we have execution_name, derive it
    if (not row_dict.get("exec_name")) and row_dict.get("execution_name"):
        row_dict["exec_name"] = _short_exec_name(
            str(row_dict["execution_name"])
        )

    # New row (normalized)
    df_new = normalize_ledger_df(pd.DataFrame([row_dict]))

    # Existing ledger (may be empty)
    df_old = read_ledger_from_gcs(bucket_name)
    if df_old is None or df_old.empty:
        return save_ledger_to_gcs(df_new, bucket_name)

    # Merge by job_id: fill missing values in existing row with new values (combine_first)
    df_old = df_old.set_index("job_id")
    df_new = df_new.set_index("job_id")
    for jid, s in df_new.iterrows():
        if jid in df_old.index:
            df_old.loc[jid] = df_old.loc[jid].combine_first(s)
        else:
            df_old.loc[jid] = s

    df_merged = df_old.reset_index()
    df_merged = normalize_ledger_df(df_merged)
    return save_ledger_to_gcs(df_merged, bucket_name)


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
        "dep_var": str(params.get("dep_var", "UPLOAD_VALUE")),  # NEW
        "date_var": str(params.get("date_var", "date")),  # NEW
        "adstock": str(params.get("adstock", "geometric")),  # NEW
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
    return _safe_tick_once(queue_name, bucket_name, launcher)


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
