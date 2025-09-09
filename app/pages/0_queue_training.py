
# pages/0_Queue_Training.py — Queue training
import os, io, json, time
from datetime import datetime
import pandas as pd
import streamlit as st
from google.cloud import storage

from app_shared import (
    PROJECT_ID, REGION, TRAINING_JOB_NAME, GCS_BUCKET,
    get_data_processor, get_job_manager, effective_sql, timed_step,
    upload_to_gcs, build_job_config_from_params, run_sql,
    load_queue_from_gcs, save_queue_to_gcs, queue_tick_once_headless,
    _sanitize_queue_name, _queue_blob_path
)

data_processor = get_data_processor()
job_manager = get_job_manager()

st.set_page_config(page_title="Robyn — Queue Training", layout="wide")
st.title("🗂️ Queue Training")

st.session_state.setdefault("queue_name", os.getenv("DEFAULT_QUEUE_NAME","default"))
st.session_state.setdefault("job_queue", [])
st.session_state.setdefault("queue_running", False)
st.session_state.setdefault("gcs_bucket", os.getenv("GCS_BUCKET","mmm-app-output"))

# Load current queue
col1, col2 = st.columns([3,1])
with col1:
    st.text_input("Queue name", key="queue_name")
with col2:
    if st.button("🔄 Load queue from GCS"):
        _ = load_queue_from_gcs(st.session_state.queue_name, st.session_state["gcs_bucket"])
        st.success("Queue loaded.")
        st.rerun()

# Quick controls
qc1, qc2, qc3 = st.columns(3)
if qc1.button("▶️ Start Queue", disabled=(not st.session_state.job_queue)):
    st.session_state.queue_running = True
    st.session_state.queue_saved_at = save_queue_to_gcs(
        st.session_state.queue_name, st.session_state.job_queue, queue_running=True
    )
    st.success("Queue set to RUNNING.")
if qc2.button("⏸️ Stop Queue"):
    st.session_state.queue_running = False
    st.session_state.queue_saved_at = save_queue_to_gcs(
        st.session_state.queue_name, st.session_state.job_queue, queue_running=False
    )
    st.info("Queue paused.")
if qc3.button("⏭️ Process Next Step"):
    res = queue_tick_once_headless(st.session_state.queue_name, st.session_state["gcs_bucket"])
    st.write(res or {})
    st.rerun()

st.divider()
st.subheader("➕ Add a single job to the queue")
with st.form("add_to_queue_form"):
    country = st.text_input("Country", value="fr")
    revision = st.text_input("Revision", value="v1")
    date_input = st.text_input("date_input", value=time.strftime("%Y-%m-%d"))
    table = st.text_input("Table", value="")
    sql = st.text_area("SQL (optional; overrides table if provided)", value="")
    iterations = st.number_input("Iterations", value=200, min_value=1)
    trials = st.number_input("Trials", value=5, min_value=1)
    train_size = st.text_input("Train size", value="0.7,0.9")
    paid_media_spends = st.text_input("paid_media_spends", value="GA_SUPPLY_COST,GA_DEMAND_COST,BING_DEMAND_COST,META_DEMAND_COST,TV_COST,PARTNERSHIP_COSTS")
    paid_media_vars = st.text_input("paid_media_vars", value="GA_SUPPLY_COST,GA_DEMAND_COST,BING_DEMAND_COST,META_DEMAND_COST,TV_COST,PARTNERSHIP_COSTS")
    context_vars = st.text_input("context_vars", value="IS_WEEKEND,TV_IS_ON")
    factor_vars = st.text_input("factor_vars", value="IS_WEEKEND,TV_IS_ON")
    organic_vars = st.text_input("organic_vars", value="ORGANIC_TRAFFIC")
    dep_var = st.text_input("dep_var", value="UPLOAD_VALUE")
    date_var = st.text_input("date_var", value="DATE")
    adstock = st.selectbox("adstock", options=["geometric","weibull_cdf"], index=0)
    submitted = st.form_submit_button("Add to queue")
    if submitted:
        params = {
            "country": country, "revision": revision, "date_input": date_input,
            "iterations": int(iterations), "trials": int(trials), "train_size": str(train_size),
            "paid_media_spends": paid_media_spends, "paid_media_vars": paid_media_vars,
            "context_vars": context_vars, "factor_vars": factor_vars, "organic_vars": organic_vars,
            "dep_var": dep_var, "date_var": date_var, "adstock": adstock,
            "table": table.strip(), "query": sql.strip(), "gcs_bucket": st.session_state["gcs_bucket"]
        }
        if not (params["table"] or params["query"]):
            st.error("Provide a table or SQL.")
        else:
            next_id = max([e["id"] for e in st.session_state.job_queue], default=0) + 1
            st.session_state.job_queue.append({
                "id": next_id, "params": params, "status":"PENDING",
                "timestamp": None, "execution_name": None, "gcs_prefix": None, "message":""
            })
            st.session_state.queue_saved_at = save_queue_to_gcs(
                st.session_state.queue_name, st.session_state.job_queue, queue_running=st.session_state.queue_running
            )
            st.success(f"Added job {next_id} to queue.")
            st.rerun()

st.subheader("Queue")
if st.session_state.job_queue:
    df = pd.DataFrame([{
        "ID": e["id"], "Status": e["status"],
        "Country": e["params"].get("country",""),
        "Revision": e["params"].get("revision",""),
        "Timestamp": e.get("timestamp",""),
        "Exec": (e.get("execution_name") or "").split("/")[-1],
        "Msg": e.get("message",""),
    } for e in st.session_state.job_queue])
    edited = st.data_editor(df, num_rows="dynamic", hide_index=True, use_container_width=True)
    if st.button("💾 Save edits"):
        by_id = {e["id"]: e for e in st.session_state.job_queue}
        max_id = max(by_id.keys(), default=0)
        new_q = []
        for _, row in edited.iterrows():
            rid = row.get("ID")
            if pd.isna(rid):
                max_id += 1
                rid = max_id
                base = {"status":"PENDING", "timestamp":None, "execution_name":None, "gcs_prefix":None, "message":""}
                params = {}
            else:
                rid = int(rid)
                base = by_id.get(rid, {})
                params = base.get("params", {})
            params["country"] = str(row.get("Country") or params.get("country",""))
            params["revision"] = str(row.get("Revision") or params.get("revision",""))
            new_q.append({
                "id": int(rid), "params": params,
                "status": base.get("status","PENDING"),
                "timestamp": base.get("timestamp"),
                "execution_name": base.get("execution_name"),
                "gcs_prefix": base.get("gcs_prefix"),
                "message": base.get("message",""),
            })
        st.session_state.job_queue = new_q
        st.session_state.queue_saved_at = save_queue_to_gcs(st.session_state.queue_name, new_q, queue_running=st.session_state.queue_running)
        st.success("Queue saved.")
        st.rerun()
else:
    st.info("Queue empty.")
