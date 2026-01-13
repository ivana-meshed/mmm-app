"""
Tests for GCS data management scripts.

These tests validate the logic of download, delete, and upload scripts
without requiring actual GCS access (using mocking).
"""

import unittest
from pathlib import Path
from unittest.mock import patch

# We'll import the classes after mocking storage.Client
with patch("google.cloud.storage.Client"):
    from scripts.copy_test_to_root import TestFolderCopier
    from scripts.delete_bucket_data import BucketDataCleaner
    from scripts.download_test_data import TestDataDownloader
    from scripts.upload_test_data import TestDataUploader


class TestDownloadScript(unittest.TestCase):
    """Tests for download_test_data.py"""

    def setUp(self):
        """Set up test fixtures."""
        with patch("google.cloud.storage.Client"):
            self.downloader = TestDataDownloader(
                bucket_name="test-bucket", output_dir="/tmp/test", dry_run=True
            )

    def test_downloader_initialization(self):
        """Test downloader initializes correctly."""
        self.assertEqual(self.downloader.bucket_name, "test-bucket")
        self.assertEqual(self.downloader.output_dir, Path("/tmp/test"))
        self.assertTrue(self.downloader.dry_run)

    def test_download_blob_dry_run(self):
        """Test download in dry run mode."""
        result = self.downloader.download_blob("test/blob.txt")
        self.assertTrue(result)


class TestDeleteScript(unittest.TestCase):
    """Tests for delete_bucket_data.py"""

    def setUp(self):
        """Set up test fixtures."""
        with patch("google.cloud.storage.Client"):
            self.cleaner = BucketDataCleaner(
                bucket_name="test-bucket", dry_run=True
            )

    def test_cleaner_initialization(self):
        """Test cleaner initializes correctly."""
        self.assertEqual(self.cleaner.bucket_name, "test-bucket")
        self.assertTrue(self.cleaner.dry_run)

    def test_should_keep_robyn_r_folders(self):
        """Test that robyn folders starting with 'r' are kept."""
        # Should keep
        self.assertTrue(
            self.cleaner.should_keep_blob(
                "robyn/r100/de/20251211_115528/file.txt"
            )
        )
        self.assertTrue(
            self.cleaner.should_keep_blob(
                "robyn/r101/fr/20251211_115528/file.txt"
            )
        )
        self.assertTrue(
            self.cleaner.should_keep_blob(
                "robyn/r1/de/20251211_115528/file.txt"
            )
        )

    def test_should_not_keep_robyn_v_folders(self):
        """Test that robyn folders not starting with 'r' are deleted."""
        self.assertFalse(
            self.cleaner.should_keep_blob(
                "robyn/v1/de/20251211_115528/file.txt"
            )
        )
        self.assertFalse(
            self.cleaner.should_keep_blob(
                "robyn/v2/fr/20251211_115528/file.txt"
            )
        )

    def test_should_not_keep_other_folders(self):
        """Test that non-robyn folders are deleted."""
        self.assertFalse(
            self.cleaner.should_keep_blob("datasets/de/latest/file.txt")
        )
        self.assertFalse(
            self.cleaner.should_keep_blob("mapped-datasets/fr/latest/file.txt")
        )
        self.assertFalse(
            self.cleaner.should_keep_blob("metadata/universal/file.txt")
        )

    def test_delete_blob_dry_run(self):
        """Test delete in dry run mode."""
        result = self.cleaner.delete_blob("test/blob.txt")
        self.assertTrue(result)


class TestUploadScript(unittest.TestCase):
    """Tests for upload_test_data.py"""

    def setUp(self):
        """Set up test fixtures."""
        with patch("google.cloud.storage.Client"):
            self.uploader = TestDataUploader(
                bucket_name="test-bucket",
                input_dir="/tmp/test",
                dry_run=True,
                skip_existing=True,
            )

    def test_uploader_initialization(self):
        """Test uploader initializes correctly."""
        self.assertEqual(self.uploader.bucket_name, "test-bucket")
        self.assertEqual(self.uploader.input_dir, Path("/tmp/test"))
        self.assertTrue(self.uploader.dry_run)
        self.assertTrue(self.uploader.skip_existing)

    @patch("scripts.upload_test_data.TestDataUploader.blob_exists")
    def test_upload_file_skip_existing(self, mock_exists):
        """Test upload skips existing files."""
        mock_exists.return_value = True
        result = self.uploader.upload_file(
            Path("/tmp/test/file.txt"), "file.txt"
        )
        self.assertTrue(result)

    def test_upload_file_dry_run(self):
        """Test upload in dry run mode."""
        result = self.uploader.upload_file(
            Path("/tmp/test/file.txt"), "file.txt"
        )
        self.assertTrue(result)

    def test_uploader_with_prefix(self):
        """Test uploader with prefix."""
        with patch("google.cloud.storage.Client"):
            uploader = TestDataUploader(
                bucket_name="test-bucket",
                input_dir="/tmp/test",
                dry_run=True,
                prefix="TEST",
            )
            self.assertEqual(uploader.prefix, "TEST/")


class TestCopyScript(unittest.TestCase):
    """Tests for copy_test_to_root.py"""

    def setUp(self):
        """Set up test fixtures."""
        with patch("google.cloud.storage.Client"):
            self.copier = TestFolderCopier(
                bucket_name="test-bucket", dry_run=True
            )

    def test_copier_initialization(self):
        """Test copier initializes correctly."""
        self.assertEqual(self.copier.bucket_name, "test-bucket")
        self.assertTrue(self.copier.dry_run)
        self.assertFalse(self.copier.overwrite)

    def test_copy_blob_dry_run(self):
        """Test copy in dry run mode."""
        result = self.copier.copy_blob("TEST/file.txt", "file.txt")
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
