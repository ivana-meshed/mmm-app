import json, os, subprocess, tempfile, time, shlex
import streamlit as st
import pandas as pd
import snowflake.connector as sf
from data_processor import DataProcessor  # NEW: Import our data processor

st.set_page_config(page_title="Robyn MMM Trainer", layout="wide")
st.title("Robyn MMM Trainer")

APP_ROOT = os.environ.get("APP_ROOT", "/app")
RSCRIPT  = os.path.join(APP_ROOT, "r", "run_all.R")   # expect /app/r/run_all.R inside the container

# NEW: Initialize data processor
@st.cache_resource
def get_data_processor():
    return DataProcessor()

data_processor = get_data_processor()

# --- Snowflake params
with st.expander("Snowflake connection"):
    sf_user = st.text_input("User", value="IPENC")
    sf_account = st.text_input("Account (e.g. xy12345.europe-west4.gcp)", value="AMXUZTH-AWS_BRIDGE")
    sf_wh = st.text_input("Warehouse", value="SMALL_WH")
    sf_db = st.text_input("Database", value="MESHED_BUYCYCLE")
    sf_schema = st.text_input("Schema", value="GROWTH")
    sf_role = st.text_input("Role", value="ACCOUNTADMIN")
    sf_password = st.text_input("Password", type="password")

# --- Data source 
with st.expander("Data selection"):
    table = st.text_input("Table (DB.SCHEMA.TABLE) — ignored if you supply Query")
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
    paid_media_spends = st.text_input("paid_media_spends (comma-separated)", value="GA_SUPPLY_COST, GA_DEMAND_COST, BING_DEMAND_COST, META_DEMAND_COST, TV_COST, PARTNERSHIP_COSTS")
    paid_media_vars   = st.text_input("paid_media_vars (comma-separated; 1:1 with spends)",  value="GA_SUPPLY_COST, GA_DEMAND_COST, BING_DEMAND_COST, META_DEMAND_COST, TV_COST, PARTNERSHIP_COSTS")
    context_vars      = st.text_input("context_vars (comma-separated)", value="IS_WEEKEND,TV_IS_ON")
    factor_vars       = st.text_input("factor_vars (comma-separated)", value="IS_WEEKEND,TV_IS_ON")
    organic_vars      = st.text_input("organic_vars (comma-separated)", value="ORGANIC_TRAFFIC")

# --- GCS / annotations
with st.expander("Outputs"):
    gcs_bucket = st.text_input("GCS bucket for outputs", value="mmm-app-output")
    ann_file = st.file_uploader("Optional: enriched_annotations.csv", type=["csv"])

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

def build_job_json(tmp_dir, csv_path=None, parquet_path=None, annotations_path=None):
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
        "paid_media_spends": [s.strip() for s in paid_media_spends.split(",") if s.strip()],
        "paid_media_vars": [s.strip() for s in paid_media_vars.split(",") if s.strip()],
        "context_vars": [s.strip() for s in context_vars.split(",") if s.strip()],
        "factor_vars": [s.strip() for s in factor_vars.split(",") if s.strip()],
        "organic_vars": [s.strip() for s in organic_vars.split(",") if s.strip()],
        "snowflake": {
            "user": sf_user,
            "password": None,
            "account": sf_account,
            "warehouse": sf_wh,
            "database": sf_db,
            "schema": sf_schema,
            "role": sf_role
        },
        "annotations_csv": annotations_path,
        "cache_snapshot": True,
        # NEW: Performance flags
        "use_parquet": True,
        "parallel_processing": True
    }
    job_path = os.path.join(tmp_dir, "job.json")
    with open(job_path, "w") as f:
        json.dump(job, f)
    return job_path

if st.button("Train"):
    if not os.path.isfile(RSCRIPT):
        st.error(f"R script not found at: {RSCRIPT}")
    else:
        with st.spinner("Training… this may take a few minutes."):
            with tempfile.TemporaryDirectory() as td:
                # 1) Query data from Snowflake
                sql = effective_sql()
                csv_path = None
                parquet_path = None
                
                if sql:
                    if not sf_password:
                        st.error("Password is required to pull data from Snowflake.")
                        st.stop()
                    try:
                        st.write("Querying Snowflake…")
                        df = run_sql(sql)
                        
                        # NEW: Create both CSV (for compatibility) and Parquet (for speed)
                        csv_path = os.path.join(td, "input_snapshot.csv")
                        parquet_path = os.path.join(td, "input_snapshot.parquet")
                        
                        # Save CSV for backward compatibility
                        df.to_csv(csv_path, index=False)
                        
                        # NEW: Create optimized Parquet file
                        st.write("Optimizing data format (CSV → Parquet)...")
                        parquet_buffer = data_processor.csv_to_parquet(df, parquet_path)
                        
                        # Show optimization results
                        csv_size = os.path.getsize(csv_path) / 1024**2
                        parquet_size = os.path.getsize(parquet_path) / 1024**2
                        compression_ratio = (1 - parquet_size / csv_size) * 100
                        
                        st.success(f"Data optimization complete:")
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
                    annotations_path = os.path.join(td, "enriched_annotations.csv")
                    with open(annotations_path, "wb") as f:
                        f.write(ann_file.read())

                # 3) Build job.json with both CSV and Parquet paths
                job_cfg = build_job_json(
                    td, 
                    csv_path=csv_path, 
                    parquet_path=parquet_path,  # NEW: Pass Parquet path
                    annotations_path=annotations_path
                )

                # 4) Set environment variables for performance
                env = os.environ.copy()
                if sf_password:
                    env["SNOWFLAKE_PASSWORD"] = sf_password
                
                # NEW: Performance environment variables
                env["R_MAX_CORES"] = str(os.cpu_count() or 4)
                env["OMP_NUM_THREADS"] = str(os.cpu_count() or 4)
                env["OPENBLAS_NUM_THREADS"] = str(os.cpu_count() or 4)

                cmd = ["Rscript", RSCRIPT, f"job_cfg={job_cfg}"]

                # 5) Execute training
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=env,
                )

                # Store results for download
                st.session_state["train_log_text"] = result.stdout or "(no output)"
                st.session_state["train_exit_code"] = int(result.returncode)

        # Show results
        if result.returncode == 0:
            st.success("Training finished. Artifacts should be in your GCS bucket.")
        else:
            st.error("Training failed. Download the run log for details.")

        st.download_button(
            "Download training log",
            data=result.stdout or "(no output)",
            file_name="robyn_run.log",
            mime="text/plain",
            key="dl_robyn_run_log",
        )
if "train_exit_code" in st.session_state:
    ok = (st.session_state["train_exit_code"] == 0)
    if ok:
        st.success("Training finished. Artifacts should be in your GCS bucket.")
    else:
        st.error("Training failed. Download the run log for details.")

    # Show tail and a downloadable file
    log_text = st.session_state.get("train_log_text", "(no output)")
    with st.expander("Show last 200 lines of training log"):
        tail = "\n".join(log_text.splitlines()[-200:])
        st.code(tail or "(empty)", language="bash")

    st.download_button(
        "Download training log",
        data=log_text.encode("utf-8"),
        file_name="robyn_run.log",
        mime="text/plain",
        key="dl_robyn_run_log_persisted",
    )