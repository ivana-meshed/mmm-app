# Testing Guide: Cloud Run Core Allocation Fix

This guide provides step-by-step instructions for testing the Cloud Run core allocation fix after deployment.

## Overview

We've reduced the Cloud Run training job configuration from 8 vCPU to 4 vCPU to test if we can achieve better core utilization while reducing costs. This guide helps verify the changes work correctly.

## Pre-Deployment Checklist

- [ ] Review changes in this PR
- [ ] Understand the expected outcomes (see CLOUD_RUN_CORE_FIX.md)
- [ ] Ensure you have access to:
  - Google Cloud Console
  - `gcloud` CLI configured for the project
  - GitHub repository with PR branch

## Deployment Steps

### 1. Deploy to Dev Environment First

```bash
# The CI/CD pipeline will automatically deploy when you push to a feat-* or dev branch
# This PR branch (copilot/fix-cores-for-cloud-run) will trigger dev deployment

# Alternatively, manually apply Terraform changes:
cd infra/terraform
terraform init
terraform plan -var-file=envs/dev.tfvars
terraform apply -var-file=envs/dev.tfvars
```

### 2. Verify Terraform Changes Applied

```bash
# Check the training job configuration
gcloud run jobs describe mmm-app-dev-training \
  --region=europe-west1 \
  --format=json | jq '.template.template.spec.containers[0].resources'

# Expected output:
# {
#   "limits": {
#     "cpu": "4.0",
#     "memory": "16Gi"
#   }
# }
```

### 3. Check Environment Variables

```bash
# Verify the max cores environment variable
gcloud run jobs describe mmm-app-dev-training \
  --region=europe-west1 \
  --format=json | jq '.template.template.spec.containers[0].env[] | select(.name | contains("CORES"))'

# Expected output should include:
# {
#   "name": "R_MAX_CORES",
#   "value": "4"
# }
# {
#   "name": "ROBYN_DIAGNOSE_CORES",
#   "value": "auto"
# }
```

## Testing in Dev Environment

### Test 1: Run a Training Job

Trigger a training job through the Streamlit UI or API:

1. **Via UI**:
   - Navigate to the dev environment URL
   - Go to "Run Experiment" page
   - Configure a simple training job (low iterations for quick test)
   - Submit and note the execution ID

2. **Via gcloud CLI** (for quick testing):
   ```bash
   gcloud run jobs execute mmm-app-dev-training \
     --region=europe-west1 \
     --wait
   ```

### Test 2: Monitor Core Detection

Watch the logs in real-time during job execution:

```bash
# Stream logs (replace EXECUTION_NAME with actual execution)
gcloud logging read \
  "resource.type=cloud_run_job \
   AND resource.labels.job_name=mmm-app-dev-training \
   AND resource.labels.location=europe-west1" \
  --limit=100 \
  --format=json \
  --freshness=5m | jq -r '.[].textPayload' | grep -A 40 "CORE DETECTION ANALYSIS"
```

You should see output like:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔧 CORE DETECTION ANALYSIS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Environment Configuration:
  - R_MAX_CORES (requested):           4
  - OMP_NUM_THREADS:                   4
  - OPENBLAS_NUM_THREADS:              4

🔍 Detection Methods:
  - parallelly::availableCores():      [X] (cgroup-aware)
  - parallel::detectCores():           [Y] (system CPUs)
```

**Record these values** - they tell us what Cloud Run is actually providing:
- X = actual cores available (this is the key metric)
- Y = system CPUs detected

### Test 3: Analyze Results

Based on the `parallelly::availableCores()` value (X), determine which scenario occurred:

#### Scenario A: X = 2 (Same as before)
```
💡 Core Allocation Analysis:
  ⚠️  CORE SHORTFALL: Requested 4 but only 2 available (50.0% shortfall)
  🔍 This pattern (2 cores with 4 vCPU) suggests Cloud Run cgroups quota limitation
```

**Outcome**: Cost reduced by 50%, performance unchanged
**Next Step**: Consider reducing to 2 vCPU for additional savings

#### Scenario B: X = 3 or 4 (Improved!)
```
💡 Core Allocation Analysis:
  ✅ Core allocation is good: 4 cores available for 4 requested
```

**Outcome**: Cost reduced by 50%, performance improved by 50-100%
**Next Step**: Keep this configuration - it's optimal!

#### Scenario C: X = 1 (Worse)
```
💡 Core Allocation Analysis:
  ⚠️  CORE SHORTFALL: Requested 4 but only 1 available (75.0% shortfall)
```

**Outcome**: Cost reduced by 50%, but performance may be slower
**Next Step**: Revert or try 2 vCPU configuration

### Test 4: Verify Training Completes Successfully

Check that the training job completes without errors:

```bash
# Get execution status
gcloud run jobs executions describe EXECUTION_NAME \
  --job=mmm-app-dev-training \
  --region=europe-west1 \
  --format=json | jq -r '.status'

# Expected: "SUCCEEDED"
```

Check for any errors in logs:

```bash
gcloud logging read \
  "resource.type=cloud_run_job \
   AND resource.labels.job_name=mmm-app-dev-training \
   AND severity>=ERROR" \
  --limit=50 \
  --format=json
```

### Test 5: Compare Training Performance

**Baseline Performance** (8 vCPU, 2 cores):
- Typical training time: ~30 minutes (100 iterations, 5 trials)
- Varies by dataset size and complexity

**Measure New Performance**:

```bash
# Get training time from logs
gcloud logging read \
  "resource.type=cloud_run_job \
   AND resource.labels.job_name=mmm-app-dev-training \
   AND textPayload:\"Training completed in\"" \
  --limit=5 \
  --format=json | jq -r '.[].textPayload'

# Example output: "Training completed in 28.5 minutes"
```

**Performance Analysis**:
- If time ≈ 30 min → Same performance with 2 cores
- If time ≈ 15-20 min → Better performance with 3-4 cores
- If time > 30 min → Slower (investigate further)

### Test 6: Enable Full Diagnostics (If Needed)

If you see unexpected behavior, enable detailed diagnostics:

```bash
# Update the job to always run diagnostics
gcloud run jobs update mmm-app-dev-training \
  --region=europe-west1 \
  --set-env-vars=ROBYN_DIAGNOSE_CORES=always

# Run another test job
gcloud run jobs execute mmm-app-dev-training \
  --region=europe-west1 \
  --wait

# Review detailed diagnostic output
gcloud logging read \
  "resource.type=cloud_run_job \
   AND textPayload:\"CLOUD RUN CORE ALLOCATION DIAGNOSTIC\"" \
  --limit=200 \
  --format=json | jq -r '.[].textPayload'
```

This will show:
- cgroups CPU quota values
- CPU throttling statistics
- Memory limits
- Full system information

## Cost Verification

### Before Change (8 vCPU/32GB)
```bash
# Check historical billing
gcloud billing accounts list
gcloud beta billing accounts get-pricing --billing-account=BILLING_ACCOUNT_ID \
  --service=6F81-5844-456A  # Cloud Run service ID
```

### After Change (4 vCPU/16GB)
- Monitor Cloud Billing console
- Compare costs for equivalent workloads
- Expected: ~50% reduction in Cloud Run costs

## Production Deployment

After successful dev testing:

### 1. Review Test Results

Create a summary:
```markdown
## Dev Testing Results

**Core Detection**:
- parallelly::availableCores(): [X]
- parallel::detectCores(): [Y]
- Final cores used: [Z]

**Performance**:
- Baseline: 30 min (8 vCPU, 2 cores)
- New: [XX] min (4 vCPU, [Z] cores)
- Change: [+/-]% 

**Cost**:
- Expected savings: 50%
- Actual behavior: [matches expectation / unexpected]

**Recommendation**:
- [ ] Deploy to production
- [ ] Reduce further to 2 vCPU
- [ ] Revert to 8 vCPU
- [ ] Other: ___________
```

### 2. Deploy to Production

If dev testing is successful:

```bash
# Merge PR to main branch (triggers production CI/CD)
# OR manually apply Terraform:

cd infra/terraform
terraform plan -var-file=envs/prod.tfvars
terraform apply -var-file=envs/prod.tfvars
```

### 3. Monitor Production

Monitor the first 5-10 production training jobs:

```bash
# Watch for issues
gcloud logging read \
  "resource.type=cloud_run_job \
   AND resource.labels.job_name=mmm-app-training \
   AND severity>=WARNING" \
  --limit=100 \
  --format=json

# Check core detection in production
gcloud logging read \
  "resource.type=cloud_run_job \
   AND resource.labels.job_name=mmm-app-training \
   AND textPayload:\"CORE DETECTION ANALYSIS\"" \
  --limit=50 \
  --format=json | jq -r '.[].textPayload' | grep -A 30 "CORE DETECTION"
```

## Rollback Procedure

If issues occur in production:

### Quick Rollback via Terraform

```bash
cd infra/terraform

# Option 1: Revert git changes
git checkout HEAD~1 envs/prod.tfvars
terraform apply -var-file=envs/prod.tfvars

# Option 2: Manual override
# Edit envs/prod.tfvars to restore:
# training_cpu       = "8.0"
# training_memory    = "32Gi"
# training_max_cores = "8"
terraform apply -var-file=envs/prod.tfvars
```

### Immediate Rollback via gcloud (Faster)

```bash
# Update job directly (bypasses Terraform)
gcloud run jobs update mmm-app-training \
  --region=europe-west1 \
  --cpu=8 \
  --memory=32Gi \
  --set-env-vars=R_MAX_CORES=8,OMP_NUM_THREADS=8,OPENBLAS_NUM_THREADS=8

# Verify
gcloud run jobs describe mmm-app-training \
  --region=europe-west1 \
  --format=json | jq '.template.template.spec.containers[0].resources'
```

## Data Collection Template

Use this template to collect data from each test run:

```yaml
Test Run: [Date/Time]
Environment: [dev/prod]
Configuration:
  training_cpu: "4.0"
  training_memory: "16Gi"
  training_max_cores: "4"

Core Detection:
  R_MAX_CORES (requested): 4
  parallelly::availableCores(): [X]
  parallel::detectCores(): [Y]
  Final cores used: [Z]
  Buffer applied: [Yes/No]

Performance:
  Dataset: [name]
  Iterations: [N]
  Trials: [N]
  Training time: [XX] minutes
  Baseline time: [YY] minutes (8 vCPU)
  Performance change: [+/-]%

Status:
  Completed: [Yes/No]
  Errors: [None/Details]
  Warnings: [None/Details]

Cost:
  Estimated cost: $[XX]
  Baseline cost: $[YY] (8 vCPU)
  Savings: [%]

Notes:
[Any observations or anomalies]
```

## Troubleshooting

### Issue: Job fails with "8 simultaneous processes spawned"

This should NOT happen with the current code, but if it does:

**Diagnosis**: Safety buffer is not being applied correctly
**Solution**: Check run_all.R lines 275-283 for buffer logic

### Issue: No logs appear in Cloud Logging

**Diagnosis**: Logs may be delayed or logging filter is incorrect
**Solution**:
```bash
# Try broader filter
gcloud logging read \
  "resource.type=cloud_run_job" \
  --limit=50 \
  --freshness=10m

# Check specific execution
gcloud run jobs executions list \
  --job=mmm-app-dev-training \
  --region=europe-west1
```

### Issue: Training is significantly slower

**Diagnosis**: May have reduced to 1 core
**Solution**:
1. Check core detection logs
2. If 1 core: Try 2.0 or 3.0 vCPU instead
3. If 2 cores: Performance should be similar to baseline

### Issue: Terraform apply fails

**Diagnosis**: Annotation syntax or unsupported feature
**Solution**:
```bash
# Check Terraform validation
terraform validate

# Review error message for specific issue
# May need to remove unsupported annotations for Cloud Run Jobs v2
```

## Success Criteria

**Minimum Success**:
- ✅ Job completes successfully
- ✅ Core detection shows 2+ cores
- ✅ No errors in logs
- ✅ Performance maintained or improved
- ✅ Cost reduced by ~50%

**Optimal Success**:
- ✅ Core detection shows 3-4 cores
- ✅ Training time reduced by 50%+
- ✅ Cost reduced by 50%
- ✅ Consistent behavior across multiple runs

## Next Steps Based on Results

### If Scenario A (2 cores with 4 vCPU)
→ **Phase 2**: Test with 2 vCPU configuration for additional savings

### If Scenario B (3-4 cores with 4 vCPU)
→ **Done**: This is the optimal configuration

### If Scenario C (1 core with 4 vCPU)
→ **Investigate**: Try 2.0 vCPU or 6.0 vCPU, may need Google Cloud support

## Related Documentation

- `docs/CLOUD_RUN_CORE_FIX.md` - Complete fix documentation and context
- `docs/CORE_ALLOCATION_INVESTIGATION.md` - Diagnostic tool documentation
- `r/diagnose_cores.R` - Standalone diagnostic script
- `r/run_all.R` (lines 196-320) - Core detection implementation

## Support Contacts

If you encounter issues during testing:
1. Check this guide and related documentation
2. Review Cloud Run logs for error details
3. Run full diagnostics with `ROBYN_DIAGNOSE_CORES=always`
4. Escalate to team lead with collected data
5. Consider opening Google Cloud support ticket for quota issues
