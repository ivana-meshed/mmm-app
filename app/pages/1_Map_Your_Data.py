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
    ensure_sf_conn,
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
st.set_page_config(page_title="Map your data", layout="wide")
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
        pd.DataFrame(columns=["var", "group", "type"]).astype("object"),
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


_init_state()

# Optional: fragments if your Streamlit supports it (safe no-op fallback)
_fragment = getattr(
    st,
    "fragment",
    lambda f=None, **_: (lambda *a, **k: f(*a, **k)) if f else (lambda f: f),
)


def _guess_goal_type(col: str) -> str:
    s = col.lower()
    revenue_keys = (
        "rev",
        "revenue",
        "gmv",
        "sales",
        "bookings",
        "turnover",
        "profit",
    )
    conversion_keys = (
        "conv",
        "conversion",
        "lead",
        "signup",
        "install",
        "purchase",
        "txn",
        "transactions",
        "orders",
    )
    if any(k in s for k in revenue_keys):
        return "revenue"
    if any(k in s for k in conversion_keys):
        return "conversion"
    # fallback: numeric columns with common names
    return "conversion"


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

    # Combine known channels with custom channels from session
    known_channels = _get_known_channels()
    custom_channels = st.session_state.get("custom_channels", [])
    all_channels = known_channels + custom_channels

    # Check if column starts with a known channel followed by underscore
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


def _apply_metadata_to_current_df(
    meta: dict, current_cols: list[str], df_raw: pd.DataFrame
) -> None:
    # goals (keep only ones that exist now)
    meta_goals = meta.get("goals", []) or []
    g = (
        pd.DataFrame(meta_goals).astype("object")
        if meta_goals
        else pd.DataFrame(columns=["var", "group", "type"]).astype("object")
    )
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

require_login_and_domain()

# sensible defaults so we can read these anywhere
st.session_state.setdefault("sf_table", "MMM_RAW")
st.session_state.setdefault("sf_sql", "")
st.session_state.setdefault("sf_country_field", "COUNTRY")
st.session_state.setdefault("source_mode", "Latest (GCS)")

# ──────────────────────────────────────────────────────────────
# Step 1) Choose your dataset
# ──────────────────────────────────────────────────────────────
st.header("Step 1) Choose your dataset")

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
            if st.session_state.get("source_choice", "Latest") in source_options
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
            st.dataframe(df.head(20), use_container_width=True, hide_index=True)
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
            st.dataframe(df.head(20), use_container_width=True, hide_index=True)
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
    "💾 Save this dataset to GCS (as new version)", on_click=_save_current_raw
)
if st.session_state["last_saved_raw_path"]:
    csave2.caption(f"Last saved: `{st.session_state['last_saved_raw_path']}`")

st.divider()
df_raw = st.session_state["df_raw"]
if df_raw.empty:
    st.info("Load or select a dataset to continue.")
    st.stop()

# ──────────────────────────────────────────────────────────────
# Step 2) Map your data
# ──────────────────────────────────────────────────────────────
st.header("Step 2) Map your data")

all_cols = df_raw.columns.astype(str).tolist()
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

# ---- Goals (form) ----
with st.form("goals_form", clear_on_submit=False):
    g1, g2 = st.columns(2)
    with g1:
        primary_goals = st.multiselect(
            "Primary goal variables", options=all_cols, default=[]
        )
    with g2:
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
            }
        )

    if st.session_state["goals_df"].empty:
        # manual first, heuristics after, then drop dups keeping manual
        heur = _initial_goals_from_columns(all_cols)
        manual = pd.concat(
            [_mk(primary_goals, "primary"), _mk(secondary_goals, "secondary")],
            ignore_index=True,
        )
        goals_src = pd.concat([manual, heur], ignore_index=True)
        goals_src = goals_src.drop_duplicates(subset=["var"], keep="first")
    else:
        # keep whatever is already in session as the starting table
        goals_src = st.session_state["goals_df"]

    goals_src = goals_src.fillna("").astype(
        {"var": "object", "group": "object", "type": "object"}
    )
    goals_edit = st.data_editor(
        goals_src,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "var": st.column_config.TextColumn("Variable"),
            "group": st.column_config.SelectboxColumn(
                "Group", options=["primary", "secondary"]
            ),
            "type": st.column_config.SelectboxColumn(
                "Type", options=["revenue", "conversion"]
            ),
        },
        key="goals_editor",
    )
    goals_submit = st.form_submit_button("✅ Apply goal changes")

if goals_submit:
    base = st.session_state.get("goals_df")
    edited = goals_edit.copy()

    # keep only non-empty vars
    edited = edited[edited["var"].astype(str).str.strip() != ""].astype(
        "object"
    )

    if base is None or base.empty:
        merged = edited
    else:
        # prefer edited rows on conflicts
        base = base.astype("object")
        base_no_dups = base[~base["var"].isin(edited["var"])]
        merged = pd.concat([base_no_dups, edited], ignore_index=True)

    # normalize dtypes & drop accidental duplicates
    merged = (
        merged.drop_duplicates(subset=["var"], keep="last")
        .fillna("")
        .astype({"var": "object", "group": "object", "type": "object"})
    )

    st.session_state["goals_df"] = merged
    st.success("Goals appended & updated.")


# ---- Custom channels UI ----
st.subheader("Custom Marketing Channels")
ch_col1, ch_col2 = st.columns([2, 1])
with ch_col1:
    new_channel = st.text_input(
        "Add custom channel (e.g., 'spotify', 'podcast')",
        key="new_channel_input",
        help="Enter channel names that aren't in the default list",
    )
with ch_col2:
    if st.button("➕ Add Channel", key="add_channel_btn"):
        if new_channel and new_channel.strip():
            custom_channels = st.session_state.get("custom_channels", [])
            channel_lower = new_channel.strip().lower()
            if (
                channel_lower not in custom_channels
                and channel_lower not in _get_known_channels()
            ):
                custom_channels.append(channel_lower)
                st.session_state["custom_channels"] = custom_channels
                st.success(f"Added custom channel: {channel_lower}")
                st.rerun()
            elif channel_lower in custom_channels:
                st.warning(
                    f"Channel '{channel_lower}' already exists in custom channels"
                )
            else:
                st.warning(
                    f"Channel '{channel_lower}' already exists in known channels"
                )

# Display current custom channels
if st.session_state.get("custom_channels"):
    st.caption(
        "Custom channels: " + ", ".join(st.session_state["custom_channels"])
    )
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

# combine and dedupe
recognized_channels = sorted(set(mapping_channels + inferred_channels))

if recognized_channels:
    st.caption(
        "Recognized channels from data: " + ", ".join(recognized_channels)
    )

st.divider()

# ---- Auto-tag rules (simple inputs update state immediately, but we only regenerate mapping when rules actually changed) ----
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
            value=", ".join(st.session_state["auto_rules"]["paid_media_vars"]),
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


# ---- Mapping editor (form) ----

with st.expander(
    "📥 Load saved metadata & apply to current dataset", expanded=False
):
    lc1, lc2, lc3 = st.columns([1.2, 1, 1])
    load_country = (
        lc1.text_input(
            "Country (metadata source)", value=st.session_state["country"]
        )
        or ""
    )  # ensure string, not None

    # list available versions for chosen country
    if load_country.strip():
        meta_versions_raw = _list_country_versions_cached(BUCKET, load_country)
    else:
        meta_versions_raw = []

    # Normalize meta versions the same way (canonical 'Latest')
    seen_meta = set()
    meta_versions = []
    for v in meta_versions_raw:
        vv = "Latest" if str(v).lower() == "latest" else v
        if vv not in seen_meta:
            meta_versions.append(vv)
            seen_meta.add(vv)

    # Allow 'Latest' for metadata
    version_opts = ["Latest"] + [v for v in meta_versions if v != "Latest"]
    picked_meta_ts = lc2.selectbox("Version", options=version_opts, index=0)

    if lc3.button("Load & apply"):
        if not load_country.strip():
            st.warning("Please select a country first.")
        elif not picked_meta_ts:
            st.warning("Please select a metadata version.")
        else:
            try:
                meta_blob: str
                if picked_meta_ts == "Latest":
                    meta_blob = _meta_latest_blob(load_country)
                else:
                    # coerce picked_meta_ts to str to satisfy the type checker
                    meta_blob = _meta_blob(load_country, str(picked_meta_ts))

                meta = _download_json_from_gcs(BUCKET, meta_blob)
                _apply_metadata_to_current_df(meta, all_cols, df_raw)
                st.success(f"Applied metadata from gs://{BUCKET}/{meta_blob}")
            except Exception as e:
                st.error(f"Failed to load metadata: {e}")

# ✅ if still empty (first load), seed using current rules (outside the form)
if st.session_state["mapping_df"].empty:
    st.session_state["mapping_df"] = _build_mapping_df(
        all_cols, df_raw, st.session_state["auto_rules"]
    )

# --- Re-apply auto-tag rules on demand (outside any form) ---
rt1, rt2 = st.columns([1, 1])

# Only fill previously-untagged rows (preserves manual edits)
if rt1.button("🔁 Auto-tag UNTAGGED columns", key="retag_missing"):
    m = st.session_state["mapping_df"].copy()
    inferred = {
        c: _infer_category(c, st.session_state["auto_rules"]) for c in all_cols
    }

    # Ensure all columns exist
    if "channel" not in m.columns:
        m["channel"] = ""
    if "data_type" not in m.columns:
        m["data_type"] = "numeric"
    if "agg_strategy" not in m.columns:
        m["agg_strategy"] = "sum"

    # Fill missing values
    m["category"] = m.apply(
        lambda r: (
            r["category"]
            if str(r["category"]).strip()
            else inferred.get(r["var"], "")
        ),
        axis=1,
    )
    m["channel"] = m.apply(
        lambda r: (
            r["channel"]
            if str(r.get("channel", "")).strip()
            else _extract_channel_from_column(str(r["var"]))
        ),
        axis=1,
    )
    m["data_type"] = m.apply(
        lambda r: (
            r["data_type"]
            if str(r.get("data_type", "")).strip()
            else _detect_data_type(df_raw, str(r["var"]))
        ),
        axis=1,
    )
    m["agg_strategy"] = m.apply(
        lambda r: (
            r["agg_strategy"]
            if str(r.get("agg_strategy", "")).strip()
            else _default_agg_strategy(str(r.get("data_type", "numeric")))
        ),
        axis=1,
    )
    st.session_state["mapping_df"] = m.astype("object")
    st.success("Filled categories for previously untagged columns.")

# Overwrite everything from current rules (discard manual edits)
if rt2.button("♻️ Re-apply to ALL columns", key="retag_all"):
    st.session_state["mapping_df"] = _build_mapping_df(
        all_cols, df_raw, st.session_state["auto_rules"]
    )
    st.warning(
        "Re-applied rules to ALL columns (manual categories were overwritten)."
    )


with st.form("mapping_form_main", clear_on_submit=False):
    mapping_src = st.session_state["mapping_df"].fillna("")

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
        set(_get_known_channels() + st.session_state.get("custom_channels", []))
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
                options=["sum", "mean", "max", "min", "auto", "mode"],
                help="Strategy for aggregating when resampling. Numeric: sum/mean/max/min. Categorical: auto/mean/sum/max/mode",
            ),
            "custom_tags": st.column_config.TextColumn(
                "Custom Tags (optional)"
            ),
        },
        key="mapping_editor",
    )

    mapping_submit = st.form_submit_button("✅ Apply mapping changes")

if mapping_submit:
    st.session_state["mapping_df"] = mapping_edit
    st.success("Mapping updated.")

# ──────────────────────────────────────────────────────────────
# Channel Aggregation: OTHER and TOTAL columns
# ──────────────────────────────────────────────────────────────
st.subheader("Channel Aggregation (Optional)")
st.markdown(
    """
Add aggregated columns for each marketing channel:
- **`<CHANNEL>_OTHER`**: Sum of columns in the channel that lack a category
- **`<CHANNEL>_TOTAL`**: Sum of all columns in the channel
"""
)

# Get unique channels from mapping
mapping_df_current = st.session_state["mapping_df"]
unique_channels = sorted(
    [
        ch
        for ch in mapping_df_current["channel"].dropna().unique()
        if str(ch).strip() and str(ch).lower() != "nan"
    ]
)

if unique_channels:
    selected_channels = st.multiselect(
        "Select channels to create aggregates for:",
        options=unique_channels,
        default=[],
        help="For each selected channel, create _OTHER and _TOTAL columns",
    )

    if st.button("➕ Add Channel Aggregates", key="add_aggregates"):
        if not selected_channels:
            st.warning("Please select at least one channel")
        else:
            df_current = st.session_state["df_raw"].copy()
            new_cols_added = []

            for channel in selected_channels:
                # Get all columns for this channel
                channel_cols = mapping_df_current[
                    mapping_df_current["channel"].str.lower() == channel.lower()
                ]["var"].tolist()

                if not channel_cols:
                    continue

                # Filter to numeric columns only
                numeric_channel_cols = [
                    c
                    for c in channel_cols
                    if c in df_current.columns
                    and pd.api.types.is_numeric_dtype(df_current[c])
                ]

                if not numeric_channel_cols:
                    st.warning(
                        f"No numeric columns found for channel '{channel}'"
                    )
                    continue

                # Get columns without category (for OTHER)
                # --- Create per-suffix aggregates for this channel (TOTAL and OTHER) ---
                import re
                from collections import defaultdict

                # Build suffix → cols mapping for numeric columns in this channel.
                # Suffix = part after the first underscore following the channel name,
                # fallback to last token after last underscore if needed.
                suffix_to_cols = defaultdict(list)
                for c in channel_cols:
                    if c not in df_current.columns:
                        continue
                    if not pd.api.types.is_numeric_dtype(df_current[c]):
                        continue
                    low = c.lower()
                    if low.startswith(f"{channel.lower()}_"):
                        suffix = low[len(channel) + 1 :]
                    else:
                        suffix = low.split("_")[-1] or low
                    suffix_to_cols[suffix].append(c)

                for suffix, cols_in_suffix in suffix_to_cols.items():
                    # sanitize suffix for column name
                    safe_suffix = re.sub(r"\W+", "_", suffix).strip("_").upper()
                    if not safe_suffix:
                        continue

                    # create TOTAL per suffix (sums all numeric columns in this suffix group)
                    total_col_name = f"{channel.upper()}_TOTAL_{safe_suffix}"
                    if total_col_name not in df_current.columns:
                        df_current[total_col_name] = df_current[
                            cols_in_suffix
                        ].sum(axis=1)
                        new_cols_added.append(total_col_name)

                        # infer category for generated total using your auto_rules (fallback)
                        inferred_cat = (
                            _infer_category(
                                f"_{suffix}", st.session_state["auto_rules"]
                            )
                            or "paid_media_spends"
                        )
                        new_row = pd.DataFrame(
                            [
                                {
                                    "var": total_col_name,
                                    "category": inferred_cat,
                                    "channel": channel,
                                    "data_type": "numeric",
                                    "agg_strategy": "sum",
                                    "custom_tags": "auto_generated_total",
                                }
                            ]
                        )
                        st.session_state["mapping_df"] = pd.concat(
                            [st.session_state["mapping_df"], new_row],
                            ignore_index=True,
                        )

                    # create OTHER per suffix: only sum columns in this suffix group that are uncategorized / marked as other
                    # build mask restricting mapping rows to this channel+suffix group
                    cols_in_suffix_lower = {ci.lower() for ci in cols_in_suffix}
                    mask_suffix = (
                        mapping_df_current["var"]
                        .astype(str)
                        .str.lower()
                        .isin(cols_in_suffix_lower)
                    )

                    mask_uncategorized = mask_suffix & (
                        mapping_df_current["category"]
                        .astype(str)
                        .str.strip()
                        .eq("")
                        | mapping_df_current["category"]
                        .astype(str)
                        .str.lower()
                        .eq("other")
                        | mapping_df_current["custom_tags"]
                        .astype(str)
                        .str.lower()
                        .str.contains(r"\bother\b", regex=True)
                    )

                    uncategorized_cols = mapping_df_current.loc[
                        mask_uncategorized, "var"
                    ].tolist()
                    numeric_uncategorized = [
                        c
                        for c in uncategorized_cols
                        if c in df_current.columns
                        and pd.api.types.is_numeric_dtype(df_current[c])
                    ]

                    if numeric_uncategorized:
                        other_col_name = (
                            f"{channel.upper()}_OTHER_{safe_suffix}"
                        )
                        if other_col_name not in df_current.columns:
                            df_current[other_col_name] = df_current[
                                numeric_uncategorized
                            ].sum(axis=1)
                            new_cols_added.append(other_col_name)

                            inferred_cat = (
                                _infer_category(
                                    f"_{suffix}", st.session_state["auto_rules"]
                                )
                                or "paid_media_spends"
                            )
                            new_row = pd.DataFrame(
                                [
                                    {
                                        "var": other_col_name,
                                        "category": inferred_cat,
                                        "channel": channel,
                                        "data_type": "numeric",
                                        "agg_strategy": "sum",
                                        "custom_tags": "auto_generated_other",
                                    }
                                ]
                            )
                            st.session_state["mapping_df"] = pd.concat(
                                [st.session_state["mapping_df"], new_row],
                                ignore_index=True,
                            )
            # Update the raw dataframe
            if new_cols_added:
                st.session_state["df_raw"] = df_current
                st.success(
                    f"Added {len(new_cols_added)} aggregate column(s): {', '.join(new_cols_added)}"
                )
                st.rerun()
            else:
                st.info(
                    "No new aggregate columns were created (they may already exist)"
                )
else:
    st.info(
        "No channels detected in the mapping. Add channel information to columns first."
    )

st.divider()

# ──────────────────────────────────────────────────────────────
# Step 3) Save your mapping
# ──────────────────────────────────────────────────────────────
st.header("Step 3) Save your mapping")

goals_df = st.session_state["goals_df"]
mapping_df = st.session_state["mapping_df"]
auto_rules = st.session_state["auto_rules"]


def _by_cat(df: pd.DataFrame, cat: str) -> list[str]:
    return df.loc[df["category"] == cat, "var"].dropna().astype(str).tolist()


# Use allowed categories from constant, excluding empty string for by_cat
by_cat = {cat: _by_cat(mapping_df, cat) for cat in ALLOWED_CATEGORIES if cat}

dep_options = goals_df["var"].tolist() or df_raw.columns.astype(str).tolist()
dep_var = st.selectbox(
    "Pick main dependent variable (optional)",
    options=dep_options,
    index=0 if dep_options else None,
)

meta_ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
goals_json = [
    {"var": str(r["var"]), "group": str(r["group"]), "type": str(r["type"])}
    for _, r in goals_df.iterrows()
    if str(r.get("var", "")).strip()
]

# Extract channel, data_type, and agg_strategy mappings
channels_map = {
    str(r["var"]): str(r.get("channel", ""))
    for _, r in mapping_df.iterrows()
    if str(r.get("channel", "")).strip()
}
data_types_map = {
    str(r["var"]): str(r.get("data_type", "numeric"))
    for _, r in mapping_df.iterrows()
}
agg_strategies_map = {
    str(r["var"]): str(r.get("agg_strategy", "sum"))
    for _, r in mapping_df.iterrows()
}

payload = {
    "project_id": PROJECT_ID,
    "bucket": BUCKET,
    "country": st.session_state["country"],
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
    "dep_var": dep_var or "",
}


def _save_metadata():
    try:
        vblob = _meta_blob(st.session_state["country"], meta_ts)
        _safe_json_dump_to_gcs(payload, BUCKET, vblob)
        _safe_json_dump_to_gcs(
            payload, BUCKET, _meta_latest_blob(st.session_state["country"])
        )
        st.session_state["last_saved_meta_path"] = f"gs://{BUCKET}/{vblob}"
        _list_country_versions_cached.clear()  # ⬅️ refresh loader pickers
        st.success(
            f"Saved metadata → gs://{BUCKET}/{vblob} (and updated latest)"
        )
    except Exception as e:
        st.error(f"Failed to save metadata: {e}")


cmeta1, cmeta2 = st.columns([1, 2])
cmeta1.button("💾 Save metadata to GCS", on_click=_save_metadata)
if st.session_state["last_saved_meta_path"]:
    cmeta2.caption(f"Last saved: `{st.session_state['last_saved_meta_path']}`")

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
    by_cat = {
        cat: _by_cat(st.session_state["mapping_df"], cat)
        for cat in allowed_categories
    }
    st.session_state["mapped_by_cat"] = by_cat  # ← used by Experiment
    st.session_state["mapped_dep_var"] = st.session_state.get("dep_var", "")

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

                stlib.switch_page("pages/2_Experiment.py")
        except Exception:
            # Fallback: link
            st.page_link(
                "pages/2_Experiment.py", label="Next → Experiment", icon="➡️"
            )
