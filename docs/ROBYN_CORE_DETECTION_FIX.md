# Robyn Core Detection Fix

## Problem

Training jobs were failing with the error:
```
robyn_run() FAILED
Message  : 8 simultaneous processes spawned
Call     : .check_ncores(cores)
```

This error occurred after PR #137 upgraded to 8 vCPU/32GB configuration with dynamic core detection.

## Root Cause

1. **PR #137 Changes**: Introduced dynamic core detection using `parallelly::availableCores()` to handle Cloud Run's unpredictable core allocation
2. **Robyn's Strict Validation**: Robyn's internal `.check_ncores()` function is more conservative than `parallelly::availableCores()` reports
3. **Cloud Run Behavior**: The number of vCPUs allocated doesn't guarantee the same number of actual usable cores
4. **System Overhead**: Some cores may be reserved for system processes, making them unavailable to Robyn

## Solution

Implemented a **try-then-fallback strategy** to maximize core utilization:

### 1. Multiple Detection Methods
```r
available_cores_parallelly <- parallelly::availableCores()
available_cores_parallel <- parallel::detectCores()
available_cores <- min(available_cores_parallelly, available_cores_parallel)
```

### 2. Try-Then-Fallback Strategy
```r
actual_cores <- min(requested_cores, available_cores)

# Try with full cores first (no preemptive -1 buffer)
safe_cores <- max(1, actual_cores)

# Attempt robyn_run() with full cores
# If it fails with core allocation error, retry with cores - 1
```

The new strategy:
- **First attempt**: Use full available cores (no -1 buffer)
- **On core error**: Automatically retry with cores - 1
- **Result**: Maximizes resource usage when possible, with automatic safety fallback

Benefits over previous approach:
- **Better resource utilization**: Uses all available cores when Robyn accepts them
- **Automatic fallback**: Only reduces cores if actually needed
- **Enhanced logging**: Shows both attempts and which succeeded

### 3. Enhanced Logging

Added comprehensive logging before `robyn_run()` to diagnose issues:
- Training parameters (iterations, trials, cores)
- System information (R version, platform, memory, CPUs)
- Data dimensions (rows, columns, date range)
- Model configuration (variables, hyperparameters)
- Core detection details from all methods
- **New**: Retry attempts and outcomes

### 4. Enhanced Error Capture

Error messages now include:
- All core detection values from different methods
- The conservative estimate used
- Future workers count
- System CPU count
- **New**: Cores attempted in first and retry attempts

## Example Scenarios

### Scenario 1: Full 8 vCPU Available - Accepted by Robyn (Ideal Case)
```
Requested (R_MAX_CORES):           8
Available (parallelly):             8
Available (parallel::detectCores): 8
Conservative estimate:              8
Initial cores for training:         8

🔄 Attempt 1: Running with 8 cores...
✅ Training successful with 8 cores  ← All cores utilized!
```

### Scenario 2: Full 8 vCPU Available - Rejected by Robyn (Fallback Case)
```
Requested (R_MAX_CORES):           8
Available (parallelly):             8
Available (parallel::detectCores): 8
Conservative estimate:              8
Initial cores for training:         8
Fallback cores if needed:           7

🔄 Attempt 1: Running with 8 cores...
⚠️  Core allocation error detected
📉 Retrying with fallback: 7 cores (reduced from 8)

🔄 Attempt 2: Running with 7 cores...
✅ Retry succeeded with 7 cores  ← Automatic fallback worked!
```

### Scenario 3: Cloud Run Limits to 2 Cores (Constrained Environment)
```
Requested (R_MAX_CORES):           8
Available (parallelly):             2
Available (parallel::detectCores): 8
Conservative estimate:              2
Initial cores for training:         2

🔄 Attempt 1: Running with 2 cores...
✅ Training successful with 2 cores  ← Uses all available, no fallback needed
```

### Scenario 4: Moderate Limitation (6 cores available, Robyn accepts)
```
Requested (R_MAX_CORES):           8
Available (parallelly):             6
Available (parallel::detectCores): 8
Conservative estimate:              6
Initial cores for training:         6

🔄 Attempt 1: Running with 6 cores...
✅ Training successful with 6 cores  ← All available cores used efficiently
```

## Why This Works

1. **Multiple Methods**: Different detection methods may report different values. Using the minimum ensures we don't overcommit.

2. **Try-Then-Fallback Strategy**: 
   - First attempt uses **all available cores** (no preemptive buffer)
   - Only reduces cores if Robyn actually rejects the allocation
   - Maximizes performance by using full capacity when possible
   - Automatic safety fallback when needed

3. **Intelligent Error Detection**: Only retries for core-related errors (e.g., "simultaneous processes spawned"), not for other failures

4. **Adaptive to Reality**: Recognizes when Cloud Run is imposing severe limitations and adapts accordingly

## Trade-offs

- **Pro**: Training jobs succeed reliably with automatic retry
- **Pro**: **Better performance**: Uses all cores when Robyn accepts them (no unnecessary -1)
- **Pro**: Better error messages showing retry attempts
- **Pro**: Adapts to Cloud Run's actual core allocation
- **Pro**: Optimal resource usage in all scenarios
- **Con**: Slightly longer failure path when cores need to be reduced (one extra attempt)

For an 8 vCPU machine with full availability:
- **Old approach**: Always used 7 cores (wasted 1 core preemptively)
- **New approach**: Tries 8 cores first, falls back to 7 only if needed
- **Result**: Better performance when 8 cores work, same safety when they don't

For an 8 vCPU machine with 2 cores available (Cloud Run limitation):
- **Behavior**: Uses all 2 cores efficiently (no unnecessary buffer)
- **Result**: Optimal for constrained environment

## Configuration

The core allocation is controlled by Terraform variables:

```hcl
# infra/terraform/envs/prod.tfvars
training_cpu       = "8.0"   # vCPU request
training_memory    = "32Gi"  # Memory allocation
training_max_cores = "8"     # Maximum requested
```

The actual cores used will be:
```
actual_cores = min(training_max_cores, available_cores)

# Try-then-fallback logic:
# 1. Try robyn_run() with actual_cores
# 2. If core allocation error detected:
#       Retry with actual_cores - 1
# 3. Otherwise: Use actual_cores successfully
```

### Important: Cloud Run Core Allocation Issue

**Current Problem**: Cloud Run with 8 vCPU is only providing **2 actual cores** to the container.

This could be due to:
1. **CPU throttling** - Cloud Run may throttle CPU allocation based on container startup time
2. **Cgroups quota** - Container cgroup limits may be set lower than vCPU count
3. **Cold start** - During cold starts, Cloud Run may initially provide fewer cores
4. **Resource contention** - Other workloads on the same host may affect allocation

**Investigation Steps**:
1. Check Cloud Run job logs for CPU throttling warnings
2. Verify `cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us` and `cat /sys/fs/cgroup/cpu/cpu.cfs_period_us`
3. Monitor if core count increases after warm-up period
4. Consider using `--cpu-boost` flag in Cloud Run (if available)
5. Test with different vCPU configurations (4, 6, 8, 16) to see allocation pattern

## Monitoring

Check the console logs for core allocation details:
```
🔧 Core Detection:
  - Requested (R_MAX_CORES):           8
  - Available (parallelly):             8
  - Available (parallel::detectCores): 8
  - Conservative estimate:              8
  - Using (safe with -1 buffer):        7

✅ Parallel processing initialized with 7 workers
```

## Related Issues

- PR #137: Introduced dynamic core detection
- PR #94: Training job resource sizing (4 vCPU/16GB configuration)
- Issue: "8 simultaneous processes spawned" error in Cloud Run

## References

- Code: `r/run_all.R` lines 196-232
- Robyn GitHub: Discussions about core allocation
- Cloud Run docs: CPU allocation behavior
- Parallelly package: Core detection methods
