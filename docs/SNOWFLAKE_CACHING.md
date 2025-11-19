# Snowflake Query Caching Implementation

## Overview

This implementation provides a two-tier caching strategy for Snowflake queries to reduce compute costs by ~70%.

## Architecture

```
┌─────────────┐
│   Request   │
└──────┬──────┘
       │
       ▼
┌─────────────────────┐
│  In-Memory Cache    │ ◄── Tier 1: Fast (1-hour TTL)
│  (Python dict)      │
└──────┬──────────────┘
       │ miss
       ▼
┌─────────────────────┐
│   GCS Cache         │ ◄── Tier 2: Persistent (24-hour TTL)
│  (Parquet files)    │
└──────┬──────────────┘
       │ miss
       ▼
┌─────────────────────┐
│  Snowflake Query    │ ◄── Execute and cache result
│  (Compute cost)     │
└─────────────────────┘
```

## Files

- **`app/utils/snowflake_cache.py`** - Core caching logic
- **`app/app_shared.py`** - Integration with `run_sql()` function
- **`app/nav/Cache_Management.py`** - UI for cache management
- **`tests/test_snowflake_cache.py`** - Unit tests

## Usage

### Automatic Caching

All queries through `run_sql()` are automatically cached:

```python
from app_shared import run_sql

# This query hits Snowflake
df = run_sql("SELECT * FROM my_table WHERE date > '2024-01-01'")

# This identical query uses cache (no Snowflake cost)
df = run_sql("SELECT * FROM my_table WHERE date > '2024-01-01'")
```

### Bypass Cache

For write operations or when fresh data is required:

```python
# Bypass cache for write operations
run_sql("INSERT INTO table VALUES (...)", use_cache=False)

# Bypass cache for real-time data
run_sql("SELECT current_timestamp()", use_cache=False)
```

### Query Normalization

Queries are normalized before caching, so these all use the same cache:

```python
run_sql("SELECT * FROM table")
run_sql("select * from table")
run_sql("SELECT  *  FROM  table")
```

## Cache Management UI

Access the cache management page in the Streamlit app:

1. Navigate to "Cache Management" in the sidebar
2. View cache statistics (hits, size, age)
3. Clear cache if needed
4. Calculate cost savings based on cache hit rate

## Configuration

Cache settings in `app/utils/snowflake_cache.py`:

```python
IN_MEMORY_TTL = 3600   # 1 hour in seconds
GCS_TTL = 86400        # 24 hours in seconds
CACHE_PREFIX = "cache/snowflake-queries/"
```

## Monitoring

### Check Cache Stats

```python
from utils.snowflake_cache import get_cache_stats

stats = get_cache_stats()
print(f"In-memory: {stats['in_memory_count']} queries")
print(f"GCS: {stats['gcs_count']} queries")
print(f"GCS size: {stats['gcs_total_size_mb']} MB")
```

### Clear Cache Programmatically

```python
from utils.snowflake_cache import clear_snowflake_cache

# Clear all cache
clear_snowflake_cache()

# Clear specific query
clear_snowflake_cache(query_hash="abc123...")
```

## Cost Savings

### Expected Savings

With a 70% cache hit rate:

| Queries/Month | Without Cache | With Cache | Savings |
|---------------|---------------|------------|---------|
| 100           | $10.00        | $3.00      | $7.00   |
| 500           | $50.00        | $15.00     | $35.00  |
| 1,000         | $100.00       | $30.00     | $70.00  |
| 5,000         | $500.00       | $150.00    | $350.00 |

### Actual Savings

Monitor actual cache hit rate in the Cache Management UI to calculate real savings.

## Storage Costs

GCS cache storage is minimal:

- Compressed Parquet format
- Automatic expiration after 24 hours
- Typical: 1-10 MB per cached query
- Monthly cost: ~$0.01-0.10 for storage

**Net savings:** Even with storage costs, you save 60-70% on Snowflake compute.

## Testing

Run unit tests:

```bash
cd /home/runner/work/mmm-app/mmm-app
python -m unittest tests.test_snowflake_cache -v
```

## Troubleshooting

### Cache not working

1. Check that `GCS_BUCKET` environment variable is set
2. Verify GCS bucket permissions (need read/write access)
3. Check application logs for cache-related errors

### High cache miss rate

1. Queries with dynamic timestamps will always miss cache
2. Use static date filters when possible
3. Consider increasing TTL for slowly-changing data

### GCS storage growing

1. Old cache files should auto-expire after 24 hours
2. Manually clear cache in Cache Management UI
3. Check GCS lifecycle policies are applied

## Future Enhancements

1. **Adaptive TTL** - Adjust TTL based on query patterns
2. **Cache warming** - Pre-populate cache for common queries
3. **Metrics dashboard** - Track cache hit rate over time
4. **Per-query TTL** - Different TTLs for different query types
5. **Cache invalidation** - Smart invalidation based on data updates

## References

- Cost estimates: `Cost estimate.csv`
- Optimization guide: `COST_OPTIMIZATION.md`
- GCS caching best practices: [Google Cloud Docs](https://cloud.google.com/storage/docs/caching)
