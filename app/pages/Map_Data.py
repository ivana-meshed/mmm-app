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
    require_login_and_domain,
    run_sql,
    upload_to_gcs,
)
from app_split_helpers import *  # bring in all helper functions/constants
from google.cloud import storage

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
    client = storage.Client()
    b = client.bucket(gs_bucket)
    blob = b.blob(blob_path)
    if not blob.exists():
        raise FileNotFoundError(f"gs://{gs_bucket}/{blob_path} not found")
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
        blob.download_to_filename(tmp.name)
        df = pd.read_parquet(tmp.name)
    return df


def _save_raw_to_gcs(
    df: pd.DataFrame, bucket: str, country: str
) -> Dict[str, str]:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
        df.to_parquet(tmp.name, index=False)
        data_gcs_path = upload_to_gcs(bucket, tmp.name, _data_blob(country, ts))
        # maintain "latest" copy
        upload_to_gcs(bucket, tmp.name, _latest_symlink_blob(country))
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


# --- Session bootstrap (call once, early) ---
def _init_state():
    st.session_state.setdefault("country", "de")
    st.session_state.setdefault("df_raw", pd.DataFrame())
    st.session_state.setdefault("data_origin", "")
    st.session_state.setdefault("picked_ts", "")
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


def _initial_goals_from_columns(cols: list[str]) -> pd.DataFrame:
    # Pick a few top candidates by name for convenience; user can delete/edit
    candidates = [
        c
        for c in cols
        if any(
            k in c.lower()
            for k in ("rev", "gmv", "sales", "conv", "lead", "purchase")
        )
    ]
    # limit to a manageable number
    candidates = candidates[:8] if candidates else []
    return pd.DataFrame(
        {
            "var": pd.Series(candidates, dtype="object"),
            "group": pd.Series(["primary"] * len(candidates), dtype="object"),
            "type": pd.Series(
                [_guess_goal_type(c) for c in candidates], dtype="object"
            ),
            "main": pd.Series([False] * len(candidates), dtype="object"),
        }
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
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Apply automatic aggregations based on custom tags and categories.
    Returns: (updated_mapping_df, updated_df_raw)

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

        if organic_vars and total_var_name not in mapping_df["var"].values:
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

    return mapping_df, df_raw


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
st.title("Customize your analytics — map your data in 3 steps.")

# sensible defaults so we can read these anywhere
st.session_state.setdefault("sf_table", "MMM_RAW")
st.session_state.setdefault("sf_sql", "")
st.session_state.setdefault("sf_country_field", "COUNTRY")
st.session_state.setdefault("source_mode", "Latest (GCS)")

# ──────────────────────────────────────────────────────────────
# Step 1) Choose your dataset
# ──────────────────────────────────────────────────────────────
st.header("Step 1) Choose your dataset")

with st.expander("📊 Data Selection", expanded=False):
    # Country picker (ISO2, GCS-first). Keep this OUTSIDE the form.
    c1, c2 = st.columns([1.2, 2])
    with c1:
        countries = _iso2_countries_gcs_first(BUCKET)
        initial_idx = (
            countries.index(st.session_state.get("country", "de"))
            if st.session_state.get("country", "de") in countries
            else 0
        )
        st.selectbox(
            "Country (ISO2)",
            options=countries,
            index=initial_idx,
            key="country",  # don't also set st.session_state["country"] manually
        )
    with c2:
        st.caption(f"GCS Bucket: **{BUCKET}**")

    @_fragment()
    def step1_loader():
        country = st.session_state.get("country", "de")

        # Get available GCS versions for the chosen country
        versions_raw = _list_country_versions_cached(
            BUCKET, country
        )  # e.g. ["20250107_101500", "20241231_235959", "latest"]
        # Normalize versions: canonicalize any 'latest' -> 'Latest' and de-duplicate, preserving order
        seen = set()
        versions = []
        for v in versions_raw:
            vv = "Latest" if str(v).lower() == "latest" else v
            if vv not in seen:
                versions.append(vv)
                seen.add(vv)

        # Build source options with a single 'Latest' entry and no duplicate 'latest'
        source_options = (
            ["Latest"] + [v for v in versions if v != "Latest"] + ["Snowflake"]
        )

        # Use a FORM so edits don’t commit on every keystroke
        with st.form("load_data_form", clear_on_submit=False):
            st.write("**Source**")
            src_idx = (
                source_options.index(
                    st.session_state.get("source_choice", "Latest")
                )
                if st.session_state.get("source_choice", "Latest")
                in source_options
                else 0
            )
            source_choice = st.selectbox(
                " ",
                options=source_options,
                index=src_idx,
                key="source_choice",
                label_visibility="collapsed",
            )

            # Snowflake inputs (only relevant if Snowflake is chosen)
            st.write("**Snowflake options**")
            st.text_input("Table (DB.SCHEMA.TABLE)", key="sf_table")
            st.text_area("Custom SQL (optional)", key="sf_sql")
            st.text_input("Country field", key="sf_country_field")

            # Buttons row: Load + Refresh GCS list (side-by-side, wide)
            b1, b2 = st.columns([1, 1.2])
            with b1:
                load_clicked = st.form_submit_button(
                    "Load", use_container_width=True
                )
            with b2:
                refresh_clicked = st.form_submit_button(
                    "↻ Refresh GCS list", use_container_width=True
                )

        # --- right after the form block (i.e., after the `with st.form(...):` ends)
        if refresh_clicked:
            _list_country_versions_cached.clear()
            st.success("Refreshed GCS version list.")
            st.rerun()

        if not load_clicked:
            df = st.session_state["df_raw"]
            if not df.empty:
                st.caption("Preview (from session):")
                st.dataframe(
                    df.head(20), use_container_width=True, hide_index=True
                )
            return

        try:
            df = None
            choice = st.session_state.get("source_choice", "Latest")

            if choice == "Latest":
                # Try latest symlink; fallback to most recent timestamp if available; else Snowflake
                try:
                    df = _download_parquet_from_gcs_cached(
                        BUCKET, _latest_symlink_blob(country)
                    )
                    st.session_state.update(
                        {
                            "df_raw": df,
                            "data_origin": "gcs_latest",
                            "picked_ts": "latest",
                        }
                    )
                except Exception:
                    if versions:
                        fallback_ts = (
                            versions[0]
                            if versions[0] != "Latest"
                            else (versions[1] if len(versions) > 1 else None)
                        )
                        if fallback_ts:
                            st.info(
                                f"‘latest’ not found — loading most recent saved version: {fallback_ts}."
                            )
                            df = _download_parquet_from_gcs_cached(
                                BUCKET, _data_blob(country, fallback_ts)
                            )

                            st.session_state.update(
                                {
                                    "df_raw": df,
                                    "data_origin": "gcs_timestamp",
                                    "picked_ts": fallback_ts,
                                }
                            )
                        else:
                            st.info(
                                "No saved timestamp versions found in GCS; falling back to Snowflake."
                            )
                            _require_sf_session()
                            sql = (
                                effective_sql(
                                    st.session_state["sf_table"],
                                    st.session_state["sf_sql"],
                                )
                                or ""
                            )
                            if sql and not st.session_state["sf_sql"].strip():
                                sql = f"{sql} WHERE {st.session_state['sf_country_field']} = '{country.upper()}'"
                            if sql:
                                df = _load_from_snowflake_cached(sql)
                                st.session_state.update(
                                    {
                                        "df_raw": df,
                                        "data_origin": "snowflake",
                                        "picked_ts": "",
                                    }
                                )
                            else:
                                st.warning(
                                    "Provide a table or SQL to load from Snowflake."
                                )
                    else:
                        st.info(
                            "No saved data found in GCS; falling back to Snowflake."
                        )
                        _require_sf_session()
                        sql = (
                            effective_sql(
                                st.session_state["sf_table"],
                                st.session_state["sf_sql"],
                            )
                            or ""
                        )
                        if sql and not st.session_state["sf_sql"].strip():
                            sql = f"{sql} WHERE {st.session_state['sf_country_field']} = '{country.upper()}'"
                        if sql:
                            df = _load_from_snowflake_cached(sql)
                            st.session_state.update(
                                {
                                    "df_raw": df,
                                    "data_origin": "snowflake",
                                    "picked_ts": "",
                                }
                            )
                        else:
                            st.warning(
                                "Provide a table or SQL to load from Snowflake."
                            )

            elif choice in versions:
                # User picked a specific GCS timestamp directly from the Source list
                df = _download_parquet_from_gcs_cached(
                    BUCKET, _data_blob(country, choice)
                )
                st.session_state.update(
                    {
                        "df_raw": df,
                        "data_origin": "gcs_timestamp",
                        "picked_ts": choice,
                    }
                )

            else:  # "Snowflake"
                _require_sf_session()
                sql = (
                    effective_sql(
                        st.session_state["sf_table"], st.session_state["sf_sql"]
                    )
                    or ""
                )
                if sql and not st.session_state["sf_sql"].strip():
                    sql = f"{sql} WHERE {sql and st.session_state['sf_country_field']} = '{country.upper()}'"
                if sql:
                    df = _load_from_snowflake_cached(sql)
                    st.session_state.update(
                        {
                            "df_raw": df,
                            "data_origin": "snowflake",
                            "picked_ts": "",
                        }
                    )
                else:
                    st.warning("Provide a table or SQL to load from Snowflake.")

            if df is not None and not df.empty:
                st.success(f"Loaded {len(df):,} rows.")
                st.dataframe(
                    df.head(20), use_container_width=True, hide_index=True
                )
                st.rerun()
            else:
                st.warning("Data load finished, but no rows were returned.")

        except Exception as e:
            st.error(f"Load failed: {e}")

    step1_loader()

    # Save snapshot (button callback uses session data; no manual st.rerun())
    def _save_current_raw():
        df = st.session_state["df_raw"]
        if df.empty:
            st.warning("No dataset loaded.")
            return
        try:
            res = _save_raw_to_gcs(df, BUCKET, st.session_state["country"])
            st.session_state["picked_ts"] = res["timestamp"]
            st.session_state["data_origin"] = "gcs_latest"
            st.session_state["last_saved_raw_path"] = res["data_gcs_path"]
            _list_country_versions_cached.clear()  # ⬅️ invalidate immediately
            st.success(f"Saved raw snapshot → {res['data_gcs_path']}")
        except Exception as e:
            st.error(f"Saving to GCS failed: {e}")

    csave1, csave2 = st.columns([1, 3])
    csave1.button(
        "💾 Save dataset to GCS",
        on_click=_save_current_raw,
        use_container_width=True,
    )
    if st.session_state["last_saved_raw_path"]:
        csave2.caption(
            f"Last saved: `{st.session_state['last_saved_raw_path']}`"
        )

    st.divider()
    df_raw = st.session_state["df_raw"]
    if df_raw.empty:
        st.info("Load or select a dataset to continue.")
        st.stop()

# ──────────────────────────────────────────────────────────────
# Step 2) Map your data
# ──────────────────────────────────────────────────────────────
st.header("Step 2) Map your data")

# Show current data state (point 4 - UI representing actual state)
data_origin = st.session_state.get("data_origin", "N/A")
picked_ts = st.session_state.get("picked_ts", "N/A")
country = st.session_state.get("country", "N/A")

if df_raw is not None and not df_raw.empty:
    st.info(
        f"🔵 **Currently Loaded:** {data_origin.upper()} | Country: {country.upper()} | Timestamp: {picked_ts} | Rows: {len(df_raw):,} | Columns: {len(df_raw.columns)}"
    )
else:
    st.warning("⚪ No data loaded yet")

all_cols = df_raw.columns.astype(str).tolist()

# ---- Load saved metadata (moved to beginning of Step 2) ----
with st.expander(
    "📥 Load saved metadata & apply to current dataset", expanded=False
):
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
        "Metadata source",
        options=metadata_options,
        index=0,
        help="Select metadata configuration. Universal mappings work for all countries. Latest = most recently saved metadata.",
        key="load_metadata_source_selector",
    )

    # Add buttons for Load and Refresh
    col_load, col_refresh = st.columns([1, 1])
    with col_load:
        load_metadata_clicked = st.button(
            "Load & apply metadata",
            use_container_width=True,
            key="load_metadata_btn",
        )
    with col_refresh:
        refresh_metadata_clicked = st.button(
            "↻ Refresh metadata list",
            use_container_width=True,
            key="refresh_metadata_btn",
        )

    # Handle refresh button
    if refresh_metadata_clicked:
        _list_metadata_versions_cached.clear()
        st.success("Refreshed metadata list.")
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
            meta_version = meta_parts[1] if len(meta_parts) > 1 else "Latest"

            # Construct blob path
            if meta_version == "Latest" or meta_version.lower() == "latest":
                meta_blob = _meta_latest_blob(meta_country)
            else:
                meta_blob = _meta_blob(meta_country, str(meta_version))

            meta = _download_json_from_gcs(BUCKET, meta_blob)
            _apply_metadata_to_current_df(meta, all_cols, df_raw)

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
                            st.write(f"  - {cat}: {len(vars_list)} variable(s)")

                if "data" in meta:
                    data_info = meta["data"]
                    st.write(f"**Data Info:**")
                    st.write(f"  - Origin: {data_info.get('origin', 'N/A')}")
                    st.write(
                        f"  - Date field: {data_info.get('date_field', 'N/A')}"
                    )
                    st.write(
                        f"  - Row count: {data_info.get('row_count', 'N/A')}"
                    )

        except Exception as e:
            st.error(f"Failed to load metadata: {e}")

# ---- Goals (form) ----
with st.expander("🎯 Goals", expanded=False):
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
    date_field = st.selectbox(
        "Date field", options=(date_candidates or all_cols), index=0
    )

    st.divider()

    with st.form("goals_form", clear_on_submit=False):
        # Stack primary and secondary goals vertically
        primary_goals = st.multiselect(
            "Primary goal variables", options=all_cols, default=[]
        )
        secondary_goals = st.multiselect(
            "Secondary goal variables", options=all_cols, default=[]
        )

        def _mk(selected, group):
            return pd.DataFrame(
                {
                    "var": pd.Series(selected, dtype="object"),
                    "group": pd.Series([group] * len(selected), dtype="object"),
                    "type": pd.Series(
                        [_guess_goal_type(v) for v in selected], dtype="object"
                    ),
                    "main": pd.Series([False] * len(selected), dtype="object"),
                }
            )

        if st.session_state["goals_df"].empty:
            # Only suggest goals on first load, don't merge with heuristics later
            heur = _initial_goals_from_columns(all_cols)
            manual = pd.concat(
                [
                    _mk(primary_goals, "primary"),
                    _mk(secondary_goals, "secondary"),
                ],
                ignore_index=True,
            )
            goals_src = pd.concat([manual, heur], ignore_index=True)
            goals_src = goals_src.drop_duplicates(subset=["var"], keep="first")
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

        goals_edit = st.data_editor(
            goals_src,
            use_container_width=True,
            num_rows="dynamic",
            column_config={
                "var": st.column_config.SelectboxColumn(
                    "Variable", options=all_cols
                ),
                "group": st.column_config.SelectboxColumn(
                    "Group", options=["primary", "secondary"]
                ),
                "type": st.column_config.SelectboxColumn(
                    "Type", options=["revenue", "conversion"], required=True
                ),
                "main": st.column_config.CheckboxColumn(
                    "Main",
                    help="Select the main dependent variable for the model",
                    default=False,
                ),
            },
            key="goals_editor",
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
                .astype({"var": "object", "group": "object", "type": "object"})
            )
            merged["main"] = merged["main"].astype(bool)

            st.session_state["goals_df"] = merged
            st.success("Goals updated.")


# ---- Custom channels UI ----
with st.expander("📺 Custom Marketing Channels", expanded=False):
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
    all_existing_channels = sorted(set(recognized_channels + existing_custom))

    # Single input field with prefilled recognized channels
    channels_input = st.text_area(
        "Marketing Channels (comma-separated)",
        value=", ".join(all_existing_channels),
        help="Edit this list to set your marketing channels. These will be used to extract channel names from column names (e.g., 'facebook_spend' → 'facebook'). Add, remove, or modify channels as needed.",
        height=100,
        key="channels_input",
    )

    if st.button(
        "➕ Apply Channels", key="add_channels_btn", use_container_width=True
    ):
        # Parse the input
        entered_channels = [
            ch.strip().lower() for ch in channels_input.split(",") if ch.strip()
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
with st.expander("🏷️ Auto-tag Rules", expanded=False):
    rcol1, rcol2, rcol3 = st.columns(3)

    def _parse_sfx(s: str) -> list[str]:
        return [x.strip() for x in s.split(",") if x.strip()]

    new_rules = {
        "paid_media_spends": _parse_sfx(
            rcol1.text_input(
                "paid_media_spends suffixes",
                value=", ".join(
                    st.session_state["auto_rules"]["paid_media_spends"]
                ),
            )
        ),
        "paid_media_vars": _parse_sfx(
            rcol1.text_input(
                "paid_media_vars suffixes",
                value=", ".join(
                    st.session_state["auto_rules"]["paid_media_vars"]
                ),
                key="paid_vars",
            )
        ),
        "context_vars": _parse_sfx(
            rcol2.text_input(
                "context_vars suffixes",
                value=", ".join(st.session_state["auto_rules"]["context_vars"]),
            )
        ),
        "organic_vars": _parse_sfx(
            rcol2.text_input(
                "organic_vars suffixes",
                value=", ".join(st.session_state["auto_rules"]["organic_vars"]),
                key="org_vars",
            )
        ),
        "factor_vars": _parse_sfx(
            rcol3.text_input(
                "factor_vars suffixes",
                value=", ".join(st.session_state["auto_rules"]["factor_vars"]),
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
    st.write("**Prefixes for aggregated variables**")
    st.caption(
        "Define prefixes to use when creating aggregated columns for these categories"
    )

    pcol1, pcol2, pcol3 = st.columns(3)

    organic_prefix = pcol1.text_input(
        "organic_vars prefix",
        value=st.session_state.get("organic_vars_prefix", "organic_"),
        help="Prefix for aggregated organic variables (e.g., 'organic_')",
        key="organic_vars_prefix_input",
    )

    context_prefix = pcol2.text_input(
        "context_vars prefix",
        value=st.session_state.get("context_vars_prefix", "context_"),
        help="Prefix for aggregated context variables (e.g., 'context_')",
        key="context_vars_prefix_input",
    )

    factor_prefix = pcol3.text_input(
        "factor_vars prefix",
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
with st.expander("🗺️ Variable Mapping", expanded=False):
    # Add sorting controls (user-controlled, not automatic)
    st.write("**Sort mapping table:**")
    sort_col1, sort_col2, sort_col3 = st.columns([2, 1, 1])
    with sort_col1:
        sort_by = st.selectbox(
            "Sort by",
            options=[
                "Original order",
                "var",
                "category",
                "channel",
                "channel_subchannel",
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
            "🔄 Apply Sort", key="apply_sort_btn", use_container_width=True
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

                    m["_sort_key"] = m["var"].apply(extract_channel_subchannel)
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
            use_container_width=True,
            num_rows="dynamic",
            column_config={
                "var": st.column_config.TextColumn("Column", disabled=True),
                "category": st.column_config.SelectboxColumn(
                    "Category", options=ALLOWED_CATEGORIES
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
                    help="Strategy for aggregating when resampling. Numeric: sum/mean/max/min. Categorical: mode",
                ),
                "custom_tags": st.column_config.TextColumn(
                    "Custom Tags (optional)"
                ),
            },
            key="mapping_editor",
        )

        # Submit button inside the form to capture edits
        mapping_submit = st.form_submit_button("✅ Apply mapping changes")

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
            updated_mapping, updated_df = _apply_automatic_aggregations(
                mapping_edit.copy(), st.session_state["df_raw"].copy(), prefixes
            )
            st.session_state["mapping_df"] = updated_mapping
            st.session_state["df_raw"] = updated_df
            num_new = len(updated_mapping) - original_length
            st.success(
                f"✅ Mapping updated! Total: {len(updated_mapping)} variables."
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

# ──────────────────────────────────────────────────────────────
# Step 3) Save your mapping
# ──────────────────────────────────────────────────────────────
st.header("Step 3) Save your mapping")

with st.expander("💾 Save Mapping Configuration", expanded=False):
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

    # Checkbox for universal vs country-specific mapping
    save_country_specific = st.checkbox(
        f"Save only for {st.session_state['country'].upper()}",
        value=False,
        help="By default, mappings are saved universally for all countries. Check this to save only for the current country.",
    )

    meta_ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

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
        "saved_at": datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(),
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
            # Determine the country for saving
            save_country = (
                st.session_state["country"]
                if save_country_specific
                else "universal"
            )

            vblob = _meta_blob(save_country, meta_ts)
            _safe_json_dump_to_gcs(payload, BUCKET, vblob)
            _safe_json_dump_to_gcs(
                payload, BUCKET, _meta_latest_blob(save_country)
            )
            st.session_state["last_saved_meta_path"] = f"gs://{BUCKET}/{vblob}"
            _list_country_versions_cached.clear()  # ⬅️ refresh loader pickers
            location_msg = (
                f"for {save_country.upper()}"
                if save_country_specific
                else "as universal mapping"
            )
            st.success(
                f"Saved metadata {location_msg} → gs://{BUCKET}/{vblob} (and updated latest)"
            )
        except Exception as e:
            st.error(f"Failed to save metadata: {e}")

    cmeta1, cmeta2 = st.columns([1, 2])
    cmeta1.button(
        "💾 Save metadata to GCS",
        on_click=_save_metadata,
        use_container_width=True,
    )
    if st.session_state["last_saved_meta_path"]:
        cmeta2.caption(
            f"Last saved: `{st.session_state['last_saved_meta_path']}`"
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
    coln1, coln2 = st.columns([1, 5])
    with coln1:
        try:
            # Streamlit >= 1.27
            if st.button("Next → Experiment", use_container_width=True):
                import streamlit as stlib

                stlib.switch_page("pages/4_Run_Experiment.py")
        except Exception:
            # Fallback: link
            st.page_link(
                "pages/4_Run_Experiment.py", label="Next → Experiment", icon="➡️"
            )
