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

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

query_params = st.query_params
logger.info(
    "Starting app/streamlit_app.py", extra={"query_params": query_params}
)

# Health check endpoint
if query_params.get("health") == "true":
    try:
        from health import health_checker

        health_status = health_checker.check_container_health()
        st.json(health_status)
        st.stop()
    except Exception as e:
        st.json(
            {
                "status": "error",
                "timestamp": datetime.now().isoformat(),
                "error": str(e),
            }
        )
        st.stop()

st.set_page_config(page_title="Robyn MMM Trainer", layout="wide")

# Configuration from environment
PROJECT_ID = os.getenv("PROJECT_ID")
REGION = os.getenv("REGION", "europe-west1")
TRAINING_JOB_NAME = os.getenv("TRAINING_JOB_NAME")
GCS_BUCKET = os.getenv("GCS_BUCKET", "mmm-app-output")


def _fmt_secs(s: float) -> str:
    if s < 60:
        return f"{s:.2f}s"
    m, sec = divmod(s, 60)
    return f"{int(m)}m {sec:.1f}s"


@contextmanager
def timed_step(name: str, bucket: list):
    """Context manager to time a step and print live status in Streamlit."""
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
    """Manages Cloud Run Job executions"""

    def __init__(self, project_id: str, region: str):
        self.project_id = project_id
        self.region = region
        self.client = run_v2.JobsClient()
        self.executions_client = run_v2.ExecutionsClient()

    def create_execution(self, job_name: str, env_vars: Dict[str, str]) -> str:
        """Create a new execution of the training job"""

        # Convert env vars to the required format
        env_list = [{"name": k, "value": v} for k, v in env_vars.items()]

        # Create execution request
        request = run_v2.RunJobRequest(
            name=f"projects/{self.project_id}/locations/{self.region}/jobs/{job_name}",
            overrides=run_v2.RunJobRequest.Overrides(
                container_overrides=[
                    run_v2.RunJobRequest.Overrides.ContainerOverride(
                        env=env_list
                    )
                ]
            ),
        )

        # Execute the job
        operation = self.client.run_job(request=request)
        execution = operation.result()

        execution_name = execution.name
        logger.info(f"Created execution: {execution_name}")
        return execution_name

    def get_execution_status(self, execution_name: str) -> Dict[str, Any]:
        """Get the status of a job execution"""
        try:
            execution = self.executions_client.get_execution(
                name=execution_name
            )

            # Parse the status
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

            # Determine overall status
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
    """Upload a local file to GCS and return the gs:// URI."""
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

# Debug info in sidebar
with st.sidebar:
    st.subheader("🔧 System Info")
    st.write(f"**Project ID**: {PROJECT_ID}")
    st.write(f"**Region**: {REGION}")
    st.write(f"**Training Job**: {TRAINING_JOB_NAME}")
    st.write(f"**GCS Bucket**: {GCS_BUCKET}")

    # Memory info
    try:
        import psutil

        memory = psutil.virtual_memory()
        st.write(f"**Available Memory**: {memory.available / 1024**3:.1f} GB")
        st.write(f"**Memory Usage**: {memory.percent:.1f}%")
    except ImportError:
        st.write("**Memory Info**: psutil not available")

    # Show recent job executions if available
    if "job_executions" in st.session_state:
        st.subheader("📋 Recent Jobs")
        for i, exec_info in enumerate(st.session_state.job_executions[-3:]):
            status = exec_info.get("status", "UNKNOWN")
            timestamp = exec_info.get("timestamp", "")
            st.write(f"**Job {i+1}**: {status}")
            st.write(f"*{timestamp}*")

st.title("Robyn MMM Trainer")

# Snowflake connection params
with st.expander("Snowflake connection"):
    sf_user = st.text_input("User", value="IPENC")
    sf_account = st.text_input("Account", value="AMXUZTH-AWS_BRIDGE")
    sf_wh = st.text_input("Warehouse", value="SMALL_WH")
    sf_db = st.text_input("Database", value="MESHED_BUYCYCLE")
    sf_schema = st.text_input("Schema", value="GROWTH")
    sf_role = st.text_input("Role", value="ACCOUNTADMIN")
    sf_password = st.text_input("Password", type="password")

# Data source
with st.expander("Data selection"):
    table = st.text_input("Table (DB.SCHEMA.TABLE)")
    query = st.text_area("Custom SQL (optional)")

# Robyn configuration
with st.expander("Robyn configuration"):
    country = st.text_input("Country", value="fr")
    iterations = st.number_input("Iterations", value=200, min_value=50)
    trials = st.number_input("Trials", value=5, min_value=1)
    train_size = st.text_input("Train size", value="0.7,0.9")
    revision = st.text_input("Revision tag", value="r100")
    date_input = st.text_input("Date tag", value=time.strftime("%Y-%m-%d"))

# Variable mapping
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


# Connection test
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
    """Create job configuration for the training job"""
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
        "max_cores": 32,  # Cloud Run Jobs can use 32 CPUs
    }


# Main training button
if st.button("🚀 Start Training Job", type="primary"):
    if not all([PROJECT_ID, REGION, TRAINING_JOB_NAME]):
        st.error("Missing configuration. Check environment variables.")
        st.stop()

    timings = []

    with st.spinner("Preparing and launching training job..."):
        with tempfile.TemporaryDirectory() as td:
            # 1) Query and prepare data
            sql = effective_sql()
            data_gcs_path = None
            annotations_gcs_path = None

            if sql:
                if not sf_password:
                    st.error("Password required for Snowflake.")
                    st.stop()

                try:
                    with timed_step("Query Snowflake", timings):
                        df = run_sql(sql)

                    # Convert to Parquet for efficient processing
                    with timed_step("Convert to Parquet", timings):
                        parquet_path = os.path.join(td, "input_data.parquet")
                        data_processor.csv_to_parquet(df, parquet_path)

                    # Upload to GCS for the training job to access
                    with timed_step("Upload data to GCS", timings):
                        timestamp = datetime.utcnow().strftime("%m%d_%H%M%S")
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

            # 2) Handle annotations if provided
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

            # 3) Create and upload job config
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

            # 4) Launch Cloud Run Job
            with timed_step("Launch training job", timings):
                env_vars = {
                    "JOB_CONFIG_GCS_PATH": config_gcs_path,
                    "SNOWFLAKE_PASSWORD": sf_password if sf_password else "",
                    "TIMESTAMP": timestamp,
                }

                try:
                    execution_name = job_manager.create_execution(
                        TRAINING_JOB_NAME, env_vars
                    )

                    # Store execution info
                    if "job_executions" not in st.session_state:
                        st.session_state.job_executions = []

                    execution_info = {
                        "execution_name": execution_name,
                        "timestamp": timestamp,
                        "status": "LAUNCHED",
                        "config_path": config_gcs_path,
                        "data_path": data_gcs_path,
                    }
                    st.session_state.job_executions.append(execution_info)

                    st.success("🎉 Training job launched successfully!")
                    st.info(
                        f"**Execution ID**: `{execution_name.split('/')[-1]}`"
                    )
                    st.info(f"**Timestamp**: {timestamp}")
                    st.info(f"**Training Resources**: 32 CPUs, 128GB RAM")

                except Exception as e:
                    st.error(f"Failed to launch training job: {e}")
                    logger.error(f"Job launch error: {e}", exc_info=True)

# Job status monitoring
if "job_executions" in st.session_state and st.session_state.job_executions:
    st.subheader("📊 Job Status Monitor")

    # Get the most recent job
    latest_job = st.session_state.job_executions[-1]
    execution_name = latest_job["execution_name"]

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("🔍 Check Status"):
            status_info = job_manager.get_execution_status(execution_name)
            st.json(status_info)

            # Update stored status
            latest_job["status"] = status_info.get("overall_status", "UNKNOWN")
            latest_job["last_checked"] = datetime.now().isoformat()

    with col2:
        if st.button("📁 View Results"):
            timestamp = latest_job.get("timestamp", "unknown")
            revision_val = "r100"  # Default, could be stored in job info
            country_val = "fr"  # Default, could be stored in job info
            gcs_prefix = f"robyn/{revision_val}/{country_val}/{timestamp}"
            st.info(f"Check results at: gs://{gcs_bucket}/{gcs_prefix}/")

            # Try to fetch and display training log
            try:
                client = storage.Client()
                bucket = client.bucket(gcs_bucket)
                log_blob = bucket.blob(f"{gcs_prefix}/robyn_console.log")

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
                        file_name=f"robyn_training_{timestamp}.log",
                        mime="text/plain",
                        key=f"dl_log_{timestamp}",
                    )
                else:
                    st.info(
                        "Training log not yet available. Check back when job completes."
                    )

            except Exception as e:
                st.warning(f"Could not fetch training log: {e}")

    with col3:
        if st.button("📋 Show All Jobs"):
            if len(st.session_state.job_executions) > 1:
                df_jobs = pd.DataFrame(
                    [
                        {
                            "Timestamp": job.get("timestamp", ""),
                            "Status": job.get("status", "UNKNOWN"),
                            "Execution": job.get("execution_name", "").split(
                                "/"
                            )[-1][:20]
                            + "...",
                            "Last Checked": job.get("last_checked", "Never"),
                        }
                        for job in st.session_state.job_executions
                    ]
                )
                st.dataframe(df_jobs)
            else:
                st.info("Only one job in history")

# Show execution timeline and upload timings
if timings:
    with st.expander("⏱️ Execution Timeline", expanded=True):
        df_times = pd.DataFrame(timings)
        total = float(df_times["Time (s)"].sum())
        df_times["% of total"] = (df_times["Time (s)"] / total * 100).round(1)

        st.dataframe(df_times, use_container_width=True)
        st.write(f"**Total setup time:** {_fmt_secs(total)}")
        st.write(
            "**Note**: Training runs asynchronously in Cloud Run Jobs with 32 CPUs"
        )

        # Upload timings to GCS (same location as R outputs)
        try:
            gcs_prefix = f"robyn/{revision}/{country}/{timestamp}"

            # Save to a temp file
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".csv", delete=False
            ) as tmp:
                df_times.to_csv(tmp.name, index=False)
                timings_csv_local = tmp.name

            # Upload to the same prefix as R outputs
            dest_blob = f"{gcs_prefix}/timings.csv"
            gcs_uri = upload_to_gcs(gcs_bucket, timings_csv_local, dest_blob)

            st.success(f"Timings CSV uploaded to **{gcs_uri}**")

            # Download button for timings
            st.download_button(
                "Download timings.csv",
                data=df_times.to_csv(index=False),
                file_name="timings.csv",
                mime="text/csv",
                key="dl_timings_csv",
            )

            # Cleanup temp file
            try:
                os.unlink(timings_csv_local)
            except:
                pass

        except Exception as e:
            st.warning(f"Failed to upload timings: {e}")
            # Still provide download button
            st.download_button(
                "Download timings.csv",
                data=df_times.to_csv(index=False),
                file_name="timings.csv",
                mime="text/csv",
                key="dl_timings_csv_fallback",
            )

# Display architecture info
with st.expander("🏗️ Architecture Info"):
    st.markdown(
        """
    **Cloud Run Jobs Architecture:**
    - **Web Interface**: Cloud Run Service (this app) - 2 CPUs, 4GB RAM
    - **Training Jobs**: Cloud Run Jobs v2 - Up to 32 CPUs, 128GB RAM
    - **Data Storage**: Google Cloud Storage (Parquet format)
    - **Job Orchestration**: Cloud Run Jobs API

    **Benefits:**
    - ✅ Up to 32 CPUs for training (4x more than Cloud Run Services)
    - ✅ 128GB RAM for large datasets
    - ✅ Jobs run independently and scale to zero
    - ✅ Better resource utilization and cost efficiency
    - ✅ Web interface stays responsive during training
    - ✅ Optimized for batch workloads
    """
    )
