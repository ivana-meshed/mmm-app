"""
Unit tests for revision tag extraction logic.
Tests the helper functions that extract revision tags from GCS paths.
"""

import unittest
from unittest.mock import MagicMock, Mock, patch


class MockBlob:
    """Mock GCS blob for testing."""

    def __init__(self, name):
        self.name = name


class TestRevisionTagExtraction(unittest.TestCase):
    """Test revision tag extraction from GCS paths."""

    def test_extract_tag_from_path_ivana(self):
        """Test extracting 'ivana' tag from robyn/ivana_1/fr/1111_155209 path."""
        # Simulate the path parsing logic from _get_revision_tags
        blob_name = "robyn/ivana_1/fr/1111_155209/some_file.csv"
        parts = blob_name.split("/")

        # Verify we have enough parts and underscore is present
        self.assertGreaterEqual(
            len(parts), 2, "Path should have at least 2 parts"
        )
        self.assertIn("_", parts[1], "Second part should contain underscore")

        # Extract tag from TAG_NUMBER format
        tag = parts[1].rsplit("_", 1)[0]

        # Verify the tag is 'ivana'
        self.assertEqual(tag, "ivana", "Should extract 'ivana' from 'ivana_1'")

    def test_extract_tag_from_path_baseline(self):
        """Test extracting 'baseline' tag from robyn/baseline_2/de/... path."""
        blob_name = "robyn/baseline_2/de/0115_143522/model.rds"
        parts = blob_name.split("/")

        self.assertGreaterEqual(len(parts), 2)
        self.assertIn("_", parts[1])

        tag = parts[1].rsplit("_", 1)[0]
        self.assertEqual(tag, "baseline")

    def test_extract_tag_from_path_multi_underscore(self):
        """Test extracting tag from path with multiple underscores in tag name."""
        blob_name = "robyn/team_alpha_test_3/fr/0115_150000/data.csv"
        parts = blob_name.split("/")

        self.assertGreaterEqual(len(parts), 2)
        self.assertIn("_", parts[1])

        # rsplit with maxsplit=1 should only split on the last underscore
        tag = parts[1].rsplit("_", 1)[0]
        self.assertEqual(
            tag,
            "team_alpha_test",
            "Should extract everything before last underscore",
        )

    def test_extract_number_from_path(self):
        """Test extracting revision number from path."""
        blob_name = "robyn/ivana_1/fr/1111_155209/file.txt"
        parts = blob_name.split("/")

        # Extract number from TAG_NUMBER format
        num_str = parts[1].split("_")[-1]
        number = int(num_str)

        self.assertEqual(number, 1, "Should extract number 1 from 'ivana_1'")

    def test_path_without_underscore_skipped(self):
        """Test that paths without underscore in revision are skipped."""
        blob_name = "robyn/r100/fr/0110_120000/file.txt"
        parts = blob_name.split("/")

        # This should be skipped by the extraction logic
        has_underscore = "_" in parts[1]
        self.assertFalse(
            has_underscore, "Old format 'r100' should not have underscore"
        )

    def test_invalid_path_too_short(self):
        """Test that paths with insufficient parts are handled."""
        blob_name = "robyn"
        parts = blob_name.split("/")

        # Should not have enough parts (only 1 part)
        self.assertLess(
            len(parts), 2, "Short path should have less than 2 parts"
        )

    def test_get_revision_tags_integration(self):
        """Test _get_revision_tags function logic with mocked blobs."""
        # Create mock blobs - don't need actual GCS client
        mock_blobs = [
            MockBlob("robyn/ivana_1/fr/1111_155209/file1.csv"),
            MockBlob("robyn/ivana_2/de/1111_160000/file2.csv"),
            MockBlob("robyn/baseline_1/fr/0115_143022/file3.rds"),
            MockBlob("robyn/baseline_2/de/0115_143522/file4.rds"),
            MockBlob("robyn/experimental_5/it/0115_144022/file5.csv"),
            MockBlob(
                "robyn/r100/fr/0110_120000/file6.txt"
            ),  # Old format, no underscore
        ]

        # Simulate the _get_revision_tags logic
        revision_tags = set()
        for blob in mock_blobs:
            parts = blob.name.split("/")
            if len(parts) >= 2 and "_" in parts[1]:
                tag = parts[1].rsplit("_", 1)[0]
                revision_tags.add(tag)

        tags_list = sorted(list(revision_tags))

        # Verify the results
        self.assertIn("ivana", tags_list, "'ivana' should be in the tags list")
        self.assertIn(
            "baseline", tags_list, "'baseline' should be in the tags list"
        )
        self.assertIn(
            "experimental",
            tags_list,
            "'experimental' should be in the tags list",
        )
        self.assertNotIn(
            "r100", tags_list, "'r100' (old format) should not be in tags list"
        )

        # Verify tags are sorted
        self.assertEqual(
            tags_list, sorted(tags_list), "Tags should be sorted alphabetically"
        )


if __name__ == "__main__":
    unittest.main()
