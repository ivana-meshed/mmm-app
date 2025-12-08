# Cost Optimization Implementation Guide

This document describes the cost reduction strategies implemented for the MMM Trainer application, as referenced in `Cost estimate.csv`.

## Cost Summary

| Scenario | Web Service | Training Jobs | Fixed Costs | Total |
|----------|-------------|---------------|-------------|-------|
| **Idle** | $0.00 | $0.00 | $2.09 | **$2.09** |
| 100 calls/month | $2.68 | $50.69 | $2.09 | **$55.46** |
| 500 calls/month | $13.40 | $253.43 | $2.09 | **$268.92** |
| 1,000 calls/month | $26.80 | $506.86 | $2.09 | **$535.75** |
| 5,000 calls/month | $134.00 | $2,534.30 | $2.09 | **$2,670.39** |

**Key insight:** Training jobs account for ~95% of variable costs at scale (1 training job per 10 web requests).

## Infrastructure: Queue Execution

### Cloud Scheduler for Background Queue Processing
**Cost: $0.10/month (included in free tier)**

The application uses Google Cloud Scheduler to process the training job queue in the background:

**Configuration:**
- **Schedule**: Every minute (`*/1 * * * *`)
- **Endpoint**: `${WEB_SERVICE_URL}?queue_tick=1&name={queue_name}`
  - Production: `?queue_tick=1&name=default`
  - Dev: `?queue_tick=1&name=default-dev`
- **Authentication**: OIDC token with dedicated service account (`robyn-queue-scheduler`)
- **Timeout**: 320 seconds per tick
- **Resource**: `google_cloud_scheduler_job.robyn_queue_tick` in `infra/terraform/main.tf`
- **Environment Variable**: `DEFAULT_QUEUE_NAME` set via `var.queue_name` in terraform

**How it works:**
1. Cloud Scheduler triggers the web service every minute with `?queue_tick=1` parameter
2. Web service reads `DEFAULT_QUEUE_NAME` from environment (set to `var.queue_name` in terraform)
3. Web service processes one queue tick: launches pending jobs or updates running job status
4. Queue state is persisted in GCS at `gs://{bucket}/robyn-queues/{queue_name}/queue.json`
5. Jobs execute independently as Cloud Run Jobs

**Benefits:**
- Queue processes automatically without requiring user to be on the page
- Reliable execution even if browser is closed
- Separate queues for dev and prod environments prevent interference
- Minimal cost (~$0.10/month, covered by Cloud Scheduler free tier of 3 jobs)

**Cost Impact:**
- Cloud Scheduler: $0.10/month (first 3 jobs free, then $0.10/job/month)
- Already deployed and included in infrastructure
- No additional costs for queue processing itself

## Implemented Optimizations

### 1. ✅ Reduced min_instances to 0 (IMPLEMENTED)
**Savings: $42.94/month (95% of idle cost)**

Changed `min_instances` from 2 to 0 in `infra/terraform/variables.tf`.

**Impact:**
- Eliminates always-on Cloud Run instances
- Reduces idle cost from $45.03/month to $2.09/month
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

### With all optimizations applied (Current):

| Scenario | Original Cost | Optimized Cost | Savings | % Reduction |
|----------|---------------|----------------|---------|-------------|
| Idle | $45.03 | $2.09 | $42.94 | 95% |
| 100 calls | $148.00 | $55.46 | $92.54 | 63% |
| 500 calls | $519.56 | $268.92 | $250.64 | 48% |
| 1,000 calls | $1,073.39 | $535.75 | $537.64 | 50% |
| 5,000 calls | $5,142.33 | $2,670.39 | $2,471.94 | 48% |

### Web-Only Scenario (No Training Jobs):

If users only browse/query without triggering training:

| Scenario | Monthly Cost |
|----------|-------------|
| Idle | $2.09 |
| 100 calls | $4.77 |
| 500 calls | $15.49 |
| 1,000 calls | $28.89 |
| 5,000 calls | $136.09 |

## Implementation Priority

1. **High Priority (Implemented)**
   - ✅ Set min_instances to 0
   - ✅ Document GCS lifecycle policies
   - ✅ Implement Snowflake query caching (two-tier: in-memory + GCS)
   - ✅ Create cache management UI
   - ✅ Training job right-sizing (4 vCPU/16GB vs 8/32)

2. **Medium Priority (Recommended Next)**
   - Apply GCS lifecycle policies to production bucket
   - Monitor cache hit rate and adjust TTLs if needed
   - Implement result compression

3. **Low Priority (Future)**
   - Optimize Docker images
   - Configure log retention
   - Monitor for preemptible/spot instances

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
