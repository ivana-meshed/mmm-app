
# pages/0_Single_Training.py — Single training run
import os, io, json, time, tempfile
from datetime import datetime
import pandas as pd
import streamlit as st
from google.cloud import storage

from app_shared import (
    PROJECT_ID, REGION, TRAINING_JOB_NAME,
    get_data_processor, get_job_manager,
    effective_sql, timed_step, upload_to_gcs, build_job_config_from_params, run_sql
)

data_processor = get_data_processor()
job_manager = get_job_manager()

st.set_page_config(page_title="Robyn — Single Training", layout="wide")
st.title("🎯 Single Training Run")

with st.expander("Robyn configuration", expanded=True):
    country = st.text_input("Country", value="fr")
    iterations = st.number_input("Iterations", value=200, min_value=50)
    trials = st.number_input("Trials", value=5, min_value=1)
    train_size = st.text_input("Train size", value="0.7,0.9")

with st.expander("Variable mapping", expanded=True):
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
    dep_var = st.text_input("dep_var (target/response column)", value="UPLOAD_VALUE")
    date_var = st.text_input("date_var (date column name in data)", value="DATE")
    adstock = st.selectbox("adstock", options=["geometric","weibull_cdf"], index=0)

with st.expander("Data source", expanded=True):
    revision = st.text_input("Revision", value="v1")
    date_input = st.text_input("date_input (string label)", value=time.strftime("%Y-%m-%d"))
    table = st.text_input("Table (preferred)", value="")
    sql = st.text_area("SQL (optional; overrides table if provided)", value="")

with st.expander("Outputs"):
    gcs_bucket = st.text_input("GCS bucket for outputs", value=os.getenv("GCS_BUCKET","mmm-app-output"))

if st.button("🚀 Launch single training"):
    params = {
        "country": country,
        "revision": revision,
        "date_input": date_input,
        "iterations": int(iterations),
        "trials": int(trials),
        "train_size": str(train_size),
        "paid_media_spends": paid_media_spends,
        "paid_media_vars": paid_media_vars,
        "context_vars": context_vars,
        "factor_vars": factor_vars,
        "organic_vars": organic_vars,
        "dep_var": dep_var,
        "date_var": date_var,
        "adstock": adstock,
        "table": table.strip(),
        "query": sql.strip(),
        "gcs_bucket": gcs_bucket,
    }
    sql_eff = params.get("query") or effective_sql(params.get("table",""), params.get("query",""))
    if not sql_eff:
        st.error("Provide a table or SQL query to prepare training data.")
        st.stop()

    timestamp = datetime.utcnow().strftime("%m%d_%H%M%S")
    with tempfile.TemporaryDirectory() as td:
        timings = []
        # 1) Query
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
        # 4) Optional annotations
        annotations_gcs_path = None
        # 5) Config
        with timed_step("Create job configuration", timings):
            job_config = build_job_config_from_params(params, data_gcs_path, timestamp, annotations_gcs_path)
            cfg_path = os.path.join(td, "job_config.json")
            with open(cfg_path,"w") as f: json.dump(job_config, f, indent=2)
            latest_obj = f"training-configs/latest/job_config.json"
            upload_to_gcs(gcs_bucket, cfg_path, latest_obj)
        # 6) Launch
        with timed_step("Launch training job", timings):
            execution_name = job_manager.create_execution(TRAINING_JOB_NAME)

        st.success(f"Launched: {execution_name}")
        # Seed timings as in main app
        if timings:
            df_times = pd.DataFrame(timings)
            dest_blob = f"robyn/{params['revision']}/{params['country']}/{timestamp}/timings.csv"
            tmp = os.path.join(td, "timings.csv")
            df_times.to_csv(tmp, index=False)
            upload_to_gcs(gcs_bucket, tmp, dest_blob)
    st.info("Training runs asynchronously in Cloud Run Jobs.")
