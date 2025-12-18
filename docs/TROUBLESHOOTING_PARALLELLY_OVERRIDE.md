# Troubleshooting Guide: Parallelly Override for 8 vCPU Training

## Quick Diagnostic Checklist

Use this checklist to verify the parallelly override is working correctly.

### ✅ Step 1: Check Environment Variable is Set

**Command:**
```bash
gcloud run jobs describe mmm-app-dev-training \
  --region=europe-west1 \
  --format="yaml(template.template.containers[0].env)"
```

**What to look for:**
```yaml
env:
- name: PARALLELLY_OVERRIDE_CORES
  value: '8'
```

**If missing:**
```bash
# Set manually (temporary)
gcloud run jobs update mmm-app-dev-training \
  --region=europe-west1 \
  --set-env-vars PARALLELLY_OVERRIDE_CORES=8

# Or deploy via Terraform (permanent)
cd infra/terraform
terraform apply -var-file=envs/dev.tfvars
```

### ✅ Step 2: Check Override Activation in Logs

**What to search for in job logs:**
```
🔧 PARALLELLY CORE OVERRIDE ACTIVE
```

**If you see this:** ✅ Override code is running

**If you DON'T see this:**
- ❌ PARALLELLY_OVERRIDE_CORES env var is not set
- ❌ Container image doesn't have the latest code
- Check Step 1 and Step 6

**Full expected output:**
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔧 PARALLELLY CORE OVERRIDE ACTIVE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚙️  Setting R_PARALLELLY_AVAILABLECORES_FALLBACK=8
📍 Timing: BEFORE library(Robyn) loads (critical for success)
🎯 Expected: parallelly::availableCores() will return 8
📝 Override source: PARALLELLY_OVERRIDE_CORES env var

✅ Override configured - will verify after Robyn loads
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### ✅ Step 3: Check Override Verification

**What to search for in job logs:**
```
✅ OVERRIDE VERIFICATION: SUCCESS
```

**If you see SUCCESS:** ✅ Override is working correctly

**If you see FAILED:**
```
❌ OVERRIDE VERIFICATION: FAILED
   Expected: 8 cores (from override)
   Actual:   2 cores (from parallelly)
```
- ❌ Override was set too late (after parallelly loaded)
- ❌ Container image has old code
- See Step 6 for fix

### ✅ Step 4: Check Job Parameters

**What to search for in job logs:**
```
✅ Cloud Run Job Parameters
   max_cores  : 8
```

**If you see:** `max_cores : 8` → ✅ Correct  
**If you see:** `max_cores : 2` → ❌ Override not working

### ✅ Step 5: Check Core Detection Results

**What to search for in job logs:**
```
🔧 CORE DETECTION ANALYSIS
  - parallelly::availableCores():      8 (cgroup-aware)
  - parallel::detectCores():           8 (system CPUs)
  - Final cores for training:           7
```

**Expected values:**
- parallelly::availableCores(): **8** (not 2)
- parallel::detectCores(): **8**
- Final cores for training: **7** or **8** (8 with -1 safety buffer)

**If you see 2 cores:** ❌ Override failed, see Step 6

### ✅ Step 6: Check Container Image Version

**Command:**
```bash
gcloud run jobs describe mmm-app-dev-training \
  --region=europe-west1 \
  --format="value(template.template.containers[0].image)"
```

**What to check:**
The image tag should be recent (after Dec 18, 2025 fix).

**If image is old:**
```bash
# Trigger rebuild via CI/CD
git commit --allow-empty -m "Rebuild container with parallelly fix"
git push
```

Wait for CI/CD to complete, then test again.

### ✅ Step 7: Check Training Performance

**Expected:**
- Training time: **0.6-0.8 minutes** (was 2.3 minutes)
- Performance improvement: **3-4x faster**

**If still slow (~2.3 minutes):**
- ❌ Training is still using only 2 cores
- Review Steps 1-6

## Common Issues and Solutions

### Issue 1: No Override Message in Logs

**Symptoms:**
- No "🔧 PARALLELLY CORE OVERRIDE ACTIVE" message
- May see: "💡 No parallelly override configured"

**Cause:**
`PARALLELLY_OVERRIDE_CORES` environment variable is not set in Cloud Run Job

**Solution:**
```bash
gcloud run jobs update mmm-app-dev-training \
  --region=europe-west1 \
  --set-env-vars PARALLELLY_OVERRIDE_CORES=8
```

### Issue 2: Override Message Appears but Verification Fails

**Symptoms:**
```
🔧 PARALLELLY CORE OVERRIDE ACTIVE
...
❌ OVERRIDE VERIFICATION: FAILED
   Expected: 8, Actual: 2
```

**Cause:**
Container has old code where override was set AFTER library(Robyn) loaded

**Solution:**
Rebuild container with latest code:
```bash
# Trigger CI/CD
git commit --allow-empty -m "Rebuild with parallelly fix"
git push
```

### Issue 3: Override Works but Still Shows 2 Cores

**Symptoms:**
- "✅ OVERRIDE VERIFICATION: SUCCESS"
- But `max_cores : 2` in job parameters

**Cause:**
Job config JSON file was created before override was deployed

**Solution:**
Create a **new** job configuration via UI (don't reuse old configs)

### Issue 4: Container Image Not Updating

**Symptoms:**
- Pushed code but logs still show old behavior
- Container image SHA hasn't changed

**Cause:**
CI/CD deployment may have failed

**Solution:**
1. Check GitHub Actions workflow status
2. Look for deployment errors in CI/CD logs
3. Manually trigger deployment if needed

## Log Analysis Examples

### ✅ WORKING (Correct Logs)

```
🔧 PARALLELLY CORE OVERRIDE ACTIVE
⚙️  Setting R_PARALLELLY_AVAILABLECORES_FALLBACK=8
📍 Timing: BEFORE library(Robyn) loads (critical for success)
✅ Override configured - will verify after Robyn loads

[Robyn loads...]

✅ OVERRIDE VERIFICATION: SUCCESS
   parallelly::availableCores() = 8 (matches override)

✅ Cloud Run Job Parameters
   max_cores  : 8

🔧 CORE DETECTION ANALYSIS
  - parallelly::availableCores():      8
  - Final cores for training:           7

🎬 Starting robyn_run() with 7 cores...
Training completed in 0.7 minutes
```

### ❌ NOT WORKING (Override Not Set)

```
💡 No parallelly override configured (PARALLELLY_OVERRIDE_CORES not set)
   Will use default core detection (may result in only 2 cores)

[Robyn loads...]

✅ Cloud Run Job Parameters
   max_cores  : 2

🔧 CORE DETECTION ANALYSIS
  - parallelly::availableCores():      2
  - Final cores for training:           2

🎬 Starting robyn_run() with 2 cores...
Training completed in 2.3 minutes
```

### ❌ NOT WORKING (Wrong Timing)

```
[Robyn loads FIRST...]

🔧 Overriding parallelly core detection with 8 cores
   Set R_PARALLELLY_AVAILABLECORES_FALLBACK=8

❌ OVERRIDE VERIFICATION: FAILED
   Expected: 8 cores (from override)
   Actual:   2 cores (from parallelly)
   The override did not take effect - parallelly loaded before env var was set

✅ Cloud Run Job Parameters
   max_cores  : 2

Training completed in 2.3 minutes
```

## Verification Commands

### Check Recent Job Executions
```bash
gcloud run jobs executions list \
  --job=mmm-app-dev-training \
  --region=europe-west1 \
  --limit=5 \
  --format="table(name,status,startTime,duration)"
```

### Get Logs for Specific Execution
```bash
# Replace EXECUTION_NAME with actual execution name
gcloud logging read \
  "resource.type=cloud_run_job \
   AND resource.labels.job_name=mmm-app-dev-training \
   AND labels.\"run.googleapis.com/execution_name\"=EXECUTION_NAME" \
  --limit=1000 \
  --format=text
```

### Search for Override Messages
```bash
gcloud logging read \
  "resource.type=cloud_run_job \
   AND resource.labels.job_name=mmm-app-dev-training \
   AND textPayload:\"PARALLELLY CORE OVERRIDE\"" \
  --limit=10
```

### Search for Verification Results
```bash
gcloud logging read \
  "resource.type=cloud_run_job \
   AND resource.labels.job_name=mmm-app-dev-training \
   AND textPayload:\"OVERRIDE VERIFICATION\"" \
  --limit=10
```

## Success Criteria

All of these should be true:
- ✅ "🔧 PARALLELLY CORE OVERRIDE ACTIVE" appears early in logs
- ✅ "✅ OVERRIDE VERIFICATION: SUCCESS" appears after Robyn loads
- ✅ `max_cores : 8` in job parameters
- ✅ `parallelly::availableCores() = 8` in core detection
- ✅ `Final cores for training: 7` or `8` (with safety buffer)
- ✅ Training time ~0.6-0.8 minutes (3-4x faster)
- ✅ No errors or job failures

## If All Else Fails

**Last resort troubleshooting:**

1. **Check R script directly in container:**
```bash
# Get the exact R script being used
gcloud artifacts docker images describe \
  europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-training:latest
```

2. **Manually verify environment in running job:**
- Add debug logging to print all environment variables
- Check if parallelly is somehow preloaded

3. **Test locally with Docker:**
```bash
docker build -f docker/Dockerfile.training -t test-training .
docker run -it -e PARALLELLY_OVERRIDE_CORES=8 test-training /bin/bash
# Then run: Rscript /app/run_all.R (with test job config)
```

## Related Documentation

- `docs/PARALLELLY_OVERRIDE_FIX.md` - Complete fix explanation
- `docs/8_VCPU_TEST_RESULTS.md` - Original problem analysis
- `docs/OVERRIDE_NOT_WORKING.md` - Deployment troubleshooting
- PR #142 - Full history of the issue
