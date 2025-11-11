import base64
import io
import json
import logging
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st
from app_shared import (
    GCS_BUCKET,
    PROJECT_ID,
    REGION,
    TRAINING_JOB_NAME,
    _require_sf_session,
    get_data_processor,
    get_job_manager,
    parse_train_size,
    require_login_and_domain,
    run_sql,
    timed_step,
    upload_to_gcs,
)
from google.cloud import storage

data_processor = get_data_processor()
job_manager = get_job_manager()
from app_split_helpers import *  # bring in all helper functions/constants

require_login_and_domain()
ensure_session_defaults()

st.title("Experiment")

# Check if we should show a message to switch to Queue tab (Requirement 8)
if st.session_state.get("switch_to_queue_tab", False):
    st.success("✅ **Configuration added to queue successfully!**")
    st.info(
        "👉 **Please click on the 'Queue' tab above** to monitor your job's progress."
    )
    st.session_state["switch_to_queue_tab"] = False

tab_single, tab_queue, tab_status = st.tabs(["Single run", "Queue", "Status"])

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


def _list_metadata_versions(bucket: str, country: str) -> List[str]:
    """Return timestamp folder names available in metadata/<country>/."""
    client = storage.Client()
    prefix = f"metadata/{country.lower().strip()}/"
    blobs = client.list_blobs(bucket, prefix=prefix, delimiter=None)
    ts = set()
    for blob in blobs:
        parts = blob.name.split("/")
        if (
            len(parts) >= 4
            and parts[-1] == "mapping.json"
            and parts[-2] != "latest"
        ):
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
    with st.expander("📊 Data selection", expanded=False):
        # Show current loaded state (point 4 - UI representing actual state)
        if (
            "preview_df" in st.session_state
            and st.session_state["preview_df"] is not None
        ):
            loaded_country = st.session_state.get("selected_country", "N/A")
            loaded_version = st.session_state.get("selected_version", "N/A")
            loaded_metadata_source = st.session_state.get(
                "selected_metadata", "N/A"
            )
            st.info(
                f"🔵 **Currently Loaded:** Data: {loaded_country.upper()} - {loaded_version} | Metadata: {loaded_metadata_source}"
            )
        else:
            st.warning("⚪ No data loaded yet")

        # Country selection
        available_countries = ["fr", "de", "it", "es", "nl", "uk"]
        selected_country = st.selectbox(
            "Country",
            options=available_countries,
            index=0,
            help="Select the country for which to load data",
        )

        # Get available metadata versions (including universal)
        gcs_bucket = st.session_state.get("gcs_bucket", GCS_BUCKET)
        try:
            # Get country-specific metadata versions
            country_meta_versions = _list_metadata_versions(gcs_bucket, selected_country)  # type: ignore
            # Get universal metadata versions
            universal_meta_versions = _list_metadata_versions(gcs_bucket, "universal")  # type: ignore

            # Combine and format metadata options
            metadata_options = []
            if universal_meta_versions:
                # Add universal options first (default)
                metadata_options.extend(
                    [f"Universal - {v}" for v in universal_meta_versions]
                )
            if country_meta_versions:
                # Add country-specific options
                metadata_options.extend(
                    [
                        f"{selected_country.upper()} - {v}"
                        for v in country_meta_versions
                    ]
                )

            if not metadata_options:
                metadata_options = ["Universal - Latest"]
        except Exception as e:
            st.warning(f"Could not list metadata versions: {e}")
            metadata_options = ["Universal - Latest"]

        # Metadata source selection (NEW - above data source)
        selected_metadata = st.selectbox(
            "Metadata source",
            options=metadata_options,
            index=0,
            help="Select metadata configuration. Universal mappings work for all countries. Latest = most recently saved metadata.",
        )

        # Get available data versions for selected country
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
        if st.button(
            "Load selected data",
            type="primary",
            use_container_width=True,
            key="load_data_btn",
        ):
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

                        # Parse metadata selection to get country and version
                        meta_parts = selected_metadata.split(" - ")
                        meta_country = (
                            "universal"
                            if meta_parts[0] == "Universal"
                            else selected_country
                        )
                        meta_version = (
                            meta_parts[1] if len(meta_parts) > 1 else "Latest"
                        )

                        # Load metadata from selected source
                        metadata = _load_metadata_from_gcs(
                            gcs_bucket, meta_country, meta_version  # type: ignore
                        )
                        st.session_state["loaded_metadata"] = metadata
                        st.session_state["selected_metadata"] = (
                            selected_metadata
                        )

                        st.success(
                            f"✅ Loaded {len(df_prev)} rows, {len(df_prev.columns)} columns from **{selected_country.upper()}** - {selected_version}"
                        )
                        st.info(f"📋 Using metadata: **{selected_metadata}**")

                        # Display summary of loaded data (Issue #1 fix: show goals details)
                        with st.expander(
                            "📊 Loaded Data Summary", expanded=False
                        ):
                            st.write(
                                f"**Data Source:** {selected_country.upper()} - {selected_version}"
                            )
                            st.write(
                                f"**Metadata Source:** {selected_metadata}"
                            )
                            st.write(f"**Rows:** {len(df_prev):,}")
                            st.write(f"**Columns:** {len(df_prev.columns)}")

                            if metadata:
                                if "goals" in metadata and metadata["goals"]:
                                    st.write(
                                        f"**Goals:** {len(metadata['goals'])} goal(s)"
                                    )
                                    # Show detailed goals information
                                    for g in metadata["goals"]:
                                        main_indicator = (
                                            " (Main)"
                                            if g.get("main", False)
                                            else ""
                                        )
                                        st.write(
                                            f"  - {g['var']}: {g.get('type', 'N/A')} ({g.get('group', 'N/A')}){main_indicator}"
                                        )
                                if "mapping" in metadata:
                                    total_vars = sum(
                                        len(v)
                                        for v in metadata["mapping"].values()
                                        if isinstance(v, list)
                                    )
                                    st.write(
                                        f"**Mapped Variables:** {total_vars}"
                                    )
                                if "data" in metadata:
                                    data_info = metadata["data"]
                                    st.write(
                                        f"**Date Field:** {data_info.get('date_field', 'N/A')}"
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

    # Load Configuration (moved outside Data selection expander)
    with st.expander("📥 Load Training Configuration", expanded=False):
        st.caption(
            "Load a previously saved configuration to apply to current data."
        )

        gcs_bucket = st.session_state.get("gcs_bucket", GCS_BUCKET)

        # List available configurations
        try:
            client = storage.Client()
            current_country = st.session_state.get("selected_country", "fr")
            prefix = f"training-configs/saved/{current_country}/"
            blobs = client.list_blobs(gcs_bucket, prefix=prefix)
            available_configs = [
                blob.name.split("/")[-1].replace(".json", "")
                for blob in blobs
                if blob.name.endswith(".json")
            ]

            if available_configs:
                selected_config = st.selectbox(
                    "Select configuration to load",
                    options=available_configs,
                    help=f"Configurations available for {current_country.upper()}",
                )

                if st.button(
                    "📥 Load Configuration",
                    use_container_width=True,
                    key="load_config_btn",
                ):
                    try:
                        blob_path = f"training-configs/saved/{current_country}/{selected_config}.json"
                        blob = client.bucket(gcs_bucket).blob(blob_path)
                        config_data = json.loads(blob.download_as_bytes())

                        # Store loaded configuration in session state (including countries)
                        st.session_state["loaded_training_config"] = (
                            config_data.get("config", {})
                        )
                        st.session_state["loaded_config_countries"] = (
                            config_data.get("countries", [])
                        )
                        # Set a timestamp to force widget refresh
                        st.session_state["loaded_config_timestamp"] = (
                            datetime.utcnow().timestamp()
                        )

                        st.success(
                            f"✅ Configuration '{selected_config}' loaded successfully!"
                        )
                        st.info(
                            "The configuration values are now applied to the form below."
                        )
                        st.json(config_data, expanded=False)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to load configuration: {e}")
            else:
                st.info(
                    f"No saved configurations found for {current_country.upper()}"
                )
        except Exception as e:
            st.warning(f"Could not list configurations: {e}")

    # Robyn config (moved outside Data selection expander)
    with st.expander("⚙️ Robyn configuration", expanded=False):
        # Country auto-filled from Data Selection
        country = st.session_state.get("selected_country", "fr")
        st.info(f"**Country:** {country.upper()} (from Data Selection)")

        # Check if there's a loaded configuration
        loaded_config = st.session_state.get("loaded_training_config", {})

        # Iterations and Trials as presets
        preset_options = {
            "Test run": {"iterations": 200, "trials": 3},
            "Production": {"iterations": 10000, "trials": 5},
            "Custom": {"iterations": 5000, "trials": 10},
        }

        # Determine default preset based on loaded config
        default_preset_index = 0
        if loaded_config:
            loaded_iterations = loaded_config.get("iterations", 200)
            loaded_trials = loaded_config.get("trials", 3)
            # Check if loaded values match a preset
            for idx, (preset_name, preset_vals) in enumerate(
                preset_options.items()
            ):
                if (
                    preset_vals["iterations"] == loaded_iterations
                    and preset_vals["trials"] == loaded_trials
                ):
                    default_preset_index = idx
                    break
            else:
                # Doesn't match any preset, default to Custom
                default_preset_index = 2

        preset_choice = st.selectbox(
            "Training preset",
            options=list(preset_options.keys()),
            index=default_preset_index,
            help="Choose a training preset or use custom values",
        )

        if preset_choice == "Custom":
            col1, col2 = st.columns(2)
            with col1:
                iterations = st.number_input(
                    "Iterations",
                    value=(
                        loaded_config.get("iterations", 5000)
                        if loaded_config
                        else 5000
                    ),
                    min_value=50,
                    step=100,
                )
            with col2:
                trials = st.number_input(
                    "Trials",
                    value=(
                        loaded_config.get("trials", 10) if loaded_config else 10
                    ),
                    min_value=1,
                    step=1,
                )
        else:
            iterations = preset_options[preset_choice]["iterations"]  # type: ignore
            trials = preset_options[preset_choice]["trials"]  # type: ignore
            st.info(f"**Iterations:** {iterations}, **Trials:** {trials}")

        # Train size stays as is
        train_size = st.text_input(
            "Train size",
            value=(
                loaded_config.get("train_size", "0.7,0.9")
                if loaded_config
                else "0.7,0.9"
            ),
            help="Comma-separated train/validation split ratios",
        )

        # Revision tag - with placeholder showing latest
        latest_revision = _get_latest_revision(gcs_bucket, country)
        revision = st.text_input(
            "Revision tag",
            value=loaded_config.get("revision", "") if loaded_config else "",
            placeholder=f"Latest revision for country: {latest_revision}",
            help="Revision identifier for organizing outputs. Required before starting training.",
        )

        # Training date range instead of single date tag
        col1, col2 = st.columns(2)
        with col1:
            # Parse loaded start date if available
            default_start_date = datetime(2024, 1, 1).date()
            if loaded_config and "start_date" in loaded_config:
                try:
                    default_start_date = datetime.strptime(
                        loaded_config["start_date"], "%Y-%m-%d"
                    ).date()
                except:
                    pass

            start_data_date = st.date_input(
                "Training start date",
                value=default_start_date,
                help="Start date for training data window (start_data_date in R script)",
            )
        with col2:
            # Parse loaded end date if available
            default_end_date = datetime.now().date()
            if loaded_config and "end_date" in loaded_config:
                try:
                    default_end_date = datetime.strptime(
                        loaded_config["end_date"], "%Y-%m-%d"
                    ).date()
                except:
                    pass

            end_data_date = st.date_input(
                "Training end date",
                value=default_end_date,
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
                # Find default index for loaded dep_var
                default_dep_var_index = 0
                if loaded_config and "dep_var" in loaded_config:
                    try:
                        default_dep_var_index = goal_options.index(
                            loaded_config["dep_var"]
                        )
                    except (ValueError, KeyError):
                        pass

                dep_var = st.selectbox(
                    "Goal variable",
                    options=goal_options,
                    index=default_dep_var_index,
                    help="Primary goal variable (dependent variable) for the model",
                )
                # Find the corresponding type
                dep_var_type = next(
                    (
                        g["type"]
                        for g in metadata["goals"]
                        if g["var"] == dep_var
                    ),
                    (
                        loaded_config.get("dep_var_type", "revenue")
                        if loaded_config
                        else "revenue"
                    ),
                )
            else:
                dep_var = st.text_input(
                    "Goal variable",
                    value=(
                        loaded_config.get("dep_var", "UPLOAD_VALUE")
                        if loaded_config
                        else "UPLOAD_VALUE"
                    ),
                )
                dep_var_type = (
                    loaded_config.get("dep_var_type", "revenue")
                    if loaded_config
                    else "revenue"
                )
        else:
            dep_var = st.text_input(
                "Goal variable",
                value=(
                    loaded_config.get("dep_var", "UPLOAD_VALUE")
                    if loaded_config
                    else "UPLOAD_VALUE"
                ),
                help="Dependent variable column in your data",
            )
            dep_var_type = (
                loaded_config.get("dep_var_type", "revenue")
                if loaded_config
                else "revenue"
            )

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
            value=(
                loaded_config.get("date_var", "date")
                if loaded_config
                else "date"
            ),
            help="Date column in your data (e.g., date)",
        )

        # Adstock selection
        adstock_options = ["geometric", "weibull_cdf", "weibull_pdf"]
        default_adstock_index = 0
        if loaded_config and "adstock" in loaded_config:
            try:
                default_adstock_index = adstock_options.index(
                    loaded_config["adstock"]
                )
            except (ValueError, KeyError):
                pass

        adstock = st.selectbox(
            "adstock",
            options=adstock_options,
            index=default_adstock_index,
            help="Robyn adstock function",
        )

        # Hyperparameters - conditional on adstock
        st.write("**Hyperparameters**")
        hyperparameter_options = [
            "Facebook recommend",
            "Meshed recommend",
            "Custom",
        ]
        default_hyperparameter_index = 1
        if loaded_config and "hyperparameter_preset" in loaded_config:
            try:
                default_hyperparameter_index = hyperparameter_options.index(
                    loaded_config["hyperparameter_preset"]
                )
            except (ValueError, KeyError):
                pass

        hyperparameter_preset = st.selectbox(
            "Hyperparameter preset",
            options=hyperparameter_options,
            index=default_hyperparameter_index,
            help="Choose hyperparameter preset or define custom values",
        )

        # Store the hyperparameter choice for later use
        st.session_state["hyperparameter_preset"] = hyperparameter_preset
        st.session_state["adstock_choice"] = adstock

        # Show info message when Custom is selected
        if hyperparameter_preset == "Custom":
            st.info(
                "📌 **Custom Hyperparameters Selected**: Scroll down to the **Variable mapping** section below to configure per-variable hyperparameter ranges for each paid media and organic variable."
            )

        # Custom hyperparameters will be collected later after variables are selected
        # We need to know which variables are selected before showing per-variable hyperparameters
        custom_hyperparameters = {}

        # NEW: optional resampling
        resample_freq_label = st.selectbox(
            "Resample input data (optional)",
            ["None", "Weekly (W)", "Monthly (M)"],
            index=0,
            help="Aggregates the input before training. Column aggregations from metadata will be used.",
        )

        # Determine default resample freq from loaded config
        resample_freq_map = {
            "none": "None",
            "W": "Weekly (W)",
            "M": "Monthly (M)",
        }
        default_resample_freq = "None"
        if loaded_config and "resample_freq" in loaded_config:
            default_resample_freq = resample_freq_map.get(
                loaded_config["resample_freq"], "None"
            )

        resample_freq = {
            "None": "none",
            "Weekly (W)": "W",
            "Monthly (M)": "M",
        }.get(resample_freq_label or default_resample_freq, "none")

        # Get column aggregations from metadata
        # These will be passed to R for per-column resampling
        column_agg_strategies = {}
        if metadata and "agg_strategies" in metadata:
            column_agg_strategies = metadata["agg_strategies"]

        # Display info about column aggregations if resampling is enabled
        if resample_freq != "none" and column_agg_strategies:
            # Count aggregations by type
            agg_counts = {}
            for agg in column_agg_strategies.values():
                agg_counts[agg] = agg_counts.get(agg, 0) + 1

            agg_summary = ", ".join(
                [f"{count} {agg}" for agg, count in sorted(agg_counts.items())]
            )
            st.info(f"ℹ️ Using column aggregations from metadata: {agg_summary}")
        elif resample_freq != "none" and not column_agg_strategies:
            st.warning(
                "⚠️ No column aggregations found in metadata. Default 'sum' will be used for all numeric columns."
            )

    # Variables (moved outside Data selection expander)
    with st.expander("🗺️ Variable mapping", expanded=False):
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
            "paid_media_spends": [],
            "paid_media_vars": [],
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
                    default_values[cat] = [
                        v for v in vars_in_cat if v is not None
                    ]

        # Merge columns from preview data and metadata
        # Include all columns from metadata even if not in preview (e.g., CUSTOM columns)
        all_columns_set = set(all_columns) if all_columns else set()

        # Add all columns from metadata to the available columns
        if metadata and "mapping" in metadata:
            for cat_vars in metadata["mapping"].values():
                if isinstance(cat_vars, list):
                    all_columns_set.update(cat_vars)

        # Convert back to list
        all_columns = list(all_columns_set)

        # Note: We don't filter default_values by all_columns anymore
        # because metadata may contain CUSTOM columns not yet in preview data

        # If no data is loaded and no metadata, show a warning
        if not all_columns:
            st.warning(
                "⚠️ Please load data first to see available columns for selection."
            )

        # Helper function to parse channel and subchannel from variable name
        def _parse_var_components(var_name: str) -> dict:
            """Parse variable name into channel, subchannel, suffix"""
            parts = var_name.split("_")
            if len(parts) >= 2:
                channel = parts[0]
                suffix = parts[-1]
                subchannel = "_".join(parts[1:-1]) if len(parts) > 2 else ""
                return {
                    "channel": channel,
                    "subchannel": subchannel,
                    "suffix": suffix,
                }
            return {"channel": "", "subchannel": "", "suffix": var_name}

        # Initialize session state for spend-var mapping if not present
        if "spend_var_mapping" not in st.session_state:
            st.session_state["spend_var_mapping"] = {}

        # Get loaded configuration to apply defaults
        loaded_config = st.session_state.get("loaded_training_config", {})

        # Get all paid_media_spends from metadata (including CUSTOM columns)
        # Don't filter by all_columns since CUSTOM columns may not be in preview yet
        available_spends = default_values["paid_media_spends"]

        # Determine default selections from loaded config
        default_paid_media_spends = available_spends  # All selected by default
        if loaded_config and "paid_media_spends" in loaded_config:
            # Parse loaded config (could be comma-separated string or list)
            loaded_spends = loaded_config["paid_media_spends"]
            if isinstance(loaded_spends, str):
                loaded_spends = [
                    s.strip() for s in loaded_spends.split(",") if s.strip()
                ]
            # Use loaded spends as defaults and add them to available options
            default_paid_media_spends = loaded_spends
            # Add loaded spends to available options (preserve order: metadata first, then loaded)
            available_spends = list(
                dict.fromkeys(available_spends + loaded_spends)
            )

            # Debug info to help troubleshoot
            st.info(
                f"📋 Loaded configuration detected with {len(loaded_spends)} paid_media_spends. "
                f"Added {len([s for s in loaded_spends if s not in default_values['paid_media_spends']])} new variables not in metadata."
            )

            # Initialize spend_var_mapping from loaded config (Issue #2 fix)
            if "paid_media_vars" in loaded_config:
                loaded_vars = loaded_config["paid_media_vars"]
                if isinstance(loaded_vars, str):
                    loaded_vars = [
                        s.strip() for s in loaded_vars.split(",") if s.strip()
                    ]

                # Add loaded vars to default_values so they appear in var options
                default_values["paid_media_vars"] = list(
                    dict.fromkeys(
                        default_values["paid_media_vars"] + loaded_vars
                    )
                )

                # Build mapping: for each spend, find the corresponding var from loaded_vars
                if metadata and "paid_media_mapping" in metadata:
                    paid_media_mapping = metadata["paid_media_mapping"]
                    # For each spend, find which loaded_var belongs to it
                    for spend in loaded_spends:
                        possible_vars = paid_media_mapping.get(spend, [])
                        # Find which loaded_var is in the possible_vars for this spend
                        matched = False
                        for var in loaded_vars:
                            if var in possible_vars:
                                st.session_state["spend_var_mapping"][
                                    spend
                                ] = var
                                matched = True
                                break
                        # If no match in possible_vars, check if the spend itself is in loaded_vars
                        # (for configs where paid_media_vars == paid_media_spends)
                        if not matched and spend in loaded_vars:
                            st.session_state["spend_var_mapping"][spend] = spend
                        # Otherwise fall back to the spend itself
                        elif not matched:
                            st.session_state["spend_var_mapping"][spend] = spend

                    # Debug: Show mapping being applied
                    st.caption(
                        f"🔧 Applied variable mappings from loaded config for {len(loaded_spends)} spends"
                    )
                else:
                    # Fallback: try to match by index
                    for i, spend in enumerate(loaded_spends):
                        if i < len(loaded_vars):
                            st.session_state["spend_var_mapping"][spend] = (
                                loaded_vars[i]
                            )

        # Filter defaults to only include items in options (prevents Streamlit error on first load)
        default_paid_media_spends = [
            s for s in default_paid_media_spends if s in available_spends
        ]

        # Display paid_media_spends first (all selected by default)
        st.markdown("**Paid Media Configuration**")
        st.caption(
            "Select paid media spend channels. For each spend, you can choose the corresponding variable metric."
        )

        # Use timestamp-based key to force widget refresh when config is loaded
        config_timestamp = st.session_state.get("loaded_config_timestamp", 0)

        paid_media_spends_list = st.multiselect(
            "paid_media_spends (Select channels to include)",
            options=available_spends,
            default=default_paid_media_spends,
            help="Select media spend columns to include in the model",
            key=f"paid_media_spends_{config_timestamp}",
        )

        # For each selected spend, show corresponding var options
        paid_media_vars_list = []
        spend_var_mapping = {}

        if paid_media_spends_list:
            st.markdown("**Variable Selection for Each Spend**")
            st.caption(
                "For each spend channel, select the corresponding metric variable. "
                "If none selected, the spend column itself will be used."
            )

            for spend in paid_media_spends_list:
                # Parse the spend variable to get channel and subchannel
                parsed = _parse_var_components(spend)
                channel = parsed["channel"]
                subchannel = parsed["subchannel"]

                # Special handling for CUSTOM columns
                is_custom_spend = "_CUSTOM" in spend

                # Find all paid_media_vars with same channel and subchannel
                if is_custom_spend:
                    # For CUSTOM spends, match other CUSTOM vars with same prefix
                    # E.g., GA_SMALL_COST_CUSTOM matches GA_SMALL_*_CUSTOM
                    base_pattern = spend.replace("_COST_CUSTOM", "").replace(
                        "_COSTS_CUSTOM", ""
                    )
                    matching_vars = [
                        v
                        for v in default_values["paid_media_vars"]
                        if v.startswith(base_pattern + "_") and "_CUSTOM" in v
                    ]
                elif subchannel:
                    # Match pattern: CHANNEL_SUBCHANNEL_*
                    pattern_prefix = f"{channel}_{subchannel}_"
                    matching_vars = [
                        v
                        for v in default_values["paid_media_vars"]
                        if v.startswith(pattern_prefix)
                    ]
                else:
                    # Match pattern: CHANNEL_* (excluding CUSTOM when spend is not CUSTOM)
                    matching_vars = [
                        v
                        for v in default_values["paid_media_vars"]
                        if v.startswith(f"{channel}_")
                        and not v.endswith("_CUSTOM")
                        and "_TOTAL_" not in v
                    ]

                # Default to the first matching var or the spend itself
                default_var = st.session_state["spend_var_mapping"].get(
                    spend
                ) or (matching_vars[0] if matching_vars else spend)

                # Ensure default_var is in the options
                if default_var not in matching_vars and default_var != spend:
                    default_var = matching_vars[0] if matching_vars else spend

                # Add the spend itself as an option
                var_options = matching_vars + [spend]
                var_options = sorted(set(var_options))

                # Find the index of the default
                try:
                    default_idx = var_options.index(default_var)
                except ValueError:
                    default_idx = 0

                # Use container with custom width to ensure full variable names are visible
                with st.container():
                    selected_var = st.selectbox(
                        f"**{spend}** → Variable:",
                        options=var_options,
                        index=default_idx,
                        help=f"Select the metric variable for {spend}",
                        key=f"var_for_{spend}_{config_timestamp}",
                    )

                spend_var_mapping[spend] = selected_var
                paid_media_vars_list.append(selected_var)

            # Update session state
            st.session_state["spend_var_mapping"] = spend_var_mapping
        else:
            st.info(
                "No paid media spends selected. Select at least one to configure variables."
            )

        # Context vars - multiselect
        st.markdown("**Context Variables**")
        # Determine default from loaded config
        default_context_vars = default_values["context_vars"]
        if loaded_config and "context_vars" in loaded_config:
            loaded_context = loaded_config["context_vars"]
            if isinstance(loaded_context, str):
                loaded_context = [
                    s.strip() for s in loaded_context.split(",") if s.strip()
                ]
            default_context_vars = loaded_context
            # Add loaded context vars to available columns
            all_columns = list(dict.fromkeys(all_columns + loaded_context))

        # Filter defaults to only include items in options
        default_context_vars = [
            v for v in default_context_vars if v in all_columns
        ]

        context_vars_list = st.multiselect(
            "context_vars",
            options=all_columns,
            default=default_context_vars,
            help="Select contextual variables (e.g., seasonality, events)",
            key=f"context_vars_{config_timestamp}",
        )

        # Factor vars - multiselect
        st.markdown("**Factor Variables**")
        # Determine default from loaded config
        default_factor_vars = default_values["factor_vars"]
        if loaded_config and "factor_vars" in loaded_config:
            loaded_factor = loaded_config["factor_vars"]
            if isinstance(loaded_factor, str):
                loaded_factor = [
                    s.strip() for s in loaded_factor.split(",") if s.strip()
                ]
            default_factor_vars = loaded_factor
            # Add loaded factor vars to available columns
            all_columns = list(dict.fromkeys(all_columns + loaded_factor))

        # Filter defaults to only include items in options
        default_factor_vars = [
            v for v in default_factor_vars if v in all_columns
        ]

        factor_vars_list = st.multiselect(
            "factor_vars",
            options=all_columns,
            default=default_factor_vars,
            help="Select factor/categorical variables",
            key=f"factor_vars_{config_timestamp}",
        )

        # Auto-add factor_vars to context_vars (requirement 6)
        if factor_vars_list:
            context_vars_list = list(set(context_vars_list + factor_vars_list))

        # Organic vars - multiselect
        st.markdown("**Organic/Baseline Variables**")
        # Determine default from loaded config
        default_organic_vars = default_values["organic_vars"]
        if loaded_config and "organic_vars" in loaded_config:
            loaded_organic = loaded_config["organic_vars"]
            if isinstance(loaded_organic, str):
                loaded_organic = [
                    s.strip() for s in loaded_organic.split(",") if s.strip()
                ]
            default_organic_vars = loaded_organic
            # Add loaded organic vars to available columns
            all_columns = list(dict.fromkeys(all_columns + loaded_organic))

        # Filter defaults to only include items in options
        default_organic_vars = [
            v for v in default_organic_vars if v in all_columns
        ]

        organic_vars_list = st.multiselect(
            "organic_vars",
            options=all_columns,
            default=default_organic_vars,
            help="Select organic/baseline variables",
            key=f"organic_vars_{config_timestamp}",
        )

        # Custom hyperparameters per variable (when Custom preset is selected)
        if hyperparameter_preset == "Custom":
            st.markdown("---")
            st.markdown("### 🎛️ Custom Hyperparameters per Variable")
            st.info(
                "📝 **Per-Variable Hyperparameters**: Define custom ranges for each paid media and organic variable. Values are prefilled with Meshed recommend defaults."
            )

            # Helper function to get variable-specific defaults based on preset
            def get_var_defaults(var_name, adstock_type):
                """Get default hyperparameter ranges for a variable"""
                # Check if loaded config has this variable's hyperparameters
                if loaded_config and "custom_hyperparameters" in loaded_config:
                    var_alphas = loaded_config["custom_hyperparameters"].get(
                        f"{var_name}_alphas"
                    )
                    if var_alphas:
                        # Loaded from config
                        if adstock_type == "geometric":
                            return {
                                "alphas": var_alphas,
                                "gammas": loaded_config[
                                    "custom_hyperparameters"
                                ].get(f"{var_name}_gammas", [0.6, 0.9]),
                                "thetas": loaded_config[
                                    "custom_hyperparameters"
                                ].get(f"{var_name}_thetas", [0.1, 0.4]),
                            }
                        else:
                            return {
                                "alphas": var_alphas,
                                "shapes": loaded_config[
                                    "custom_hyperparameters"
                                ].get(f"{var_name}_shapes", [0.5, 2.5]),
                                "scales": loaded_config[
                                    "custom_hyperparameters"
                                ].get(f"{var_name}_scales", [0.001, 0.15]),
                            }

                # Use Meshed recommend defaults
                if adstock_type == "geometric":
                    if "ORGANIC" in var_name.upper():
                        return {
                            "alphas": [0.5, 2.0],
                            "gammas": [0.3, 0.7],
                            "thetas": [0.9, 0.99],
                        }
                    elif "TV" in var_name.upper():
                        return {
                            "alphas": [0.8, 2.2],
                            "gammas": [0.6, 0.99],
                            "thetas": [0.7, 0.95],
                        }
                    elif "PARTNERSHIP" in var_name.upper():
                        return {
                            "alphas": [0.65, 2.25],
                            "gammas": [0.45, 0.875],
                            "thetas": [0.3, 0.625],
                        }
                    else:
                        return {
                            "alphas": [1.0, 3.0],
                            "gammas": [0.6, 0.9],
                            "thetas": [0.1, 0.4],
                        }
                else:  # weibull
                    return {
                        "alphas": [0.5, 3.0],
                        "shapes": [0.5, 2.5],
                        "scales": [0.001, 0.15],
                    }

            # Combine all variables that need hyperparameters
            all_hyper_vars = paid_media_vars_list + organic_vars_list

            if all_hyper_vars:
                st.caption(
                    f"Configuring hyperparameters for {len(all_hyper_vars)} variable(s)"
                )

                # Use expander for each variable to keep UI manageable
                for idx, var in enumerate(all_hyper_vars):
                    with st.expander(f"📈 **{var}**", expanded=False):
                        defaults = get_var_defaults(var, adstock)

                        if adstock == "geometric":
                            col1, col2 = st.columns(2)
                            with col1:
                                alphas_min = st.number_input(
                                    "Alpha Min",
                                    value=float(defaults["alphas"][0]),
                                    min_value=0.1,
                                    max_value=10.0,
                                    step=0.1,
                                    key=f"custom_hyper_{idx}_{var}_alphas_min",
                                    help=f"Minimum alpha for {var}",
                                )
                            with col2:
                                alphas_max = st.number_input(
                                    "Alpha Max",
                                    value=float(defaults["alphas"][1]),
                                    min_value=0.1,
                                    max_value=10.0,
                                    step=0.1,
                                    key=f"custom_hyper_{idx}_{var}_alphas_max",
                                    help=f"Maximum alpha for {var}",
                                )

                            col1, col2 = st.columns(2)
                            with col1:
                                gammas_min = st.number_input(
                                    "Gamma Min",
                                    value=float(defaults["gammas"][0]),
                                    min_value=0.0,
                                    max_value=1.0,
                                    step=0.05,
                                    key=f"custom_hyper_{idx}_{var}_gammas_min",
                                    help=f"Minimum gamma for {var}",
                                )
                            with col2:
                                gammas_max = st.number_input(
                                    "Gamma Max",
                                    value=float(defaults["gammas"][1]),
                                    min_value=0.0,
                                    max_value=1.0,
                                    step=0.05,
                                    key=f"custom_hyper_{idx}_{var}_gammas_max",
                                    help=f"Maximum gamma for {var}",
                                )

                            col1, col2 = st.columns(2)
                            with col1:
                                thetas_min = st.number_input(
                                    "Theta Min",
                                    value=float(defaults["thetas"][0]),
                                    min_value=0.0,
                                    max_value=1.0,
                                    step=0.05,
                                    key=f"custom_hyper_{idx}_{var}_thetas_min",
                                    help=f"Minimum theta for {var}",
                                )
                            with col2:
                                thetas_max = st.number_input(
                                    "Theta Max",
                                    value=float(defaults["thetas"][1]),
                                    min_value=0.0,
                                    max_value=1.0,
                                    step=0.05,
                                    key=f"custom_hyper_{idx}_{var}_thetas_max",
                                    help=f"Maximum theta for {var}",
                                )

                            # Store per-variable hyperparameters
                            custom_hyperparameters[f"{var}_alphas"] = [
                                alphas_min,
                                alphas_max,
                            ]
                            custom_hyperparameters[f"{var}_gammas"] = [
                                gammas_min,
                                gammas_max,
                            ]
                            custom_hyperparameters[f"{var}_thetas"] = [
                                thetas_min,
                                thetas_max,
                            ]

                        else:  # weibull
                            col1, col2 = st.columns(2)
                            with col1:
                                alphas_min = st.number_input(
                                    "Alpha Min",
                                    value=float(defaults["alphas"][0]),
                                    min_value=0.1,
                                    max_value=10.0,
                                    step=0.1,
                                    key=f"custom_hyper_{idx}_{var}_alphas_min",
                                    help=f"Minimum alpha for {var}",
                                )
                            with col2:
                                alphas_max = st.number_input(
                                    "Alpha Max",
                                    value=float(defaults["alphas"][1]),
                                    min_value=0.1,
                                    max_value=10.0,
                                    step=0.1,
                                    key=f"custom_hyper_{idx}_{var}_alphas_max",
                                    help=f"Maximum alpha for {var}",
                                )

                            col1, col2 = st.columns(2)
                            with col1:
                                shapes_min = st.number_input(
                                    "Shape Min",
                                    value=float(defaults["shapes"][0]),
                                    min_value=0.0001,
                                    max_value=10.0,
                                    step=0.1,
                                    key=f"custom_hyper_{idx}_{var}_shapes_min",
                                    help=f"Minimum shape for {var}",
                                )
                            with col2:
                                shapes_max = st.number_input(
                                    "Shape Max",
                                    value=float(defaults["shapes"][1]),
                                    min_value=0.0001,
                                    max_value=10.0,
                                    step=0.1,
                                    key=f"custom_hyper_{idx}_{var}_shapes_max",
                                    help=f"Maximum shape for {var}",
                                )

                            col1, col2 = st.columns(2)
                            with col1:
                                scales_min = st.number_input(
                                    "Scale Min",
                                    value=float(defaults["scales"][0]),
                                    min_value=0.0,
                                    max_value=1.0,
                                    step=0.001,
                                    format="%.3f",
                                    key=f"custom_hyper_{idx}_{var}_scales_min",
                                    help=f"Minimum scale for {var}",
                                )
                            with col2:
                                scales_max = st.number_input(
                                    "Scale Max",
                                    value=float(defaults["scales"][1]),
                                    min_value=0.0,
                                    max_value=1.0,
                                    step=0.01,
                                    format="%.3f",
                                    key=f"custom_hyper_{idx}_{var}_scales_max",
                                    help=f"Maximum scale for {var}",
                                )

                            # Store per-variable hyperparameters
                            custom_hyperparameters[f"{var}_alphas"] = [
                                alphas_min,
                                alphas_max,
                            ]
                            custom_hyperparameters[f"{var}_shapes"] = [
                                shapes_min,
                                shapes_max,
                            ]
                            custom_hyperparameters[f"{var}_scales"] = [
                                scales_min,
                                scales_max,
                            ]
            else:
                st.warning(
                    "⚠️ Please select paid media and/or organic variables first to configure their hyperparameters."
                )

        # Store custom hyperparameters in session state
        st.session_state["custom_hyperparameters"] = custom_hyperparameters

        # Convert lists to comma-separated strings for backward compatibility
        paid_media_spends = ", ".join(paid_media_spends_list)
        paid_media_vars = ", ".join(paid_media_vars_list)
        context_vars = ", ".join(context_vars_list)
        factor_vars = ", ".join(factor_vars_list)
        organic_vars = ", ".join(organic_vars_list)

    # Save Configuration (moved outside Data selection expander, after Variable mapping)
    with st.expander("💾 Save Training Configuration", expanded=False):
        st.caption(
            "Save the current training configuration to apply it later to other data sources or countries."
        )

        gcs_bucket = st.session_state.get("gcs_bucket", GCS_BUCKET)

        col1, col2 = st.columns([3, 1])
        with col1:
            config_name = st.text_input(
                "Configuration name",
                placeholder="e.g., baseline_config_v1",
                help="Name for this training configuration",
            )
        with col2:
            # Pre-check "Multi-country" if loaded config has multiple countries
            loaded_countries = st.session_state.get(
                "loaded_config_countries", []
            )
            default_multi = len(loaded_countries) > 1
            save_for_multi = st.checkbox(
                "Multi-country",
                value=default_multi,
                help="Save for multiple countries",
            )

        if save_for_multi:
            # Use loaded countries if available, otherwise default to selected_country
            loaded_countries = st.session_state.get(
                "loaded_config_countries", []
            )
            default_countries = (
                loaded_countries
                if loaded_countries
                else [st.session_state.get("selected_country", "de")]
            )
            config_countries = st.multiselect(
                "Select countries",
                options=["fr", "de", "it", "es", "nl", "uk"],
                default=default_countries,
                help="Countries this configuration applies to",
            )
        else:
            config_countries = [st.session_state.get("selected_country", "de")]

        # Add action buttons (Issue #5 fix: add queue options)
        col_btn1, col_btn2, col_btn3 = st.columns(3)

        save_config_clicked = col_btn1.button(
            "💾 Save Configuration",
            use_container_width=True,
            key="save_config_btn",
        )
        add_to_queue_clicked = col_btn2.button(
            "➕ Add to Queue", use_container_width=True, key="add_to_queue_btn"
        )
        add_and_start_clicked = col_btn3.button(
            "▶️ Add to Queue & Start",
            use_container_width=True,
            key="add_and_start_btn",
        )

        # Add Download as CSV button
        st.markdown("---")

        # Helper function to convert custom_hyperparameters to CSV format
        def convert_hyperparams_to_csv_format(custom_hp, adstock_type):
            """Convert custom_hyperparameters dict to CSV column format"""
            csv_cols = {}
            if custom_hp:
                for key, value in custom_hp.items():
                    if isinstance(value, list) and len(value) == 2:
                        # Per-variable format: VAR_NAME_alphas = [min, max]
                        csv_cols[key] = str(value)
            return csv_cols

        # Build CSV row for current configuration
        csv_row = {
            "country": country,
            "revision": revision,
            "start_date": start_date_str,
            "end_date": end_date_str,
            "iterations": int(iterations),
            "trials": int(trials),
            "train_size": train_size,
            "paid_media_spends": paid_media_spends,
            "paid_media_vars": paid_media_vars,
            "context_vars": context_vars,
            "factor_vars": factor_vars,
            "organic_vars": organic_vars,
            "gcs_bucket": gcs_bucket,
            "data_gcs_path": f"gs://{gcs_bucket}/datasets/{country}/latest/raw.parquet",
            "table": "",
            "query": "",
            "dep_var": dep_var,
            "dep_var_type": dep_var_type,
            "date_var": date_var,
            "adstock": adstock,
            "hyperparameter_preset": hyperparameter_preset,
            "resample_freq": resample_freq,
            "column_agg_strategies": (
                json.dumps(column_agg_strategies)
                if column_agg_strategies
                else ""
            ),
            "annotations_gcs_path": "",
        }

        # Add custom hyperparameters to CSV row
        if hyperparameter_preset == "Custom" and custom_hyperparameters:
            csv_row.update(
                convert_hyperparams_to_csv_format(
                    custom_hyperparameters, adstock
                )
            )

        csv_df = pd.DataFrame([csv_row])

        st.download_button(
            "📥 Download as CSV",
            data=csv_df.to_csv(index=False),
            file_name=f"robyn_config_{country}_{revision}_{time.strftime('%Y%m%d')}.csv",
            mime="text/csv",
            use_container_width=True,
            help="Download current configuration as CSV for batch processing",
        )

        st.markdown("---")

        if save_config_clicked:
            if not revision or not revision.strip():
                st.error("⚠️ Revision tag is required to save configuration.")
            elif not config_name or not config_name.strip():
                st.error("⚠️ Configuration name is required.")
            else:
                try:
                    # Build configuration payload
                    config_payload = {
                        "name": config_name,
                        "created_at": datetime.utcnow().isoformat(),
                        "countries": config_countries,
                        "config": {
                            "iterations": int(iterations),
                            "trials": int(trials),
                            "train_size": train_size,
                            "revision": revision,
                            "start_date": start_date_str,
                            "end_date": end_date_str,
                            "paid_media_spends": paid_media_spends,
                            "paid_media_vars": paid_media_vars,
                            "context_vars": context_vars,
                            "factor_vars": factor_vars,
                            "organic_vars": organic_vars,
                            "dep_var": dep_var,
                            "dep_var_type": dep_var_type,
                            "date_var": date_var,
                            "adstock": adstock,
                            "hyperparameter_preset": hyperparameter_preset,
                            "custom_hyperparameters": (
                                custom_hyperparameters
                                if hyperparameter_preset == "Custom"
                                else {}
                            ),
                            "resample_freq": resample_freq,
                            "column_agg_strategies": column_agg_strategies,
                        },
                    }

                    # Save to GCS
                    client = storage.Client()
                    for ctry in config_countries:
                        blob_path = (
                            f"training-configs/saved/{ctry}/{config_name}.json"
                        )
                        blob = client.bucket(gcs_bucket).blob(blob_path)
                        blob.upload_from_string(
                            json.dumps(config_payload, indent=2),
                            content_type="application/json",
                        )

                    countries_str = ", ".join(
                        [c.upper() for c in config_countries]
                    )
                    st.success(
                        f"✅ Configuration '{config_name}' saved for: {countries_str}"
                    )
                except Exception as e:
                    st.error(f"Failed to save configuration: {e}")

        # Handle "Add to Queue" button (Issue #5 fix)
        if add_to_queue_clicked or add_and_start_clicked:
            if not revision or not revision.strip():
                st.error("⚠️ Revision tag is required.")
            else:
                try:
                    # Import helper from app_split_helpers
                    from app_split_helpers import (
                        _normalize_row,
                        save_queue_to_gcs,
                        set_queue_running,
                    )

                    # Get next queue ID
                    next_id = (
                        max(
                            [e["id"] for e in st.session_state.job_queue],
                            default=0,
                        )
                        + 1
                    )

                    # Create queue entries for each country
                    new_entries = []
                    logging.info(
                        f"[QUEUE] Adding jobs from Single Run tab for countries: {config_countries}"
                    )
                    logging.info(
                        f"[QUEUE] Starting queue ID: {next_id}, Queue name: {st.session_state.get('queue_name')}"
                    )

                    for i, ctry in enumerate(config_countries):
                        # Get data source information
                        # Use GCS path pattern from loaded data
                        data_version = st.session_state.get(
                            "selected_version", "Latest"
                        )
                        data_blob_path = _get_data_blob(
                            ctry, data_version.lower()
                        )

                        # Build params dict
                        params = {
                            "country": ctry,
                            "revision": revision,
                            "date_input": time.strftime(
                                "%Y-%m-%d"
                            ),  # Current date when job is added to queue
                            "iterations": int(iterations),
                            "trials": int(trials),
                            "train_size": train_size,
                            "paid_media_spends": paid_media_spends,
                            "paid_media_vars": paid_media_vars,
                            "context_vars": context_vars,
                            "factor_vars": factor_vars,
                            "organic_vars": organic_vars,
                            "gcs_bucket": gcs_bucket,
                            "table": "",  # Using GCS, not Snowflake
                            "query": "",  # Using GCS, not Snowflake
                            "dep_var": dep_var,
                            "dep_var_type": dep_var_type,
                            "date_var": date_var,
                            "adstock": adstock,
                            "hyperparameter_preset": hyperparameter_preset,
                            "custom_hyperparameters": (
                                custom_hyperparameters
                                if hyperparameter_preset == "Custom"
                                else {}
                            ),
                            "resample_freq": resample_freq,
                            "column_agg_strategies": column_agg_strategies,
                            "annotations_gcs_path": "",
                            "start_date": start_date_str,
                            "end_date": end_date_str,
                            "data_gcs_path": f"gs://{gcs_bucket}/{data_blob_path}",
                        }

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

                    # Add to queue
                    st.session_state.job_queue.extend(new_entries)
                    logging.info(
                        f"[QUEUE] Added {len(new_entries)} new entries to queue (IDs: {[e['id'] for e in new_entries]})"
                    )
                    logging.info(
                        f"[QUEUE] Total queue size after addition: {len(st.session_state.job_queue)}"
                    )

                    # Save queue to GCS
                    st.session_state.queue_saved_at = save_queue_to_gcs(
                        st.session_state.queue_name,
                        st.session_state.job_queue,
                        queue_running=st.session_state.queue_running,
                    )

                    # Start queue if "Add & Start" was clicked
                    if add_and_start_clicked:
                        logging.info(
                            f"[QUEUE] Starting queue '{st.session_state.queue_name}' via 'Add & Start' button"
                        )
                        set_queue_running(st.session_state.queue_name, True)
                        st.session_state.queue_running = True
                        logging.info(
                            f"[QUEUE] Queue running state set to: {st.session_state.queue_running}"
                        )

                    # Show success message
                    countries_str = ", ".join(
                        [c.upper() for c in config_countries]
                    )
                    logging.info(
                        f"[QUEUE] Successfully added jobs to queue for: {countries_str}"
                    )
                    st.success(
                        f"✅ Added {len(new_entries)} job(s) to queue for: {countries_str}"
                    )

                    # Set flag to switch to Queue tab
                    st.session_state["switch_to_queue_tab"] = True

                    # Rerun to refresh and switch tab
                    st.rerun()

                except Exception as e:
                    st.error(f"Failed to add to queue: {e}")

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
                custom_hyperparameters,  # NEW
                resample_freq,
                column_agg_strategies,
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
        custom_hyperparameters,  # NEW
        resample_freq,
        column_agg_strategies,
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
            "custom_hyperparameters": custom_hyperparameters,  # NEW
            "resample_freq": resample_freq,
            "column_agg_strategies": column_agg_strategies,
            "data_gcs_path": "",  # Will be filled later
        }

    if st.button(
        "🚀 Start Training Job",
        type="primary",
        use_container_width=True,
        key="start_training_job_btn",
    ):
        # Validate revision is filled
        if not revision or not revision.strip():
            st.error(
                "⚠️ Revision tag is required. Please enter a revision identifier before starting training."
            )
            st.stop()

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

                    # Get annotation file from session state (set in Connect_Data page)
                    ann_file = st.session_state.get("annotations_file")
                    if ann_file is not None:
                        with timed_step("Upload annotations to GCS", timings):
                            annotations_path = os.path.join(
                                td, "enriched_annotations.csv"
                            )
                            # Reset file pointer and read (with error handling)
                            try:
                                if hasattr(ann_file, "seek"):
                                    ann_file.seek(0)
                                with open(annotations_path, "wb") as f:
                                    f.write(ann_file.read())
                                annotations_blob = f"training-data/{timestamp}/enriched_annotations.csv"
                                annotations_gcs_path = upload_to_gcs(
                                    gcs_bucket,  # type: ignore
                                    annotations_path,
                                    annotations_blob,
                                )
                            except (AttributeError, IOError) as e:
                                st.warning(
                                    f"Could not read annotations file: {e}. Continuing without annotations."
                                )
                                annotations_gcs_path = None

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

                        # Store the latest job info for status monitoring
                        st.session_state["latest_job_execution"] = {
                            "execution_name": execution_name,
                            "timestamp": timestamp,
                            "revision": revision,
                            "country": country,
                            "gcs_prefix": gcs_prefix,
                        }

                        # Add job to history immediately after launch
                        try:
                            from datetime import datetime as dt

                            from app_shared import append_row_to_job_history

                            append_row_to_job_history(
                                {
                                    "job_id": gcs_prefix,
                                    "state": "RUNNING",  # Initial state
                                    "country": country,
                                    "revision": revision,
                                    "date_input": dt.utcnow().strftime(
                                        "%Y-%m-%d"
                                    ),  # Current date when job is run
                                    "iterations": int(iterations),
                                    "trials": int(trials),
                                    "train_size": train_size,
                                    "paid_media_spends": paid_media_spends,
                                    "paid_media_vars": paid_media_vars,
                                    "context_vars": context_vars,
                                    "factor_vars": factor_vars,
                                    "organic_vars": organic_vars,
                                    "gcs_bucket": gcs_bucket,
                                    "table": "",
                                    "query": "",
                                    "dep_var": dep_var,
                                    "date_var": date_var,
                                    "adstock": adstock,
                                    "start_time": dt.utcnow().isoformat(
                                        timespec="seconds"
                                    )
                                    + "Z",
                                    "end_time": None,
                                    "duration_minutes": None,
                                    "gcs_prefix": gcs_prefix,
                                    "bucket": gcs_bucket,
                                    "exec_name": execution_name.split("/")[-1],
                                    "execution_name": execution_name,
                                    "message": "Job launched from single run",
                                },
                                gcs_bucket,
                            )
                        except Exception as e:
                            st.warning(f"Could not add job to history: {e}")

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

    # =============== Job Status Display (Requirement 7) ===============
    st.divider()
    st.info(
        "👉 **View current and past job executions in the 'Status' tab above.**"
    )

    # ===================== BATCH QUEUE (CSV) =====================


# Extracted from streamlit_app.py tab_queue (Batch/Queue run):
with tab_queue:
    if st.session_state.get("queue_running") and not (
        st.session_state.get("job_queue") or []
    ):
        st.session_state.queue_running = False

    st.subheader(
        "Batch queue (CSV) — queue & run multiple jobs sequentially",
    )

    # Initialize expander state tracking if not present
    if "csv_upload_expanded" not in st.session_state:
        st.session_state.csv_upload_expanded = False
    if "queue_builder_expanded" not in st.session_state:
        st.session_state.queue_builder_expanded = False
    if "current_queue_expanded" not in st.session_state:
        st.session_state.current_queue_expanded = False

    _render_flash("batch_dupes")
    maybe_refresh_queue_from_gcs()

    # Queue name + Load/Save (outside expanders, always visible)
    cqn1, cqn2, cqn3 = st.columns([2, 1, 1])
    new_qname = cqn1.text_input(
        "Queue name",
        value=st.session_state["queue_name"],
        help="Persists to GCS under robyn-queues/<name>/queue.json",
    )
    if new_qname != st.session_state["queue_name"]:
        st.session_state["queue_name"] = new_qname

    if cqn2.button("⬇️ Load from GCS", key="load_queue_from_gcs"):
        payload = load_queue_payload(st.session_state.queue_name)
        st.session_state.job_queue = payload["entries"]
        st.session_state.queue_running = payload.get("queue_running", False)
        st.session_state.queue_saved_at = payload.get("saved_at")
        st.success(f"Loaded queue '{st.session_state.queue_name}' from GCS")

    if cqn3.button("⬆️ Save to GCS", key="save_queue_to_gcs"):
        st.session_state.queue_saved_at = save_queue_to_gcs(
            st.session_state.queue_name,
            st.session_state.job_queue,
            queue_running=st.session_state.queue_running,
        )
        st.success(f"Saved queue '{st.session_state.queue_name}' to GCS")

    # ========== EXPANDER 1: CSV Upload ==========
    with st.expander(
        "📤 CSV Upload",
        expanded=st.session_state.csv_upload_expanded,
    ):

        # Detailed instructions in expander
        with st.expander("📋 Detailed Instructions", expanded=False):
            st.markdown(
                """
Upload a CSV where each row defines a training run. **Supported columns** (all optional except `country`, `revision`, and data source):

- `country`, `revision`, `iterations`, `trials`, `train_size`
- `start_date`, `end_date` — Training window dates (YYYY-MM-DD format)
- `paid_media_spends`, `paid_media_vars`, `context_vars`, `factor_vars`, `organic_vars`
- `dep_var`, `dep_var_type` (revenue|conversion), `date_var`, `adstock`
- `hyperparameter_preset` (Facebook recommend|Meshed recommend|Custom)
- **Custom hyperparameters** (only when hyperparameter_preset=Custom):
  - For geometric adstock: `alphas_min`, `alphas_max`, `gammas_min`, `gammas_max`, `thetas_min`, `thetas_max`
  - For weibull adstock: `alphas_min`, `alphas_max`, `shapes_min`, `shapes_max`, `scales_min`, `scales_max`
  - **Per-variable hyperparameters**: `{VAR_NAME}_alphas`, `{VAR_NAME}_gammas`, `{VAR_NAME}_thetas` (for geometric) or `{VAR_NAME}_shapes`, `{VAR_NAME}_scales` (for weibull)
- `resample_freq` (none|W|M) - Column aggregations from metadata will be used when resampling
- `gcs_bucket` (optional override per row)
- **Data source (choose one):**
  - `data_gcs_path` (gs:// path to parquet file) — **Recommended for GCS-based workflows**
  - `query` or `table` — For Snowflake-based workflows
- `annotations_gcs_path` (optional gs:// path)

**Note on CSV flexibility**: Not all rows need to have the same columns. For example, rows with Custom preset can include per-variable hyperparameters while other rows can omit them. The CSV parser will fill missing columns with empty values automatically. This allows you to mix different job configurations in the same CSV file, similar to how single run jobs can have different configurations.

**Note on GCS workflows**: For GCS-based workflows (matching Single run), use `data_gcs_path`. The legacy `query`/`table` fields are still supported for Snowflake-based workflows. Column aggregations are automatically loaded from metadata.json when resampling is enabled.
                """
            )

        # Example CSV with 3 jobs including per-variable hyperparameters
        example = pd.DataFrame(
            [
                {
                    "country": "fr",
                    "revision": "r101",
                    "start_date": "2024-01-01",
                    "end_date": time.strftime("%Y-%m-%d"),
                    "iterations": 300,
                    "trials": 3,
                    "train_size": "0.7,0.9",
                    "paid_media_spends": "GA_SUPPLY_COST, GA_DEMAND_COST, META_DEMAND_COST, TV_COST",
                    "paid_media_vars": "GA_SUPPLY_COST, GA_DEMAND_COST, META_DEMAND_COST, TV_COST",
                    "context_vars": "IS_WEEKEND,TV_IS_ON",
                    "factor_vars": "IS_WEEKEND,TV_IS_ON",
                    "organic_vars": "ORGANIC_TRAFFIC",
                    "gcs_bucket": st.session_state["gcs_bucket"],
                    "data_gcs_path": f"gs://{st.session_state['gcs_bucket']}/datasets/fr/latest/raw.parquet",
                    "table": "",
                    "query": "",
                    "dep_var": "UPLOAD_VALUE",
                    "dep_var_type": "revenue",
                    "date_var": "date",
                    "adstock": "geometric",
                    "hyperparameter_preset": "Meshed recommend",
                    "resample_freq": "none",
                    "annotations_gcs_path": "",
                    # Per-variable hyperparameters (empty for non-Custom preset)
                    "GA_SUPPLY_COST_alphas": "",
                    "GA_SUPPLY_COST_gammas": "",
                    "GA_SUPPLY_COST_thetas": "",
                    "GA_DEMAND_COST_alphas": "",
                    "GA_DEMAND_COST_gammas": "",
                    "GA_DEMAND_COST_thetas": "",
                    "BING_DEMAND_COST_alphas": "",
                    "BING_DEMAND_COST_gammas": "",
                    "BING_DEMAND_COST_thetas": "",
                    "META_DEMAND_COST_alphas": "",
                    "META_DEMAND_COST_gammas": "",
                    "META_DEMAND_COST_thetas": "",
                    "ORGANIC_TRAFFIC_alphas": "",
                    "ORGANIC_TRAFFIC_gammas": "",
                    "ORGANIC_TRAFFIC_thetas": "",
                },
                {
                    "country": "de",
                    "revision": "r102",
                    "start_date": "2024-01-01",
                    "end_date": time.strftime("%Y-%m-%d"),
                    "iterations": 200,
                    "trials": 5,
                    "train_size": "0.75,0.9",
                    "paid_media_spends": "BING_DEMAND_COST, META_DEMAND_COST, TV_COST, PARTNERSHIP_COSTS",
                    "paid_media_vars": "BING_DEMAND_COST, META_DEMAND_COST, TV_COST, PARTNERSHIP_COSTS",
                    "context_vars": "",
                    "factor_vars": "",
                    "organic_vars": "ORGANIC_TRAFFIC",
                    "gcs_bucket": st.session_state["gcs_bucket"],
                    "data_gcs_path": f"gs://{st.session_state['gcs_bucket']}/datasets/de/latest/raw.parquet",
                    "table": "",
                    "query": "",
                    "dep_var": "UPLOAD_VALUE",
                    "dep_var_type": "conversion",
                    "date_var": "date",
                    "adstock": "weibull_cdf",
                    "hyperparameter_preset": "Facebook recommend",
                    "resample_freq": "W",
                    "annotations_gcs_path": "",
                    # Per-variable hyperparameters (empty for non-Custom preset)
                    "GA_SUPPLY_COST_alphas": "",
                    "GA_SUPPLY_COST_gammas": "",
                    "GA_SUPPLY_COST_thetas": "",
                    "GA_DEMAND_COST_alphas": "",
                    "GA_DEMAND_COST_gammas": "",
                    "GA_DEMAND_COST_thetas": "",
                    "BING_DEMAND_COST_alphas": "",
                    "BING_DEMAND_COST_gammas": "",
                    "BING_DEMAND_COST_thetas": "",
                    "META_DEMAND_COST_alphas": "",
                    "META_DEMAND_COST_gammas": "",
                    "META_DEMAND_COST_thetas": "",
                    "ORGANIC_TRAFFIC_alphas": "",
                    "ORGANIC_TRAFFIC_gammas": "",
                    "ORGANIC_TRAFFIC_thetas": "",
                },
                {
                    "country": "it",
                    "revision": "r103",
                    "start_date": "2024-01-01",
                    "end_date": time.strftime("%Y-%m-%d"),
                    "iterations": 250,
                    "trials": 4,
                    "train_size": "0.7,0.9",
                    "paid_media_spends": "GA_SUPPLY_COST, GA_DEMAND_COST, BING_DEMAND_COST, META_DEMAND_COST",
                    "paid_media_vars": "GA_SUPPLY_COST, GA_DEMAND_COST, BING_DEMAND_COST, META_DEMAND_COST",
                    "context_vars": "IS_WEEKEND",
                    "factor_vars": "IS_WEEKEND",
                    "organic_vars": "ORGANIC_TRAFFIC",
                    "gcs_bucket": st.session_state["gcs_bucket"],
                    "data_gcs_path": f"gs://{st.session_state['gcs_bucket']}/datasets/it/latest/raw.parquet",
                    "table": "",
                    "query": "",
                    "dep_var": "UPLOAD_VALUE",
                    "dep_var_type": "revenue",
                    "date_var": "date",
                    "adstock": "geometric",
                    "hyperparameter_preset": "Custom",
                    "resample_freq": "none",
                    "annotations_gcs_path": "",
                    # Per-variable hyperparameters for Custom preset
                    "GA_SUPPLY_COST_alphas": "[0.8, 2.5]",
                    "GA_SUPPLY_COST_gammas": "[0.5, 0.85]",
                    "GA_SUPPLY_COST_thetas": "[0.15, 0.5]",
                    "GA_DEMAND_COST_alphas": "[1.0, 3.0]",
                    "GA_DEMAND_COST_gammas": "[0.6, 0.9]",
                    "GA_DEMAND_COST_thetas": "[0.1, 0.4]",
                    "BING_DEMAND_COST_alphas": "[1.0, 3.0]",
                    "BING_DEMAND_COST_gammas": "[0.6, 0.9]",
                    "BING_DEMAND_COST_thetas": "[0.1, 0.4]",
                    "META_DEMAND_COST_alphas": "[1.0, 3.0]",
                    "META_DEMAND_COST_gammas": "[0.6, 0.9]",
                    "META_DEMAND_COST_thetas": "[0.1, 0.4]",
                    "ORGANIC_TRAFFIC_alphas": "[0.5, 2.0]",
                    "ORGANIC_TRAFFIC_gammas": "[0.3, 0.7]",
                    "ORGANIC_TRAFFIC_thetas": "[0.9, 0.99]",
                },
            ]
        )

        # Example with varying columns - demonstrates CSV flexibility
        example_varied = pd.DataFrame(
            [
                {
                    "country": "fr",
                    "revision": "r201",
                    "start_date": "2024-01-01",
                    "end_date": time.strftime("%Y-%m-%d"),
                    "iterations": 300,
                    "trials": 3,
                    "train_size": "0.7,0.9",
                    "paid_media_spends": "GA_SUPPLY_COST, GA_DEMAND_COST, META_DEMAND_COST, TV_COST",
                    "paid_media_vars": "GA_SUPPLY_COST, GA_DEMAND_COST, META_DEMAND_COST, TV_COST",
                    "context_vars": "IS_WEEKEND,TV_IS_ON",
                    "factor_vars": "IS_WEEKEND,TV_IS_ON",
                    "organic_vars": "ORGANIC_TRAFFIC",
                    "gcs_bucket": st.session_state["gcs_bucket"],
                    "data_gcs_path": f"gs://{st.session_state['gcs_bucket']}/datasets/fr/latest/raw.parquet",
                    "dep_var": "UPLOAD_VALUE",
                    "dep_var_type": "revenue",
                    "date_var": "date",
                    "adstock": "geometric",
                    "hyperparameter_preset": "Meshed recommend",
                    "resample_freq": "none",
                    # Note: This row omits table, query, annotations_gcs_path, and per-variable hyperparameters
                },
                {
                    "country": "de",
                    "revision": "r202",
                    "start_date": "2024-01-01",
                    "end_date": time.strftime("%Y-%m-%d"),
                    "iterations": 200,
                    "trials": 5,
                    "train_size": "0.75,0.9",
                    "paid_media_spends": "BING_DEMAND_COST, META_DEMAND_COST, TV_COST",
                    "paid_media_vars": "BING_DEMAND_COST, META_DEMAND_COST, TV_COST",
                    "context_vars": "",
                    "gcs_bucket": st.session_state["gcs_bucket"],
                    "data_gcs_path": f"gs://{st.session_state['gcs_bucket']}/datasets/de/latest/raw.parquet",
                    "dep_var": "UPLOAD_VALUE",
                    "date_var": "date",
                    "adstock": "weibull_cdf",
                    "hyperparameter_preset": "Facebook recommend",
                    "resample_freq": "W",
                    # Note: This row omits factor_vars, organic_vars, dep_var_type, and other optional fields
                    # Note: IS_WEEKEND is removed because weekly resampling makes it constant (no variance)
                },
                {
                    "country": "it",
                    "revision": "r203",
                    "iterations": 250,
                    "trials": 4,
                    "train_size": "0.7,0.9",
                    "paid_media_spends": "GA_SUPPLY_COST, GA_DEMAND_COST, BING_DEMAND_COST, META_DEMAND_COST",
                    "paid_media_vars": "GA_SUPPLY_COST, GA_DEMAND_COST, BING_DEMAND_COST, META_DEMAND_COST",
                    "organic_vars": "ORGANIC_TRAFFIC",
                    "gcs_bucket": st.session_state["gcs_bucket"],
                    "data_gcs_path": f"gs://{st.session_state['gcs_bucket']}/datasets/it/latest/raw.parquet",
                    "dep_var": "UPLOAD_VALUE",
                    "date_var": "date",
                    "adstock": "geometric",
                    "hyperparameter_preset": "Custom",
                    # Per-variable hyperparameters for Custom preset (only for some variables)
                    "GA_SUPPLY_COST_alphas": "[0.8, 2.5]",
                    "GA_SUPPLY_COST_gammas": "[0.5, 0.85]",
                    "GA_SUPPLY_COST_thetas": "[0.15, 0.5]",
                    "BING_DEMAND_COST_alphas": "[1.0, 3.0]",
                    "BING_DEMAND_COST_gammas": "[0.6, 0.9]",
                    "BING_DEMAND_COST_thetas": "[0.1, 0.4]",
                    # Note: This row omits start_date, end_date, context_vars, factor_vars, dep_var_type, resample_freq
                },
            ]
        )

        # Download buttons for both examples
        col_ex1, col_ex2 = st.columns(2)
        with col_ex1:
            st.download_button(
                "📥 Download Example CSV (consistent columns)",
                data=example.to_csv(index=False),
                file_name="robyn_batch_example_consistent.csv",
                mime="text/csv",
                use_container_width=True,
                help="All rows have the same columns - recommended for beginners",
            )
        with col_ex2:
            st.download_button(
                "📥 Download Example CSV (varying columns)",
                data=example_varied.to_csv(index=False),
                file_name="robyn_batch_example_varied.csv",
                mime="text/csv",
                use_container_width=True,
                help="Rows have different columns - demonstrates CSV flexibility",
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
                    # Read CSV with flexible parsing - allows missing columns per row
                    # This mimics single run behavior where not all fields are required
                    logging.info(
                        f"[QUEUE] Uploading CSV file: {getattr(up, 'name', 'unknown')}, size: {getattr(up, 'size', 0)} bytes"
                    )
                    st.session_state.uploaded_df = pd.read_csv(
                        up, keep_default_na=True
                    )

                    # Fill any missing columns that might be expected but not present
                    # This makes the CSV structure more forgiving
                    st.session_state.uploaded_fingerprint = fingerprint
                    logging.info(
                        f"[QUEUE] Successfully loaded CSV with {len(st.session_state.uploaded_df)} rows and {len(st.session_state.uploaded_df.columns)} columns"
                    )
                    st.success(
                        f"Loaded {len(st.session_state.uploaded_df)} rows from CSV"
                    )
                except Exception as e:
                    logging.error(
                        f"[QUEUE] Failed to parse CSV: {e}", exc_info=True
                    )
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
                logging.info(
                    f"[QUEUE] Processing 'Append uploaded rows to builder' - {len(uploaded_edited)} rows in uploaded table"
                )
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
                    # Check for data source: query, table, or data_gcs_path
                    if not (
                        params.get("query")
                        or params.get("table")
                        or params.get("data_gcs_path")
                    ):
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
                    logging.info(
                        f"[QUEUE] Appending {added_count} unique rows to queue builder"
                    )
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

                    logging.info(
                        f"[QUEUE] Successfully appended {added_count} rows. {len(st.session_state.uploaded_df)} rows remaining in upload table (duplicates/invalid)"
                    )
                    st.success(
                        f"Appended {added_count} row(s) to the builder. "
                        f"Remaining in upload: {len(st.session_state.uploaded_df)} duplicate/invalid row(s)."
                    )
                    # Auto-transition to Queue Builder expander
                    st.session_state.csv_upload_expanded = False
                    st.session_state.queue_builder_expanded = True
                    st.rerun()

    # ========== EXPANDER 2: Queue Builder ==========
    with st.expander(
        "✏️ Queue Builder",
        expanded=st.session_state.queue_builder_expanded,
    ):
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
                "start_date": p.get("start_date", ""),
                "end_date": p.get("end_date", ""),
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
                "data_gcs_path": p.get("data_gcs_path", ""),
                "table": p.get("table", ""),
                "query": p.get("query", ""),
                "dep_var": p.get("dep_var", ""),
                "dep_var_type": p.get("dep_var_type", "revenue"),
                "date_var": p.get("date_var", ""),
                "adstock": p.get("adstock", ""),
                "hyperparameter_preset": p.get(
                    "hyperparameter_preset", "Meshed recommend"
                ),
                "resample_freq": p.get("resample_freq", "none"),
                "annotations_gcs_path": p.get("annotations_gcs_path", ""),
            }

        seed_df = pd.DataFrame([_entry_to_row(e) for e in existing_entries])
        if seed_df.empty:
            seed_df = seed_df.reindex(
                columns=[
                    "country",
                    "revision",
                    "start_date",
                    "end_date",
                    "iterations",
                    "trials",
                    "train_size",
                    "paid_media_spends",
                    "paid_media_vars",
                    "context_vars",
                    "factor_vars",
                    "organic_vars",
                    "gcs_bucket",
                    "data_gcs_path",
                    "table",
                    "query",
                    "dep_var",
                    "dep_var_type",
                    "date_var",
                    "adstock",
                    "hyperparameter_preset",
                    "resample_freq",
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
            logging.info(
                f"[QUEUE] Processing 'Enqueue' from Queue Builder - {len(st.session_state.qb_df)} rows in builder"
            )
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
                    # Check for data source: query, table, or data_gcs_path
                    if not (
                        params.get("query")
                        or params.get("table")
                        or params.get("data_gcs_path")
                    ):
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
                    logging.info(
                        f"[QUEUE] No new entries to enqueue (all duplicates or invalid)"
                    )
                    pass
                else:
                    logging.info(
                        f"[QUEUE] Enqueuing {len(new_entries)} new jobs from builder (IDs: {[e['id'] for e in new_entries]})"
                    )
                    st.session_state.job_queue.extend(new_entries)
                    logging.info(
                        f"[QUEUE] Total queue size after enqueue: {len(st.session_state.job_queue)}"
                    )
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
                    # Auto-transition to Current Queue expander
                    st.session_state.queue_builder_expanded = False
                    st.session_state.current_queue_expanded = True
                    st.rerun()

    # ========== EXPANDER 3: Current Queue ==========
    with st.expander(
        "📋 Current Queue",
        expanded=st.session_state.current_queue_expanded,
    ):
        # Queue controls
        st.caption(
            f"Queue status: {'▶️ RUNNING' if st.session_state.queue_running else '⏸️ STOPPED'} · "
            f"{sum(e['status'] in ('RUNNING','LAUNCHING') for e in st.session_state.job_queue)} running"
        )

        if st.button(
            "🔁 Refresh from GCS",
            use_container_width=True,
            key="refresh_queue_from_gcs",
        ):
            maybe_refresh_queue_from_gcs(force=True)
            st.success("Refreshed from GCS.")
            st.rerun()

        qc1, qc2, qc3, qc4 = st.columns(4)
        if qc1.button(
            "▶️ Start Queue",
            disabled=(len(st.session_state.job_queue) == 0),
            key="start_queue_btn",
        ):
            logging.info(
                f"[QUEUE] Starting queue '{st.session_state.queue_name}' via Start button - {len(st.session_state.job_queue)} jobs in queue"
            )
            set_queue_running(st.session_state.queue_name, True)
            st.session_state.queue_running = True
            st.success("Queue set to RUNNING.")
            st.info(
                "👉 **View current and past job executions in the 'Status' tab above.**"
            )
            st.rerun()
        if qc2.button("⏸️ Stop Queue", key="stop_queue_btn"):
            logging.info(
                f"[QUEUE] Stopping queue '{st.session_state.queue_name}' via Stop button"
            )
            set_queue_running(st.session_state.queue_name, False)
            st.session_state.queue_running = False
            st.info("Queue paused.")
            st.rerun()
        if qc3.button("⏭️ Process Next Step", key="process_next_step_btn"):
            logging.info(
                f"[QUEUE] Manual queue tick triggered for '{st.session_state.queue_name}'"
            )
            _queue_tick()
            st.toast("Ticked queue")
            st.rerun()

        if qc4.button("💾 Save now", key="save_queue_now_btn"):
            save_queue_to_gcs(
                st.session_state.queue_name, st.session_state.job_queue
            )
            st.success("Queue saved to GCS.")
            # No rerun needed for save operation

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


# ===================== STATUS TAB =====================
with tab_status:
    st.subheader("Job Status & History")
    st.write(
        "Track all your training jobs - both from Single run and Queue tabs."
    )

    # Job Status Monitor
    render_job_status_monitor(key_prefix="status")

    st.divider()

    # Job History
    render_jobs_job_history(key_prefix="status")
