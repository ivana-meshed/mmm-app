import os
from typing import Optional

import pandas as pd
import streamlit as st
from app_shared import (
    GCS_BUCKET,
    PROJECT_ID,
    _connect_snowflake,
    require_login_and_domain,
    run_sql,
    sync_session_state_keys,
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
        _ = serialization.load_pem_private_key(
            pem.encode("utf-8"), password=None, backend=default_backend()
        )
        # Save the PEM format to Secret Manager
        upsert_secret(PERSISTENT_KEY_SECRET_ID, pem.encode("utf-8"), PROJECT_ID)
        return True
    except Exception as e:
        st.error(f"Failed to save key to Secret Manager: {e}")
        return False


st.title("Connect your Data Source")

# Sync session state across all pages to maintain selections
sync_session_state_keys()

# Data source selector
data_source_type = st.radio(
    "Select Data Source Type:",
    options=["Snowflake", "BigQuery", "CSV Upload"],
    index=0,
    horizontal=True,
    help=(
        "Choose how you want to connect your data. Snowflake and "
        "BigQuery allow querying cloud databases, while CSV Upload "
        "lets you upload a local file."
    ),
)
st.session_state["data_source_type"] = data_source_type

st.divider()

# ============= SNOWFLAKE CONNECTION =============
if data_source_type == "Snowflake":
    st.subheader("Connect to Snowflake")

    # Check if there's a persisted key available
    persisted_key_available = False
    if not st.session_state.get("_checked_persisted_key"):
        persisted_key = load_persisted_key()
        if persisted_key:
            st.session_state["_sf_private_key_bytes"] = persisted_key
            persisted_key_available = True
            st.info(
                "✅ Found a previously saved private key. You can use it to connect without uploading a new key. "
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

            st.markdown("**Upload Private key (PEM)**")
            sf_pk_pem = st.text_area(
                "Private Key (PEM format)",
                value="",
                placeholder="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"
                + ("\n(Using saved key)" if persisted_key_available else ""),
                help="Paste a new key or continue with your saved key.",
                height=150,
            )
            sf_pk_file = st.file_uploader(
                "…or upload a .pem file instead", type=["pem", "key", "p8"]
            )

            # Add checkbox to persist the key
            save_key = st.checkbox(
                "💾 Save this key for future sessions (Recommended)",
                value=False,
                help="Securely store this key so you won’t need to upload it again in future sessions (Google Secret Manager).",
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
                "Table Name",
                value=st.session_state.get("sf_preview_table", "MMM_RAW"),
                help="Enter a table name (e.g. MMM_daily) or a full-qualified path (DB.SCHEMA.TABLE).",
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
                    pem.encode("utf-8"),
                    password=None,
                    backend=default_backend(),
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
                            "✅ Your private key has been saved securely for future sessions (Secret Manager)."
                        )
                    else:
                        st.warning(
                            "⚠️ Failed to save key to Secret Manager, but we'll continue with the connection attempt."
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
            st.success(
                f"Connected to Snowflake as `{sf_user}` on `{sf_account}`."
            )
            st.session_state["sf_preview_table"] = preview_table
            if (preview_table or "").strip():
                try:
                    df_prev = run_sql(f"SELECT * FROM {preview_table} LIMIT 20")
                    st.caption(
                        f"Preview: Showing first 20 rows of `{preview_table}`"
                    )
                    st.dataframe(df_prev, width="stretch", hide_index=True)
                except Exception as e:
                    st.warning(
                        f"Could not preview table `{preview_table}`: {e}"
                    )

        except Exception as e:
            st.session_state["sf_connected"] = False
            st.error(f"Connection could not be established. Error: {e}")

    if st.session_state.sf_connected:
        with st.container(border=True):
            st.markdown("**Status:** ✅ Connected")
            c1, c2, c3 = st.columns(3)
            c1.write(
                f"**Warehouse:** `{st.session_state.sf_params.get('warehouse', '')}`"
            )
            c2.write(
                f"**Database:** `{st.session_state.sf_params.get('database', '')}`"
            )
            c3.write(
                f"**Schema:** `{st.session_state.sf_params.get('schema', '')}`"
            )
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
                    name = f"projects/{PROJECT_ID}/secrets/{PERSISTENT_KEY_SECRET_ID}"
                    try:
                        client.delete_secret(request={"name": name})
                        st.session_state.pop("_sf_private_key_bytes", None)
                        st.session_state["_checked_persisted_key"] = False
                        st.success(
                            "✅ Your saved private key has been removed from Secret Manager."
                        )
                    except Exception as e:
                        st.warning(f"Could not delete saved key: {e}")
                except Exception as e:
                    st.error(f"Failed to clear saved key: {e}")

    else:
        st.info(
            "You're not connected yet. Enter your Snowflake details above and click **Connect**."
        )


# ============= BIGQUERY CONNECTION =============
elif data_source_type == "BigQuery":
    st.subheader("Connect to BigQuery")

    # Secret ID for persistent BigQuery credentials storage
    BQ_PERSISTENT_CREDS_SECRET_ID = os.getenv(
        "BQ_PERSISTENT_CREDS_SECRET", "bq-credentials-persistent"
    )

    # Check if there's a persisted credentials available
    persisted_creds_available = False
    if not st.session_state.get("_checked_persisted_bq_creds"):
        try:
            creds_json = access_secret(
                BQ_PERSISTENT_CREDS_SECRET_ID, PROJECT_ID
            )
            if creds_json:
                st.session_state["_bq_credentials_json"] = creds_json.decode(
                    "utf-8"
                )
                persisted_creds_available = True
                st.info(
                    "✅ Found previously saved credentials. You can use "
                    "them to connect without uploading new credentials."
                )
        except Exception:
            pass
        st.session_state["_checked_persisted_bq_creds"] = True
    else:
        persisted_creds_available = (
            st.session_state.get("_bq_credentials_json") is not None
        )

    with st.form("bq_connect_form", clear_on_submit=False):
        bq_project_id = st.text_input(
            "Project ID",
            value=st.session_state.get("bq_project_id", "")
            or os.getenv("BQ_PROJECT_ID", ""),
            help="Your Google Cloud Project ID",
        )

        st.markdown("**Service Account Credentials**")
        bq_creds_json = st.text_area(
            "Service Account JSON",
            value="",
            placeholder='{\n  "type": "service_account",\n  "project_id": "your-project",\n  ...\n}'
            + (
                "\n(Using saved credentials)"
                if persisted_creds_available
                else ""
            ),
            help="Paste your service account JSON or continue with saved credentials.",
            height=150,
        )
        bq_creds_file = st.file_uploader(
            "…or upload a JSON key file instead", type=["json"]
        )

        # Add checkbox to persist credentials
        save_creds = st.checkbox(
            "💾 Save credentials for future sessions (Recommended)",
            value=False,
            help="Securely store credentials so you won't need to upload them again (Google Secret Manager).",
        )

        # Table for preview
        preview_table_bq = st.text_input(
            "Table ID for Preview (optional)",
            value=st.session_state.get("bq_preview_table", ""),
            help="Enter fully qualified table ID: project.dataset.table",
        )

        submitted_bq = st.form_submit_button("🔌 Connect")

    if submitted_bq:
        try:
            creds_json = None

            # Priority 1: newly uploaded file
            if bq_creds_file is not None:
                creds_json = bq_creds_file.read().decode("utf-8")
            # Priority 2: pasted credentials
            elif (bq_creds_json or "").strip():
                creds_json = bq_creds_json.strip()
            # Priority 3: use existing persisted credentials
            elif st.session_state.get("_bq_credentials_json"):
                creds_json = st.session_state["_bq_credentials_json"]
            else:
                raise ValueError(
                    "Provide BigQuery service account credentials (JSON) or ensure saved credentials exist."
                )

            # Test connection by creating a client
            from utils.bigquery_connector import create_bigquery_client

            bq_client = create_bigquery_client(
                bq_project_id, credentials_json=creds_json
            )

            # Save to Secret Manager if requested and we have new credentials
            if save_creds and (bq_creds_file or bq_creds_json.strip()):
                try:
                    upsert_secret(
                        BQ_PERSISTENT_CREDS_SECRET_ID,
                        creds_json.encode("utf-8"),
                        PROJECT_ID,
                    )
                    st.success(
                        "✅ Your BigQuery credentials have been saved securely for future sessions."
                    )
                except Exception as e:
                    st.warning(
                        f"⚠️ Failed to save credentials to Secret Manager: {e}"
                    )

            # Store connection info in session state
            st.session_state["bq_client"] = bq_client
            st.session_state["bq_project_id"] = bq_project_id
            st.session_state["_bq_credentials_json"] = creds_json
            st.session_state["bq_connected"] = True
            st.session_state["data_connected"] = True
            st.success(
                f"Connected to BigQuery project `{bq_project_id}` successfully."
            )

            # Preview table if provided
            if (preview_table_bq or "").strip():
                try:
                    from utils.bigquery_connector import get_table_preview

                    df_prev = get_table_preview(bq_client, preview_table_bq)
                    st.caption(
                        f"Preview: Showing first 20 rows of `{preview_table_bq}`"
                    )
                    st.dataframe(df_prev, width="stretch", hide_index=True)
                    st.session_state["bq_preview_table"] = preview_table_bq
                except Exception as e:
                    st.warning(
                        f"Could not preview table `{preview_table_bq}`: {e}"
                    )

        except Exception as e:
            st.session_state["bq_connected"] = False
            st.error(f"Connection could not be established. Error: {e}")

    if st.session_state.get("bq_connected"):
        with st.container(border=True):
            st.markdown("**Status:** ✅ Connected to BigQuery")
            st.write(
                f"**Project ID:** `{st.session_state.get('bq_project_id', '')}`"
            )
            dc1, dc2, dc3 = st.columns(3)
            if dc2.button("⏏️ Disconnect", key="bq_disconnect"):
                st.session_state["bq_client"] = None
                st.session_state["bq_connected"] = False
                st.session_state["data_connected"] = False
                st.session_state.pop("_bq_credentials_json", None)
                st.success("Disconnected from BigQuery.")
            if dc3.button("🗑️ Clear Saved Credentials", key="bq_clear"):
                try:
                    from google.cloud import secretmanager

                    client = secretmanager.SecretManagerServiceClient()
                    name = f"projects/{PROJECT_ID}/secrets/{BQ_PERSISTENT_CREDS_SECRET_ID}"
                    try:
                        client.delete_secret(request={"name": name})
                        st.session_state.pop("_bq_credentials_json", None)
                        st.session_state["_checked_persisted_bq_creds"] = False
                        st.success(
                            "✅ Your saved BigQuery credentials have been removed."
                        )
                    except Exception as e:
                        st.warning(f"Could not delete saved credentials: {e}")
                except Exception as e:
                    st.error(f"Failed to clear saved credentials: {e}")
    else:
        st.info(
            "You're not connected yet. Enter your BigQuery details above and click **Connect**."
        )

# ============= CSV UPLOAD =============
elif data_source_type == "CSV Upload":
    st.subheader("Upload CSV File")

    st.markdown(
        """
    Upload a CSV file containing your marketing mix data. The file should include:
    - Date column
    - Dependent variable (e.g., revenue, conversions)
    - Media spend columns
    - Media impression/activity columns
    - Context variables (optional)
    - Organic variables (optional)
    """
    )

    uploaded_file = st.file_uploader(
        "Choose a CSV file",
        type=["csv"],
        help="Upload your marketing mix modeling data as a CSV file",
    )

    if uploaded_file is not None:
        try:
            # Read the CSV file
            df = pd.read_csv(uploaded_file)

            st.success(
                f"✅ File uploaded successfully! Shape: {df.shape[0]} rows × {df.shape[1]} columns"
            )

            # Show preview
            st.subheader("Data Preview")
            st.dataframe(df.head(20), width="stretch", hide_index=True)

            # Store in session state
            st.session_state["csv_data"] = df
            st.session_state["csv_connected"] = True
            st.session_state["data_connected"] = True
            st.session_state["csv_filename"] = uploaded_file.name

            # Show data info
            with st.expander("📊 Data Summary", expanded=False):
                col1, col2 = st.columns(2)
                with col1:
                    st.write("**Column Names:**")
                    for col in df.columns:
                        st.write(f"- {col}")
                with col2:
                    st.write("**Data Types:**")
                    st.dataframe(df.dtypes.to_frame("Type"), width="stretch")

        except Exception as e:
            st.error(f"Error reading CSV file: {e}")
            st.session_state["csv_connected"] = False
            st.session_state["data_connected"] = False

    if st.session_state.get("csv_connected"):
        with st.container(border=True):
            st.markdown("**Status:** ✅ CSV Data Loaded")
            st.write(
                f"**File:** `{st.session_state.get('csv_filename', 'unknown')}`"
            )
            st.write(
                f"**Shape:** {st.session_state['csv_data'].shape[0]} rows × {st.session_state['csv_data'].shape[1]} columns"
            )
            if st.button("🔄 Upload New File", key="csv_new"):
                st.session_state["csv_data"] = None
                st.session_state["csv_connected"] = False
                st.session_state["data_connected"] = False
                st.rerun()
    else:
        st.info(
            "Upload a CSV file above to begin. Make sure it includes all necessary columns for your MMM analysis."
        )

# ============= Outputs Configuration =============
st.divider()
st.subheader("Output Location")

with st.expander("📤 Output Setting", expanded=False):
    gcs_bucket = st.text_input(
        "Google Cloud Storage bucket for outputs:",
        value=st.session_state.get("gcs_bucket", GCS_BUCKET),
        help="All outputs will be stored in this bucket.",
    )
    st.session_state["gcs_bucket"] = gcs_bucket

# ============= Navigation =============

# Allow navigation once any data source is connected
st.divider()
try:
    data_connected = (
        st.session_state.get("sf_connected")
        or st.session_state.get("bq_connected")
        or st.session_state.get("csv_connected")
    )

    if data_connected:
        if st.button("Next → Map Your Data"):
            import streamlit as stlib

            stlib.switch_page("nav/Map_Data.py")
    else:
        st.info(
            "Connect to a data source above (Snowflake, BigQuery, or upload CSV) to enable Next."
        )
except Exception:
    st.page_link("nav/Map_Data.py", label="Next → Map Your Data", icon="➡️")
