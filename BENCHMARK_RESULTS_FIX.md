# Benchmark Results Not Found - Issue Analysis and Fixes

## Problem Report

User ran:
```bash
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --test-run
python scripts/process_queue_simple.py --loop --cleanup
```

**Issues:**
1. Expected 3 jobs but only 1 was executed
2. Job completed successfully but results folder was missing
3. Insufficient logging to diagnose the problem

## Root Cause Analysis

### Issue 1: Confusion about --test-run behavior

**What happened:**
- Script logged: "Generated 3 test variants"
- Then: "Submitted 1 benchmark jobs to queue"
- User expected 3 jobs to run

**Why:**
- The `--test-run` flag is **designed** to only run the first variant
- This is for quick validation before running expensive full benchmarks
- The messaging was confusing - it showed "3 variants" but didn't clearly explain only 1 would run

**Fix:**
- Added clear message: "Generated {N} total variants, but TEST MODE only runs the first one"
- Added helpful tip: "To run all {N} variants, use --config without --test-run"

### Issue 2: Results folder missing

**What happened:**
- Job completed with status "SUCCEEDED"
- Expected results at: `gs://mmm-app-output/robyn/default/de/20260212_134234_196/`
- Results folder was missing

**Potential causes:**
1. R script failed to upload results to GCS
2. Path mismatch between where Python expects and where R saves
3. Upload succeeded but took longer than expected
4. R script saved to different location than logged

**Fix:**
- Added `verify_results_exist()` function that:
  - Checks GCS for files at expected path
  - Waits up to 10 seconds with retry logic
  - Lists actual files found
  - Reports which key files are present/missing
- Now logs specific files found: model_summary.json, best_model_plots.png, console.log
- Provides manual check command if verification fails

### Issue 3: Insufficient logging

**What was missing:**
- Job name showed as "unknown" instead of variant name
- No visibility into what params were passed to Cloud Run job
- No confirmation that results actually uploaded
- Couldn't tell if issue was path construction or upload failure

**Fix:**
- Added detailed job configuration logging:
  - Shows country, revision, timestamp, data_gcs_path
  - Shows benchmark_variant and benchmark_test
- Improved job name extraction (checks params.benchmark_variant)
- Added result verification with detailed feedback
- Shows which files were found and which are missing

## Changes Made

### 1. scripts/benchmark_mmm.py (lines 1316-1322)

**Before:**
```python
print("\n🧪 TEST RUN MODE")
print(f"Iterations: 10 (reduced from {benchmark_config.iterations})")
print(f"Trials: 1 (reduced from {benchmark_config.trials})")
print(f"Testing variant: {variants[0].get('benchmark_variant', 'first')}")
```

**After:**
```python
print("\n🧪 TEST RUN MODE")
print(f"Generated {len(variants)} total variants, but TEST MODE only runs the first one")
print(f"Iterations: 10 (reduced from {benchmark_config.iterations})")
print(f"Trials: 1 (reduced from {benchmark_config.trials})")
print(f"Testing variant: {variants[0].get('benchmark_variant', 'first')}")
print(f"\n💡 To run all {len(variants)} variants, use --config without --test-run")
```

### 2. scripts/process_queue_simple.py

**Added verify_results_exist() function (64 lines):**
- Checks if results exist at GCS path
- Waits up to 10 seconds with retry logic
- Returns dict with: exists, files_found, message
- Handles errors gracefully

**Enhanced process_one_job() logging:**
- Extract and log benchmark_variant and benchmark_test from params
- Log complete job configuration being passed to Cloud Run
- Store benchmark info in job entry for later retrieval

**Enhanced update_running_jobs_status():**
- Better job name extraction (checks params.benchmark_variant first)
- Call verify_results_exist() after job completes
- Report which key files were found
- Warn about missing files
- Provide manual check commands

## Example Output

### Old Output:
```
✅ Job completed: unknown
   Results at: gs://mmm-app-output/robyn/default/de/20260212_134234_196/
```

### New Output (Success):
```
✅ Job completed: geometric
   Results at: gs://mmm-app-output/robyn/default/de/20260212_134234_196/
   Verifying results in GCS...
   ✓ Results verified: Found 12 files
   ✓ Key files found: model_summary.json, best_model_plots.png, console.log
```

### New Output (Failure):
```
✅ Job completed: geometric
   Results at: gs://mmm-app-output/robyn/default/de/20260212_134234_196/
   Verifying results in GCS...
   ⚠️  No files found after 10s timeout
   ⚠️  Results may still be uploading or job may have failed
   💡 Manually check: gsutil ls gs://mmm-app-output/robyn/default/de/20260212_134234_196/
```

## Next Steps for User

### If results still not found:

1. **Check the R job logs:**
   ```bash
   gcloud run jobs executions describe <execution-id> \
     --region=europe-west1 --format=yaml
   ```

2. **Check container logs:**
   ```bash
   gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=mmm-app-dev-training" \
     --limit=100 --format=json
   ```

3. **Manually check GCS:**
   ```bash
   gsutil ls -r gs://mmm-app-output/robyn/
   ```

4. **Look for error files in GCS:**
   ```bash
   gsutil ls gs://mmm-app-output/robyn/default/de/*/robyn_*_error.*
   ```

5. **Check if revision is wrong:**
   - Results are saved to: `robyn/{revision}/{country}/{timestamp}/`
   - If revision in params is not "default", results will be elsewhere
   - New logging shows what revision is being used

### To run all 3 variants:

```bash
# Remove --test-run flag
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json

# Then process
python scripts/process_queue_simple.py --loop --cleanup
```

## Testing Recommendations

1. Run with --test-run and check new log messages
2. Verify the result verification works (shows files found)
3. Check if results are actually at the logged path
4. If still missing, check container logs for R script errors
5. Verify timestamp consistency between Python and R

## Files Modified

- `scripts/benchmark_mmm.py` - Improved messaging (2 lines added)
- `scripts/process_queue_simple.py` - Enhanced logging and verification (118 lines added, 5 modified)

Total changes: +120 lines, -5 lines
