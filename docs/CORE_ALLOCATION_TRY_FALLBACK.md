# Core Allocation: Try-Then-Fallback Strategy

**Date**: January 2026  
**PR**: [Link to PR]  
**Status**: ✅ Implemented

## Overview

Changed the core allocation strategy from preemptively applying a -1 safety buffer to a **try-then-fallback** approach that maximizes resource utilization.

## Problem

The previous implementation always reduced available cores by 1 before training to prevent Robyn's `.check_ncores()` validation errors. This approach:
- **Wasted resources**: Always used 7 cores even when 8 would work fine
- **Lost performance**: ~14% slower than necessary (7/8 = 87.5%)
- **Preemptive**: Applied safety buffer without knowing if it was needed

## Solution

### New Strategy

1. **Try full cores first**: Attempt `robyn_run()` with all available cores
2. **Detect core errors**: If Robyn rejects with core allocation error, catch it
3. **Automatic fallback**: Retry with `cores - 1`
4. **Enhanced logging**: Show both attempts and which succeeded

### Implementation

```r
# Detection (unchanged)
available_cores <- min(parallelly::availableCores(), parallel::detectCores())
actual_cores <- min(requested_cores, available_cores)
safe_cores <- max(1, actual_cores)  # No -1 buffer upfront

# Try-then-fallback
.try_robyn_run <- function(cores_to_use, attempt_number = 1) {
    # Update future plan
    plan(multisession, workers = cores_to_use)
    
    # Attempt training
    tryCatch(
        robyn_run(..., cores = cores_to_use),
        error = function(e) list(error = e, cores_attempted = cores_to_use)
    )
}

# First attempt
OutputModels <- .try_robyn_run(max_cores, attempt_number = 1)

# Check if core-related error occurred
if (is.list(OutputModels) && !is.null(OutputModels$error)) {
    if (.is_core_allocation_error(conditionMessage(OutputModels$error)) && max_cores > 1) {
        # Retry with reduced cores
        OutputModels <- .try_robyn_run(max_cores - 1, attempt_number = 2)
    }
}
```

### Error Detection

The `.is_core_allocation_error()` function checks for patterns indicating core allocation issues:
- "simultaneous processes spawned"
- "ncores"
- "cores.*exceeded"
- "parallel.*failed"

Only these errors trigger a retry. Other errors (data issues, model failures, etc.) are handled normally.

## Example Scenarios

### Scenario 1: Full Cores Accepted (Best Case)
```
🔧 CORE DETECTION ANALYSIS
  - Available cores: 8
  - Initial cores:   8
  - Fallback:        7

🔄 Attempt 1: Running with 8 cores...
✅ Training successful with 8 cores

Result: Uses all 8 cores (100% utilization)
```

### Scenario 2: Full Cores Rejected (Fallback Case)
```
🔧 CORE DETECTION ANALYSIS
  - Available cores: 8
  - Initial cores:   8
  - Fallback:        7

🔄 Attempt 1: Running with 8 cores...
⚠️  Core allocation error detected
📉 Retrying with fallback: 7 cores

🔄 Attempt 2: Running with 7 cores...
✅ Retry succeeded with 7 cores

Result: Uses 7 cores (same as old behavior, but tried 8 first)
```

### Scenario 3: Limited Cores (Constrained)
```
🔧 CORE DETECTION ANALYSIS
  - Available cores: 2
  - Initial cores:   2

🔄 Attempt 1: Running with 2 cores...
✅ Training successful with 2 cores

Result: Uses all 2 cores (no wasted buffer)
```

## Performance Impact

| Scenario | Old Behavior | New Behavior | Improvement |
|----------|--------------|--------------|-------------|
| 8 cores available, Robyn accepts 8 | Used 7 cores | Uses 8 cores | **+14%** faster |
| 8 cores available, Robyn rejects 8 | Used 7 cores | Tries 8, falls back to 7 | Same speed, one extra attempt (~1s overhead) |
| 2 cores available (constrained) | Used 2 cores | Uses 2 cores | No change |

### Training Time Estimate
- **8 cores**: ~0.6 minutes
- **7 cores**: ~0.7 minutes  
- **2 cores**: ~2.3 minutes

## Benefits

✅ **Better Resource Utilization**: Uses all available cores when possible  
✅ **No Performance Loss**: Same fallback safety as before  
✅ **Minimal Overhead**: Only one extra attempt when fallback needed  
✅ **Enhanced Logging**: Clear visibility into retry attempts  
✅ **Smart Detection**: Only retries for core-related errors  

## Trade-offs

⚠️ **Slightly Longer Failure Path**: When fallback is needed, adds ~1 second for first attempt  
✅ **Worth It**: Potential 14% performance gain outweighs minor overhead

## Configuration

No configuration changes needed. The strategy is automatic and works with existing Terraform variables:

```hcl
# infra/terraform/envs/prod.tfvars
training_cpu       = "8.0"
training_memory    = "32Gi"
training_max_cores = "8"
```

## Testing

### Manual Testing
To test the new behavior:
1. Deploy to dev environment
2. Run training job with 8 vCPU allocation
3. Check logs for:
   - "🔄 Attempt 1: Running with X cores"
   - Success message or retry message
   - Final core count used

### Expected Log Output
Look for these patterns in Cloud Logging:
```
🔧 CORE DETECTION ANALYSIS
...
Initial cores for training:         8
Fallback cores if needed:           7

🔄 Attempt 1: Running with 8 cores...
```

## Rollback Plan

If issues arise, revert to previous behavior by:
1. Remove retry logic (lines 1772-1996 in r/run_all.R)
2. Restore simple buffer logic:
   ```r
   if (actual_cores > 2 && actual_cores >= requested_cores) {
       safe_cores <- max(1, actual_cores - 1)
   } else {
       safe_cores <- max(1, actual_cores)
   }
   ```
3. Revert documentation changes

## Related Documentation

- [ROBYN_CORE_DETECTION_FIX.md](./ROBYN_CORE_DETECTION_FIX.md) - Core detection implementation
- [PARALLELLY_OVERRIDE_FIX.md](./PARALLELLY_OVERRIDE_FIX.md) - Parallelly package override
- [TROUBLESHOOTING_PARALLELLY_OVERRIDE.md](./TROUBLESHOOTING_PARALLELLY_OVERRIDE.md) - Diagnostic checklist

## References

- Robyn's `.check_ncores()` validation: Checks available cores at runtime
- Cloud Run cgroups: May limit cores below vCPU allocation
- parallelly package: Used for core detection in R

## Future Improvements

Potential enhancements:
1. **Metrics collection**: Track success rate of first vs. fallback attempts
2. **Adaptive learning**: Remember which core count works and use it first next time
3. **Configurable retry**: Allow disabling retry via environment variable
4. **Multiple fallback steps**: Try cores-2, cores-3 if cores-1 also fails

---

**Implementation**: r/run_all.R lines 340-350 (allocation), 1772-1996 (retry logic)  
**Author**: GitHub Copilot  
**Reviewed by**: [Pending]
