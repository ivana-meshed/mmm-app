"""
Snowflake connection and query utilities.

Provides secure connection management with support for:
- Key-pair authentication via Secret Manager
- Password-based authentication (fallback)
- Connection pooling and keepalive
- Query execution with pandas integration
"""

import base64
import logging
from typing import Optional

import pandas as pd
import snowflake.connector as sf
from config import settings
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from google.cloud import secretmanager

logger = logging.getLogger(__name__)


def load_private_key_from_secret_manager(secret_id: str) -> bytes:
    """
    Load RSA private key from Google Secret Manager and convert to PKCS#8 DER format.

    Args:
        secret_id: Secret Manager secret ID or full resource path

    Returns:
        Private key in PKCS#8 DER format (bytes)

    Raises:
        RuntimeError: If secret cannot be loaded or key cannot be parsed
    """
    client = secretmanager.SecretManagerServiceClient()

    # Build full resource name if just ID provided
    if secret_id.startswith("projects/"):
        name = f"{secret_id}/versions/latest"
    else:
        if not settings.PROJECT_ID:
            raise RuntimeError("PROJECT_ID environment variable is not set")
        name = f"projects/{settings.PROJECT_ID}/secrets/{secret_id}/versions/latest"

    try:
        response = client.access_secret_version(name=name)
        payload = response.payload.data
    except Exception as e:
        logger.error(f"Failed to fetch private key from Secret Manager: {e}")
        raise RuntimeError(f"Could not load private key: {e}")

    # Try to parse as PEM first
    try:
        pem_str = payload.decode("utf-8")
        key = serialization.load_pem_private_key(
            pem_str.encode("utf-8"),
            password=None,
            backend=default_backend(),
        )
        # Convert to PKCS#8 DER format required by Snowflake
        return key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    except Exception:
        # If not PEM, assume it's already DER
        return bytes(payload)


def create_snowflake_connection(
    user: str,
    account: str,
    warehouse: str,
    database: str,
    schema: str,
    role: Optional[str] = None,
    password: Optional[str] = None,
    private_key: Optional[bytes] = None,
) -> sf.SnowflakeConnection:
    """
    Create a Snowflake connection with keepalive enabled.

    Supports both key-pair and password authentication.
    Key-pair authentication is preferred for security.

    Args:
        user: Snowflake username
        account: Snowflake account identifier
        warehouse: Warehouse name
        database: Database name
        schema: Schema name
        role: Optional role name
        password: Optional password (for password auth)
        private_key: Optional private key bytes (for key-pair auth)

    Returns:
        Active Snowflake connection

    Raises:
        RuntimeError: If neither password nor private_key is provided
    """
    connection_params = {
        "user": user,
        "account": account,
        "warehouse": warehouse,
        "database": database,
        "schema": schema,
        "client_session_keep_alive": True,
        "session_parameters": {"CLIENT_SESSION_KEEP_ALIVE": True},
    }

    if role:
        connection_params["role"] = role

    if private_key is not None:
        connection_params["private_key"] = private_key
    elif password is not None:
        connection_params["password"] = password
    else:
        raise RuntimeError("Either password or private_key must be provided")

    return sf.connect(**connection_params)


def get_snowflake_connection_from_env() -> Optional[sf.SnowflakeConnection]:
    """
    Create Snowflake connection using environment variables.

    Attempts key-pair authentication first (via Secret Manager),
    falls back to password authentication if available.

    Returns:
        Snowflake connection, or None if configuration is incomplete
    """
    config = settings.get_snowflake_config()
    if not config:
        logger.warning("Snowflake configuration incomplete")
        return None

    # Try key-pair authentication first
    if settings.SF_PRIVATE_KEY_SECRET:
        try:
            private_key_bytes = load_private_key_from_secret_manager(
                settings.SF_PRIVATE_KEY_SECRET
            )
            return create_snowflake_connection(
                user=config["user"],
                account=config["account"],
                warehouse=config["warehouse"],
                database=config["database"],
                schema=config["schema"],
                role=config.get("role"),
                private_key=private_key_bytes,
            )
        except Exception as e:
            logger.warning(f"Key-pair auth failed: {e}")

    # Fall back to password auth
    if config.get("password"):
        return create_snowflake_connection(
            user=config["user"],
            account=config["account"],
            warehouse=config["warehouse"],
            database=config["database"],
            schema=config["schema"],
            role=config.get("role"),
            password=config["password"],
        )

    raise RuntimeError(
        "Could not establish Snowflake connection (key-pair failed and no password fallback)"
    )


def execute_query(
    connection: sf.SnowflakeConnection, query: str, fetch_pandas: bool = True
) -> Optional[pd.DataFrame]:
    """
    Execute a SQL query using an existing Snowflake connection.

    Args:
        connection: Active Snowflake connection
        query: SQL query to execute
        fetch_pandas: If True, return results as DataFrame; if False, return None

    Returns:
        DataFrame with query results if fetch_pandas=True, otherwise None
    """
    cursor = connection.cursor()
    try:
        cursor.execute(query)
        if fetch_pandas:
            return cursor.fetch_pandas_all()
        return None
    finally:
        cursor.close()


def get_table_columns(
    user: str,
    password: str,
    account: str,
    warehouse: str,
    database: str,
    schema: str,
    table: str,
    role: Optional[str] = None,
) -> list:
    """
    Get column names for a Snowflake table.

    Args:
        user: Snowflake username
        password: Snowflake password
        account: Account identifier
        warehouse: Warehouse name
        database: Database name
        schema: Schema name
        table: Table name (format: database.schema.table)
        role: Optional role name

    Returns:
        List of column names
    """
    db, sch, tbl = table.split(".")

    with create_snowflake_connection(
        user, account, warehouse, database, schema, role, password=password
    ) as conn:
        cursor = conn.cursor()
        cursor.execute(f"SHOW COLUMNS IN {db}.{sch}.{tbl}")
        rows = cursor.fetchall()
        # Column name is at index 2
        return [row[2] for row in rows]


def run_query_sample(
    user: str,
    password: str,
    account: str,
    warehouse: str,
    database: str,
    schema: str,
    sql: str,
    role: Optional[str] = None,
    limit: int = 1000,
) -> pd.DataFrame:
    """
    Run a SQL query with a LIMIT clause for sampling.

    Args:
        user: Snowflake username
        password: Snowflake password
        account: Account identifier
        warehouse: Warehouse name
        database: Database name
        schema: Schema name
        sql: SQL query
        role: Optional role name
        limit: Number of rows to return

    Returns:
        DataFrame with query results
    """
    with create_snowflake_connection(
        user, account, warehouse, database, schema, role, password=password
    ) as conn:
        query = f"{sql} LIMIT {limit}"
        return pd.read_sql(query, conn)
