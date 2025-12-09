"""
Google Cloud Secret Manager utilities.

Provides functions for managing secrets in Google Cloud Secret Manager.
"""

from typing import Optional

from config import settings
from google.auth import default
from google.cloud import secretmanager


def _get_client() -> secretmanager.SecretManagerServiceClient:
    """Get Secret Manager client."""
    return secretmanager.SecretManagerServiceClient()


def _get_project_id(project_id: Optional[str] = None) -> str:
    """
    Get project ID from parameter or infer from environment/ADC.

    Args:
        project_id: Optional explicit project ID

    Returns:
        Project ID string

    Raises:
        RuntimeError: If project ID cannot be determined from any source
    """
    if project_id is not None:
        return project_id
    if settings.PROJECT_ID:
        return settings.PROJECT_ID
    # Fall back to ADC
    try:
        _, proj = default()
        if not proj:
            raise RuntimeError(
                "Could not determine project ID from Application Default Credentials"
            )
        return proj
    except Exception as e:
        raise RuntimeError(
            f"Failed to get project ID: {e}. "
            "Please set PROJECT_ID environment variable or pass project_id parameter."
        )


def upsert_secret(
    secret_id: str, payload: bytes, project_id: Optional[str] = None
) -> None:
    """
    Create or update a secret in Secret Manager.

    Creates the secret if it doesn't exist, then adds a new version with the provided payload.

    Args:
        secret_id: Secret identifier
        payload: Secret data as bytes
        project_id: Optional GCP project ID (inferred if not provided)
    """
    client = _get_client()
    project_id = _get_project_id(project_id)
    parent = f"projects/{project_id}"
    name = f"{parent}/secrets/{secret_id}"

    # Create secret if it doesn't exist
    try:
        client.create_secret(
            parent=parent,
            secret_id=secret_id,
            secret={"replication": {"automatic": {}}},
        )
    except Exception:
        pass  # Already exists

    # Add new version
    client.add_secret_version(parent=name, payload={"data": payload})


def access_secret(
    secret_id: str, project_id: Optional[str] = None
) -> Optional[bytes]:
    """
    Access the latest version of a secret from Secret Manager.

    Args:
        secret_id: Secret identifier
        project_id: Optional GCP project ID (inferred if not provided)

    Returns:
        Secret data as bytes, or None if secret doesn't exist
    """
    client = _get_client()
    project_id = _get_project_id(project_id)
    name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"

    try:
        resp = client.access_secret_version(name=name)
        return resp.payload.data
    except Exception:
        return None
