"""
Cloud secrets management abstraction layer.

Provides a unified interface for secrets management that works with both
Google Cloud Secret Manager and AWS Secrets Manager.

The appropriate implementation is selected based on the CLOUD_PROVIDER environment variable:
- "gcp" (default): Use Google Cloud Secret Manager
- "aws": Use AWS Secrets Manager
"""

import os
from abc import ABC, abstractmethod
from typing import Optional


class CloudSecretsProvider(ABC):
    """Abstract base class for cloud secrets providers."""

    @abstractmethod
    def get_secret(self, secret_name: str, version: str = "latest") -> str:
        """
        Get a secret value from the secrets manager.

        Args:
            secret_name: Name/ID of the secret
            version: Version of the secret (default: "latest")

        Returns:
            Secret value as string

        Raises:
            Exception: If secret not found or access denied
        """
        pass

    @abstractmethod
    def create_secret(
        self, secret_name: str, secret_value: str, description: str = ""
    ) -> str:
        """
        Create a new secret.

        Args:
            secret_name: Name/ID for the secret
            secret_value: Value to store
            description: Optional description

        Returns:
            Secret ARN/resource name
        """
        pass

    @abstractmethod
    def update_secret(self, secret_name: str, secret_value: str) -> str:
        """
        Update an existing secret value.

        Args:
            secret_name: Name/ID of the secret
            secret_value: New value to store

        Returns:
            Version ID/ARN of the new version
        """
        pass

    @abstractmethod
    def delete_secret(self, secret_name: str, force: bool = False) -> None:
        """
        Delete a secret.

        Args:
            secret_name: Name/ID of the secret
            force: If True, delete immediately without recovery window (AWS)
        """
        pass

    @abstractmethod
    def secret_exists(self, secret_name: str) -> bool:
        """
        Check if a secret exists.

        Args:
            secret_name: Name/ID of the secret

        Returns:
            True if secret exists, False otherwise
        """
        pass


class GCPSecretsProvider(CloudSecretsProvider):
    """Google Cloud Secret Manager implementation."""

    def __init__(self):
        from google.cloud import secretmanager

        self.client = secretmanager.SecretManagerServiceClient()
        self.project_id = os.getenv("PROJECT_ID")
        if not self.project_id:
            raise ValueError(
                "PROJECT_ID environment variable must be set for GCP secrets"
            )

    def _get_secret_path(
        self, secret_name: str, version: str = "latest"
    ) -> str:
        """Build the full secret path for GCP."""
        return f"projects/{self.project_id}/secrets/{secret_name}/versions/{version}"

    def _get_secret_name_path(self, secret_name: str) -> str:
        """Build the secret name path (without version) for GCP."""
        return f"projects/{self.project_id}/secrets/{secret_name}"

    def get_secret(self, secret_name: str, version: str = "latest") -> str:
        """Get a secret from Google Secret Manager."""
        name = self._get_secret_path(secret_name, version)
        response = self.client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")

    def create_secret(
        self, secret_name: str, secret_value: str, description: str = ""
    ) -> str:
        """Create a new secret in Google Secret Manager."""
        from google.cloud import secretmanager

        parent = f"projects/{self.project_id}"

        # Create the secret
        secret = self.client.create_secret(
            request={
                "parent": parent,
                "secret_id": secret_name,
                "secret": {
                    "replication": {"automatic": {}},
                    "labels": {"managed_by": "mmm-app"},
                },
            }
        )

        # Add the secret version
        self.client.add_secret_version(
            request={
                "parent": secret.name,
                "payload": {"data": secret_value.encode("UTF-8")},
            }
        )

        return secret.name

    def update_secret(self, secret_name: str, secret_value: str) -> str:
        """Update a secret in Google Secret Manager."""
        parent = self._get_secret_name_path(secret_name)
        response = self.client.add_secret_version(
            request={
                "parent": parent,
                "payload": {"data": secret_value.encode("UTF-8")},
            }
        )
        return response.name

    def delete_secret(self, secret_name: str, force: bool = False) -> None:
        """Delete a secret from Google Secret Manager."""
        name = self._get_secret_name_path(secret_name)
        self.client.delete_secret(request={"name": name})

    def secret_exists(self, secret_name: str) -> bool:
        """Check if a secret exists in Google Secret Manager."""
        try:
            name = self._get_secret_name_path(secret_name)
            self.client.get_secret(request={"name": name})
            return True
        except Exception:
            return False


class AWSSecretsProvider(CloudSecretsProvider):
    """AWS Secrets Manager implementation."""

    def __init__(self):
        import boto3

        self.client = boto3.client("secretsmanager")

    def get_secret(self, secret_name: str, version: str = "latest") -> str:
        """Get a secret from AWS Secrets Manager."""
        kwargs = {"SecretId": secret_name}
        if version != "latest":
            kwargs["VersionId"] = version

        response = self.client.get_secret_value(**kwargs)
        return response["SecretString"]

    def create_secret(
        self, secret_name: str, secret_value: str, description: str = ""
    ) -> str:
        """Create a new secret in AWS Secrets Manager."""
        kwargs = {
            "Name": secret_name,
            "SecretString": secret_value,
            "Tags": [{"Key": "ManagedBy", "Value": "mmm-app"}],
        }
        if description:
            kwargs["Description"] = description

        response = self.client.create_secret(**kwargs)
        return response["ARN"]

    def update_secret(self, secret_name: str, secret_value: str) -> str:
        """Update a secret in AWS Secrets Manager."""
        response = self.client.put_secret_value(
            SecretId=secret_name, SecretString=secret_value
        )
        return response["VersionId"]

    def delete_secret(self, secret_name: str, force: bool = False) -> None:
        """Delete a secret from AWS Secrets Manager."""
        kwargs = {"SecretId": secret_name}
        if force:
            kwargs["ForceDeleteWithoutRecovery"] = True
        else:
            kwargs["RecoveryWindowInDays"] = 7

        self.client.delete_secret(**kwargs)

    def secret_exists(self, secret_name: str) -> bool:
        """Check if a secret exists in AWS Secrets Manager."""
        try:
            self.client.describe_secret(SecretId=secret_name)
            return True
        except self.client.exceptions.ResourceNotFoundException:
            return False
        except Exception:
            return False


# Singleton instance
_provider: Optional[CloudSecretsProvider] = None


def get_secrets_provider() -> CloudSecretsProvider:
    """
    Get the appropriate cloud secrets provider based on CLOUD_PROVIDER env var.

    Returns:
        CloudSecretsProvider instance (GCP or AWS)
    """
    global _provider
    if _provider is None:
        provider_name = os.getenv("CLOUD_PROVIDER", "gcp").lower()
        if provider_name == "aws":
            _provider = AWSSecretsProvider()
        else:
            _provider = GCPSecretsProvider()
    return _provider


# Convenience functions that delegate to the provider
def get_secret(secret_name: str, version: str = "latest") -> str:
    """Get a secret value from the secrets manager."""
    return get_secrets_provider().get_secret(secret_name, version)


def create_secret(
    secret_name: str, secret_value: str, description: str = ""
) -> str:
    """Create a new secret."""
    return get_secrets_provider().create_secret(
        secret_name, secret_value, description
    )


def update_secret(secret_name: str, secret_value: str) -> str:
    """Update an existing secret value."""
    return get_secrets_provider().update_secret(secret_name, secret_value)


def delete_secret(secret_name: str, force: bool = False) -> None:
    """Delete a secret."""
    get_secrets_provider().delete_secret(secret_name, force)


def secret_exists(secret_name: str) -> bool:
    """Check if a secret exists."""
    return get_secrets_provider().secret_exists(secret_name)
