"""
Tests for BigQuery connector utilities.

This module tests the BigQuery connection and query functionality.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from app.utils.bigquery_connector import (
    create_bigquery_client,
    execute_query,
    load_credentials_from_secret_manager,
)


class TestBigQueryConnector(unittest.TestCase):
    """Test cases for BigQuery connector functions."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_project_id = "test-project"
        self.test_credentials = {
            "type": "service_account",
            "project_id": "test-project",
            "private_key_id": "key123",
            "private_key": "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----\n",
            "client_email": "test@test-project.iam.gserviceaccount.com",
            "client_id": "123456789",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/test%40test-project.iam.gserviceaccount.com",
        }

    @patch("app.utils.bigquery_connector.secretmanager")
    @patch("app.utils.bigquery_connector.settings")
    def test_load_credentials_from_secret_manager(
        self, mock_settings, mock_secretmanager
    ):
        """Test loading credentials from Secret Manager."""
        mock_settings.PROJECT_ID = "test-project"

        # Mock the secret manager client and response
        mock_client = MagicMock()
        mock_secretmanager.SecretManagerServiceClient.return_value = mock_client

        mock_response = MagicMock()
        mock_response.payload.data = json.dumps(self.test_credentials).encode(
            "utf-8"
        )
        mock_client.access_secret_version.return_value = mock_response

        # Test with just secret ID
        result = load_credentials_from_secret_manager("bq-creds-test")

        self.assertEqual(result["project_id"], "test-project")
        self.assertEqual(result["type"], "service_account")

    @patch("app.utils.bigquery_connector.bigquery")
    @patch("app.utils.bigquery_connector.service_account")
    def test_create_bigquery_client_with_json(
        self, mock_service_account, mock_bigquery
    ):
        """Test creating BigQuery client with JSON credentials."""
        mock_credentials = MagicMock()
        mock_service_account.Credentials.from_service_account_info.return_value = (
            mock_credentials
        )
        mock_client = MagicMock()
        mock_bigquery.Client.return_value = mock_client

        credentials_json = json.dumps(self.test_credentials)
        client = create_bigquery_client(
            self.test_project_id, credentials_json=credentials_json
        )

        mock_service_account.Credentials.from_service_account_info.assert_called_once()
        mock_bigquery.Client.assert_called_once_with(
            project=self.test_project_id, credentials=mock_credentials
        )
        self.assertEqual(client, mock_client)

    @patch("app.utils.bigquery_connector.bigquery")
    @patch("app.utils.bigquery_connector.service_account")
    def test_create_bigquery_client_with_dict(
        self, mock_service_account, mock_bigquery
    ):
        """Test creating BigQuery client with dict credentials."""
        mock_credentials = MagicMock()
        mock_service_account.Credentials.from_service_account_info.return_value = (
            mock_credentials
        )
        mock_client = MagicMock()
        mock_bigquery.Client.return_value = mock_client

        client = create_bigquery_client(
            self.test_project_id, credentials_dict=self.test_credentials
        )

        mock_service_account.Credentials.from_service_account_info.assert_called_once()
        mock_bigquery.Client.assert_called_once_with(
            project=self.test_project_id, credentials=mock_credentials
        )
        self.assertEqual(client, mock_client)

    def test_create_bigquery_client_no_credentials(self):
        """Test that creating client without credentials raises error."""
        with self.assertRaises(RuntimeError) as context:
            create_bigquery_client(self.test_project_id)

        self.assertIn(
            "Either credentials_json or credentials_dict must be provided",
            str(context.exception),
        )

    @patch("app.utils.bigquery_connector.bigquery")
    def test_execute_query(self, mock_bigquery):
        """Test executing a query."""
        mock_client = MagicMock()
        mock_query_job = MagicMock()
        mock_df = MagicMock()
        mock_query_job.to_dataframe.return_value = mock_df
        mock_client.query.return_value = mock_query_job

        result = execute_query(mock_client, "SELECT * FROM table")

        mock_client.query.assert_called_once_with("SELECT * FROM table")
        mock_query_job.to_dataframe.assert_called_once()
        self.assertEqual(result, mock_df)

    @patch("app.utils.bigquery_connector.bigquery")
    def test_execute_query_no_fetch(self, mock_bigquery):
        """Test executing a query without fetching results."""
        mock_client = MagicMock()
        mock_query_job = MagicMock()
        mock_client.query.return_value = mock_query_job

        result = execute_query(
            mock_client, "INSERT INTO table VALUES (1)", fetch_pandas=False
        )

        mock_client.query.assert_called_once()
        mock_query_job.to_dataframe.assert_not_called()
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
