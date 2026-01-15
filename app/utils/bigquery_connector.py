"""
BigQuery connection and query utilities.

Provides secure connection management with support for:
- Service account authentication via Secret Manager
- JSON key file authentication
- Query execution with pandas integration
"""

import json
import logging
from typing import Optional

import pandas as pd
from google.cloud import bigquery, secretmanager
from google.oauth2 import service_account

# Import from parent config module (app.config)
try:
    from config import settings
except ImportError:
    from ..config import settings

logger = logging.getLogger(__name__)


def load_credentials_from_secret_manager(secret_id: str) -> dict:
    """
    Load BigQuery service account credentials from Google Secret Manager.

    Args:
        secret_id: Secret Manager secret ID or full resource path

    Returns:
        Service account credentials dictionary

    Raises:
        RuntimeError: If secret cannot be loaded or credentials are invalid
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
        logger.error(
            f"Failed to fetch BigQuery credentials from Secret Manager: {e}"
        )
        raise RuntimeError(f"Could not load BigQuery credentials: {e}")

    try:
        credentials_dict = json.loads(payload.decode("utf-8"))
        return credentials_dict
    except Exception as e:
        logger.error(f"Failed to parse BigQuery credentials JSON: {e}")
        raise RuntimeError(f"Invalid BigQuery credentials format: {e}")


def create_bigquery_client(
    project_id: str,
    credentials_json: Optional[str] = None,
    credentials_dict: Optional[dict] = None,
) -> bigquery.Client:
    """
    Create a BigQuery client with service account credentials.

    Args:
        project_id: GCP project ID
        credentials_json: Service account JSON as string
        credentials_dict: Service account credentials as dict

    Returns:
        BigQuery client instance

    Raises:
        RuntimeError: If neither credentials_json nor credentials_dict is
        provided
    """
    if credentials_dict is not None:
        credentials = service_account.Credentials.from_service_account_info(
            credentials_dict
        )
    elif credentials_json is not None:
        credentials_dict = json.loads(credentials_json)
        credentials = service_account.Credentials.from_service_account_info(
            credentials_dict
        )
    else:
        raise RuntimeError(
            "Either credentials_json or credentials_dict must be provided"
        )

    return bigquery.Client(project=project_id, credentials=credentials)


def execute_query(
    client: bigquery.Client, query: str, fetch_pandas: bool = True
) -> Optional[pd.DataFrame]:
    """
    Execute a SQL query using a BigQuery client.

    Args:
        client: BigQuery client instance
        query: SQL query to execute
        fetch_pandas: If True, return results as DataFrame; else return None

    Returns:
        DataFrame with query results if fetch_pandas=True, otherwise None
    """
    try:
        query_job = client.query(query)
        if fetch_pandas:
            return query_job.to_dataframe()
        return None
    except Exception as e:
        logger.error(f"BigQuery query failed: {e}")
        raise


def get_table_preview(
    client: bigquery.Client, table_id: str, limit: int = 20
) -> pd.DataFrame:
    """
    Get a preview of a BigQuery table.

    Args:
        client: BigQuery client instance
        table_id: Fully qualified table ID (project.dataset.table)
        limit: Number of rows to return

    Returns:
        DataFrame with table preview
    """
    query = f"SELECT * FROM `{table_id}` LIMIT {limit}"
    return execute_query(client, query)
