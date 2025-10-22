# (full file contents)
import os, io, json, tempfile, time, re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from contextlib import contextmanager

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
    GCS_BUCKET,
    PROJECT_ID,
    REGION,
    TRAINING_JOB_NAME,
    upload_to_gcs,
    parse_train_size,
    timed_step,
    get_job_manager,
)

data_processor = get_data_processor()
job_manager = get_job_manager()

from app_split_helpers import *  # bring in all helper functions/constants


st.set_page_config(page_title="Experiment", page_icon="🧪", layout="wide")

require_login_and_domain()
ensure_session_defaults()

st.title("Experiment")

tab_single, tab_queue = st.tabs(["Single run", "Queue"])

# Prefill fields from saved metadata if present (session_state keys should already be set by Map Your Data page).


# Helper functions for GCS data loading
def _list_country_versions(bucket: str, country: str) -> List[str]:
    """Return timestamp folder names available in datasets/<country>/."""
    client = storage.Client()
    prefix = f"datasets/{country.lower().strip()}/"
    blobs = client.list_blobs(bucket, prefix=prefix, delimiter=None)
    ts = set()
    for blob in blobs:
        parts = blob.name.split("/")
        if len(parts) >= 4 and parts[-1] == "raw.parquet":
            ts.add(parts[-2])
    versions = sorted(ts, reverse=True)
    # Replace "latest" with "Latest" if present
    return ["Latest" if v.lower() == "latest" else v for v in versions]


def _get_data_blob(country: str, version: str) -> str:
    """Get GCS blob path for data."""
    if version.lower() == "latest":
        return f"datasets/{country.lower().strip()}/latest/raw.parquet"
    return f"datasets/{country.lower().strip()}/{version}/raw.parquet"


def _get_meta_blob(country: str, version: str) -> str:
    """Get GCS blob path for metadata."""
    if version.lower() == "latest":
        return f"metadata/{country.lower().strip()}/latest/mapping.json"
    return f"metadata/{country.lower().strip()}/{version}/mapping.json"


def _download_from_gcs(bucket: str, blob_path: str, local_path: str):
    """Download file from GCS."""
    client = storage.Client()
    blob = client.bucket(bucket).blob(blob_path)
    if not blob.exists():
        raise FileNotFoundError(f"gs://{bucket}/{blob_path} not found")
    blob.download_to_filename(local_path)


def _load_metadata_from_gcs(
    bucket: str, country: str, version: str
) -> Optional[Dict]:
    """Load metadata.json from GCS."""
    try:
        client = storage.Client()
        blob_path = _get_meta_blob(country, version)
        blob = client.bucket(bucket).blob(blob_path)
        if not blob.exists():
            return None
        return json.loads(blob.download_as_bytes())
    except Exception as e:
        st.warning(f"Could not load metadata: {e}")
        return None


def _get_latest_revision(bucket: str, country: str) -> str:
    """Get the latest revision tag from GCS for a country."""
    try:
        client = storage.Client()
        prefix = f"robyn/"
        blobs = list(client.list_blobs(bucket, prefix=prefix, delimiter="/"))
        # Extract revision folders
        revisions = set()
        for blob in blobs:
            parts = blob.name.split("/")
            if len(parts) >= 2:
                revisions.add(parts[1])

        # Sort revisions (assuming r### format)
        sorted_revs = sorted(
            [r for r in revisions if r.startswith("r")],
            key=lambda x: int(x[1:]) if x[1:].isdigit() else 0,
            reverse=True,
        )
        return sorted_revs[0] if sorted_revs else "r100"
    except Exception:
        return "r100"


# Extracted from streamlit_app.py tab_single (Single run):
with tab_single:
    st.subheader("Robyn configuration & training")

    # Data selection
    with st.expander("Data selection", expanded=True):
        # Country selection
        available_countries = ["fr", "de", "it", "es", "nl", "uk"]
        selected_country = st.selectbox(
            "Country",
            options=available_countries,
            index=0,
            help="Select the country for which to load data",
        )

        # Get available data versions for selected country
        gcs_bucket = st.session_state.get("gcs_bucket", GCS_BUCKET)
        try:
            available_versions = _list_country_versions(gcs_bucket, selected_country)  # type: ignore
            if not available_versions:
                available_versions = ["Latest"]
        except Exception as e:
            st.warning(f"Could not list data versions: {e}")
            available_versions = ["Latest"]

        # Data source selection
        selected_version = st.selectbox(
            "Data source",
            options=available_versions,
            index=0,
            help="Select data version to use. Latest = most recently saved data.",
        )

        # Load data button with automatic preview
        if st.button("Load selected data", type="primary"):
            tmp_path = None
            try:
                with st.spinner("Loading data from GCS..."):
                    blob_path = _get_data_blob(selected_country, selected_version)  # type: ignore
                    with tempfile.NamedTemporaryFile(
                        suffix=".parquet", delete=False
                    ) as tmp:
                        tmp_path = tmp.name
                        _download_from_gcs(gcs_bucket, blob_path, tmp_path)
                        df_prev = pd.read_parquet(tmp_path)
                        st.session_state["preview_df"] = df_prev
                        st.session_state["selected_country"] = selected_country
                        st.session_state["selected_version"] = selected_version

                        # Load metadata
                        metadata = _load_metadata_from_gcs(
                            gcs_bucket, selected_country, selected_version  # type: ignore
                        )
                        st.session_state["loaded_metadata"] = metadata

                        st.success(
                            f"✅ Loaded {len(df_prev)} rows, {len(df_prev.columns)} columns"
                        )
            except Exception as e:
                st.error(f"Failed to load data: {e}")
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        # Show preview if available
        if (
            "preview_df" in st.session_state
            and st.session_state["preview_df"] is not None
        ):
            st.write("**Preview (first 5 rows):**")
            st.dataframe(
                st.session_state["preview_df"].head(5), use_container_width=True
            )

        # Robyn config
        with st.expander("Robyn configuration", expanded=True):
            # Country auto-filled from Data Selection
            country = st.session_state.get("selected_country", "fr")
            st.info(f"**Country:** {country.upper()} (from Data Selection)")

            # Iterations and Trials as presets
            preset_options = {
                "Test run": {"iterations": 200, "trials": 3},
                "Production": {"iterations": 2000, "trials": 5},
                "Custom": {"iterations": 5000, "trials": 10},
            }
            preset_choice = st.selectbox(
                "Training preset",
                options=list(preset_options.keys()),
                index=0,
                help="Choose a training preset or use custom values",
            )

            if preset_choice == "Custom":
                col1, col2 = st.columns(2)
                with col1:
                    iterations = st.number_input(
                        "Iterations", value=5000, min_value=50, step=100
                    )
                with col2:
                    trials = st.number_input(
                        "Trials", value=10, min_value=1, step=1
                    )
            else:
                iterations = preset_options[preset_choice]["iterations"]  # type: ignore
                trials = preset_options[preset_choice]["trials"]  # type: ignore
                st.info(f"**Iterations:** {iterations}, **Trials:** {trials}")

            # Train size stays as is
            train_size = st.text_input(
                "Train size",
                value="0.7,0.9",
                help="Comma-separated train/validation split ratios",
            )

            # Revision tag - auto-prefilled with latest
            default_revision = _get_latest_revision(gcs_bucket, country)
            revision = st.text_input(
                "Revision tag",
                value=default_revision,
                help="Revision identifier for organizing outputs",
            )

            # Training date range instead of single date tag
            col1, col2 = st.columns(2)
            with col1:
                start_data_date = st.date_input(
                    "Training start date",
                    value=datetime(2024, 1, 1).date(),
                    help="Start date for training data window (start_data_date in R script)",
                )
            with col2:
                end_data_date = st.date_input(
                    "Training end date",
                    value=datetime.now().date(),
                    help="End date for training data window (end_data_date in R script)",
                )

            # Convert dates to strings for config
            start_date_str = start_data_date.strftime("%Y-%m-%d")  # type: ignore
            end_date_str = end_data_date.strftime("%Y-%m-%d")  # type: ignore

            # Goal variable from metadata
            metadata = st.session_state.get("loaded_metadata")
            if metadata and "goals" in metadata:
                goal_options = [
                    g["var"]
                    for g in metadata["goals"]
                    if g.get("group") == "primary"
                ]
                if goal_options:
                    dep_var = st.selectbox(
                        "Goal variable",
                        options=goal_options,
                        index=0,
                        help="Primary goal variable (dependent variable) for the model",
                    )
                    # Find the corresponding type
                    dep_var_type = next(
                        (
                            g["type"]
                            for g in metadata["goals"]
                            if g["var"] == dep_var
                        ),
                        "revenue",
                    )
                else:
                    dep_var = st.text_input(
                        "Goal variable", value="UPLOAD_VALUE"
                    )
                    dep_var_type = "revenue"
            else:
                dep_var = st.text_input(
                    "Goal variable",
                    value="UPLOAD_VALUE",
                    help="Dependent variable column in your data",
                )
                dep_var_type = "revenue"

            # Goals type - display and allow override
            dep_var_type = st.selectbox(
                "Goals type",
                options=["revenue", "conversion"],
                index=0 if dep_var_type == "revenue" else 1,
                help="Type of the goal variable: revenue (monetary) or conversion (count)",
            )

            # Date variable
            date_var = st.text_input(
                "date_var",
                value="date",
                help="Date column in your data (e.g., date)",
            )

            # Adstock selection
            adstock = st.selectbox(
                "adstock",
                options=["geometric", "weibull_cdf", "weibull_pdf"],
                index=0,
                help="Robyn adstock function",
            )

            # Hyperparameters - conditional on adstock
            st.write("**Hyperparameters**")
            hyperparameter_preset = st.selectbox(
                "Hyperparameter preset",
                options=["Facebook recommend", "Meshed recommend", "Custom"],
                index=1,
                help="Choose hyperparameter preset or define custom values",
            )

            # Store the hyperparameter choice for later use
            st.session_state["hyperparameter_preset"] = hyperparameter_preset
            st.session_state["adstock_choice"] = adstock

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
            }.get(resample_freq_label or "None", "none")

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
            }.get(resample_agg_label or "sum", "sum")

        # Variables
        with st.expander("Variable mapping", expanded=True):
            # Get available columns from loaded data
            preview_df = st.session_state.get("preview_df")
            if preview_df is not None and not preview_df.empty:
                # Get all columns except date column
                all_columns = [
                    col for col in preview_df.columns if col.lower() != "date"
                ]
            else:
                # Fallback to empty list if no data loaded yet
                all_columns = []

            # Auto-populate from metadata if available
            metadata = st.session_state.get("loaded_metadata")

            default_values = {
                "paid_media_spends": [
                    "GA_SUPPLY_COST",
                    "GA_DEMAND_COST",
                    "BING_DEMAND_COST",
                    "META_DEMAND_COST",
                    "TV_COST",
                    "PARTNERSHIP_COSTS",
                ],
                "paid_media_vars": [
                    "GA_SUPPLY_COST",
                    "GA_DEMAND_COST",
                    "BING_DEMAND_COST",
                    "META_DEMAND_COST",
                    "TV_COST",
                    "PARTNERSHIP_COSTS",
                ],
                "context_vars": ["IS_WEEKEND", "TV_IS_ON"],
                "factor_vars": ["IS_WEEKEND", "TV_IS_ON"],
                "organic_vars": ["ORGANIC_TRAFFIC"],
            }

            # Extract values from metadata if available
            if metadata and "mapping" in metadata:
                mapping_raw = metadata["mapping"]

                # Normalize mapping into a list of dicts with keys 'var' and 'category'
                normalized_mapping: List[Dict[str, Optional[str]]] = []
                try:
                    if isinstance(mapping_raw, dict):
                        # Two common shapes:
                        # 1) { category: [var1, var2, ...], ... }
                        # 2) { var_name: { "category": "...", ... }, ... } OR { var_name: "category" }
                        values_are_lists = all(
                            isinstance(v, list) for v in mapping_raw.values()
                        )
                        if values_are_lists:
                            # keys are categories
                            for cat_key, vars_list in mapping_raw.items():
                                for v in vars_list:
                                    normalized_mapping.append(
                                        {"var": v, "category": cat_key}
                                    )
                        else:
                            # keys are var names
                            for var_name, val in mapping_raw.items():
                                if isinstance(val, dict):
                                    category = val.get("category")
                                elif isinstance(val, str):
                                    category = val
                                else:
                                    category = None
                                normalized_mapping.append(
                                    {"var": var_name, "category": category}
                                )
                    elif isinstance(mapping_raw, list):
                        # List may contain dicts or strings
                        for item in mapping_raw:
                            if isinstance(item, dict):
                                # Expect item to have 'var' and maybe 'category'
                                normalized_mapping.append(
                                    {
                                        "var": item.get("var")
                                        or item.get("name")
                                        or None,
                                        "category": item.get("category"),
                                    }
                                )
                            elif isinstance(item, str):
                                # Bare var name with unknown category
                                normalized_mapping.append(
                                    {"var": item, "category": None}
                                )
                    # Fallback: if normalization yielded nothing, leave mapping_empty
                except Exception:
                    normalized_mapping = []

                # Use normalized mapping to fill defaults per category when possible
                for cat in [
                    "paid_media_spends",
                    "paid_media_vars",
                    "context_vars",
                    "factor_vars",
                    "organic_vars",
                ]:
                    vars_in_cat = [
                        m.get("var")
                        for m in normalized_mapping
                        if m.get("category") == cat and m.get("var")
                    ]
                    if vars_in_cat:
                        default_values[cat] = vars_in_cat

            # Filter defaults to only include columns that exist in the data
            if all_columns:
                for cat in default_values:
                    default_values[cat] = [
                        v for v in default_values[cat] if v in all_columns
                    ]

            # If no data is loaded, show a warning
            if not all_columns:
                st.warning(
                    "⚠️ Please load data first to see available columns for selection."
                )

            # Paid media spends - multiselect
            paid_media_spends_list = st.multiselect(
                "paid_media_spends",
                options=all_columns,
                default=default_values["paid_media_spends"],
                help="Select media spend columns",
            )

            # Paid media vars - multiselect (will be made nested later based on clarification)
            paid_media_vars_list = st.multiselect(
                "paid_media_vars",
                options=all_columns,
                default=default_values["paid_media_vars"],
                help="Select media variable columns (e.g., impressions, clicks)",
            )

            # Context vars - multiselect
            context_vars_list = st.multiselect(
                "context_vars",
                options=all_columns,
                default=default_values["context_vars"],
                help="Select contextual variables (e.g., seasonality, events)",
            )

            # Factor vars - multiselect
            factor_vars_list = st.multiselect(
                "factor_vars",
                options=all_columns,
                default=default_values["factor_vars"],
                help="Select factor/categorical variables",
            )

            # Organic vars - multiselect
            organic_vars_list = st.multiselect(
                "organic_vars",
                options=all_columns,
                default=default_values["organic_vars"],
                help="Select organic/baseline variables",
            )

            # Convert lists to comma-separated strings for backward compatibility
            paid_media_spends = ", ".join(paid_media_spends_list)
            paid_media_vars = ", ".join(paid_media_vars_list)
            context_vars = ", ".join(context_vars_list)
            factor_vars = ", ".join(factor_vars_list)
            organic_vars = ", ".join(organic_vars_list)

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
                params_from_ui_single(
                    country,
                    iterations,
                    trials,
                    train_size,
                    revision,
                    start_date_str,
                    end_date_str,
                    paid_media_spends,
                    paid_media_vars,
                    context_vars,
                    factor_vars,
                    organic_vars,
                    gcs_bucket,
                    dep_var,
                    dep_var_type,
                    date_var,
                    adstock,
                    hyperparameter_preset,
                    resample_freq,  # NEW
                    resample_agg,  # NEW
                ),
                data_gcs_path,
                timestamp,
                annotations_gcs_path,
            )

        def params_from_ui_single(
            country,
            iterations,
            trials,
            train_size,
            revision,
            start_date_str,
            end_date_str,
            paid_media_spends,
            paid_media_vars,
            context_vars,
            factor_vars,
            organic_vars,
            gcs_bucket,
            dep_var,
            dep_var_type,
            date_var,
            adstock,
            hyperparameter_preset,
            resample_freq,
            resample_agg,
        ) -> dict:
            return {
                "country": country,
                "iterations": int(iterations),
                "trials": int(trials),
                "train_size": parse_train_size(train_size),
                "revision": revision,
                "start_date": start_date_str,
                "end_date": end_date_str,
                "paid_media_spends": paid_media_spends,
                "paid_media_vars": paid_media_vars,
                "context_vars": context_vars,
                "factor_vars": factor_vars,
                "organic_vars": organic_vars,
                "gcs_bucket": gcs_bucket,
                "table": "",  # Not used in GCS mode
                "query": "",  # Not used in GCS mode
                "dep_var": dep_var,
                "dep_var_type": dep_var_type,
                "date_var": date_var,
                "adstock": adstock,
                "hyperparameter_preset": hyperparameter_preset,
                "resample_freq": resample_freq,
                "resample_agg": resample_agg,
                "data_gcs_path": "",  # Will be filled later
            }

        if st.button("🚀 Start Training Job", type="primary"):
            # Validate data is loaded
            if (
                "preview_df" not in st.session_state
                or st.session_state["preview_df"] is None
            ):
                st.error(
                    "Please load data first using the 'Load selected data' button."
                )
                st.stop()

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
                        data_gcs_path = None
                        annotations_gcs_path = None

                        # Use the already loaded GCS data
                        blob_path = _get_data_blob(selected_country, selected_version)  # type: ignore
                        data_gcs_path = f"gs://{gcs_bucket}/{blob_path}"

                        # No need to query and upload - data is already in GCS
                        st.info(f"Using data from: {data_gcs_path}")

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
                                    gcs_bucket,  # type: ignore
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
                                gcs_bucket, config_path, config_blob  # type: ignore
                            )
                            _ = upload_to_gcs(
                                gcs_bucket,  # type: ignore
                                config_path,
                                "training-configs/latest/job_config.json",
                            )

                        # 5) Launch Cloud Run Job
                        with timed_step("Launch training job", timings):
                            assert TRAINING_JOB_NAME is not None
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
                                gcs_bucket = st.session_state["gcs_bucket"]

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

                uploaded_edited = st.data_editor(  # type: ignore[arg-type]
                    uploaded_view,
                    key=f"uploaded_editor_{up_nonce}",  # <= bump key when sort changes
                    num_rows="dynamic",
                    use_container_width=True,
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
                delete_col = (
                    uploaded_edited["Delete"]
                    if "Delete" in uploaded_edited
                    else pd.Series(False, index=uploaded_edited.index)
                )
                keep_mask = ~delete_col.fillna(False).astype(bool)
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
                        dup["missing_data_source"].append(i + 1)  # type: ignore
                        to_append_mask.append(False)
                        continue
                    sig = _sig_from_params_dict(params)
                    if sig in builder_sigs:
                        dup["in_builder"].append(
                            i + 1  # type: ignore
                        )  # pyright: ignore[reportOperatorIssue]
                        to_append_mask.append(False)
                        continue
                    if sig in queue_sigs:
                        dup["in_queue"].append(i + 1)  # type: ignore
                        to_append_mask.append(False)
                        continue
                    if sig in job_history_sigs:
                        dup["in_job_history"].append(i + 1)  # type: ignore
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
                width="stretch",  # type: ignore
                key=f"queue_builder_editor_{qb_nonce}",  # <= bump key when sort changes
                hide_index=True,
                column_config={
                    "Delete": st.column_config.CheckboxColumn(
                        "Delete", help="Mark to remove from builder"
                    )
                },
            )  # type: ignore

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
                    width="stretch",  # type: ignore
                    column_config=q_cfg,
                )  # type: ignore

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
                            continue  #
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
            st.dataframe(df_times, width="stretch")  # type: ignore
            st.write(f"**Total setup time:** {_fmt_secs(total)}")
            st.write(
                "**Note**: Training runs asynchronously in Cloud Run Jobs."
            )
