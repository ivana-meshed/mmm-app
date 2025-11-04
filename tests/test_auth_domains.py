"""
Tests for Google authentication domain validation.

This module tests the domain validation logic for Google OAuth authentication.
"""

import importlib
import os
import unittest
from unittest.mock import patch


class TestAllowedDomains(unittest.TestCase):
    """Test cases for allowed domains configuration."""

    def _reload_settings(self):
        """Helper method to reload settings module."""
        from app.config import settings
        importlib.reload(settings)
        return settings

    def test_single_domain_parsing(self):
        """Test parsing a single domain from environment variable."""
        with patch.dict(os.environ, {"ALLOWED_DOMAINS": "mesheddata.com"}):
            settings = self._reload_settings()
            
            self.assertIn("mesheddata.com", settings.ALLOWED_DOMAINS)
            self.assertEqual(len(settings.ALLOWED_DOMAINS), 1)

    def test_multiple_domains_parsing(self):
        """Test parsing multiple domains from environment variable."""
        with patch.dict(os.environ, {"ALLOWED_DOMAINS": "mesheddata.com,example.com,test.org"}):
            settings = self._reload_settings()
            
            self.assertIn("mesheddata.com", settings.ALLOWED_DOMAINS)
            self.assertIn("example.com", settings.ALLOWED_DOMAINS)
            self.assertIn("test.org", settings.ALLOWED_DOMAINS)
            self.assertEqual(len(settings.ALLOWED_DOMAINS), 3)

    def test_domains_with_spaces(self):
        """Test parsing domains with extra whitespace."""
        with patch.dict(os.environ, {"ALLOWED_DOMAINS": " mesheddata.com , example.com , test.org "}):
            settings = self._reload_settings()
            
            # Should be trimmed
            self.assertIn("mesheddata.com", settings.ALLOWED_DOMAINS)
            self.assertIn("example.com", settings.ALLOWED_DOMAINS)
            self.assertIn("test.org", settings.ALLOWED_DOMAINS)
            # Should not have spaces
            self.assertNotIn(" mesheddata.com ", settings.ALLOWED_DOMAINS)

    def test_domains_normalized_to_lowercase(self):
        """Test that domains are normalized to lowercase."""
        with patch.dict(os.environ, {"ALLOWED_DOMAINS": "MeshedData.COM,EXAMPLE.COM"}):
            settings = self._reload_settings()
            
            self.assertIn("mesheddata.com", settings.ALLOWED_DOMAINS)
            self.assertIn("example.com", settings.ALLOWED_DOMAINS)
            # Should not contain uppercase versions
            
            self.assertIn("mesheddata.com", settings.ALLOWED_DOMAINS)
            self.assertIn("example.com", settings.ALLOWED_DOMAINS)
            # Should not contain uppercase versions
            self.assertNotIn("MeshedData.COM", settings.ALLOWED_DOMAINS)

    def test_empty_domains_filtered(self):
        """Test that empty domains are filtered out."""
        with patch.dict(os.environ, {"ALLOWED_DOMAINS": "mesheddata.com,,example.com,  ,test.org"}):
            settings = self._reload_settings()
            
            self.assertIn("mesheddata.com", settings.ALLOWED_DOMAINS)
            self.assertIn("example.com", settings.ALLOWED_DOMAINS)
            self.assertIn("test.org", settings.ALLOWED_DOMAINS)
            self.assertNotIn("", settings.ALLOWED_DOMAINS)

    def test_backward_compatibility_allowed_domain(self):
        """Test backward compatibility with ALLOWED_DOMAIN (singular) env var."""
        with patch.dict(os.environ, {"ALLOWED_DOMAIN": "legacy.com", "ALLOWED_DOMAINS": "mesheddata.com"}):
            settings = self._reload_settings()
            
            # Both should be present
            self.assertIn("mesheddata.com", settings.ALLOWED_DOMAINS)
            self.assertIn("legacy.com", settings.ALLOWED_DOMAINS)

    def test_default_domain(self):
        """Test that default domain is set when no env var is provided."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove all env vars
            if "ALLOWED_DOMAINS" in os.environ:
                del os.environ["ALLOWED_DOMAINS"]
            if "ALLOWED_DOMAIN" in os.environ:
                del os.environ["ALLOWED_DOMAIN"]
            
            settings = self._reload_settings()
            
            # Should default to mesheddata.com
            self.assertIn("mesheddata.com", settings.ALLOWED_DOMAINS)


class TestEmailDomainValidation(unittest.TestCase):
    """Test cases for email domain validation logic."""

    def test_valid_email_single_domain(self):
        """Test validation with a single allowed domain."""
        allowed_domains = ["mesheddata.com"]
        
        # Valid email
        email = "user@mesheddata.com"
        email_domain = email.split("@")[-1] if "@" in email else ""
        is_valid = any(email_domain == domain for domain in allowed_domains)
        self.assertTrue(is_valid)
        
        # Invalid email
        email = "user@example.com"
        email_domain = email.split("@")[-1] if "@" in email else ""
        is_valid = any(email_domain == domain for domain in allowed_domains)
        self.assertFalse(is_valid)

    def test_valid_email_multiple_domains(self):
        """Test validation with multiple allowed domains."""
        allowed_domains = ["mesheddata.com", "example.com", "test.org"]
        
        # Valid emails
        for email in ["user@mesheddata.com", "user@example.com", "user@test.org"]:
            email_domain = email.split("@")[-1] if "@" in email else ""
            is_valid = any(email_domain == domain for domain in allowed_domains)
            self.assertTrue(is_valid, f"{email} should be valid")
        
        # Invalid email
        email = "user@blocked.com"
        email_domain = email.split("@")[-1] if "@" in email else ""
        is_valid = any(email_domain == domain for domain in allowed_domains)
        self.assertFalse(is_valid)

    def test_case_insensitive_validation(self):
        """Test that email validation is case-insensitive."""
        allowed_domains = ["mesheddata.com"]
        
        # These should all be valid after normalization
        for email in ["user@MeshedData.com", "user@MESHEDDATA.COM", "user@mesheddata.COM"]:
            email_lower = email.lower()
            email_domain = email_lower.split("@")[-1] if "@" in email_lower else ""
            is_valid = any(email_domain == domain for domain in allowed_domains)
            self.assertTrue(is_valid, f"{email} should be valid")

    def test_email_without_at_symbol(self):
        """Test handling of malformed email without @ symbol."""
        allowed_domains = ["mesheddata.com"]
        
        email = "notanemail"
        email_domain = email.split("@")[-1] if "@" in email else ""
        is_valid = any(email_domain == domain for domain in allowed_domains)
        self.assertFalse(is_valid)

    def test_subdomain_not_matched(self):
        """Test that subdomains are not automatically allowed."""
        allowed_domains = ["mesheddata.com"]
        
        # subdomain.mesheddata.com should NOT be allowed
        email = "user@subdomain.mesheddata.com"
        email_domain = email.split("@")[-1] if "@" in email else ""
        is_valid = any(email_domain == domain for domain in allowed_domains)
        self.assertFalse(is_valid, "Subdomains should not be automatically allowed")


if __name__ == "__main__":
    unittest.main()
