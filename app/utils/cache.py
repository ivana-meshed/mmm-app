"""
Caching utilities for expensive operations.

Provides in-memory caching with TTL (time-to-live) for:
- GCS list operations
- Metadata reads
- Query results
"""

import hashlib
import json
import logging
import time
from functools import wraps
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Global cache storage
_cache: Dict[str, Dict[str, Any]] = {}


def _get_cache_key(func_name: str, *args, **kwargs) -> str:
    """
    Generate a cache key from function name and arguments.

    Args:
        func_name: Name of the function
        *args: Positional arguments
        **kwargs: Keyword arguments

    Returns:
        Cache key string
    """
    # Create a stable representation of arguments
    key_parts = [func_name]

    # Add positional args
    for arg in args:
        if isinstance(arg, (str, int, float, bool)):
            key_parts.append(str(arg))
        else:
            # For complex types, use hash
            key_parts.append(str(hash(str(arg))))

    # Add keyword args (sorted for stability)
    for k, v in sorted(kwargs.items()):
        if isinstance(v, (str, int, float, bool)):
            key_parts.append(f"{k}={v}")
        else:
            key_parts.append(f"{k}={hash(str(v))}")

    # Create hash of all parts
    key_str = "|".join(key_parts)
    return hashlib.md5(key_str.encode()).hexdigest()


def cached(ttl_seconds: int = 300):
    """
    Decorator to cache function results with a time-to-live.

    Args:
        ttl_seconds: Time-to-live in seconds (default: 5 minutes)

    Example:
        @cached(ttl_seconds=600)
        def expensive_function(param1, param2):
            # Expensive computation
            return result
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Generate cache key
            cache_key = _get_cache_key(func.__name__, *args, **kwargs)

            # Check if cached value exists and is not expired
            if cache_key in _cache:
                entry = _cache[cache_key]
                age = time.time() - entry["timestamp"]
                if age < ttl_seconds:
                    logger.debug(
                        f"Cache hit for {func.__name__} (age: {age:.1f}s)"
                    )
                    return entry["value"]
                else:
                    logger.debug(
                        f"Cache expired for {func.__name__} (age: {age:.1f}s)"
                    )

            # Call function and cache result
            logger.debug(f"Cache miss for {func.__name__}, executing function")
            result = func(*args, **kwargs)
            _cache[cache_key] = {"timestamp": time.time(), "value": result}

            return result

        return wrapper

    return decorator


def clear_cache(pattern: Optional[str] = None):
    """
    Clear cached entries.

    Args:
        pattern: If provided, only clear keys containing this substring.
                 If None, clear all cache entries.

    Returns:
        Number of entries cleared
    """
    global _cache

    if pattern is None:
        count = len(_cache)
        _cache.clear()
        logger.info(f"Cleared all {count} cache entries")
        return count

    # Clear entries matching pattern
    keys_to_remove = [k for k in _cache.keys() if pattern in k]
    for key in keys_to_remove:
        del _cache[key]

    logger.info(
        f"Cleared {len(keys_to_remove)} cache entries matching '{pattern}'"
    )
    return len(keys_to_remove)


def get_cache_stats() -> Dict[str, Any]:
    """
    Get statistics about the current cache.

    Returns:
        Dictionary with cache statistics
    """
    if not _cache:
        return {
            "size": 0,
            "oldest_age": 0,
            "newest_age": 0,
            "average_age": 0,
        }

    current_time = time.time()
    ages = [current_time - entry["timestamp"] for entry in _cache.values()]

    return {
        "size": len(_cache),
        "oldest_age": max(ages),
        "newest_age": min(ages),
        "average_age": sum(ages) / len(ages),
    }


def invalidate_on_write(func: Callable) -> Callable:
    """
    Decorator to automatically clear cache when a write operation occurs.

    Use this on functions that modify data to ensure cache stays consistent.

    Example:
        @invalidate_on_write
        def save_metadata(data):
            # Save data to GCS
            ...
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        # Clear cache after write
        clear_cache()
        logger.info(f"Cache invalidated after {func.__name__}")
        return result

    return wrapper
