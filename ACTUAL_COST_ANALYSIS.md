# In-Depth Cost Analysis Based on Actual Usage Data

**Analysis Date:** 2026-02-02  
**Data Collection Date:** 2026-02-02 12:55:52  
**Analysis Type:** Actual usage patterns with optimization recommendations

---

## Executive Summary

### 🚨 Critical Finding: Artifact Registry Bloat

**IMMEDIATE ACTION REQUIRED:** Artifact Registry contains **122.58 GB** of images (9,228 individual images), costing approximately **$12.26/month** in storage alone. This represents a **1,226% increase** over the estimated $1.00/month, indicating severe image accumulation without proper cleanup.

### Actual Monthly Costs (Based on Collected Data)

| Cost Component | Estimated | **Actual** | Variance | Status |
|----------------|-----------|------------|----------|--------|
| **Artifact Registry** | $1.00 | **$12.26** | +1,126% | 🚨 Critical |
| **GCS Storage** | $0.82 | **$0.58** | -29% | ✅ Good |
| **Secret Manager** | $0.36 | **$0.42** | +17% | ✅ Good |
| **Cloud Scheduler** | $0.00 | **$0.00** | 0% | ✅ Good (3 jobs = free) |
| **Base Infrastructure** | $2.09 | **$13.26** | +534% | 🚨 Critical |

**Total Monthly Fixed Costs: ~$13.26/month** (vs. estimated $2.09/month)

**Primary Issue:** Artifact Registry bloat accounts for 92% of the unexpected cost increase.

---

## Detailed Cost Breakdown

### 1. Artifact Registry - CRITICAL ISSUE ⚠️

**Actual Usage:**
- **Total Size:** 122.58 GB (122,580.087 MB)
- **Number of Images:** 9,228 images
- **Image Types:** mmm-app, mmm-training, mmm-training-base, mmm-web

**Cost Calculation:**
```
Storage: 122.58 GB × $0.10/GB/month = $12.26/month
```

**Analysis:**

The registry contains **9,228 images**, which is extremely high. Based on the infrastructure:
- 4 image types (mmm-app, mmm-training, mmm-training-base, mmm-web)
- With normal development, you'd expect ~10-20 tags per image
- **Expected total: 40-80 images**
- **Actual total: 9,228 images**

This suggests:
1. **No cleanup policy** - Every build from CI/CD is retained indefinitely
2. **High build frequency** - Potentially hundreds of builds without cleanup
3. **Accumulation over time** - Images dating back months

**Impact:**
- **$12.26/month** in unnecessary costs
- **$147.12/year** wasted on old images
- Slower image pulls due to registry bloat
- Harder to identify current vs. old images

---

### 2. Google Cloud Storage (GCS)

**Actual Usage:**
- **Total Size:** 28.74 GiB (30,861,511,313 bytes)
- **Number of Objects:** 4,524 objects
- **Average Object Size:** 6.82 MB

**Storage Breakdown:**
```
Training data by region:
- de/ (Germany): ~21-23 GiB (largest)
- fr/ (France): ~3-4 GiB
- es/ (Spain): ~1-2 GiB
- training-configs/: ~0 B (empty queues - good!)
```

**Cost Calculation:**
```
Standard storage: 28.74 GB × $0.020/GB/month = $0.57/month
With lifecycle policies (Nearline after 30d): ~$0.50/month
```

**Analysis:**
- ✅ Storage size is reasonable and matches usage patterns
- ✅ Training config queues are clean (no backlog)
- ✅ Data organized by region/market (de, fr, es)
- ⚠️ Consider lifecycle policies if data older than 90 days exists

**Recommendation:** Storage is well-managed. Consider implementing:
- Delete training data older than 180 days (if not needed)
- Move data older than 90 days to Coldline storage

---

### 3. Secret Manager

**Actual Usage:**
- **Total Secrets:** 7 active secrets
- **Secrets List:**
  1. `bq-credentials-persistent` (created 2026-01-15)
  2. `sf-password` (created 2025-09-03)
  3. `sf-private-key` (created 2025-10-16)
  4. `sf-private-key-persistent` (created 2025-10-22)
  5. `streamlit-auth-client-id` (created 2025-10-16)
  6. `streamlit-auth-client-secret` (created 2025-10-16)
  7. `streamlit-auth-cookie-secret` (created 2025-10-16)

**Cost Calculation:**
```
Storage: 7 secrets × $0.06/secret/month = $0.42/month
Access: Included in web service usage
```

**Analysis:**
- ✅ Reasonable number of secrets
- ✅ All secrets appear to be in active use
- ⚠️ One additional secret compared to estimate (bq-credentials-persistent)

**Recommendation:** Continue monitoring. No optimization needed.

---

### 4. Cloud Scheduler

**Actual Usage:**
- **Total Jobs:** 3 scheduler jobs
- **Jobs:**
  1. `mmm-warmup-job` - Every 5 minutes (keeping services warm)
  2. `robyn-queue-tick` - Every minute (production queue)
  3. `robyn-queue-tick-dev` - Every minute (dev queue)

**Cost Calculation:**
```
First 3 jobs: Free (covered by free tier)
Total cost: $0.00/month
```

**Analysis:**
- ✅ Within the 3-job free tier
- ⚠️ Warmup job runs every 5 minutes (12 runs/hour × 24 hours = 288 runs/day)
- ⚠️ Queue tick jobs run every minute (1,440 runs/day each)

**Questions to Consider:**
1. Is the warmup job necessary with `min_instances=0`?
   - If you've accepted cold starts, warmup may be redundant
   - Could save scheduler quota if you add more jobs later

2. Are queue ticks needed every minute?
   - Consider reducing to every 2-5 minutes if jobs aren't time-sensitive
   - Would reduce web service invocations

**Recommendation:** Review if warmup job is needed with current architecture.

---

### 5. Cloud Run Services

**Actual Services:**

**Production (mmm-app-web):**
- URL: https://mmm-app-web-wuepn6nq5a-ew.a.run.app
- Revision: mmm-app-web-00184-nbk
- Traffic: 100% to latest
- Status: Active

**Development (mmm-app-dev-web):**
- URL: https://mmm-app-dev-web-wuepn6nq5a-ew.a.run.app
- Revision: mmm-app-dev-web-00738-fzm
- Traffic: 100% to latest
- Status: Active

**Analysis:**
- ✅ Both services are active and receiving traffic
- ✅ Using latest revisions (good deployment hygiene)
- ⚠️ Dev service has 738 revisions (high, suggests frequent deploys)
- ⚠️ Prod service has 184 revisions (also high)

**Revision Accumulation:**
- Each revision is stored until explicitly deleted
- Old revisions don't cost much, but clutter the service
- High revision count suggests no cleanup

**Recommendation:** Implement revision cleanup (keep last 10-20 revisions).

---

### 6. Cloud Run Training Jobs

**Actual Usage:**
- **Production:** `mmm-app-training` (logs show 1000+ entries)
- **Development:** `mmm-app-dev-training` (logs show 1000+ entries)

**Analysis:**
- ⚠️ Job execution data not available (API error during collection)
- ✅ Both jobs are actively used (based on logging volume)
- ⚠️ High log volume (1000+ entries each) suggests significant usage

**Cost Impact:**
- Cannot calculate exact training job costs without execution data
- Based on log volume, estimate **moderate to heavy usage**
- Training jobs likely represent 80-95% of variable costs

**Recommendation:** 
1. Get detailed job execution history manually
2. Track average job duration and frequency
3. Calculate actual training costs based on real execution data

---

### 7. Cloud Logging

**Actual Usage:**
- **Production Web Service:** 0 log entries (last 30 days)
- **Development Web Service:** 0 log entries (last 30 days)
- **Production Training Jobs:** 1,000+ log entries (sample limit reached)
- **Development Training Jobs:** 1,000+ log entries (sample limit reached)

**Analysis:**
- ✅ Web service logging is minimal (good for costs)
- ⚠️ Training job logging is substantial
- Actual log volume (GB) needs to be calculated from log size, not entry count

**Estimated Cost:**
```
First 50 GB: Free
Above 50 GB: $0.50/GB

Estimated monthly logging: 10-20 GB (within free tier)
Cost: $0.00/month (likely within free tier)
```

**Recommendation:** Monitor logging volume to ensure staying within free tier.

---

## Cost Optimization Recommendations

### 🚨 PRIORITY 1: Clean Up Artifact Registry (IMMEDIATE)

**Problem:** 9,228 images consuming 122.58 GB, costing $12.26/month

**Solution: Implement Aggressive Cleanup Policy**

#### Step 1: Identify Images to Keep

Keep only:
- **Latest tag** for each image (production use)
- **Last 5 commit SHA tags** for each image (rollback capability)
- **Recent images** from last 14 days

#### Step 2: Delete Old Images

**Script to clean up old images:**

```bash
#!/bin/bash
# cleanup-artifact-registry.sh

PROJECT_ID="datawarehouse-422511"
REPO="mmm-repo"
LOCATION="europe-west1"
KEEP_LAST_N=5  # Keep last N images per image name

# Image types
IMAGES=("mmm-app" "mmm-web" "mmm-training" "mmm-training-base")

for IMAGE in "${IMAGES[@]}"; do
  echo "Processing $IMAGE..."
  
  # Get all digests sorted by creation time (oldest first)
  gcloud artifacts docker images list \
    $LOCATION-docker.pkg.dev/$PROJECT_ID/$REPO/$IMAGE \
    --include-tags \
    --sort-by=CREATE_TIME \
    --format="value(version)" | \
  head -n -$KEEP_LAST_N | \
  while read DIGEST; do
    echo "Deleting old image: $IMAGE@$DIGEST"
    gcloud artifacts docker images delete \
      $LOCATION-docker.pkg.dev/$PROJECT_ID/$REPO/$IMAGE@$DIGEST \
      --quiet
  done
done

echo "Cleanup complete!"
```

**Manual cleanup (safer first approach):**

```bash
# 1. List all images older than 30 days
gcloud artifacts docker images list \
  europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo \
  --filter="createTime<'2026-01-03'" \
  --format="value(version)" > old_images.txt

# 2. Review the list before deleting
wc -l old_images.txt

# 3. Delete old images (after review!)
while read IMAGE; do
  echo "Deleting $IMAGE"
  gcloud artifacts docker images delete "$IMAGE" --quiet
done < old_images.txt
```

**Expected Savings:**
- Reduce from 122.58 GB to ~5-10 GB (keeping last 5 versions of 4 images)
- New cost: ~$0.50-1.00/month
- **Savings: $11.26-11.76/month ($135-141/year)**

#### Step 3: Implement Automatic Cleanup Policy

**In `infra/terraform/main.tf`, add lifecycle policy:**

```hcl
resource "google_artifact_registry_repository" "mmm_repo" {
  repository_id = "mmm-repo"
  location      = var.region
  format        = "DOCKER"

  cleanup_policies {
    id     = "delete-old-images"
    action = "DELETE"
    condition {
      tag_state = "UNTAGGED"
      older_than = "2592000s"  # 30 days
    }
  }

  cleanup_policies {
    id     = "keep-minimum-versions"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }
}
```

---

### ⚠️ PRIORITY 2: Review Warmup Job Necessity

**Problem:** Warmup job runs every 5 minutes but `min_instances=0`

**Analysis:**
- With `min_instances=0`, instances shut down when idle
- Warmup job prevents shutdown by making requests every 5 minutes
- This creates a pseudo-`min_instances=1` behavior

**Options:**

**Option A: Remove Warmup Job (Save invocation costs)**
```bash
gcloud scheduler jobs delete mmm-warmup-job --location=europe-west1
```
- Accept 1-3 second cold start latency
- Reduce web service invocations by 288/day
- Align with `min_instances=0` cost optimization

**Option B: Increase Warmup Interval**
```bash
# Change from every 5 minutes to every 15 minutes
gcloud scheduler jobs update http mmm-warmup-job \
  --location=europe-west1 \
  --schedule="*/15 * * * *"
```
- Reduce invocations from 288/day to 96/day
- Still prevent some cold starts

**Option C: Set min_instances=1**
- Remove warmup job
- Set `min_instances=1` in Terraform
- Consistent performance, ~$40/month increase

**Recommendation:** 
- **Option A** if cold starts are acceptable
- **Option B** if occasional warmup is needed
- **Option C** if performance is critical

---

### ⚠️ PRIORITY 3: Implement GCS Lifecycle Policies

**Current State:** 28.74 GiB of training data, no lifecycle policies visible

**Recommendation: Implement Tiered Storage**

```bash
# Create lifecycle policy
cat > gcs-lifecycle.json << 'EOF'
{
  "lifecycle": {
    "rule": [
      {
        "action": {"type": "SetStorageClass", "storageClass": "NEARLINE"},
        "condition": {
          "age": 30,
          "matchesPrefix": ["training_data/"]
        }
      },
      {
        "action": {"type": "SetStorageClass", "storageClass": "COLDLINE"},
        "condition": {
          "age": 90,
          "matchesPrefix": ["training_data/"]
        }
      },
      {
        "action": {"type": "Delete"},
        "condition": {
          "age": 180,
          "matchesPrefix": ["training_data/de/", "training_data/fr/", "training_data/es/"]
        }
      }
    ]
  }
}
EOF

# Apply lifecycle policy
gsutil lifecycle set gcs-lifecycle.json gs://mmm-app-output
```

**Expected Savings:**
```
Current: 28.74 GB × $0.020/GB = $0.57/month

With lifecycle policies:
- 0-30 days (33%): 9.5 GB × $0.020 = $0.19
- 30-90 days (33%): 9.5 GB × $0.010 = $0.10
- 90-180 days (33%): 9.5 GB × $0.004 = $0.04
- 180+ days: Deleted

New cost: ~$0.33/month
Savings: $0.24/month ($2.88/year)
```

---

### 🔧 PRIORITY 4: Monitor Training Job Costs

**Problem:** Unable to collect job execution history due to API error

**Action Items:**

1. **Get Manual Job Execution Data:**
```bash
# Get last 100 executions for prod
gcloud run jobs executions list \
  --job=mmm-app-training \
  --region=europe-west1 \
  --limit=100 \
  --format="table(name,status.startTime,status.completionTime,status.logUri)"

# Get last 100 executions for dev  
gcloud run jobs executions list \
  --job=mmm-app-dev-training \
  --region=europe-west1 \
  --limit=100 \
  --format="table(name,status.startTime,status.completionTime,status.logUri)"
```

2. **Calculate Actual Training Costs:**
```bash
# Get execution details with duration
for execution in $(gcloud run jobs executions list \
  --job=mmm-app-training \
  --region=europe-west1 \
  --limit=10 \
  --format="value(name)"); do
  
  gcloud run jobs executions describe $execution \
    --job=mmm-app-training \
    --region=europe-west1 \
    --format="table(
      metadata.name,
      status.startTime,
      status.completionTime,
      status.logUri
    )"
done
```

3. **Set Up Cost Monitoring:**
```bash
# Enable billing export to BigQuery
# (Must be done in GCP Console: Billing → Billing Export)

# Then query costs:
SELECT
  service.description,
  sku.description,
  usage_start_time,
  usage_end_time,
  SUM(cost) as cost,
  SUM(usage.amount) as usage_amount,
  usage.unit
FROM `datawarehouse-422511.billing_export.gcp_billing_export_*`
WHERE service.description = 'Cloud Run'
  AND _TABLE_SUFFIX >= FORMAT_DATE('%Y%m%d', DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY))
GROUP BY 1,2,3,4,7
ORDER BY cost DESC
```

---

### 🔍 PRIORITY 5: Implement Monitoring and Alerts

**Set Up Budget Alerts:**

```bash
# Create budget alert for project
gcloud billing budgets create \
  --billing-account=$(gcloud billing projects describe datawarehouse-422511 --format="value(billingAccountName)") \
  --display-name="MMM App Monthly Budget" \
  --budget-amount=500 \
  --threshold-rule=percent=50 \
  --threshold-rule=percent=75 \
  --threshold-rule=percent=90 \
  --threshold-rule=percent=100
```

**Set Up Custom Metrics:**

1. **Artifact Registry Size Monitoring:**
```bash
# Create alert when registry exceeds 50GB
# (Configure in Cloud Console: Monitoring → Alerting)
```

2. **Training Job Cost Monitoring:**
```bash
# Track daily training job costs
# Alert if exceeds $50/day
```

---

## Updated Monthly Cost Projections

### Current Actual Costs (Before Optimization)

| Component | Monthly Cost | Annual Cost |
|-----------|--------------|-------------|
| **Artifact Registry** | $12.26 | $147.12 |
| **GCS Storage** | $0.58 | $6.96 |
| **Secret Manager** | $0.42 | $5.04 |
| **Cloud Scheduler** | $0.00 | $0.00 |
| **Cloud Logging** | $0.00 | $0.00 |
| **Base Total** | **$13.26** | **$159.12** |
| **Training Jobs** | *Unknown* | *Unknown* |
| **Web Services** | *~$2-5* | *~$24-60* |
| **Snowflake** | *~$10-30* | *~$120-360* |
| **Estimated Total** | **$25-50/month** | **$300-600/year** |

### After Priority 1 Optimization (Clean Artifact Registry)

| Component | Monthly Cost | Annual Cost | Savings |
|-----------|--------------|-------------|---------|
| **Artifact Registry** | $0.50 | $6.00 | -$141.12/year |
| **GCS Storage** | $0.58 | $6.96 | $0 |
| **Secret Manager** | $0.42 | $5.04 | $0 |
| **Cloud Scheduler** | $0.00 | $0.00 | $0 |
| **Cloud Logging** | $0.00 | $0.00 | $0 |
| **Base Total** | **$1.50** | **$18.00** | **-$141.12** |

### After All Optimizations

| Component | Monthly Cost | Annual Cost | Savings |
|-----------|--------------|-------------|---------|
| **Artifact Registry** | $0.50 | $6.00 | -$141.12/year |
| **GCS Storage** | $0.33 | $3.96 | -$3.00/year |
| **Secret Manager** | $0.42 | $5.04 | $0 |
| **Cloud Scheduler** | $0.00 | $0.00 | $0 |
| **Cloud Logging** | $0.00 | $0.00 | $0 |
| **Base Total** | **$1.25** | **$15.00** | **-$144.12** |

**Total Annual Savings: $144/year on base infrastructure alone**

---

## Implementation Checklist

### Week 1: Critical Items

- [ ] **Day 1-2: Clean Artifact Registry**
  - [ ] Run manual cleanup script (delete images older than 30 days)
  - [ ] Verify registry size reduced to <10 GB
  - [ ] Test that latest images still work
  - [ ] Document current image tags before cleanup

- [ ] **Day 3: Implement Artifact Registry Cleanup Policy**
  - [ ] Add cleanup policy to Terraform
  - [ ] Apply Terraform changes
  - [ ] Verify policy is active

- [ ] **Day 4-5: Get Training Job Data**
  - [ ] Manually collect last 100 job executions
  - [ ] Calculate average job duration and frequency
  - [ ] Calculate actual training costs
  - [ ] Update cost analysis with real numbers

### Week 2: Medium Priority Items

- [ ] **Implement GCS Lifecycle Policies**
  - [ ] Create lifecycle configuration
  - [ ] Test on small subset
  - [ ] Apply to full bucket
  - [ ] Verify older data moves to cheaper tiers

- [ ] **Review Warmup Job**
  - [ ] Test application without warmup job
  - [ ] Measure cold start latency
  - [ ] Decide: remove, reduce frequency, or keep
  - [ ] Update scheduler accordingly

- [ ] **Set Up Monitoring**
  - [ ] Create budget alerts
  - [ ] Set up Artifact Registry size alerts
  - [ ] Configure training job cost tracking

### Week 3-4: Long-term Improvements

- [ ] **Enable BigQuery Billing Export**
  - [ ] Create BigQuery dataset
  - [ ] Enable billing export
  - [ ] Create cost analysis queries
  - [ ] Schedule weekly cost reports

- [ ] **Document Cost Attribution**
  - [ ] Tag resources with environment labels
  - [ ] Set up cost allocation by team/environment
  - [ ] Create monthly cost review process

- [ ] **Optimize Training Workloads**
  - [ ] Review if dev needs production-sized jobs
  - [ ] Consider reducing dev to 4 vCPU/16GB
  - [ ] Implement result compression
  - [ ] Review memory usage for right-sizing

---

## Cost Optimization Summary

### Immediate Wins (Next 7 Days)

| Optimization | Effort | Savings/Year | ROI |
|-------------|--------|--------------|-----|
| **Clean Artifact Registry** | 2 hours | $141/year | Immediate |
| **Implement Registry Cleanup Policy** | 1 hour | $141/year | Ongoing |
| **GCS Lifecycle Policies** | 1 hour | $3/year | Ongoing |
| **Review Warmup Job** | 30 min | $5-50/year | Variable |

**Total Immediate Savings: $144-191/year for 4-5 hours of work**

### Long-term Optimizations (Next 30 Days)

| Optimization | Effort | Savings/Year | When |
|-------------|--------|--------------|------|
| **Training Job Right-Sizing** | 4 hours | $50-200/year | After analysis |
| **Result Compression** | 2 hours | $30-50/year | Month 2 |
| **Memory Optimization** | 3 hours | $50-100/year | Month 2-3 |
| **Snowflake Query Optimization** | 4 hours | $100-300/year | Ongoing |

**Total Long-term Savings: $230-650/year**

---

## Recommended Next Actions

### 1. Immediate (This Week)

**Clean up Artifact Registry:**
```bash
# Save this script as cleanup-registry.sh
# Review and run manually

cd /home/runner/work/mmm-app/mmm-app
./scripts/cleanup-registry.sh  # Create this script with cleanup commands
```

### 2. Short-term (Next 2 Weeks)

1. Get actual training job execution data
2. Calculate real training costs
3. Implement GCS lifecycle policies
4. Set up cost monitoring alerts

### 3. Medium-term (Next Month)

1. Review training job sizing (dev vs prod)
2. Implement result compression
3. Monitor and optimize based on real usage
4. Schedule quarterly cost reviews

---

## Questions for Follow-up

To further refine this analysis, please provide:

1. **Training Job Frequency:**
   - How many jobs do you run per day/week in dev?
   - How many jobs do you run per day/week in prod?
   - What is the typical workload (benchmark vs production)?

2. **Business Requirements:**
   - What is your cold start tolerance? (affects warmup job decision)
   - How long do you need to retain training data? (affects GCS lifecycle)
   - What is your monthly budget for this infrastructure?

3. **Usage Patterns:**
   - Peak usage times?
   - Seasonal variations?
   - Expected growth in the next 6-12 months?

---

## Appendix: Data Collection Details

### Data Files Analyzed

1. **GCS Storage:** `20260202_125552_gcs_storage.txt`
   - Total: 28.74 GiB (4,524 objects)
   - Primary usage: training_data/

2. **Artifact Registry:** `20260202_125552_artifact_registry.txt`
   - Total: 122.58 GB (9,228 images)
   - Critical bloat issue identified

3. **Service Details:** `20260202_125552_service_details.txt`
   - Both dev and prod active
   - High revision counts

4. **Logging Volume:** `20260202_125552_logging_volume.txt`
   - Training jobs: 1000+ entries each
   - Web services: 0 entries (good)

5. **Secret Manager:** `20260202_125552_secret_manager.txt`
   - 7 active secrets
   - All appear necessary

6. **Scheduler Jobs:** `20260202_125552_scheduler_jobs.txt`
   - 3 active jobs (within free tier)
   - Warmup job may be redundant

### Data Collection Issues

- Job execution history API error (format issue with runningDuration)
- Need manual collection of job execution data
- Logging volume in GB not available (only entry counts)

---

**Document Version:** 1.0  
**Last Updated:** 2026-02-02  
**Next Review:** After implementing Priority 1 optimizations  
**Contact:** Cost Analysis Team
