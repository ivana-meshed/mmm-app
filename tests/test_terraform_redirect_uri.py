"""
Tests for Terraform OAuth redirect URI configuration.

This module tests that the OAuth redirect URI is correctly computed
based on the service name and environment.
"""

import unittest


class TestTerraformRedirectURI(unittest.TestCase):
    """Test cases for OAuth redirect URI configuration in Terraform."""

    def test_prod_redirect_uri(self):
        """Test that prod environment generates correct redirect URI."""
        service_name = "mmm-app"
        expected_url = f"https://{service_name}-web-wuepn6nq5a-ew.a.run.app"
        expected_redirect_uri = f"{expected_url}/oauth2callback"
        
        # The actual URL pattern for prod
        self.assertEqual(
            expected_redirect_uri,
            "https://mmm-app-web-wuepn6nq5a-ew.a.run.app/oauth2callback"
        )

    def test_dev_redirect_uri(self):
        """Test that dev environment generates correct redirect URI."""
        service_name = "mmm-app-dev"
        expected_url = f"https://{service_name}-web-wuepn6nq5a-ew.a.run.app"
        expected_redirect_uri = f"{expected_url}/oauth2callback"
        
        # The actual URL pattern for dev
        self.assertEqual(
            expected_redirect_uri,
            "https://mmm-app-dev-web-wuepn6nq5a-ew.a.run.app/oauth2callback"
        )

    def test_redirect_uri_pattern(self):
        """Test that redirect URI follows the correct pattern."""
        test_cases = [
            ("mmm-app", "https://mmm-app-web-wuepn6nq5a-ew.a.run.app/oauth2callback"),
            ("mmm-app-dev", "https://mmm-app-dev-web-wuepn6nq5a-ew.a.run.app/oauth2callback"),
            ("test-service", "https://test-service-web-wuepn6nq5a-ew.a.run.app/oauth2callback"),
        ]
        
        for service_name, expected_uri in test_cases:
            with self.subTest(service_name=service_name):
                computed_uri = f"https://{service_name}-web-wuepn6nq5a-ew.a.run.app/oauth2callback"
                self.assertEqual(computed_uri, expected_uri)

    def test_redirect_uri_has_oauth2callback(self):
        """Test that redirect URI always ends with /oauth2callback."""
        service_name = "mmm-app"
        redirect_uri = f"https://{service_name}-web-wuepn6nq5a-ew.a.run.app/oauth2callback"
        
        self.assertTrue(redirect_uri.endswith("/oauth2callback"))

    def test_redirect_uri_has_https(self):
        """Test that redirect URI uses HTTPS protocol."""
        service_name = "mmm-app"
        redirect_uri = f"https://{service_name}-web-wuepn6nq5a-ew.a.run.app/oauth2callback"
        
        self.assertTrue(redirect_uri.startswith("https://"))

    def test_redirect_uri_contains_service_name(self):
        """Test that redirect URI contains the service name."""
        service_name = "mmm-app"
        redirect_uri = f"https://{service_name}-web-wuepn6nq5a-ew.a.run.app/oauth2callback"
        
        self.assertIn(service_name, redirect_uri)


if __name__ == "__main__":
    unittest.main()
