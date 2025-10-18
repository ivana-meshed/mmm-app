# pages/2_Customize_Analytics.py
import os, io, json, tempfile
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st
from google.cloud import storage

from app_shared import (
    require_login_and_domain,
    get_data_processor,
    run_sql,
    ensure_sf_conn,
    upload_to_gcs,
    effective_sql,
    GCS_BUCKET,
    PROJECT_ID,
)

# ──────────────────────────────────────────────────────────────
# Setup
# ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="Map your data", layout="wide")
require_login_and_domain()

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


# ──────────────────────────────────────────────────────────────
# Page header & helper image
# ──────────────────────────────────────────────────────────────
st.title("Customize your analytics — map your data in 3 steps.")

# ──────────────────────────────────────────────────────────────
# Step 1) Choose your dataset
# ──────────────────────────────────────────────────────────────
st.header("Step 1) Choose your dataset")

c1, c2, c3 = st.columns([1.5, 1, 2])
with c1:
    country = st.text_input(
        "Country", value=st.session_state.get("country", "fr")
    ).strip()
with c2:
    source_mode = st.selectbox(
        "Source",
        ["Latest (GCS)", "Previous (GCS)", "Snowflake (current)"],
        help="Pick latest saved snapshot, a previous timestamp, or pull fresh from Snowflake.",
    )
with c3:
    st.caption(f"GCS Bucket: **{BUCKET}**")

df_raw: Optional[pd.DataFrame] = None
picked_ts: Optional[str] = None
data_origin: str = "unknown"

if country:
    if source_mode in ("Latest (GCS)", "Previous (GCS)"):
        # list versions for this country
        versions = _list_country_versions(BUCKET, country)
        if not versions:
            st.info(
                "No saved data found in GCS for this country. Falling back to Snowflake."
            )
            source_mode = "Snowflake (current)"

    if source_mode == "Latest (GCS)":
        try:
            # “latest” symlink path
            df_raw = _download_parquet_from_gcs(
                BUCKET, _latest_symlink_blob(country)
            )
            data_origin = "gcs_latest"
            picked_ts = "latest"
        except Exception as e:
            st.warning(f"Could not load latest from GCS: {e}")
            df_raw = None

    if source_mode == "Previous (GCS)":
        with st.container():
            ts = st.selectbox("Pick a timestamp (GCS)", options=versions)
        if ts:
            try:
                df_raw = _download_parquet_from_gcs(
                    BUCKET, _data_blob(country, ts)
                )
                data_origin = "gcs_timestamp"
                picked_ts = ts
            except Exception as e:
                st.error(f"Failed to load selected version: {e}")
                df_raw = None

    if source_mode == "Snowflake (current)":
        st.info(
            "If not connected, go to the **Snowflake Connection** page first."
        )
        default_table = st.session_state.get("sf_preview_table", "MMM_RAW")
        with st.container(border=True):
            st.caption("Pull data from Snowflake for this country")
            t1, t2 = st.columns([2, 1])
            with t1:
                table = st.text_input(
                    "Table (DB.SCHEMA.TABLE)", value=default_table
                )
                custom_sql = st.text_area("Custom SQL (optional)", value="")
            with t2:
                country_field = st.text_input("Country field", value="COUNTRY")
                limit_rows = st.number_input(
                    "Row limit (preview before save)",
                    min_value=100,
                    value=100000,
                )
            if st.button("Load from Snowflake"):
                sql = effective_sql(table, custom_sql) or ""
                if not sql:
                    st.warning("Provide a table or a SQL query.")
                else:
                    try:
                        # add filter by country if user used table or forgot it in SQL
                        if not custom_sql.strip():
                            sql = f"{sql} WHERE {country_field} = '{country.upper()}'"
                        df_raw = run_sql(sql)
                        data_origin = "snowflake"
                        picked_ts = None
                        st.success(
                            f"Loaded {len(df_raw):,} rows from Snowflake."
                        )
                    except Exception as e:
                        st.error(f"Snowflake load failed: {e}")
                        df_raw = None

# Preview + Save snapshot to GCS
if df_raw is not None and not df_raw.empty:
    st.caption("Preview (first 20 rows):")
    st.dataframe(df_raw.head(20), use_container_width=True, hide_index=True)

    csave1, csave2 = st.columns([1, 1])
    do_save = csave1.button("💾 Save this dataset to GCS (as new version)")
    if do_save:
        try:
            res = _save_raw_to_gcs(df_raw, BUCKET, country)
            picked_ts = res["timestamp"]
            st.success(f"Saved raw snapshot → {res['data_gcs_path']}")
            data_origin = "gcs_latest"
        except Exception as e:
            st.error(f"Saving to GCS failed: {e}")
else:
    st.info("Load or select a dataset to continue.")

st.divider()

# Stop here until we have data
if df_raw is None or df_raw.empty:
    st.stop()

# ──────────────────────────────────────────────────────────────
# Step 2) Map your data
# ──────────────────────────────────────────────────────────────
st.header("Step 2) Map your data")

all_cols = df_raw.columns.tolist()
date_candidates = [c for c in all_cols if c.lower() in ("date", "ds")] + [
    c for c in all_cols if "date" in c.lower() or c.lower().endswith("_dt")
]
date_candidates = sorted(set(date_candidates), key=str.lower)

# 2a) Goal variables (primary/secondary) + type
g1, g2, g3 = st.columns([1.4, 1.4, 1])
with g1:
    primary_goals = st.multiselect(
        "Primary goal variables", options=all_cols, default=[]
    )
with g2:
    secondary_goals = st.multiselect(
        "Secondary goal variables", options=all_cols, default=[]
    )
with g3:
    date_field = st.selectbox(
        "Date field", options=(date_candidates or all_cols), index=0
    )


def _build_goals_df(selected: List[str], group: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "var": selected,
            "group": group,
            "type": [
                (
                    "revenue"
                    if "rev" in v.lower() or "gmv" in v.lower()
                    else "conversion"
                )
                for v in selected
            ],
        }
    )


goals_df = pd.concat(
    [
        _build_goals_df(primary_goals, "primary"),
        _build_goals_df(secondary_goals, "secondary"),
    ],
    ignore_index=True,
)
st.caption("Tag goal variables with a type (used later as dep_variable_type):")
goals_df = st.data_editor(
    goals_df,
    use_container_width=True,
    num_rows="dynamic",
    column_config={
        "group": st.column_config.SelectboxColumn(
            "Group", options=["primary", "secondary"]
        ),
        "type": st.column_config.SelectboxColumn(
            "Type", options=["revenue", "conversion"]
        ),
        "var": st.column_config.TextColumn("Variable"),
    },
    key="goals_editor",
)

# 2b) Auto-tag rules (suffix → category)
st.subheader("Auto-tag rules")
st.caption(
    "Columns ending with these suffixes will be classified automatically; you can still edit them in the table below."
)


def _txt(defaults: List[str]) -> str:
    return ", ".join(defaults)


rcol1, rcol2, rcol3 = st.columns(3)
with rcol1:
    paid_spends_suffix = st.text_input(
        "paid_media_spends suffixes", value="_cost,_spend"
    )
    paid_vars_suffix = st.text_input(
        "paid_media_vars suffixes", value="_impressions,_clicks,_sessions"
    )
with rcol2:
    context_suffix = st.text_input(
        "context_vars suffixes", value="_index,_temp,_price,_holiday"
    )
    organic_suffix = st.text_input(
        "organic_vars suffixes", value="_organic,_direct"
    )
with rcol3:
    factor_suffix = st.text_input("factor_vars suffixes", value="_flag,_is,_on")


def _parse_sfx(s: str) -> List[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


auto_rules = {
    "paid_media_spends": _parse_sfx(paid_spends_suffix),
    "paid_media_vars": _parse_sfx(paid_vars_suffix),
    "context_vars": _parse_sfx(context_suffix),
    "organic_vars": _parse_sfx(organic_suffix),
    "factor_vars": _parse_sfx(factor_suffix),
}


# 2c) Apply rules → editable mapping table
def _infer_category(col: str, rules: Dict[str, List[str]]) -> str:
    for cat, endings in rules.items():
        for suf in endings:
            if col.lower().endswith(suf.lower()):
                return cat
    return ""  # not tagged


mapping_seed = pd.DataFrame(
    {
        "var": all_cols,
        "category": [_infer_category(c, auto_rules) for c in all_cols],
        "custom_tags": ["" for _ in all_cols],  # optional extra tag
    }
)

st.subheader("Applied mapping (editable)")
st.caption(
    "Edit categories for any columns not auto-tagged. Only categories listed below are recognized for Robyn inputs."
)
allowed_categories = [
    "paid_media_spends",
    "paid_media_vars",
    "context_vars",
    "organic_vars",
    "factor_vars",
    "",
]
mapping_df = st.data_editor(
    mapping_seed,
    use_container_width=True,
    key="mapping_editor",
    column_config={
        "var": st.column_config.TextColumn("Column"),
        "category": st.column_config.SelectboxColumn(
            "Category", options=allowed_categories
        ),
        "custom_tags": st.column_config.TextColumn("Custom Tags (optional)"),
    },
)


# Build compact lists by category (to feed Robyn later)
def _by_cat(df: pd.DataFrame, cat: str) -> List[str]:
    return df.loc[df["category"] == cat, "var"].dropna().astype(str).tolist()


by_cat = {cat: _by_cat(mapping_df, cat) for cat in allowed_categories if cat}

st.divider()

# ──────────────────────────────────────────────────────────────
# Step 3) Save your mapping
# ──────────────────────────────────────────────────────────────
st.header("Step 3) Save your mapping")

# pick a dep_var to highlight (optional UX)
dep_var = st.selectbox(
    "Pick the main dependent variable for this dataset (optional)",
    options=(primary_goals or secondary_goals or all_cols),
    index=0 if (primary_goals or secondary_goals or all_cols) else None,
)

meta_ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
goals_json = [
    {"var": r["var"], "group": r["group"], "type": r["type"]}
    for _, r in goals_df.iterrows()
    if str(r.get("var", "")).strip()
]

payload = {
    "project_id": PROJECT_ID,
    "bucket": BUCKET,
    "country": country,
    "saved_at": datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(),
    "data": {
        "origin": data_origin,  # gcs_latest | gcs_timestamp | snowflake
        "timestamp": picked_ts,  # 'latest' or ts for data snapshot
        "date_field": date_field,
        "row_count": int(len(df_raw)),
    },
    "goals": goals_json,  # primary/secondary + type
    "dep_variable_type": {g["var"]: g["type"] for g in goals_json},
    "autotag_rules": auto_rules,  # suffix rules you typed
    "mapping": by_cat,  # columns bucketed by category
    "dep_var": dep_var or "",  # convenience for your UI later
}

st.json(payload, expanded=False)

cmeta1, cmeta2 = st.columns([1, 1])
if cmeta1.button("💾 Save metadata to GCS"):
    try:
        # Save versioned + latest
        meta_blob_v = _meta_blob(country, meta_ts)
        meta_blob_latest = _meta_latest_blob(country)
        _safe_json_dump_to_gcs(payload, BUCKET, meta_blob_v)
        _safe_json_dump_to_gcs(payload, BUCKET, meta_blob_latest)
        st.success(
            f"Saved metadata → gs://{BUCKET}/{meta_blob_v} (and updated latest)"
        )
    except Exception as e:
        st.error(f"Failed to save metadata: {e}")

if cmeta2.button("🔎 Validate categories"):
    unknown = sorted(
        set(mapping_df["category"].fillna("").unique())
        - set(allowed_categories)
    )
    if unknown:
        st.warning(f"Unknown categories found: {unknown}")
    else:
        st.success("All good: categories are valid.")
