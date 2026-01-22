# (Updated) pages/2_Customize_Analytics.py
# - Normalizes GCS "latest" vs "Latest" so the Source list shows only one entry.
# - Replaces any "latest" items from _list_country_versions with canonical "Latest"
#   and preserves ordering / uniqueness.
import io
import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st
from app_shared import (
    GCS_BUCKET,
    PROJECT_ID,
    _require_sf_session,
    effective_sql,
    get_data_processor,
    list_data_versions,
    list_meta_versions,
    require_login_and_domain,
    run_sql,
    sync_session_state_keys,
    upload_to_gcs,
)
from app_split_helpers import *  # bring in all helper functions/constants
from google.cloud import storage
from utils.gcs_utils import format_cet_timestamp, get_cet_now

# ──────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────
ALLOWED_CATEGORIES = [
    "paid_media_spends",
    "paid_media_vars",
    "context_vars",
    "organic_vars",
    "factor_vars",
    "",
]

# ──────────────────────────────────────────────────────────────
# Setup
# ──────────────────────────────────────────────────────────────
require_login_and_domain()
ensure_session_defaults()

dp = get_data_processor()
BUCKET = st.session_state.get("gcs_bucket", GCS_BUCKET)


# Helper: GCS paths we’ll standardize on
def _data_root(country: str) -> str:
    return f"datasets/{country.lower().strip()}"


def _data_blob(country: str, ts: str) -> str:
    return f"{_data_root(country)}/{ts}/raw.parquet"


def _latest_symlink_blob(country: str) -> str:
    return f"{_data_root(country)}/latest/raw.parquet"


# Mapped data paths (for Step 3 - Save & Reuse, after variable mapping)
def _mapped_data_root(country: str) -> str:
    return f"mapped-datasets/{country.lower().strip()}"


def _mapped_data_blob(country: str, ts: str) -> str:
    return f"{_mapped_data_root(country)}/{ts}/raw.parquet"


def _mapped_latest_symlink_blob(country: str) -> str:
    return f"{_mapped_data_root(country)}/latest/raw.parquet"


def _meta_blob(country: str, ts: str) -> str:
    return f"metadata/{country.lower().strip()}/{ts}/mapping.json"


def _meta_latest_blob(country: str) -> str:
    return f"metadata/{country.lower().strip()}/latest/mapping.json"


def _list_country_versions(bucket: str, country: str) -> List[str]:
    """Return timestamp folder names available in datasets/<country>/."""
    client = storage.Client()
    b = client.bucket(bucket)
    prefix = f"{_data_root(country)}/"
    blobs = client.list_blobs(bucket, prefix=prefix, delimiter=None)
    # Extract "<ts>/" directory part between country/ and /raw.parquet
    ts = set()
    for blob in blobs:
        # want datasets/country/<ts>/raw.parquet
        parts = blob.name.split("/")
        if len(parts) >= 4 and parts[-1] == "raw.parquet":
            ts.add(parts[-2])
    return sorted(ts, reverse=True)


def _list_metadata_versions(bucket: str, country: str) -> List[str]:
    """Return timestamp folder names available in metadata/<country>/."""
    client = storage.Client()
    prefix = f"metadata/{country.lower().strip()}/"
    blobs = client.list_blobs(bucket, prefix=prefix, delimiter=None)
    # Extract "<ts>/" directory part from metadata/country/<ts>/mapping.json
    ts = set()
    for blob in blobs:
        # want metadata/country/<ts>/mapping.json
        parts = blob.name.split("/")
        if (
            len(parts) >= 4
            and parts[-1] == "mapping.json"
            and parts[-2] != "latest"
        ):
            ts.add(parts[-2])
    return sorted(ts, reverse=True)


def _download_parquet_from_gcs(gs_bucket: str, blob_path: str) -> pd.DataFrame:
    """Download parquet file from GCS with database-specific type handling."""
    import logging
    import pyarrow.parquet as pq
    import pyarrow as pa

    logger = logging.getLogger(__name__)
    client = storage.Client()
    b = client.bucket(gs_bucket)
    blob = b.blob(blob_path)
    if not blob.exists():
        raise FileNotFoundError(f"gs://{gs_bucket}/{blob_path} not found")
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
        blob.download_to_filename(tmp.name)
        try:
            # Read parquet file using PyArrow first to handle database-specific types
            table = pq.read_table(tmp.name)

            # Check for database-specific types and convert them
            schema = table.schema
            db_type_columns = []
            for i, field in enumerate(schema):
                field_type_str = str(field.type).lower()
                # Check if the type string contains database-specific type indicators
                if "db" in field_type_str and any(
                    db_type in field_type_str
                    for db_type in [
                        "dbdate",
                        "dbtime",
                        "dbdecimal",
                        "dbtimestamp",
                    ]
                ):
                    db_type_columns.append(field.name)
                    logger.warning(
                        f"Column '{field.name}' has database-specific type '{field.type}'"
                    )

            # Convert to pandas with type mapping for database-specific types
            if db_type_columns:
                logger.info(
                    f"Converting database-specific types in columns: {db_type_columns}"
                )

                # Create a types_mapper that converts unknown types to string
                def types_mapper(pa_type):
                    type_str = str(pa_type).lower()
                    if "db" in type_str:
                        # Map database types to string for safe conversion
                        return pd.StringDtype()
                    return None  # Use default mapping for other types

                df = table.to_pandas(types_mapper=types_mapper)
            else:
                # No database-specific types, use standard conversion
                df = table.to_pandas()

            # Log data types for debugging
            logger.info(
                f"Loaded parquet from gs://{gs_bucket}/{blob_path}: "
                f"{len(df)} rows, {len(df.columns)} columns"
            )

            return df
        except Exception as e:
            logger.error(
                f"Error reading parquet file from gs://{gs_bucket}/{blob_path}: {e}"
            )
            raise


def _save_raw_to_gcs(
    df: pd.DataFrame, bucket: str, country: str, timestamp: str = None
) -> Dict[str, str]:
    ts = timestamp or format_cet_timestamp()
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
        df.to_parquet(tmp.name, index=False)
        data_gcs_path = upload_to_gcs(bucket, tmp.name, _data_blob(country, ts))
        # maintain "latest" copy
        upload_to_gcs(bucket, tmp.name, _latest_symlink_blob(country))
    return {"timestamp": ts, "data_gcs_path": data_gcs_path}


def _save_mapped_to_gcs(
    df: pd.DataFrame, bucket: str, country: str, timestamp: str = None
) -> Dict[str, str]:
    """Save mapped dataset to mapped-datasets/ path (separate from raw datasets)."""
    ts = timestamp or format_cet_timestamp()
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
        df.to_parquet(tmp.name, index=False)
        data_gcs_path = upload_to_gcs(
            bucket, tmp.name, _mapped_data_blob(country, ts)
        )
        # maintain "latest" copy
        upload_to_gcs(bucket, tmp.name, _mapped_latest_symlink_blob(country))
    return {"timestamp": ts, "data_gcs_path": data_gcs_path}


def _safe_json_dump_to_gcs(payload: dict, bucket: str, dest_blob: str):
    b = storage.Client().bucket(bucket)
    blob = b.blob(dest_blob)
    blob.upload_from_string(
        json.dumps(payload, indent=2), content_type="application/json"
    )


# --- Cache I/O ---
@st.cache_data(show_spinner=False)
def _list_country_versions_cached(bucket: str, country: str) -> list[str]:
    return _list_country_versions(bucket, country)


@st.cache_data(show_spinner=False)
def _list_metadata_versions_cached(bucket: str, country: str) -> list[str]:
    return _list_metadata_versions(bucket, country)


@st.cache_data(show_spinner=False)
def _download_parquet_from_gcs_cached(
    gs_bucket: str, blob_path: str
) -> pd.DataFrame:
    return _download_parquet_from_gcs(gs_bucket, blob_path)


@st.cache_data(show_spinner=False)
def _load_from_snowflake_cached(sql: str) -> pd.DataFrame:
    _require_sf_session()
    return run_sql(sql)


def _simplify_error_message(
    error_msg: str, data_source: str, country: str
) -> str:
    """
    Convert technical database errors into user-friendly messages.

    Args:
        error_msg: The raw error message from the database
        data_source: The data source type (Snowflake, BigQuery, CSV Upload)
        country: The country code being processed

    Returns:
        A simplified, user-friendly error message
    """
    error_lower = error_msg.lower()

    # Snowflake invalid identifier errors
    if "invalid identifier" in error_lower and data_source == "Snowflake":
        # Extract the invalid column name if present
        import re

        match = re.search(r"invalid identifier ['\"]?(\w+)['\"]?", error_lower)
        if match:
            col_name = match.group(1).upper()
            return f"Column '{col_name}' not found in Snowflake table. Check your country field name or table structure."
        return "Invalid column name in Snowflake query. Check your country field name."

    # BigQuery table qualification errors
    if (
        "must be qualified with a dataset" in error_lower
        and data_source == "BigQuery"
    ):
        # Extract table name if present
        import re

        match = re.search(
            r'table ["\']?(\w+)["\']?', error_lower, re.IGNORECASE
        )
        if match:
            table_name = match.group(1)
            return f"Table '{table_name}' needs dataset qualification. Use format: project.dataset.table (e.g., 'my-project.my_dataset.{table_name}')"
        return "Table name needs dataset qualification. Use format: project.dataset.table"

    # BigQuery syntax errors
    if "syntax error" in error_lower and data_source == "BigQuery":
        return "SQL syntax error in BigQuery query. Check your table name and SQL syntax."

    # Snowflake SQL compilation errors
    if "sql compilation error" in error_lower and data_source == "Snowflake":
        # Try to extract the specific error
        import re

        match = re.search(
            r"error line \d+ at position \d+ (.+?)(?:\.|$)", error_lower
        )
        if match:
            specific_error = match.group(1).strip()
            return f"SQL error in Snowflake: {specific_error}. Check your query syntax and column names."
        return "SQL compilation error in Snowflake. Check your table name, country field, and query syntax."

    # Connection errors
    if "connection" in error_lower or "timeout" in error_lower:
        return f"Connection problem with {data_source}. Check your network and credentials."

    # Permission errors
    if (
        "permission" in error_lower
        or "access denied" in error_lower
        or "forbidden" in error_lower
    ):
        return f"Permission denied for {data_source}. Check your access rights to the table."

    # Table not found errors
    if (
        "does not exist" in error_lower
        or "not found" in error_lower
        or "unknown table" in error_lower
    ):
        return f"Table not found in {data_source}. Check that the table name is correct and you have access."

    # If error is very long (> 200 chars), truncate but keep the beginning
    if len(error_msg) > 200:
        return error_msg[:197] + "..."

    # Return original error if we can't simplify it
    return error_msg


# --- Session bootstrap (call once, early) ---
def _init_state():
    st.session_state.setdefault("country", "de")
    # Multi-country support: list of selected countries
    st.session_state.setdefault("selected_countries", [])
    st.session_state.setdefault("df_raw", pd.DataFrame())
    # Per-country dataframes for multi-country support
    st.session_state.setdefault("df_raw_by_country", {})
    st.session_state.setdefault("data_origin", "")
    st.session_state.setdefault("picked_ts", "")
    # Shared timestamp for saving metadata and datasets
    st.session_state.setdefault("shared_save_timestamp", "")
    st.session_state.setdefault(
        "goals_df",
        pd.DataFrame(columns=["var", "group", "type", "main"]).astype("object"),
    )
    st.session_state.setdefault(
        "auto_rules",
        {
            "paid_media_spends": [
                "_cost",
                "_spend",
                "_costs",
                "_spends",
                "_budget",
                "_amount",
            ],
            "paid_media_vars": ["_impressions", "_clicks", "_sessions"],
            "context_vars": ["_index", "_temp", "_price", "_holiday"],
            "organic_vars": ["_organic", "_direct"],
            "factor_vars": ["_flag", "_is", "_on"],
        },
    )
    st.session_state.setdefault(
        "mapping_df",
        pd.DataFrame(
            columns=[
                "var",
                "category",
                "channel",
                "data_type",
                "agg_strategy",
                "custom_tags",
            ]
        ).astype("object"),
    )
    st.session_state.setdefault("custom_channels", [])
    st.session_state.setdefault("last_saved_raw_path", "")
    st.session_state.setdefault("last_saved_meta_path", "")
    st.session_state.setdefault("organic_vars_prefix", "organic_")
    st.session_state.setdefault("context_vars_prefix", "context_")
    st.session_state.setdefault("factor_vars_prefix", "factor_")
    st.session_state.setdefault("aggregation_sources", {})
    # Track loaded metadata source for UI feedback
    st.session_state.setdefault("loaded_metadata_source", "")


_init_state()

# Optional: fragments if your Streamlit supports it (safe no-op fallback)
_fragment = getattr(
    st,
    "fragment",
    lambda f=None, **_: (lambda *a, **k: f(*a, **k)) if f else (lambda f: f),
)


def _guess_goal_type(col: str) -> str:
    """
    Guess goal type based on column name.
    Only returns a type if we're very confident based on suffix patterns.
    Returns empty string if uncertain - user must tag manually.
    """
    s = col.lower()

    # Very specific patterns we're confident about
    if s.endswith("_gmv") or s.endswith("_revenue") or s.endswith("_rev"):
        return "revenue"

    # For other cases, return empty string so user must tag manually
    return ""


# ──────────────────────────────────────────────────────────────
# Channel detection helpers
# ──────────────────────────────────────────────────────────────
def _get_known_channels() -> list[str]:
    """Return list of known marketing channel names."""
    return [
        "meta",
        "facebook",
        "fb",
        "instagram",
        "ig",
        "tiktok",
        "google",
        "ga",
        "googleanalytics",
        "adwords",
        "bing",
        "youtube",
        "yt",
        "tv",
        "television",
        "partnership",
        "partner",
        "affiliate",
        "email",
        "newsletter",
        "twitter",
        "x",
        "linkedin",
        "pinterest",
        "snapchat",
        "reddit",
        "amazon",
        "display",
        "programmatic",
        "video",
        "search",
        "sem",
        "seo",
        "organic",
        "direct",
        "referral",
        "paid",
        "ppc",
    ]


def _extract_channel_from_column(col: str) -> str:
    """
    Extract channel name from column if it follows pattern "<CHANNEL>_<something>".
    Returns empty string if no channel detected.
    """
    col_lower = col.lower().strip()

    # Use custom channels if explicitly set, otherwise use known channels
    custom_channels = st.session_state.get("custom_channels", [])
    if custom_channels:
        # When custom channels are set, ONLY use those (don't include known channels)
        all_channels = custom_channels
    else:
        # Fall back to known channels if no custom channels are set
        all_channels = _get_known_channels()

    # Check if column starts with a channel followed by underscore
    for channel in all_channels:
        if col_lower.startswith(f"{channel}_"):
            return channel

    # No channel detected
    return ""


def _detect_data_type(df: pd.DataFrame, col: str) -> str:
    """
    Detect if a column is numeric or categorical.
    Returns 'numeric' or 'categorical'.
    """
    if col not in df.columns:
        return "numeric"

    dtype = df[col].dtype

    # Check bool first (pandas considers bool as numeric, but we want it as categorical)
    if pd.api.types.is_bool_dtype(dtype):
        return "categorical"
    # Check pandas dtype
    elif pd.api.types.is_numeric_dtype(dtype):
        return "numeric"
    elif str(dtype) == "category" or pd.api.types.is_object_dtype(dtype):
        return "categorical"
    else:
        # Default to numeric for datetime and other types
        return "numeric"


def _default_agg_strategy(data_type: str) -> str:
    """
    Return default aggregation strategy based on data type.
    For numeric: 'sum'
    For categorical: 'auto'
    """
    if data_type == "numeric":
        return "sum"
    else:
        return "auto"


def _build_mapping_df(
    all_cols: list[str], df_raw: pd.DataFrame, rules: dict
) -> pd.DataFrame:
    """
    Build a complete mapping DataFrame with all required columns.
    Helper function to reduce code duplication.
    """
    return pd.DataFrame(
        {
            "var": pd.Series(all_cols, dtype="object"),
            "category": pd.Series(
                [_infer_category(c, rules) for c in all_cols],
                dtype="object",
            ),
            "channel": pd.Series(
                [_extract_channel_from_column(c) for c in all_cols],
                dtype="object",
            ),
            "data_type": pd.Series(
                [_detect_data_type(df_raw, c) for c in all_cols],
                dtype="object",
            ),
            "agg_strategy": pd.Series(
                [
                    _default_agg_strategy(_detect_data_type(df_raw, c))
                    for c in all_cols
                ],
                dtype="object",
            ),
            "custom_tags": pd.Series([""] * len(all_cols), dtype="object"),
        }
    ).astype("object")


def _initial_goals_from_columns() -> pd.DataFrame:
    # Return empty DataFrame - user must select goals manually
    return pd.DataFrame(columns=["var", "group", "type", "main"]).astype(
        "object"
    )


def _download_json_from_gcs(gs_bucket: str, blob_path: str) -> dict:
    client = storage.Client()
    blob = client.bucket(gs_bucket).blob(blob_path)
    if not blob.exists():
        raise FileNotFoundError(f"gs://{gs_bucket}/{blob_path} not found")
    return json.loads(blob.download_as_bytes())


def _infer_category(col: str, rules: dict[str, list[str]]) -> str:
    s = str(col).lower()
    for cat, endings in rules.items():
        for suf in endings:
            if s.endswith(str(suf).lower()):
                return cat
    return ""


def _parse_variable_name(var_name: str) -> dict:
    """
    Parse a variable name into components: CHANNEL_SUBCHANNEL_SUFFIX
    Returns dict with keys: channel, subchannel, suffix, original
    """
    parts = var_name.split("_")
    if len(parts) >= 2:
        channel = parts[0]
        suffix = parts[-1]
        subchannel = "_".join(parts[1:-1]) if len(parts) > 2 else ""
        return {
            "channel": channel,
            "subchannel": subchannel,
            "suffix": suffix,
            "original": var_name,
        }
    return {
        "channel": "",
        "subchannel": "",
        "suffix": var_name,
        "original": var_name,
    }


def _apply_automatic_aggregations(
    mapping_df: pd.DataFrame, df_raw: pd.DataFrame, prefixes: dict = None
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """
    Apply automatic aggregations based on custom tags and categories.
    Returns: (updated_mapping_df, updated_df_raw, aggregation_sources)

    aggregation_sources is a dict mapping each _CUSTOM column name to its
    source info: {"source_columns": [...], "agg_method": "sum"/"mean"/...}

    This function:
    1. Copies aggregations from paid_media_spends to paid_media_vars
    2. Creates custom tag aggregates (e.g., GA_SMALL_COST_CUSTOM)
    3. Prefixes organic, context, and factor variables with configurable prefixes
    4. Creates TOTAL columns for each channel/suffix grouping
    """
    # Default prefixes if not provided
    if prefixes is None:
        prefixes = {
            "organic_vars": "ORGANIC_",
            "context_vars": "CONTEXT_",
            "factor_vars": "FACTOR_",
        }

    new_mapping_rows = []
    new_columns = {}
    aggregation_sources = {}  # Track source columns for each custom aggregate

    # Group variables by category
    paid_spends = mapping_df[
        mapping_df["category"] == "paid_media_spends"
    ].copy()
    paid_vars = mapping_df[mapping_df["category"] == "paid_media_vars"].copy()
    organic = mapping_df[mapping_df["category"] == "organic_vars"].copy()
    context = mapping_df[mapping_df["category"] == "context_vars"].copy()
    factor = mapping_df[mapping_df["category"] == "factor_vars"].copy()

    # 1. Copy aggregations from paid_media_spends to paid_media_vars
    for _, spend_row in paid_spends.iterrows():
        var_name = str(spend_row["var"])
        parsed = _parse_variable_name(var_name)
        channel = parsed["channel"]
        subchannel = parsed["subchannel"]
        suffix = parsed["suffix"]

        # Find corresponding paid_media_vars with same channel and subchannel
        if subchannel:
            pattern = f"{channel}_{subchannel}_"
        else:
            pattern = f"{channel}_"

        # Copy custom tags and agg_strategy to matching paid_media_vars
        matching_vars = paid_vars[
            paid_vars["var"].str.startswith(pattern, na=False)
        ]
        for idx, var_row in matching_vars.iterrows():
            if str(spend_row.get("custom_tags", "")).strip():
                mapping_df.at[idx, "custom_tags"] = spend_row["custom_tags"]
            if str(spend_row.get("agg_strategy", "")).strip():
                mapping_df.at[idx, "agg_strategy"] = spend_row["agg_strategy"]

    # Refresh after copying
    paid_spends = mapping_df[
        mapping_df["category"] == "paid_media_spends"
    ].copy()
    paid_vars = mapping_df[mapping_df["category"] == "paid_media_vars"].copy()

    # 2. Create custom tag aggregates for paid_media_spends and paid_media_vars
    for category in ["paid_media_spends", "paid_media_vars"]:
        cat_df = mapping_df[mapping_df["category"] == category].copy()

        # Group by channel and custom tags
        for channel in cat_df["channel"].dropna().unique():
            if not str(channel).strip():
                continue

            channel_rows = cat_df[cat_df["channel"] == channel]

            # Collect all custom tags for this channel
            all_tags = set()
            for tags_str in channel_rows["custom_tags"].dropna():
                tags = [
                    t.strip() for t in str(tags_str).split(",") if t.strip()
                ]
                all_tags.update(tags)

            # For each unique tag, create aggregates for each suffix
            for tag in all_tags:
                # Get rows with this tag
                tag_rows = channel_rows[
                    channel_rows["custom_tags"]
                    .fillna("")
                    .str.contains(
                        r"\b" + str(tag) + r"\b", case=False, regex=True
                    )
                ]

                # Group by suffix
                suffixes = {}
                for _, row in tag_rows.iterrows():
                    parsed = _parse_variable_name(str(row["var"]))
                    suffix = parsed["suffix"]
                    if suffix not in suffixes:
                        suffixes[suffix] = []
                    suffixes[suffix].append(str(row["var"]))

                # Create custom aggregate for each suffix
                for suffix, vars_list in suffixes.items():
                    custom_var_name = f"{channel.upper()}_{tag.upper()}_{suffix.upper()}_CUSTOM"

                    # Always track aggregation sources (even if column already exists)
                    aggregation_sources[custom_var_name] = {
                        "source_columns": vars_list,
                        "agg_method": "sum",
                        "category": category,
                        "channel": channel,
                        "custom_tag": tag,
                    }

                    # Only create if it doesn't already exist
                    if custom_var_name not in mapping_df["var"].values:
                        new_mapping_rows.append(
                            {
                                "var": custom_var_name,
                                "category": category,
                                "channel": channel,
                                "data_type": "numeric",
                                "agg_strategy": "sum",
                                "custom_tags": tag,
                            }
                        )

                        # Calculate the sum in df_raw
                        if all(v in df_raw.columns for v in vars_list):
                            new_columns[custom_var_name] = df_raw[
                                vars_list
                            ].sum(axis=1)

    # 3. Create TOTAL columns for each category/channel/suffix grouping
    for category in ["paid_media_spends", "paid_media_vars"]:
        cat_df = mapping_df[mapping_df["category"] == category].copy()

        # Group by channel and suffix
        for channel in cat_df["channel"].dropna().unique():
            if not str(channel).strip():
                continue

            channel_rows = cat_df[cat_df["channel"] == channel]

            # Group by suffix (excluding _CUSTOM columns)
            suffixes = {}
            subchannels_by_suffix = {}  # Track unique subchannels per suffix
            for _, row in channel_rows.iterrows():
                var_name = str(row["var"])
                if "_CUSTOM" in var_name:
                    continue
                parsed = _parse_variable_name(var_name)
                suffix = parsed["suffix"]
                subchannel = parsed["subchannel"]

                if suffix not in suffixes:
                    suffixes[suffix] = []
                    subchannels_by_suffix[suffix] = set()

                suffixes[suffix].append(var_name)
                if subchannel:  # Only track non-empty subchannels
                    subchannels_by_suffix[suffix].add(subchannel)

            # Create TOTAL for each suffix (only if multiple subchannels exist)
            for suffix, vars_list in suffixes.items():
                # Don't create TOTAL if channel has only one subchannel
                if len(subchannels_by_suffix.get(suffix, set())) <= 1:
                    continue

                total_var_name = (
                    f"{channel.upper()}_TOTAL_{suffix.upper()}_CUSTOM"
                )

                # Always track aggregation sources (even if column already exists)
                aggregation_sources[total_var_name] = {
                    "source_columns": vars_list,
                    "agg_method": "sum",
                    "category": category,
                    "channel": channel,
                    "custom_tag": "TOTAL",
                }

                # Only create if it doesn't already exist
                if total_var_name not in mapping_df["var"].values:
                    new_mapping_rows.append(
                        {
                            "var": total_var_name,
                            "category": category,
                            "channel": channel,
                            "data_type": "numeric",
                            "agg_strategy": "sum",
                            "custom_tags": "",
                        }
                    )

                    # Calculate the sum in df_raw
                    if all(v in df_raw.columns for v in vars_list):
                        new_columns[total_var_name] = df_raw[vars_list].sum(
                            axis=1
                        )

    # 4. For organic_vars category, create custom tag aggregates and TOTAL
    if not organic.empty:
        # Collect custom tags
        all_tags = set()
        for tags_str in organic["custom_tags"].dropna():
            tags = [t.strip() for t in str(tags_str).split(",") if t.strip()]
            all_tags.update(tags)

        # Create custom tag aggregates
        for tag in all_tags:
            tag_rows = organic[
                organic["custom_tags"]
                .fillna("")
                .str.contains(r"\b" + str(tag) + r"\b", case=False, regex=True)
            ]
            vars_list = tag_rows["var"].tolist()

            if vars_list:
                # Extract common suffix from all variables in the group (Issue #3 fix)
                # For example: NL_DAILY_SESSIONS, SEO_DAILY_SESSIONS -> suffix should be SESSIONS
                # Find the longest common suffix across all variables
                suffixes = []
                for var in vars_list:
                    parsed = _parse_variable_name(var)
                    suffixes.append(parsed["suffix"])

                # Use the most common suffix (or first if all are different)
                if suffixes:
                    from collections import Counter

                    suffix_counts = Counter(suffixes)
                    suffix = suffix_counts.most_common(1)[0][0]
                else:
                    suffix = "SESSIONS"  # Default fallback

                # Use configurable prefix
                organic_prefix = (
                    prefixes.get("organic_vars", "ORGANIC_").rstrip("_").upper()
                )
                custom_var_name = (
                    f"{organic_prefix}_{tag.upper()}_{suffix.upper()}_CUSTOM"
                )

                # Always track aggregation sources (even if column already exists)
                aggregation_sources[custom_var_name] = {
                    "source_columns": vars_list,
                    "agg_method": "sum",
                    "category": "organic_vars",
                    "channel": "organic",
                    "custom_tag": tag,
                }

                if custom_var_name not in mapping_df["var"].values:
                    new_mapping_rows.append(
                        {
                            "var": custom_var_name,
                            "category": "organic_vars",
                            "channel": "organic",
                            "data_type": "numeric",
                            "agg_strategy": "sum",
                            "custom_tags": tag,
                        }
                    )

                    # Calculate sum
                    if all(v in df_raw.columns for v in vars_list):
                        new_columns[custom_var_name] = df_raw[vars_list].sum(
                            axis=1
                        )

        # Create ORGANIC_TOTAL (sum of all organic vars excluding _CUSTOM)
        organic_vars = [
            str(v)
            for v in organic["var"]
            if "_CUSTOM" not in str(v) and str(v) in df_raw.columns
        ]

        # Use configurable prefix
        organic_prefix = (
            prefixes.get("organic_vars", "ORGANIC_").rstrip("_").upper()
        )
        total_var_name = f"{organic_prefix}_TOTAL_CUSTOM"

        # Always track aggregation sources if we have organic vars (even if column already exists)
        if organic_vars:
            aggregation_sources[total_var_name] = {
                "source_columns": organic_vars,
                "agg_method": "sum",
                "category": "organic_vars",
                "channel": "organic",
                "custom_tag": "TOTAL",
            }

            if total_var_name not in mapping_df["var"].values:
                new_mapping_rows.append(
                    {
                        "var": total_var_name,
                        "category": "organic_vars",
                        "channel": "organic",
                        "data_type": "numeric",
                        "agg_strategy": "sum",
                        "custom_tags": "",
                    }
                )

                new_columns[total_var_name] = df_raw[organic_vars].sum(axis=1)

    # Add new mapping rows
    if new_mapping_rows:
        new_df = pd.DataFrame(new_mapping_rows).astype("object")
        mapping_df = pd.concat([mapping_df, new_df], ignore_index=True)

    # Add new columns to df_raw
    for col_name, col_data in new_columns.items():
        if col_name not in df_raw.columns:
            df_raw[col_name] = col_data

    return mapping_df, df_raw, aggregation_sources


def _apply_metadata_to_current_df(
    meta: dict, current_cols: list[str], df_raw: pd.DataFrame
) -> None:
    # goals (keep only ones that exist now)
    meta_goals = meta.get("goals", []) or []
    g = (
        pd.DataFrame(meta_goals).astype("object")
        if meta_goals
        else pd.DataFrame(columns=["var", "group", "type", "main"]).astype(
            "object"
        )
    )
    # Add main column if it doesn't exist in loaded metadata
    if "main" not in g.columns:
        g["main"] = False
    g = g[g["var"].isin(current_cols)]
    st.session_state["goals_df"] = g

    # rules
    if isinstance(meta.get("autotag_rules"), dict):
        st.session_state["auto_rules"] = {
            k: [str(x) for x in v] for k, v in meta["autotag_rules"].items()
        }

    # custom channels
    if isinstance(meta.get("custom_channels"), list):
        st.session_state["custom_channels"] = meta["custom_channels"]

    # variable prefixes
    if isinstance(meta.get("variable_prefixes"), dict):
        prefixes = meta["variable_prefixes"]
        st.session_state["organic_vars_prefix"] = prefixes.get(
            "organic_vars", "organic_"
        )
        st.session_state["context_vars_prefix"] = prefixes.get(
            "context_vars", "context_"
        )
        st.session_state["factor_vars_prefix"] = prefixes.get(
            "factor_vars", "factor_"
        )

    # mapping → build a full mapping_df for current columns
    meta_map = meta.get("mapping", {}) or {}
    # Flatten mapping dict: {cat: [vars]}
    var_to_cat = {}
    for cat, vars_ in meta_map.items():
        for v in vars_ or []:
            var_to_cat[str(v)] = cat

    # Get channel, data_type, and agg_strategy from metadata if available
    meta_channels = meta.get("channels", {}) or {}
    meta_data_types = meta.get("data_types", {}) or {}
    meta_agg_strategies = meta.get("agg_strategies", {}) or {}

    rows = []
    for c in current_cols:
        cat = var_to_cat.get(c)
        if not cat:
            # fallback to rules for new cols
            cat = _infer_category(c, st.session_state["auto_rules"])

        # Get or detect channel
        channel = meta_channels.get(c, "") or _extract_channel_from_column(c)

        # Get or detect data type
        data_type = meta_data_types.get(c, "") or _detect_data_type(df_raw, c)

        # Get or default aggregation strategy
        agg_strategy = meta_agg_strategies.get(c, "") or _default_agg_strategy(
            data_type
        )

        rows.append(
            {
                "var": c,
                "category": cat or "",
                "channel": channel,
                "data_type": data_type,
                "agg_strategy": agg_strategy,
                "custom_tags": "",
            }
        )
    st.session_state["mapping_df"] = pd.DataFrame(rows).astype("object")


# --- Country options: ISO2 with GCS-first ordering ---
@st.cache_data(show_spinner=False)
def _iso2_countries_gcs_first(bucket: str) -> list[str]:
    try:
        import pycountry

        # Explicitly type the loop variable and cast to avoid Pylance complaints
        all_iso2 = sorted(
            {
                str(getattr(c, "alpha_2", "")).lower()
                for c in list(pycountry.countries)
                if getattr(c, "alpha_2", None)
            }
        )
    except Exception:
        # Fallback if pycountry isn't installed
        all_iso2 = sorted(
            [
                "us",
                "gb",
                "de",
                "fr",
                "es",
                "it",
                "nl",
                "se",
                "no",
                "fi",
                "dk",
                "ie",
                "pt",
                "pl",
                "cz",
                "hu",
                "at",
                "ch",
                "be",
                "ca",
                "mx",
                "br",
                "ar",
                "cl",
                "co",
                "pe",
                "au",
                "nz",
                "jp",
                "kr",
                "cn",
                "in",
                "sg",
                "my",
                "th",
                "ph",
                "id",
                "ae",
                "sa",
                "tr",
                "za",
            ]
        )

    has_data, no_data = [], []
    for code in all_iso2:
        try:
            versions = _list_country_versions_cached(bucket, code)
            (has_data if versions else no_data).append(code)
        except Exception:
            no_data.append(code)
    return has_data + no_data


# ──────────────────────────────────────────────────────────────
# Page header & helper image
# ──────────────────────────────────────────────────────────────
st.title("Map Data & Define Goals")

# sensible defaults so we can read these anywhere
st.session_state.setdefault("sf_table", "DB.SCHEMA.TABLE")
st.session_state.setdefault("sf_sql", "")
st.session_state.setdefault("sf_country_field", "COUNTRY")
st.session_state.setdefault("bq_table", "")
st.session_state.setdefault("bq_sql", "")
st.session_state.setdefault("bq_country_field", "country")
st.session_state.setdefault("source_mode", "Latest (GCS)")

# ──────────────────────────────────────────────────────────────
# Step 1) Choose your dataset
# ──────────────────────────────────────────────────────────────
st.header("1. Select Dataset")

with st.expander("📊 Choose the data you want to analyze.", expanded=False):

    # ═══════════════════════════════════════════════════════════
    # STEP 1.1: Data Source Type Selection (Radio Buttons)
    # ═══════════════════════════════════════════════════════════
    st.markdown("#### 1.1 Choose Data Source Type")

    # Radio button for primary selection (horizontal layout)
    data_source_mode = st.radio(
        "How would you like to load data?",
        options=[
            "Load previously saved data from GCS",
            "Connect and load new dataset",
        ],
        key="data_source_mode",
        horizontal=True,
        help="Choose whether to load already saved data or connect to a new data source",
    )

    st.divider()

    @_fragment()
    def step1_loader():
        # Get available GCS versions for the first country (for UI display)
        # We need at least one country to get versions, so use a default if none selected yet
        temp_country = st.session_state.get("country", "de")

        # Get available GCS versions for version checking (UI display)
        versions_raw = _list_country_versions_cached(
            BUCKET, temp_country
        )  # e.g. ["20250107_101500", "20241231_235959", "latest"]
        # Normalize versions: canonicalize any 'latest' -> 'Latest' and de-duplicate, preserving order
        seen = set()
        versions = []
        for v in versions_raw:
            vv = "Latest" if str(v).lower() == "latest" else v
            if vv not in seen:
                versions.append(vv)
                seen.add(vv)

        # ═══════════════════════════════════════════════════════════
        # STEP 1.2: Data Source Selection Based on Mode
        # ═══════════════════════════════════════════════════════════
        st.markdown("#### 1.2 Select Data Source")

        # Get data source mode from radio button
        data_source_mode = st.session_state.get(
            "data_source_mode", "Load previously saved data from GCS"
        )

        # Show appropriate UI based on mode
        if data_source_mode == "Load previously saved data from GCS":
            # Show GCS version selector
            st.info("📦 Loading from previously saved datasets in GCS")

            # Split options into two categories
            # 1. Previously loaded data (GCS versions)
            saved_data_options = ["Latest"] + [
                v for v in versions if v != "Latest"
            ]

            current_source = st.session_state.get("source_choice", "Latest")
            if current_source not in saved_data_options:
                current_source = "Latest"

            source_choice = st.selectbox(
                "Select GCS version:",
                options=saved_data_options,
                index=saved_data_options.index(current_source),
                key="gcs_version_choice",
                help="Choose which saved dataset version to load",
            )

        else:
            # Show new data source options with all 3 options in dropdown
            # Build new source options with connection status
            sf_connected = st.session_state.get("sf_connected", False)
            bq_connected = (
                st.session_state.get("bq_connected", False)
                and st.session_state.get("bq_client") is not None
            )
            csv_connected = (
                st.session_state.get("csv_connected", False)
                and st.session_state.get("csv_data") is not None
            )

            # All data source options (always show all 3)
            all_source_options = ["Snowflake", "BigQuery", "CSV Upload"]
            connection_status = {
                "Snowflake": sf_connected,
                "BigQuery": bq_connected,
                "CSV Upload": csv_connected,
            }

            # Get current selection or default to first option
            current_source = st.session_state.get("source_choice", None)
            if current_source not in all_source_options:
                current_source = "Snowflake"

            # Create columns for dropdown and connection status
            col1, col2 = st.columns([3, 1])

            with col1:
                source_choice = st.selectbox(
                    "Select data source:",
                    options=all_source_options,
                    index=all_source_options.index(current_source),
                    key="new_source_selection",
                    help="Choose which data source to load from",
                )

            with col2:
                # Show connection status for the selected source
                st.write("")  # Add spacing to align with selectbox
                if connection_status.get(source_choice, False):
                    st.success("✅ Connected")
                else:
                    st.warning("⚠️ Not connected")

            # Show message if not connected
            if not connection_status.get(source_choice, False):
                st.caption(
                    f"💡 Connect to {source_choice} in the **Connect Data** page"
                )

        # Store the final choice
        st.session_state["source_choice"] = source_choice

        # Show appropriate inputs only for the selected new data source (OUTSIDE FORM)
        if source_choice == "Snowflake":
            with st.expander("❄️ Snowflake Query", expanded=True):
                st.text_input(
                    "Select table",
                    key="sf_table",
                )
                st.text_area("Or: Write a custom SQL", key="sf_sql")
                st.text_input("Select country field:", key="sf_country_field")

        elif source_choice == "BigQuery":
            with st.expander("🔍 BigQuery Query", expanded=True):
                st.text_input(
                    "Table ID (project.dataset.table)",
                    key="bq_table",
                    value=st.session_state.get("bq_table", ""),
                    help="Fully qualified table ID: project.dataset.table",
                )
                st.text_area(
                    "Or: Write a custom SQL query",
                    key="bq_sql",
                    value=st.session_state.get("bq_sql", ""),
                    help="Custom BigQuery SQL. Use {country} as placeholder for country filter.",
                )
                st.text_input(
                    "Country field name:",
                    key="bq_country_field",
                    value=st.session_state.get("bq_country_field", "country"),
                    help="Column name to filter by country",
                )

        elif source_choice == "CSV Upload":
            with st.expander("📁 CSV Upload", expanded=True):
                csv_filename = st.session_state.get("csv_filename", "unknown")
                csv_shape = (
                    st.session_state.get("csv_data").shape
                    if st.session_state.get("csv_data") is not None
                    else (0, 0)
                )
                st.info(
                    f"**Loaded CSV**: {csv_filename}\n\n**Shape**: {csv_shape[0]:,} rows × {csv_shape[1]} columns"
                )
                st.caption(
                    "Note: CSV data will be used as-is for all selected countries."
                )

        # ═══════════════════════════════════════════════════════════
        # STEP 1.3: Country Selection
        # ═══════════════════════════════════════════════════════════
        st.divider()
        st.markdown("#### 1.3 Select Countries")

        # Country picker (ISO2, GCS-first) as multiselect
        c1, c2, c3 = st.columns([3, 0.8, 0.8])

        countries = _iso2_countries_gcs_first(BUCKET)

        # Handle All/Clear button clicks before rendering multiselect
        with c2:
            # Select All button
            if st.button("All", key="select_all_countries", width="stretch"):
                st.session_state["selected_countries_widget"] = countries
                st.rerun()
        with c3:
            # Clear button
            if st.button(
                "Clear", key="deselect_all_countries", width="stretch"
            ):
                st.session_state["selected_countries_widget"] = []
                st.rerun()

        with c1:
            # Initialize default countries if not already in session state
            if "selected_countries_widget" not in st.session_state:
                # Default to all countries that have data in GCS
                default_countries = [
                    c
                    for c in countries
                    if _list_country_versions_cached(BUCKET, c)
                ][
                    :10
                ]  # Limit to first 10 to avoid overwhelming
                if not default_countries and countries:
                    default_countries = [countries[0]]
                st.session_state["selected_countries_widget"] = (
                    default_countries
                )

            selected_countries = st.multiselect(
                "Countries",
                options=countries,
                key="selected_countries_widget",
                help="Select one or more countries to analyze. All selected countries will use the same mapping.",
            )
            # Update session state
            st.session_state["selected_countries"] = selected_countries
            # Keep backward compatibility with single country field
            if selected_countries:
                st.session_state["country"] = selected_countries[0]

        st.caption(f"GCS Bucket: **{BUCKET}**")

        # Check if countries are selected before proceeding
        selected_countries = st.session_state.get("selected_countries", [])
        if not selected_countries:
            st.warning("Please select at least one country above.")
            return

        # Show info about multi-country behavior
        if len(selected_countries) > 1:
            st.info(
                f"📍 Loading data for **{len(selected_countries)} countries**: "
                f"{', '.join([c.upper() for c in selected_countries])}. "
                f"Each country's data will be loaded and saved separately."
            )

        # Use a FORM only for the action buttons
        with st.form("load_data_form", clear_on_submit=False):
            # Buttons row: Load + Refresh GCS list (side-by-side, wide)
            b1, b2 = st.columns([1, 1.2])
            with b1:
                load_clicked = st.form_submit_button("Load", width="stretch")
            with b2:
                refresh_clicked = st.form_submit_button(
                    "↻ Refresh GCS list", width="stretch"
                )

        # --- right after the form block (i.e., after the `with st.form(...):` ends)
        if refresh_clicked:
            _list_country_versions_cached.clear()
            st.success("Refreshed GCS version list.")
            st.rerun()

        if not load_clicked:
            # Show preview of loaded data by country
            df_by_country = st.session_state.get("df_raw_by_country", {})
            if df_by_country:
                st.caption(
                    f"Preview (from session) - {len(df_by_country)} countries loaded:"
                )
                for country, df in df_by_country.items():
                    with st.expander(f"{country.upper()} - {len(df):,} rows"):
                        st.dataframe(
                            df.head(10),
                            width="stretch",
                            hide_index=True,
                        )
            elif not st.session_state.get("df_raw", pd.DataFrame()).empty:
                # Backward compatibility: show single df_raw
                st.caption("Preview (from session):")
                st.dataframe(
                    st.session_state["df_raw"].head(20),
                    width="stretch",
                    hide_index=True,
                )
            return

        # Validate required fields before loading
        choice = st.session_state.get("source_choice", "Latest")

        # Validate country field for Snowflake/BigQuery/CSV when using table mode
        if choice == "Snowflake":
            # Check if using table mode (not custom SQL)
            if not st.session_state.get("sf_sql", "").strip():
                country_field = st.session_state.get(
                    "sf_country_field", ""
                ).strip()
                if not country_field:
                    st.error(
                        "⚠️ **Country field is required for Snowflake table queries**\n\n"
                        "Please fill in the 'Select country field:' input above. "
                        "This should be the column name in your Snowflake table that contains country codes "
                        "(e.g., 'COUNTRY', 'COUNTRY_CODE', 'ISO_CODE')."
                    )
                    return
        elif choice == "BigQuery":
            # Check if using table mode (not custom SQL)
            if not st.session_state.get("bq_sql", "").strip():
                country_field = st.session_state.get(
                    "bq_country_field", ""
                ).strip()
                if not country_field:
                    st.error(
                        "⚠️ **Country field is required for BigQuery table queries**\n\n"
                        "Please fill in the 'Country field name:' input above. "
                        "This should be the column name in your BigQuery table that contains country codes "
                        "(e.g., 'country', 'country_code', 'iso_code')."
                    )
                    return

        try:
            df_by_country = {}
            loaded_count = 0
            failed_countries = []
            load_details = []  # Track what happened for each country

            # Load data for each selected country
            with st.spinner(
                f"Loading data for {len(selected_countries)} countries..."
            ):
                for country in selected_countries:
                    try:
                        df = None
                        load_method = ""

                        if choice == "Snowflake":
                            # Load from Snowflake with country-specific WHERE clause
                            _require_sf_session()
                            base_sql = effective_sql(
                                st.session_state["sf_table"],
                                st.session_state["sf_sql"],
                            )
                            if base_sql:
                                # Add country filter if not using custom SQL
                                if not st.session_state["sf_sql"].strip():
                                    country_field = st.session_state.get(
                                        "sf_country_field", "COUNTRY"
                                    ).strip()
                                    sql = f"{base_sql} WHERE {country_field} = '{country.upper()}'"
                                else:
                                    # Custom SQL - user must include country filter themselves
                                    # Replace placeholder if present
                                    sql = st.session_state["sf_sql"].replace(
                                        "{country}", country.upper()
                                    )

                                # Log the SQL for debugging
                                st.write(
                                    f"Executing SQL for {country.upper()}: {sql}"
                                )

                                df = _load_from_snowflake_cached(sql)

                                # Validate that country field exists in the data (case-insensitive)
                                if (
                                    df is not None
                                    and not df.empty
                                    and not st.session_state["sf_sql"].strip()
                                ):
                                    country_field = st.session_state.get(
                                        "sf_country_field", "COUNTRY"
                                    ).strip()
                                    # Case-insensitive column name matching
                                    df_columns_lower = {
                                        col.lower(): col for col in df.columns
                                    }
                                    if (
                                        country_field.lower()
                                        not in df_columns_lower
                                    ):
                                        raise ValueError(
                                            f"Country field '{country_field}' not found in Snowflake table. "
                                            f"Available columns: {', '.join(df.columns.tolist())}. "
                                            f"Please check the 'Select country field:' input and ensure it matches a column name in your table."
                                        )

                                load_method = f"Snowflake ({len(df) if df is not None else 0} rows)"

                        elif choice == "BigQuery":
                            # Load from BigQuery with country-specific WHERE clause
                            from utils.bigquery_connector import execute_query

                            bq_client = st.session_state.get("bq_client")
                            if not bq_client:
                                raise ValueError(
                                    "BigQuery client not found. Please reconnect in Connect Data page."
                                )

                            bq_table = st.session_state.get("bq_table", "")
                            bq_sql = st.session_state.get("bq_sql", "")

                            if bq_sql.strip():
                                # Custom SQL - replace country placeholder
                                sql = bq_sql.replace(
                                    "{country}", country.upper()
                                )
                            elif bq_table.strip():
                                # Use table with country filter
                                country_field = st.session_state.get(
                                    "bq_country_field", "country"
                                ).strip()
                                sql = f"SELECT * FROM `{bq_table}` WHERE {country_field} = '{country.upper()}'"
                            else:
                                raise ValueError(
                                    "Please provide either a BigQuery table ID or custom SQL query."
                                )

                            df = execute_query(
                                bq_client, sql, fetch_pandas=True
                            )

                            # Validate that country field exists in the data (case-insensitive)
                            if (
                                df is not None
                                and not df.empty
                                and not bq_sql.strip()
                            ):
                                country_field = st.session_state.get(
                                    "bq_country_field", "country"
                                ).strip()
                                # Case-insensitive column name matching
                                df_columns_lower = {
                                    col.lower(): col for col in df.columns
                                }
                                if (
                                    country_field.lower()
                                    not in df_columns_lower
                                ):
                                    raise ValueError(
                                        f"Country field '{country_field}' not found in BigQuery table. "
                                        f"Available columns: {', '.join(df.columns.tolist())}. "
                                        f"Please check the 'Country field name:' input and ensure it matches a column name in your table."
                                    )

                            load_method = f"BigQuery ({len(df) if df is not None else 0} rows)"

                        elif choice == "CSV Upload":
                            # Load from uploaded CSV (same data for all countries)
                            csv_data = st.session_state.get("csv_data")
                            if csv_data is None or csv_data.empty:
                                raise ValueError(
                                    "CSV data not found. Please upload a CSV in Connect Data page."
                                )

                            # For CSV, we use the same data for all countries
                            # Users can filter by country column if they have one
                            df = csv_data.copy()
                            load_method = f"CSV Upload ({len(df)} rows)"

                        else:
                            # Load from GCS
                            country_versions = _list_country_versions_cached(
                                BUCKET, country
                            )

                            if choice == "Latest":
                                # Try latest symlink first
                                try:
                                    blob_path = _latest_symlink_blob(country)
                                    df = _download_parquet_from_gcs_cached(
                                        BUCKET, blob_path
                                    )
                                    load_method = f"GCS latest ({len(df)} rows)"
                                except Exception as gcs_err:
                                    # Fallback to most recent timestamp
                                    if country_versions:
                                        fallback_ts = (
                                            country_versions[0]
                                            if country_versions[0].lower()
                                            != "latest"
                                            else (
                                                country_versions[1]
                                                if len(country_versions) > 1
                                                else None
                                            )
                                        )
                                        if fallback_ts:
                                            blob_path = _data_blob(
                                                country, fallback_ts
                                            )
                                            df = _download_parquet_from_gcs_cached(
                                                BUCKET, blob_path
                                            )
                                            load_method = f"GCS {fallback_ts} ({len(df)} rows)"
                                        else:
                                            load_method = f"No data in GCS"
                                    else:
                                        load_method = f"No versions in GCS"
                            else:
                                # User picked a specific GCS timestamp
                                blob_path = _data_blob(country, choice)
                                df = _download_parquet_from_gcs_cached(
                                    BUCKET, blob_path
                                )
                                load_method = f"GCS {choice} ({len(df)} rows)"

                        if df is not None and not df.empty:
                            df_by_country[country] = df
                            loaded_count += 1
                            load_details.append(
                                f"✅ {country.upper()}: {load_method}"
                            )
                        else:
                            # Country has no data (0 rows or None)
                            if df is not None and df.empty:
                                # Data source returned empty result
                                load_details.append(
                                    f"⚠️ {country.upper()}: Skipped - No data found (0 rows)"
                                )
                            else:
                                # df is None - data source had no data
                                failed_countries.append(country)
                                load_details.append(
                                    f"❌ {country.upper()}: {load_method or 'No data available'}"
                                )

                    except Exception as e:
                        failed_countries.append(country)
                        # Parse and simplify error message for business users
                        error_msg = str(e)
                        user_friendly_error = _simplify_error_message(
                            error_msg, choice, country
                        )
                        load_details.append(
                            f"❌ {country.upper()}: {user_friendly_error}"
                        )

            # Update session state
            st.session_state["df_raw_by_country"] = df_by_country
            # For backward compatibility, set df_raw to the first country's data
            if df_by_country:
                first_country_df = list(df_by_country.values())[0]
                st.session_state.update(
                    {
                        "df_raw": first_country_df,
                        "data_origin": (
                            "gcs_latest"
                            if choice == "Latest"
                            else (
                                "gcs_timestamp"
                                if choice in versions
                                else "snowflake"
                            )
                        ),
                        "picked_ts": (
                            choice
                            if choice in versions
                            else ("latest" if choice == "Latest" else "")
                        ),
                    }
                )

            # Show results
            if loaded_count > 0:
                st.success(f"✅ Loaded data for {loaded_count} countries.")

                # Show load details
                with st.expander("📋 Load details", expanded=False):
                    for detail in load_details:
                        st.write(detail)

                # Show data preview for each country
                for country, df in df_by_country.items():
                    with st.expander(f"{country.upper()} - {len(df):,} rows"):
                        st.dataframe(
                            df.head(10),
                            width="stretch",
                            hide_index=True,
                        )

            if failed_countries:
                st.warning(
                    f"⚠️ Failed to load data for {len(failed_countries)} countries"
                )
                with st.expander("Failed countries details"):
                    for detail in load_details:
                        if "❌" in detail:
                            st.write(detail)

            if loaded_count > 0:
                st.rerun()
            else:
                st.error("No data was loaded for any country.")

        except Exception as e:
            st.error(f"Load failed: {e}")

    step1_loader()

    # Save snapshot (button callback uses session data; no manual st.rerun())
    def _save_current_raw():
        # Get data by country
        df_by_country = st.session_state.get("df_raw_by_country", {})

        # Fallback to single df_raw for backward compatibility
        if not df_by_country:
            df = st.session_state.get("df_raw", pd.DataFrame())
            if df.empty:
                st.warning("No dataset loaded.")
                return
            # Use single country
            country = st.session_state.get("country", "de")
            df_by_country = {country: df}

        if not df_by_country:
            st.warning("No dataset loaded.")
            return

        try:
            # Generate shared timestamp for all countries
            shared_ts = format_cet_timestamp()
            saved_paths = []

            # Save each country's data to its own path
            for country, df in df_by_country.items():
                if df is not None and not df.empty:
                    res = _save_raw_to_gcs(
                        df, BUCKET, country, timestamp=shared_ts
                    )
                    saved_paths.append(res["data_gcs_path"])

            st.session_state["picked_ts"] = shared_ts
            st.session_state["shared_save_timestamp"] = shared_ts
            st.session_state["data_origin"] = "gcs_latest"
            st.session_state["last_saved_raw_path"] = (
                saved_paths[0] if saved_paths else ""
            )
            _list_country_versions_cached.clear()  # ⬅️ invalidate local cache
            list_data_versions.clear()  # ⬅️ invalidate app_shared cache

            if len(saved_paths) == 1:
                st.success(f"Saved raw snapshot → {saved_paths[0]}")
            else:
                st.success(
                    f"✅ Saved raw snapshots for {len(saved_paths)} countries"
                )
                with st.expander("Saved paths"):
                    for path in saved_paths:
                        st.write(f"- {path}")
        except Exception as e:
            st.error(f"Saving to GCS failed: {e}")

    csave1, csave2 = st.columns([1, 3])
    csave1.button(
        "💾 Save dataset to GCS",
        on_click=_save_current_raw,
        width="stretch",
    )
    if st.session_state["last_saved_raw_path"]:
        csave2.caption(
            f"Last saved: `{st.session_state['last_saved_raw_path']}`"
        )

    st.divider()

# ──────────────────────────────────────────────────────────────
# Step 2) Map your data
# ──────────────────────────────────────────────────────────────
st.header("2. Map Variables")

df_raw = st.session_state.get("df_raw", pd.DataFrame())

with st.expander(
    "🗺️ Tell the tool what each data point represents.", expanded=False
):
    # Show current data state (point 4 - UI representing actual state)
    data_origin = st.session_state.get("data_origin", "N/A")
    picked_ts = st.session_state.get("picked_ts", "N/A")
    country = st.session_state.get("country", "N/A")
    loaded_metadata_source = st.session_state.get("loaded_metadata_source", "")

    if df_raw is not None and not df_raw.empty:
        st.info(
            f"🔵 **Data:** {data_origin.upper()} | Country: {country.upper()} | Timestamp: {picked_ts} | Rows: {len(df_raw):,} | Columns: {len(df_raw.columns)}"
        )
        # Show metadata status
        if loaded_metadata_source:
            st.info(f"📋 **Metadata:** Loaded from {loaded_metadata_source}")
        else:
            st.caption("📋 **Metadata:** Using default auto-tagging rules")
    else:
        st.warning(
            "⚪ No data loaded yet - load data in Step 1 to configure mapping"
        )

    all_cols = df_raw.columns.astype(str).tolist() if not df_raw.empty else []

    # ---- Load saved metadata (moved to beginning of Step 2) ----
    with st.expander("📥 Start from previous mapping", expanded=False):
        # Get available metadata versions (including universal) - same logic as Experiment page
        try:
            # Get country-specific metadata versions
            country_meta_versions = _list_metadata_versions_cached(
                BUCKET, st.session_state["country"]
            )
            # Get universal metadata versions
            universal_meta_versions = _list_metadata_versions_cached(
                BUCKET, "universal"
            )

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
                        f"{st.session_state['country'].upper()} - {v}"
                        for v in country_meta_versions
                    ]
                )

            if not metadata_options:
                metadata_options = ["Universal - Latest"]
        except Exception as e:
            st.warning(f"Could not list metadata versions: {e}")
            metadata_options = ["Universal - Latest"]

        # Metadata source selection
        selected_metadata = st.selectbox(
            "Select mapping:",
            options=metadata_options,
            index=0,
            help="'Universal' mappings work for all countries. Latest = most recently saved metadata.",
            key="load_metadata_source_selector",
        )

        # Add buttons for Load, Clear, and Refresh
        col_load, col_clear, col_refresh = st.columns([1, 1, 1])
        with col_load:
            load_metadata_clicked = st.button(
                "Apply mapping",
                width="stretch",
                key="load_metadata_btn",
            )
        with col_clear:
            clear_metadata_clicked = st.button(
                "🗑️ Clear metadata",
                width="stretch",
                key="clear_metadata_btn",
                help="Clear all loaded metadata and reset mapping to defaults",
            )
        with col_refresh:
            refresh_metadata_clicked = st.button(
                "↻ Refresh metadata list",
                width="stretch",
                key="refresh_metadata_btn",
            )

        # Handle refresh button
        if refresh_metadata_clicked:
            _list_metadata_versions_cached.clear()
            st.success("Refreshed metadata list.")
            st.rerun()

        # Handle clear metadata button
        if clear_metadata_clicked:
            # Reset all metadata-related session state to defaults
            st.session_state["goals_df"] = pd.DataFrame(
                columns=["var", "group", "type", "main"]
            ).astype("object")
            st.session_state["mapping_df"] = pd.DataFrame(
                columns=[
                    "var",
                    "category",
                    "channel",
                    "data_type",
                    "agg_strategy",
                    "custom_tags",
                ]
            ).astype("object")
            st.session_state["auto_rules"] = {
                "paid_media_spends": [
                    "_cost",
                    "_spend",
                    "_costs",
                    "_spends",
                    "_budget",
                    "_amount",
                ],
                "paid_media_vars": ["_impressions", "_clicks", "_sessions"],
                "context_vars": ["_index", "_temp", "_price", "_holiday"],
                "organic_vars": ["_organic", "_direct"],
                "factor_vars": ["_flag", "_is", "_on"],
            }
            st.session_state["custom_channels"] = []
            st.session_state["organic_vars_prefix"] = "organic_"
            st.session_state["context_vars_prefix"] = "context_"
            st.session_state["factor_vars_prefix"] = "factor_"
            st.session_state["last_saved_meta_path"] = ""
            st.session_state["aggregation_sources"] = {}
            # Clear the loaded metadata source indicator
            st.session_state["loaded_metadata_source"] = ""

            # Rebuild mapping_df from current data with default rules
            if not df_raw.empty:
                st.session_state["mapping_df"] = _build_mapping_df(
                    all_cols, df_raw, st.session_state["auto_rules"]
                )

            st.success("✅ Metadata cleared. All mappings reset to defaults.")
            st.rerun()

        if load_metadata_clicked:
            try:
                # Parse metadata selection to get country and version
                meta_parts = selected_metadata.split(" - ")
                meta_country = (
                    "universal"
                    if meta_parts[0] == "Universal"
                    else st.session_state["country"]
                )
                meta_version = (
                    meta_parts[1] if len(meta_parts) > 1 else "Latest"
                )

                # Construct blob path
                if meta_version == "Latest" or meta_version.lower() == "latest":
                    meta_blob = _meta_latest_blob(meta_country)
                else:
                    meta_blob = _meta_blob(meta_country, str(meta_version))

                meta = _download_json_from_gcs(BUCKET, meta_blob)
                _apply_metadata_to_current_df(meta, all_cols, df_raw)

                # Track the loaded metadata source for UI feedback
                st.session_state["loaded_metadata_source"] = selected_metadata

                # Show what was loaded
                st.success(f"✅ Loaded metadata from: **{selected_metadata}**")
                st.info(f"📁 Source: gs://{BUCKET}/{meta_blob}")

                # Display summary of loaded data
                with st.expander("📊 Loaded Metadata Summary", expanded=False):
                    if "goals" in meta:
                        st.write(f"**Goals:** {len(meta['goals'])} goal(s)")
                        for g in meta["goals"]:
                            st.write(
                                f"  - {g.get('var', 'N/A')} ({g.get('type', 'N/A')}, {g.get('group', 'N/A')})"
                            )

                    if "mapping" in meta:
                        st.write(f"**Variable Categories:**")
                        for cat, vars_list in meta["mapping"].items():
                            if vars_list:
                                st.write(
                                    f"  - {cat}: {len(vars_list)} variable(s)"
                                )

                    if "data" in meta:
                        data_info = meta["data"]
                        st.write(f"**Data Info:**")
                        st.write(
                            f"  - Origin: {data_info.get('origin', 'N/A')}"
                        )
                        st.write(
                            f"  - Date field: {data_info.get('date_field', 'N/A')}"
                        )
                        st.write(
                            f"  - Row count: {data_info.get('row_count', 'N/A')}"
                        )

            except FileNotFoundError:
                st.warning(
                    "⚠️ No saved metadata found on GCS. "
                    "Use the sections below to create new mappings and save them to get started."
                )
            except Exception as e:
                st.error(f"Failed to load metadata: {e}")

    # ---- Goals (form) ----
    with st.expander("🎯 Define Business Goals", expanded=False):
        # Date field selection (moved into Goals expander)
        date_candidates = sorted(
            {
                c
                for c in all_cols
                if c.lower() in ("date", "ds")
                or "date" in c.lower()
                or c.lower().endswith("_dt")
            }
        )
        date_field_options = date_candidates or all_cols or ["date"]
        date_field = st.selectbox(
            "Select Date field",
            options=date_field_options,
            index=0 if date_field_options else None,
        )

        st.divider()

        # Initialize goals nonce for forcing table refresh
        st.session_state.setdefault("goals_nonce", 0)

        # Primary and secondary goal selection
        primary_goals = st.multiselect(
            "Define primary business goal (e.g. GMV, Bookings)",
            options=all_cols,
            default=[],
            key="primary_goals_select",
        )
        secondary_goals = st.multiselect(
            "Define secondary business goals (e.g. Signups, App Installs)",
            options=all_cols,
            default=[],
            help="Secondary goals support full driver analysis. Select only a few to maintain oversight.",
            key="secondary_goals_select",
        )

        def _mk(selected, group):
            return pd.DataFrame(
                {
                    "var": pd.Series(selected, dtype="object"),
                    "group": pd.Series([group] * len(selected), dtype="object"),
                    "type": pd.Series(
                        [_guess_goal_type(v) for v in selected],
                        dtype="object",
                    ),
                    "main": pd.Series([False] * len(selected), dtype="object"),
                }
            )

        # "Add Business Goals" button - adds goals to table without applying
        add_goals_col1, add_goals_col2 = st.columns([1, 3])
        with add_goals_col1:
            if st.button(
                "➕ Add Business Goals",
                key="add_goals_btn",
                width="stretch",
                help="Add selected goals to the table below without applying mappings yet",
            ):
                new_primary = _mk(primary_goals, "primary")
                new_secondary = _mk(secondary_goals, "secondary")
                new_goals = pd.concat(
                    [new_primary, new_secondary], ignore_index=True
                )

                if not new_goals.empty:
                    # Merge with existing goals, avoiding duplicates
                    existing = st.session_state.get("goals_df", pd.DataFrame())
                    if existing.empty:
                        merged = new_goals
                    else:
                        combined = pd.concat(
                            [existing, new_goals], ignore_index=True
                        )
                        merged = combined.drop_duplicates(
                            subset=["var"], keep="last"
                        )

                    st.session_state["goals_df"] = merged.fillna("").astype(
                        {"var": "object", "group": "object", "type": "object"}
                    )
                    if "main" not in st.session_state["goals_df"].columns:
                        st.session_state["goals_df"]["main"] = False
                    st.session_state["goals_df"]["main"] = st.session_state[
                        "goals_df"
                    ]["main"].astype(bool)

                    # Increment nonce to force table refresh
                    st.session_state["goals_nonce"] = (
                        st.session_state.get("goals_nonce", 0) + 1
                    )
                    st.success(f"Added {len(new_goals)} goal(s) to the table.")
                    st.rerun()
                else:
                    st.warning("Please select at least one goal to add.")

        st.divider()

        # Build goals source
        if st.session_state["goals_df"].empty:
            # Return empty goals on first load - user must select manually
            heur = _initial_goals_from_columns()
            goals_src = heur
        else:
            # Keep only what's in session - don't add heuristics if user has edited
            goals_src = st.session_state["goals_df"]

        goals_src = goals_src.fillna("").astype(
            {"var": "object", "group": "object", "type": "object"}
        )
        # Add main column if it doesn't exist
        if "main" not in goals_src.columns:
            goals_src["main"] = False
        goals_src["main"] = goals_src["main"].astype(bool)

        with st.form("goals_form", clear_on_submit=False):
            goals_edit = st.data_editor(
                goals_src,
                width="stretch",
                num_rows="dynamic",
                column_config={
                    "var": st.column_config.SelectboxColumn(
                        "Goal", options=all_cols
                    ),
                    "group": st.column_config.SelectboxColumn(
                        "Goal Priority", options=["primary", "secondary"]
                    ),
                    "type": st.column_config.SelectboxColumn(
                        "Goal Type",
                        options=["revenue", "conversion"],
                        required=True,
                    ),
                    "main": st.column_config.CheckboxColumn(
                        "Select main goal",
                        help="Select the main business goal for the model",
                        default=False,
                    ),
                },
                key=f"goals_editor_{st.session_state.get('goals_nonce', 0)}",
            )
            goals_submit = st.form_submit_button("✅ Apply goal changes")

        if goals_submit:
            # Simply use what the user has in the editor - don't merge with old data
            edited = goals_edit.copy()

            # Keep only non-empty vars
            edited = edited[edited["var"].astype(str).str.strip() != ""].astype(
                "object"
            )

            # Validate that all goals have a type
            empty_types = edited[edited["type"].astype(str).str.strip() == ""]
            if not empty_types.empty:
                st.error(
                    f"⚠️ Please specify a type (revenue or conversion) for all goal variables. Missing types for: {', '.join(empty_types['var'].tolist())}"
                )
            else:
                # Ensure only one main is selected
                main_count = edited["main"].astype(bool).sum()
                if main_count > 1:
                    st.warning(
                        "⚠️ Multiple goals marked as 'Main'. Only the first one will be used as the main dependent variable."
                    )

                # Drop duplicates and normalize
                merged = (
                    edited.drop_duplicates(subset=["var"], keep="last")
                    .fillna({"var": "", "group": "", "type": "", "main": False})
                    .astype(
                        {"var": "object", "group": "object", "type": "object"}
                    )
                )
                merged["main"] = merged["main"].astype(bool)

                st.session_state["goals_df"] = merged
                # Increment nonce to force table refresh
                st.session_state["goals_nonce"] = (
                    st.session_state.get("goals_nonce", 0) + 1
                )
                st.success("Goals updated.")
                st.rerun()

    # ---- Custom channels UI ----
    with st.expander("📺 Define marketing channels", expanded=False):
        # Common marketing channel prefixes for prefill
        COMMON_MARKETING_CHANNELS = [
            "meta",
            "facebook",
            "google",
            "ga",
            "bing",
            "twitter",
            "tiktok",
            "linkedin",
            "snapchat",
            "pinterest",
            "youtube",
            "tv",
            "radio",
            "display",
            "programmatic",
            "affiliate",
            "email",
            "sms",
            "push",
            "influencer",
            "partnership",
            "organic",
        ]

        # Show recognized channels from mapping and inferred from column names
        mapping_channels = []
        if (
            "mapping_df" in st.session_state
            and not st.session_state["mapping_df"].empty
        ):
            mapping_channels = sorted(
                {
                    str(ch).strip().lower()
                    for ch in st.session_state["mapping_df"]["channel"]
                    .dropna()
                    .astype(str)
                    if str(ch).strip()
                }
            )

        # infer channels directly from column names (uses same extractor as mapping)
        inferred_channels = sorted(
            {
                _extract_channel_from_column(c)
                for c in all_cols
                if _extract_channel_from_column(c)
            }
        )

        # combine and dedupe to get recognized channels
        recognized_channels = sorted(set(mapping_channels + inferred_channels))

        # Combine recognized channels with existing custom channels for prefill
        existing_custom = st.session_state.get("custom_channels", [])

        # If no channels exist yet, prefill with common marketing channels
        all_existing_channels = (
            COMMON_MARKETING_CHANNELS
            if not (recognized_channels or existing_custom)
            else sorted(set(recognized_channels + existing_custom))
        )

        # Single input field with prefilled recognized channels
        channels_input = st.text_area(
            "List your channel names (comma-separated)",
            value=", ".join(all_existing_channels),
            help="Used to auto-detect your media channels from column names (e.g., 'facebook_', 'tv_'). Common channels are prefilled for your convenience.",
            height=100,
            key="channels_input",
        )

        if st.button(
            "➕ Apply Channel Detection",
            key="add_channels_btn",
            width="stretch",
        ):
            # Parse the input
            entered_channels = [
                ch.strip().lower()
                for ch in channels_input.split(",")
                if ch.strip()
            ]

            # All entered channels become the new custom channels list
            # This replaces the old behavior of only adding "new" channels
            st.session_state["custom_channels"] = entered_channels

            # Rebuild mapping_df to include updated channels
            if not st.session_state["mapping_df"].empty:
                # Re-extract channels for all variables using the updated channel list
                # Always update the channel field, set to None if extraction returns empty
                for idx, row in st.session_state["mapping_df"].iterrows():
                    var_name = str(row["var"])
                    extracted_channel = _extract_channel_from_column(var_name)
                    # Update channel field: use extracted value or None if empty
                    st.session_state["mapping_df"].at[idx, "channel"] = (
                        extracted_channel if extracted_channel else None
                    )

            st.success(
                f"✅ Channels updated! Now using {len(entered_channels)} channel(s): {', '.join(entered_channels)}"
            )
            st.rerun()

        st.divider()

    # ---- Auto-tag rules ----

    with st.expander("🏷️ Automatically tag your variables", expanded=False):
        st.write("**Use suffixes to tag columns.**")
        rcol1, rcol2, rcol3 = st.columns(3)

        def _parse_sfx(s: str) -> list[str]:
            return [x.strip() for x in s.split(",") if x.strip()]

        new_rules = {
            "paid_media_spends": _parse_sfx(
                rcol1.text_input(
                    "Paid media spends:",
                    value=", ".join(
                        st.session_state["auto_rules"]["paid_media_spends"]
                    ),
                    help="Suffixes that identify spend columns, e.g. '_spend', '_cost'",
                )
            ),
            "paid_media_vars": _parse_sfx(
                rcol1.text_input(
                    "Paid media variables",
                    value=", ".join(
                        st.session_state["auto_rules"]["paid_media_vars"]
                    ),
                    key="paid_vars",
                    help="Suffixes for media activity metrics, e.g. '_clicks', '_impressions', '_views'",
                )
            ),
            "context_vars": _parse_sfx(
                rcol2.text_input(
                    "Context Variables",
                    value=", ".join(
                        st.session_state["auto_rules"]["context_vars"]
                    ),
                    help="Suffixes for non-media drivers, e.g. '_promo', '_weather'",
                )
            ),
            "organic_vars": _parse_sfx(
                rcol2.text_input(
                    "Organic Variables",
                    value=", ".join(
                        st.session_state["auto_rules"]["organic_vars"]
                    ),
                    key="org_vars",
                    help="Suffixes for organic traffic channels, e.g. '_organic', '_direct'. Similar to Paid Spends, they also receive response-curves.",
                )
            ),
            "factor_vars": _parse_sfx(
                rcol3.text_input(
                    "Factor Variables (True/False)",
                    value=", ".join(
                        st.session_state["auto_rules"]["factor_vars"]
                    ),
                    help="Suffixes for binary flags, e.g. 'is_big_promotion','is_holiday'",
                )
            ),
        }
        rules_changed = json.dumps(new_rules, sort_keys=True) != json.dumps(
            st.session_state["auto_rules"], sort_keys=True
        )
        if rules_changed:
            st.session_state["auto_rules"] = new_rules
            # seed mapping again only when rules change AND user hasn't started manual edits
            if st.session_state["mapping_df"].empty:
                st.session_state["mapping_df"] = _build_mapping_df(
                    all_cols, df_raw, new_rules
                )

        # Prefix configuration for aggregated variables
        st.divider()
        st.write("**Use prefixes to automate tagging instead:**")
        st.caption(
            "Define prefixes to use when creating aggregated columns for these categories"
        )

        pcol1, pcol2, pcol3 = st.columns(3)

        organic_prefix = pcol1.text_input(
            "Organic Variables",
            value=st.session_state.get("organic_vars_prefix", "organic_"),
            help="Prefix for aggregated organic variables (e.g., 'organic_')",
            key="organic_vars_prefix_input",
        )

        context_prefix = pcol2.text_input(
            "Context Variables",
            value=st.session_state.get("context_vars_prefix", "context_"),
            help="Prefix for aggregated context variables (e.g., 'context_')",
            key="context_vars_prefix_input",
        )

        factor_prefix = pcol3.text_input(
            "Factor Variables (True/False)",
            value=st.session_state.get("factor_vars_prefix", "factor_"),
            help="Prefix for aggregated factor variables (e.g., 'factor_')",
            key="factor_vars_prefix_input",
        )

        # Update session state with new prefixes
        st.session_state["organic_vars_prefix"] = organic_prefix
        st.session_state["context_vars_prefix"] = context_prefix
        st.session_state["factor_vars_prefix"] = factor_prefix

    # ---- Mapping editor (form) ----

    # ✅ if still empty (first load), seed using current rules (outside the form)
    if st.session_state["mapping_df"].empty:
        st.session_state["mapping_df"] = _build_mapping_df(
            all_cols, df_raw, st.session_state["auto_rules"]
        )

    # ---- Variable Mapping Editor ----
    with st.expander("🗺️ Review and Finalize Mapping", expanded=False):
        st.write(
            "Review auto-tagged variables, adjust where needed and confirm before saving."
        )
        st.caption(
            "Hint: Hover over the **header names** in the mapping table to see extra info."
        )

        # Add sorting controls (user-controlled, not automatic)
        sort_col1, sort_col2, sort_col3 = st.columns([2, 1, 1])
        with sort_col1:
            sort_by = st.selectbox(
                "Sort by",
                options=[
                    "Original order",
                    "var",  # FE: column name rename
                    "category",
                    "channel",
                    "channel_subchannel",  # FE: column gibts nicht? wäre gut zu displayen?
                    "data_type",
                ],
                index=0,
                help="Choose a column to sort the mapping table. 'channel_subchannel' sorts by CHANNEL_SUBCHANNEL pattern.",
                key="sort_by_selector",
            )
        with sort_col2:
            sort_order = st.selectbox(
                "Order",
                options=["Ascending", "Descending"],
                index=0,
                key="sort_order_selector",
            )
        with sort_col3:
            if st.button(
                "🔄 Apply Sort", key="apply_sort_btn", width="stretch"
            ):
                if sort_by != "Original order":
                    ascending = sort_order == "Ascending"
                    m = st.session_state["mapping_df"].copy()

                    # Handle channel_subchannel sorting
                    if sort_by == "channel_subchannel":
                        # Create a temporary column for sorting by channel_subchannel
                        def extract_channel_subchannel(var_name):
                            parsed = _parse_variable_name(str(var_name))
                            channel = parsed.get("channel", "")
                            subchannel = parsed.get("subchannel", "")
                            if channel and subchannel:
                                return f"{channel}_{subchannel}"
                            elif channel:
                                return channel
                            else:
                                return ""

                        m["_sort_key"] = m["var"].apply(
                            extract_channel_subchannel
                        )
                        st.session_state["mapping_df"] = (
                            m.sort_values(by="_sort_key", ascending=ascending)
                            .drop(columns=["_sort_key"])
                            .reset_index(drop=True)
                        )
                    else:
                        st.session_state["mapping_df"] = m.sort_values(
                            by=sort_by, ascending=ascending
                        ).reset_index(drop=True)

                    st.success(f"Sorted by {sort_by} ({sort_order})")
                    st.rerun()

        with st.form("mapping_form_main", clear_on_submit=False):
            # Filter out goal variables and date field from the mapping display
            goals_df = st.session_state["goals_df"]
            date_and_goal_vars_display = set(
                [date_field] + goals_df["var"].tolist()
            )
            mapping_src = (
                st.session_state["mapping_df"][
                    ~st.session_state["mapping_df"]["var"].isin(
                        date_and_goal_vars_display
                    )
                ]
                .copy()
                .fillna("")
            )

            # Ensure all expected columns exist
            expected_cols = [
                "var",
                "category",
                "channel",
                "data_type",
                "agg_strategy",
                "custom_tags",
            ]
            for col in expected_cols:
                if col not in mapping_src.columns:
                    mapping_src[col] = ""

            mapping_src = mapping_src.astype("object")

            # Get all available channels for the dropdown
            all_available_channels = sorted(
                set(
                    _get_known_channels()
                    + st.session_state.get("custom_channels", [])
                )
            )

            mapping_edit = st.data_editor(
                mapping_src,
                width="stretch",
                num_rows="dynamic",
                column_config={
                    "var": st.column_config.TextColumn("Column", disabled=True),
                    "category": st.column_config.SelectboxColumn(
                        "Category",
                        options=ALLOWED_CATEGORIES,
                        help="Marketing Mix Model variable category",
                    ),
                    "channel": st.column_config.SelectboxColumn(
                        "Channel",
                        options=[""] + all_available_channels,
                        help="Marketing channel for this column",
                    ),
                    "data_type": st.column_config.SelectboxColumn(
                        "Data Type",
                        options=["numeric", "categorical"],
                        help="Whether the column contains numeric or categorical data",
                    ),
                    "agg_strategy": st.column_config.SelectboxColumn(
                        "Aggregation",
                        options=["sum", "mean", "max", "min", "mode"],
                        help="How this column is rolled up when changing granularity (e.g., day → week → month). Numeric fields: sum/mean/max/min. Categorical: mode.",
                    ),
                    "custom_tags": st.column_config.TextColumn(
                        "Custom Channels",
                        help="Tag sub-channels to group them into a custom channel (e.g., tag multiple GA sub-channels as 'small' → creates GA_CUSTOM_SMALL for MMM).",
                    ),
                },
                key="mapping_editor",
            )

            # Submit button inside the form to capture edits
            mapping_submit = st.form_submit_button("✅ Apply new mapping")

        # Handle form submission - capture edits and apply aggregations
        if mapping_submit:
            # Apply automatic aggregations to edited mapping
            try:
                # Store original length before updating
                original_length = len(st.session_state["mapping_df"])

                # Prepare prefixes dict
                prefixes = {
                    "organic_vars": st.session_state.get(
                        "organic_vars_prefix", "ORGANIC_"
                    ),
                    "context_vars": st.session_state.get(
                        "context_vars_prefix", "CONTEXT_"
                    ),
                    "factor_vars": st.session_state.get(
                        "factor_vars_prefix", "FACTOR_"
                    ),
                }
                # Use mapping_edit which has the user's edits from the data_editor
                # Apply aggregations to the first country's data to get updated mapping
                (
                    updated_mapping,
                    updated_df,
                    aggregation_sources,
                ) = _apply_automatic_aggregations(
                    mapping_edit.copy(),
                    st.session_state["df_raw"].copy(),
                    prefixes,
                )

                # Now apply the same aggregations to ALL countries in df_raw_by_country
                df_by_country = st.session_state.get("df_raw_by_country", {})
                if df_by_country:
                    updated_df_by_country = {}
                    for country, country_df in df_by_country.items():
                        try:
                            # Apply aggregations to each country's data
                            _, country_updated_df, _ = (
                                _apply_automatic_aggregations(
                                    mapping_edit.copy(),
                                    country_df.copy(),
                                    prefixes,
                                )
                            )
                            updated_df_by_country[country] = country_updated_df
                        except Exception as country_err:
                            st.warning(
                                f"Failed to apply aggregations to {country.upper()}: {country_err}"
                            )
                            updated_df_by_country[country] = (
                                country_df  # Keep original
                            )
                    st.session_state["df_raw_by_country"] = (
                        updated_df_by_country
                    )
                    # Also update df_raw to be consistent with the first country
                    first_country = list(updated_df_by_country.keys())[0]
                    st.session_state["df_raw"] = updated_df_by_country[
                        first_country
                    ]
                else:
                    # Single country mode - just update df_raw
                    st.session_state["df_raw"] = updated_df

                st.session_state["mapping_df"] = updated_mapping
                st.session_state["aggregation_sources"] = aggregation_sources
                num_new = len(updated_mapping) - original_length
                countries_count = len(df_by_country) if df_by_country else 1
                st.success(
                    f"✅ Mapping updated for {countries_count} countries! Total: {len(updated_mapping)} variables."
                )
                if num_new > 0:
                    st.info(
                        "💾 **Important:** Click 'Save this dataset to GCS' above to persist the new aggregated columns. "
                        "Then save the metadata below so the columns are available in the Experiment page."
                    )
                # Trigger a rerun to refresh the UI with new columns
                st.rerun()
            except Exception as e:
                st.error(f"Failed to apply automatic aggregations: {e}")
                # Still save the edits even if aggregation fails
                st.session_state["mapping_df"] = mapping_edit
                st.warning("Saved edits without automatic aggregations.")

        st.divider()


# End of Step 2 expander
# ──────────────────────────────────────────────────────────────
# Step 3) Save your mapping
# ──────────────────────────────────────────────────────────────
st.header("3. Save & Reuse")

with st.expander("💾 Store mapping for future use.", expanded=False):
    goals_df = st.session_state["goals_df"]
    mapping_df = st.session_state["mapping_df"]
    auto_rules = st.session_state["auto_rules"]

    def _by_cat(df: pd.DataFrame, cat: str) -> list[str]:
        """Get variables for a category, excluding those with empty category AND channel."""
        filtered_df = df[df["category"] == cat].copy()
        # Exclude rows where both category and channel are empty
        result = []
        for _, r in filtered_df.iterrows():
            cat_val = str(r.get("category", "")).strip()
            ch_val = str(r.get("channel", "")).strip()
            # Include if either category or channel is non-empty
            if cat_val or ch_val:
                result.append(str(r["var"]))
        return result

    # Use allowed categories from constant, excluding empty string for by_cat
    # Also filter out date field and goal variables from the mapping
    date_and_goal_vars = set([date_field] + goals_df["var"].tolist())
    mapping_df_filtered = mapping_df[
        ~mapping_df["var"].isin(date_and_goal_vars)
    ].copy()
    by_cat = {
        cat: _by_cat(mapping_df_filtered, cat)
        for cat in ALLOWED_CATEGORIES
        if cat
    }

    # Auto-add factor_vars to context_vars (requirement 6)
    if by_cat.get("factor_vars"):
        context_vars_set = set(by_cat.get("context_vars", []))
        context_vars_set.update(by_cat["factor_vars"])
        by_cat["context_vars"] = sorted(list(context_vars_set))

    # Get main dependent variable from goals_df
    dep_var = ""
    if not goals_df.empty:
        main_goals = goals_df[goals_df.get("main", False).astype(bool)]
        if not main_goals.empty:
            dep_var = str(main_goals.iloc[0]["var"])
        elif not goals_df.empty:
            # Fallback to first primary goal if no main is selected
            primary_goals = goals_df[goals_df["group"] == "primary"]
            if not primary_goals.empty:
                dep_var = str(primary_goals.iloc[0]["var"])

    # Show selected countries for saving
    selected_countries = st.session_state.get("selected_countries", [])
    if selected_countries:
        countries_display = ", ".join([c.upper() for c in selected_countries])
        st.info(f"📍 Selected countries: **{countries_display}**")

    # Determine checkbox label based on number of selected countries
    current_country = st.session_state.get("country", "de")
    if len(selected_countries) > 1:
        checkbox_label = (
            f"Save metadata only for {current_country.upper()} "
            f"(first of {len(selected_countries)} selected)"
        )
    else:
        checkbox_label = f"Save metadata only for {current_country.upper()}"

    # Checkbox for universal vs country-specific mapping
    save_country_specific = st.checkbox(
        checkbox_label,
        value=False,
        help="By default, mappings are saved universally for all countries. Check this box to save metadata only for the primary country.",
    )

    meta_ts = format_cet_timestamp()

    # Build goals JSON with aggregation info based on type
    goals_json = []
    for _, r in goals_df.iterrows():
        if str(r.get("var", "")).strip():
            goal_type = str(r.get("type", ""))
            # Define aggregation based on type
            if goal_type == "revenue":
                agg = "sum"
            elif goal_type == "conversion":
                agg = "mean"
            else:
                agg = "sum"  # default

            goals_json.append(
                {
                    "var": str(r["var"]),
                    "group": str(r["group"]),
                    "type": goal_type,
                    "agg_strategy": agg,
                    "main": bool(r.get("main", False)),
                }
            )

    # Extract channel, data_type, and agg_strategy mappings (excluding date and goals)
    channels_map = {
        str(r["var"]): str(r.get("channel", ""))
        for _, r in mapping_df_filtered.iterrows()
        if str(r.get("channel", "")).strip()
    }
    data_types_map = {
        str(r["var"]): str(r.get("data_type", "numeric"))
        for _, r in mapping_df_filtered.iterrows()
    }
    # Set date field data type to "date"
    data_types_map[date_field] = "date"

    agg_strategies_map = {
        str(r["var"]): str(r.get("agg_strategy", "sum"))
        for _, r in mapping_df_filtered.iterrows()
    }

    # Build paid_media_spends to paid_media_vars mapping
    paid_media_mapping = {}
    paid_spends = mapping_df[
        mapping_df["category"] == "paid_media_spends"
    ].copy()
    paid_vars = mapping_df[mapping_df["category"] == "paid_media_vars"].copy()

    for _, spend_row in paid_spends.iterrows():
        spend_var = str(spend_row["var"])
        parsed = _parse_variable_name(spend_var)
        channel = parsed["channel"]
        subchannel = parsed["subchannel"]
        suffix = parsed["suffix"]

        # Check if this is a _CUSTOM column
        if "_CUSTOM" in spend_var:
            # For _CUSTOM columns, we need to match the exact pattern
            # E.g., GA_SMALL_COST_CUSTOM should match GA_SMALL_*_CUSTOM
            # Extract the pattern before the suffix
            # Pattern: CHANNEL_TAG_SUFFIX_CUSTOM or CHANNEL_TOTAL_SUFFIX_CUSTOM
            if "_TOTAL_" in spend_var:
                # CHANNEL_TOTAL_SUFFIX_CUSTOM → CHANNEL_TOTAL_*_CUSTOM
                base_pattern = spend_var.replace("_COST_CUSTOM", "")  # GA_TOTAL
                matching_vars = [
                    str(v)
                    for v in paid_vars["var"]
                    if str(v).startswith(base_pattern + "_")
                    and "_CUSTOM" in str(v)
                ]
            else:
                # CHANNEL_TAG_SUFFIX_CUSTOM → CHANNEL_TAG_*_CUSTOM
                # E.g., GA_SMALL_COST_CUSTOM → GA_SMALL_*_CUSTOM
                base_pattern = spend_var.replace("_COST_CUSTOM", "")  # GA_SMALL
                matching_vars = [
                    str(v)
                    for v in paid_vars["var"]
                    if str(v).startswith(base_pattern + "_")
                    and "_CUSTOM" in str(v)
                ]
        else:
            # Regular columns (non-_CUSTOM)
            # Find corresponding paid_media_vars with same channel and subchannel
            if subchannel:
                pattern = f"{channel}_{subchannel}_"
            else:
                pattern = f"{channel}_"

            matching_vars = [
                str(v)
                for v in paid_vars["var"]
                if str(v).startswith(pattern) and "_CUSTOM" not in str(v)
            ]

        if matching_vars:
            paid_media_mapping[spend_var] = matching_vars

    payload = {
        "project_id": PROJECT_ID,
        "bucket": BUCKET,
        "country": (
            st.session_state["country"]
            if save_country_specific
            else "universal"
        ),
        "saved_at": get_cet_now().isoformat(),
        "data": {
            "origin": st.session_state["data_origin"],
            "timestamp": st.session_state["picked_ts"] or "latest",
            "date_field": date_field,
            "row_count": int(len(df_raw)),
        },
        "goals": goals_json,
        "dep_variable_type": {g["var"]: g["type"] for g in goals_json},
        "autotag_rules": auto_rules,
        "custom_channels": st.session_state.get("custom_channels", []),
        "mapping": by_cat,
        "channels": channels_map,
        "data_types": data_types_map,
        "agg_strategies": agg_strategies_map,
        "paid_media_mapping": paid_media_mapping,
        "aggregation_sources": st.session_state.get("aggregation_sources", {}),
        "dep_var": dep_var or "",
        "variable_prefixes": {
            "organic_vars": st.session_state.get(
                "organic_vars_prefix", "organic_"
            ),
            "context_vars": st.session_state.get(
                "context_vars_prefix", "context_"
            ),
            "factor_vars": st.session_state.get(
                "factor_vars_prefix", "factor_"
            ),
        },
    }

    def _save_metadata():
        try:
            # Generate a shared timestamp for all saves
            shared_ts = format_cet_timestamp()
            st.session_state["shared_save_timestamp"] = shared_ts

            # Get data by country
            df_by_country = st.session_state.get("df_raw_by_country", {})

            # Fallback to single df_raw for backward compatibility
            if not df_by_country:
                df = st.session_state.get("df_raw", pd.DataFrame())
                if not df.empty:
                    country = st.session_state.get("country", "de")
                    df_by_country = {country: df}

            # Save the MAPPED dataset for each country (to mapped-datasets/ path)
            # This is separate from raw datasets saved in Step 1
            saved_paths = []

            if df_by_country:
                for country, df in df_by_country.items():
                    if df is not None and not df.empty:
                        try:
                            # Save mapped dataset for each country with shared timestamp
                            res = _save_mapped_to_gcs(
                                df, BUCKET, country, timestamp=shared_ts
                            )
                            saved_paths.append(res["data_gcs_path"])
                        except Exception as e:
                            st.error(
                                f"Failed to save dataset for {country}: {e}"
                            )
                            return  # Don't save metadata if dataset save failed

                st.session_state["picked_ts"] = shared_ts
                st.session_state["data_origin"] = "gcs_mapped"
                st.session_state["last_saved_mapped_path"] = (
                    saved_paths[0] if saved_paths else ""
                )

                if len(saved_paths) == 1:
                    st.success(f"✅ Saved mapped dataset → {saved_paths[0]}")
                else:
                    st.success(
                        f"✅ Saved mapped datasets for {len(saved_paths)} countries"
                    )
                    with st.expander("Saved paths"):
                        for path in saved_paths:
                            st.write(f"- {path}")
            else:
                st.warning("⚠️ No dataset loaded - saving metadata only")

            # Determine the country for saving metadata
            save_country = (
                st.session_state["country"]
                if save_country_specific
                else "universal"
            )

            # Use shared timestamp for metadata too
            vblob = _meta_blob(save_country, shared_ts)
            _safe_json_dump_to_gcs(payload, BUCKET, vblob)
            _safe_json_dump_to_gcs(
                payload, BUCKET, _meta_latest_blob(save_country)
            )
            st.session_state["last_saved_meta_path"] = f"gs://{BUCKET}/{vblob}"
            _list_country_versions_cached.clear()  # ⬅️ refresh local loader pickers
            _list_metadata_versions_cached.clear()  # ⬅️ refresh local metadata list
            list_data_versions.clear()  # ⬅️ refresh app_shared data cache
            list_meta_versions.clear()  # ⬅️ refresh app_shared metadata cache
            location_msg = (
                f"for {save_country.upper()}"
                if save_country_specific
                else "as universal mapping"
            )
            st.success(
                f"✅ Saved metadata {location_msg} → gs://{BUCKET}/{vblob} (and updated latest)"
            )
        except Exception as e:
            st.error(f"Failed to save metadata: {e}")

    cmeta1, cmeta2 = st.columns([1, 2])
    cmeta1.button(
        "💾 Save dataset & metadata to GCS",
        on_click=_save_metadata,
        width="stretch",
        help="Saves both the current dataset (with custom variables) and metadata configuration to GCS",
    )
    if st.session_state["last_saved_meta_path"]:
        cmeta2.caption(
            f"Last saved metadata: `{st.session_state['last_saved_meta_path']}`"
        )

    with st.expander("Preview metadata JSON", expanded=False):
        st.json(payload, expanded=False)

    if not st.session_state["mapping_df"].empty:
        allowed_categories = [
            "paid_media_spends",
            "paid_media_vars",
            "context_vars",
            "organic_vars",
            "factor_vars",
        ]
        # Use the filtered mapping for by_cat (excluding date and goals)
        by_cat_session = {
            cat: _by_cat(mapping_df_filtered, cat) for cat in allowed_categories
        }
        st.session_state["mapped_by_cat"] = (
            by_cat_session  # ← used by Experiment
        )
        st.session_state["mapped_dep_var"] = dep_var or ""

# --- Show Next only when we have metadata (either loaded or just saved)
can_go_next = not st.session_state["mapping_df"].empty
if can_go_next:
    st.divider()
    # Make the button wider by using a larger column ratio
    coln1, coln2 = st.columns([2, 4])
    with coln1:
        try:
            # Streamlit >= 1.27
            if st.button("Next → Prepare Training Data", width="stretch"):
                # Store values from Map Data for prefilling Prepare Training Data
                # 3.1: Store the main goal
                if not goals_df.empty:
                    main_goals = goals_df[
                        goals_df.get("main", False).astype(bool)
                    ]
                    if not main_goals.empty:
                        st.session_state["prefill_goal"] = str(
                            main_goals.iloc[0]["var"]
                        )
                    else:
                        # Fallback to first primary goal if no main is selected
                        primary_goals = goals_df[goals_df["group"] == "primary"]
                        if not primary_goals.empty:
                            st.session_state["prefill_goal"] = str(
                                primary_goals.iloc[0]["var"]
                            )

                # 3.2: Store paid media spends from mapping
                paid_spends_list = by_cat.get("paid_media_spends", [])
                st.session_state["prefill_paid_media_spends"] = paid_spends_list

                # 3.3: Store paid_media_mapping for media response variables
                st.session_state["prefill_paid_media_mapping"] = (
                    paid_media_mapping
                )

                # Store selected countries for Prepare Training Data page
                st.session_state["prefill_countries"] = st.session_state.get(
                    "selected_countries",
                    [st.session_state.get("country", "de")],
                )

                import streamlit as stlib

                stlib.switch_page("nav/Prepare_Training_Data.py")
        except Exception:
            # Fallback: link
            st.page_link(
                "nav/Prepare_Training_Data.py",
                label="Next → Prepare Training Data",
                icon="➡️",
            )
