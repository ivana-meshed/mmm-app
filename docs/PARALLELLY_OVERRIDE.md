# Parallelly Core Override Implementation

**Date**: December 17, 2025  
**Updated**: December 18, 2025  
**Status**: ✅ FIXED - See `PARALLELLY_OVERRIDE_FIX.md` for the correct implementation  
**Issue**: parallelly package rejects Cloud Run's cgroups quota (8.342) as "out of range"  
**Solution**: Override parallelly detection to force use of allocated cores

## ⚠️ IMPORTANT UPDATE (Dec 18, 2025)

**The original implementation in this document had a critical timing issue.**

The override code was placed AFTER `library(Robyn)`, which meant the parallelly package was already loaded before the environment variable was set. This caused the override to have no effect.

**✅ THE FIX HAS BEEN IMPLEMENTED**

See `docs/PARALLELLY_OVERRIDE_FIX.md` for:
- Detailed explanation of why the original implementation didn't work
- The correct implementation (override BEFORE library(Robyn))
- Testing and verification instructions
- Expected behavior after the fix

---

## Original Documentation (For Historical Reference)

## Problem

Testing showed that upgrading to 8 vCPU did not improve core allocation because:
1. Cloud Run correctly allocates 8.342 CPUs via cgroups
2. The `parallelly` R package rejects this as "out of range [1,8]"
3. Falls back to default of 2 cores
4. Training only uses 2 cores despite 8 vCPU allocation

## Solution: Force parallelly to Use All Cores

### Implementation

**R Code Changes** (`r/run_all.R`):
```r
# Override parallelly detection to force use of requested cores
override_cores <- Sys.getenv("PARALLELLY_OVERRIDE_CORES", "")
if (nzchar(override_cores)) {
    override_value <- as.numeric(override_cores)
    if (!is.na(override_value) && override_value > 0) {
        cat(sprintf("\n🔧 Overriding parallelly core detection with %d cores\n", override_value))
        options(parallelly.availableCores.fallback = override_value)
    }
}
```

**Terraform Configuration** (`infra/terraform/main.tf`):
```hcl
env {
  name  = "PARALLELLY_OVERRIDE_CORES"
  value = var.training_max_cores  # Set to "8" for 8 vCPU
}
```

### How It Works

1. **Before parallelly loads**, set the fallback option
2. When `parallelly::availableCores()` rejects the cgroups quota
3. It uses the fallback value (8) instead of default (2)
4. Training should now use all 8 cores

### Expected Outcome

**Before Override**:
- `parallelly::availableCores()` = 2
- Final cores for training: 2
- Training time: ~2.3 minutes

**After Override**:
- `parallelly::availableCores()` = 8 (overridden)
- Final cores for training: 8 (or 7 with -1 buffer)
- Training time: ~0.6 minutes (4x faster)

## Testing Plan

### Phase 1: Dev Environment (Immediate)

1. **Deploy Changes**:
   ```bash
   cd infra/terraform
   terraform apply -var-file=envs/dev.tfvars
   ```

2. **Trigger Test Job** via UI

3. **Check Logs**:
   ```bash
   gcloud logging read \
     "resource.type=cloud_run_job \
      AND resource.labels.job_name=mmm-app-dev-training \
      AND textPayload:\"Overriding parallelly\"" \
     --limit=10
   ```

4. **Verify Core Usage**:
   ```bash
   gcloud logging read \
     "resource.type=cloud_run_job \
      AND resource.labels.job_name=mmm-app-dev-training \
      AND textPayload:\"Final cores for training\"" \
     --limit=10
   ```

   **Look for**: `Final cores for training: 7` (or 8)

5. **Check Training Time**:
   - Should be ~0.6-0.8 minutes (3-4x faster than 2.3 min)
   - Compare with baseline

### Phase 2: Production (If Successful)

1. Deploy to production
2. Monitor first 3-5 jobs
3. Verify consistent improvement
4. Check for any errors or failures

## Success Criteria

### Minimum Success
- ✅ `parallelly::availableCores()` reports 8 (not 2)
- ✅ Final cores for training: 7-8 (not 2)
- ✅ No job failures or errors
- ✅ Training completes successfully

### Target Success
- ✅ Training time: ≤1 minute (vs 2.3 min baseline)
- ✅ 3-4x performance improvement
- ✅ Consistent results across multiple jobs
- ✅ CPU utilization near 100% in Cloud Run metrics

### Optimal Success
- ✅ Training time: ~0.6 minutes
- ✅ 4x performance improvement
- ✅ Cost per unit work: $0.73/core-hour (vs $2.92 current)
- ✅ All 8 cores actively used

## Risk Assessment

### Potential Issues

1. **Cores Not Actually Available**
   - **Risk**: Override claims 8 cores but only 2 are real
   - **Symptom**: Job hangs, timeouts, or OOM errors
   - **Mitigation**: Monitor job completion, check CPU metrics
   - **Rollback**: Remove override env var

2. **Robyn Validation Failure**
   - **Risk**: Robyn's internal checks reject 8 cores
   - **Symptom**: "8 simultaneous processes spawned" error
   - **Mitigation**: Already have -1 safety buffer logic
   - **Rollback**: Revert to auto-detection

3. **Memory Contention**
   - **Risk**: 8 parallel processes consume too much memory
   - **Symptom**: OOM kills, swap thrashing
   - **Mitigation**: 32GB should be sufficient for 8 cores
   - **Fallback**: Reduce to 4-6 cores if needed

### Safety Features

- ✅ Override is opt-in (only if env var set)
- ✅ Validates input (must be numeric and > 0)
- ✅ Logs when override is active
- ✅ Existing -1 buffer logic still applies
- ✅ Easy to disable (remove env var)

## Monitoring

### Key Metrics to Track

1. **Core Detection**:
   ```bash
   # Should see: "Overriding parallelly core detection with 8 cores"
   # Should see: "Final cores for training: 7" (or 8)
   ```

2. **Training Duration**:
   ```bash
   gcloud run jobs executions list \
     --job=mmm-app-dev-training \
     --region=europe-west1 \
     --format="table(name,status,duration)"
   ```

3. **CPU Utilization** (Cloud Run Console):
   - Check if CPU usage spikes near 100%
   - Verify sustained high utilization during training
   - Look for throttling events

4. **Job Success Rate**:
   - Monitor for any new failures
   - Check error logs for memory issues
   - Verify all jobs complete successfully

## Rollback Plan

If override causes issues:

### Option 1: Disable Override (Keep 8 vCPU)
```hcl
# In infra/terraform/main.tf, comment out or set to empty:
env {
  name  = "PARALLELLY_OVERRIDE_CORES"
  value = ""  # Disable override
}
```

**Result**: Back to 2 cores, but still paying for 8 vCPU

### Option 2: Revert to 4 vCPU (No Override)
```hcl
# In envs/prod.tfvars and envs/dev.tfvars:
training_cpu       = "4.0"
training_memory    = "16Gi"
training_max_cores = "4"

# Remove override from main.tf
```

**Result**: 2 cores at 50% lower cost

## Cost Analysis

### If Override Works (Expected)

**8 vCPU with 8 cores (override enabled)**:
- Cost: $5.85/job (before speed improvement)
- Training time: ~0.7 minutes (4x faster)
- Actual cost: ~$1.37/job (faster completion)
- Cost per core-hour: $0.69
- **Net benefit**: 53% cost reduction + 4x faster

### If Override Fails

**8 vCPU with 2 cores (no override)**:
- Cost: $5.85/job
- Training time: ~2.3 minutes
- Cost per core-hour: $2.92
- **Action**: Revert to 4 vCPU immediately

## Documentation Updates

Created:
- ✅ `docs/PARALLELLY_OVERRIDE.md` (this file)

Will update after testing:
- [ ] `docs/8_VCPU_TEST_RESULTS.md` - Add override test results
- [ ] `docs/CPU_ALLOCATION_UPGRADE.md` - Document successful solution
- [ ] `README.md` - Update if this becomes permanent solution

## Next Steps

**Immediate** (within 1 hour):
1. ✅ Code changes implemented
2. ⏳ Deploy to dev environment
3. ⏳ Run test training job
4. ⏳ Verify core usage in logs
5. ⏳ Check training performance

**Short-term** (within 24 hours):
1. ⏳ Analyze test results
2. ⏳ Decision: Keep override, disable, or revert?
3. ⏳ Deploy to production if successful
4. ⏳ Monitor production jobs

**Follow-up** (within 1 week):
1. ⏳ Collect performance data (10-20 jobs)
2. ⏳ Calculate actual cost savings
3. ⏳ Decide on permanent configuration
4. ⏳ Update documentation with final results

## Alternative If This Fails

If forcing 8 cores doesn't work or causes issues:

1. **Revert to 4 vCPU** (immediate cost savings)
2. **Plan GKE Autopilot migration** (long-term stability)
3. **File issue with parallelly package** (may help others)

## Conclusion

This override is a **tactical workaround** for the parallelly package's validation issue. It should allow us to use all 8 allocated cores without changing the underlying Cloud Run configuration.

**Expected Result**: 4x performance improvement at similar cost per unit of work.

**Confidence**: Medium-High (70% chance of success)
- The cores are allocated (cgroups shows 8.34)
- Override sets the right option
- Risk is that some other limitation prevents actual use

**Testing will confirm** if the cores are truly available and usable.
