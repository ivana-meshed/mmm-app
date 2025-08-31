import json
import logging
import os
import time
import tempfile
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, Any, Optional

import pandas as pd
import snowflake.connector as sf
import streamlit as st
from google.cloud import run_v2

from google.cloud import storage
from data_processor import DataProcessor

# Page config MUST run before any other Streamlit output
st.set_page_config(page_title="Robyn MMM Trainer", layout="wide")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

query_params = st.query_params
logger.info(
    "Starting app/streamlit_app.py", extra={"query_params": query_params}
)

# Health check endpoint (kept simple; does not render the app)
if query_params.get("health") == "true":
    try:
        from health import health_checker

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

# ---- Environment config
PROJECT_ID = os.getenv("PROJECT_ID")
REGION = os.getenv("REGION", "europe-west1")
TRAINING_JOB_NAME = os.getenv(
    "TRAINING_JOB_NAME"
)  # may be short ("mmm-app-training") or FQN
GCS_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")


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


class CloudRunJobManager:
    """Manages Cloud Run Job executions."""

    def __init__(self, project_id: str, region: str):
        self.project_id = project_id
        self.region = region
        self.client = run_v2.JobsClient()
        self.executions_client = run_v2.ExecutionsClient()

    def _job_fqn(self, job_name: str) -> str:
        # Accept either short name or fully-qualified name
        if job_name.startswith("projects/"):
            return job_name
        return f"projects/{self.project_id}/locations/{self.region}/jobs/{job_name}"

    def create_execution(self, job_name: str, env_vars: Dict[str, str]) -> str:
        fqn = self._job_fqn(job_name)
        overrides = {
            "container_overrides": [
                {"env": [{"name": k, "value": v} for k, v in env_vars.items()]}
            ]
        }
        try:
            # newer clients
            op = self.client.run_job(
                request={"name": fqn, "overrides": overrides}
            )
        except TypeError:
            # older clients
            logging.warning(
                "Cloud Run client lacks 'overrides'; starting job without per-run env."
            )
            op = self.client.run_job(name=fqn)

        execution = op.result()
        logging.info("Created execution: %s", execution.name)
        return execution.name

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

# Session state defaults (prevents NameError on first render)
st.session_state.setdefault("job_executions", [])
st.session_state.setdefault("last_timings", None)

# Sidebar
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

st.title("Robyn MMM Trainer")

# --- Snowflake params
with st.expander("Snowflake connection"):
    sf_user = st.text_input("User", value="IPENC")
    sf_account = st.text_input("Account", value="AMXUZTH-AWS_BRIDGE")
    sf_wh = st.text_input("Warehouse", value="SMALL_WH")
    sf_db = st.text_input("Database", value="MESHED_BUYCYCLE")
    sf_schema = st.text_input("Schema", value="GROWTH")
    sf_role = st.text_input("Role", value="ACCOUNTADMIN")
    sf_password = st.text_input("Password", type="password")

# --- Data source
with st.expander("Data selection"):
    table = st.text_input("Table (DB.SCHEMA.TABLE)")
    query = st.text_area("Custom SQL (optional)")

# --- Robyn configuration
with st.expander("Robyn configuration"):
    country = st.text_input("Country", value="fr")
    iterations = st.number_input("Iterations", value=200, min_value=50)
    trials = st.number_input("Trials", value=5, min_value=1)
    train_size = st.text_input("Train size", value="0.7,0.9")
    revision = st.text_input("Revision tag", value="r100")
    date_input = st.text_input("Date tag", value=time.strftime("%Y-%m-%d"))

# --- Variables
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

# --- Outputs
with st.expander("Outputs"):
    gcs_bucket = st.text_input("GCS bucket for outputs", value=GCS_BUCKET)
    ann_file = st.file_uploader(
        "Optional: enriched_annotations.csv", type=["csv"]
    )


def sf_connect():
    return sf.connect(
        user=sf_user,
        password=sf_password,
        account=sf_account,
        warehouse=sf_wh,
        database=sf_db,
        schema=sf_schema,
        role=sf_role if sf_role else None,
    )


def run_sql(sql: str) -> pd.DataFrame:
    con = sf_connect()
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


def effective_sql():
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


# Quick connection test
if st.button("Test connection & preview 5 rows"):
    sql = effective_sql()
    if not sql:
        st.warning("Provide a table or a SQL query.")
    elif not sf_password:
        st.error("Password is required to connect.")
    else:
        try:
            preview_sql = f"SELECT * FROM ({sql}) t LIMIT 5"
            df_prev = run_sql(preview_sql)
            st.success("Connection OK")
            st.dataframe(df_prev)
        except Exception as e:
            st.error(f"Preview failed: {e}")


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
        "max_cores": 32,
    }


# ========== LAUNCH TRAINING JOB ==========
if st.button("🚀 Start Training Job", type="primary"):
    if not all([PROJECT_ID, REGION, TRAINING_JOB_NAME]):
        st.error(
            "Missing configuration. Check environment variables on the web service."
        )
        st.stop()

    # One timestamp for the entire run (used in data path + R outputs)
    timestamp = datetime.utcnow().strftime("%m%d_%H%M%S")
    timings: list[dict[str, float]] = []

    with st.spinner("Preparing and launching training job..."):
        with tempfile.TemporaryDirectory() as td:
            sql = effective_sql()
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
                with timed_step("Query Snowflake", timings):
                    df = run_sql(sql)

                with timed_step("Convert to Parquet", timings):
                    parquet_path = os.path.join(td, "input_data.parquet")
                    data_processor.csv_to_parquet(df, parquet_path)

                with timed_step("Upload data to GCS", timings):
                    data_blob = f"training-data/{timestamp}/input_data.parquet"
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
                    annotations_blob = (
                        f"training-data/{timestamp}/enriched_annotations.csv"
                    )
                    annotations_gcs_path = upload_to_gcs(
                        gcs_bucket, annotations_path, annotations_blob
                    )

            # Job config
            with timed_step("Create job configuration", timings):
                job_config = create_job_config(
                    data_gcs_path, timestamp, annotations_gcs_path
                )
                config_path = os.path.join(td, "job_config.json")
                with open(config_path, "w") as f:
                    json.dump(job_config, f, indent=2)
                config_blob = f"training-configs/{timestamp}/job_config.json"
                config_gcs_path = upload_to_gcs(
                    gcs_bucket, config_path, config_blob
                )
                latest_blob = "training-configs/latest/job_config.json"
                latest_gcs_path = upload_to_gcs(
                    gcs_bucket, config_path, latest_blob
                )

            # Launch job
            with timed_step("Launch training job", timings):
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
                        # ✅ used later by “View Results”
                        "revision": revision,
                        "country": country,
                        "gcs_prefix": f"robyn/{revision}/{country}/{timestamp}",
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

    # Keep timings from this run for the timeline section
    st.session_state.last_timings = {
        "df": pd.DataFrame(timings),
        "timestamp": timestamp,
        "revision": revision,
        "country": country,
        "gcs_bucket": gcs_bucket,
    }

# ===== Job status & results =====
if st.session_state.job_executions:
    st.subheader("📊 Job Status Monitor")
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
            gcs_prefix = latest_job.get("gcs_prefix")
            bucket = latest_job.get("gcs_bucket", gcs_bucket)
            st.info(f"Check results at: gs://{bucket}/{gcs_prefix}/")

            # Try to fetch training log
            try:
                client = storage.Client()
                bucket_obj = client.bucket(bucket)
                log_blob = bucket_obj.blob(f"{gcs_prefix}/robyn_console.log")
                if log_blob.exists():
                    log_content = log_blob.download_as_text()
                    st.text_area(
                        "Training Log (last 2000 chars):",
                        value=log_content[-2000:],
                        height=200,
                    )
                    st.download_button(
                        "Download full training log",
                        data=log_content,
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
                        ][:20]
                        + "...",
                        "Last Checked": job.get("last_checked", "Never"),
                        "Revision": job.get("revision", ""),
                        "Country": job.get("country", ""),
                    }
                    for job in st.session_state.job_executions
                ]
            )
            st.dataframe(df_jobs, use_container_width=True)

# ===== Execution timeline & upload timings =====
if st.session_state.last_timings:
    with st.expander("⏱️ Execution Timeline", expanded=True):
        df_times = st.session_state.last_timings["df"]
        total = float(df_times["Time (s)"].sum()) if not df_times.empty else 0.0
        if total > 0:
            df_times = df_times.copy()
            df_times["% of total"] = (df_times["Time (s)"] / total * 100).round(
                1
            )
        st.dataframe(df_times, use_container_width=True)
        st.write(f"**Total setup time:** {_fmt_secs(total)}")
        st.write("**Note**: Training runs asynchronously in Cloud Run Jobs.")

        # Upload timings CSV next to R outputs
        try:
            ts = st.session_state.last_timings["timestamp"]
            rev = st.session_state.last_timings["revision"]
            ctry = st.session_state.last_timings["country"]
            bucket = st.session_state.last_timings["gcs_bucket"]
            gcs_prefix = f"robyn/{rev}/{ctry}/{ts}"

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".csv", delete=False
            ) as tmp:
                df_times.to_csv(tmp.name, index=False)
                timings_csv_local = tmp.name

            dest_blob = f"{gcs_prefix}/timings.csv"
            gcs_uri = upload_to_gcs(bucket, timings_csv_local, dest_blob)
            st.success(f"Timings CSV uploaded to **{gcs_uri}**")
            st.download_button(
                "Download timings.csv",
                data=df_times.to_csv(index=False),
                file_name="timings.csv",
                mime="text/csv",
                key="dl_timings_csv",
            )
            try:
                os.unlink(timings_csv_local)
            except Exception:
                pass
        except Exception as e:
            st.warning(f"Failed to upload timings: {e}")
            st.download_button(
                "Download timings.csv",
                data=df_times.to_csv(index=False),
                file_name="timings.csv",
                mime="text/csv",
                key="dl_timings_csv_fallback",
            )

# Architecture info
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
