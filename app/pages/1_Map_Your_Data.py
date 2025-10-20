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
    return run_sql(sql)


# --- Session bootstrap (call once, early) ---
def _init_state():
    st.session_state.setdefault("country", "fr")
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
            "paid_media_spends": ["_cost", "_spend"],
            "paid_media_vars": ["_impressions", "_clicks", "_sessions"],
            "context_vars": ["_index", "_temp", "_price", "_holiday"],
            "organic_vars": ["_organic", "_direct"],
            "factor_vars": ["_flag", "_is", "_on"],
        },
    )
    st.session_state.setdefault(
        "mapping_df",
        pd.DataFrame(columns=["var", "category", "custom_tags"]).astype(
            "object"
        ),
    )
    st.session_state.setdefault("last_saved_raw_path", "")
    st.session_state.setdefault("last_saved_meta_path", "")


_init_state()

# Optional: fragments if your Streamlit supports it (safe no-op fallback)
_fragment = getattr(
    st,
    "fragment",
    lambda f=None, **_: (lambda *a, **k: f(*a, **k)) if f else (lambda f: f),
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
        "Source", ["Latest (GCS)", "Previous (GCS)", "Snowflake (current)"]
    )
with c3:
    st.caption(f"GCS Bucket: **{BUCKET}**")

st.session_state["country"] = country


@_fragment()
def step1_loader():
    # Use a FORM so edits don’t commit on every keystroke
    with st.form("load_data_form", clear_on_submit=False):
        versions = []
        if country and source_mode in ("Latest (GCS)", "Previous (GCS)"):
            versions = _list_country_versions_cached(BUCKET, country)
            if not versions:
                st.info(
                    "No saved data found in GCS for this country. Falling back to Snowflake."
                )
        ts_choice = None
        if source_mode == "Previous (GCS)" and versions:
            ts_choice = st.selectbox(
                "Pick a timestamp (GCS)", options=versions, key="pick_ts"
            )

        # Snowflake inputs (shown but harmless when not used)
        default_table = st.session_state.get("sf_preview_table", "MMM_RAW")
        tcol = st.text_input(
            "Table (DB.SCHEMA.TABLE)", value=default_table, key="sf_table"
        )
        qcol = st.text_area("Custom SQL (optional)", value="", key="sf_sql")
        cfield = st.text_input(
            "Country field", value="COUNTRY", key="sf_country_field"
        )

        submitted = st.form_submit_button("Load")

    if not submitted:
        # Show what we currently have in memory
        df = st.session_state["df_raw"]
        if not df.empty:
            st.caption("Preview (from session):")
            st.dataframe(df.head(20), use_container_width=True, hide_index=True)
        return

    # ——— On submit: perform the actual load (cached) and persist in session_state
    try:
        if source_mode == "Latest (GCS)" and versions:
            df = _download_parquet_from_gcs_cached(
                BUCKET, _latest_symlink_blob(country)
            )
            st.session_state.update(
                df_raw=df, data_origin="gcs_latest", picked_ts="latest"
            )

        elif source_mode == "Previous (GCS)" and ts_choice:
            df = _download_parquet_from_gcs_cached(
                BUCKET, _data_blob(country, ts_choice)
            )
            st.session_state.update(
                df_raw=df, data_origin="gcs_timestamp", picked_ts=ts_choice
            )

        else:  # Snowflake (or GCS fallback with no versions)
            sql = (
                effective_sql(
                    st.session_state["sf_table"], st.session_state["sf_sql"]
                )
                or ""
            )
            if sql and not st.session_state["sf_sql"].strip():
                sql = f"{sql} WHERE {st.session_state['sf_country_field']} = '{country.upper()}'"
            df = _load_from_snowflake_cached(sql)
            st.session_state.update(
                df_raw=df, data_origin="snowflake", picked_ts=""
            )

        st.success(f"Loaded {len(df):,} rows.")
        st.dataframe(df.head(20), use_container_width=True, hide_index=True)

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

    # build editable frame from current selection OR from session if already edited
    if st.session_state["goals_df"].empty:

        def _mk(selected, group):
            return pd.DataFrame(
                {
                    "var": pd.Series(selected, dtype="object"),
                    "group": pd.Series([group] * len(selected), dtype="object"),
                    "type": pd.Series(
                        [
                            (
                                "revenue"
                                if any(k in v.lower() for k in ("rev", "gmv"))
                                else "conversion"
                            )
                            for v in selected
                        ],
                        dtype="object",
                    ),
                }
            )

        goals_src = pd.concat(
            [_mk(primary_goals, "primary"), _mk(secondary_goals, "secondary")],
            ignore_index=True,
        )
    else:
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
    st.session_state["goals_df"] = goals_edit
    st.success("Goals updated.")

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

        def _infer_category(col: str, rules: dict[str, list[str]]) -> str:
            for cat, endings in rules.items():
                for suf in endings:
                    if col.lower().endswith(suf.lower()):
                        return cat
            return ""

        st.session_state["mapping_df"] = pd.DataFrame(
            {
                "var": pd.Series(all_cols, dtype="object"),
                "category": pd.Series(
                    [_infer_category(c, new_rules) for c in all_cols],
                    dtype="object",
                ),
                "custom_tags": pd.Series([""] * len(all_cols), dtype="object"),
            }
        ).astype("object")

# ---- Mapping editor (form) ----
allowed_categories = [
    "paid_media_spends",
    "paid_media_vars",
    "context_vars",
    "organic_vars",
    "factor_vars",
    "",
]
with st.form("mapping_form", clear_on_submit=False):
    # if still empty (first load), seed once using current rules
    if st.session_state["mapping_df"].empty:
        st.session_state["mapping_df"] = pd.DataFrame(
            {
                "var": pd.Series(all_cols, dtype="object"),
                "category": pd.Series([""] * len(all_cols), dtype="object"),
                "custom_tags": pd.Series([""] * len(all_cols), dtype="object"),
            }
        ).astype("object")

    mapping_src = (
        st.session_state["mapping_df"]
        .fillna("")
        .astype(
            {"var": "object", "category": "object", "custom_tags": "object"}
        )
    )
    mapping_edit = st.data_editor(
        mapping_src,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "var": st.column_config.TextColumn(
                "Column", disabled=True
            ),  # disable name edits to keep alignment with dataset
            "category": st.column_config.SelectboxColumn(
                "Category", options=allowed_categories
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
# Step 3) Save your mapping
# ──────────────────────────────────────────────────────────────
st.header("Step 3) Save your mapping")

goals_df = st.session_state["goals_df"]
mapping_df = st.session_state["mapping_df"]
auto_rules = st.session_state["auto_rules"]


def _by_cat(df: pd.DataFrame, cat: str) -> list[str]:
    return df.loc[df["category"] == cat, "var"].dropna().astype(str).tolist()


allowed_categories = [
    "paid_media_spends",
    "paid_media_vars",
    "context_vars",
    "organic_vars",
    "factor_vars",
]
by_cat = {cat: _by_cat(mapping_df, cat) for cat in allowed_categories}

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
    "mapping": by_cat,
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
