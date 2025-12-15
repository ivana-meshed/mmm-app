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

## Scenario Analysis

### Scenario 1: Dev vs Prod Workflow Cost Comparison

The dev and prod environments use different training job configurations for cost optimization. This scenario compares the monthly costs for typical usage patterns.

**Environment Configurations:**
- **Dev** (ci-dev.yml): 2 vCPU, 8GB memory for training jobs
- **Prod** (ci.yml): 4 vCPU, 16GB memory for training jobs
- **Web Service**: Both use 2 vCPU, 4GB memory (same cost)

**Monthly Cost Breakdown:**

| Usage Scenario | Dev Environment | Prod Environment | Difference | % More for Prod |
|----------------|-----------------|------------------|------------|-----------------|
| **Idle** | $2.09 | $2.09 | $0.00 | 0% |
| **100 calls/month** (10 training jobs) | $3.60 | $5.09 | +$1.48 | 41% |
| **500 calls/month** (50 training jobs) | $9.65 | $17.07 | +$7.42 | 77% |
| **1,000 calls/month** (100 training jobs) | $17.21 | $32.05 | +$14.83 | 86% |
| **5,000 calls/month** (500 training jobs) | $77.70 | $151.87 | +$74.16 | 95% |

**Cost Components (example: 500 calls/month):**

**Dev Environment ($9.65 total):**
- Web Service: $0.15
- Training Jobs (50 runs): $7.42
- Fixed Costs: $2.09

**Prod Environment ($17.07 total):**
- Web Service: $0.15
- Training Jobs (50 runs): $14.83
- Fixed Costs: $2.09

**Key Insights:**
- Dev environment is 41-95% cheaper depending on usage
- Cost difference grows with higher usage (training job costs dominate)
- Prod provides ~45% faster training at ~2x the cost
- Dev is ideal for experimentation and development
- Prod is better for production workloads where speed matters

### Scenario 2: Training Job Sizing Analysis

This scenario analyzes the cost and time trade-offs for different machine sizes when running a large training job with **10,000 iterations and 10 trials**.

**Baseline Data:**
- Configuration: Dev (2 vCPU, 8GB)
- Training parameters: 2,000 iterations, 5 trials, daily data (2024-01-01 to 2025-12-02)
- Actual runtime: 1,983 seconds (33 minutes)

**Extrapolation for 10,000 iterations × 10 trials:**

Work scales linearly with iterations × trials:
- Baseline work: 2,000 × 5 = 10,000 units
- Target work: 10,000 × 10 = 100,000 units
- Scaling factor: 10x

| Configuration | vCPU | Memory | Duration | Duration (hours) | Cost per Run | Cost per 100 Runs | Cost per Month (500 runs) |
|---------------|------|--------|----------|------------------|--------------|-------------------|---------------------------|
| **Dev Config** | 2 | 8GB | 19,830 sec (5.5 hrs) | 5.51 | $1.48 | $148.33 | $741.64 |
| **Prod Config** | 4 | 16GB | 11,017 sec (3.1 hrs) | 3.06 | $1.65 | $164.81 | $824.05 |
| **2x Prod** | 8 | 32GB | 5,832 sec (1.6 hrs) | 1.62 | $1.75 | $174.50 | $872.52 |
| **4x Prod** | 16 | 64GB | 3,005 sec (0.8 hrs) | 0.83 | $1.80 | $179.79 | $898.96 |

**Performance vs Cost Trade-offs:**

| Configuration | Time Savings vs Dev | Cost Premium vs Dev | Cost per Hour Saved |
|---------------|---------------------|---------------------|---------------------|
| **Dev Config** | Baseline | Baseline | - |
| **Prod Config** | 44% faster (2.5 hrs saved) | +11% cost | $0.07 per hour saved |
| **2x Prod** | 71% faster (3.9 hrs saved) | +18% cost | $0.07 per hour saved |
| **4x Prod** | 85% faster (4.7 hrs saved) | +21% cost | $0.07 per hour saved |

**Recommendations by Use Case:**

1. **Development & Experimentation** → **Dev Config (2 vCPU, 8GB)**
   - Best for iterative development
   - Lowest cost per run ($1.48)
   - Acceptable for overnight or background training
   - 5.5 hour runtime is manageable for non-urgent work

2. **Production Workloads** → **Prod Config (4 vCPU, 16GB)**
   - Good balance of speed and cost
   - 3 hour runtime fits within a work session
   - Only 11% more expensive than dev
   - Current production default

3. **Time-Critical Analysis** → **2x Prod Config (8 vCPU, 32GB)**
   - 1.6 hour runtime for quick turnaround
   - Useful for urgent stakeholder requests
   - 18% more expensive than dev
   - Consider for high-value, time-sensitive work

4. **Ultra-Fast Iteration** → **4x Prod Config (16 vCPU, 64GB)**
   - 50 minute runtime for rapid experimentation
   - Best for interactive/exploratory analysis
   - 21% more expensive than dev
   - Diminishing returns on cost efficiency

**Cost-Efficiency Analysis:**

- All configurations have similar cost per hour saved (~$0.07/hour)
- The marginal cost increase is relatively small (11-21%)
- Time savings are substantial (44-85%)
- **Recommendation**: Use larger machines when:
  - Results are needed urgently (stakeholder meetings, decisions)
  - Running multiple experiments in a day
  - Developer time is more valuable than compute cost
  - Interactive exploration requires fast feedback

**When to Scale Up:**

```
Developer hourly cost: ~$50-100/hour
Compute savings: ~$0.30 for 4x faster execution
Time saved: 4.7 hours

If saving 4.7 hours of developer time = $235-470 value
Additional compute cost = $0.30
ROI = 780-1,560x return on investment
```

**Conclusion**: For time-sensitive work, the additional compute cost is negligible compared to the value of faster results. Choose configuration based on urgency, not just cost.
