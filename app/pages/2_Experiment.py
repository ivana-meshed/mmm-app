# pages/2_Experiment.py
import os, io, json, tempfile, time, re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from contextlib import contextmanager

import os, io, json, time, re
from datetime import datetime, timezone


import logging
import base64

import pandas as pd
import streamlit as st

from google.cloud import storage


from app_shared import (
    require_login_and_domain,
    get_data_processor,
    run_sql,
    _require_sf_session,
)

from app_split_helpers import *  # bring in all helper functions/constants


st.set_page_config(page_title="Experiment", page_icon="🧪", layout="wide")

require_login_and_domain()
ensure_session_defaults()

st.title("Experiment")

tab_single, tab_queue = st.tabs(["Single run", "Queue"])

# Prefill fields from saved metadata if present (session_state keys should already be set by Map Your Data page).

# Extracted from streamlit_app.py tab_single (Single run):
with tab_single:
    st.subheader("Robyn configuration & training")
    if not st.session_state.get("sf_connected", False):
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

            # NEW: optional resampling
            c_rs1, c_rs2 = st.columns([1, 1])
            resample_freq_label = c_rs1.selectbox(
                "Resample input data (optional)",
                ["None", "Weekly (W)", "Monthly (M)"],
                index=0,
                help="Aggregates the input before training.",
            )
            resample_freq = {
                "None": "none",
                "Weekly (W)": "W",
                "Monthly (M)": "M",
            }[resample_freq_label]
            resample_agg_label = c_rs2.selectbox(
                "Aggregation for metrics (when resampling)",
                ["sum", "avg (mean)", "max", "min"],
                index=0,
                help="Applied to numeric columns during resample.",
            )
            resample_agg = {
                "sum": "sum",
                "avg (mean)": "mean",
                "max": "max",
                "min": "min",
            }[resample_agg_label]

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
                    resample_freq,  # NEW
                    resample_agg,  # NEW
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
                        # NEW: optional resample (single job)
                        # with timed_step(
                        #    "Optional resample (single job)", timings
                        # ):
                        #    df = _maybe_resample_df(
                        #        df, date_var, resample_freq, resample_agg
                        #    )
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

        render_jobs_job_history(key_prefix="single")

    # ===================== BATCH QUEUE (CSV) =====================

_queue_tick()


# Extracted from streamlit_app.py tab_queue (Batch/Queue run):
with tab_queue:
    if st.session_state.get("queue_running") and not (
        st.session_state.get("job_queue") or []
    ):
        st.session_state.queue_running = False

    st.subheader(
        "Batch queue (CSV) — queue & run multiple jobs sequentially",
    )
    with st.expander(
        "📚 Batch queue (CSV) — queue & run multiple jobs sequentially",
        expanded=False,
    ):
        _render_flash("batch_dupes")
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
- `resample_freq` (none|W|M)
- `resample_agg` (sum|mean|max|min) – used when resampling
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
                    "resample_freq": "none",
                    "resample_agg": "sum",
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
                    "resample_freq": "none",
                    "resample_agg": "sum",
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
                    "resample_freq": "none",
                    "resample_agg": "sum",
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

        # --- CSV upload (editable, persistent, deletable) ---
        up = st.file_uploader("Upload batch CSV", type=["csv"], key="batch_csv")

        # Session scaffolding
        if "uploaded_df" not in st.session_state:
            st.session_state.uploaded_df = pd.DataFrame()
        if "uploaded_fingerprint" not in st.session_state:
            st.session_state.uploaded_fingerprint = None

        # Load only when the *file changes* (never reload just because the table is empty)
        if up is not None:
            fingerprint = f"{getattr(up, 'name', '')}:{getattr(up, 'size', '')}"
            if st.session_state.uploaded_fingerprint != fingerprint:
                try:
                    st.session_state.uploaded_df = pd.read_csv(up)
                    st.session_state.uploaded_fingerprint = fingerprint
                    st.success(
                        f"Loaded {len(st.session_state.uploaded_df)} rows from CSV"
                    )
                except Exception as e:
                    st.error(f"Failed to parse CSV: {e}")
        else:
            # If user clears the file input, allow re-uploading the same file later
            st.session_state.uploaded_fingerprint = None

        # ===== Uploaded CSV (FORM) =====
        st.markdown("#### 📥 Uploaded CSV (editable)")
        st.caption(
            "Edits in this table are committed when you press a button below. "
            "Use **Append uploaded rows to builder** here to move unique rows into the builder."
        )

        if st.session_state.uploaded_df.empty:
            st.caption("No uploaded CSV yet (or it has 0 rows).")
        else:
            uploaded_view = st.session_state.uploaded_df.copy()
            if "Delete" not in uploaded_view.columns:
                uploaded_view.insert(0, "Delete", False)

            uploaded_view, up_nonce = _sorted_with_controls(
                uploaded_view, prefix="uploaded"
            )

            with st.form("uploaded_csv_form"):
                # Show editable grid with a Delete column (like queue builder)

                uploaded_edited = st.data_editor(
                    uploaded_view,
                    key=f"uploaded_editor_{up_nonce}",  # <= bump key when sort changes
                    num_rows="dynamic",
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "Delete": st.column_config.CheckboxColumn(
                            "Delete", help="Mark to remove from uploaded table"
                        )
                    },
                )

                u1, u2, u3 = st.columns([1, 1, 1])

                append_uploaded_clicked = u1.form_submit_button(
                    "Append uploaded rows to builder",
                    disabled=uploaded_edited.drop(
                        columns="Delete", errors="ignore"
                    ).empty,
                )
                delete_uploaded_clicked = u2.form_submit_button(
                    "🗑 Delete selected"
                )
                clear_uploaded_clicked = u3.form_submit_button(
                    "🧹 Clear uploaded table"
                )

            # ---- Handle CSV form actions ----

            if delete_uploaded_clicked:
                keep_mask = (
                    ~uploaded_edited.get("Delete", False)
                    .fillna(False)
                    .astype(bool)
                )
                st.session_state.uploaded_df = (
                    uploaded_edited.loc[keep_mask]
                    .drop(columns="Delete", errors="ignore")
                    .reset_index(drop=True)
                )
                st.success("Deleted selected uploaded rows.")
                st.rerun()

            if clear_uploaded_clicked:
                st.session_state.uploaded_df = pd.DataFrame()
                st.session_state.uploaded_fingerprint = None
                st.success("Cleared uploaded table.")
                st.rerun()

            if append_uploaded_clicked:
                # Canonical, edited upload table as seen in the UI (including any user sorting)
                up_base = (
                    uploaded_edited.drop(columns="Delete", errors="ignore")
                    .copy()
                    .reset_index(drop=True)
                )

                # Columns the builder expects (preserve your existing behavior)
                need_cols = (
                    list(st.session_state.qb_df.columns)
                    if "qb_df" in st.session_state
                    else []
                )

                # Helpers
                def _sig_from_params_dict(d: dict) -> str:
                    return json.dumps(d, sort_keys=True)

                # Build signature sets against which we dedupe
                builder_sigs = {
                    _sig_from_params_dict(_normalize_row(r))
                    for _, r in (
                        st.session_state.qb_df
                        if "qb_df" in st.session_state
                        else pd.DataFrame()
                    ).iterrows()
                }

                queue_sigs = set()
                for e in st.session_state.job_queue:
                    try:
                        queue_sigs.add(
                            _sig_from_params_dict(
                                _normalize_row(pd.Series(e.get("params", {})))
                            )
                        )
                    except Exception:
                        pass

                # JOB_HISTORY sigs (SUCCEEDED/FAILED)
                try:
                    df_led = read_job_history_from_gcs(
                        st.session_state.get("gcs_bucket", GCS_BUCKET)
                    )
                except Exception:
                    df_led = pd.DataFrame()

                job_history_sigs = set()
                if not df_led.empty and "state" in df_led.columns:
                    df_led = df_led[
                        df_led["state"].isin(["SUCCEEDED", "FAILED"])
                    ].copy()
                    cols_like = need_cols or df_led.columns
                    for _, r in df_led.iterrows():
                        params_like = {c: r.get(c, "") for c in cols_like}
                        params_like = _normalize_row(pd.Series(params_like))
                        job_history_sigs.add(_sig_from_params_dict(params_like))

                # Decide row-by-row whether to append (True) or keep in upload (False)
                dup = {
                    "in_builder": [],
                    "in_queue": [],
                    "in_job_history": [],
                    "missing_data_source": [],
                }
                to_append_mask = []

                for i, r in up_base.iterrows():
                    params = _normalize_row(r)
                    if not (params.get("query") or params.get("table")):
                        dup["missing_data_source"].append(i + 1)
                        to_append_mask.append(False)
                        continue
                    sig = _sig_from_params_dict(params)
                    if sig in builder_sigs:
                        dup["in_builder"].append(i + 1)
                        to_append_mask.append(False)
                        continue
                    if sig in queue_sigs:
                        dup["in_queue"].append(i + 1)
                        to_append_mask.append(False)
                        continue
                    if sig in job_history_sigs:
                        dup["in_job_history"].append(i + 1)
                        to_append_mask.append(False)
                        continue
                    to_append_mask.append(True)

                to_append_mask = pd.Series(to_append_mask, index=up_base.index)
                added_count = int(to_append_mask.sum())

                _toast_dupe_summary(
                    "Append to builder", dup, added_count=added_count
                )

                if added_count > 0:
                    # Append to builder (use builder schema)
                    to_append = up_base.loc[to_append_mask]
                    if need_cols:
                        to_append = to_append.reindex(
                            columns=need_cols, fill_value=""
                        )
                    st.session_state.qb_df = pd.concat(
                        [st.session_state.qb_df, to_append], ignore_index=True
                    )

                    # Keep only rows NOT appended in the upload table (i.e., the duplicates / invalid ones)
                    st.session_state.uploaded_df = up_base.loc[
                        ~to_append_mask
                    ].reset_index(drop=True)

                    st.success(
                        f"Appended {added_count} row(s) to the builder. "
                        f"Remaining in upload: {len(st.session_state.uploaded_df)} duplicate/invalid row(s)."
                    )
                    st.rerun()

        # Seed once from current GCS queue (do NOT re-seed on every rerun)
        # ===== Queue Builder (parameters only, editable) =====
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
                "resample_freq": p.get("resample_freq", "none"),
                "resample_agg": p.get("resample_agg", "sum"),
                "annotations_gcs_path": p.get("annotations_gcs_path", ""),
            }

        seed_df = pd.DataFrame([_entry_to_row(e) for e in existing_entries])
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
                    "resample_freq",
                    "resample_agg",
                    "annotations_gcs_path",
                ]
            )
            seed_df.loc[0] = [""] * len(seed_df.columns)

        if "qb_df" not in st.session_state:
            st.session_state.qb_df = seed_df.copy()

        st.markdown("#### ✏️ Queue Builder (editable)")
        st.caption(
            "Starts with your current GCS queue (params only). "
            "Edit cells, add rows, or append from the uploaded CSV. "
            "Use the buttons below; edits are committed on submit."
        )

        # Use a FORM so editor commits the last active cell before any button logic
        builder_src = st.session_state.qb_df.copy()

        # Add Delete checkbox column (not persisted) and enable sorting/nonce
        if "Delete" not in builder_src.columns:
            builder_src.insert(0, "Delete", False)

        builder_src, qb_nonce = _sorted_with_controls(builder_src, prefix="qb")

        with st.form("queue_builder_form"):
            # Editable builder (params), plus a Delete column just for selection
            builder_edited = st.data_editor(
                builder_src,
                num_rows="dynamic",
                width="stretch",
                key=f"queue_builder_editor_{qb_nonce}",  # <= bump key when sort changes
                hide_index=True,
                column_config={
                    "Delete": st.column_config.CheckboxColumn(
                        "Delete", help="Mark to remove from builder"
                    )
                },
            )

            # Persist edits to builder params (drop Delete column)
            st.session_state.qb_df = builder_edited.drop(
                columns="Delete", errors="ignore"
            ).reset_index(drop=True)

            # Actions for the builder table only – now includes Delete selected
            bb0, bb1, bb2, bb3 = st.columns(4)

            save_builder_clicked = bb0.form_submit_button(
                "💾 Save builder edits"
            )

            delete_builder_clicked = bb1.form_submit_button("🗑 Delete selected")
            reset_clicked = bb2.form_submit_button(
                "Reset builder to current GCS queue"
            )
            clear_builder_clicked = bb3.form_submit_button(
                "Clear builder (empty table)"
            )

            # Enqueue & clear queue
            bc1, bc2 = st.columns(2)
            enqueue_clicked = bc1.form_submit_button("➕ Enqueue all rows")

            clear_queue_clicked = bc2.form_submit_button("🧹 Clear queue")

        # ----- Handle form actions (after form so we have latest editor state) -----
        if delete_builder_clicked:
            keep_mask = (
                (~builder_edited.get("Delete", False))
                .fillna(False)
                .astype(bool)
            )
            st.session_state.qb_df = (
                builder_edited.loc[keep_mask]
                .drop(columns="Delete", errors="ignore")
                .reset_index(drop=True)
            )
            st.success("Deleted selected builder rows.")
            st.rerun()

        if save_builder_clicked:
            # Persist ONLY when explicitly saving (or do this in “any submit” branches)
            st.session_state.qb_df = builder_edited.drop(
                columns="Delete", errors="ignore"
            ).reset_index(drop=True)
            st.success("Builder saved.")
            st.rerun()

        if reset_clicked:
            st.session_state.qb_df = seed_df.copy()
            st.info("Builder reset to current GCS queue.")
            st.rerun()

        if clear_builder_clicked:
            st.session_state.qb_df = seed_df.iloc[0:0].copy()
            st.info("Builder cleared.")
            st.rerun()

        if clear_queue_clicked:
            st.session_state["job_queue"] = []
            st.session_state["queue_running"] = False
            save_queue_to_gcs(st.session_state.queue_name, [])
            st.success("Queue cleared & saved to GCS.")
            st.rerun()

        # Build helper here so it’s shared by both append & enqueue
        def _sig_from_params_dict(d: dict) -> str:
            return json.dumps(d, sort_keys=True)

        need_cols = list(st.session_state.qb_df.columns)

        if enqueue_clicked:
            # Build separate sets so we can categorize reasons
            if st.session_state.qb_df.dropna(how="all").empty:
                st.warning(
                    "No rows to enqueue. Add at least one non-empty row."
                )
            else:
                queue_sigs_existing = set()
                for e in st.session_state.job_queue:
                    try:
                        norm_existing = _normalize_row(
                            pd.Series(e.get("params", {}))
                        )
                        queue_sigs_existing.add(
                            json.dumps(norm_existing, sort_keys=True)
                        )
                    except Exception:
                        pass

                try:
                    df_led = read_job_history_from_gcs(
                        st.session_state.get("gcs_bucket", GCS_BUCKET)
                    )
                except Exception:
                    df_led = pd.DataFrame()

                job_history_sigs_existing = set()
                if not df_led.empty and "state" in df_led.columns:
                    df_led = df_led[
                        df_led["state"].isin(["SUCCEEDED", "FAILED"])
                    ].copy()
                    for _, r in df_led.iterrows():
                        params_like = {c: r.get(c, "") for c in need_cols}
                        params_like = _normalize_row(pd.Series(params_like))
                        job_history_sigs_existing.add(
                            json.dumps(params_like, sort_keys=True)
                        )

                next_id = (
                    max(
                        [e["id"] for e in st.session_state.job_queue], default=0
                    )
                    + 1
                )

                new_entries, enqueued_sigs = [], set()
                dup = {
                    "in_queue": [],
                    "in_job_history": [],
                    "missing_data_source": [],
                }

                for i, row in st.session_state.qb_df.iterrows():
                    params = _normalize_row(row)
                    if not (params.get("query") or params.get("table")):
                        dup["missing_data_source"].append(i + 1)
                        continue
                    sig = json.dumps(params, sort_keys=True)
                    if sig in queue_sigs_existing:
                        dup["in_queue"].append(i + 1)
                        continue
                    if sig in job_history_sigs_existing:
                        dup["in_job_history"].append(i + 1)
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
                    enqueued_sigs.add(sig)

                # Toaster (and zero-add info if applicable)
                _toast_dupe_summary(
                    "Enqueue", dup, added_count=len(new_entries)
                )

                if not new_entries:
                    # nothing to enqueue
                    pass
                else:
                    st.session_state.job_queue.extend(new_entries)
                    st.session_state.queue_saved_at = save_queue_to_gcs(
                        st.session_state.queue_name,
                        st.session_state.job_queue,
                        queue_running=st.session_state.queue_running,
                    )

                    # Remove only the rows that were enqueued from the builder
                    def _row_sig(r: pd.Series) -> str:
                        return json.dumps(_normalize_row(r), sort_keys=True)

                    keep_mask = ~st.session_state.qb_df.apply(
                        _row_sig, axis=1
                    ).isin(enqueued_sigs)
                    st.session_state.qb_df = st.session_state.qb_df.loc[
                        keep_mask
                    ].reset_index(drop=True)

                    st.success(
                        f"Enqueued {len(new_entries)} new job(s), saved to GCS, and removed them from the builder."
                    )
                    st.rerun()

        # Queue controls
        st.caption(
            f"Queue status: {'▶️ RUNNING' if st.session_state.queue_running else '⏸️ STOPPED'} · "
            f"{sum(e['status'] in ('RUNNING','LAUNCHING') for e in st.session_state.job_queue)} running"
        )

        if st.button("🔁 Refresh from GCS"):
            maybe_refresh_queue_from_gcs(force=True)
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
            _queue_tick()
            st.toast("Ticked queue")
            st.rerun()

        if qc4.button("💾 Save now"):
            save_queue_to_gcs(
                st.session_state.queue_name, st.session_state.job_queue
            )
            st.success("Queue saved to GCS.")

        _auto_refresh_and_tick(interval_ms=2000)

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

            # Add Delete checkbox column (not persisted) and enable sorting/nonce
            if "Delete" not in df_queue.columns:
                df_queue.insert(0, "Delete", False)

            df_queue_view, q_nonce = _sorted_with_controls(
                df_queue, prefix="queue"
            )

            # Build per-column config so everything is read-only EXCEPT Delete
            q_cfg = {}
            for c in df_queue_view.columns:
                if c == "Delete":
                    q_cfg[c] = st.column_config.CheckboxColumn(
                        "Delete", help="Mark to remove from queue"
                    )
                elif c in ("ID",):
                    q_cfg[c] = st.column_config.NumberColumn(c, disabled=True)
                else:
                    q_cfg[c] = st.column_config.TextColumn(c, disabled=True)

            # Form so the checkbox state is committed before deleting
            with st.form("queue_table_form"):
                edited = st.data_editor(
                    df_queue_view,
                    key=f"queue_editor_{q_nonce}",  # <= bump key when sort changes
                    hide_index=True,
                    width="stretch",
                    column_config=q_cfg,
                )

                delete_queue_clicked = st.form_submit_button(
                    "🗑 Delete selected (PENDING/ERROR only)"
                )

            # Deletion logic identical to before, but reading from the form-edited frame
            ids_to_delete = set()
            if "Delete" in edited.columns:
                ids_to_delete = set(
                    edited.loc[edited["Delete"] == True, "ID"]
                    .astype(int)
                    .tolist()
                )

            if delete_queue_clicked:
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

    render_jobs_job_history(key_prefix="queue")
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
