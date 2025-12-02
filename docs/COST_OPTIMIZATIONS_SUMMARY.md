# Cost Optimizations Implementation Summary

## Overview

This document summarizes the cost optimization implementations for the MMM Trainer application.

## Current Cost Estimates (December 2025)

| Scenario | Web Service | Training Jobs | Fixed Costs | **Total** |
|----------|-------------|---------------|-------------|-----------|
| **Idle** | $0.00 | $0.00 | $2.09 | **$2.09** |
| 100 calls/month | $2.68 | $50.69 | $2.09 | **$55.46** |
| 500 calls/month | $13.40 | $253.43 | $2.09 | **$268.92** |
| 1,000 calls/month | $26.80 | $506.86 | $2.09 | **$535.75** |
| 5,000 calls/month | $134.00 | $2,534.30 | $2.09 | **$2,670.39** |

**Key finding:** Training jobs account for ~95% of variable costs at scale.

## Implemented Optimizations

### 1. ✅ Reduced Minimum Instances to 0

**File:** `infra/terraform/variables.tf`

**Change:**
```hcl
variable "min_instances" {
  default = 0  # Changed from 2
}
```

**Savings:** $42.94/month (95% of idle cost)

**Trade-off:** Adds 1-3 second cold start latency on first request after idle period

**To deploy:**
```bash
cd infra/terraform
terraform apply -var-file="envs/prod.tfvars"
```

---

### 2. ✅ Training Job Right-Sizing

**Files:** `infra/terraform/variables.tf`, `infra/terraform/envs/prod.tfvars`

**Change:**
```hcl
# Reduced from 8 vCPU/32GB to 4 vCPU/16GB
training_cpu       = "4.0"
training_memory    = "16Gi"
training_max_cores = "4"
```

**Savings:** ~50% per training job (~$51 vs ~$100 per job including GCS costs)

**Trade-off:** Jobs may take 1.5-2x longer to complete

---

### 3. ✅ GCS Lifecycle Policies

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

### 4. ✅ Snowflake Query Caching

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
| **Idle** | $45.03 | $2.09 | $42.94 | **95%** |
| 100 calls/month | $148.00 | $55.46 | $92.54 | **63%** |
| 500 calls/month | $519.56 | $268.92 | $250.64 | **48%** |
| 1,000 calls/month | $1,073.39 | $535.75 | $537.64 | **50%** |
| 5,000 calls/month | $5,142.33 | $2,670.39 | $2,471.94 | **48%** |

## Cost Breakdown by Category (Optimized, 5K calls/month)

| Category | Monthly Cost | % of Total | Description |
|----------|--------------|------------|-------------|
| Training Jobs | $2,534.30 | 95.0% | Cloud Run jobs, GCS storage/egress |
| Web Service | $134.00 | 5.0% | Cloud Run web, Snowflake, networking |
| Fixed Infrastructure | $2.09 | 0.1% | Storage, secrets, scheduler |
| **Total** | **$2,670.39** | **100%** | |

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

- **2025-12-02:** Updated cost estimates
  - Restructured cost estimate CSV to separate web service vs training job costs
  - Updated all documentation with revised cost figures
  - Added web-only scenario for users who browse without triggering training
  
- **2025-11-19:** Initial implementation
  - Set min_instances to 0
  - Documented GCS lifecycle policies
  - Implemented Snowflake query caching
  - Created cache management UI
  - Updated cost estimates
