"""
Unit tests for version module.
"""

import unittest
import re
from pathlib import Path


class TestVersioning(unittest.TestCase):
    """Tests for version management."""

    def test_version_file_exists(self):
        """Test that VERSION file exists."""
        version_file = Path(__file__).parent.parent / "VERSION"
        self.assertTrue(
            version_file.exists(), "VERSION file should exist at repo root"
        )

    def test_version_format(self):
        """Test that VERSION file contains valid semver format."""
        version_file = Path(__file__).parent.parent / "VERSION"
        version = version_file.read_text().strip()
        
        # Semantic versioning pattern: X.Y.Z or X.Y.Z-prerelease
        semver_pattern = r'^\d+\.\d+\.\d+(-[a-zA-Z0-9.-]+)?$'
        self.assertIsNotNone(
            re.match(semver_pattern, version),
            f"Version '{version}' does not match semver format"
        )

    def test_version_module_import(self):
        """Test that version module can be imported."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
        
        from __version__ import __version__
        
        self.assertIsInstance(__version__, str)
        self.assertGreater(len(__version__), 0)

    def test_version_consistency(self):
        """Test that VERSION file matches __version__ module."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
        
        from __version__ import __version__
        
        version_file = Path(__file__).parent.parent / "VERSION"
        file_version = version_file.read_text().strip()
        
        self.assertEqual(
            __version__,
            file_version,
            "Version in __version__ should match VERSION file"
        )


if __name__ == "__main__":
    unittest.main()
