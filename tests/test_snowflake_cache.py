"""
Tests for Snowflake query caching functionality.

Tests the two-tier caching strategy:
1. In-memory cache with 1-hour TTL
2. GCS persistent cache with 24-hour TTL
"""

import hashlib
import io
import time
import unittest
from unittest.mock import MagicMock, Mock, patch

import pandas as pd

# Add parent directory to path for imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from utils.snowflake_cache import (
    _get_query_hash,
    get_cached_query_result,
    clear_snowflake_cache,
    get_cache_stats,
    init_cache,
)


class TestSnowflakeCache(unittest.TestCase):
    """Test Snowflake query caching functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        # Initialize cache with test bucket
        init_cache("test-bucket")
        # Clear any existing cache
        clear_snowflake_cache()
        
    def tearDown(self):
        """Clean up after tests."""
        clear_snowflake_cache()
        
    def test_query_hash_normalization(self):
        """Test that query hashing normalizes SQL properly."""
        query1 = "SELECT * FROM table"
        query2 = "select * from table"
        query3 = "SELECT  *  FROM  table"
        
        hash1 = _get_query_hash(query1)
        hash2 = _get_query_hash(query2)
        hash3 = _get_query_hash(query3)
        
        # All should produce the same hash due to normalization
        self.assertEqual(hash1, hash2)
        self.assertEqual(hash2, hash3)
        
    def test_query_hash_different_queries(self):
        """Test that different queries produce different hashes."""
        query1 = "SELECT * FROM table1"
        query2 = "SELECT * FROM table2"
        
        hash1 = _get_query_hash(query1)
        hash2 = _get_query_hash(query2)
        
        self.assertNotEqual(hash1, hash2)
        
    def test_in_memory_cache_hit(self):
        """Test that in-memory cache returns cached results."""
        query = "SELECT * FROM test_table"
        expected_df = pd.DataFrame({"col1": [1, 2, 3], "col2": ["a", "b", "c"]})
        
        # Mock execute function
        execute_func = Mock(return_value=expected_df)
        
        # First call should execute query
        result1 = get_cached_query_result(query, execute_func, use_gcs_cache=False)
        self.assertEqual(execute_func.call_count, 1)
        pd.testing.assert_frame_equal(result1, expected_df)
        
        # Second call should use cache
        result2 = get_cached_query_result(query, execute_func, use_gcs_cache=False)
        self.assertEqual(execute_func.call_count, 1)  # Still only called once
        pd.testing.assert_frame_equal(result2, expected_df)
        
    def test_in_memory_cache_miss_different_queries(self):
        """Test that different queries don't hit each other's cache."""
        query1 = "SELECT * FROM table1"
        query2 = "SELECT * FROM table2"
        
        df1 = pd.DataFrame({"col1": [1, 2, 3]})
        df2 = pd.DataFrame({"col2": [4, 5, 6]})
        
        execute_func1 = Mock(return_value=df1)
        execute_func2 = Mock(return_value=df2)
        
        result1 = get_cached_query_result(query1, execute_func1, use_gcs_cache=False)
        result2 = get_cached_query_result(query2, execute_func2, use_gcs_cache=False)
        
        self.assertEqual(execute_func1.call_count, 1)
        self.assertEqual(execute_func2.call_count, 1)
        
        pd.testing.assert_frame_equal(result1, df1)
        pd.testing.assert_frame_equal(result2, df2)
        
    @patch('utils.snowflake_cache.storage.Client')
    def test_gcs_cache_write(self, mock_storage_client):
        """Test that results are written to GCS cache."""
        # Set up mocks
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_storage_client.return_value.bucket.return_value = mock_bucket
        mock_bucket.blob.return_value = mock_blob
        
        query = "SELECT * FROM test_table"
        df = pd.DataFrame({"col1": [1, 2, 3]})
        execute_func = Mock(return_value=df)
        
        # Execute query with GCS cache enabled
        result = get_cached_query_result(query, execute_func, use_gcs_cache=True)
        
        # Verify blob upload was called
        mock_blob.upload_from_file.assert_called_once()
        
        # Verify metadata was set
        self.assertIsNotNone(mock_blob.metadata)
        self.assertIn("cached_timestamp", mock_blob.metadata)
        
    def test_cache_stats(self):
        """Test that cache statistics are returned correctly."""
        # Initially empty
        stats = get_cache_stats()
        self.assertEqual(stats["in_memory_count"], 0)
        
        # Add some cached queries
        query1 = "SELECT * FROM table1"
        query2 = "SELECT * FROM table2"
        df = pd.DataFrame({"col1": [1, 2, 3]})
        execute_func = Mock(return_value=df)
        
        get_cached_query_result(query1, execute_func, use_gcs_cache=False)
        get_cached_query_result(query2, execute_func, use_gcs_cache=False)
        
        stats = get_cache_stats()
        self.assertEqual(stats["in_memory_count"], 2)
        
    def test_clear_cache(self):
        """Test that clearing cache removes all entries."""
        query = "SELECT * FROM test_table"
        df = pd.DataFrame({"col1": [1, 2, 3]})
        execute_func = Mock(return_value=df)
        
        # Cache a query
        get_cached_query_result(query, execute_func, use_gcs_cache=False)
        stats = get_cache_stats()
        self.assertEqual(stats["in_memory_count"], 1)
        
        # Clear cache
        clear_snowflake_cache()
        stats = get_cache_stats()
        self.assertEqual(stats["in_memory_count"], 0)
        
        # Next query should execute, not use cache
        execute_func.reset_mock()
        get_cached_query_result(query, execute_func, use_gcs_cache=False)
        self.assertEqual(execute_func.call_count, 1)
        

class TestCacheIntegration(unittest.TestCase):
    """Integration tests for caching with app_shared."""
    
    @patch('app_shared.ensure_sf_conn')
    def test_run_sql_with_cache(self, mock_ensure_conn):
        """Test that run_sql uses caching by default."""
        # This test would require importing app_shared and mocking Snowflake
        # For now, we'll just ensure the import works
        try:
            from app_shared import run_sql
            # Verify function signature includes use_cache parameter
            import inspect
            sig = inspect.signature(run_sql)
            self.assertIn('use_cache', sig.parameters)
            self.assertEqual(sig.parameters['use_cache'].default, True)
        except ImportError:
            self.skipTest("app_shared not available in test environment")


if __name__ == "__main__":
    unittest.main()
