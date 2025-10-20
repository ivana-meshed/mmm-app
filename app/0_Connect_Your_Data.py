# 0_Connect_Your_Data.py
import streamlit as st
import pandas as pd
from app_shared import (
    require_login_and_domain,
    get_data_processor,
    _require_sf_session,
    effective_sql,
    run_sql,
    upload_to_gcs,
    GCS_BUCKET,
)
from google.cloud import storage
from datetime import datetime
import tempfile

st.set_page_config(page_title="Connect your Data", layout="wide")
require_login_and_domain()

# ── Basic session defaults used cross-pages
st.session_state.setdefault("gcs_bucket", GCS_BUCKET)
st.session_state.setdefault("country", "de")
st.session_state.setdefault("df_raw", pd.DataFrame())
st.session_state.setdefault("data_origin", "")
st.session_state.setdefault("picked_ts", "")
BUCKET = st.session_state["gcs_bucket"]


# --- Helpers (same as in your mapping page, trimmed here) ---
def _data_root(country: str) -> str:
    return f"datasets/{country.lower().strip()}"


def _data_blob(country: str, ts: str) -> str:
    return f"{_data_root(country)}/{ts}/raw.parquet"


def _latest_symlink_blob(country: str) -> str:
    return f"{_data_root(country)}/latest/raw.parquet"


@st.cache_data(show_spinner=False)
def _list_country_versions(bucket: str, country: str) -> list[str]:
    client = storage.Client()
    prefix = f"{_data_root(country)}/"
    blobs = client.list_blobs(bucket, prefix=prefix, delimiter=None)
    ts = set()
    for b in blobs:
        parts = b.name.split("/")
        if len(parts) >= 4 and parts[-1] == "raw.parquet":
            ts.add(parts[-2])
    return sorted(ts, reverse=True)


@st.cache_data(show_spinner=False)
def _download_parquet_from_gcs(gs_bucket: str, blob_path: str):
    client = storage.Client()
    blob = client.bucket(gs_bucket).blob(blob_path)
    import pandas as pd, tempfile

    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
        blob.download_to_filename(tmp.name)
        return pd.read_parquet(tmp.name)


# --- Country (ISO2) with GCS-first ordering ---
@st.cache_data(show_spinner=False)
def _iso2_countries_gcs_first(bucket: str) -> list[str]:
    try:
        import pycountry

        all_iso2 = sorted({c.alpha_2.lower() for c in pycountry.countries})
    except Exception:
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
            versions = _list_country_versions(bucket, code)
            (has_data if versions else no_data).append(code)
        except Exception:
            no_data.append(code)
    return has_data + no_data


st.title("Connect your Data")

countries = _iso2_countries_gcs_first(BUCKET)
initial_country_idx = (
    countries.index(st.session_state["country"])
    if st.session_state["country"] in countries
    else 0
)
st.selectbox(
    "Country (ISO2)",
    options=countries,
    index=initial_country_idx,
    key="country",
)

# “Source” options = Latest, <timestamps>, Snowflake
versions = _list_country_versions(BUCKET, st.session_state["country"])
source_options = ["Latest"] + versions + ["Snowflake"]
src_idx = (
    0
    if "source_choice" not in st.session_state
    else (
        source_options.index(st.session_state["source_choice"])
        if st.session_state["source_choice"] in source_options
        else 0
    )
)
st.selectbox(
    "Source", options=source_options, index=src_idx, key="source_choice"
)

# Snowflake inputs
st.session_state.setdefault("sf_table", "MMM_RAW")
st.session_state.setdefault("sf_sql", "")
st.session_state.setdefault("sf_country_field", "COUNTRY")
st.text_input("Table (DB.SCHEMA.TABLE)", key="sf_table")
st.text_area("Custom SQL (optional)", key="sf_sql")
st.text_input("Country field", key="sf_country_field")

c1, c2 = st.columns([1, 1.2])
with c1:
    load_clicked = st.button("Load", use_container_width=True)
with c2:
    refresh_clicked = st.button(
        "↻ Refresh GCS list", use_container_width=True, key="refresh_list"
    )

if refresh_clicked:
    _list_country_versions.clear()
    _download_parquet_from_gcs.clear()
    st.success("Refreshed GCS version list.")
    st.rerun()


def _load_from_snowflake(country_iso2: str):
    _require_sf_session()
    sql = (
        effective_sql(st.session_state["sf_table"], st.session_state["sf_sql"])
        or ""
    )
    if sql and not st.session_state["sf_sql"].strip():
        sql = f"{sql} WHERE {st.session_state['sf_country_field']} = '{country_iso2.upper()}'"
    return run_sql(sql) if sql else None


if load_clicked:
    df = None
    country = st.session_state["country"]
    choice = st.session_state["source_choice"]
    try:
        if choice == "Latest":
            try:
                df = _download_parquet_from_gcs(
                    BUCKET, _latest_symlink_blob(country)
                )
                st.session_state.update(
                    df_raw=df, data_origin="gcs_latest", picked_ts="latest"
                )
            except Exception:
                if versions:
                    ts = versions[0]
                    df = _download_parquet_from_gcs(
                        BUCKET, _data_blob(country, ts)
                    )
                    st.session_state.update(
                        df_raw=df, data_origin="gcs_timestamp", picked_ts=ts
                    )
                else:
                    df = _load_from_snowflake(country)
                    if df is not None:
                        st.session_state.update(
                            df_raw=df, data_origin="snowflake", picked_ts=""
                        )
        elif choice in versions:
            df = _download_parquet_from_gcs(BUCKET, _data_blob(country, choice))
            st.session_state.update(
                df_raw=df, data_origin="gcs_timestamp", picked_ts=choice
            )
        else:  # Snowflake
            df = _load_from_snowflake(country)
            if df is not None:
                st.session_state.update(
                    df_raw=df, data_origin="snowflake", picked_ts=""
                )
    except Exception as e:
        st.error(f"Load failed: {e}")

if not st.session_state["df_raw"].empty:
    st.success(f"Loaded {len(st.session_state['df_raw']):,} rows.")
    st.dataframe(
        st.session_state["df_raw"].head(20),
        use_container_width=True,
        hide_index=True,
    )

    # Allow saving raw snapshot to GCS
    def _save_current_raw():
        df = st.session_state["df_raw"]
        if df.empty:
            st.warning("No dataset loaded.")
            return
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        with tempfile.NamedTemporaryFile(
            suffix=".parquet", delete=False
        ) as tmp:
            df.to_parquet(tmp.name, index=False)
            path = _data_blob(st.session_state["country"], ts)
            upload_to_gcs(BUCKET, tmp.name, path)
            upload_to_gcs(
                BUCKET,
                tmp.name,
                _latest_symlink_blob(st.session_state["country"]),
            )
        st.success(f"Saved raw snapshot → gs://{BUCKET}/{path}")

    st.button(
        "💾 Save this dataset to GCS (as new version)",
        on_click=_save_current_raw,
    )

# Next: go to Map Your Data
st.divider()
try:
    st.page_link(
        "pages/1_Map_Your_Data.py", label="Next → Map Your Data", icon="➡️"
    )
except Exception:
    st.write("Open the **Map Your Data** page from the sidebar.")
