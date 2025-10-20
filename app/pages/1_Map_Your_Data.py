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
    _require_sf_session,
    ensure_sf_conn,
    upload_to_gcs,
    effective_sql,
    GCS_BUCKET,
    PROJECT_ID,
    require_login_and_domain,
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


def _apply_metadata_to_current_df(meta: dict, current_cols: list[str]) -> None:
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

    # mapping → build a full mapping_df for current columns
    meta_map = meta.get("mapping", {}) or {}
    # Flatten mapping dict: {cat: [vars]}
    var_to_cat = {}
    for cat, vars_ in meta_map.items():
        for v in vars_ or []:
            var_to_cat[str(v)] = cat

    rows = []
    for c in current_cols:
        cat = var_to_cat.get(c)
        if not cat:
            # fallback to rules for new cols
            cat = _infer_category(c, st.session_state["auto_rules"])
        rows.append({"var": c, "category": cat or "", "custom_tags": ""})
    st.session_state["mapping_df"] = pd.DataFrame(rows).astype("object")


# --- Country options: ISO2 with GCS-first ordering ---
@st.cache_data(show_spinner=False)
def _iso2_countries_gcs_first(bucket: str) -> list[str]:
    try:
        import pycountry

        all_iso2 = sorted({c.alpha_2.lower() for c in pycountry.countries})
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

# ──────────────────────────────────────────────────────────────
# Step 1) Choose your dataset
# ──────────────────────────────────────────────────────────────
st.header("Step 1) Choose your dataset")
# ──────────────────────────────────────────────────────────────

require_login_and_domain()

# sensible defaults so we can read these anywhere
st.session_state.setdefault("sf_table", "MMM_RAW")
st.session_state.setdefault("sf_sql", "")
st.session_state.setdefault("sf_country_field", "COUNTRY")
st.session_state.setdefault("source_mode", "Latest (GCS)")


@_fragment()
def step1_loader():
    country = st.session_state.get("country", "de")

    # Get available GCS versions for the chosen country
    versions = _list_country_versions_cached(
        BUCKET, country
    )  # e.g. ["20250107_101500", "20241231_235959"]
    source_options = ["Latest"] + versions + ["Snowflake"]

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
                    df_raw=df, data_origin="gcs_latest", picked_ts="latest"
                )
            except Exception:
                if versions:
                    fallback_ts = versions[0]
                    st.info(
                        f"‘latest’ not found — loading most recent saved version: {fallback_ts}."
                    )
                    df = _download_parquet_from_gcs_cached(
                        BUCKET, _data_blob(country, fallback_ts)
                    )
                    st.session_state.update(
                        df_raw=df,
                        data_origin="gcs_timestamp",
                        picked_ts=fallback_ts,
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
                            df_raw=df, data_origin="snowflake", picked_ts=""
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
                df_raw=df, data_origin="gcs_timestamp", picked_ts=choice
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
                sql = f"{sql} WHERE {st.session_state['sf_country_field']} = '{country.upper()}'"
            if sql:
                df = _load_from_snowflake_cached(sql)
                st.session_state.update(
                    df_raw=df, data_origin="snowflake", picked_ts=""
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


with st.expander(
    "📥 Load saved metadata & apply to current dataset", expanded=False
):
    lc1, lc2, lc3 = st.columns([1.2, 1, 1])
    load_country = lc1.text_input(
        "Country (metadata source)", value=st.session_state["country"]
    )
    # list available versions for chosen country
    meta_versions = _list_country_versions_cached(
        BUCKET, load_country
    )  # reuse same listing under /datasets/
    # We also allow 'latest' for metadata
    version_opts = ["latest"] + meta_versions
    picked_meta_ts = lc2.selectbox("Version", options=version_opts, index=0)
    if lc3.button("Load & apply"):
        try:
            meta_blob = (
                _meta_latest_blob(load_country)
                if picked_meta_ts == "latest"
                else _meta_blob(load_country, picked_meta_ts)
            )
            meta = _download_json_from_gcs(BUCKET, meta_blob)
            _apply_metadata_to_current_df(meta, all_cols)
            st.success(f"Applied metadata from gs://{BUCKET}/{meta_blob}")
        except Exception as e:
            st.error(f"Failed to load metadata: {e}")

# ✅ if still empty (first load), seed using current rules (outside the form)
if st.session_state["mapping_df"].empty:
    st.session_state["mapping_df"] = pd.DataFrame(
        {
            "var": pd.Series(all_cols, dtype="object"),
            "category": pd.Series(
                [
                    _infer_category(c, st.session_state["auto_rules"])
                    for c in all_cols
                ],
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

# ✅ mapping_df is already seeded above if empty

# --- Re-apply auto-tag rules on demand (outside any form) ---
rt1, rt2 = st.columns([1, 1])

# Only fill previously-untagged rows (preserves manual edits)
if rt1.button("🔁 Auto-tag UNTAGGED columns", key="retag_missing"):
    m = st.session_state["mapping_df"].copy()
    inferred = {
        c: _infer_category(c, st.session_state["auto_rules"]) for c in all_cols
    }
    m["category"] = m.apply(
        lambda r: (
            r["category"]
            if str(r["category"]).strip()
            else inferred.get(r["var"], "")
        ),
        axis=1,
    )
    st.session_state["mapping_df"] = m.astype("object")
    st.success("Filled categories for previously untagged columns.")

# Overwrite everything from current rules (discard manual edits)
if rt2.button("♻️ Re-apply to ALL columns", key="retag_all"):
    st.session_state["mapping_df"] = pd.DataFrame(
        {
            "var": pd.Series(all_cols, dtype="object"),
            "category": pd.Series(
                [
                    _infer_category(c, st.session_state["auto_rules"])
                    for c in all_cols
                ],
                dtype="object",
            ),
            "custom_tags": pd.Series([""] * len(all_cols), dtype="object"),
        }
    ).astype("object")
    st.warning(
        "Re-applied rules to ALL columns (manual categories were overwritten)."
    )


with st.form("mapping_form_main", clear_on_submit=False):
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
            "var": st.column_config.TextColumn("Column", disabled=True),
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
