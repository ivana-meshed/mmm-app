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

Implemented a **multi-layered conservative approach**:

### 1. Multiple Detection Methods
```r
available_cores_parallelly <- parallelly::availableCores()
available_cores_parallel <- parallel::detectCores()
available_cores <- min(available_cores_parallelly, available_cores_parallel)
```

### 2. Safety Buffer
```r
safe_cores <- max(1, min(requested_cores, available_cores) - 1)
```

The `-1` buffer prevents Robyn's `.check_ncores()` from failing due to:
- System overhead
- Reserved cores
- Temporary resource constraints
- Docker/cgroup limitations

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

### Scenario 1: 8 vCPU Cloud Run Instance
```
Requested (R_MAX_CORES):           8
Available (parallelly):             8
Available (parallel::detectCores): 8
Conservative estimate:              8
Using (safe with -1 buffer):        7  ← Prevents "8 simultaneous processes" error
```

### Scenario 2: Limited Core Availability
```
Requested (R_MAX_CORES):           8
Available (parallelly):             6
Available (parallel::detectCores): 8
Conservative estimate:              6
Using (safe with -1 buffer):        5  ← Uses even fewer cores safely
```

### Scenario 3: Very Limited Resources
```
Requested (R_MAX_CORES):           8
Available (parallelly):             2
Available (parallel::detectCores): 2
Conservative estimate:              2
Using (safe with -1 buffer):        1  ← max(1, ...) ensures at least 1 core
```

## Why This Works

1. **Multiple Methods**: Different detection methods may report different values. Using the minimum ensures we don't overcommit.

2. **Safety Buffer**: The `-1` ensures Robyn never tries to spawn as many processes as the system reports, leaving room for:
   - System processes
   - Future's own overhead
   - Robyn's validation checks
   - Temporary resource constraints

3. **Minimum of 1**: Ensures training can always proceed with at least 1 core, even in very constrained environments.

## Trade-offs

- **Pro**: Training jobs succeed reliably
- **Pro**: Better error messages for debugging
- **Con**: May use 1 fewer core than theoretically possible (minimal performance impact)

For an 8 vCPU machine:
- **Before**: Failed with "8 simultaneous processes spawned"
- **After**: Succeeds with 7 cores (12.5% slower, but works reliably)

## Configuration

The core allocation is controlled by Terraform variables:

```hcl
# infra/terraform/envs/prod.tfvars
training_max_cores = "8"  # Maximum requested
```

The actual cores used will be:
```
actual_cores = max(1, min(training_max_cores, available_cores) - 1)
```

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
