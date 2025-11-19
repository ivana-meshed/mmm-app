# Cost Optimization Implementation Guide

This document describes the cost reduction strategies implemented for the MMM Trainer application, as referenced in `Cost estimate.csv`.

## Implemented Optimizations

### 1. ✅ Reduced min_instances to 0 (IMPLEMENTED)
**Savings: $42.34/month (94% of idle cost)**

Changed `min_instances` from 2 to 0 in `infra/terraform/variables.tf`.

**Impact:**
- Eliminates always-on Cloud Run instances
- Reduces idle cost from $45.03/month to $2.69/month
- Trade-off: Adds 1-3 second cold start latency on first request

**To apply:**
```bash
cd infra/terraform
terraform apply -var-file="envs/prod.tfvars"
```

### 2. ✅ GCS Lifecycle Policies (DOCUMENTED)
**Savings: ~$0.78/month on storage, up to 80% on historical data**

Created `infra/terraform/storage.tf` with lifecycle policy configuration.

**Policy Rules:**
- Move data to Nearline after 30 days (50% cheaper: $0.010/GB vs $0.020/GB)
- Move data to Coldline after 90 days (80% cheaper: $0.004/GB vs $0.020/GB)
- Delete old queue data after 365 days

**To apply manually:**
```bash
cat > lifecycle.json << 'EOF'
{
  "lifecycle": {
    "rule": [
      {
        "action": {"type": "SetStorageClass", "storageClass": "NEARLINE"},
        "condition": {
          "age": 30,
          "matchesPrefix": ["robyn/", "datasets/", "training-data/"]
        }
      },
      {
        "action": {"type": "SetStorageClass", "storageClass": "COLDLINE"},
        "condition": {
          "age": 90,
          "matchesPrefix": ["robyn/", "datasets/", "training-data/"]
        }
      },
      {
        "action": {"type": "Delete"},
        "condition": {
          "age": 365,
          "matchesPrefix": ["robyn-queues/"]
        }
      }
    ]
  }
}
EOF

gcloud storage buckets update gs://mmm-app-output --lifecycle-file=lifecycle.json
```

### 3. ✅ Request Caching for Snowflake (IMPLEMENTED)
**Savings: Up to 70% of Snowflake costs when using cached data**

Implemented a two-tier caching strategy for Snowflake queries.

**What was implemented:**

1. **Created `app/utils/snowflake_cache.py`**
   - In-memory cache (TTL: 1 hour) for immediate access
   - GCS persistent cache (TTL: 24 hours) for durability
   - Automatic query normalization (ignores whitespace/case differences)

2. **Updated `app/app_shared.py`**
   - Modified `run_sql()` function to use caching by default
   - Added `use_cache` parameter for fine-grained control
   - Automatically initializes cache on application startup

3. **Created Cache Management UI** (`app/nav/Cache_Management.py`)
   - View cache statistics (in-memory and GCS)
   - Clear cache when needed
   - Cost savings calculator
   - Cache hit rate monitoring

**How it works:**

```python
# Queries are automatically cached
df = run_sql("SELECT * FROM table")  # First call: hits Snowflake
df = run_sql("SELECT * FROM table")  # Second call: uses cache

# Disable caching for specific queries
df = run_sql("INSERT INTO table VALUES ...", use_cache=False)
```

**Cache behavior:**
- Tier 1: In-memory cache (fast, 1-hour TTL)
- Tier 2: GCS cache (persistent, 24-hour TTL)
- Queries are normalized (whitespace/case-insensitive)
- Write operations automatically bypass cache

**Expected savings with 70% cache hit rate:**
- 100 calls/month: $10.00 → $3.00 (save $7/month)
- 500 calls/month: $50.00 → $15.00 (save $35/month)
- 1000 calls/month: $100.00 → $30.00 (save $70/month)
- 5000 calls/month: $500.00 → $150.00 (save $350/month)

### 4. 📝 Result Compression (RECOMMENDATION)
**Savings: ~50% reduction in storage and egress costs**

Compress training results before uploading to GCS.

**Implementation in `r/run_all.R`:**

```r
# After model training, compress results
library(zip)

# Compress RDS files
zip::zip(
  zipfile = "results.zip",
  files = c("OutputCollect.RDS", "InputCollect.RDS", "plots/", "one_pagers/")
)

# Upload compressed file instead of individual files
# This reduces both storage and egress costs
```

**Expected savings:**
- Storage: 50% reduction (e.g., $12.80 → $6.40 at 5000 calls/month)
- Egress: 50% reduction (e.g., $60.00 → $30.00 at 5000 calls/month)

### 5. 📝 Log Retention Policies (RECOMMENDATION)
**Savings: Minimal (first 50GB/month free)**

Configure log retention in Cloud Logging:

```bash
# Delete logs older than 30 days
gcloud logging sinks create delete-old-logs \
  logging.googleapis.com/projects/datawarehouse-422511 \
  --log-filter='timestamp<"2024-01-01T00:00:00Z"'
```

### 6. 📝 Optimize Docker Images (RECOMMENDATION)
**Savings: ~$0.05-0.10/month**

Reduce Artifact Registry storage by optimizing Docker images:

```dockerfile
# Use multi-stage builds
FROM python:3.11-slim as builder
# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.11-slim
# Copy only necessary files
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
```

### 7. 📝 Preemptible Cloud Run Jobs (FUTURE)
**Savings: Up to 50% on training job costs**

Currently not available for Cloud Run, but monitor GCP announcements for spot/preemptible instances.

### 8. 📝 Regional vs Multi-Regional GCS (CURRENT STATE)
**Cost: Optimal**

Currently using regional GCS (europe-west1) which is already the most cost-effective option for this use case.

## Updated Cost Estimates with Optimizations

### With min_instances=0 and 70% Snowflake cache hit rate:

| Scenario | Original Cost | Optimized Cost | Savings | % Reduction |
|----------|---------------|----------------|---------|-------------|
| Idle | $45.03 | $2.69 | $42.34 | 94% |
| 100 calls | $148.00 | $98.66 | $49.34 | 33% |
| 500 calls | $519.56 | $434.56 | $85.00 | 16% |
| 1000 calls | $1,073.39 | $933.39 | $140.00 | 13% |
| 5000 calls | $5,142.33 | $4,792.33 | $350.00 | 7% |

### Additional savings with compression (estimated):

| Scenario | Optimized Cost | With Compression | Total Savings | % Reduction |
|----------|----------------|------------------|---------------|-------------|
| Idle | $2.69 | $2.30 | $42.73 | 95% |
| 100 calls | $98.66 | $97.66 | $50.34 | 34% |
| 500 calls | $434.56 | $430.06 | $89.50 | 17% |
| 1000 calls | $933.39 | $924.39 | $149.00 | 14% |
| 5000 calls | $4,792.33 | $4,747.33 | $395.00 | 8% |

## Implementation Priority

1. **High Priority (Implemented)**
   - ✅ Set min_instances to 0
   - ✅ Document GCS lifecycle policies
   - ✅ Implement Snowflake query caching (two-tier: in-memory + GCS)
   - ✅ Create cache management UI

2. **Medium Priority (Recommended Next)**
   - Apply GCS lifecycle policies to production bucket
   - Monitor cache hit rate and adjust TTLs if needed
   - Implement result compression

3. **Low Priority (Future)**
   - Optimize Docker images
   - Configure log retention
   - Monitor for preemptible instances

## Rollout Plan

1. **Immediate (Today)**
   - Deploy min_instances=0 change to dev environment
   - Monitor cold start latency
   - If acceptable, deploy to production

2. **Week 1**
   - Apply GCS lifecycle policies
   - Monitor storage costs

3. **Week 2-3**
   - Implement Snowflake caching
   - Test cache hit rate
   - Monitor Snowflake costs

4. **Week 4**
   - Implement result compression
   - Monitor storage and egress costs

## Monitoring

Track these metrics to measure optimization impact:

```bash
# Cloud Run costs
gcloud billing accounts list
gcloud billing accounts get-iam-policy <ACCOUNT_ID>

# Storage costs
gsutil du -s gs://mmm-app-output
gsutil lifecycle get gs://mmm-app-output

# Snowflake costs
# Check Snowflake UI for compute credit usage
```

## Reverting Changes

If cold starts become unacceptable:

```bash
cd infra/terraform
# Revert min_instances to 2 in variables.tf or override in tfvars
terraform apply -var="min_instances=2" -var-file="envs/prod.tfvars"
```
