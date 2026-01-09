"""
Tests for GCS data collection and management scripts.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pandas as pd


class TestCollectGCSData(unittest.TestCase):
    """Tests for collect_gcs_data_examples.py script."""

    def test_safe_json_serialize_datetime(self):
        """Test serialization of datetime objects."""
        # Import the function
        import sys
        from datetime import datetime

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from collect_gcs_data_examples import safe_json_serialize

        dt = datetime(2024, 1, 1, 12, 0, 0)
        result = safe_json_serialize(dt)
        self.assertIsInstance(result, str)
        self.assertIn("2024", result)

    def test_safe_json_serialize_dataframe(self):
        """Test serialization of pandas DataFrame."""
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from collect_gcs_data_examples import safe_json_serialize

        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        result = safe_json_serialize(df)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)


class TestGenerateTestData(unittest.TestCase):
    """Tests for generate_test_data.py script."""

    def test_parse_dtype_int(self):
        """Test parsing integer dtype."""
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from generate_test_data import parse_dtype

        self.assertEqual(parse_dtype("int64"), int)
        self.assertEqual(parse_dtype("int32"), int)

    def test_parse_dtype_float(self):
        """Test parsing float dtype."""
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from generate_test_data import parse_dtype

        self.assertEqual(parse_dtype("float64"), float)
        self.assertEqual(parse_dtype("float32"), float)

    def test_parse_dtype_string(self):
        """Test parsing string dtype."""
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from generate_test_data import parse_dtype

        self.assertEqual(parse_dtype("object"), str)
        self.assertEqual(parse_dtype("string"), str)

    def test_parse_dtype_datetime(self):
        """Test parsing datetime dtype."""
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from generate_test_data import parse_dtype

        self.assertEqual(parse_dtype("datetime64"), "datetime")

    def test_generate_synthetic_data(self):
        """Test generating synthetic data from schema."""
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from generate_test_data import generate_synthetic_data

        schema = {
            "columns": ["col1", "col2", "col3"],
            "dtypes": {"col1": "int64", "col2": "float64", "col3": "object"},
            "sample_values": {"col1": [1, 2, 3], "col2": [1.5, 2.5, 3.5]},
        }

        df = generate_synthetic_data(schema, num_rows=50)

        self.assertEqual(len(df), 50)
        self.assertEqual(len(df.columns), 3)
        self.assertIn("col1", df.columns)
        self.assertIn("col2", df.columns)
        self.assertIn("col3", df.columns)


class TestUploadTestData(unittest.TestCase):
    """Tests for upload_test_data.py script."""

    def test_list_files_to_upload(self):
        """Test listing files to upload."""
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from upload_test_data import list_files_to_upload

        # Create temp directory with some files
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Create some test files
            (tmppath / "file1.json").write_text("{}")
            (tmppath / "subdir").mkdir()
            (tmppath / "subdir" / "file2.parquet").write_text("data")

            files = list_files_to_upload(tmppath)

            self.assertEqual(len(files), 2)
            file_names = [f.name for f in files]
            self.assertIn("file1.json", file_names)
            self.assertIn("file2.parquet", file_names)


class TestDeleteNonRevisionData(unittest.TestCase):
    """Tests for delete_non_revision_data.py script."""

    def test_is_revision_path(self):
        """Test revision path detection."""
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from delete_non_revision_data import is_revision_path

        # Should match
        self.assertTrue(is_revision_path("robyn/v1/de/r12/model.json"))
        self.assertTrue(is_revision_path("metadata/de/r24/mapping.json"))
        self.assertTrue(is_revision_path("data/r1/file.txt"))
        self.assertTrue(is_revision_path("test/r999/deep/path/file.csv"))

        # Should not match
        self.assertFalse(is_revision_path("robyn/v1/de/20231015/model.json"))
        self.assertFalse(is_revision_path("metadata/de/latest/mapping.json"))
        self.assertFalse(is_revision_path("data/revision12/file.txt"))
        self.assertFalse(
            is_revision_path("test/r/file.txt")
        )  # r without digits

    def test_extract_revision_folders(self):
        """Test extracting unique revision folder names."""
        import sys

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        from delete_non_revision_data import extract_revision_folders

        blob_names = [
            "robyn/v1/de/r12/model.json",
            "robyn/v1/de/r12/results.csv",
            "robyn/v1/de/r24/model.json",
            "metadata/universal/r1/mapping.json",
        ]

        revisions = extract_revision_folders(blob_names)

        self.assertEqual(len(revisions), 3)
        self.assertIn("r12", revisions)
        self.assertIn("r24", revisions)
        self.assertIn("r1", revisions)


if __name__ == "__main__":
    unittest.main()
