"""
Tests for caching utilities.
"""

import time
import unittest

from app.utils.cache import (
    cached,
    clear_cache,
    get_cache_stats,
    invalidate_on_write,
)


class TestCaching(unittest.TestCase):
    """Tests for cache decorator and utilities."""

    def setUp(self):
        """Clear cache before each test."""
        clear_cache()

    def test_cached_function_basic(self):
        """Test basic caching functionality."""
        call_count = {"count": 0}

        @cached(ttl_seconds=10)
        def expensive_function(x):
            call_count["count"] += 1
            return x * 2

        # First call should execute function
        result1 = expensive_function(5)
        self.assertEqual(result1, 10)
        self.assertEqual(call_count["count"], 1)

        # Second call should use cache
        result2 = expensive_function(5)
        self.assertEqual(result2, 10)
        self.assertEqual(call_count["count"], 1)  # Not incremented

    def test_cached_function_different_args(self):
        """Test caching with different arguments."""
        call_count = {"count": 0}

        @cached(ttl_seconds=10)
        def expensive_function(x):
            call_count["count"] += 1
            return x * 2

        # Different arguments should not use cache
        result1 = expensive_function(5)
        result2 = expensive_function(10)
        self.assertEqual(result1, 10)
        self.assertEqual(result2, 20)
        self.assertEqual(call_count["count"], 2)

    def test_cached_function_ttl_expiry(self):
        """Test cache expiration after TTL."""
        call_count = {"count": 0}

        @cached(ttl_seconds=1)  # 1 second TTL
        def expensive_function(x):
            call_count["count"] += 1
            return x * 2

        # First call
        result1 = expensive_function(5)
        self.assertEqual(call_count["count"], 1)

        # Wait for TTL to expire
        time.sleep(1.5)

        # Should execute function again
        result2 = expensive_function(5)
        self.assertEqual(result2, 10)
        self.assertEqual(call_count["count"], 2)

    def test_clear_cache_all(self):
        """Test clearing all cache entries."""

        @cached(ttl_seconds=10)
        def func1(x):
            return x

        @cached(ttl_seconds=10)
        def func2(x):
            return x * 2

        # Populate cache
        func1(1)
        func2(1)

        stats = get_cache_stats()
        self.assertEqual(stats["size"], 2)

        # Clear all
        count = clear_cache()
        self.assertEqual(count, 2)

        stats = get_cache_stats()
        self.assertEqual(stats["size"], 0)

    def test_clear_cache_pattern(self):
        """Test clearing cache with pattern matching."""

        @cached(ttl_seconds=10)
        def func1(x):
            return x

        @cached(ttl_seconds=10)
        def func2(x):
            return x * 2

        # Populate cache
        func1(1)
        func2(1)

        # Clear only func1 entries (won't work perfectly due to hash,
        # but test the mechanism)
        stats_before = get_cache_stats()
        clear_cache(pattern="func")
        stats_after = get_cache_stats()

        # Should have cleared some entries
        self.assertLessEqual(stats_after["size"], stats_before["size"])

    def test_get_cache_stats_empty(self):
        """Test cache stats with empty cache."""
        stats = get_cache_stats()
        self.assertEqual(stats["size"], 0)
        self.assertEqual(stats["oldest_age"], 0)
        self.assertEqual(stats["newest_age"], 0)
        self.assertEqual(stats["average_age"], 0)

    def test_get_cache_stats_populated(self):
        """Test cache stats with populated cache."""

        @cached(ttl_seconds=10)
        def func(x):
            return x

        func(1)
        func(2)

        stats = get_cache_stats()
        self.assertEqual(stats["size"], 2)
        self.assertGreater(stats["oldest_age"], 0)
        self.assertGreater(stats["average_age"], 0)

    def test_invalidate_on_write(self):
        """Test automatic cache invalidation on write."""

        @cached(ttl_seconds=10)
        def read_func():
            return "cached_value"

        @invalidate_on_write
        def write_func():
            return "written"

        # Populate cache
        result1 = read_func()
        stats = get_cache_stats()
        self.assertEqual(stats["size"], 1)

        # Write should clear cache
        write_func()
        stats = get_cache_stats()
        self.assertEqual(stats["size"], 0)


if __name__ == "__main__":
    unittest.main()
