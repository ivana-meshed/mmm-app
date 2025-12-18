# Parallelly Override Not Working - Diagnosis

## Issue
Even after deploying the fix (commit d338e96) that loads the parallelly library before setting the fallback option, the override is still not taking effect. Logs show `max_cores: 2` and no override message appears.

## Root Cause
The `PARALLELLY_OVERRIDE_CORES` environment variable is **not being set in the Cloud Run Job**. 

Looking at the logs from the job that ran at 15:37 on 2025-12-17:
- ❌ No `"Overriding parallelly core detection with 8 cores"` message
- ❌ Still shows `max_cores: 2`
- ❌ The override code block never executed (no output from lines 217-220 in run_all.R)

This means `Sys.getenv("PARALLELLY_OVERRIDE_CORES", "")` is returning empty string.

## Why This Happens

### Terraform Configuration vs Runtime Environment

The Terraform configuration in `infra/terraform/main.tf` includes:
```hcl
env {
  name  = "PARALLELLY_OVERRIDE_CORES"
  value = var.training_max_cores  # Should be "8"
}
```

**However**, the Cloud Run Job needs to be **redeployed** for this environment variable to take effect.

### Deployment Process

When you push code changes to GitHub:
1. ✅ GitHub Actions triggers
2. ✅ Docker images are rebuilt with latest R code (d338e96)
3. ✅ Images are pushed to Artifact Registry
4. ⚠️ **Terraform apply** needs to run to update the Cloud Run Job definition
5. ⚠️ Cloud Run Job must be updated with new environment variables

## Verification Steps

### Step 1: Check if Terraform was applied

Check the CI/CD logs for the dev deployment:
```bash
# Look for "Terraform Apply" step
# Should show: "Apply complete! Resources: X added, Y changed, Z destroyed."
```

If Terraform didn't apply, or if the apply didn't include the environment variable change, the job won't have `PARALLELLY_OVERRIDE_CORES` set.

### Step 2: Verify Cloud Run Job Configuration

Use gcloud to check the actual environment variables in the Cloud Run Job:

```bash
gcloud run jobs describe mmm-app-dev-training \
  --region=europe-west1 \
  --format="yaml(template.template.containers[0].env)"
```

**Look for**:
```yaml
env:
  - name: PARALLELLY_OVERRIDE_CORES
    value: '8'
```

If this is missing, the job hasn't been updated with the new Terraform configuration.

### Step 3: Check Terraform State

If Terraform was applied but the env var is missing:

```bash
cd infra/terraform
terraform show | grep -A 5 "PARALLELLY_OVERRIDE_CORES"
```

This will show if Terraform thinks the env var is configured.

## Solutions

### Solution 1: Force Terraform Apply (Recommended)

If the environment variable is not in the Cloud Run Job definition:

```bash
cd infra/terraform
terraform init
terraform plan -var-file=envs/dev.tfvars
# Review the plan - should show env variable being added
terraform apply -var-file=envs/dev.tfvars
```

### Solution 2: Manually Verify CI/CD Completed

Check the GitHub Actions workflow for the branch:
1. Go to Actions tab in GitHub
2. Find the latest workflow run for `copilot/fix-cloud-run-core-issues` branch
3. Check if all steps completed successfully
4. Specifically check the "Terraform Apply" step

### Solution 3: Trigger a Redeploy

Push a small change to force a full redeploy:
```bash
# Add a comment to trigger redeploy
git commit --allow-empty -m "Trigger redeploy for PARALLELLY_OVERRIDE_CORES"
git push
```

## Expected Outcome After Fix

Once the Cloud Run Job has the `PARALLELLY_OVERRIDE_CORES=8` environment variable:

1. **Override message appears**:
   ```
   🔧 Overriding parallelly core detection with 8 cores (PARALLELLY_OVERRIDE_CORES)
   ```

2. **Core detection shows 8**:
   ```
   🔧 CORE DETECTION ANALYSIS
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   
   📊 Environment Configuration:
     - R_MAX_CORES (requested):           8
   
   🔍 Detection Methods:
     - parallelly::availableCores():      8 (cgroup-aware)
     - parallel::detectCores():           8 (system CPUs)
   
   Final cores for training:           7
   ```

3. **Training uses 7 cores** (with -1 safety buffer)

4. **Performance improvement**: ~0.6-0.8 minutes (vs 2.3 minutes)

## Alternative: Set Environment Variable Directly (Temporary Test)

To test if the override works without waiting for Terraform, you could temporarily set the env var via gcloud:

```bash
gcloud run jobs update mmm-app-dev-training \
  --region=europe-west1 \
  --update-env-vars PARALLELLY_OVERRIDE_CORES=8
```

Then run a test job. If this works, it confirms the issue is just the Terraform deployment, not the R code.

## Next Steps

1. **Verify** Cloud Run Job has `PARALLELLY_OVERRIDE_CORES=8` environment variable
2. **If missing**: Run Terraform apply manually or trigger CI/CD redeploy
3. **Test** with a new training job after verification
4. **Confirm** override message appears in logs and 7-8 cores are used

## Summary

The R code fix (d338e96) is correct, but the environment variable needs to be deployed to the Cloud Run Job via Terraform. The job is still running with the old configuration that doesn't include `PARALLELLY_OVERRIDE_CORES`.
