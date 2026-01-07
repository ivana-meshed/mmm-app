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

Implemented a **multi-layered conservative approach with smart buffering**:

### 1. Multiple Detection Methods
```r
available_cores_parallelly <- parallelly::availableCores()
available_cores_parallel <- parallel::detectCores()
available_cores <- min(available_cores_parallelly, available_cores_parallel)
```

### 2. Smart Safety Buffer
```r
actual_cores <- min(requested_cores, available_cores)

# Only apply -1 buffer if we have enough cores and we're using the requested amount
if (actual_cores > 2 && actual_cores >= requested_cores) {
    safe_cores <- max(1, actual_cores - 1)  # Apply buffer
} else {
    safe_cores <- max(1, actual_cores)  # Use what we have
}
```

The -1 buffer is **conditionally applied**:
- **Applied** when we have > 2 cores AND we're at/above requested amount (prevents "8 simultaneous processes spawned")
- **NOT applied** when Cloud Run severely limits cores (e.g., 2 when 8 requested) - use what's available
- This prevents wasting cores in already-constrained environments

### 3. Enhanced Logging

Added comprehensive logging before `robyn_run()` to diagnose issues:
- Training parameters (iterations, trials, cores)
- System information (R version, platform, memory, CPUs)
- Data dimensions (rows, columns, date range)
- Model configuration (variables, hyperparameters)
- Core detection details from all methods

### 4. Enhanced Error Capture

Error messages now include:
- All core detection values from different methods
- The conservative estimate used
- Future workers count
- System CPU count

## Example Scenarios

### Scenario 1: Full 8 vCPU Available (Ideal Case)
```
Requested (R_MAX_CORES):           8
Available (parallelly):             8
Available (parallel::detectCores): 8
Conservative estimate:              8
Actual cores to use:                8
Safety buffer applied:              Yes (-1)
Final cores for training:           7  ← Prevents "8 simultaneous processes" error
```

### Scenario 2: Cloud Run Limits to 2 Cores (Current Issue)
```
Requested (R_MAX_CORES):           8
Available (parallelly):             2
Available (parallel::detectCores): 8
Conservative estimate:              2
Actual cores to use:                2
Safety buffer applied:              No
Final cores for training:           2  ← Use all available, don't waste with buffer
```

### Scenario 3: Moderate Limitation (6 cores)
```
Requested (R_MAX_CORES):           8
Available (parallelly):             6
Available (parallel::detectCores): 8
Conservative estimate:              6
Actual cores to use:                6
Safety buffer applied:              No
Final cores for training:           6  ← Use available since below requested
```

### Scenario 4: Very Limited Resources
```
Requested (R_MAX_CORES):           8
Available (parallelly):             1
Available (parallel::detectCores): 2
Conservative estimate:              1
Actual cores to use:                1
Safety buffer applied:              No
Final cores for training:           1  ← Use what we have (≤2 threshold)
```

## Why This Works

1. **Multiple Methods**: Different detection methods may report different values. Using the minimum ensures we don't overcommit.

2. **Smart Safety Buffer**: 
   - The `-1` buffer is **only applied when we're at or above the requested cores**
   - This prevents the "X simultaneous processes spawned" error in ideal scenarios
   - When Cloud Run already limits cores below requested, we use all available
   - Prevents wasting scarce resources with unnecessary buffering

3. **Minimum of 1**: Ensures training can always proceed with at least 1 core, even in very constrained environments.

4. **Adaptive to Reality**: Recognizes when Cloud Run is imposing severe limitations and adapts accordingly

## Trade-offs

- **Pro**: Training jobs succeed reliably
- **Pro**: Better error messages for debugging
- **Pro**: Adapts to Cloud Run's actual core allocation
- **Pro**: Uses all available cores when already constrained
- **Con**: May use 1 fewer core than theoretically possible **only in ideal scenarios** (minimal performance impact)

For an 8 vCPU machine with full availability:
- **Before**: Failed with "8 simultaneous processes spawned"
- **After**: Succeeds with 7 cores (12.5% slower, but works reliably)

For an 8 vCPU machine with 2 cores available (current Cloud Run issue):
- **Before my fix**: Would use 2 cores (if error was fixed)
- **My initial fix**: Used only 1 core (wasted resources)
- **This improved fix**: Uses 2 cores (optimal for constrained environment)

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

# Smart buffer logic:
if (actual_cores > 2 && actual_cores >= training_max_cores) {
    final_cores = actual_cores - 1  # Apply safety buffer
} else {
    final_cores = actual_cores       # Use what's available
}
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
