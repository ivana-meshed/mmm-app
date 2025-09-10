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
    LEDGER_COLUMNS,
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
    _safe_tick_once,  # (kept for parity; not used below
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
    cols = LEDGER_COLUMNS
    return pd.DataFrame(columns=cols)


def render_jobs_ledger(key_prefix: str = "single") -> None:
    with st.expander("📚 Jobs Ledger (from GCS)", expanded=False):
        try:
            df_ledger = read_ledger_from_gcs(
                st.session_state.get("gcs_bucket", GCS_BUCKET)
            )
        except Exception as e:
            st.error(f"Failed to read ledger from GCS: {e}")
            return

        # Force canonical order/shape before editing
        df_ledger = df_ledger.reindex(columns=LEDGER_COLUMNS)

        st.caption("Append rows directly and click **Save to GCS** to persist.")
        locked = {
            "job_id",
            "bucket",
            "gcs_prefix",
            "exec_name",
            "execution_name",
            "start_time",
            "end_time",
            "duration_minutes",
        }
        col_cfg = {
            c: st.column_config.TextColumn(disabled=True)
            for c in locked
            if c in df_ledger.columns
        }
        # If some are numeric, use NumberColumn; adjust as needed.

        edited = st.data_editor(
            df_ledger,
            num_rows="dynamic",
            width="stretch",
            key=f"ledger_editor_{key_prefix}_{st.session_state.get('ledger_nonce', 0)}",
            column_order=LEDGER_COLUMNS,
            column_config=col_cfg,
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
            "🧹 Normalize & Save", key=f"normalize_ledger_{key_prefix}"
        ):
            try:
                # Re-read, normalize, save
                from app_shared import normalize_ledger_df, save_ledger_to_gcs

                save_ledger_to_gcs(
                    normalize_ledger_df(edited),
                    st.session_state.get("gcs_bucket", GCS_BUCKET),
                )
                st.success("Ledger normalized & saved.")
            except Exception as e:
                st.error(f"Normalize failed: {e}")


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
                    st.dataframe(df_prev, width="stretch")
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
                        st.dataframe(df_prev, width="stretch")
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
                    "dep_var": "UPLOAD_VALUE",
                    "date_var": "date",
                    "adstock": "geometric",
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
                    "trials": 3,
                    "train_size": "0.7,0.9",
                    "paid_media_spends": "GA_SUPPLY_COST, GA_DEMAND_COST, META_DEMAND_COST, TV_COST",
                    "paid_media_vars": "GA_SUPPLY_COST, GA_DEMAND_COST, META_DEMAND_COST, TV_COST",
                    "context_vars": "IS_WEEKEND,TV_IS_ON",
                    "factor_vars": "IS_WEEKEND,TV_IS_ON",
                    "organic_vars": "ORGANIC_TRAFFIC",
                    "gcs_bucket": st.session_state["gcs_bucket"],
                    "table": "MESHED_BUYCYCLE.GROWTH.MMM_RAW",
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

        # --- CSV upload (unchanged) ---
        # --- CSV upload (view only; append is explicit) ---
        up = st.file_uploader("Upload batch CSV", type=["csv"], key="batch_csv")
        uploaded_df = None
        if up:
            try:
                uploaded_df = pd.read_csv(up)
                st.success(f"Loaded {len(uploaded_df)} rows from CSV")
                st.dataframe(uploaded_df.head(), width="stretch")
            except Exception as e:
                st.error(f"Failed to parse CSV: {e}")

        # ===== Queue Builder (parameters only, editable) =====
        # Seed once from current GCS queue (do NOT re-seed on every rerun)
        payload = load_queue_payload(st.session_state.queue_name)
        existing_entries = payload.get("entries", [])

        def _as_csv(v):
            if isinstance(v, (list, tuple)):
                return ", ".join(str(x) for x in v if str(x).strip())
            return "" if v is None else str(v)

        def _entry_to_row(e: dict) -> dict:
            p = e.get("params", {}) or {}
            return {
                "country": p.get("country", ""),
                "revision": p.get("revision", ""),
                "date_input": p.get("date_input", ""),
                "iterations": p.get("iterations", ""),
                "trials": p.get("trials", ""),
                "train_size": _as_csv(p.get("train_size", "")),
                "paid_media_spends": _as_csv(p.get("paid_media_spends", "")),
                "paid_media_vars": _as_csv(p.get("paid_media_vars", "")),
                "context_vars": _as_csv(p.get("context_vars", "")),
                "factor_vars": _as_csv(p.get("factor_vars", "")),
                "organic_vars": _as_csv(p.get("organic_vars", "")),
                "gcs_bucket": p.get(
                    "gcs_bucket", st.session_state["gcs_bucket"]
                ),
                "table": p.get("table", ""),
                "query": p.get("query", ""),
                "dep_var": p.get("dep_var", ""),
                "date_var": p.get("date_var", ""),
                "adstock": p.get("adstock", ""),
                "annotations_gcs_path": p.get("annotations_gcs_path", ""),
            }

        seed_df = pd.DataFrame([_entry_to_row(e) for e in existing_entries])
        # If absolutely empty, start with a single blank row so the user can type without the UI "blinking"
        if seed_df.empty:
            seed_df = seed_df.reindex(
                columns=[
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
                    "gcs_bucket",
                    "table",
                    "query",
                    "dep_var",
                    "date_var",
                    "adstock",
                    "annotations_gcs_path",
                ]
            )
            seed_df.loc[0] = [""] * len(seed_df.columns)

        st.session_state.setdefault("qb_df", None)
        st.session_state.setdefault("qb_initialized", False)

        if (
            not st.session_state.qb_initialized
            or st.session_state.qb_df is None
        ):
            st.session_state.qb_df = seed_df.copy()
            st.session_state.qb_initialized = True

        st.markdown("#### ✏️ Queue Builder (editable)")
        st.caption(
            "Starts with your current GCS queue (params only). "
            "Edit cells, add rows, or append from the uploaded CSV. "
            "Click **Enqueue all rows** to add new rows to the GCS queue (duplicates are skipped)."
        )

        # Builder editor — edits persist in session
        builder_edited = st.data_editor(
            st.session_state.qb_df,
            num_rows="dynamic",
            width="stretch",
            key="queue_builder_editor",
        )
        st.session_state.qb_df = builder_edited

        # Buttons for builder
        b1, b2, b3 = st.columns(3)
        if b1.button(
            "Append uploaded rows to builder", disabled=(uploaded_df is None)
        ):
            st.session_state.qb_df = pd.concat(
                [st.session_state.qb_df, uploaded_df], ignore_index=True
            )
            st.success(f"Appended {len(uploaded_df)} rows to builder.")
            st.rerun()

        if b2.button("Reset builder to current GCS queue"):
            st.session_state.qb_df = seed_df.copy()
            st.session_state.qb_initialized = True
            st.info("Builder reset to current GCS queue.")
            st.rerun()

        if b3.button("Clear builder (empty table)"):
            st.session_state.qb_df = seed_df.iloc[0:0].copy()
            st.session_state.qb_initialized = True
            st.info("Builder cleared.")
            st.rerun()

        # Normalizer reused for each row
        def _normalize_row(row: pd.Series) -> dict:
            def _g(v, default):
                return (
                    row.get(v) if (v in row and pd.notna(row[v])) else default
                )

            return {
                "country": str(_g("country", country)),
                "revision": str(_g("revision", revision)),
                "date_input": str(_g("date_input", date_input)),
                "iterations": (
                    int(float(_g("iterations", iterations)))
                    if str(_g("iterations", iterations)).strip()
                    else int(iterations)
                ),
                "trials": (
                    int(float(_g("trials", trials)))
                    if str(_g("trials", trials)).strip()
                    else int(trials)
                ),
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

        # Enqueue button
        c_left, c_right = st.columns(2)
        if c_left.button(
            "➕ Enqueue all rows",
            disabled=(
                st.session_state.qb_df is None or st.session_state.qb_df.empty
            ),
        ):
            # Build duplicate-signature set from existing queue
            existing_sigs = set()
            for e in st.session_state.job_queue:
                try:
                    norm_existing = _normalize_row(
                        pd.Series(e.get("params", {}))
                    )
                    existing_sigs.add(json.dumps(norm_existing, sort_keys=True))
                except Exception:
                    pass

            next_id = (
                max([e["id"] for e in st.session_state.job_queue], default=0)
                + 1
            )
            new_entries = []
            for _, row in st.session_state.qb_df.iterrows():
                params = _normalize_row(row)
                if not (params.get("query") or params.get("table")):
                    continue
                sig = json.dumps(params, sort_keys=True)
                if sig in existing_sigs:
                    continue
                new_entries.append(
                    {
                        "id": next_id + len(new_entries),
                        "params": params,
                        "status": "PENDING",
                        "timestamp": None,
                        "execution_name": None,
                        "gcs_prefix": None,
                        "message": "",
                    }
                )

            if not new_entries:
                st.info(
                    "Nothing new to enqueue (all rows are duplicates or missing data source)."
                )
            else:
                st.session_state.job_queue.extend(new_entries)
                st.session_state.queue_saved_at = save_queue_to_gcs(
                    st.session_state.queue_name,
                    st.session_state.job_queue,
                    queue_running=st.session_state.queue_running,
                )
                st.success(
                    f"Enqueued {len(new_entries)} new job(s) and saved to GCS."
                )
                st.rerun()

        if c_right.button("🧹 Clear queue"):
            st.session_state["job_queue"] = []
            st.session_state["queue_running"] = False
            save_queue_to_gcs(st.session_state.queue_name, [])
            st.success("Queue cleared & saved to GCS.")
            st.rerun()

        # Queue controls
        st.caption(
            f"Queue status: {'▶️ RUNNING' if st.session_state.queue_running else '⏸️ STOPPED'} · "
            f"{sum(e['status'] in ('RUNNING','LAUNCHING') for e in st.session_state.job_queue)} running"
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
                width="stretch",
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
            st.dataframe(df_times, width="stretch")
            st.write(f"**Total setup time:** {_fmt_secs(total)}")
            st.write(
                "**Note**: Training runs asynchronously in Cloud Run Jobs."
            )


def _queue_tick():
    maybe_refresh_queue_from_gcs()
    q = st.session_state.job_queue
    if not q:
        return

    # 1) Update RUNNING/LAUNCHING job status (if any)
    running = [e for e in q if e["status"] in ("RUNNING", "LAUNCHING")]
    if running:
        entry = running[0]
        try:
            status_info = job_manager.get_execution_status(
                entry["execution_name"]
            )
            s = (status_info.get("overall_status") or "").upper()

            if s in TERMINAL_STATES:
                final_state = (
                    "SUCCEEDED" if s in ("SUCCEEDED", "COMPLETED") else s
                )
                entry["status"] = final_state
                entry["message"] = status_info.get("error", "") or final_state

                start_iso = entry.get("start_time") or entry.get("timestamp")

                # Append to ledger
                try:
                    exec_full = entry.get("execution_name") or ""
                    exec_short = exec_full.split("/")[-1] if exec_full else ""
                    append_row_to_ledger(
                        {
                            "job_id": entry.get("gcs_prefix")
                            or entry.get("id"),
                            "state": final_state,
                            "country": entry["params"].get("country"),
                            "revision": entry["params"].get("revision"),
                            "date_input": entry["params"].get("date_input"),
                            "iterations": entry["params"].get("iterations"),
                            "trials": entry["params"].get("trials"),
                            "train_size": entry["params"].get("train_size"),
                            "dep_var": entry["params"].get("dep_var"),
                            "adstock": entry["params"].get("adstock"),
                            "start_time": start_iso,
                            "end_time": datetime.utcnow().isoformat(
                                timespec="seconds"
                            )
                            + "Z",
                            "gcs_prefix": entry.get("gcs_prefix"),
                            "bucket": entry.get("gcs_bucket")
                            or st.session_state.get("gcs_bucket", GCS_BUCKET),
                            "exec_name": exec_short,
                            "execution_name": exec_full,
                        },
                        st.session_state.get("gcs_bucket", GCS_BUCKET),
                    )
                    # bump a nonce so the ledger editor rerenders
                    st.session_state["ledger_nonce"] = (
                        st.session_state.get("ledger_nonce", 0) + 1
                    )
                except Exception as e:
                    st.warning(f"Ledger append failed: {e}")

                # Remove completed from queue
                completed_id = entry["id"]
                st.session_state.job_queue = [
                    e
                    for e in st.session_state.job_queue
                    if e["id"] != completed_id
                ]
                st.session_state.queue_saved_at = save_queue_to_gcs(
                    st.session_state.queue_name,
                    st.session_state.job_queue,
                    queue_running=st.session_state.queue_running,
                )
                st.rerun()
                return

            # Promote LAUNCHING -> RUNNING if we see progress
            if entry["status"] == "LAUNCHING" and s in ("RUNNING", "PENDING"):
                entry["status"] = "RUNNING"

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
            st.rerun()
            return

    # 2) If none running and queue_running, launch next PENDING with a lease
    if st.session_state.queue_running:
        pending = [e for e in q if e["status"] == "PENDING"]
        if not pending:
            return
        entry = pending[0]
        try:
            # Lease first
            entry["status"] = "LAUNCHING"
            entry["message"] = "Launching..."
            st.session_state.queue_saved_at = save_queue_to_gcs(
                st.session_state.queue_name,
                q,
                queue_running=st.session_state.queue_running,
            )

            exec_info = prepare_and_launch_job(entry["params"])
            time.sleep(SAFE_LAG_SECONDS_AFTER_RUNNING)
            entry["execution_name"] = exec_info["execution_name"]
            entry["timestamp"] = exec_info["timestamp"]
            entry["gcs_prefix"] = exec_info["gcs_prefix"]
            entry["status"] = "RUNNING"
            entry["message"] = "Launched"
            entry["start_time"] = (
                datetime.utcnow().isoformat(timespec="seconds") + "Z"
            )
            st.session_state.job_executions.append(exec_info)

            st.session_state.queue_saved_at = save_queue_to_gcs(
                st.session_state.queue_name,
                q,
                queue_running=st.session_state.queue_running,
            )
            st.rerun()
        except Exception as e:
            entry["status"] = "ERROR"
            entry["message"] = f"launch failed: {e}"
            st.session_state.queue_saved_at = save_queue_to_gcs(
                st.session_state.queue_name,
                q,
                queue_running=st.session_state.queue_running,
            )
            st.rerun()


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

and app_shared.py

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
