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
    _connect_snowflake,
    _require_sf_session,
    get_data_processor,
    require_login_and_domain,
    run_sql,
)
from app_split_helpers import *  # bring in all helper functions/constants
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from gcp_secrets import access_secret, upsert_secret

require_login_and_domain()
ensure_session_defaults()

# Secret ID for persistent private key storage
PERSISTENT_KEY_SECRET_ID = os.getenv(
    "SF_PERSISTENT_KEY_SECRET", "sf-private-key-persistent"
)


def load_persisted_key() -> Optional[bytes]:
    """Load the persisted private key from Secret Manager if it exists."""
    try:
        pem_bytes = access_secret(PERSISTENT_KEY_SECRET_ID, PROJECT_ID)
        if pem_bytes:
            # Convert PEM -> PKCS#8 DER bytes
            key = serialization.load_pem_private_key(
                pem_bytes, password=None, backend=default_backend()
            )
            return key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
    except Exception as e:
        st.session_state.setdefault("_key_load_error", str(e))
    return None


def save_persisted_key(pem: str) -> bool:
    """Save the private key to Secret Manager for persistence."""
    try:
        # Validate the key first by loading it
        key = serialization.load_pem_private_key(
            pem.encode("utf-8"), password=None, backend=default_backend()
        )
        # Save the PEM format to Secret Manager
        upsert_secret(PERSISTENT_KEY_SECRET_ID, pem.encode("utf-8"), PROJECT_ID)
        return True
    except Exception as e:
        st.error(f"Failed to save key to Secret Manager: {e}")
        return False


st.title("Connect your Data")

st.subheader("Connect to Snowflake")

# Check if there's a persisted key available
persisted_key_available = False
if not st.session_state.get("_checked_persisted_key"):
    persisted_key = load_persisted_key()
    if persisted_key:
        st.session_state["_sf_private_key_bytes"] = persisted_key
        persisted_key_available = True
        st.info(
            "✅ Found a previously saved private key. You can connect without uploading a new one."
        )
    st.session_state["_checked_persisted_key"] = True
else:
    persisted_key_available = (
        st.session_state.get("_sf_private_key_bytes") is not None
    )

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
            "**Private key (PEM)** — paste or upload (optional if key is already saved):"
        )
        sf_pk_pem = st.text_area(
            "Paste PEM key",
            value="",
            placeholder="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"
            + ("\n(Using saved key)" if persisted_key_available else ""),
            help="Upload a new key or use the previously saved one.",
            height=120,
        )
        sf_pk_file = st.file_uploader(
            "…or upload a .pem file", type=["pem", "key", "p8"]
        )

        # Add checkbox to persist the key
        save_key = st.checkbox(
            "💾 Save this key for future sessions",
            value=False,
            help="Store the private key in Google Secret Manager so you don't have to upload it every time.",
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
        # Determine which key to use: new upload/paste OR existing persisted key
        pem = None
        pk_der = None

        # Priority 1: newly uploaded file
        if sf_pk_file is not None:
            pem = sf_pk_file.read().decode("utf-8", errors="replace")
        # Priority 2: pasted key
        elif (sf_pk_pem or "").strip():
            pem = sf_pk_pem.strip()
        # Priority 3: use existing persisted key if available
        elif st.session_state.get("_sf_private_key_bytes"):
            pk_der = st.session_state["_sf_private_key_bytes"]
        else:
            raise ValueError(
                "Provide a Snowflake private key (PEM) or ensure a saved key exists."
            )

        # If we have a PEM string, convert it to DER
        if pem:
            key = serialization.load_pem_private_key(
                pem.encode("utf-8"), password=None, backend=default_backend()
            )
            pk_der = key.private_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )

            # Save to Secret Manager if requested
            if save_key:
                if save_persisted_key(pem):
                    st.success(
                        "✅ Private key saved to Secret Manager for future use."
                    )
                else:
                    st.warning(
                        "⚠️ Failed to save key to Secret Manager, but connection will proceed."
                    )

        # Build connection using the key
        conn = _connect_snowflake(
            user=sf_user,
            account=sf_account,
            warehouse=sf_wh,
            database=sf_db,
            schema=sf_schema,
            role=sf_role,
            private_key=pk_der,
        )

        # Store non-sensitive params and keep key bytes in-session
        st.session_state["sf_params"] = dict(
            user=sf_user,
            account=sf_account,
            warehouse=sf_wh,
            database=sf_db,
            schema=sf_schema,
            role=sf_role,
        )
        st.session_state["_sf_private_key_bytes"] = pk_der
        st.session_state["sf_conn"] = conn
        st.session_state["sf_connected"] = True
        st.success(f"Connected to Snowflake as `{sf_user}` on `{sf_account}`.")
        st.session_state["sf_preview_table"] = preview_table
        if (preview_table or "").strip():
            try:
                df_prev = run_sql(f"SELECT * FROM {preview_table} LIMIT 20")
                st.caption(f"Preview: first 20 rows of `{preview_table}`")
                st.dataframe(df_prev, use_container_width=True, hide_index=True)
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
        dc1, dc2, dc3 = st.columns(3)
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
                st.session_state.pop("_sf_private_key_bytes", None)
                st.success("Disconnected.")
        if dc3.button("🗑️ Clear Saved Key"):
            try:
                # Delete the secret from Secret Manager
                from google.cloud import secretmanager

                client = secretmanager.SecretManagerServiceClient()
                name = (
                    f"projects/{PROJECT_ID}/secrets/{PERSISTENT_KEY_SECRET_ID}"
                )
                try:
                    client.delete_secret(request={"name": name})
                    st.session_state.pop("_sf_private_key_bytes", None)
                    st.session_state["_checked_persisted_key"] = False
                    st.success(
                        "✅ Saved private key deleted from Secret Manager."
                    )
                except Exception as e:
                    st.warning(f"Could not delete saved key: {e}")
            except Exception as e:
                st.error(f"Failed to clear saved key: {e}")

else:
    st.info("Not connected. Fill the form above and click **Connect**.")

# ============= Outputs Configuration =============
st.divider()
st.subheader("Outputs Configuration")

with st.expander("📤 Outputs", expanded=False):
    gcs_bucket = st.text_input(
        "GCS bucket for outputs",
        value=st.session_state.get("gcs_bucket", GCS_BUCKET),
        help="Google Cloud Storage bucket where training outputs will be stored",
    )
    st.session_state["gcs_bucket"] = gcs_bucket

    ann_file = st.file_uploader(
        "Optional: enriched_annotations.csv",
        type=["csv"],
        help="Upload an annotations file to enrich your model training",
    )
    # Store annotation file in session state if uploaded
    if ann_file is not None:
        st.session_state["annotations_file"] = ann_file
        st.success(f"Annotations file '{ann_file.name}' uploaded successfully.")

    if st.session_state.get("annotations_file") is not None:
        st.info(
            f"Current annotations file: {st.session_state['annotations_file'].name}"
        )

# ============= Navigation =============

# Once Snowflake is connected, allow navigation to mapping
st.divider()
try:
    if st.session_state.get("sf_connected"):
        if st.button("Next → Map Your Data"):
            import streamlit as stlib

            stlib.switch_page("pages/1_Map_Data.py")
    else:
        st.info("Fill in your Snowflake credentials above to enable Next.")
except Exception:
    st.page_link("pages/1_Map_Data.py", label="Next → Map Your Data", icon="➡️")
