# Cost Optimization Scripts

This directory contains automated scripts for managing and optimizing costs in the MMM application infrastructure.

## Scripts

### 1. cleanup_artifact_registry.sh

**Purpose:** Automatically cleans up old Docker images from Google Artifact Registry.

**Problem it solves:** Artifact Registry was accumulating 9,228 images (122.58 GB) costing $12.26/month due to no cleanup policy.

**Usage:**
```bash
# Dry run (preview what would be deleted)
./cleanup_artifact_registry.sh

# Actually delete old images (keeps last 10 by default)
DRY_RUN=false KEEP_LAST_N=10 ./cleanup_artifact_registry.sh

# Keep more versions
DRY_RUN=false KEEP_LAST_N=20 ./cleanup_artifact_registry.sh
```

**Configuration:**
- `KEEP_LAST_N`: Number of most recent versions to keep per image type (default: 10)
- `DRY_RUN`: Set to 'false' to actually delete images (default: true)

**Automated Execution:**
- Runs weekly via GitHub Actions (cost-optimization.yml)
- Can be manually triggered from GitHub Actions UI
- Terraform also manages cleanup policies for ongoing automatic cleanup

**Expected Savings:** $11-12/month ($132-144/year)

---

### 2. get_training_costs.sh

**Purpose:** Automatically calculates actual training job costs from Cloud Run executions.

**Problem it solves:** Need to track and report actual training costs instead of estimates.

**Usage:**
```bash
# Analyze last 30 days (default)
./get_training_costs.sh

# Analyze different time period
DAYS_BACK=7 ./get_training_costs.sh
DAYS_BACK=60 ./get_training_costs.sh
```

**What it calculates:**
- Total number of training jobs executed
- Success vs. failed job counts
- Average job duration
- Detailed cost breakdown (CPU + Memory)
- Cost per job
- Projected monthly costs

**Configuration:**
- `PROJECT_ID`: GCP project (default: datawarehouse-422511)
- `REGION`: GCP region (default: europe-west1)
- `DAYS_BACK`: Number of days to analyze (default: 30)

**Automated Execution:**
- Runs weekly via GitHub Actions (cost-optimization.yml)
- Generates cost report and creates GitHub issue with findings
- Can be manually triggered from GitHub Actions UI

**Output Example:**
```
Training Job Cost Analysis
==========================================
Project: datawarehouse-422511
Region: europe-west1
Period: Last 30 days

Analyzing: mmm-app-training
  Total executions: 45
  Configuration: 8 vCPU, 32 GB
  Successful: 42
  Failed: 3
  Total duration: 32400 seconds (540 minutes)
  Average duration: 12 minutes per job

  Cost Breakdown:
    CPU cost:    $6.22
    Memory cost: $2.59
    Total cost:  $8.81
    Per job:     $0.20

Summary (Last 30 days)
Total jobs executed: 45
Total training cost: $8.81
Average cost per job: $0.20

Projected monthly (30 days):
  Jobs: ~45
  Cost: $8.81
```

---

### 3. collect_cost_data.sh

**Purpose:** Collects comprehensive usage data for cost analysis.

**Usage:**
```bash
./collect_cost_data.sh
```

**What it collects:**
- GCS storage usage by prefix
- Artifact Registry image counts and sizes
- Cloud Run job execution history
- Cloud Logging volume estimates
- Secret Manager details
- Cloud Scheduler job status

**Output:** Creates timestamped files in `cost-analysis-data/` directory

---

## Automation Architecture

### Terraform Automation

The following resources are now managed by Terraform with automatic lifecycle policies:

**1. Artifact Registry (`infra/terraform/artifact_registry.tf`)**
- Cleanup policies that keep minimum 10 versions
- Delete untagged images after 30 days
- Delete tagged images after 90 days

**2. GCS Bucket (`infra/terraform/storage.tf`)**
- Move to Nearline storage after 30 days
- Move to Coldline storage after 90 days
- Delete training data after 180 days
- Delete training configs after 7 days

### GitHub Actions Automation

**Workflow:** `.github/workflows/cost-optimization.yml`

**Schedule:** Weekly on Sundays at 2 AM UTC

**What it does:**
1. Runs artifact registry cleanup
2. Calculates training costs for last 30 days
3. Checks repository size after cleanup
4. Uploads cost report as artifact
5. Creates GitHub issue with cost summary

**Manual Triggering:**
You can manually trigger the workflow from the GitHub Actions UI with options to:
- Run cleanup only
- Run cost analysis only
- Run both

### CI/CD Integration

The main CI/CD workflows (`ci.yml` and `ci-dev.yml`) push images with both SHA and `latest` tags. The Terraform cleanup policies ensure old images are automatically removed.

## Cost Savings Summary

| Optimization | Monthly Savings | Annual Savings | Status |
|--------------|-----------------|----------------|--------|
| Artifact Registry Cleanup | $11.26 | $135.12 | ✅ Automated |
| GCS Lifecycle Policies | $0.24 | $2.88 | ✅ Automated |
| **Total Base Infrastructure** | **$11.50** | **$138** | **✅ Complete** |

## Monitoring

### Check Artifact Registry Size
```bash
gcloud artifacts repositories describe mmm-repo \
  --location=europe-west1 \
  --format="value(sizeBytes)"
```

### Check GCS Storage Usage
```bash
gsutil du -sh gs://mmm-app-output
```

### View Cost Reports
Cost reports are available in GitHub Actions:
1. Go to Actions → Cost Optimization Automation
2. Select a workflow run
3. Download the cost report artifact

### Check Lifecycle Policies

**Artifact Registry:**
```bash
gcloud artifacts repositories describe mmm-repo \
  --location=europe-west1 \
  --format="yaml(cleanupPolicies)"
```

**GCS Bucket:**
```bash
gsutil lifecycle get gs://mmm-app-output
```

## Troubleshooting

### Cleanup Script Fails

If the cleanup script fails with permission errors:
```bash
# Check authentication
gcloud auth list

# Ensure you have the correct permissions
gcloud projects get-iam-policy datawarehouse-422511 \
  --flatten="bindings[].members" \
  --filter="bindings.members:user:YOUR_EMAIL"
```

### Cost Script Returns No Data

If the cost script returns no executions:
1. Check that jobs have run in the specified time period
2. Verify region is correct
3. Ensure job names haven't changed

### Terraform Import (if needed)

If resources already exist outside Terraform:

```bash
# Import Artifact Registry repository
terraform import google_artifact_registry_repository.mmm_repo \
  projects/datawarehouse-422511/locations/europe-west1/repositories/mmm-repo

# Import GCS bucket
terraform import google_storage_bucket.mmm_output mmm-app-output
```

## Future Enhancements

Potential improvements to consider:

1. **Cost Alerting:**
   - Set up GCP budget alerts
   - Email notifications for cost spikes
   - Slack integration for real-time alerts

2. **Enhanced Reporting:**
   - Cost trends over time
   - Cost breakdown by environment (dev vs prod)
   - Comparison to budget

3. **Optimization Recommendations:**
   - Identify underutilized resources
   - Suggest right-sizing opportunities
   - Detect cost anomalies

4. **Dashboard:**
   - Real-time cost visualization
   - Historical cost trends
   - Projected monthly costs

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Review GitHub Actions logs for automation runs
3. See `ACTUAL_COST_ANALYSIS.md` for detailed cost analysis
4. See `COST_OPTIMIZATION_IMPLEMENTATION.md` for implementation guide
