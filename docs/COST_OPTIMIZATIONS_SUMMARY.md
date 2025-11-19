# Cost Optimizations Implementation Summary

## Overview

This document summarizes the cost optimization implementations for the MMM Trainer application.

## Implemented Optimizations

### 1. ✅ Reduced Minimum Instances to 0

**File:** `infra/terraform/variables.tf`

**Change:**
```hcl
variable "min_instances" {
  default = 0  # Changed from 2
}
```

**Savings:** $42.34/month (94% of idle cost)

**Trade-off:** Adds 1-3 second cold start latency on first request after idle period

**To deploy:**
```bash
cd infra/terraform
terraform apply -var-file="envs/prod.tfvars"
```

---

### 2. ✅ GCS Lifecycle Policies

**File:** `infra/terraform/storage.tf` (documentation)

**What it does:**
- Moves data to Nearline storage after 30 days (50% cheaper)
- Moves data to Coldline storage after 90 days (80% cheaper)
- Deletes old queue data after 365 days

**Savings:** ~$0.78/month on baseline storage, up to 80% on old data

**To apply:**
```bash
gcloud storage buckets update gs://mmm-app-output --lifecycle-file=lifecycle.json
```

See `infra/terraform/storage.tf` for the lifecycle.json content.

---

### 3. ✅ Snowflake Query Caching

**Files:**
- `app/utils/snowflake_cache.py` - Core caching logic
- `app/app_shared.py` - Integration
- `app/nav/Cache_Management.py` - Management UI
- `tests/test_snowflake_cache.py` - Tests
- `docs/SNOWFLAKE_CACHING.md` - Documentation

**How it works:**
- **Tier 1:** In-memory cache (1-hour TTL) for immediate access
- **Tier 2:** GCS persistent cache (24-hour TTL) for durability
- Queries are automatically normalized (whitespace/case insensitive)

**Savings:** $7-350/month (70% reduction in Snowflake costs with typical 70% cache hit rate)

**Usage:**
```python
# Automatic caching
df = run_sql("SELECT * FROM table")  # First: hits Snowflake
df = run_sql("SELECT * FROM table")  # Second: uses cache

# Bypass cache for writes
df = run_sql("INSERT INTO ...", use_cache=False)
```

**Monitoring:**
- Access "Cache Management" page in Streamlit app
- View real-time cache statistics
- Calculate actual cost savings

---

## Total Cost Savings

| Scenario | Original | Optimized | Savings | % Saved |
|----------|----------|-----------|---------|---------|
| **Idle** | $45.03 | $2.69 | $42.34 | **94%** |
| 100 calls/month | $148.00 | $98.66 | $49.34 | **33%** |
| 500 calls/month | $519.56 | $434.56 | $85.00 | **16%** |
| 1,000 calls/month | $1,073.39 | $933.39 | $140.00 | **13%** |
| 5,000 calls/month | $5,142.33 | $4,792.33 | $350.00 | **7%** |

## Cost Breakdown by Service (Optimized, 5K calls/month)

| Service | Monthly Cost | % of Total | Optimization Applied |
|---------|--------------|------------|---------------------|
| Snowflake | $150.00 | 3.1% | ✅ 70% cache hit rate |
| Cloud Run (Training) | $3,361.00 | 70.1% | 🔮 Future: preemptible instances |
| Cloud Run (Web) | $1,264.40 | 26.4% | ✅ min_instances=0 |
| Cloud Storage | $70.82 | 1.5% | ✅ Lifecycle policies |
| Networking | $30.00 | 0.6% | 📝 Compression recommended |
| Other | $16.11 | 0.3% | - |
| **Total** | **$4,792.33** | **100%** | |

## Next Steps (Optional)

### Medium Priority

1. **Apply GCS lifecycle policies** (5 minutes)
   - Run the gcloud command from `infra/terraform/storage.tf`
   - Verify with: `gcloud storage buckets describe gs://mmm-app-output`

2. **Monitor cache hit rate** (ongoing)
   - Access Cache Management page weekly
   - Adjust TTLs if needed based on usage patterns

3. **Implement result compression** (1-2 hours)
   - Compress training results before GCS upload
   - Expected savings: 50% on storage and egress (~$35/month at 5K calls)

### Low Priority

4. **Optimize Docker images** (2-3 hours)
   - Use multi-stage builds
   - Savings: ~$0.05/month

5. **Configure log retention** (30 minutes)
   - Set retention policies in Cloud Logging
   - Minimal savings (first 50GB free)

## Monitoring & Validation

### Verify Optimizations

1. **Check Cloud Run min instances:**
   ```bash
   gcloud run services describe mmm-app-web --region=europe-west1 \
     --format='get(spec.template.metadata.annotations)'
   ```
   Should show `run.googleapis.com/min-instances: 0`

2. **Check cache statistics:**
   - Open Streamlit app
   - Navigate to "Cache Management"
   - Verify cache is being used (in_memory_count > 0)

3. **Check GCS lifecycle policies:**
   ```bash
   gcloud storage buckets describe gs://mmm-app-output --format="get(lifecycle)"
   ```

### Monitor Costs

Track costs in GCP Console:
1. Go to Billing → Reports
2. Filter by project: `datawarehouse-422511`
3. Group by: Service
4. Compare month-over-month costs

Expected reductions:
- Cloud Run: ~$42/month (min_instances)
- Snowflake: ~$7-350/month (caching)
- Cloud Storage: ~$0.78/month (lifecycle)

## Rollback Instructions

### Revert min_instances to 2

If cold starts are unacceptable:

```bash
cd infra/terraform
terraform apply -var="min_instances=2" -var-file="envs/prod.tfvars"
```

Cost impact: +$42.34/month

### Disable Snowflake caching

If caching causes issues:

```python
# In app/app_shared.py, change default:
def run_sql(sql: str, use_cache: bool = False):  # Changed from True
```

Or selectively disable per query:
```python
df = run_sql(query, use_cache=False)
```

### Remove GCS lifecycle policies

```bash
gcloud storage buckets update gs://mmm-app-output --clear-lifecycle
```

## Support

- **Cost estimates:** See `Cost estimate.csv`
- **Optimization details:** See `COST_OPTIMIZATION.md`
- **Caching documentation:** See `docs/SNOWFLAKE_CACHING.md`
- **Infrastructure:** See `infra/terraform/`

## Change Log

- **2025-11-19:** Initial implementation
  - Set min_instances to 0
  - Documented GCS lifecycle policies
  - Implemented Snowflake query caching
  - Created cache management UI
  - Updated cost estimates
