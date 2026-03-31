"""
Unit tests for track_daily_costs.py

Tests the cost tracking functionality without requiring actual GCP credentials.
"""

import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

# Mock the google-cloud-bigquery import before importing the module
sys.modules["google.cloud"] = MagicMock()
sys.modules["google.cloud.bigquery"] = MagicMock()
sys.modules["google.api_core"] = MagicMock()
sys.modules["google.api_core.exceptions"] = MagicMock()

# Import after mocking
sys.path.insert(0, "/home/runner/work/mmm-app/mmm-app/scripts")
import track_daily_costs


class TestCostTracking(unittest.TestCase):
    """Test cases for cost tracking functions."""

    def test_get_date_range(self):
        """Test date range calculation."""
        start_date, end_date = track_daily_costs.get_date_range(30)

        # Parse dates
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        # Verify the range is approximately 30 days
        delta = (end - start).days
        self.assertGreaterEqual(delta, 29)
        self.assertLessEqual(delta, 31)

    def test_categorize_cost_requests(self):
        """Test cost categorization for requests."""
        # User requests
        category = track_daily_costs.categorize_cost(
            "Cloud Run Request Time", None
        )
        self.assertEqual(category, "user_requests")

        # Scheduler requests (with queue in resource name)
        category = track_daily_costs.categorize_cost(
            "Cloud Run Request Time", "robyn-queue-tick"
        )
        self.assertEqual(category, "scheduler_requests")

    def test_categorize_cost_compute(self):
        """Test cost categorization for compute resources."""
        # CPU costs
        category = track_daily_costs.categorize_cost(
            "Cloud Run CPU Allocation Time", None
        )
        self.assertEqual(category, "compute_cpu")

        # Memory costs
        category = track_daily_costs.categorize_cost(
            "Cloud Run Memory Allocation Time", None
        )
        self.assertEqual(category, "compute_memory")

    def test_categorize_cost_storage(self):
        """Test cost categorization for storage."""
        # Artifact Registry
        category = track_daily_costs.categorize_cost(
            "Artifact Registry Storage", None
        )
        self.assertEqual(category, "registry")

        # Cloud Storage
        category = track_daily_costs.categorize_cost(
            "Cloud Storage Standard Storage", None
        )
        self.assertEqual(category, "storage")

        # Scheduler service
        category = track_daily_costs.categorize_cost(
            "Cloud Scheduler Jobs", None
        )
        self.assertEqual(category, "scheduler_service")

    def test_identify_service(self):
        """Test service identification."""
        # Production services
        self.assertEqual(
            track_daily_costs.identify_service("mmm-app-web", "Cloud Run CPU"),
            "mmm-app-web",
        )
        self.assertEqual(
            track_daily_costs.identify_service(
                "mmm-app-training", "Cloud Run CPU"
            ),
            "mmm-app-training",
        )

        # Development services
        self.assertEqual(
            track_daily_costs.identify_service(
                "mmm-app-dev-web", "Cloud Run CPU"
            ),
            "mmm-app-dev-web",
        )
        self.assertEqual(
            track_daily_costs.identify_service(
                "mmm-app-dev-training", "Cloud Run CPU"
            ),
            "mmm-app-dev-training",
        )

        # Scheduler names map to web services
        self.assertEqual(
            track_daily_costs.identify_service(
                "robyn-queue-tick", "Cloud Scheduler"
            ),
            "mmm-app-web",
        )
        self.assertEqual(
            track_daily_costs.identify_service(
                "robyn-queue-tick-dev", "Cloud Scheduler"
            ),
            "mmm-app-dev-web",
        )

        # Registry and storage are identified
        self.assertEqual(
            track_daily_costs.identify_service(
                None, "Artifact Registry Storage"
            ),
            "registry",
        )
        self.assertEqual(
            track_daily_costs.identify_service(None, "Cloud Storage Standard"),
            "storage",
        )

    def test_process_costs_basic(self):
        """Test basic cost processing."""
        # Sample billing data
        query_results = [
            {
                "usage_date": "2026-02-09",
                "service_name": "Cloud Run",
                "sku_description": "Cloud Run CPU Allocation Time",
                "resource_name": "mmm-app-web",
                "total_cost": 10.50,
            },
            {
                "usage_date": "2026-02-09",
                "service_name": "Cloud Run",
                "sku_description": "Cloud Run Memory Allocation Time",
                "resource_name": "mmm-app-web",
                "total_cost": 3.25,
            },
        ]

        costs_by_date = track_daily_costs.process_costs(query_results)

        # Verify structure
        self.assertIn("2026-02-09", costs_by_date)
        self.assertIn("mmm-app-web", costs_by_date["2026-02-09"])

        # Verify costs
        web_costs = costs_by_date["2026-02-09"]["mmm-app-web"]
        self.assertIn("compute_cpu", web_costs)
        self.assertIn("compute_memory", web_costs)
        self.assertEqual(web_costs["compute_cpu"], 10.50)
        self.assertEqual(web_costs["compute_memory"], 3.25)

    def test_process_costs_shared(self):
        """Test shared cost distribution (registry, storage)."""
        query_results = [
            {
                "usage_date": "2026-02-09",
                "service_name": "Artifact Registry",
                "sku_description": "Artifact Registry Storage",
                "resource_name": None,
                "total_cost": 4.00,  # Will be split 4 ways
            }
        ]

        costs_by_date = track_daily_costs.process_costs(query_results)

        # Verify all services got a share
        date_costs = costs_by_date["2026-02-09"]
        self.assertEqual(len(date_costs), 4)  # 4 services

        # Each service should get $1.00 (4.00 / 4)
        for service in track_daily_costs.SERVICE_MAPPING.keys():
            self.assertIn(service, date_costs)
            self.assertIn("registry", date_costs[service])
            self.assertEqual(date_costs[service]["registry"], 1.00)

    def test_format_currency(self):
        """Test currency formatting."""
        self.assertEqual(track_daily_costs.format_currency(10.5), "$10.50")
        self.assertEqual(track_daily_costs.format_currency(0.123), "$0.12")
        self.assertEqual(
            track_daily_costs.format_currency(1234.567), "$1234.57"
        )

    def test_build_cost_query(self):
        """Test SQL query generation."""
        query = track_daily_costs.build_cost_query(
            "2026-02-01", "2026-02-09", "test-project"
        )

        # Verify query contains expected elements
        self.assertIn("test-project", query)
        self.assertIn("2026-02-01", query)
        self.assertIn("2026-02-09", query)
        self.assertIn("Cloud Run", query)
        self.assertIn("Artifact Registry", query)
        self.assertIn("Cloud Storage", query)
        self.assertIn("Cloud Scheduler", query)


class TestCostProcessing(unittest.TestCase):
    """Test cost processing with multiple scenarios."""

    def test_zero_cost_filtering(self):
        """Test that zero and negative costs are filtered out."""
        query_results = [
            {
                "usage_date": "2026-02-09",
                "service_name": "Cloud Run",
                "sku_description": "Cloud Run CPU",
                "resource_name": "mmm-app-web",
                "total_cost": 0.0,
            },
            {
                "usage_date": "2026-02-09",
                "service_name": "Cloud Run",
                "sku_description": "Cloud Run Memory",
                "resource_name": "mmm-app-web",
                "total_cost": -5.0,
            },
            {
                "usage_date": "2026-02-09",
                "service_name": "Cloud Run",
                "sku_description": "Cloud Run CPU",
                "resource_name": "mmm-app-training",
                "total_cost": 10.0,
            },
        ]

        costs_by_date = track_daily_costs.process_costs(query_results)

        # Only positive cost should be included
        self.assertIn("mmm-app-training", costs_by_date["2026-02-09"])
        # Zero/negative cost service might not be in results
        if "mmm-app-web" in costs_by_date["2026-02-09"]:
            # If it exists, it should only have shared costs, not CPU/memory
            web_costs = costs_by_date["2026-02-09"]["mmm-app-web"]
            self.assertNotIn("compute_cpu", web_costs)
            self.assertNotIn("compute_memory", web_costs)

    def test_multiple_dates(self):
        """Test processing costs across multiple dates."""
        query_results = [
            {
                "usage_date": "2026-02-09",
                "service_name": "Cloud Run",
                "sku_description": "Cloud Run CPU",
                "resource_name": "mmm-app-web",
                "total_cost": 10.0,
            },
            {
                "usage_date": "2026-02-08",
                "service_name": "Cloud Run",
                "sku_description": "Cloud Run CPU",
                "resource_name": "mmm-app-web",
                "total_cost": 12.0,
            },
        ]

        costs_by_date = track_daily_costs.process_costs(query_results)

        # Both dates should be present
        self.assertIn("2026-02-09", costs_by_date)
        self.assertIn("2026-02-08", costs_by_date)
        self.assertEqual(
            costs_by_date["2026-02-09"]["mmm-app-web"]["compute_cpu"],
            10.0,
        )
        self.assertEqual(
            costs_by_date["2026-02-08"]["mmm-app-web"]["compute_cpu"],
            12.0,
        )


if __name__ == "__main__":
    unittest.main()
