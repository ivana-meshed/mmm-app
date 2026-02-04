# Cost Optimization Implementation Guide

This guide provides step-by-step instructions for implementing the cost optimizations identified in the actual cost analysis.

## Overview

Based on the actual data collected, the primary cost issue is **Artifact Registry bloat** (9,228 images, 122.58 GB, $12.26/month). Following these steps will save approximately **$140/year** on base infrastructure costs.

---

## Priority 1: Clean Up Artifact Registry (IMMEDIATE)

### Expected Impact
- **Monthly Savings:** $11-12/month
- **Annual Savings:** $132-144/year
- **Effort:** 1-2 hours
- **Risk:** Low (keeps latest versions)

### Step 1: Review Current State

```bash
# Check current repository size
gcloud artifacts repositories describe mmm-repo \
  --location=europe-west1 \
  --format="value(sizeBytes)"

# Count images
gcloud artifacts docker images list \
  europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo \
  --format="value(version)" | wc -l
```

**Expected output:** ~122.58 GB, ~9,228 images

### Step 2: Run Cleanup Script (Dry Run)

```bash
# First, run in dry-run mode to see what would be deleted
cd /path/to/mmm-app
./scripts/cleanup_artifact_registry.sh

# Or with custom settings
DRY_RUN=true KEEP_LAST_N=10 ./scripts/cleanup_artifact_registry.sh
```

**Review the output carefully!** The script will show which images would be deleted.

### Step 3: Run Actual Cleanup

```bash
# Delete old images (keeps last 10 of each type)
DRY_RUN=false KEEP_LAST_N=10 ./scripts/cleanup_artifact_registry.sh

# Or be more aggressive (keep last 5)
DRY_RUN=false KEEP_LAST_N=5 ./scripts/cleanup_artifact_registry.sh
```

### Step 4: Verify Cleanup

```bash
# Check new repository size
gcloud artifacts repositories describe mmm-repo \
  --location=europe-west1 \
  --format="value(sizeBytes)"

# Count remaining images
gcloud artifacts docker images list \
  europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo \
  --format="value(version)" | wc -l
```

**Expected after cleanup:** ~5-10 GB, 40-80 images

### Step 5: Implement Automatic Cleanup (Terraform)

Add to `infra/terraform/main.tf`:

```hcl
# After the artifact registry repository resource, add:

resource "google_artifact_registry_repository" "mmm_repo" {
  # ... existing configuration ...
  
  cleanup_policies {
    id     = "delete-untagged"
    action = "DELETE"
    condition {
      tag_state = "UNTAGGED"
      older_than = "2592000s"  # 30 days in seconds
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

Then apply:

```bash
cd infra/terraform
terraform plan -var-file="envs/prod.tfvars"
terraform apply -var-file="envs/prod.tfvars"
```

---

## Priority 2: Implement GCS Lifecycle Policies

### Expected Impact
- **Monthly Savings:** $0.24/month
- **Annual Savings:** $2.88/year
- **Effort:** 30 minutes
- **Risk:** None (can be reverted)

### Step 1: Review Current GCS Usage

```bash
# Check current lifecycle policy
gsutil lifecycle get gs://mmm-app-output

# Check storage breakdown
gsutil du -sh gs://mmm-app-output/training_data/*/
```

### Step 2: Apply Lifecycle Policy

```bash
# Apply the prepared lifecycle policy
gsutil lifecycle set gcs-lifecycle-policy.json gs://mmm-app-output
```

### Step 3: Verify Policy

```bash
# Verify policy is applied
gsutil lifecycle get gs://mmm-app-output

# Monitor over next 30 days as data ages
```

---

## Priority 3: Review Warmup Job

### Expected Impact
- **Monthly Savings:** $0-5/month (depends on choice)
- **Annual Savings:** $0-60/year
- **Effort:** 15 minutes + testing
- **Risk:** May increase cold start latency

### Option A: Remove Warmup Job

**If cold starts are acceptable:**

```bash
# Delete warmup job
gcloud scheduler jobs delete mmm-warmup-job \
  --location=europe-west1 \
  --quiet

# Test application behavior
# - Access dev URL: https://mmm-app-dev-web-wuepn6nq5a-ew.a.run.app
# - Access prod URL: https://mmm-app-web-wuepn6nq5a-ew.a.run.app
# - Measure cold start time (should be 1-3 seconds)
```

### Option B: Reduce Warmup Frequency

**If you want some warmup but less frequent:**

```bash
# Change from every 5 minutes to every 15 minutes
gcloud scheduler jobs update http mmm-warmup-job \
  --location=europe-west1 \
  --schedule="*/15 * * * *"
```

### Option C: Keep Current (No Change)

If cold starts are not acceptable, keep the current warmup job.

**Trade-off:**
- Warmup every 5 minutes = 288 invocations/day
- With `min_instances=0`, this creates pseudo-`min_instances=1`
- Consider if you should just set `min_instances=1` instead (clearer intent)

---

## Priority 4: Set Up Cost Monitoring

### Expected Impact
- **Monthly Savings:** N/A (prevents future cost overruns)
- **Effort:** 1 hour
- **Risk:** None

### Step 1: Create Budget Alert

```bash
# Get billing account ID
BILLING_ACCOUNT=$(gcloud billing projects describe datawarehouse-422511 \
  --format="value(billingAccountName)")

# Create budget with alerts at 50%, 75%, 90%, 100%
gcloud billing budgets create \
  --billing-account=$BILLING_ACCOUNT \
  --display-name="MMM App Monthly Budget" \
  --budget-amount=500 \
  --threshold-rule=percent=50 \
  --threshold-rule=percent=75 \
  --threshold-rule=percent=90 \
  --threshold-rule=percent=100 \
  --all-updates-rule-pubsub-topic=projects/datawarehouse-422511/topics/budget-alerts
```

### Step 2: Enable Billing Export to BigQuery

1. Go to GCP Console: **Billing → Billing Export**
2. Click **BigQuery Export**
3. Click **Enable BigQuery Export**
4. Create dataset: `billing_export`
5. Enable detailed usage cost

### Step 3: Create Cost Monitoring Dashboard

In GCP Console: **Monitoring → Dashboards → Create Dashboard**

Add charts for:
1. **Artifact Registry Size** over time
2. **Cloud Run Execution Costs** by service
3. **GCS Storage Costs** by bucket
4. **Daily Cost Trends**

---

## Priority 5: Get Training Job Cost Data

### Expected Impact
- **Monthly Savings:** TBD (need data first)
- **Effort:** 30 minutes
- **Risk:** None (data collection only)

### Step 1: Collect Job Execution History

```bash
# Production job executions (last 100)
gcloud run jobs executions list \
  --job=mmm-app-training \
  --region=europe-west1 \
  --limit=100 \
  --format="table(
    name.basename(),
    status.startTime.date('%Y-%m-%d %H:%M'),
    status.completionTime.date('%Y-%m-%d %H:%M'),
    status.conditions[0].status
  )" > prod_job_history.txt

# Development job executions (last 100)
gcloud run jobs executions list \
  --job=mmm-app-dev-training \
  --region=europe-west1 \
  --limit=100 \
  --format="table(
    name.basename(),
    status.startTime.date('%Y-%m-%d %H:%M'),
    status.completionTime.date('%Y-%m-%d %H:%M'),
    status.conditions[0].status
  )" > dev_job_history.txt
```

### Step 2: Calculate Job Durations

Use the collected data to calculate:
- Average job duration per environment
- Job frequency (jobs per day/week)
- Success rate
- Peak usage times

### Step 3: Calculate Actual Costs

```
Cost per job = (Duration in seconds × vCPU count × CPU rate) + 
               (Duration in seconds × Memory GB × Memory rate)

Current config (8 vCPU, 32GB):
- CPU rate: $0.000024/vCPU-second
- Memory rate: $0.0000025/GB-second

Example for 12-minute job:
720 sec × 8 × $0.000024 + 720 sec × 32 × $0.0000025 = $0.138 + $0.058 = $0.196
```

---

## Verification Checklist

After implementing optimizations, verify:

- [ ] **Artifact Registry**
  - [ ] Size reduced from 122.58 GB to <10 GB
  - [ ] Image count reduced from 9,228 to <100
  - [ ] Latest images still accessible and working
  - [ ] Cleanup policy active in Terraform

- [ ] **GCS Lifecycle Policies**
  - [ ] Policy visible with `gsutil lifecycle get`
  - [ ] Monitoring for data moving to cheaper tiers
  - [ ] No unexpected deletions

- [ ] **Warmup Job**
  - [ ] Decision made and implemented
  - [ ] Cold start latency tested and acceptable
  - [ ] Application performance maintained

- [ ] **Cost Monitoring**
  - [ ] Budget alerts configured and receiving notifications
  - [ ] BigQuery billing export enabled
  - [ ] Monitoring dashboard created

- [ ] **Training Job Data**
  - [ ] Execution history collected
  - [ ] Average costs calculated
  - [ ] Cost analysis document updated

---

## Expected Cost Reduction Timeline

| Week | Action | Cumulative Savings/Year |
|------|--------|------------------------|
| **Week 1** | Clean Artifact Registry | $132-144 |
| **Week 2** | Implement GCS Lifecycle | $135-147 |
| **Week 2** | Review Warmup Job | $135-207 |
| **Week 3** | Set Up Monitoring | $135-207 (preventive) |
| **Week 4** | Analyze Training Jobs | TBD based on findings |

**Total Expected Savings: $140-200+/year on base infrastructure**

---

## Rollback Procedures

### If Artifact Registry Cleanup Causes Issues

```bash
# Check if you can roll back to a specific image
gcloud run services update mmm-app-web \
  --image=europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-web:PREVIOUS_TAG \
  --region=europe-west1

# If needed, rebuild images
# (Trigger CI/CD by pushing to main branch)
```

### If GCS Lifecycle Policy Causes Issues

```bash
# Remove lifecycle policy
gsutil lifecycle set /dev/null gs://mmm-app-output

# Or modify specific rules
# Edit gcs-lifecycle-policy.json and reapply
```

### If Warmup Job Removal Causes Issues

```bash
# Recreate warmup job
gcloud scheduler jobs create http mmm-warmup-job \
  --location=europe-west1 \
  --schedule="*/5 * * * *" \
  --uri="https://mmm-app-web-wuepn6nq5a-ew.a.run.app/" \
  --http-method=GET
```

---

## Questions or Issues?

If you encounter any issues during implementation:

1. **Check the logs:**
   ```bash
   gcloud logging read "resource.type=cloud_run_service" --limit=50
   ```

2. **Verify service health:**
   ```bash
   curl -I https://mmm-app-web-wuepn6nq5a-ew.a.run.app/
   ```

3. **Review recent changes:**
   ```bash
   gcloud run services describe mmm-app-web --region=europe-west1
   ```

4. **Contact:** Review ACTUAL_COST_ANALYSIS.md for detailed analysis

---

**Document Version:** 1.0  
**Last Updated:** 2026-02-02  
**Related Documents:**
- ACTUAL_COST_ANALYSIS.md
- scripts/cleanup_artifact_registry.sh
- gcs-lifecycle-policy.json
