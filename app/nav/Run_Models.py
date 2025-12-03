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
    list_mapped_data_versions,
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

# Clear mapped data cache to ensure we get fresh data
list_mapped_data_versions.clear()

st.title("Run Marketing Mix Models")

# Check if we should show a message to switch to Queue tab (Requirement 8)
if st.session_state.get("switch_to_queue_tab", False):
    st.success("✅ **Configuration added to queue successfully!**")
    st.info(
        "👉 **Please click on the 'Queue' tab above** to monitor your job's progress."
    )
    st.session_state["switch_to_queue_tab"] = False

tab_single, tab_queue, tab_status = st.tabs(
    ["Single Run", "Batch Run", "Queue Monitor"]
)

# Prefill fields from saved metadata if present (session_state keys should already be set by Map Your Data page).


# Helper functions for GCS data loading
def _list_available_countries(bucket: str) -> List[str]:
    """List all countries that have mapped-datasets in GCS."""
    try:
        client = storage.Client()
        prefix = "mapped-datasets/"
        blobs = client.list_blobs(bucket, prefix=prefix, delimiter=None)
        countries = set()
        for blob in blobs:
            parts = blob.name.split("/")
            # mapped-datasets/<country>/<version>/raw.parquet
            if len(parts) >= 2 and parts[1]:
                countries.add(parts[1].lower())
        return sorted(list(countries)) if countries else []
    except Exception as e:
        logging.warning(f"Could not list available countries from GCS: {e}")
        return []


def _list_training_data_versions(bucket: str, country: str) -> List[str]:
    """List available selected_columns.json versions from Prepare Training Data.

    Returns list of timestamps for which selected_columns.json exists.
    Path pattern: training_data/{country}/{timestamp}/selected_columns.json
    """
    try:
        client = storage.Client()
        prefix = f"training_data/{country.lower().strip()}/"
        blobs = client.list_blobs(bucket, prefix=prefix, delimiter=None)
        versions = []
        for blob in blobs:
            if blob.name.endswith("selected_columns.json"):
                parts = blob.name.split("/")
                # training_data/<country>/<timestamp>/selected_columns.json
                if len(parts) >= 4:
                    versions.append(parts[2])
        # Sort newest first
        return sorted(versions, reverse=True) if versions else []
    except Exception as e:
        logging.warning(
            f"Could not list training data versions for {country}: {e}"
        )
        return []


def _load_training_data_json(
    bucket: str, country: str, version: str
) -> Optional[Dict]:
    """Load selected_columns.json from Prepare Training Data page."""
    try:
        client = storage.Client()
        blob_path = f"training_data/{country.lower().strip()}/{version}/selected_columns.json"
        blob = client.bucket(bucket).blob(blob_path)
        if not blob.exists():
            return None
        return json.loads(blob.download_as_bytes())
    except Exception as e:
        st.warning(f"Could not load training data config: {e}")
        return None


def _list_country_versions(bucket: str, country: str) -> List[str]:
    """Return timestamp folder names available in mapped-datasets/<country>/."""
    client = storage.Client()
    # Use mapped-datasets for Run Models (data processed through Map Data Step 3)
    prefix = f"mapped-datasets/{country.lower().strip()}/"
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
    """Get GCS blob path for mapped data (from Map Data Step 3)."""
    if version.lower() == "latest":
        return f"mapped-datasets/{country.lower().strip()}/latest/raw.parquet"
    return f"mapped-datasets/{country.lower().strip()}/{version}/raw.parquet"


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


def _get_revision_tags(bucket: str) -> List[str]:
    """Get all unique revision tags from GCS."""
    try:
        client = storage.Client()
        prefix = f"robyn/"
        blobs = client.list_blobs(bucket, prefix=prefix, delimiter=None)
        # Extract revision folders (format: TAG_NUMBER)
        revision_tags = set()
        for blob in blobs:
            parts = blob.name.split("/")
            if len(parts) >= 2 and "_" in parts[1]:
                # Extract tag from TAG_NUMBER format
                tag = parts[1].rsplit("_", 1)[0]
                revision_tags.add(tag)

        return sorted(list(revision_tags))
    except Exception:
        return []


def _get_next_revision_number(bucket: str, tag: str) -> int:
    """Get the next revision number for a given tag."""
    try:
        client = storage.Client()
        prefix = f"robyn/"
        blobs = client.list_blobs(bucket, prefix=prefix, delimiter=None)
        # Find all numbers for this tag
        numbers = []
        for blob in blobs:
            parts = blob.name.split("/")
            if len(parts) >= 2 and parts[1].startswith(f"{tag}_"):
                # Extract number from TAG_NUMBER format
                try:
                    num_str = parts[1].split("_")[-1]
                    numbers.append(int(num_str))
                except (ValueError, IndexError):
                    continue

        # Return max + 1, or 1 if no existing numbers
        return max(numbers) + 1 if numbers else 1
    except Exception:
        return 1


# Extracted from streamlit_app.py tab_single (Single run):
with tab_single:
    st.subheader("Setup an Experiment Run")

    # Data selection
    with st.expander("📊 Select Data", expanded=False):

        gcs_bucket = st.session_state.get("gcs_bucket", GCS_BUCKET)

        # Get countries from Map Data session state if available,
        # otherwise load from GCS mapped-datasets
        map_data_countries = st.session_state.get("selected_countries", [])
        prefill_countries = st.session_state.get("prefill_countries", [])

        # Combine all available countries - priority: Map Data > prefill > GCS
        if map_data_countries:
            available_countries = map_data_countries
            st.info(
                f"Using countries from Map Data: **{', '.join([c.upper() for c in available_countries])}**"
            )
        elif prefill_countries:
            available_countries = prefill_countries
            st.info(
                f"Using countries from previous session: **{', '.join([c.upper() for c in available_countries])}**"
            )
        else:
            # Load available countries from GCS mapped-datasets
            available_countries = _list_available_countries(gcs_bucket)
            if not available_countries:
                st.warning(
                    "No mapped datasets found in GCS. Please map and save data first using the Map Data page."
                )
                available_countries = ["de"]  # Default fallback

        # Store available countries in session state for use in Save Model Settings
        st.session_state["run_models_available_countries"] = available_countries

        # Allow selection of primary country
        selected_country = st.selectbox(
            "Primary Country",
            options=available_countries,
            index=0,
            help="Choose the country this model run will focus on",
        )

        # Get available metadata versions (including universal)
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

        # Get available data versions for selected country
        # Use the same function as Prepare Training Data page for consistency
        try:
            available_versions = list_mapped_data_versions(
                gcs_bucket, selected_country, refresh_key=""
            )
            if not available_versions:
                available_versions = ["Latest"]
        except Exception as e:
            st.warning(f"Could not list mapped data versions: {e}")
            available_versions = ["Latest"]

        # Mapped Data version selection - uses same list as Prepare Training Data page
        selected_version = st.selectbox(
            "Mapped Data version",
            options=available_versions,
            index=0,
            help="Select mapped data version. Uses the same list as Prepare Training Data page.",
        )

        # Metadata source selection
        selected_metadata = st.selectbox(
            "Metadata version",
            options=metadata_options,
            index=0,
            help="Select metadata configuration. Universal mappings work for all countries. Latest = most recently saved metadata.",
        )

        # Training Data Config from Prepare Training Data page
        st.markdown("---")
        st.markdown("**Training Data Configuration (from Prepare Training Data)**")
        st.caption(
            "Optionally load a saved selected_columns.json to prefill model inputs."
        )

        try:
            training_data_versions = _list_training_data_versions(
                gcs_bucket, selected_country
            )
            if training_data_versions:
                training_data_options = ["None"] + training_data_versions
            else:
                training_data_options = ["None"]
        except Exception:
            training_data_options = ["None"]

        selected_training_data = st.selectbox(
            "Select Training Data Config",
            options=training_data_options,
            index=0,
            help="Load selected_columns.json from Prepare Training Data to prefill model inputs.",
        )

        # Load and store training data config if selected
        if selected_training_data != "None":
            training_data_config = _load_training_data_json(
                gcs_bucket, selected_country, selected_training_data
            )
            if training_data_config:
                st.session_state["training_data_config"] = training_data_config
                st.success(
                    f"✅ Loaded training data config: {selected_training_data}"
                )
                with st.expander("Preview Training Data Config", expanded=False):
                    st.json(training_data_config)
            else:
                st.session_state["training_data_config"] = None
        else:
            st.session_state["training_data_config"] = None

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
    with st.expander("📥 Load Model Settings", expanded=False):
        st.caption(
            "Load a saved model setting and apply them to the current data."
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
                    "Saved settings:",
                    options=available_configs,
                    help=f"Model settings available for {current_country.upper()}",
                )

                if st.button(
                    "📥 Apply Settings",
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
                            f"✅ Setting '{selected_config}' loaded successfully!"
                        )
                        st.info(
                            "These settings are now applied to the form below."
                        )
                        st.json(config_data, expanded=False)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to load model settings: {e}")
            else:
                st.info(
                    f"No saved settings found for {current_country.upper()}"
                )
        except Exception as e:
            st.warning(f"Could not list settings: {e}")

    # Robyn config (moved outside Data selection expander)
    with st.expander("⚙️ Robyn Training Settings", expanded=False):
        # Country auto-filled from Data Selection
        country = st.session_state.get("selected_country", "fr")
        st.info(f"**Country:** {country.upper()} (from Data Selection)")

        # Check if there's a loaded configuration
        loaded_config = st.session_state.get("loaded_training_config", {})

        # Iterations and Trials as presets
        preset_options = {
            "Test Run": {"iterations": 200, "trials": 3},
            "Benchmark": {"iterations": 2000, "trials": 5},
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
            "Run Mode",
            options=list(preset_options.keys()),
            index=default_preset_index,
            help="Choose how extensive training should be",
        )
        st.caption(
            "**Test Run**: For checking output structure only •  "
            "**Benchmark**: directional comparisons only (not fully optimized) • "
            "**Production**: Full-scale experiment suited for business decisions"
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
            "Train-Test Split Ratios",
            value=(
                loaded_config.get("train_size", "0.7,0.9")
                if loaded_config
                else "0.7,0.9"
            ),
            help="Comma-separated train/validation split ratios. E.g., '0.7,0.9' means 70% train, 20% validation, 10% test.",
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
                help="Start date for training window. Typically the start of paid media spends.",
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
                help="End date for training data window. Typically the most recent paid media dates available.",
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
                    "Select Goal",
                    options=goal_options,
                    index=default_dep_var_index,
                    help="What business outcome do you want to optimize for?",
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
            "Goal type",
            options=["revenue", "conversion"],
            index=0 if dep_var_type == "revenue" else 1,
            help="'Revenue' for monetary values (e.g. GMV), 'conversion' for units (e.g. bookings).",
        )

        # Date variable
        date_var = st.text_input(
            "Date Column",
            value=(
                loaded_config.get("date_var", "date")
                if loaded_config
                else "date"
            ),
            help="Name of date column in your data",
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
            "Adstock Method",
            options=adstock_options,
            index=default_adstock_index,
            help="Adstock modeling method to use for paid media and organic variables",
        )
        st.caption("**Adstock:** models how advertising decays over time.")

        # Hyperparameters - conditional on adstock
        hyperparameter_options = [
            "Meta recommend",
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
            "Adstock hyperparameter settings",
            options=hyperparameter_options,
            index=default_hyperparameter_index,
            help="Choose predefined hyperparameter settings or define custom ranges (advanced users only).",
        )

        # Store the hyperparameter choice for later use
        st.session_state["hyperparameter_preset"] = hyperparameter_preset
        st.session_state["adstock_choice"] = adstock

        # Show info message when Custom is selected
        if hyperparameter_preset == "Custom":
            st.info(
                "📌 **Custom hyperparameters selected**: Define ranges for each paid/organic variable in the ***Variable Mapping*** below."
            )

        # Custom hyperparameters will be collected later after variables are selected
        # We need to know which variables are selected before showing per-variable hyperparameters
        custom_hyperparameters = {}

        # NEW: optional resampling
        resample_freq_label = st.selectbox(
            "Aggregate input data (optional)",
            ["None", "Weekly (W)", "Monthly (M)"],
            index=0,
            help="Aggregates the raw data before training. Column level aggregation rules from metadata apply.",
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
    with st.expander("🗺️ Choose Model Inputs", expanded=False):
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

        # Check for training data config from Prepare Training Data page
        # This takes priority over metadata defaults
        training_data_config = st.session_state.get("training_data_config")
        if training_data_config:
            st.info(
                "📋 **Using training data config from Prepare Training Data page**"
            )
            # Prefill from training data config
            if training_data_config.get("paid_media_spends"):
                default_values["paid_media_spends"] = training_data_config[
                    "paid_media_spends"
                ]
            if training_data_config.get("paid_media_vars"):
                default_values["paid_media_vars"] = training_data_config[
                    "paid_media_vars"
                ]
            if training_data_config.get("organic_vars"):
                default_values["organic_vars"] = training_data_config[
                    "organic_vars"
                ]
            if training_data_config.get("context_vars"):
                default_values["context_vars"] = training_data_config[
                    "context_vars"
                ]
            if training_data_config.get("factor_vars"):
                default_values["factor_vars"] = training_data_config[
                    "factor_vars"
                ]
            # Also update the spend_var_mapping if available
            if training_data_config.get("var_to_spend_mapping"):
                # Invert the mapping: var -> spend to spend -> var
                # The var_to_spend_mapping maps media_var -> spend_col
                # We need spend_col -> media_var for the UI
                var_to_spend = training_data_config["var_to_spend_mapping"]
                spend_to_var = {}
                for var_name, spend_name in var_to_spend.items():
                    # Only add if not already present (first occurrence wins)
                    if spend_name not in spend_to_var:
                        spend_to_var[spend_name] = var_name
                st.session_state["spend_var_mapping"] = spend_to_var

        # Extract values from metadata if available (only if no training data config)
        elif metadata and "mapping" in metadata:
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

            # Debug info to help troubleshoot : FE -> unclear
            st.info(
                f"📋 Loaded settings. Identified {len(loaded_spends)} paid media channels. "
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
                        f"🔧 Applied Variable Mappings from loaded config for {len(loaded_spends)} spends"
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
        st.markdown("**Paid Media Settings**")
        st.caption(
            "Select which paid media spends to optimize. For each, choose a metric to model it's effect"
        )

        # Use timestamp-based key to force widget refresh when config is loaded
        config_timestamp = st.session_state.get("loaded_config_timestamp", 0)

        paid_media_spends_list = st.multiselect(
            "Paid Media Spends",
            options=available_spends,
            default=default_paid_media_spends,
            help="Select media spend columns to include in the optimization",
            key=f"paid_media_spends_{config_timestamp}",
        )

        # For each selected spend, show corresponding var options
        paid_media_vars_list = []
        spend_var_mapping = {}

        if paid_media_spends_list:
            st.markdown("**Select a performance metric for each paid channel**")
            st.caption(
                "For each spend, pick a performance metric to model its effect (e.g. impressions, clicks). "
                "If left empty the spend itself will be used as a fallback."
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
                        f"**{spend}** → Performance Metric:",
                        options=var_options,
                        index=default_idx,
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
        st.caption(
            "Non-media drivers like seasonality, promotions, pricing, events, etc."
        )
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
            "Select context variables to include",
            options=all_columns,
            default=default_context_vars,
            help="Select contextual variables (e.g., seasonality, events)",
            key=f"context_vars_{config_timestamp}",
        )

        # Factor vars - multiselect
        st.markdown("**Factor Variables (True/False)**")
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
            "Select Binary Variables to include",
            options=all_columns,
            default=default_factor_vars,
            help="Binary variables (e.g. is_weekend, is_holiday)",
            key=f"factor_vars_{config_timestamp}",
        )

        # Auto-add factor_vars to context_vars (requirement 6)
        if factor_vars_list:
            context_vars_list = list(set(context_vars_list + factor_vars_list))

        # Organic vars - multiselect
        st.markdown("**Organic Variables**")
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
            "Select organic variables to include",
            options=all_columns,
            default=default_organic_vars,
            help="Non-paid channels that drive business outcomes (e.g., SEO, direct, brand search)",
            key=f"organic_vars_{config_timestamp}",
        )

        # Validate selected variables against loaded data
        # Check if any selected variables contain _CUSTOM and are not in preview_df
        preview_df = st.session_state.get("preview_df")
        if preview_df is not None:
            preview_columns = set(preview_df.columns)
            all_selected_vars = (
                paid_media_spends_list
                + paid_media_vars_list
                + context_vars_list
                + factor_vars_list
                + organic_vars_list
            )
            missing_custom_vars = [
                v
                for v in all_selected_vars
                if "_CUSTOM" in v and v not in preview_columns
            ]

            if missing_custom_vars:
                st.warning(
                    f"⚠️ **Warning:** {len(missing_custom_vars)} custom variable(s) selected but not found in loaded data: "
                    f"{', '.join(missing_custom_vars[:5])}"
                    + (
                        f" and {len(missing_custom_vars) - 5} more..."
                        if len(missing_custom_vars) > 5
                        else ""
                    )
                )
                st.info(
                    "💡 **To fix this:** Go to the 'Map Data' page, load your data, "
                    "apply mapping changes to create custom variables, "
                    "then click 'Save dataset & metadata to GCS' (this now saves both automatically)."
                )

        # Custom hyperparameters per variable (when Custom preset is selected)
        if hyperparameter_preset == "Custom":
            st.markdown("---")
            st.markdown("### 🎛️ Custom Hyperparameters per variable")
            st.info(
                "📝 **Define adstock ranges for each paid media and organic variable."
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

    # Revision Configuration (new section above Save Training Configuration)
    with st.expander("🏷️ Tag Experiment Run", expanded=False):
        st.caption(
            "Assign a name and run-number to organize your experiments. You can find the results later in the Results tab using these. Files are also stored under robyn/{TAG}_{NUMBER}/{COUNTRY}/{TIMESTAMP}/."
        )

        gcs_bucket = st.session_state.get("gcs_bucket", GCS_BUCKET)

        # Get existing revision tags
        existing_tags = _get_revision_tags(gcs_bucket)

        # Check if there's a loaded configuration
        loaded_config = st.session_state.get("loaded_training_config", {})

        # Parse loaded revision if it exists (might be old format "r100" or new format with tag/number)
        loaded_revision_tag = ""
        loaded_revision_number = None
        if loaded_config and "revision" in loaded_config:
            old_revision = loaded_config["revision"]
            # Try to parse as TAG_NUMBER format first
            if "_" in old_revision:
                parts = old_revision.rsplit("_", 1)
                loaded_revision_tag = parts[0]
                try:
                    loaded_revision_number = int(parts[1])
                except (ValueError, IndexError):
                    pass
            else:
                # Old format - treat entire string as tag
                loaded_revision_tag = old_revision

        # Also check for new format fields
        if loaded_config.get("revision_tag"):
            loaded_revision_tag = loaded_config["revision_tag"]
        if loaded_config.get("revision_number"):
            loaded_revision_number = loaded_config["revision_number"]

        # Add option to create new tag
        tag_options = ["-- Create New Tag --"] + existing_tags

        # Determine default selection
        default_tag_index = 0
        if loaded_revision_tag and loaded_revision_tag in existing_tags:
            default_tag_index = tag_options.index(loaded_revision_tag)

        # Revision tag selection/creation
        col1, col2 = st.columns(2)

        with col1:
            selected_tag_option = st.selectbox(
                "Tag Name",
                options=tag_options,
                index=default_tag_index,
                help="Choose an existing tag or create a new one. Use short identifiers like gmv_model, brand_effect, q1, crm_tests.",
            )

            # If "Create New Tag" is selected, show text input
            if selected_tag_option == "-- Create New Tag --":
                revision_tag = st.text_input(
                    "New Tag Name",
                    value=(
                        loaded_revision_tag
                        if loaded_revision_tag not in existing_tags
                        else ""
                    ),
                    placeholder="Short and descriptive, e.g., brand_search, crm_tests, q1_budget",
                    help="Use a clear identifier such as team name, purpose, or feature name.",
                )
            else:
                revision_tag = selected_tag_option
                st.info(f"Using existing tag: **{revision_tag}**")

        with col2:
            # Calculate next revision number for the selected tag
            if revision_tag and revision_tag != "-- Create New Tag --":
                next_number = _get_next_revision_number(
                    gcs_bucket, revision_tag
                )
                default_number = (
                    loaded_revision_number
                    if loaded_revision_number is not None
                    else next_number
                )

                revision_number = st.number_input(
                    "Tag Number",
                    value=default_number,
                    min_value=1,
                    step=1,
                    help=f"Next free number for this '{revision_tag}' is {next_number}. You can override if you want.",
                )

                if revision_number < next_number:
                    st.warning(
                        f"⚠️ Tag Number {revision_number} already exists for tag '{revision_tag}'. Use {next_number} or above to avoid overwriting previous runs."
                    )
            else:
                revision_number = st.number_input(
                    "Tag Number",
                    value=1,
                    min_value=1,
                    step=1,
                    help="Enter the tag number (must be numeric)",
                    disabled=True,
                )
                if not revision_tag or revision_tag == "-- Create New Tag --":
                    st.info("👆 Please enter a tag first")

        # Show the combined revision identifier
        if revision_tag and revision_tag != "-- Create New Tag --":
            combined_revision = f"{revision_tag}_{revision_number}"
            st.success(
                f"✅ Your model run will be saved under: **robyn/{combined_revision}/{{COUNTRY}}/{{TIMESTAMP}}/**"
            )
        else:
            combined_revision = ""
            st.warning("⚠️ Please select or create a tag for the experiment run")

        # For backward compatibility, create a combined "revision" field
        revision = combined_revision

    # Save Configuration (moved outside Data selection expander, after Variable Mapping)
    with st.expander("💾 Save Model Settings", expanded=False):
        st.caption(
            "Save the current model settings so you can reuse them later."
        )

        gcs_bucket = st.session_state.get("gcs_bucket", GCS_BUCKET)

        col1, col2 = st.columns([3, 1])
        with col1:
            config_name = st.text_input(
                "Settings name",
                placeholder="e.g., gmv_model_v1",
                help="Name for this training configuration",
            )
        with col2:
            # Pre-check "Multi-country" if loaded config has multiple countries
            loaded_countries = st.session_state.get(
                "loaded_config_countries", []
            )
            default_multi = len(loaded_countries) > 1
            save_for_multi = st.checkbox(
                "Apply to multiple countries",
                value=default_multi,
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
            # Use available countries from Select Data section instead of hardcoded list
            available_countries_for_multi = st.session_state.get(
                "run_models_available_countries", ["de"]
            )
            config_countries = st.multiselect(
                "Select countries",
                options=available_countries_for_multi,
                default=default_countries,
            )
        else:
            config_countries = [st.session_state.get("selected_country", "de")]

        # Add action buttons (Issue #5 fix: add queue options)
        col_btn1, col_btn2, col_btn3 = st.columns(3)

        save_config_clicked = col_btn1.button(
            "💾 Save Settings",
            use_container_width=True,
            key="save_config_btn",
        )
        add_to_queue_clicked = col_btn2.button(
            "➕ Save Settings & Add Run to Queue",
            use_container_width=True,
            key="add_to_queue_btn",
        )
        add_and_start_clicked = col_btn3.button(
            "▶️ Save, Add Run to Queue & Start Queue",
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
            "revision_tag": (
                revision_tag if revision_tag != "-- Create New Tag --" else ""
            ),
            "revision_number": (
                revision_number
                if revision_tag != "-- Create New Tag --"
                else ""
            ),
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
            "data_gcs_path": f"gs://{gcs_bucket}/mapped-datasets/{country}/latest/raw.parquet",
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
            help="Download current settings as CSV and use for batch processing",
        )

        st.markdown("---")

        if save_config_clicked:
            if not revision or not revision.strip():
                st.error("⚠️ Version tag is required to save configuration.")
            elif not config_name or not config_name.strip():
                st.error("⚠️ Setting name is required.")
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
                            "version": revision,
                            "version_tag": revision_tag,
                            "version_number": revision_number,
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
                        f"✅ Settings '{config_name}' saved for: {countries_str}"
                    )
                except Exception as e:
                    st.error(f"Failed to save settings: {e}")

        # Handle "Add to Queue" button (Issue #5 fix)
        if add_to_queue_clicked or add_and_start_clicked:
            if not revision or not revision.strip():
                st.error("⚠️ Version tag is required.")
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
                            "version": revision,
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
            "version": revision,
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

    # Get the config_countries list from Save Model Settings section
    # This is used for multi-country training
    multi_country_list = st.session_state.get(
        "run_models_available_countries", []
    )

    # Multi-country training button
    col_multi, col_single = st.columns(2)

    with col_multi:
        start_multi_training = st.button(
            "🌍 Start Training for All Countries",
            type="secondary",
            use_container_width=True,
            key="start_multi_training_job_btn",
            help=f"Start training jobs in parallel for all {len(multi_country_list)} countries",
        )

    with col_single:
        start_single_training = st.button(
            "🚀 Start Training Job",
            type="primary",
            use_container_width=True,
            key="start_training_job_btn",
        )

    # Handle multi-country training
    if start_multi_training:
        if not multi_country_list or len(multi_country_list) == 0:
            st.error(
                "⚠️ No countries available. Please load data first."
            )
            st.stop()

        if not revision or not revision.strip():
            st.error(
                "⚠️ Version tag is required. Please enter a version identifier before starting training."
            )
            st.stop()

        if not all([PROJECT_ID, REGION, TRAINING_JOB_NAME]):
            st.error(
                "Missing configuration. Check environment variables on the web service."
            )
            st.stop()

        # Add jobs to queue for all countries
        try:
            from app_split_helpers import (
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

            new_entries = []
            logging.info(
                f"[QUEUE] Starting multi-country training for: {multi_country_list}"
            )

            for i, ctry in enumerate(multi_country_list):
                # Get data source information for each country
                data_version = st.session_state.get(
                    "selected_version", "Latest"
                )
                data_blob_path = _get_data_blob(ctry, data_version.lower())

                params = {
                    "country": ctry,
                    "version": revision,
                    "date_input": time.strftime("%Y-%m-%d"),
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
                f"[QUEUE] Added {len(new_entries)} jobs for multi-country training"
            )

            # Save queue to GCS
            st.session_state.queue_saved_at = save_queue_to_gcs(
                st.session_state.queue_name,
                st.session_state.job_queue,
                queue_running=st.session_state.queue_running,
            )

            # Start the queue
            set_queue_running(st.session_state.queue_name, True)
            st.session_state.queue_running = True

            countries_str = ", ".join([c.upper() for c in multi_country_list])
            st.success(
                f"✅ Started training jobs for {len(new_entries)} countries: {countries_str}"
            )
            st.info("👉 Go to the **Queue Monitor** tab to track progress.")

            # Set flag to switch to Queue tab
            st.session_state["switch_to_queue_tab"] = True
            st.rerun()

        except Exception as e:
            st.error(f"Failed to start multi-country training: {e}")

    if start_single_training:
        # Validate revision is filled
        if not revision or not revision.strip():
            st.error(
                "⚠️ Version tag is required. Please enter a version identifier before starting training."
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

        # Use shared timestamp from Map Data if available, otherwise generate new one
        shared_ts = st.session_state.get("shared_save_timestamp", "")
        if shared_ts:
            # Timestamp format constants
            FULL_TIMESTAMP_LENGTH = 15  # YYYYMMDD_HHMMSS format length
            YEAR_PREFIX_LENGTH = 4  # Length of year prefix to remove

            # Convert from YYYYMMDD_HHMMSS to MMDD_HHMMSS format for consistency
            try:
                if len(shared_ts) >= FULL_TIMESTAMP_LENGTH:
                    timestamp = shared_ts[YEAR_PREFIX_LENGTH:]  # Remove year
                else:
                    timestamp = shared_ts
            except Exception:
                timestamp = datetime.utcnow().strftime("%m%d_%H%M%S")
        else:
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

                    # Get annotation file from session state (set in Robyn Configuration section above)
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
                            "iterations": int(iterations),
                            "trials": int(trials),
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

                            result = append_row_to_job_history(
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
                            if result:
                                st.success(
                                    f"✅ Job added to history: {gcs_prefix}"
                                )
                            else:
                                st.error(
                                    f"❌ Failed to add job to history (returned False)"
                                )
                        except Exception as e:
                            st.error(f"❌ Could not add job to history: {e}")
                            import traceback

                            st.code(traceback.format_exc())

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


# Extracted from streamlit_app.py tab_queue (Batch/Queue run):
with tab_queue:
    if st.session_state.get("queue_running") and not (
        st.session_state.get("job_queue") or []
    ):
        st.session_state.queue_running = False

    # Ensure this exists for the "Current Queue" expander in tab_status
    if "current_queue_expanded" not in st.session_state:
        st.session_state.current_queue_expanded = False

    # ========== Run Batch Experiments ==========
    st.markdown(
        "#### 📥 Run multiple experiments via batch run. Upload CSV to continue."
    )
    st.caption(
        "Upload a CSV where each row defines one experiment. You can edit rows after upload."
    )

    up = st.file_uploader("**Select CSV:**", type=["csv"], key="batch_csv")

    # Session scaffolding
    if "uploaded_df" not in st.session_state:
        st.session_state.uploaded_df = pd.DataFrame()
    if "uploaded_fingerprint" not in st.session_state:
        st.session_state.uploaded_fingerprint = None

    # Load only when the *file changes*
    if up is not None:
        fingerprint = f"{getattr(up, 'name', '')}:{getattr(up, 'size', '')}"
        if st.session_state.uploaded_fingerprint != fingerprint:
            try:
                logging.info(
                    f"[QUEUE] Uploading CSV file: {getattr(up, 'name', 'unknown')}, size: {getattr(up, 'size', 0)} bytes"
                )
                st.session_state.uploaded_df = pd.read_csv(
                    up, keep_default_na=True
                )
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

    # Helper to load current GCS queue into a DataFrame (for reset button)
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
            "gcs_bucket": p.get("gcs_bucket", st.session_state["gcs_bucket"]),
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

    def _load_queue_as_df() -> pd.DataFrame:
        payload = load_queue_payload(st.session_state.queue_name)
        existing_entries = payload.get("entries", [])
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
        return seed_df

    if st.session_state.uploaded_df.empty:
        st.caption("No CSV uploaded yet (or it has 0 rows).")
    else:
        # Work on a copy with a Delete column & sorting controls
        uploaded_view = st.session_state.uploaded_df.copy()
        if "Delete" not in uploaded_view.columns:
            uploaded_view.insert(0, "Delete", False)

        uploaded_view, up_nonce = _sorted_with_controls(
            uploaded_view, prefix="uploaded"
        )

        with st.form("uploaded_csv_form"):
            uploaded_edited = st.data_editor(  # type: ignore[arg-type]
                uploaded_view,
                key=f"uploaded_editor_{up_nonce}",
                num_rows="dynamic",
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Delete": st.column_config.CheckboxColumn(
                        "Delete", help="Mark to remove from table"
                    )
                },
            )

            # Action buttons (builder actions, now for uploaded_df)
            b1, b2, b3 = st.columns(3)
            save_uploaded_clicked = b1.form_submit_button("💾 Save edits")
            clear_uploaded_clicked = b2.form_submit_button("🧹 Clear table")
            enqueue_clicked = b3.form_submit_button("➕ Add all to queue")

        # ----- Handle actions -----

        # Always have a "clean" version of what the user sees (drop Delete)
        cleaned_uploaded = uploaded_edited.drop(
            columns="Delete", errors="ignore"
        ).reset_index(drop=True)

        if save_uploaded_clicked:
            st.session_state.uploaded_df = cleaned_uploaded
            st.success("Saved table edits.")
            st.rerun()

        if clear_uploaded_clicked:
            st.session_state.uploaded_df = pd.DataFrame()
            st.session_state.uploaded_fingerprint = None
            st.success("Cleared table.")
            st.rerun()

        # Enqueue logic (adapted from Queue Builder, now operating on uploaded_df)
        if enqueue_clicked:
            logging.info(
                f"[QUEUE] Processing 'Enqueue' from uploaded table - {len(cleaned_uploaded)} rows"
            )

            if cleaned_uploaded.dropna(how="all").empty:
                st.warning(
                    "No rows to enqueue. Add at least one non-empty row."
                )
            else:

                def _sig_from_params_dict(d: dict) -> str:
                    return json.dumps(d, sort_keys=True)

                need_cols = list(cleaned_uploaded.columns)

                # Existing queue signatures
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

                # Job history signatures
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
                        [e["id"] for e in st.session_state.job_queue],
                        default=0,
                    )
                    + 1
                )

                new_entries, enqueued_sigs = [], set()
                dup = {
                    "in_queue": [],
                    "in_job_history": [],
                    "missing_data_source": [],
                }

                for i, row in cleaned_uploaded.iterrows():
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

                _toast_dupe_summary(
                    "Enqueue", dup, added_count=len(new_entries)
                )

                if not new_entries:
                    logging.info(
                        "[QUEUE] No new entries to enqueue (all duplicates or invalid)"
                    )
                else:
                    logging.info(
                        f"[QUEUE] Enqueuing {len(new_entries)} new jobs from uploaded table (IDs: {[e['id'] for e in new_entries]})"
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

                    # Remove only enqueued rows from uploaded_df
                    def _row_sig(r: pd.Series) -> str:
                        return json.dumps(_normalize_row(r), sort_keys=True)

                    keep_mask = ~cleaned_uploaded.apply(_row_sig, axis=1).isin(
                        enqueued_sigs
                    )
                    st.session_state.uploaded_df = cleaned_uploaded.loc[
                        keep_mask
                    ].reset_index(drop=True)

                    st.success(
                        f"Enqueued {len(new_entries)} new job(s), saved to GCS, and removed them from the table."
                    )
                    # Auto-open Current Queue in status tab
                    st.session_state.current_queue_expanded = True
                    st.rerun()

    st.markdown("---")

    # ==== Define example CSVs (unchanged) ==================================
    example = pd.DataFrame(
        [
            {
                "country": "fr",
                "revision_tag": "baseline",
                "revision_number": 1,
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
                "data_gcs_path": f"gs://{st.session_state['gcs_bucket']}/mapped-datasets/fr/latest/raw.parquet",
                "table": "",
                "query": "",
                "dep_var": "UPLOAD_VALUE",
                "dep_var_type": "revenue",
                "date_var": "date",
                "adstock": "geometric",
                "hyperparameter_preset": "Meshed recommend",
                "resample_freq": "none",
                "annotations_gcs_path": "",
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
                "revision_tag": "baseline",
                "revision_number": 2,
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
                "data_gcs_path": f"gs://{st.session_state['gcs_bucket']}/mapped-datasets/de/latest/raw.parquet",
                "table": "",
                "query": "",
                "dep_var": "UPLOAD_VALUE",
                "dep_var_type": "conversion",
                "date_var": "date",
                "adstock": "weibull_cdf",
                "hyperparameter_preset": "Facebook recommend",
                "resample_freq": "W",
                "annotations_gcs_path": "",
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
                "revision_tag": "experiment",
                "revision_number": 1,
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
                "data_gcs_path": f"gs://{st.session_state['gcs_bucket']}/mapped-datasets/it/latest/raw.parquet",
                "table": "",
                "query": "",
                "dep_var": "UPLOAD_VALUE",
                "dep_var_type": "revenue",
                "date_var": "date",
                "adstock": "geometric",
                "hyperparameter_preset": "Custom",
                "resample_freq": "none",
                "annotations_gcs_path": "",
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

    example_varied = pd.DataFrame(
        [
            {
                "country": "fr",
                "revision_tag": "team_a",
                "revision_number": 1,
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
                "data_gcs_path": f"gs://{st.session_state['gcs_bucket']}/mapped-datasets/fr/latest/raw.parquet",
                "dep_var": "UPLOAD_VALUE",
                "dep_var_type": "revenue",
                "date_var": "date",
                "adstock": "geometric",
                "hyperparameter_preset": "Meshed recommend",
                "resample_freq": "none",
            },
            {
                "country": "de",
                "revision_tag": "team_b",
                "revision_number": 1,
                "start_date": "2024-01-01",
                "end_date": time.strftime("%Y-%m-%d"),
                "iterations": 200,
                "trials": 5,
                "train_size": "0.75,0.9",
                "paid_media_spends": "BING_DEMAND_COST, META_DEMAND_COST, TV_COST",
                "paid_media_vars": "BING_DEMAND_COST, META_DEMAND_COST, TV_COST",
                "context_vars": "",
                "gcs_bucket": st.session_state["gcs_bucket"],
                "data_gcs_path": f"gs://{st.session_state['gcs_bucket']}/mapped-datasets/de/latest/raw.parquet",
                "dep_var": "UPLOAD_VALUE",
                "date_var": "date",
                "adstock": "weibull_cdf",
                "hyperparameter_preset": "Facebook recommend",
                "resample_freq": "W",
            },
            {
                "country": "it",
                "revision_tag": "experimental",
                "revision_number": 5,
                "iterations": 250,
                "trials": 4,
                "train_size": "0.7,0.9",
                "paid_media_spends": "GA_SUPPLY_COST, GA_DEMAND_COST, BING_DEMAND_COST, META_DEMAND_COST",
                "paid_media_vars": "GA_SUPPLY_COST, GA_DEMAND_COST, BING_DEMAND_COST, META_DEMAND_COST",
                "organic_vars": "ORGANIC_TRAFFIC",
                "gcs_bucket": st.session_state["gcs_bucket"],
                "data_gcs_path": f"gs://{st.session_state['gcs_bucket']}/mapped-datasets/it/latest/raw.parquet",
                "dep_var": "UPLOAD_VALUE",
                "date_var": "date",
                "adstock": "geometric",
                "hyperparameter_preset": "Custom",
                "GA_SUPPLY_COST_alphas": "[0.8, 2.5]",
                "GA_SUPPLY_COST_gammas": "[0.5, 0.85]",
                "GA_SUPPLY_COST_thetas": "[0.15, 0.5]",
                "BING_DEMAND_COST_alphas": "[1.0, 3.0]",
                "BING_DEMAND_COST_gammas": "[0.6, 0.9]",
                "BING_DEMAND_COST_thetas": "[0.1, 0.4]",
            },
        ]
    )

    # ==== Top layout: left (workflow) | right (Queue + CSV templates) ======
    left_col, right_col = st.columns([2, 3])

    with left_col:
        st.write("**ℹ️ Recommended workflow**")
        st.write("1. Use **Single Run** to build and verify one experiment.")
        st.write("2. Download the configuration as a CSV.")
        st.write(
            "3. Add more rows to your CSV (e.g., more countries or variables)."
        )
        st.write("4. Upload the CSV here to create a batch queue.")

    with right_col:
        with st.expander(
            "Queue Settings", expanded=False
        ):  # --- Queue settings (top of right column) ---
            cqn1, cqn2, cqn3 = st.columns([2, 1, 1])
            new_qname = cqn1.text_input(
                "Queue name",
                key="batch_queue_name_input",
                value=st.session_state.get("queue_name", "default_queue"),
                help="Persists to GCS under robyn-queues/<name>/queue.json",
            )

            if new_qname != st.session_state.get("queue_name"):
                st.session_state["queue_name"] = new_qname

            if cqn2.button("⬇️ Load from GCS", key="batch_load_queue_from_gcs"):
                payload = load_queue_payload(st.session_state.queue_name)
                st.session_state.job_queue = payload["entries"]
                st.session_state.queue_running = payload.get(
                    "queue_running", False
                )
                st.session_state.queue_saved_at = payload.get("saved_at")
                st.success(
                    f"Loaded queue '{st.session_state.queue_name}' from GCS"
                )

            if cqn3.button("⬆️ Save to GCS", key="batch_save_queue_to_gcs"):
                st.session_state.queue_saved_at = save_queue_to_gcs(
                    st.session_state.queue_name,
                    st.session_state.job_queue,
                    queue_running=st.session_state.queue_running,
                )
                st.success(
                    f"Saved queue '{st.session_state.queue_name}' to GCS"
                )

        # --- CSV templates (collapsed, less prominent) ---
        with st.expander("CSV examples", expanded=False):
            st.caption("Download example CSVs to see the expected structure.")
            col_ex1, col_ex2 = st.columns(2)
            with col_ex1:
                st.download_button(
                    "Single-experiment template",
                    data=example.to_csv(index=False),
                    file_name="robyn_batch_example_consistent.csv",
                    mime="text/csv",
                    use_container_width=False,
                    help="All rows have the same columns – recommended starting point.",
                )
            with col_ex2:
                st.download_button(
                    "Mixed-experiments template",
                    data=example_varied.to_csv(index=False),
                    file_name="robyn_batch_example_varied.csv",
                    mime="text/csv",
                    use_container_width=False,
                    help="Rows can differ in columns – shows CSV flexibility.",
                )

    _render_flash("batch_dupes")
    maybe_refresh_queue_from_gcs()

# ===================== STATUS TAB =====================
with tab_status:

    # Job Status Monitor
    render_job_status_monitor(key_prefix="status")

    with st.expander("📋 Current Queue", expanded=False):
        # Queue controls
        st.caption(
            "Queue status: "
            f"{sum(e['status'] in ('RUNNING','LAUNCHING') for e in st.session_state.job_queue)} running"
        )

        if st.button(
            "🔁 Refresh queue",
            use_container_width=True,
            key="refresh_queue_from_gcs",
        ):
            maybe_refresh_queue_from_gcs(force=True)
            st.success("Refreshed from GCS.")
            st.rerun()

        qc1, qc2, qc3 = st.columns(3)
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
        if qc3.button("⏭️ Start Next Job", key="process_next_step_btn"):
            logging.info(
                f"[QUEUE] Manual queue tick triggered for '{st.session_state.queue_name}'"
            )
            _queue_tick()
            st.toast("Ticked queue")
            st.rerun()

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

    # Job History
    render_jobs_job_history(key_prefix="status")
