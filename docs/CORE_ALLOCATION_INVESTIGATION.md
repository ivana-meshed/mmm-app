# Cloud Run Core Allocation Investigation Guide

## Overview

This guide provides step-by-step instructions to investigate why Cloud Run with 8 vCPU is only providing 2 actual cores to the training container.

## Automated Diagnostic Tool

We've implemented an automated diagnostic script that runs when core allocation issues are detected.

### How It Works

1. **Automatic Detection**: The diagnostic runs automatically when:
   - Available cores < 50% of requested cores
   - Or when `ROBYN_DIAGNOSE_CORES=always` environment variable is set

2. **What It Checks**:
   - Environment variables (R_MAX_CORES, OMP_NUM_THREADS, etc.)
   - R core detection methods (parallel, parallelly)
   - Linux cgroups CPU quota and limits
   - System CPU information
   - Memory allocation
   - Future package configuration
   - Process limits (ulimit)
   - Cloud Run specific metadata
   - CPU throttling statistics

3. **Output**: Detailed diagnostic report with:
   - Current values for all checks
   - Identified root cause
   - Specific recommendations to fix the issue

## Manual Investigation Steps

If you need to run diagnostics manually or investigate further:

### Step 1: Enable Diagnostic Mode

Add environment variable to Cloud Run Job configuration:

```bash
# In Terraform (infra/terraform/main.tf)
env {
  name  = "ROBYN_DIAGNOSE_CORES"
  value = "always"  # Options: "always", "auto", "never"
}
```

Or test locally:
```bash
export ROBYN_DIAGNOSE_CORES=always
Rscript r/run_all.R
```

### Step 2: Run Standalone Diagnostic

You can also run the diagnostic script independently:

```bash
# SSH into Cloud Run job (if running) or run locally
Rscript r/diagnose_cores.R
```

This will output:
```
════════════════════════════════════════════════════════════════════════
🔍 CLOUD RUN CORE ALLOCATION DIAGNOSTIC
════════════════════════════════════════════════════════════════════════

📋 Step 1: Environment Variables
─────────────────────────────────────────────────────────────────────
  ✓ R_MAX_CORES                 = 8
  ✓ OMP_NUM_THREADS             = 8
  ...

📋 Step 2: R Core Detection Methods
─────────────────────────────────────────────────────────────────────
  parallel::detectCores()           = 8
  parallel::detectCores(logical=F)  = 8 (physical)
  parallelly::availableCores()      = 2 (cgroup-aware)

📋 Step 3: Linux Cgroups CPU Quota
─────────────────────────────────────────────────────────────────────
  ✓ cpu.cfs_quota_us (v1)              = 200000
  ✓ cpu.cfs_period_us (v1)             = 100000

  📊 Calculated CPU Quota:
     cgroups v1: 2.00 CPUs (quota=200000 / period=100000)

... [more diagnostic output]

📋 Step 9: Analysis & Recommendations
─────────────────────────────────────────────────────────────────────

  Summary:
    • Requested cores (R_MAX_CORES):    8
    • System CPUs (detectCores):        8
    • Available (cgroup-aware):         2

  ⚠️  ISSUE DETECTED: Available cores (2) < Requested (8)

  🔍 Root Cause: cgroups v1 CPU quota
     • Quota: 200000 microseconds
     • Period: 100000 microseconds
     • Effective CPUs: 2.00

  💡 Solution: Update Cloud Run job configuration:
     Set training_max_cores to 2 in Terraform
```

### Step 3: Check Cloud Run Configuration

Verify the actual Cloud Run Job configuration:

```bash
# Check current job configuration
gcloud run jobs describe mmm-app-training \
  --region=europe-west1 \
  --format=json | jq '.template.template.spec.containers[0].resources'

# Expected output should show:
# {
#   "limits": {
#     "cpu": "8.0",
#     "memory": "32Gi"
#   }
# }
```

### Step 4: Check Active Executions

Monitor an active job execution to see real-time resource usage:

```bash
# List recent executions
gcloud run jobs executions list \
  --job=mmm-app-training \
  --region=europe-west1 \
  --limit=5

# Get details of a specific execution
gcloud run jobs executions describe EXECUTION_NAME \
  --job=mmm-app-training \
  --region=europe-west1
```

### Step 5: Check Container Logs for Diagnostic Output

```bash
# View logs from a specific execution
gcloud logging read \
  "resource.type=cloud_run_job \
   AND resource.labels.job_name=mmm-app-training \
   AND resource.labels.location=europe-west1" \
  --limit=100 \
  --format=json | jq -r '.[].textPayload' | grep -A 50 "CORE ALLOCATION DIAGNOSTIC"
```

## Common Issues and Solutions

### Issue 1: Cgroups CPU Quota Too Low

**Symptom**: Diagnostic shows `cpu.cfs_quota_us` / `cpu.cfs_period_us` = 2.00 CPUs

**Root Cause**: Cloud Run is enforcing a lower CPU quota than the vCPU allocation

**Solution**:
```hcl
# Option A: Match Terraform config to actual quota
# In infra/terraform/envs/prod.tfvars
training_max_cores = "2"  # Match the cgroup quota

# Option B: Request higher CPU allocation
# In infra/terraform/envs/prod.tfvars  
training_cpu = "4.0"  # Try intermediate value first
```

### Issue 2: Cold Start CPU Throttling

**Symptom**: Cores available increases after 30-60 seconds of runtime

**Root Cause**: Cloud Run throttles CPU during container startup

**Solution**:
```r
# In r/run_all.R, add warm-up period before training
if (Sys.getenv("K_SERVICE") != "") {
    cat("Warming up container (30 seconds)...\n")
    Sys.sleep(30)
    
    # Re-check cores after warm-up
    available_cores_after_warmup <- parallelly::availableCores()
    cat(sprintf("Cores after warm-up: %d\n", available_cores_after_warmup))
}
```

### Issue 3: CPU Boost Not Enabled

**Symptom**: Cores stay low even after warm-up

**Root Cause**: Cloud Run CPU boost is not enabled for the job

**Solution**:
```bash
# Update Cloud Run Job with CPU boost (if available)
gcloud run jobs update mmm-app-training \
  --region=europe-west1 \
  --cpu-boost  # Enable CPU boost during startup

# Or in Terraform (check if supported)
# This feature may not be available yet for Cloud Run Jobs
```

### Issue 4: Resource Contention on Host

**Symptom**: Cores vary between executions, diagnostic shows throttling events

**Root Cause**: Other workloads on the same host node consuming resources

**Solution**:
```hcl
# Try different CPU allocations to get on different host pools
# In infra/terraform/envs/prod.tfvars
training_cpu = "4.0"   # Instead of 8.0
training_memory = "16Gi"  # Instead of 32Gi
```

## Recommended Actions Based on Diagnostic Output

### If Diagnostic Shows: cgroups quota = 2 CPUs

**Immediate Fix**:
```hcl
# Update infra/terraform/envs/prod.tfvars
training_max_cores = "2"
```

**Long-term Investigation**:
1. File support ticket with Google Cloud explaining the discrepancy
2. Test with different vCPU allocations (4, 6, 8, 16)
3. Monitor if quota changes over time or with different regions

### If Diagnostic Shows: Throttling Events

**Check throttling statistics**:
```bash
# View in logs
gcloud logging read \
  "resource.type=cloud_run_job \
   AND textPayload:throttled" \
  --limit=20
```

**Solutions**:
- Reduce CPU-intensive operations during startup
- Spread work more evenly across time
- Consider using Cloud Run services instead of jobs if applicable

### If Diagnostic Shows: Unlimited quota but low cores

**This suggests**:
- Cold start limitation
- R or parallelly package issue
- Future package configuration problem

**Actions**:
1. Add warm-up period (see Issue 2 solution)
2. Update R packages:
   ```r
   install.packages(c("parallel", "parallelly", "future"))
   ```
3. Test outside Cloud Run to verify R package behavior

## Testing the Fix

After implementing a fix:

1. **Run a test job**:
   ```bash
   # Trigger a training job through the UI or API
   ```

2. **Check the diagnostic output**:
   ```bash
   # View logs
   gcloud logging read \
     "resource.type=cloud_run_job \
      AND resource.labels.job_name=mmm-app-training" \
     --limit=50 \
     --format=json | jq -r '.[].textPayload'
   ```

3. **Verify core usage**:
   - Look for "Final cores for training: X" in logs
   - Should match your expectations based on the fix

4. **Monitor training performance**:
   - Check training completion time
   - Compare with expected duration for the core count

## Environment Variables Reference

Control diagnostic behavior:

```bash
# Always run diagnostics (verbose output)
ROBYN_DIAGNOSE_CORES=always

# Auto-run diagnostics only when core discrepancy detected (default)
ROBYN_DIAGNOSE_CORES=auto

# Never run diagnostics
ROBYN_DIAGNOSE_CORES=never
```

## Files

- **r/diagnose_cores.R** - Standalone diagnostic script
- **r/run_all.R** - Integrated automatic diagnostics (lines 196-270)
- **docs/CORE_ALLOCATION_INVESTIGATION.md** - This guide

## Support

If diagnostics don't reveal the issue:

1. Capture full diagnostic output
2. Include Cloud Run Job configuration
3. Note the specific error message
4. File an issue with:
   - Diagnostic output
   - Expected vs actual core count
   - Cloud Run configuration
   - Region and timestamp of execution
