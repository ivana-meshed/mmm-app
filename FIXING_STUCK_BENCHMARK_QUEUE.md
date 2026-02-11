# Fixing Stuck Benchmark Queue

## Problem
Your benchmark was submitted but the queue isn't processing the jobs.

## Root Cause
The benchmark script was missing a critical field (`data_gcs_path`) that the queue processor needs to execute jobs.

## Fix Applied
The script has been updated to include the `data_gcs_path` field. The fix is in commit `f569e61`.

## How to Fix Your Stuck Benchmark

You have two options:

### Option 1: Resubmit (Recommended)

The easiest solution is to resubmit your benchmark with the fixed script:

```bash
# Pull the latest changes
git pull origin copilot/build-benchmarking-script

# Resubmit your benchmark
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json
```

This will create a new benchmark with properly formatted queue entries that will process correctly.

### Option 2: Manually Fix the Queue (Advanced)

If you want to salvage the existing benchmark (`adstock_comparison_20260211_095620`), you can manually fix the queue:

**Step 1: Download the queue**
```bash
# Using gsutil
gsutil cp gs://mmm-app-output/robyn-queues/default/queue.json ./queue_backup.json
```

**Step 2: Edit the queue JSON**

For each entry in the queue with `"status": "PENDING"`, add the `data_gcs_path` field to the `params`:

```json
{
  "id": 1,
  "status": "PENDING",
  "params": {
    "country": "de",
    "paid_media_spends": [...],
    // ADD THIS LINE:
    "data_gcs_path": "gs://mmm-app-output/mapped-datasets/de/20251211_115528/raw.parquet"
  }
}
```

The path format is:
```
gs://{bucket}/mapped-datasets/{country}/{data_version}/raw.parquet
```

Where:
- `{bucket}` = `mmm-app-output` (or your GCS bucket)
- `{country}` = `de` (lowercase)
- `{data_version}` = `20251211_115528` (from your benchmark config)

**Step 3: Upload the fixed queue**
```bash
gsutil cp queue_backup.json gs://mmm-app-output/robyn-queues/default/queue.json
```

**Step 4: Verify processing**

Go to the Streamlit app → "Run Experiment" → "Queue Monitor" tab and check that:
1. Jobs status changes from `PENDING` → `LAUNCHING` → `RUNNING`
2. Jobs eventually complete with `SUCCEEDED` status

## Verification

After applying either fix, verify the queue is processing:

1. **Check queue status in Streamlit**:
   - Navigate to "Run Experiment" → "Queue Monitor"
   - Refresh the page (or wait for auto-refresh)
   - Status should progress: PENDING → LAUNCHING → RUNNING → SUCCEEDED

2. **Check Cloud Logging** (optional):
   ```
   [QUEUE] Attempting to launch job 1
   [QUEUE] Job params: country=de, revision=default, iterations=2000
   [QUEUE] Successfully launched job 1
   ```

3. **Check GCS for results** (after completion):
   ```
   gs://mmm-app-output/robyn/default/de/{timestamp}/
   ├── model_summary.json
   ├── OutputCollect.RDS
   └── ... (other output files)
   ```

## What Was Wrong

The original benchmark script created queue entries like this:

```json
{
  "params": {
    "country": "de",
    "paid_media_spends": ["GA_SUPPLY_COST", ...],
    "paid_media_vars": ["GA_SUPPLY_SESSIONS", ...],
    // ❌ Missing data_gcs_path!
  }
}
```

The queue processor (`prepare_and_launch_job`) requires either:
- `data_gcs_path` (for GCS-based workflow), OR
- `table` or `query` (for Snowflake-based workflow)

Without either field, the job couldn't determine where to get the training data from, so it never launched.

## What Was Fixed

The script now creates queue entries with the required field:

```json
{
  "params": {
    "country": "de",
    "paid_media_spends": ["GA_SUPPLY_COST", ...],
    "paid_media_vars": ["GA_SUPPLY_SESSIONS", ...],
    "data_gcs_path": "gs://mmm-app-output/mapped-datasets/de/20251211_115528/raw.parquet"  // ✅ Fixed!
  }
}
```

The `data_gcs_path` is constructed from the `data_version` field in your `selected_columns.json` file.

## Future Prevention

This fix is now permanent in the script. All future benchmarks submitted with the updated script will automatically include the required `data_gcs_path` field.

## Need Help?

If you're still having issues:

1. Check that your queue is set to "running" (not paused)
2. Verify the queue tick scheduler is working (check Cloud Scheduler)
3. Look for error messages in Cloud Logging
4. Check that the data file actually exists at the `data_gcs_path`

## Summary

- **Cause**: Missing `data_gcs_path` field in queue params
- **Fix**: Script updated to construct path from `data_version`
- **Action**: Resubmit your benchmark OR manually fix the queue JSON
- **Verification**: Check queue status in Streamlit app
