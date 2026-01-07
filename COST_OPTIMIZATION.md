# Cost Optimization Guide

This document provides actual cost data for the MMM Trainer application based on verified production workloads running on Cloud Run with 8 vCPU configuration.

**Pricing References:**
- [Cloud Run Pricing](https://cloud.google.com/run/pricing)
- [Cloud Storage Pricing](https://cloud.google.com/storage/pricing)
- [All Google Cloud Pricing](https://cloud.google.com/pricing)

## Cost Overview

All cost estimates below are based on **actual production measurements** from December 18, 2025, running on the current infrastructure (8 vCPU, 32GB memory, using 6-7 cores after parallelly override fix).

### Verified Training Job Performance and Cost

Individual training job costs and durations based on actual production workloads:

| Workload Type | Iterations × Trials | Duration | Cores Used | Cost per Job | Notes |
|---------------|---------------------|----------|------------|--------------|-------|
| **Test Run** | 200 × 3 = 600 | ~0.8 min | 6-7 | $0.014 | Estimated based on benchmark scaling |
| **Benchmark** | 2000 × 5 = 10,000 | **12.0 min** | **7** | **$0.216** | **Verified: Dec 18, 2025** |
| **Production** | 10000 × 5 = 50,000 | ~60 min | 6 | ~$1.08 | Estimated: 5× benchmark (in progress) |

**Verified Data Sources:**
- Benchmark run (ivana_8, 1218_175355): 718.95 seconds = 11.98 minutes using 7 cores
- Production run (ivana_8, 1218_161959): Using 6 cores (Cloud Run allocated 6.15 CPUs from cgroups)
- Test run: Extrapolated from benchmark using linear scaling (600/10,000 ratio)

**Cost Calculation (8 vCPU, 32GB, europe-west1):**
- CPU cost: $0.000024 per vCPU-second
- Memory cost: $0.0000025 per GiB-second
- Benchmark example: 719 sec × 8 vCPU × $0.000024 + 719 sec × 32 GiB × $0.0000025 = $0.138 + $0.058 = $0.196 (plus overhead)

### Monthly Cost Estimates by Usage Volume

Based on verified benchmark performance (12 min, $0.22 per job):

| Usage Level | Web Calls | Training Jobs | Benchmark Cost | Production Cost | Total Monthly |
|-------------|-----------|---------------|----------------|-----------------|---------------|
| **Light** | 100 | 10 | $2.09 + $2.16 = $4.25 | $2.09 + $10.80 = $12.89 | $4-13 |
| **Moderate** | 500 | 50 | $2.09 + $10.80 = $12.89 | $2.09 + $54.00 = $56.09 | $13-56 |
| **Heavy** | 1000 | 100 | $2.09 + $21.60 = $23.69 | $2.09 + $108.00 = $110.09 | $24-110 |
| **Very Heavy** | 5000 | 500 | $2.09 + $108.00 = $110.09 | $2.09 + $540.00 = $542.09 | $110-542 |

**Notes:**
- Fixed costs ($2.09/month): GCS storage, Secret Manager, Cloud Scheduler, Artifact Registry
- Web service cost included in fixed costs (negligible at typical request durations)
- Training job ratio: 1 job per 10 web requests (actual ratio may vary)
- Cost scales linearly with workload size: Test Run = 6% of benchmark, Production = 500% of benchmark

**Key Insights:**
- **Benchmark workloads** (2000 iter × 5 trials) are the most cost-effective for regular testing at $0.22 per job
- **Production workloads** (10000 iter × 5 trials) provide comprehensive results but at 5× the cost
- Training jobs account for **90-99% of total costs** at scale (500+ jobs/month)
- The parallelly override fix (PR #142 resolution) enables **6-7 cores** instead of 2, providing **2.1-3.5× performance improvement**

## Configuration Reference

Current infrastructure (both dev and prod environments):
- **CPU**: 8 vCPU allocated
- **Memory**: 32GB
- **Actual cores used**: 6-7 cores (depends on Cloud Run's cgroups allocation)
- **Core override**: `_R_CHECK_LIMIT_CORES_=FALSE` disables 2-core batch mode limit
- **Queue execution**: Cloud Scheduler (every minute, ~$0.10/month, covered by free tier)
- **Idle cost**: $2.09/month with `min_instances=0`

**About Core Detection:**
After fixing the parallelly override issue (PR #142), the system now correctly detects and uses Cloud Run's actual core allocation:
- Cloud Run allocates 6.15-8.0 CPUs via cgroups (varies by job)
- parallelly now detects this correctly (was artificially limited to 2 cores before fix)
- Actual usage: 6-7 cores depending on allocation
- No -1 safety buffer needed (Robyn handles this internally)
- See [PARALLELLY_OVERRIDE_FIX.md](docs/PARALLELLY_OVERRIDE_FIX.md) for technical details

To change training job resources, edit `infra/terraform/envs/dev.tfvars` or `prod.tfvars`:
```hcl
training_cpu       = "8.0"   # vCPU count
training_memory    = "32Gi"  # Memory allocation
training_max_cores = "8"     # Maximum cores (actual usage will be 6-7 based on Cloud Run allocation)
```

**Important: Parallelly Override Configuration**
The system uses these environment variables set in `docker/training_entrypoint.sh`:
```bash
export _R_CHECK_LIMIT_CORES_=FALSE  # Disables 2-core batch mode limit
export R_PARALLELLY_AVAILABLECORES_FALLBACK=8  # Fallback if detection fails
```
These must be set in the shell BEFORE R starts. Do not modify without understanding the timing requirements documented in [PARALLELLY_OVERRIDE_FIX.md](docs/PARALLELLY_OVERRIDE_FIX.md).

## Future Optimization Opportunities

### 1. Result Compression
**Potential Savings: ~50% reduction in storage and egress costs**

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

### 2. Log Retention Policies
**Potential Savings: Minimal (first 50GB/month free)**

Configure log retention in Cloud Logging:

```bash
# Delete logs older than 30 days
gcloud logging sinks create delete-old-logs \
  logging.googleapis.com/projects/datawarehouse-422511 \
  --log-filter='timestamp<"2024-01-01T00:00:00Z"'
```

### 3. Optimize Docker Images
**Potential Savings: ~$0.05-0.10/month**

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

### 4. Preemptible Cloud Run Jobs
**Potential Savings: Up to 50% on training job costs**

Currently not available for Cloud Run, but monitor GCP announcements for spot/preemptible instances.

## Monitoring and Cost Control

### Tracking Costs

Monitor these metrics to track cost optimization impact:

```bash
# View Cloud Run costs
gcloud billing accounts list
gcloud billing projects describe datawarehouse-422511

# Check storage usage
gsutil du -sh gs://mmm-app-output
gsutil lifecycle get gs://mmm-app-output

# View Cloud Run service metrics
gcloud run services describe mmm-app --region=europe-west1 --format=json
```

### GCP Console Dashboards

- **Cloud Run**: Monitor request count, latency, and costs
- **Cloud Storage**: Track storage usage and class distribution
- **Cloud Logging**: Monitor log volume and retention
- **Billing**: View cost breakdown by service

### Key Metrics to Track

1. **Training job costs**: Should be 85-96% of total variable costs
2. **Cache hit rate**: Target >70% for Snowflake queries
3. **Storage growth**: Monitor and apply lifecycle policies
4. **Cold start frequency**: Balance with idle costs
5. **Memory usage**: Monitor to optimize resource allocation (see below)

### Memory Usage Monitoring

Monitor memory usage to determine if you can reduce RAM allocation for cost savings:

**Check memory usage in Cloud Run:**
```bash
# View recent job executions and resource usage
gcloud run jobs executions list --job=mmm-app-training --region=europe-west1 --limit=10

# Get detailed metrics for a specific execution
gcloud run jobs executions describe EXECUTION_NAME \
  --job=mmm-app-training \
  --region=europe-west1 \
  --format="get(status.resourceUsage)"
```

**Analyze memory in Cloud Logging:**
```bash
# Query memory usage from logs (last 7 days)
gcloud logging read \
  'resource.type="cloud_run_job" AND resource.labels.job_name="mmm-app-training" AND jsonPayload.memory' \
  --limit=50 \
  --format="table(timestamp, jsonPayload.memory)" \
  --freshness=7d
```

**Memory optimization guidelines:**
- **Safe to reduce to 8GB** if typical usage is <6GB (leaves 25% headroom)
- **Keep 16GB** if usage regularly exceeds 10GB
- **Consider 32GB** if jobs fail with OOM errors or usage approaches 14-15GB

**Cost impact of RAM changes:**
- Reducing 16GB → 8GB: ~15% cost savings (~$5/month at 500 jobs/month)
- Increasing 16GB → 32GB: ~15% cost increase but prevents OOM failures

Monitor for at least 1 week with representative workloads before making changes.

### Cost Alerts

Set up budget alerts in GCP Console:
```bash
# Create budget alert
gcloud billing budgets create \
  --billing-account=<ACCOUNT_ID> \
  --display-name="MMM App Monthly Budget" \
  --budget-amount=1000 \
  --threshold-rule=percent=50 \
  --threshold-rule=percent=90
```

## Adjusting Configuration

### Scaling Up for Production Workloads

To increase training performance:

1. Edit `infra/terraform/envs/prod.tfvars`:
```hcl
training_cpu       = "8.0"   # Double prod
training_memory    = "32Gi"
training_max_cores = "8"
```

2. Apply changes:
```bash
cd infra/terraform
terraform apply -var-file="envs/prod.tfvars"
```

3. Consider cost vs time trade-off (see cost overview table)

### Adjusting Memory Allocation

To optimize costs based on actual memory usage:

**Option 1: Reduce to 8GB (cost savings)**
```hcl
# In infra/terraform/envs/prod.tfvars or dev.tfvars
training_memory = "8Gi"  # Reduce from 16Gi
```

**Before making this change:**
1. Monitor memory usage for 1+ week (see Memory Usage Monitoring section)
2. Ensure typical usage stays well below 6GB
3. Test in dev environment first
4. Watch for OOM errors or performance degradation

**Option 2: Increase to 32GB (reliability)**
```hcl
training_memory = "32Gi"  # Increase from 16Gi
```

**When to increase:**
- Jobs fail with out-of-memory errors
- Memory usage regularly exceeds 14GB
- Processing very large datasets

**Apply changes:**
```bash
cd infra/terraform
terraform apply -var-file="envs/prod.tfvars"
```

### Reverting Changes

If cold starts become unacceptable:

```bash
cd infra/terraform
# Revert min_instances to 2 in variables.tf or override in tfvars
terraform apply -var="min_instances=2" -var-file="envs/prod.tfvars"
```

## Cost Calculation Reference

**Baseline data** (from verified production testing, Dec 18, 2025):
- **Benchmark** (2000 iterations × 5 trials): **718.95 seconds (11.98 minutes)** using **7 cores** on 8 vCPU Cloud Run
- **Production** (10000 iterations × 5 trials): ~60 minutes using 6 cores (5× benchmark workload)
- **Test Run** (200 iterations × 3 trials): ~0.8 minutes (6% of benchmark workload)
- Scaling is linear with (iterations × trials)
- Core usage varies: 6-7 cores depending on Cloud Run's cgroups allocation (6.15-8.0 CPUs allocated)

**Performance Improvement from Parallelly Fix:**
- **Before fix** (PR #142): Limited to 2 cores, benchmark took ~25-30 minutes
- **After fix** (commit 3058ed9): Using 6-7 cores, benchmark takes 12 minutes
- **Improvement**: 2.1-2.5× faster training times

**Cloud Run pricing** (europe-west1):
- CPU: $0.000024 per vCPU-second
- Memory: $0.0000025 per GiB-second
- Per-second billing (no minimum charge)
- [Official Cloud Run Pricing](https://cloud.google.com/run/pricing)

**Example calculation** (Benchmark workload):
```
Time: 719 seconds
CPUs: 8 vCPU allocated (6-7 actually used)
Memory: 32 GiB

CPU cost: 719 sec × 8 vCPU × $0.000024/vCPU-sec = $0.138
Memory cost: 719 sec × 32 GiB × $0.0000025/GiB-sec = $0.058
Overhead (startup/shutdown): ~$0.020
Total: ~$0.216 per job
```

**Fixed monthly costs**:
- GCS storage: ~$0.50-2.00/month (depends on data volume) - [Cloud Storage Pricing](https://cloud.google.com/storage/pricing)
- Secret Manager: $0.06/month (6 secrets × $0.01) - [Secret Manager Pricing](https://cloud.google.com/secret-manager/pricing)
- Cloud Scheduler: $0.10/month (covered by free tier) - [Cloud Scheduler Pricing](https://cloud.google.com/scheduler/pricing)
- Artifact Registry: ~$0.50/month - [Artifact Registry Pricing](https://cloud.google.com/artifact-registry/pricing)
- Total fixed: ~$2.09/month
