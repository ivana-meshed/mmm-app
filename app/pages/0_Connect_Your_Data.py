# pages/0_Connect_Your_Data.py
import os, io, json, tempfile
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st
from app_split_helpers import *  # bring in all helper functions/constants

from app_shared import (
    require_login_and_domain,
    get_data_processor,
    run_sql,
    _require_sf_session,
    GCS_BUCKET,
    _connect_snowflake,
)


st.set_page_config(
    page_title="Connect your Data", page_icon="🧩", layout="wide"
)

require_login_and_domain()

st.title("Connect your Data")

st.subheader("Connect to Snowflake (persists for this session)")
with st.form("sf_connect_form", clear_on_submit=False):
    c1, c2 = st.columns(2)
    with c1:
        sf_user = st.text_input(
            "User",
            value=(st.session_state.sf_params or {}).get("user", "")
            or os.getenv("SF_USER"),
        )
        sf_account = st.text_input(
            "Account",
            value=(st.session_state.sf_params or {}).get("account", "")
            or os.getenv("SF_ACCOUNT"),
        )
        sf_wh = st.text_input(
            "Warehouse",
            value=(st.session_state.sf_params or {}).get("warehouse", "")
            or os.getenv("SF_WAREHOUSE"),
        )

        st.markdown(
            "**Private key (PEM)** — paste or upload one of the two below:"
        )
        sf_pk_pem = st.text_area(
            "Paste PEM key",
            value="",
            placeholder="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----",
            help="This stays only in your browser session. Not stored on server.",
            height=120,
        )
        sf_pk_file = st.file_uploader(
            "…or upload a .pem file", type=["pem", "key", "p8"]
        )

    with c2:
        sf_schema = st.text_input(
            "Schema",
            value=(st.session_state.sf_params or {}).get("schema", "")
            or os.getenv("SF_SCHEMA"),
        )
        sf_role = st.text_input(
            "Role",
            value=(st.session_state.sf_params or {}).get("role", "")
            or os.getenv("SF_ROLE"),
        )
        sf_db = st.text_input(
            "Database",
            value=(st.session_state.sf_params or {}).get("database", "")
            or os.getenv("SF_DATABASE"),
        )

        # ✅ NEW: default MMM_RAW; allow fully-qualified or relative to DB/SCHEMA above
        preview_table = st.text_input(
            "Preview table after connect",
            value=st.session_state.get("sf_preview_table", "MMM_RAW"),
            help="Use DB.SCHEMA.TABLE or a table in the selected Database/Schema.",
        )

    submitted = st.form_submit_button("🔌 Connect")

if submitted:
    try:
        # choose source: uploaded file wins if provided
        if sf_pk_file is not None:
            pem = sf_pk_file.read().decode("utf-8", errors="replace")
        else:
            pem = (sf_pk_pem or "").strip()
        if not pem:
            raise ValueError("Provide a Snowflake private key (PEM).")

        # Convert PEM -> PKCS#8 DER bytes (what the Snowflake connector needs)
        key = serialization.load_pem_private_key(
            pem.encode("utf-8"), password=None, backend=default_backend()
        )
        pk_der = key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        # Build connection using the provided key (no Secret Manager)
        conn = _connect_snowflake(
            user=sf_user,
            account=sf_account,
            warehouse=sf_wh,
            database=sf_db,
            schema=sf_schema,
            role=sf_role,
            private_key=pk_der,
        )

        # Store non-sensitive params and keep key bytes only in-session
        st.session_state["sf_params"] = dict(
            user=sf_user,
            account=sf_account,
            warehouse=sf_wh,
            database=sf_db,
            schema=sf_schema,
            role=sf_role,
        )
        st.session_state["_sf_private_key_bytes"] = pk_der  # <— in memory
        st.session_state["sf_conn"] = conn
        st.session_state["sf_connected"] = True
        st.success(f"Connected to Snowflake as `{sf_user}` on `{sf_account}`.")
        st.session_state["sf_preview_table"] = preview_table
        if preview_table.strip():
            try:
                df_prev = run_sql(f"SELECT * FROM {preview_table} LIMIT 20")
                st.caption(f"Preview: first 20 rows of `{preview_table}`")
                st.dataframe(df_prev, width="stretch", hide_index=True)
            except Exception as e:
                st.warning(f"Could not preview table `{preview_table}`: {e}")

    except Exception as e:
        st.session_state["sf_connected"] = False
        st.error(f"Connection failed: {e}")

if st.session_state.sf_connected:
    with st.container(border=True):
        st.markdown("**Status:** ✅ Connected")
        c1, c2, c3 = st.columns(3)
        c1.write(
            f"**Warehouse:** `{st.session_state.sf_params.get('warehouse','')}`"
        )
        c2.write(
            f"**Database:** `{st.session_state.sf_params.get('database','')}`"
        )
        c3.write(f"**Schema:** `{st.session_state.sf_params.get('schema','')}`")
        dc1, dc2 = st.columns(2)
        if dc1.button("🔄 Reconnect"):
            try:
                ensure_sf_conn()
                st.success("Reconnected.")
            except Exception as e:
                st.error(f"Reconnect failed: {e}")
        if dc2.button("⏏️ Disconnect"):
            try:
                conn = st.session_state.get("sf_conn")
                if conn:
                    conn.close()
            finally:
                st.session_state["sf_conn"] = None
                st.session_state["sf_connected"] = False
                st.session_state.pop("_sf_private_key_bytes", None)  # <—
                st.success("Disconnected.")

else:
    st.info("Not connected. Fill the form above and click **Connect**.")

# ============= TAB 2: Configure & Train =============

# Once Snowflake is connected, allow navigation to mapping
st.divider()
col1, col2 = st.columns([1, 5])
with col1:
    try:
        if st.session_state.get("sf_connected"):
            if st.button("Next → Map Your Data", use_container_width=True):
                import streamlit as stlib

                stlib.switch_page("pages/1_Map_Your_Data.py")
        else:
            st.info("Fill in your Snowflake credentials above to enable Next.")
    except Exception:
        st.page_link(
            "pages/1_Map_Your_Data.py", label="Next → Map Your Data", icon="➡️"
        )
