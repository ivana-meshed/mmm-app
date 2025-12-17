# 8 vCPU Upgrade Test Results - FAILED

**Date**: December 17, 2025  
**Test Environment**: Dev (mmm-app-dev-training)  
**Configuration**: 8 vCPU / 32GB  
**Result**: ❌ No improvement - still only 2 cores available

## Executive Summary

The upgrade from 4 vCPU to 8 vCPU **did not resolve the core allocation issue**. Despite successful deployment and correct cgroups quota (8.34 CPUs), the system still only uses 2 cores for training.

**Outcome**: Same performance at 2x the cost = **failed experiment**.

## Test Results

### Expected vs Actual

| Metric | Before (4 vCPU) | Expected (8 vCPU) | Actual (8 vCPU) | Result |
|--------|-----------------|-------------------|-----------------|--------|
| **vCPU Configured** | 4 | 8 | 8 | ✅ |
| **Actual Cores** | 2 | 6-8 | 2 | ❌ |
| **Training Time** | ~2.3 min | ~0.8 min | 2.28 min | ❌ |
| **Cost per Job** | $2.92 | $1.95 | $5.85 | ❌ Worse |
| **Performance Improvement** | - | 3-4x | 0% | ❌ |

### Diagnostic Output Analysis

#### System Configuration (Working as Expected)
```
Environment Check:
- CPU cores available: 8              ✅ Correct
- Memory available: 31Gi              ✅ Correct
- R_MAX_CORES: 8                      ✅ Correct
- parallel::detectCores(): 8          ✅ Correct
```

#### Cgroups Quota (Working as Expected)
```
cgroups v1 CPU quota:
  cpu.cfs_quota_us  = 834200
  cpu.cfs_period_us = 100000
  Effective CPUs    = 8.34            ✅ Correct (not the issue)
```

#### Core Detection (The Problem)
```
parallelly::availableCores() = 2     ❌ Still limited to 2
Final cores for training:     2      ❌ No improvement
```

### Key Observation: Unusual Warning

The logs show an interesting warning from `parallelly`:
```
Warning message:
In getCGroups1CpuQuota() :
  [INTERNAL]: Will ignore the cgroups CPU quota, because it is out of range [1,8]: 8.342
```

**Analysis**: The `parallelly` package is **ignoring** the cgroups quota of 8.34 CPUs because it's outside its expected range, and falling back to a default of 2 cores.

## Root Cause: parallelly Package Limitation

### The Issue

The `parallelly` R package has hardcoded logic that:
1. Reads the cgroups CPU quota (834200/100000 = 8.342)
2. Validates if it's in a "reasonable" range relative to `R_MAX_CORES` (8)
3. **Rejects the quota** if it's slightly above the expected value
4. **Falls back to a default** (appears to be 2 cores)

### Why This Happens

The cgroups quota (834200) is 4.2% higher than the expected 8.0 CPUs:
- Expected: 800000 microseconds (8.0 CPUs)
- Actual: 834200 microseconds (8.342 CPUs)
- Difference: 34200 microseconds (4.2% overallocation)

Cloud Run likely adds a small buffer (8.342 instead of exactly 8.0), but `parallelly` interprets this as invalid and ignores it.

### Impact

- Despite having 8 vCPU allocated correctly
- Despite cgroups quota being set correctly
- The `parallelly` package refuses to use more than 2 cores
- This is a **software limitation**, not a Cloud Run limitation

## Why the Upgrade to 8 vCPU Failed

### Hypothesis (Incorrect)
Higher vCPU tiers would bypass platform quotas and provide better core allocation.

### Reality (Correct)
The issue is not Cloud Run's quota enforcement, but the **R parallelly package's validation logic** rejecting the quota value.

### Evidence
1. Cgroups quota correctly shows 8.34 CPUs (not restricted)
2. System correctly detects 8 CPUs
3. parallelly explicitly warns it's ignoring the quota
4. Same 2-core limitation as 4 vCPU configuration

## Cost Impact

### Before (4 vCPU)
- Cost: $2.92 per job
- Performance: 2 cores, ~2.3 min
- Cost per core: $1.46

### After (8 vCPU)
- Cost: $5.85 per job (+100%)
- Performance: 2 cores, 2.28 min (same)
- Cost per core: $2.92 (+100%)

**Net Result**: Doubled cost with zero benefit.

## Alternative Solutions

Since 8 vCPU didn't help, we have these options:

### Option 1: Revert to 4 vCPU (Recommended Short-term) ⭐
**Action**: Reduce back to 4 vCPU to save money
**Rationale**: No benefit from 8 vCPU, cut costs in half
**Cost**: $2.92/job (50% savings vs current)
**Performance**: Same (2 cores)
**Effort**: 10 minutes (Terraform change)

### Option 2: Try 2 vCPU (Cost Optimization)
**Action**: Reduce to minimum that provides 2 cores
**Rationale**: Match allocation to actual usage
**Cost**: $1.46/job (75% savings vs 8 vCPU)
**Performance**: Likely same (2 cores)
**Risk**: Might get only 1 core
**Effort**: 10 minutes (Terraform change)

### Option 3: Fix parallelly Package Behavior
**Action**: Override parallelly's core detection
**Rationale**: Force it to recognize 8 cores
**Implementation**:
```r
# In r/run_all.R, override the detection:
options(parallelly.availableCores.fallback = 8)
# Or set environment variable before parallelly loads:
Sys.setenv("PARALLELLY_AVAILABLECORES_FALLBACK" = "8")
```
**Cost**: Same (8 vCPU)
**Performance**: Potentially use all 8 cores
**Risk**: May cause issues if cores aren't actually available
**Effort**: 1 hour (code change + testing)

### Option 4: Migrate to GKE Autopilot ⭐⭐
**Action**: Move training jobs to GKE
**Rationale**: More predictable CPU allocation, no parallelly quirks
**Cost**: Similar (~$0.05/vCPU-hour)
**Performance**: Guaranteed 8 cores
**Effort**: 2-3 days (significant refactor)
**Benefit**: Long-term stability

### Option 5: Try 16 vCPU (Last Resort)
**Action**: Test if 16 vCPU tier behaves differently
**Rationale**: Different tier might have different quota behavior
**Cost**: $11.70/job (2x more than 8 vCPU)
**Performance**: Unknown (might still be 2 cores)
**Risk**: Even higher cost with no guarantee of improvement
**Effort**: 10 minutes

## Recommendation: Three-Phase Approach

### Phase 1: Immediate Cost Reduction (Today)
**Action**: Revert to 4 vCPU configuration
**Justification**: No benefit from 8 vCPU, reduce costs immediately
**Impact**: 50% cost reduction

### Phase 2: Test parallelly Override (This Week)
**Action**: Try Option 3 (override parallelly detection)
**Test**: Deploy with forced 8-core setting
**Monitor**: Check if training actually uses more cores
**Decision**: Keep if works, revert if causes issues

### Phase 3: Long-term Solution (Next Month)
**Action**: Evaluate GKE Autopilot migration
**Timeline**: 2-3 week project
**Benefit**: Predictable, scalable, no software quirks
**Cost**: Similar to current setup

## Testing Checklist for Option 3 (parallelly Override)

If we want to try forcing 8 cores:

- [ ] Add `options(parallelly.availableCores.fallback = 8)` to r/run_all.R
- [ ] Test in dev environment
- [ ] Check logs for "Final cores for training: 8"
- [ ] Verify training completes without errors
- [ ] Monitor CPU usage in Cloud Run metrics
- [ ] Compare training time (should be ~4x faster if successful)
- [ ] If successful, deploy to production
- [ ] If fails, revert immediately

## Lessons Learned

1. **Assumption Validation**: Our hypothesis (higher vCPU tier = more cores) was incorrect
2. **Software Limitations**: The issue is R package behavior, not Cloud Run platform
3. **Cost Analysis**: Always test before scaling up resources
4. **Diagnostic Value**: The diagnostic tool correctly identified the issue (parallelly warning)
5. **Alternative Approaches**: Sometimes the solution isn't "more resources" but "different approach"

## Next Actions

**Immediate** (within 24 hours):
1. Decide: Revert to 4 vCPU or try parallelly override?
2. Update Terraform configuration accordingly
3. Deploy and validate

**Short-term** (this week):
1. If reverting: Document lessons learned, close issue
2. If trying override: Test in dev, monitor closely
3. Make final decision based on results

**Long-term** (next month):
1. Research GKE Autopilot migration
2. Prototype training job on GKE
3. Evaluate cost/benefit
4. Plan migration if beneficial

## Related Documentation

- `docs/CLOUD_RUN_CORE_FIX.md` - Original implementation plan
- `docs/CPU_ALLOCATION_UPGRADE.md` - Implementation details
- `docs/CORE_ALLOCATION_INVESTIGATION.md` - Diagnostic guide
- `docs/CLOUD_RUN_CPU_SOLUTION.md` - Alternative solutions

## Conclusion

The 8 vCPU upgrade was a **failed experiment** that taught us the issue is not Cloud Run's platform quotas, but the R `parallelly` package's validation logic rejecting Cloud Run's cgroup configuration.

**Recommended Next Step**: Revert to 4 vCPU to save costs, then either:
1. Try overriding `parallelly` detection (quick test), or
2. Plan GKE Autopilot migration (long-term solution)

**Status**: Awaiting decision on next course of action.
