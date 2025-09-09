# streamlit_app.py — Streamlit front-end for launching & monitoring Robyn training jobs on Cloud Run Jobs
import json
import logging
import os
import time
import tempfile
from datetime import datetime
from typing import Dict, Any, Optional, List

import pandas as pd
import snowflake.connector as sf
import streamlit as st
from google.cloud import storage

from app_shared import (
    # Env / constants (already read from env in app_shared)
    PROJECT_ID,
    REGION,
    TRAINING_JOB_NAME,
    GCS_BUCKET,
    DEFAULT_QUEUE_NAME,
    SAFE_LAG_SECONDS_AFTER_RUNNING,
    # Helpers
    timed_step,
    parse_train_size,
    effective_sql,
    _sf_params_from_env,
    ensure_sf_conn,
    run_sql,
    upload_to_gcs,
    read_status_json,  # (kept for parity; not used below)
    build_job_config_from_params,
    _sanitize_queue_name,  # (kept for parity; not used below)
    _queue_blob_path,  # (kept for parity; not used below)
    load_queue_from_gcs,
    save_queue_to_gcs,
    load_queue_payload,
    queue_tick_once_headless,
    handle_queue_tick_from_query_params,
    get_job_manager,
    get_data_processor,
    _fmt_secs,
    _connect_snowflake,  # use shared connector for consistency with ensure_sf_conn
    read_ledger_from_gcs,
    save_ledger_to_gcs,
    append_row_to_ledger,
)

# Instantiate shared resources
data_processor = get_data_processor()
job_manager = get_job_manager()

TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED", "COMPLETED", "ERROR"}

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
# Session defaults
# ─────────────────────────────
st.session_state.setdefault("job_executions", [])
st.session_state.setdefault("gcs_bucket", GCS_BUCKET)
st.session_state.setdefault("last_timings", None)
st.session_state.setdefault("auto_refresh", False)

# Persistent Snowflake session objects/params
st.session_state.setdefault("sf_params", None)
st.session_state.setdefault("sf_connected", False)
st.session_state.setdefault("sf_conn", None)

# Batch queue state
st.session_state.setdefault("job_queue", [])  # list of dicts
st.session_state.setdefault("queue_running", False)

# Persistent queue session vars
st.session_state.setdefault("queue_name", DEFAULT_QUEUE_NAME)
st.session_state.setdefault("queue_loaded_from_gcs", False)
st.session_state.setdefault("queue_saved_at", None)


# ─────────────────────────────
# Launcher used by queue tick
# ─────────────────────────────
def prepare_and_launch_job(params: dict) -> dict:
    """
    One complete job: query SF -> parquet -> upload -> write config (timestamped + latest) -> run Cloud Run Job.
    Returns exec_info dict with execution_name, timestamp, gcs_prefix, etc.
    """
    # 0) Validate & resolve SQL
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

        # Optional annotations (batch: pass a gs:// in params)
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
            # "latest" copy (job reads this)
            _ = upload_to_gcs(
                gcs_bucket,
                config_path,
                "training-configs/latest/job_config.json",
            )

        # 5) Launch job (Cloud Run Jobs)
        with timed_step("Launch training job", timings):
            execution_name = job_manager.create_execution(TRAINING_JOB_NAME)

        # Seed timings.csv (web-side steps) if not present
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

    return {
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


# ─────────────────────────────
# Early stateless tick endpoint (?queue_tick=1)
# ─────────────────────────────
res = handle_queue_tick_from_query_params(
    st.query_params,
    st.session_state.get("gcs_bucket", GCS_BUCKET),
    launcher=prepare_and_launch_job,
)
if isinstance(res, dict) and res:
    st.json(res)
    st.stop()


# ─────────────────────────────
# Small UI helpers
# ─────────────────────────────
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
    dep_var,
    date_var,
    adstock,
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
        "dep_var": dep_var,
        "date_var": date_var,
        "adstock": adstock,
    }


def _empty_ledger_df() -> pd.DataFrame:
    # Matches fields written by run_all.R::append_to_ledger()
    cols = [
        "job_id",
        "exec_name",
        "state",
        "country",
        "revision",
        "date_input",
        "iterations",
        "trials",
        "train_size",
        "dep_var",
        "adstock",
        "start_time",
        "end_time",
        "duration_minutes",
        "gcs_prefix",
        "bucket",
    ]
    return pd.DataFrame(columns=cols)


def render_jobs_ledger(key_prefix: str = "single") -> None:
    """Render the Jobs Ledger editor/viewer (always editable, even if empty)."""
    with st.expander("📚 Jobs Ledger (from GCS)", expanded=False):
        try:
            df_ledger = read_ledger_from_gcs(
                st.session_state.get("gcs_bucket", GCS_BUCKET)
            )
        except Exception as e:
            st.error(f"Failed to read ledger from GCS: {e}")
            df_ledger = None

        if df_ledger is None or df_ledger.empty:
            st.info("Ledger is empty — add a row below and save to create it.")
            df_ledger = _empty_ledger_df()

        st.caption("Append rows directly and click **Save to GCS** to persist.")
        edited = st.data_editor(
            df_ledger,
            num_rows="dynamic",
            use_container_width=True,
            key=f"ledger_editor_{key_prefix}",
        )
        c1, c2 = st.columns(2)
        if c1.button("💾 Save ledger to GCS", key=f"save_ledger_{key_prefix}"):
            try:
                save_ledger_to_gcs(
                    edited, st.session_state.get("gcs_bucket", GCS_BUCKET)
                )
                st.success("Ledger saved to GCS.")
            except Exception as e:
                st.error(f"Failed to save ledger: {e}")
        if c2.button(
            "➕ Append last row to ledger", key=f"append_ledger_{key_prefix}"
        ):
            try:
                if not edited.empty:
                    append_row_to_ledger(
                        edited.iloc[-1].to_dict(),
                        st.session_state.get("gcs_bucket", GCS_BUCKET),
                    )
                    st.success("Appended last row to ledger on GCS.")
            except Exception as e:
                st.error(f"Append failed: {e}")


def render_job_status_monitor(key_prefix: str = "single") -> None:
    """Status UI usable in both tabs, even without a session job."""
    st.subheader("📊 Job Status Monitor")

    # Prefer the latest session execution if present; allow manual input always.
    default_exec = (
        (st.session_state.job_executions[-1]["execution_name"])
        if st.session_state.get("job_executions")
        else ""
    )
    exec_name = st.text_input(
        "Execution resource name (paste one to check any run)",
        value=default_exec,
        key=f"exec_input_{key_prefix}",
    )

    if st.button("🔍 Check Status", key=f"check_status_{key_prefix}"):
        if not exec_name:
            st.warning("Paste an execution resource name to check.")
        else:
            try:
                status_info = job_manager.get_execution_status(exec_name)
                st.json(status_info)
            except Exception as e:
                st.error(f"Status check failed: {e}")

    # Quick results/log viewer driven by the ledger (no execution name required)
    with st.expander("📁 View Results (pick from ledger)", expanded=False):
        try:
            df_led = read_ledger_from_gcs(
                st.session_state.get("gcs_bucket", GCS_BUCKET)
            )
        except Exception as e:
            st.error(f"Failed to read ledger: {e}")
            df_led = None

        if df_led is None or df_led.empty or "gcs_prefix" not in df_led.columns:
            st.info("No ledger entries with results yet.")
        else:
            df_led = df_led.copy()

            # Build readable labels
            def _label(r):
                return f"[{r.get('state','?')}] {r.get('country','?')}/{r.get('revision','?')} · {r.get('gcs_prefix','—')}"

            df_led["__label__"] = df_led.apply(_label, axis=1)
            idx = st.selectbox(
                "Pick a job",
                options=list(df_led.index),
                format_func=lambda i: df_led.loc[i, "__label__"],
                key=f"ledger_pick_{key_prefix}",
            )
            row = df_led.loc[idx]
            bucket_view = row.get(
                "bucket", st.session_state.get("gcs_bucket", GCS_BUCKET)
            )
            gcs_prefix_view = row.get("gcs_prefix")
            if gcs_prefix_view:
                st.info(
                    f"Results location: gs://{bucket_view}/{gcs_prefix_view}/"
                )
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
                            key=f"log_tail_{key_prefix}",
                        )
                        st.download_button(
                            "Download full training log",
                            data=log_bytes,
                            file_name=f"robyn_training_{row.get('job_id','')}.log",
                            mime="text/plain",
                            key=f"dl_log_{key_prefix}",
                        )
                    else:
                        st.info("Training log not yet available for this job.")
                except Exception as e:
                    st.warning(f"Could not fetch training log: {e}")


def set_queue_running(
    queue_name: str, running: bool, bucket_name: Optional[str] = None
) -> None:
    """Toggle the persisted queue_running flag and update session."""
    doc = load_queue_from_gcs(queue_name, bucket_name=bucket_name)
    st.session_state.queue_running = bool(running)
    save_queue_to_gcs(
        queue_name,
        entries=doc.get("entries", []),
        queue_running=running,
        bucket_name=bucket_name,
    )


def maybe_refresh_queue_from_gcs(force: bool = False):
    """Refresh local session state from GCS if remote changed (or force=True)."""
    payload = load_queue_payload(st.session_state.queue_name)
    remote_saved_at = payload.get("saved_at")
    if force or (
        remote_saved_at
        and remote_saved_at != st.session_state.get("queue_saved_at")
    ):
        st.session_state.job_queue = payload.get("entries", [])
        st.session_state.queue_running = payload.get(
            "queue_running", st.session_state.get("queue_running", False)
        )
        st.session_state.queue_saved_at = remote_saved_at


# ─────────────────────────────
# UI layout
# ─────────────────────────────
st.title("Robyn MMM Trainer")
tab_conn, tab_single, tab_queue = st.tabs(
    ["1) Snowflake Connection", "2) Single Job Training", "3) Queue Training"]
)


# Auto-load persisted queue once per session (per queue_name)
if not st.session_state.queue_loaded_from_gcs:
    try:
        payload = load_queue_from_gcs(st.session_state.queue_name)
        st.session_state.job_queue = payload.get("entries", [])
        st.session_state.queue_running = payload.get("queue_running", True)
        st.session_state.queue_saved_at = payload.get("saved_at")
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
                "User",
                value=(st.session_state.sf_params or {}).get("user", "")
                or os.getenv("SF_USER"),
            )
            sf_account = st.text_input(
                "Account",
                value=(st.session_state.sf_params or {}).get("account", "")
                or os.getenv("SF_ACCOUNT"),
            )
            sf_wh = st.text_input(
                "Warehouse",
                value=(st.session_state.sf_params or {}).get("warehouse", "")
                or os.getenv("SF_WAREHOUSE"),
            )
            sf_db = st.text_input(
                "Database",
                value=(st.session_state.sf_params or {}).get("database", "")
                or os.getenv("SF_DATABASE"),
            )
        with c2:
            sf_schema = st.text_input(
                "Schema",
                value=(st.session_state.sf_params or {}).get("schema", "")
                or os.getenv("SF_SCHEMA"),
            )
            sf_role = st.text_input(
                "Role",
                value=(st.session_state.sf_params or {}).get("role", "")
                or os.getenv("SF_ROLE"),
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
with tab_single:
    st.subheader("Robyn configuration & training")
    if not st.session_state.sf_connected:
        st.warning("Please connect to Snowflake in tab 1 first.")
    if st.session_state.sf_connected:
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
            date_input = st.text_input(
                "Date tag", value=time.strftime("%Y-%m-%d")
            )
            dep_var = st.text_input(
                "dep_var",
                value="UPLOAD_VALUE",
                help="Dependent variable column in your data (e.g., UPLOAD_VALUE)",
            )
            date_var = st.text_input(
                "date_var",
                value="date",
                help="Date column in your data (e.g., date)",
            )
            adstock = st.selectbox(
                "adstock",
                options=["geometric", "weibull_cdf", "weibull_pdf"],
                index=0,
                help="Robyn adstock function",
            )

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
            factor_vars = st.text_input(
                "factor_vars", value="IS_WEEKEND,TV_IS_ON"
            )
            organic_vars = st.text_input(
                "organic_vars", value="ORGANIC_TRAFFIC"
            )

        # Outputs
        with st.expander("Outputs"):
            gcs_bucket = st.text_input(
                "GCS bucket for outputs", value=st.session_state["gcs_bucket"]
            )
            st.session_state["gcs_bucket"] = gcs_bucket
            ann_file = st.file_uploader(
                "Optional: enriched_annotations.csv", type=["csv"]
            )

        # =============== Single-run button ===============
        def create_job_config_single(
            data_gcs_path: str,
            timestamp: str,
            annotations_gcs_path: Optional[str],
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
                    dep_var,
                    date_var,
                    adstock,
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
            timings: List[Dict[str, float]] = []

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
                            parquet_path = os.path.join(
                                td, "input_data.parquet"
                            )
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
                            with timed_step(
                                "Upload annotations to GCS", timings
                            ):
                                annotations_path = os.path.join(
                                    td, "enriched_annotations.csv"
                                )
                                with open(annotations_path, "wb") as f:
                                    f.write(ann_file.read())
                                annotations_blob = f"training-data/{timestamp}/enriched_annotations.csv"
                                annotations_gcs_path = upload_to_gcs(
                                    gcs_bucket,
                                    annotations_path,
                                    annotations_blob,
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

        render_jobs_ledger(key_prefix="single")
        render_job_status_monitor(key_prefix="single")

    # ===================== BATCH QUEUE (CSV) =====================
with tab_queue:
    st.subheader(
        "Batch queue (CSV) — queue & run multiple jobs sequentially",
    )
    with st.expander(
        "📚 Batch queue (CSV) — queue & run multiple jobs sequentially",
        expanded=False,
    ):
        maybe_refresh_queue_from_gcs()
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
            payload = load_queue_payload(st.session_state.queue_name)
            st.session_state.job_queue = payload["entries"]
            st.session_state.queue_running = payload.get("queue_running", False)
            st.session_state.queue_saved_at = payload.get("saved_at")
            st.success(f"Loaded queue '{st.session_state.queue_name}' from GCS")

        if cqn3.button("⬆️ Save to GCS"):
            st.session_state.queue_saved_at = save_queue_to_gcs(
                st.session_state.queue_name,
                st.session_state.job_queue,
                queue_running=st.session_state.queue_running,
            )
            st.success(f"Saved queue '{st.session_state.queue_name}' to GCS")

        st.markdown(
            """
Upload a CSV where each row defines a training run. **Supported columns** (all optional except `country`, `revision`, and data source):

- `country`, `revision`, `date_input`, `iterations`, `trials`, `train_size`
- `paid_media_spends`, `paid_media_vars`, `context_vars`, `factor_vars`, `organic_vars`
- `dep_var`, `date_var`, `adstock`
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
                    "query": "",
                    "dep_var": "UPLOAD_VALUE",
                    "date_var": "date",
                    "adstock": "geometric",
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
                    "dep_var": "UPLOAD_VALUE",
                    "date_var": "date",
                    "adstock": "geometric",
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
                "dep_var": str(_g("dep_var", dep_var)),
                "date_var": str(_g("date_var", date_var)),
                "adstock": str(_g("adstock", adstock)),
                "annotations_gcs_path": str(_g("annotations_gcs_path", "")),
            }

        c_left, c_right = st.columns(2)
        if c_left.button("➕ Enqueue all rows", disabled=(parsed_df is None)):
            if parsed_df is not None:
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

        if st.button("🔁 Refresh from GCS"):
            _ = load_queue_from_gcs(st.session_state.queue_name)
            st.success("Refreshed from GCS.")
            st.rerun()

        qc1, qc2, qc3, qc4 = st.columns(4)
        if qc1.button(
            "▶️ Start Queue", disabled=(len(st.session_state.job_queue) == 0)
        ):
            set_queue_running(st.session_state.queue_name, True)
            st.success("Queue set to RUNNING.")
            st.rerun()
        if qc2.button("⏸️ Stop Queue"):
            set_queue_running(st.session_state.queue_name, False)
            st.info("Queue paused.")
            st.rerun()
        if qc3.button("⏭️ Process Next Step"):
            res = queue_tick_once_headless(
                st.session_state.queue_name,
                st.session_state["gcs_bucket"],
                launcher=prepare_and_launch_job,
            )
            st.toast(res.get("message", "tick"))
            maybe_refresh_queue_from_gcs(force=True)
            st.rerun()
        if qc4.button("💾 Save now"):
            save_queue_to_gcs(
                st.session_state.queue_name, st.session_state.job_queue
            )
            st.success("Queue saved to GCS.")

        # Queue table
        maybe_refresh_queue_from_gcs()
        st.caption(
            f"GCS saved_at: {st.session_state.get('queue_saved_at') or '—'} · "
            f"{sum(e['status']=='PENDING' for e in st.session_state.job_queue)} pending · "
            f"{sum(e['status']=='RUNNING' for e in st.session_state.job_queue)} running · "
            f"Queue is {'RUNNING' if st.session_state.queue_running else 'STOPPED'}"
        )

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
            if "Delete" not in df_queue.columns:
                df_queue.insert(0, "Delete", False)

            edited = st.data_editor(
                df_queue,
                key="queue_editor",
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Delete": st.column_config.CheckboxColumn(
                        "Delete", help="Mark to remove from queue"
                    )
                },
            )

            ids_to_delete = set()
            if "Delete" in edited.columns:
                ids_to_delete = set(
                    edited.loc[edited["Delete"] == True, "ID"]
                    .astype(int)
                    .tolist()
                )

            if st.button("🗑 Delete selected (PENDING/ERROR only)"):
                new_q, blocked = [], []
                for e in st.session_state.job_queue:
                    if e["id"] in ids_to_delete:
                        if e.get("status") in (
                            "PENDING",
                            "ERROR",
                            "CANCELLED",
                            "FAILED",
                        ):
                            continue  # drop it
                        else:
                            blocked.append(e["id"])
                            new_q.append(e)
                    else:
                        new_q.append(e)

                st.session_state.job_queue = new_q
                st.session_state.queue_saved_at = save_queue_to_gcs(
                    st.session_state.queue_name,
                    st.session_state.job_queue,
                    queue_running=st.session_state.queue_running,
                )
                if blocked:
                    st.warning(
                        f"Did not delete non-deletable entries: {sorted(blocked)}"
                    )
                st.success("Queue updated.")
                st.rerun()
    render_jobs_ledger(key_prefix="queue")
    render_job_status_monitor(key_prefix="queue")

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


def _queue_tick():
    maybe_refresh_queue_from_gcs()
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
            s = (status_info.get("overall_status") or "").upper()

            if s in TERMINAL_STATES:
                # normalize COMPLETED -> SUCCEEDED for readability
                final_state = (
                    "SUCCEEDED" if s in ("SUCCEEDED", "COMPLETED") else s
                )
                entry["status"] = final_state
                entry["message"] = status_info.get("error", "") or final_state

                # build ledger row
                now_iso = datetime.utcnow().isoformat()
                start_iso = entry.get("start_time")
                try:
                    duration_minutes = (
                        round(
                            (
                                datetime.fromisoformat(now_iso)
                                - datetime.fromisoformat(start_iso)
                            ).total_seconds()
                            / 60.0,
                            2,
                        )
                        if start_iso
                        else None
                    )
                except Exception:
                    duration_minutes = None

                params = entry.get("params", {})
                row = {
                    "job_id": entry.get("id"),
                    "exec_name": entry.get(
                        "execution_name", ""
                    ),  # save exec name
                    "state": final_state,
                    "country": params.get("country"),
                    "revision": params.get("revision"),
                    "date_input": params.get("date_input"),
                    "iterations": params.get("iterations"),
                    "trials": params.get("trials"),
                    "train_size": params.get("train_size"),
                    "dep_var": params.get("dep_var"),
                    "adstock": params.get("adstock"),
                    "start_time": start_iso,
                    "end_time": now_iso,
                    "duration_minutes": duration_minutes,
                    "gcs_prefix": entry.get("gcs_prefix"),
                    "bucket": params.get("gcs_bucket")
                    or st.session_state.get("gcs_bucket", GCS_BUCKET),
                }

                try:
                    append_row_to_ledger(
                        row, st.session_state.get("gcs_bucket", GCS_BUCKET)
                    )
                    # remove finished job from queue
                    st.session_state.job_queue = [
                        e
                        for e in st.session_state.job_queue
                        if e.get("id") != entry.get("id")
                    ]
                except Exception as e:
                    # If ledger write fails, we keep it in queue but mark error.
                    # (If you want to remove even on ledger error, delete the 'except' body.)
                    entry["message"] = (
                        f"{final_state}; ledger append failed: {e}"
                    )

                st.session_state.queue_saved_at = save_queue_to_gcs(
                    st.session_state.queue_name,
                    st.session_state.job_queue,
                    queue_running=st.session_state.queue_running,
                )
                return

            # Not terminal yet: persist any change
            st.session_state.queue_saved_at = save_queue_to_gcs(
                st.session_state.queue_name,
                q,
                queue_running=st.session_state.queue_running,
            )
            return

        except Exception as e:
            entry["status"] = "ERROR"
            entry["message"] = str(e)
            st.session_state.queue_saved_at = save_queue_to_gcs(
                st.session_state.queue_name,
                q,
                queue_running=st.session_state.queue_running,
            )
            return

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
            entry["start_time"] = datetime.utcnow().isoformat()
            st.session_state.job_executions.append(exec_info)
            changed = True
        except Exception as e:
            entry["status"] = "ERROR"
            entry["message"] = f"launch failed: {e}"
            changed = True

    if changed:
        st.session_state.queue_saved_at = save_queue_to_gcs(
            st.session_state.queue_name,
            q,
            queue_running=st.session_state.queue_running,
        )


# Tick the queue on every rerun
_queue_tick()

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
