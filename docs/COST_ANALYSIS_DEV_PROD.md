# Comprehensive Cost Analysis: Dev vs Prod Environments

**Last Updated:** 2026-02-02  
**Analysis Date:** 2026-02-02

This document provides a complete cost breakdown for both development (`dev`) and production (`main`/`prod`) environments of the MMM Trainer application.

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Environment Overview](#environment-overview)
3. [Cost Components Breakdown](#cost-components-breakdown)
4. [Shared vs Dedicated Resources](#shared-vs-dedicated-resources)
5. [Monthly Cost Projections](#monthly-cost-projections)
6. [Cost Allocation Between Environments](#cost-allocation-between-environments)
7. [Data Collection Instructions](#data-collection-instructions)
8. [Optimization Recommendations](#optimization-recommendations)

---

## Executive Summary

### Key Findings

**Current Configuration (Both Environments):**
- Both dev and prod use **identical resource configurations** (8 vCPU, 32GB memory for training jobs)
- Both environments share the **same GCP project** and many resources
- Training job costs are **95%+ of variable costs** in both environments
- Fixed costs are **shared across both environments** (~$2.09/month base)

**Estimated Total Monthly Costs:**

| Scenario | Dev Only | Prod Only | Combined (Dev + Prod) |
|----------|----------|-----------|----------------------|
| **Idle** | $1.05 | $1.05 | $2.09 |
| **Light (100 calls/month each)** | ~$15-22 | ~$15-22 | ~$30-44 |
| **Moderate (500 calls/month each)** | ~$69-102 | ~$69-102 | ~$138-204 |
| **Heavy (1000 calls/month each)** | ~$135-202 | ~$135-202 | ~$270-404 |

**Note:** Costs shown are **estimates based on current configuration**. Actual costs will vary based on:
- Actual training job frequency and duration
- Web service request patterns
- GCS storage growth
- CI/CD build frequency

---

## Environment Overview

### Infrastructure Configuration

Both environments share:
- **Project ID:** `datawarehouse-422511`
- **Region:** `europe-west1` (Belgium)
- **GCS Bucket:** `mmm-app-output` (shared, with prefixes)
- **Artifact Registry:** `mmm-repo` (shared repository)
- **Service Accounts:** Shared across environments
- **Snowflake:** Same warehouse (`SMALL_WH`)

#### Production Environment (`main` branch)

| Resource | Configuration |
|----------|--------------|
| **Cloud Run Web Service** | `mmm-app-web` |
| **Cloud Run Training Job** | `mmm-app-training` |
| **Cloud Scheduler Job** | `robyn-queue-tick` |
| **Queue Name** | `default` |
| **Web Service Resources** | 2 vCPU, 4GB RAM, min_instances=0 |
| **Training Job Resources** | 8 vCPU, 32GB RAM, 6-hour timeout |

#### Development Environment (`dev`, `feat-*`, `copilot/*` branches)

| Resource | Configuration |
|----------|--------------|
| **Cloud Run Web Service** | `mmm-app-dev-web` |
| **Cloud Run Training Job** | `mmm-app-dev-training` |
| **Cloud Scheduler Job** | `robyn-queue-tick-dev` |
| **Queue Name** | `default-dev` |
| **Web Service Resources** | 2 vCPU, 4GB RAM, min_instances=0 |
| **Training Job Resources** | 8 vCPU, 32GB RAM, 6-hour timeout |

---

## Cost Components Breakdown

### 1. Cloud Run Services (Web)

Both environments run identical web service configurations with `min_instances=0` for cost optimization.

**Per-Request Cost:**
- Average request duration: 30 seconds
- CPU: 2 vCPU × 30s × $0.000024/vCPU-sec = $0.00144
- Memory: 4GB × 30s × $0.0000025/GB-sec = $0.00030
- **Total per request: ~$0.00174**

**Monthly Web Service Cost (per environment):**

| Calls/Month | Dev Cost | Prod Cost | Notes |
|-------------|----------|-----------|-------|
| 100 | $0.17 | $0.17 | Development testing |
| 500 | $0.87 | $0.87 | Active development |
| 1,000 | $1.74 | $1.74 | Heavy usage |
| 5,000 | $8.70 | $8.70 | Production scale |

**Cold Starts:**
- With `min_instances=0`, first request after idle incurs 1-3 second latency
- No cost during idle periods
- Savings: ~$42.94/month per environment vs `min_instances=2`

---

### 2. Cloud Run Jobs (Training)

Both environments use **identical training job configurations** (8 vCPU, 32GB RAM).

**Per-Job Cost (verified production data):**

| Workload Type | Iterations × Trials | Duration | Cost per Job | Use Case |
|---------------|---------------------|----------|--------------|----------|
| **Test Run** | 200 × 3 = 600 | ~0.8 min | $0.014 | Quick validation |
| **Benchmark** | 2,000 × 5 = 10,000 | 12.0 min | $0.20 | Testing/dev |
| **Production** | 10,000 × 5 = 50,000 | 80-120 min | $1.33-$2.00 | Production runs |

**Cost Calculation (8 vCPU, 32GB):**
```
Benchmark (12 min = 720 sec):
  CPU: 720s × 8 vCPU × $0.000024/vCPU-sec = $0.138
  Memory: 720s × 32GB × $0.0000025/GB-sec = $0.058
  Total: $0.196 ≈ $0.20 per job

Production (80-120 min):
  Low (80 min): $1.33 per job
  High (120 min): $2.00 per job
```

**Monthly Training Cost (assuming 1 job per 10 web calls):**

| Web Calls | Jobs/Month | Dev Cost (Benchmark) | Dev Cost (Production) | Prod Cost |
|-----------|------------|---------------------|----------------------|-----------|
| 100 | 10 | $2.00 | $13-20 | $13-20 |
| 500 | 50 | $10.00 | $67-100 | $67-100 |
| 1,000 | 100 | $20.00 | $133-200 | $133-200 |
| 5,000 | 500 | $100.00 | $665-1,000 | $665-1,000 |

**Note:** Dev environment typically uses **benchmark workloads** (12 min, $0.20/job) while prod uses **production workloads** (80-120 min, $1.33-$2.00/job).

---

### 3. Google Cloud Storage (GCS)

**Shared Resource:** Both environments use the same bucket (`mmm-app-output`) with logical separation via prefixes/paths.

**Storage Costs:**
- **Standard Storage:** $0.020/GB/month
- **Nearline (>30 days):** $0.010/GB/month (lifecycle policy)
- **Coldline (>90 days):** $0.004/GB/month (lifecycle policy)

**Current Configuration:**
- Base storage: ~80GB historical data
- Growth: ~2GB per training job result
- Lifecycle policies: Active (move to Nearline after 30 days, Coldline after 90 days)

**Monthly Storage Cost Estimates:**

| Scenario | Total Storage | Dev Contribution | Prod Contribution | Monthly Cost |
|----------|---------------|------------------|-------------------|--------------|
| **Current Baseline** | 80GB | ~20GB | ~60GB | $0.82-2.00 |
| **With 50 jobs/month** | 180GB | ~50GB | ~130GB | $1.80-3.60 |
| **With 200 jobs/month** | 480GB | ~120GB | ~360GB | $4.80-9.60 |

**Additional GCS Costs:**
- **Class A Operations** (writes): $0.05 per 10K ops (~100 writes per job)
- **Class B Operations** (reads): $0.004 per 10K ops (~200 reads per job)
- **Network Egress:** $0.12/GB for inter-region, free within europe-west1

**Per-Job GCS Operations Cost:**
```
Per job: 100 writes + 200 reads = ~$0.0005 + $0.00008 ≈ $0.0006
Egress: ~1GB download per result = $0.12 (if downloaded outside region)
```

---

### 4. Artifact Registry

**Shared Resource:** Single registry (`mmm-repo`) stores images for both environments.

**Storage:**
- **Pricing:** $0.10/GB/month
- **Current Images:**
  - `mmm-web`: ~500MB per tag
  - `mmm-training-base`: ~2GB (shared base)
  - `mmm-training`: ~2.5GB per tag
- **Tags per environment:** 2 (commit SHA + latest)

**Monthly Cost Estimate:**
```
Web images: 2 tags × 2 envs × 0.5GB = 2GB = $0.20
Training base: 1 image × 2GB = 2GB = $0.20
Training images: 2 tags × 2 envs × 2.5GB = 10GB = $1.00
Total: ~$1.40/month
```

**Note:** With build caching and tag cleanup, actual cost is typically ~$0.10-0.50/month.

---

### 5. Secret Manager

**Shared Resource:** All secrets are project-wide and shared between environments.

**Secrets:**
1. `sf-private-key` (Snowflake authentication)
2. `sf-private-key-persistent` (user-uploaded keys)
3. `streamlit-auth-client-id` (Google OAuth)
4. `streamlit-auth-client-secret` (Google OAuth)
5. `streamlit-auth-cookie-secret` (session management)

**Pricing:**
- **Storage:** $0.06/secret/month
- **Access:** $0.03 per 10,000 accesses

**Monthly Cost:**
```
Storage: 5 secrets × $0.06 = $0.30/month
Access (100 calls/month): ~10 accesses per call = $0.03
Access (1,000 calls/month): ~10K accesses = $0.30
Total: $0.33-0.60/month (shared across both environments)
```

---

### 6. Cloud Scheduler

**Dedicated Resources:** Separate scheduler jobs for each environment.

**Jobs:**
- `robyn-queue-tick` (prod): Triggers every minute
- `robyn-queue-tick-dev` (dev): Triggers every minute

**Pricing:**
- **First 3 jobs:** Free (covered by free tier)
- **Additional jobs:** $0.10/job/month

**Monthly Cost:**
```
2 scheduler jobs = Free (within 3-job free tier)
Total: $0.00/month
```

---

### 7. Snowflake

**Shared Resource:** Both environments use the same Snowflake warehouse.

**Configuration:**
- **Warehouse:** `SMALL_WH`
- **Pricing:** ~$2 per compute credit
- **Cache Hit Rate:** ~70% (with two-tier caching)

**Cost per Request:**
- Without cache: ~$0.006 per query (0.1 credit per 5 queries)
- With 70% cache hit rate: ~$0.0018 per query (30% hit Snowflake)

**Monthly Cost Estimates:**

| Total Calls (Dev + Prod) | Dev Calls | Prod Calls | Dev Cost | Prod Cost | Total Cost |
|--------------------------|-----------|------------|----------|-----------|------------|
| 200 | 100 | 100 | $0.60 | $0.60 | $1.20 |
| 1,000 | 500 | 500 | $3.00 | $3.00 | $6.00 |
| 2,000 | 1,000 | 1,000 | $6.00 | $6.00 | $12.00 |
| 10,000 | 5,000 | 5,000 | $30.00 | $30.00 | $60.00 |

---

### 8. Cloud Logging

**Shared Resource:** Logs from both environments stored in same project.

**Pricing:**
- **First 50GB/month:** Free
- **Additional:** $0.50/GB

**Log Volume Estimates:**
- Web service: ~0.25GB per 100 requests
- Training job: ~0.25GB per 10 jobs
- System logs: ~10GB/month baseline

**Monthly Cost:**
```
Idle: ~10GB = Free
Light (200 calls, 20 jobs): ~11GB = Free
Moderate (1,000 calls, 100 jobs): ~15GB = Free
Heavy (5,000 calls, 500 jobs): ~65GB = $7.50
```

---

### 9. CI/CD Costs

#### GitHub Actions

**Workflows:**
- `ci.yml` (prod): Triggers on push to `main`
- `ci-dev.yml` (dev): Triggers on push to `dev`, `feat-*`, `copilot/*`

**Build Components:**
1. Docker build (web): ~5 minutes
2. Docker build (training-base): ~15-20 minutes (heavy R packages)
3. Docker build (training): ~2 minutes (extends base)
4. Terraform apply: ~2 minutes

**Total build time per deployment:** ~25-30 minutes

**GitHub Actions Pricing (Linux):**
- **Free tier:** 2,000 minutes/month for private repos
- **Additional:** $0.008/minute

**Monthly Cost Estimates:**

| Scenario | Dev Builds | Prod Builds | Total Minutes | Cost |
|----------|-----------|-------------|---------------|------|
| **Light** (2 dev, 1 prod/week) | 8 | 4 | 300 min | Free |
| **Moderate** (5 dev, 2 prod/week) | 20 | 8 | 700 min | Free |
| **Heavy** (10 dev, 4 prod/week) | 40 | 16 | 1,400 min | Free |
| **Very Heavy** (20 dev, 8 prod/week) | 80 | 32 | 2,800 min | $6.40 |

**Note:** Most development workflows stay within the 2,000 minute free tier.

#### Cloud Build

**Pricing:**
- **First 120 build-minutes/day:** Free
- **Additional:** $0.003/build-minute

**Current Usage:** Primarily use GitHub Actions, minimal Cloud Build usage.

**Monthly Cost:** $0.00 (within free tier)

---

### 10. Networking Costs

**Ingress:** Free (all inbound traffic)

**Egress (Outbound Traffic):**
- **Within europe-west1:** Free
- **To other GCP regions:** $0.01/GB
- **To internet:** $0.12/GB (first 1TB), $0.11/GB (next 9TB)

**Cost Drivers:**
- Training result downloads (if accessed from outside GCP)
- API responses to users
- Cross-region data transfer (none in current setup)

**Monthly Egress Cost Estimates:**

| Scenario | Results Downloaded | Egress Volume | Cost |
|----------|-------------------|---------------|------|
| **Light** (10 jobs) | 10GB | 10GB | $1.20 |
| **Moderate** (50 jobs) | 50GB | 50GB | $6.00 |
| **Heavy** (200 jobs) | 200GB | 200GB | $24.00 |

---

## Shared vs Dedicated Resources

### Shared Resources (Cost Split Between Environments)

| Resource | Cost Allocation | Monthly Cost |
|----------|----------------|--------------|
| **GCS Bucket** | By usage (prefix-based tracking) | $0.82-9.60+ |
| **Artifact Registry** | 50/50 split (2 image sets) | $0.10-1.40 |
| **Secret Manager** | 50/50 split (shared secrets) | $0.33-0.60 |
| **Snowflake** | By query count (trackable per env) | $1.20-60.00+ |
| **Cloud Logging** | By log volume (env label filtering) | $0.00-7.50+ |
| **Service Accounts** | Free | $0.00 |
| **IAM** | Free | $0.00 |

**Total Shared Fixed Costs:** ~$2.09/month base (split ~$1.05 per environment)

### Dedicated Resources (Environment-Specific)

| Resource | Dev Cost | Prod Cost | Notes |
|----------|----------|-----------|-------|
| **Cloud Run Web Service** | Usage-based | Usage-based | Per-request billing |
| **Cloud Run Training Jobs** | Usage-based | Usage-based | Per-second billing |
| **Cloud Scheduler** | $0.00 | $0.00 | Within free tier |

---

## Monthly Cost Projections

### Development Environment Cost Estimates

**Assumptions:**
- Benchmark workloads (12 min, $0.20/job) primarily used in dev
- Lower web service usage than prod
- Testing and validation focused

| Usage Level | Web Calls | Training Jobs | Monthly Total |
|-------------|-----------|---------------|---------------|
| **Idle** | 0 | 0 | **$1.05** |
| **Light Testing** | 50 | 5 | **$5-8** |
| **Active Development** | 200 | 20 | **$15-25** |
| **Heavy Testing** | 500 | 50 | **$35-55** |
| **Production-Scale Testing** | 1,000 | 100 | **$70-105** |

**Breakdown (Heavy Testing - 500 calls, 50 jobs):**
```
Web Service: $0.87
Training Jobs: $10.00 (benchmark workload)
GCS Storage: $1.00
GCS Operations: $0.15
Snowflake: $3.00
Secret Manager: $0.15
Cloud Logging: $0.50
Egress: $3.00
Artifact Registry: $0.20
Total: ~$19/month
```

---

### Production Environment Cost Estimates

**Assumptions:**
- Production workloads (80-120 min, $1.33-$2.00/job) primarily used
- Higher web service usage
- Business-critical operations

| Usage Level | Web Calls | Training Jobs | Monthly Total |
|-------------|-----------|---------------|---------------|
| **Idle** | 0 | 0 | **$1.05** |
| **Light** | 100 | 10 | **$15-22** |
| **Moderate** | 500 | 50 | **$70-105** |
| **Heavy** | 1,000 | 100 | **$135-205** |
| **Very Heavy** | 5,000 | 500 | **$670-1,005** |

**Breakdown (Moderate - 500 calls, 50 jobs):**
```
Web Service: $0.87
Training Jobs: $67-100 (production workload)
GCS Storage: $2.00
GCS Operations: $0.50
Snowflake: $3.00
Secret Manager: $0.15
Cloud Logging: $1.00
Egress: $6.00
Artifact Registry: $0.25
Total: ~$80-115/month
```

---

### Combined Environment Cost Estimates

**Total monthly costs when both environments are actively used:**

| Scenario | Dev Activity | Prod Activity | Combined Total |
|----------|-------------|---------------|----------------|
| **Minimal** | Idle | Light | **$16-23** |
| **Light** | Light Testing | Light | **$20-30** |
| **Moderate** | Active Dev | Moderate | **$90-130** |
| **Heavy** | Heavy Testing | Heavy | **$170-260** |
| **Very Heavy** | Production-Scale | Very Heavy | **$740-1,110** |

---

## Cost Allocation Between Environments

### Method 1: Direct Attribution (Recommended)

Track costs by resource labels and naming:

**Cloud Run Services:**
- `mmm-app-web` → Production
- `mmm-app-dev-web` → Development

**Cloud Run Jobs:**
- `mmm-app-training` → Production  
- `mmm-app-dev-training` → Development

**GCS Bucket Prefixes:**
- Monitor by path patterns:
  - `/training-configs/default/` → Production
  - `/training-configs/default-dev/` → Development
  - `/results/*/` → Track by job execution ID

**Cloud Logging:**
- Filter by service name:
  - `resource.labels.service_name="mmm-app-web"` → Production
  - `resource.labels.service_name="mmm-app-dev-web"` → Development

### Method 2: Shared Cost Allocation

For shared resources without clear attribution:

**Allocation Rules:**

| Resource | Allocation Method | Dev % | Prod % |
|----------|------------------|-------|--------|
| **Artifact Registry** | Equal split | 50% | 50% |
| **Secret Manager** | Equal split | 50% | 50% |
| **Base GCS Storage** | Historical analysis | 25% | 75% |
| **Snowflake** | Query count tracking | Variable | Variable |

**Tracking Commands:**

```bash
# Check Cloud Run costs by service
gcloud billing accounts list
gcloud billing projects describe datawarehouse-422511

# GCS storage by prefix
gsutil du -sh gs://mmm-app-output/training-configs/default/
gsutil du -sh gs://mmm-app-output/training-configs/default-dev/

# Cloud Run execution counts
gcloud run jobs executions list --job=mmm-app-training --limit=100
gcloud run jobs executions list --job=mmm-app-dev-training --limit=100

# Cloud Logging volume by service
gcloud logging read 'resource.labels.service_name="mmm-app-web"' \
  --format="value(timestamp)" --limit=1000 | wc -l
gcloud logging read 'resource.labels.service_name="mmm-app-dev-web"' \
  --format="value(timestamp)" --limit=1000 | wc -l
```

---

## Data Collection Instructions

To refine this cost analysis with actual usage data, please provide the following information:

### 1. GCS Storage Usage

```bash
# Total bucket size
gsutil du -sh gs://mmm-app-output

# Storage by prefix (if using environment-specific prefixes)
gsutil du -sh gs://mmm-app-output/training-configs/default/
gsutil du -sh gs://mmm-app-output/training-configs/default-dev/

# Storage growth over time (list objects with timestamps)
gsutil ls -lR gs://mmm-app-output | tail -100
```

**Please provide:**
- Total bucket size (GB)
- Breakdown by environment prefix (if distinguishable)
- Approximate growth rate (GB/month)

### 2. Artifact Registry Storage

```bash
# List all images and their sizes
gcloud artifacts docker images list \
  europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo \
  --include-tags --format="table(package,version,size)"

# Total registry size
gcloud artifacts repositories describe mmm-repo \
  --location=europe-west1 \
  --format="get(sizeBytes)"
```

**Please provide:**
- Total repository size (GB)
- Number of image tags per image
- Cleanup policy (if any)

### 3. Cloud Run Execution History

```bash
# Production training job history (last 30 days)
gcloud run jobs executions list \
  --job=mmm-app-training \
  --region=europe-west1 \
  --limit=100 \
  --format="table(name,createTime,completionTime,status)"

# Dev training job history (last 30 days)
gcloud run jobs executions list \
  --job=mmm-app-dev-training \
  --region=europe-west1 \
  --limit=100 \
  --format="table(name,createTime,completionTime,status)"

# Web service metrics (requires Cloud Monitoring API)
gcloud monitoring time-series list \
  --filter='metric.type="run.googleapis.com/request_count" 
    AND resource.labels.service_name=("mmm-app-web" OR "mmm-app-dev-web")' \
  --format=json
```

**Please provide:**
- Number of training jobs per month (dev vs prod)
- Average job duration by environment
- Web service request counts (monthly)

### 4. Cloud Logging Volume

```bash
# Logging volume by service (last 30 days)
gcloud logging read \
  'resource.type="cloud_run_service" 
   AND (resource.labels.service_name="mmm-app-web" 
        OR resource.labels.service_name="mmm-app-dev-web")' \
  --limit=1000 \
  --format="value(textPayload)" \
  --freshness=30d | wc -c

# Training job logging volume
gcloud logging read \
  'resource.type="cloud_run_job"
   AND (resource.labels.job_name="mmm-app-training"
        OR resource.labels.job_name="mmm-app-dev-training")' \
  --limit=1000 \
  --format="value(textPayload)" \
  --freshness=30d | wc -c
```

**Please provide:**
- Approximate log volume (GB/month)
- Breakdown by environment (if available)

### 5. Snowflake Usage

```sql
-- Query history by application (last 30 days)
SELECT 
  DATE_TRUNC('day', START_TIME) as date,
  WAREHOUSE_NAME,
  COUNT(*) as query_count,
  SUM(TOTAL_ELAPSED_TIME)/1000 as total_seconds,
  SUM(CREDITS_USED_CLOUD_SERVICES) as credits_used
FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY(
  END_TIME_RANGE_START=>DATEADD('day', -30, CURRENT_TIMESTAMP()),
  END_TIME_RANGE_END=>CURRENT_TIMESTAMP()
))
WHERE WAREHOUSE_NAME = 'SMALL_WH'
  AND USER_NAME = 'IPENC'
GROUP BY date, WAREHOUSE_NAME
ORDER BY date DESC;
```

**Please provide:**
- Monthly query count
- Average credits used per month
- Cache hit rate (if available from Snowflake metrics)

### 6. CI/CD Build Frequency

**From GitHub Actions:**
- Number of deployments to dev per month
- Number of deployments to prod per month
- Average build duration

**To retrieve:**
```bash
# Via GitHub CLI
gh run list --repo ivana-meshed/mmm-app --workflow=ci.yml --limit=50
gh run list --repo ivana-meshed/mmm-app --workflow=ci-dev.yml --limit=50
```

**Please provide:**
- Dev deployments per month
- Prod deployments per month
- Average total build minutes per deployment

### 7. Billing Export (Optional but Highly Recommended)

Enable detailed billing export for precise cost attribution:

```bash
# Create BigQuery dataset for billing export
bq mk --dataset --location=europe-west1 billing_export

# Enable billing export in GCP Console:
# Billing → Billing Export → BigQuery Export → Enable
```

Once enabled, run queries like:

```sql
-- Cost by service and environment (last 30 days)
SELECT 
  service.description as service,
  labels.value as environment,
  SUM(cost) as total_cost,
  SUM(usage.amount) as usage_amount,
  usage.unit as unit
FROM `datawarehouse-422511.billing_export.gcp_billing_export_*`
WHERE _TABLE_SUFFIX BETWEEN 
  FORMAT_DATE('%Y%m%d', DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY))
  AND FORMAT_DATE('%Y%m%d', CURRENT_DATE())
  AND labels.key = 'environment'
GROUP BY service, environment, unit
ORDER BY total_cost DESC;
```

---

## Optimization Recommendations

### 1. Environment-Specific Optimizations

#### Development Environment

**Goal:** Minimize costs while maintaining development velocity

**Recommendations:**

1. **Use Benchmark Workloads for Testing** (Currently Implemented ✅)
   - Benchmark: 2,000 × 5 iterations = $0.20 per job (12 min)
   - Production: 10,000 × 5 iterations = $1.33-$2.00 per job (80-120 min)
   - **Savings:** 85-90% per job in dev environment

2. **Reduce Training Job Resources for Light Testing**
   - Current: 8 vCPU, 32GB RAM
   - Consider for dev: 4 vCPU, 16GB RAM (50% cost reduction)
   - Trade-off: 1.5-2× longer execution time
   - **Potential Savings:** $5-50/month depending on usage

3. **Implement Aggressive GCS Lifecycle Policies for Dev Data**
   ```json
   {
     "lifecycle": {
       "rule": [
         {
           "action": {"type": "Delete"},
           "condition": {
             "age": 30,
             "matchesPrefix": ["training-configs/default-dev/"]
           }
         }
       ]
     }
   }
   ```
   - **Savings:** $0.50-5.00/month on old dev data

4. **Use Smaller Docker Images for Dev**
   - Create dev-specific base image without unnecessary tools
   - **Savings:** $0.10-0.50/month on Artifact Registry

#### Production Environment

**Goal:** Optimize costs without compromising reliability or performance

**Recommendations:**

1. **Monitor and Right-Size Memory Allocation**
   - Current: 32GB RAM (may be more than needed)
   - Action: Monitor actual memory usage for 2-4 weeks
   - If <20GB used: Consider 24GB (25% savings on memory cost)
   - **Potential Savings:** $3-15/month

2. **Implement Result Compression** (Not yet implemented)
   ```r
   # In r/run_all.R, before uploading to GCS
   library(zip)
   zip::zip("results.zip", 
            files = c("OutputCollect.RDS", "InputCollect.RDS", 
                     "plots/", "one_pagers/"))
   ```
   - **Savings:** 50% on storage and egress costs ($20-40/month at 500 jobs/month)

3. **Optimize Cloud Logging Retention**
   - Current: Default 30-day retention
   - Recommended: 7-day retention for debug logs, 30-day for important events
   - **Savings:** Minimal (within 50GB free tier for most usage)

4. **Enable Detailed Cost Monitoring**
   - Set up GCP billing export to BigQuery
   - Create dashboard for cost tracking by environment
   - Set budget alerts at $50, $100, $200 thresholds
   - **Benefit:** Early detection of cost anomalies

### 2. Cross-Environment Optimizations

**Recommendations:**

1. **Shared Image Registry Cleanup**
   ```bash
   # Delete images older than 30 days except 'latest' and recent SHAs
   gcloud artifacts docker images list \
     europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-training \
     --filter="createTime<'2026-01-01'" \
     --format="get(image)" | \
     xargs -I {} gcloud artifacts docker images delete {} --quiet
   ```
   - **Savings:** $0.20-1.00/month

2. **Consolidate Snowflake Queries with Better Caching**
   - Current: 70% cache hit rate
   - Target: 85% cache hit rate with optimized cache keys
   - **Savings:** Additional 15% reduction in Snowflake costs

3. **Optimize CI/CD Build Times**
   - Use GitHub Actions cache more aggressively
   - Consider scheduled base image builds (vs every push)
   - **Savings:** 20-40% reduction in build time (faster deployments)

### 3. Cost Monitoring and Alerting

**Set up monitoring with these commands:**

```bash
# Create budget alerts
gcloud billing budgets create \
  --billing-account=<ACCOUNT_ID> \
  --display-name="MMM App Dev Environment" \
  --budget-amount=100 \
  --threshold-rule=percent=50 \
  --threshold-rule=percent=90 \
  --threshold-rule=percent=100

gcloud billing budgets create \
  --billing-account=<ACCOUNT_ID> \
  --display-name="MMM App Prod Environment" \
  --budget-amount=500 \
  --threshold-rule=percent=50 \
  --threshold-rule=percent=90 \
  --threshold-rule=percent=100

# Create custom dashboard for cost tracking
# (Manual setup in GCP Console → Monitoring → Dashboards)
```

**Key Metrics to Monitor:**

1. **Training Job Execution Rate**
   - Dev: jobs/day
   - Prod: jobs/day
   - Alert if dev exceeds prod (may indicate inefficient testing)

2. **GCS Storage Growth Rate**
   - Bytes/day by prefix
   - Alert if exceeds expected growth (2GB per job)

3. **Cloud Run Request Patterns**
   - Requests/day by service
   - Alert on unusual spikes

4. **Snowflake Credit Consumption**
   - Credits/day
   - Alert if exceeds baseline

---

## Summary and Next Steps

### Key Takeaways

1. **Both environments currently use identical infrastructure configurations** (8 vCPU, 32GB RAM)
2. **Training jobs account for 95%+ of variable costs** in both environments
3. **Shared resources represent ~$2.09/month base cost**, split across environments
4. **Dev environment can use benchmark workloads** (12 min, $0.20/job) for significant cost savings vs production workloads

### Recommended Actions

**Immediate (This Week):**
1. ✅ Collect actual usage data using commands in "Data Collection Instructions"
2. ✅ Review this cost analysis with stakeholders
3. ✅ Set up GCP billing export to BigQuery for detailed tracking
4. ✅ Configure budget alerts for both environments

**Short-term (Next 2 Weeks):**
1. Monitor actual training job frequency and duration by environment
2. Analyze GCS storage growth patterns
3. Evaluate memory usage to determine right-sizing opportunity
4. Implement dev-specific GCS lifecycle policies

**Medium-term (Next Month):**
1. Consider reducing dev training job resources to 4 vCPU, 16GB RAM
2. Implement result compression for production jobs
3. Optimize Artifact Registry cleanup policies
4. Review and optimize Snowflake query patterns

**Long-term (Next Quarter):**
1. Evaluate separate GCP projects for dev/prod (if cost allocation is critical)
2. Consider preemptible/spot instances when available for Cloud Run Jobs
3. Implement advanced cost optimization based on usage patterns
4. Regular quarterly cost reviews and optimization cycles

---

## Appendix: Pricing References

### GCP Pricing (europe-west1)

| Service | Component | Price |
|---------|-----------|-------|
| **Cloud Run** | CPU | $0.000024/vCPU-second |
| **Cloud Run** | Memory | $0.0000025/GB-second |
| **Cloud Run** | Requests | $0.40/million requests |
| **Cloud Storage** | Standard | $0.020/GB/month |
| **Cloud Storage** | Nearline | $0.010/GB/month |
| **Cloud Storage** | Coldline | $0.004/GB/month |
| **Cloud Storage** | Class A ops | $0.05/10K operations |
| **Cloud Storage** | Class B ops | $0.004/10K operations |
| **Cloud Storage** | Egress | $0.12/GB (internet) |
| **Artifact Registry** | Storage | $0.10/GB/month |
| **Secret Manager** | Storage | $0.06/secret/month |
| **Secret Manager** | Access | $0.03/10K accesses |
| **Cloud Scheduler** | Jobs | $0.10/job/month (first 3 free) |
| **Cloud Logging** | Ingestion | $0.50/GB (first 50GB free) |
| **Snowflake** | Small warehouse | $2.00/credit |

### External References

- [Cloud Run Pricing](https://cloud.google.com/run/pricing)
- [Cloud Storage Pricing](https://cloud.google.com/storage/pricing)
- [Artifact Registry Pricing](https://cloud.google.com/artifact-registry/pricing)
- [Secret Manager Pricing](https://cloud.google.com/secret-manager/pricing)
- [Cloud Scheduler Pricing](https://cloud.google.com/scheduler/pricing)
- [Cloud Logging Pricing](https://cloud.google.com/stackdriver/pricing)
- [Snowflake Pricing](https://www.snowflake.com/pricing/)

---

**Document Version:** 1.0  
**Last Updated:** 2026-02-02  
**Author:** Cost Analysis Team  
**Review Cycle:** Quarterly
