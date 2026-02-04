# Implementation Guide for New Cost Automation

This document explains how to deploy the new cost optimization automations to your infrastructure.

## Overview

We've addressed all three issues:
1. ✅ Fixed cleanup script (now works correctly)
2. ✅ Added automated training cost tracking
3. ✅ Implemented lifecycle policies in Terraform and CI/CD

## Step 1: Test the Fixed Cleanup Script

The cleanup script now works correctly. Test it first:

```bash
# 1. Preview what would be deleted (dry run)
./scripts/cleanup_artifact_registry.sh

# 2. If the preview looks good, run actual cleanup
DRY_RUN=false KEEP_LAST_N=10 ./scripts/cleanup_artifact_registry.sh
```

**What changed:**
- Fixed image path construction to use full package name
- Better error handling with success/failure messages
- Corrected sort order for deletion

**Expected output:**
```
Processing: mmm-app
  Total images: 385
  Images to delete: 375
  Deleting: europe-west1-docker.pkg.dev/...@sha256:...
  ✓ Deleted successfully
```

## Step 2: Test Training Cost Analysis

Try the new cost analysis script:

```bash
# Analyze last 30 days
./scripts/get_training_costs.sh

# Analyze last 7 days
DAYS_BACK=7 ./scripts/get_training_costs.sh
```

**What it shows:**
- Number of jobs executed (dev and prod)
- Average duration per job
- Detailed cost breakdown
- Monthly projections

## Step 3: Deploy Terraform Changes

The Terraform files now manage lifecycle policies automatically.

### Important: Check if Resources Already Exist

First, check if the Artifact Registry and GCS bucket are managed by Terraform:

```bash
cd infra/terraform
terraform state list
```

**If you see these resources:**
- `google_artifact_registry_repository.mmm_repo`
- `google_storage_bucket.mmm_output`

Then resources are already managed. Skip to Step 3b.

**If you DON'T see them:**
They exist but aren't in Terraform state. You need to import them.

### Step 3a: Import Existing Resources (If Needed)

```bash
cd infra/terraform

# Import Artifact Registry
terraform import google_artifact_registry_repository.mmm_repo \
  projects/datawarehouse-422511/locations/europe-west1/repositories/mmm-repo

# Import GCS bucket
terraform import google_storage_bucket.mmm_output \
  mmm-app-output
```

**What this does:**
- Adds existing resources to Terraform state
- Allows Terraform to manage them without recreating
- **Does not modify** the existing resources

### Step 3b: Apply Terraform Changes

```bash
cd infra/terraform

# Review what will change
terraform plan -var-file="envs/prod.tfvars"

# Apply the changes
terraform apply -var-file="envs/prod.tfvars"
```

**What this adds:**
- Artifact Registry cleanup policies
- GCS lifecycle rules
- Outputs for repository URL and bucket name

**Expected changes:**
```
Terraform will perform the following actions:

  # google_artifact_registry_repository.mmm_repo will be updated in-place
  ~ resource "google_artifact_registry_repository" "mmm_repo" {
      + cleanup_policies {
          + id     = "keep-minimum-versions"
          + action = "KEEP"
          ...
        }
    }

  # google_storage_bucket.mmm_output will be updated in-place
  ~ resource "google_storage_bucket" "mmm_output" {
      + lifecycle_rule {
          + action {
              + type          = "SetStorageClass"
              + storage_class = "NEARLINE"
            }
          ...
        }
    }
```

### Step 3c: Do the Same for Dev Environment

```bash
cd infra/terraform

# For dev environment
terraform plan -var-file="envs/dev.tfvars"
terraform apply -var-file="envs/dev.tfvars"
```

## Step 4: Verify Policies Are Active

After applying Terraform:

### Check Artifact Registry Policies
```bash
gcloud artifacts repositories describe mmm-repo \
  --location=europe-west1 \
  --format="yaml(cleanupPolicies)"
```

**Expected output:**
```yaml
cleanupPolicies:
- id: keep-minimum-versions
  action: KEEP
  mostRecentVersions:
    keepCount: 10
- id: delete-untagged
  action: DELETE
  condition:
    olderThan: 2592000s
    tagState: UNTAGGED
```

### Check GCS Lifecycle Rules
```bash
gsutil lifecycle get gs://mmm-app-output
```

**Expected output:**
```json
{
  "lifecycle": {
    "rule": [
      {
        "action": {
          "type": "SetStorageClass",
          "storageClass": "NEARLINE"
        },
        "condition": {
          "age": 30,
          "matchesPrefix": ["training_data/"]
        }
      },
      ...
    ]
  }
}
```

## Step 5: Enable GitHub Actions Automation

The new workflow will run automatically, but you can also trigger it manually:

1. Go to **GitHub Actions** in your repository
2. Select **Cost Optimization Automation** workflow
3. Click **Run workflow**
4. Choose options:
   - Run cleanup: Yes/No
   - Run cost analysis: Yes/No
5. Click **Run workflow**

**What it does:**
- Runs cleanup script automatically
- Generates cost report
- Creates GitHub issue with findings
- Uploads report as artifact

**Schedule:** Runs every Sunday at 2 AM UTC automatically

## Step 6: Monitor Results

### Check Artifact Registry Size
```bash
gcloud artifacts repositories describe mmm-repo \
  --location=europe-west1 \
  --format="value(sizeBytes)"
```

**Before:** ~122,580,087,000 bytes (122.58 GB)
**After:** ~5,000,000,000-10,000,000,000 bytes (5-10 GB)

### Check GCS Storage Classes
```bash
gsutil ls -L gs://mmm-app-output/training_data/** | grep "Storage class"
```

**After 30 days:** Old objects will show "Storage class: NEARLINE"
**After 90 days:** Old objects will show "Storage class: COLDLINE"

### View Cost Reports

1. Go to **GitHub Actions**
2. Find completed **Cost Optimization Automation** runs
3. Download **cost-report** artifact
4. Check **Issues** for automated cost summaries

## Troubleshooting

### Issue: "Resource already exists" during Terraform apply

**Solution:** You need to import the resource first (see Step 3a)

### Issue: Cleanup script still failing

**Possible causes:**
1. Authentication: Run `gcloud auth login`
2. Permissions: Check you have `artifactregistry.repositories.update` permission
3. Wrong project: Verify `PROJECT_ID` is correct

**Debug:**
```bash
# Check current project
gcloud config get-value project

# List images manually
gcloud artifacts docker images list \
  europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo \
  --format="value(package,version)" | head -5
```

### Issue: Cost script returns no data

**Possible causes:**
1. No recent job executions
2. Wrong region or job name
3. Need `jq` and `bc` installed

**Solution:**
```bash
# Install dependencies
sudo apt-get install -y jq bc

# Check jobs exist
gcloud run jobs list --region=europe-west1

# Check executions manually
gcloud run jobs executions list \
  --job=mmm-app-training \
  --region=europe-west1 \
  --limit=5
```

### Issue: Terraform import fails

**Solution:**
1. Verify resource exists:
   ```bash
   gcloud artifacts repositories list --location=europe-west1
   gsutil ls
   ```

2. Check Terraform syntax:
   ```bash
   cd infra/terraform
   terraform validate
   ```

3. Use correct import format (see Step 3a)

## Expected Cost Savings Timeline

| Week | Action | Cumulative Savings/Year |
|------|--------|------------------------|
| **Week 1** | Run cleanup script manually | $0 (one-time cleanup) |
| **Week 2** | Apply Terraform policies | $135 (ongoing automatic) |
| **Week 3** | Verify automation working | $138 (includes GCS) |
| **Ongoing** | Automatic cleanup & monitoring | $138/year maintained |

## Next Steps After Implementation

1. **Week 1-2:** Monitor first cleanup and policy activation
2. **Week 3-4:** Review first automated cost report
3. **Month 2:** Verify storage classes are changing automatically
4. **Month 3:** Compare actual costs to projections

## Rollback Procedure

If you need to rollback changes:

### Rollback Terraform
```bash
cd infra/terraform

# Remove cleanup policies
terraform state rm google_artifact_registry_repository.mmm_repo
terraform state rm google_storage_bucket.mmm_output

# Or revert to previous state
git checkout HEAD~1 infra/terraform/artifact_registry.tf
git checkout HEAD~1 infra/terraform/storage.tf
```

### Disable GitHub Workflow
```bash
# Disable the workflow file
git mv .github/workflows/cost-optimization.yml \
      .github/workflows/cost-optimization.yml.disabled
```

## Success Criteria

After full deployment, you should see:

✅ Cleanup script completes without errors
✅ Artifact Registry size reduced to <10 GB
✅ Terraform shows lifecycle policies active
✅ Weekly GitHub Actions runs successfully
✅ Cost reports generated automatically
✅ Old GCS data moved to cheaper storage classes

## Questions?

- Review `scripts/COST_AUTOMATION_README.md` for detailed script documentation
- Check `ACTUAL_COST_ANALYSIS.md` for cost breakdown
- See `COST_OPTIMIZATION_IMPLEMENTATION.md` for manual procedures

---

**Last Updated:** 2026-02-03
**Version:** 1.0
