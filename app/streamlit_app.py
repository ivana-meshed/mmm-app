import json
import logging
import os
import shlex
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime

import pandas as pd
import snowflake.connector as sf
import streamlit as st
from data_processor import DataProcessor  # NEW: Import our data processor

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

query_params = st.query_params
logger.info(
    "Starting app/streamlit_app.py", extra={"query_params": query_params}
)

if query_params.get("health") == "true":
    try:
        # Import your health checker
        from health import health_checker

        # Get health status
        health_status = health_checker.check_container_health()

        # Return JSON response for API consumers
        st.json(health_status)
        st.stop()

    except Exception as e:
        # Simple fallback
        st.json(
            {
                "status": "error",
                "timestamp": datetime.now().isoformat(),
                "error": str(e),
            }
        )
        st.stop()

# Check for API requests
if st.query_params.get("api") == "train":
    # This is a training API request
    try:
        # In a real API, you'd get data from request body
        # For now, we'll use query parameters
        api_data = {
            "country": st.query_params.get("country", "test"),
            "iterations": int(st.query_params.get("iterations", "50")),
            "trials": int(st.query_params.get("trials", "2")),
            "job_id": st.query_params.get("job_id", f"api-{int(time.time())}"),
            "paid_media_spends": ["GA_SUPPLY_COST", "GA_DEMAND_COST"],
            "paid_media_vars": ["GA_SUPPLY_COST", "GA_DEMAND_COST"],
            "context_vars": ["IS_WEEKEND"],
            "factor_vars": ["IS_WEEKEND"],
            "organic_vars": ["ORGANIC_TRAFFIC"],
        }

        st.session_state.api_request_data = api_data
        handle_train_api()

    except Exception as e:
        st.json({"status": "error", "error": str(e)})
        st.stop()

st.set_page_config(page_title="Robyn MMM Trainer", layout="wide")


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


def upload_to_gcs(bucket_name: str, local_path: str, dest_blob: str) -> str:
    """Upload a local file to GCS and return the gs:// URI."""
    try:
        from google.cloud import (  # ensure 'google-cloud-storage' is in requirements.txt
            storage,
        )
    except ImportError as e:
        raise RuntimeError(
            "google-cloud-storage not installed in the image"
        ) from e

    client = storage.Client()  # uses Cloud Run default creds
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(dest_blob)
    blob.upload_from_filename(local_path)
    return f"gs://{bucket_name}/{dest_blob}"


# Debug info in sidebar
with st.sidebar:
    st.subheader("🔧 Debug Info")
    st.write(f"**Container CPU Count**: {os.cpu_count()}")
    st.write(f"**R_MAX_CORES**: {os.getenv('R_MAX_CORES', 'Not set')}")
    st.write(f"**OMP_NUM_THREADS**: {os.getenv('OMP_NUM_THREADS', 'Not set')}")
    st.write(f"**GCS_BUCKET**: {os.getenv('GCS_BUCKET', 'Not set')}")

    # Memory info
    try:
        import psutil

        memory = psutil.virtual_memory()
        st.write(f"**Available Memory**: {memory.available / 1024**3:.1f} GB")
        st.write(f"**Memory Usage**: {memory.percent:.1f}%")
    except ImportError:
        st.write("**Memory Info**: psutil not available")

st.title("Robyn MMM Trainer")


# Add error handling wrapper
def safe_execute(func, error_msg="Operation failed"):
    """Execute function with error handling"""
    try:
        return func()
    except Exception as e:
        st.error(f"{error_msg}: {str(e)}")
        logger.error(f"{error_msg}: {str(e)}", exc_info=True)
        return None


APP_ROOT = os.environ.get("APP_ROOT", "/app")
RSCRIPT = os.path.join(
    APP_ROOT, "r", "run_all.R"
)  # expect /app/r/run_all.R inside the container


# NEW: Initialize data processor
@st.cache_resource
def get_data_processor():
    return DataProcessor()


data_processor = get_data_processor()

# --- Snowflake params
with st.expander("Snowflake connection"):
    sf_user = st.text_input("User", value="IPENC")
    sf_account = st.text_input(
        "Account (e.g. xy12345.europe-west4.gcp)", value="AMXUZTH-AWS_BRIDGE"
    )
    sf_wh = st.text_input("Warehouse", value="SMALL_WH")
    sf_db = st.text_input("Database", value="MESHED_BUYCYCLE")
    sf_schema = st.text_input("Schema", value="GROWTH")
    sf_role = st.text_input("Role", value="ACCOUNTADMIN")
    sf_password = st.text_input("Password", type="password")

# --- Data source
with st.expander("Data selection"):
    table = st.text_input(
        "Table (DB.SCHEMA.TABLE) — ignored if you supply Query"
    )
    query = st.text_area("Custom SQL (optional)")

# --- Robyn knobs
with st.expander("Robyn configuration"):
    country = st.text_input("Country", value="fr")
    iterations = st.number_input("Iterations", value=200, min_value=50)
    trials = st.number_input("Trials", value=5, min_value=1)
    train_size = st.text_input("Train size (e.g. 0.7,0.9)", value="0.7,0.9")
    revision = st.text_input("Revision tag", value="r100")
    date_input = st.text_input("Date tag", value=time.strftime("%Y-%m-%d"))

# --- Variables (user will paste/select from Snowflake columns)
with st.expander("Variable mapping"):
    paid_media_spends = st.text_input(
        "paid_media_spends (comma-separated)",
        value="GA_SUPPLY_COST, GA_DEMAND_COST, BING_DEMAND_COST, META_DEMAND_COST, TV_COST, PARTNERSHIP_COSTS",
    )
    paid_media_vars = st.text_input(
        "paid_media_vars (comma-separated; 1:1 with spends)",
        value="GA_SUPPLY_COST, GA_DEMAND_COST, BING_DEMAND_COST, META_DEMAND_COST, TV_COST, PARTNERSHIP_COSTS",
    )
    context_vars = st.text_input(
        "context_vars (comma-separated)", value="IS_WEEKEND,TV_IS_ON"
    )
    factor_vars = st.text_input(
        "factor_vars (comma-separated)", value="IS_WEEKEND,TV_IS_ON"
    )
    organic_vars = st.text_input(
        "organic_vars (comma-separated)", value="ORGANIC_TRAFFIC"
    )

# --- GCS / annotations
with st.expander("Outputs"):
    gcs_bucket = st.text_input("GCS bucket for outputs", value="mmm-app-output")
    ann_file = st.file_uploader(
        "Optional: enriched_annotations.csv", type=["csv"]
    )


def parse_train_size(txt: str):
    try:
        vals = [float(x.strip()) for x in txt.split(",") if x.strip() != ""]
        if len(vals) == 2:
            return vals
    except Exception:
        pass
    return [0.7, 0.9]


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


# Quick connection test & preview
if st.button("Test connection & preview 5 rows"):
    sql = effective_sql()
    if not sql:
        st.warning("Provide a table or a SQL query.")
    elif not sf_password:
        st.error("Password is required to connect.")
    else:
        try:
            # Wrap the user SQL to limit rows (works for most selects)
            preview_sql = f"SELECT * FROM ({sql}) t LIMIT 5"
            df_prev = run_sql(preview_sql)
            st.success("Connection OK")
            st.dataframe(df_prev)
        except Exception as e:
            st.error(f"Preview failed: {e}")


def build_job_json(
    tmp_dir,
    csv_path=None,
    parquet_path=None,
    annotations_path=None,
    timestamp=None,
):
    """Updated to support both CSV and Parquet paths"""
    job = {
        "country": country,
        "iterations": int(iterations),
        "trials": int(trials),
        "train_size": parse_train_size(train_size),
        "revision": revision,
        "date_input": date_input,
        "gcs_bucket": gcs_bucket,
        "table": table,
        "query": query,
        # NEW: Support both formats
        "csv_path": csv_path,
        "parquet_path": parquet_path,  # NEW: Parquet path for faster loading
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
        "snowflake": {
            "user": sf_user,
            "password": None,
            "account": sf_account,
            "warehouse": sf_wh,
            "database": sf_db,
            "schema": sf_schema,
            "role": sf_role,
        },
        "annotations_csv": annotations_path,
        "cache_snapshot": True,
        # NEW: Performance flags
        "use_parquet": True,
        "parallel_processing": True,
        "timestamp": timestamp,
    }
    job_path = os.path.join(tmp_dir, "job.json")
    with open(job_path, "w") as f:
        json.dump(job, f)
    return job_path


if st.button("Train"):
    # fresh run: clear any previous timings
    timings = []

    if not os.path.isfile(RSCRIPT):
        st.error(f"R script not found at: {RSCRIPT}")
    else:
        with st.spinner("Training… this may take a few minutes."):
            with tempfile.TemporaryDirectory() as td:
                # 1) Query data from Snowflake (optional)
                sql = effective_sql()
                csv_path = None
                parquet_path = None

                if sql:
                    if not sf_password:
                        st.error(
                            "Password is required to pull data from Snowflake."
                        )
                        st.stop()
                    try:
                        with timed_step("Query Snowflake", timings):
                            df = run_sql(sql)

                        # Save CSV for compatibility
                        with timed_step("Write CSV snapshot", timings):
                            csv_path = os.path.join(td, "input_snapshot.csv")
                            df.to_csv(csv_path, index=False)

                        # Convert to Parquet (optimized)
                        with timed_step("Convert CSV → Parquet", timings):
                            parquet_path = os.path.join(
                                td, "input_snapshot.parquet"
                            )
                            parquet_buffer = data_processor.csv_to_parquet(
                                df, parquet_path
                            )

                        # Compute and display format stats (tiny but timed)
                        with timed_step("Compute snapshot stats", timings):
                            csv_size = os.path.getsize(csv_path) / 1024**2
                            parquet_size = (
                                os.path.getsize(parquet_path) / 1024**2
                            )
                            compression_ratio = (
                                1 - parquet_size / csv_size
                            ) * 100

                        st.success("Data optimization complete:")
                        st.write(f"- Original CSV: {csv_size:.1f} MB")
                        st.write(f"- Optimized Parquet: {parquet_size:.1f} MB")
                        st.write(f"- Size reduction: {compression_ratio:.1f}%")
                        st.write(f"- Pulled {len(df):,} rows from Snowflake")

                    except Exception as e:
                        st.error(f"Query failed: {e}")
                        st.stop()

                # 2) Optional annotations upload
                annotations_path = None
                if ann_file is not None:
                    with timed_step("Read uploaded annotations", timings):
                        annotations_path = os.path.join(
                            td, "enriched_annotations.csv"
                        )
                        with open(annotations_path, "wb") as f:
                            f.write(ann_file.read())

                # 3) Build job.json with both CSV and Parquet paths
                with timed_step("Build job.json", timings):
                    timestamp = datetime.utcnow().strftime("%m%d_%H%M%S")
                    job_cfg = build_job_json(
                        td,
                        csv_path=csv_path,
                        parquet_path=parquet_path,
                        annotations_path=annotations_path,
                        timestamp=timestamp,
                    )

                # 4) Prepare environment for training
                with timed_step("Prepare training environment", timings):
                    env = os.environ.copy()
                    if sf_password:
                        env["SNOWFLAKE_PASSWORD"] = sf_password
                    env["R_MAX_CORES"] = str(os.cpu_count() or 8)
                    env["OMP_NUM_THREADS"] = str(os.cpu_count() or 8)
                    env["OPENBLAS_NUM_THREADS"] = str(os.cpu_count() or 8)
                    cmd = ["Rscript", RSCRIPT, f"job_cfg={job_cfg}"]

                # 5) Execute training (Rscript)
                with timed_step("Run Rscript (Robyn training)", timings):
                    result = subprocess.run(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        env=env,
                    )
                # Store results for download
                st.session_state["train_log_text"] = (
                    result.stdout or "(no output)"
                )
                st.session_state["train_exit_code"] = int(result.returncode)

        # Show results
        if result.returncode == 0:
            st.success(
                "Training finished. Artifacts should be in your GCS bucket."
            )
        else:
            st.error("Training failed. Download the run log for details.")

        st.download_button(
            "Download training log",
            data=result.stdout or "(no output)",
            file_name="robyn_run.log",
            mime="text/plain",
            key="dl_robyn_run_log",
        )

        # 🔎 Execution time summary
        if timings:
            df_times = pd.DataFrame(timings)
            total = float(df_times["Time (s)"].sum())
            df_times["% of total"] = (df_times["Time (s)"] / total * 100).round(
                1
            )

            with st.expander("⏱️ Execution timeline & totals", expanded=True):
                st.dataframe(df_times, use_container_width=True)
                st.write(f"**Total elapsed:** {_fmt_secs(total)}")

            gcs_prefix = f"robyn/{revision}/{country}/{timestamp}"

            # Save to a guaranteed existing temp file
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".csv", delete=False
            ) as tmp:
                df_times.to_csv(tmp.name, index=False)
                timings_csv_local = tmp.name  # keep path to upload afterwards

            # Upload to the same prefix as R
            dest_blob = f"{gcs_prefix}/timings.csv"
            gcs_uri = upload_to_gcs(gcs_bucket, timings_csv_local, dest_blob)

            st.success(f"Timings CSV uploaded to **{gcs_uri}**")
            st.download_button(
                "Download timings.csv",
                data=df_times.to_csv(index=False),
                file_name="timings.csv",
                mime="text/csv",
                key="dl_timings_csv",
            )
