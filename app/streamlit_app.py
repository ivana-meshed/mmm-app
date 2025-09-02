# app.py — Streamlit front-end for launching & monitoring Robyn training jobs on Cloud Run Jobs

import io
import json
import logging
import os
import time
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, Any, Optional

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

# ─────────────────────────────
# Environment / constants
# ─────────────────────────────
PROJECT_ID = os.getenv("PROJECT_ID")
REGION = os.getenv("REGION", "europe-west1")
TRAINING_JOB_NAME = os.getenv("TRAINING_JOB_NAME")  # short or FQN
GCS_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")

# Session defaults
st.session_state.setdefault("job_executions", [])
st.session_state.setdefault("gcs_bucket", GCS_BUCKET)
st.session_state.setdefault("last_timings", None)
st.session_state.setdefault("auto_refresh", False)


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

    def create_execution(self, job_name: str, env_vars: Dict[str, str]) -> str:
        """
        Kick off a job and return the execution name quickly (non-blocking).
        NOTE: run_job() does not accept per-run overrides; pass dynamic data via GCS config.
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
        try:
            execution = self.executions_client.get_execution(
                name=execution_name
            )
            status = {
                "name": execution.name,
                "uid": execution.uid,
                "creation_timestamp": execution.creation_timestamp,
                "completion_timestamp": execution.completion_timestamp,
                "running_count": execution.running_count,
                "succeeded_count": execution.succeeded_count,
                "failed_count": execution.failed_count,
                "cancelled_count": execution.cancelled_count,
            }
            if execution.completion_timestamp and execution.succeeded_count > 0:
                status["overall_status"] = "SUCCEEDED"
            elif execution.completion_timestamp and execution.failed_count > 0:
                status["overall_status"] = "FAILED"
            elif (
                execution.completion_timestamp and execution.cancelled_count > 0
            ):
                status["overall_status"] = "CANCELLED"
            elif execution.running_count > 0:
                status["overall_status"] = "RUNNING"
            else:
                status["overall_status"] = "PENDING"
            return status
        except Exception as e:
            logger.error(f"Error getting execution status: {e}")
            return {"overall_status": "ERROR", "error": str(e)}


def upload_to_gcs(bucket_name: str, local_path: str, dest_blob: str) -> str:
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(dest_blob)
    blob.upload_from_filename(local_path)
    return f"gs://{bucket_name}/{dest_blob}"


@st.cache_resource
def get_data_processor():
    return DataProcessor()


@st.cache_resource
def get_job_manager():
    return CloudRunJobManager(PROJECT_ID, REGION)


data_processor = get_data_processor()
job_manager = get_job_manager()


# ─────────────────────────────
# Helpers
# ─────────────────────────────
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


def sf_connect(
    sf_user, sf_password, sf_account, sf_wh, sf_db, sf_schema, sf_role
):
    return sf.connect(
        user=sf_user,
        password=sf_password,
        account=sf_account,
        warehouse=sf_wh,
        database=sf_db,
        schema=sf_schema,
        role=sf_role if sf_role else None,
    )


def run_sql(
    sql: str, sf_user, sf_password, sf_account, sf_wh, sf_db, sf_schema, sf_role
) -> pd.DataFrame:
    con = sf_connect(
        sf_user, sf_password, sf_account, sf_wh, sf_db, sf_schema, sf_role
    )
    try:
        cur = con.cursor()
        cur.execute(sql)
        df = cur.fetch_pandas_all()
        return df
    finally:
        try:
            cur.close()
        except Exception:
            pass
        con.close()


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
# Optional: show training time button (only if a job exists)
# ─────────────────────────────
if st.session_state.job_executions:
    latest_job_for_time = st.session_state.job_executions[-1]
    time_prefix = (
        latest_job_for_time.get("gcs_prefix")
        or f"robyn/{latest_job_for_time.get('revision','r100')}/{latest_job_for_time.get('country','fr')}/{latest_job_for_time.get('timestamp','')}"
    )
    if st.button("⏱️ Refresh training time"):
        s = read_status_json(GCS_BUCKET, time_prefix)
        if not s:
            st.info("No status.json yet (job may still be starting).")
        else:
            if s.get("state") == "SUCCEEDED" and "duration_minutes" in s:
                st.success(f"Training time: {s['duration_minutes']} minutes")
            elif "start_time" in s:
                try:
                    started = datetime.fromisoformat(
                        s["start_time"].replace("Z", "")
                    )
                    elapsed = (
                        datetime.now(timezone.utc)
                        - started.replace(tzinfo=timezone.utc)
                    ).total_seconds() / 60
                    st.info(f"Running… elapsed ~{elapsed:.1f} minutes")
                except Exception:
                    st.info("Running… (could not parse start time)")

# Auto-refresh toggle (manual)
if st.toggle(
    "Auto-refresh status",
    value=st.session_state["auto_refresh"],
    key="auto_refresh",
):
    st.rerun()

# ─────────────────────────────
# Sidebar
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

# ─────────────────────────────
# Main UI
# ─────────────────────────────
st.title("Robyn MMM Trainer")

# Snowflake params
with st.expander("Snowflake connection"):
    sf_user = st.text_input("User", value="IPENC")
    sf_account = st.text_input("Account", value="AMXUZTH-AWS_BRIDGE")
    sf_wh = st.text_input("Warehouse", value="SMALL_WH")
    sf_db = st.text_input("Database", value="MESHED_BUYCYCLE")
    sf_schema = st.text_input("Schema", value="GROWTH")
    sf_role = st.text_input("Role", value="ACCOUNTADMIN")
    sf_password = st.text_input("Password", type="password")

# Data selection
with st.expander("Data selection"):
    table = st.text_input("Table (DB.SCHEMA.TABLE)")
    query = st.text_area("Custom SQL (optional)")

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
    context_vars = st.text_input("context_vars", value="IS_WEEKEND,TV_IS_ON")
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


def create_job_config(
    data_gcs_path: str,
    timestamp: str,
    annotations_gcs_path: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "country": country,
        "iterations": int(iterations),
        "trials": int(trials),
        "train_size": parse_train_size(train_size),
        "revision": revision,
        "date_input": date_input,
        "gcs_bucket": gcs_bucket,
        "data_gcs_path": data_gcs_path,
        "annotations_gcs_path": annotations_gcs_path,
        "paid_media_spends": [
            s.strip() for s in paid_media_spends.split(",") if s.strip()
        ],
        "paid_media_vars": [
            s.strip() for s in paid_media_vars.split(",") if s.strip()
        ],
        "context_vars": [
            s.strip() for s in context_vars.split(",") if s.strip()
        ],
        "factor_vars": [s.strip() for s in factor_vars.split(",") if s.strip()],
        "organic_vars": [
            s.strip() for s in organic_vars.split(",") if s.strip()
        ],
        "timestamp": timestamp,
        "use_parquet": True,
        "parallel_processing": True,
        "max_cores": 8,
    }


# Quick connection test
if st.button("Test connection & preview 5 rows"):
    sql = effective_sql(table, query)
    if not sql:
        st.warning("Provide a table or a SQL query.")
    elif not sf_password:
        st.error("Password is required to connect.")
    else:
        try:
            preview_sql = f"SELECT * FROM ({sql}) t LIMIT 5"
            df_prev = run_sql(
                preview_sql,
                sf_user,
                sf_password,
                sf_account,
                sf_wh,
                sf_db,
                sf_schema,
                sf_role,
            )
            st.success("Connection OK")
            st.dataframe(df_prev)
        except Exception as e:
            st.error(f"Preview failed: {e}")

# ─────────────────────────────
# Launch Training
# ─────────────────────────────
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
                sql = effective_sql(table, query)
                data_gcs_path = None
                annotations_gcs_path = None

                if not sql:
                    st.error(
                        "Provide a table or SQL query to prepare training data."
                    )
                    st.stop()

                if not sf_password:
                    st.error("Password required for Snowflake.")
                    st.stop()

                try:
                    # 1) Query Snowflake
                    with timed_step("Query Snowflake", timings):
                        df = run_sql(
                            sql,
                            sf_user,
                            sf_password,
                            sf_account,
                            sf_wh,
                            sf_db,
                            sf_schema,
                            sf_role,
                        )

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

                    st.success(
                        f"Data prepared: {len(df):,} rows uploaded to {data_gcs_path}"
                    )

                except Exception as e:
                    st.error(f"Data preparation failed: {e}")
                    st.stop()

                # Optional annotations
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
                    job_config = create_job_config(
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
                    # run_job doesn't accept overrides; R script has fallback to latest/ if needed
                    env_vars = {
                        "JOB_CONFIG_GCS_PATH": config_gcs_path,
                        "SNOWFLAKE_PASSWORD": sf_password or "",
                        "TIMESTAMP": timestamp,
                    }
                    try:
                        execution_name = job_manager.create_execution(
                            TRAINING_JOB_NAME, env_vars
                        )
                        exec_info = {
                            "execution_name": execution_name,
                            "timestamp": timestamp,
                            "status": "LAUNCHED",
                            "config_path": config_gcs_path,
                            "data_path": data_gcs_path,
                            # used later by “View Results”
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
                        st.info(
                            "**Training Resources**: 8 CPUs, 32GB RAM (per Terraform)"
                        )
                    except Exception as e:
                        st.error(str(e))
                        logger.error(f"Job launch error: {e}", exc_info=True)

    finally:
        # Seed timings.csv once (web-side steps only). The R job will append its own row later.
        if timings:
            df_times = pd.DataFrame(timings)
            try:
                client = storage.Client()
                dest_blob = f"{gcs_prefix}/timings.csv"
                blob = client.bucket(gcs_bucket).blob(dest_blob)
                if not blob.exists():  # only seed if not present
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
                        "`timings.csv` already exists — leaving it for the job to append its R row."
                    )
            except Exception as e:
                st.warning(f"Failed to upload timings: {e}")

        # Cache timings locally for UI display
        st.session_state.last_timings = {
            "df": pd.DataFrame(timings),
            "timestamp": timestamp,
            "revision": revision,
            "country": country,
            "gcs_bucket": gcs_bucket,
        }

# ─────────────────────────────
# Job Status Monitor
# ─────────────────────────────
st.subheader("📊 Job Status Monitor")

if st.session_state.job_executions:
    latest_job = st.session_state.job_executions[-1]
    execution_name = latest_job["execution_name"]

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("🔍 Check Status"):
            status_info = job_manager.get_execution_status(execution_name)
            st.json(status_info)
            latest_job["status"] = status_info.get("overall_status", "UNKNOWN")
            latest_job["last_checked"] = datetime.now().isoformat()

    with col2:
        if st.button("📁 View Results"):
            gcs_prefix_view = latest_job.get("gcs_prefix")
            bucket_view = latest_job.get("gcs_bucket", GCS_BUCKET)
            st.info(f"Check results at: gs://{bucket_view}/{gcs_prefix_view}/")

            # Try to fetch training log
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

    with col3:
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
    st.info("No jobs launched yet in this session. Start a training job above.")

# ─────────────────────────────
# Execution timeline & timings.csv handling
# ─────────────────────────────
if st.session_state.last_timings:
    with st.expander("⏱️ Execution Timeline", expanded=True):
        df_times = st.session_state.last_timings["df"]
        total = float(df_times["Time (s)"].sum()) if not df_times.empty else 0.0
        if total > 0:
            df_times = df_times.copy()
            df_times["% of total"] = (df_times["Time (s)"] / total * 100).round(
                1
            )

        st.markdown("**Setup steps (this session)**")
        st.dataframe(df_times, use_container_width=True)
        st.write(f"**Total setup time:** {_fmt_secs(total)}")
        st.write("**Note**: Training runs asynchronously in Cloud Run Jobs.")

        # Seed the timings file if it's missing (idempotent)
        try:
            ts = st.session_state.last_timings["timestamp"]
            rev = st.session_state.last_timings["revision"]
            ctry = st.session_state.last_timings["country"]
            bucket_seed = st.session_state.last_timings["gcs_bucket"]
            gcs_prefix_seed = f"robyn/{rev}/{ctry}/{ts}"
            dest_blob_seed = f"{gcs_prefix_seed}/timings.csv"

            client = storage.Client()
            blob_seed = client.bucket(bucket_seed).blob(dest_blob_seed)
            if not blob_seed.exists() and not df_times.empty:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".csv", delete=False
                ) as tmp:
                    df_times.to_csv(tmp.name, index=False)
                    upload_to_gcs(bucket_seed, tmp.name, dest_blob_seed)
                st.success(
                    f"Timings CSV uploaded to **gs://{bucket_seed}/{dest_blob_seed}**"
                )
            elif blob_seed.exists():
                st.info(
                    "`timings.csv` already exists — job will append the R row."
                )
        except Exception as e:
            st.warning(f"Skipped uploading timings: {e}")

        # Authoritative copy from GCS (this is where the R row appears)
        try:
            blob_live = (
                storage.Client().bucket(bucket_seed).blob(dest_blob_seed)
            )
            if blob_live.exists():
                gcs_bytes = blob_live.download_as_bytes()
                df_gcs = pd.read_csv(io.BytesIO(gcs_bytes))
                st.markdown("**Timings from GCS (authoritative)**")
                st.dataframe(df_gcs, use_container_width=True)
                st.download_button(
                    "Download timings.csv",
                    data=gcs_bytes,
                    file_name="timings.csv",
                    mime="text/csv",
                    key="dl_timings_from_gcs",
                )
            else:
                st.info("`timings.csv` not in GCS yet.")
        except Exception as e:
            st.warning(f"Could not load timings from GCS: {e}")

# ─────────────────────────────
# Architecture info
# ─────────────────────────────
with st.expander("🏗️ Architecture Info"):
    st.markdown(
        """
**Cloud Run Jobs Architecture:**
- **Web Interface**: Cloud Run Service (this app)
- **Training Jobs**: Cloud Run Jobs v2
- **Storage**: Google Cloud Storage (Parquet)
- **Orchestration**: Cloud Run Jobs API

**Benefits:** up to 8 CPUs/32GB per your Terraform, async jobs, web stays responsive
"""
    )
