"""
Tests for model summary aggregation utilities
"""

import json

# Import the module to test
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from aggregate_model_summaries import ModelSummaryAggregator


class TestModelSummaryAggregator(unittest.TestCase):
    """Test ModelSummaryAggregator class"""

    def setUp(self):
        """Set up test fixtures"""
        self.bucket_name = "test-bucket"
        self.project_id = "test-project"

    @patch("aggregate_model_summaries.storage.Client")
    def test_init(self, mock_client_class):
        """Test aggregator initialization"""
        aggregator = ModelSummaryAggregator(self.bucket_name, self.project_id)

        self.assertEqual(aggregator.bucket_name, self.bucket_name)
        self.assertEqual(aggregator.project_id, self.project_id)
        mock_client_class.assert_called_once_with(project=self.project_id)

    @patch("aggregate_model_summaries.storage.Client")
    def test_list_model_runs_basic(self, mock_client_class):
        """Test listing model runs"""
        # Mock GCS client
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        # Mock blobs
        mock_blob1 = MagicMock()
        mock_blob1.name = "robyn/v1/US/123456/OutputCollect.RDS"

        mock_blob2 = MagicMock()
        mock_blob2.name = "robyn/v1/UK/234567/OutputCollect.RDS"

        mock_client.list_blobs.return_value = [mock_blob1, mock_blob2]

        # Mock bucket.blob().exists()
        mock_bucket = MagicMock()
        mock_summary_blob1 = MagicMock()
        mock_summary_blob1.exists.return_value = True

        mock_summary_blob2 = MagicMock()
        mock_summary_blob2.exists.return_value = False

        def blob_side_effect(path):
            if "123456" in path:
                return mock_summary_blob1
            return mock_summary_blob2

        mock_bucket.blob.side_effect = blob_side_effect
        mock_client_class.return_value.bucket.return_value = mock_bucket

        aggregator = ModelSummaryAggregator(self.bucket_name, self.project_id)
        aggregator.bucket = mock_bucket

        runs = aggregator.list_model_runs()

        # Should find 2 runs
        self.assertEqual(len(runs), 2)

        # Check first run
        self.assertEqual(runs[0]["revision"], "v1")
        self.assertEqual(runs[0]["country"], "US")
        self.assertEqual(runs[0]["timestamp"], "123456")
        self.assertTrue(runs[0]["has_summary"])

        # Check second run
        self.assertEqual(runs[1]["country"], "UK")
        self.assertFalse(runs[1]["has_summary"])

    @patch("aggregate_model_summaries.storage.Client")
    def test_read_summary(self, mock_client_class):
        """Test reading a summary from GCS"""
        # Create a mock summary
        test_summary = {
            "country": "US",
            "revision": "v1",
            "timestamp": "123456",
            "has_pareto_models": True,
            "best_model": {"model_id": "1_2_3", "nrmse": 0.05},
        }

        # Mock GCS client
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_blob.exists.return_value = True
        mock_blob.download_as_text.return_value = json.dumps(test_summary)

        mock_bucket.blob.return_value = mock_blob
        mock_client.bucket.return_value = mock_bucket

        aggregator = ModelSummaryAggregator(self.bucket_name, self.project_id)
        aggregator.bucket = mock_bucket

        # Read the summary
        summary = aggregator.read_summary("robyn/v1/US/123456")

        # Verify the result
        self.assertIsNotNone(summary)
        self.assertEqual(summary["country"], "US")
        self.assertTrue(summary["has_pareto_models"])
        self.assertEqual(summary["best_model"]["nrmse"], 0.05)

    @patch("aggregate_model_summaries.storage.Client")
    def test_read_summary_not_found(self, mock_client_class):
        """Test reading a non-existent summary"""
        # Mock GCS client
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_blob.exists.return_value = False

        mock_bucket.blob.return_value = mock_blob
        mock_client.bucket.return_value = mock_bucket

        aggregator = ModelSummaryAggregator(self.bucket_name, self.project_id)
        aggregator.bucket = mock_bucket

        # Try to read non-existent summary
        summary = aggregator.read_summary("robyn/v1/US/999999")

        # Should return None
        self.assertIsNone(summary)

    @patch("aggregate_model_summaries.storage.Client")
    def test_aggregate_by_country(self, mock_client_class):
        """Test aggregating summaries by country"""
        # Create mock summaries
        summary1 = {
            "country": "US",
            "has_pareto_models": True,
            "best_model": {"model_id": "1_2_3", "nrmse": 0.05},
        }
        summary2 = {
            "country": "US",
            "has_pareto_models": False,
            "best_model": {"model_id": "4_5_6", "nrmse": 0.06},
        }
        summary3 = {
            "country": "US",
            "has_pareto_models": True,
            "best_model": {"model_id": "7_8_9", "nrmse": 0.04},
        }

        # Mock the aggregator methods
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        with patch.object(
            ModelSummaryAggregator, "list_model_runs"
        ) as mock_list:
            with patch.object(
                ModelSummaryAggregator, "read_summary"
            ) as mock_read:
                mock_list.return_value = [
                    {
                        "path": "robyn/v1/US/1",
                        "has_summary": True,
                        "country": "US",
                    },
                    {
                        "path": "robyn/v1/US/2",
                        "has_summary": True,
                        "country": "US",
                    },
                    {
                        "path": "robyn/v1/US/3",
                        "has_summary": True,
                        "country": "US",
                    },
                ]

                mock_read.side_effect = [summary1, summary2, summary3]

                aggregator = ModelSummaryAggregator(
                    self.bucket_name, self.project_id
                )

                # Aggregate
                result = aggregator.aggregate_by_country("US")

                # Verify
                self.assertEqual(result["country"], "US")
                self.assertEqual(result["total_runs"], 3)
                self.assertEqual(result["runs_with_pareto_models"], 2)
                self.assertEqual(len(result["runs"]), 3)

                # Best overall should have lowest NRMSE (0.04)
                self.assertEqual(
                    result["best_model_overall"]["model_id"], "7_8_9"
                )
                self.assertEqual(result["best_model_overall"]["nrmse"], 0.04)


class TestSummaryJSONSchema(unittest.TestCase):
    """Test that summary JSON matches expected schema"""

    def test_summary_schema_structure(self):
        """Test basic structure of a summary dict"""
        summary = {
            "country": "US",
            "revision": "v1",
            "timestamp": "123456",
            "created_at": "2025-11-13T12:00:00",
            "training_time_mins": 45.5,
            "has_pareto_models": True,
            "pareto_model_count": 5,
            "candidate_model_count": 100,
            "best_model": {
                "model_id": "1_2_3",
                "nrmse": 0.05,
                "rsq_train": 0.95,
            },
            "pareto_models": [],
            "candidate_models": [],
        }

        # Verify required fields exist
        self.assertIn("country", summary)
        self.assertIn("revision", summary)
        self.assertIn("timestamp", summary)
        self.assertIn("has_pareto_models", summary)
        self.assertIn("best_model", summary)

        # Verify types
        self.assertIsInstance(summary["country"], str)
        self.assertIsInstance(summary["has_pareto_models"], bool)
        self.assertIsInstance(summary["pareto_model_count"], int)
        self.assertIsInstance(summary["training_time_mins"], (int, float))

        # Verify best_model structure
        self.assertIn("model_id", summary["best_model"])
        self.assertIn("nrmse", summary["best_model"])

    def test_aggregated_summary_schema(self):
        """Test structure of aggregated summary"""
        aggregated = {
            "country": "US",
            "revision": "v1",
            "aggregated_at": "2025-11-13T12:00:00",
            "total_runs": 10,
            "runs_with_pareto_models": 8,
            "best_model_overall": {"model_id": "1_2_3", "nrmse": 0.04},
            "runs": [],
        }

        # Verify required fields
        self.assertIn("country", aggregated)
        self.assertIn("total_runs", aggregated)
        self.assertIn("runs", aggregated)

        # Verify types
        self.assertIsInstance(aggregated["total_runs"], int)
        self.assertIsInstance(aggregated["runs"], list)


if __name__ == "__main__":
    unittest.main()
