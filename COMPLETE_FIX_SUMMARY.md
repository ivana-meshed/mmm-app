# Complete Fix for Benchmark Queue Execution

## Your Issue

You ran:
```bash
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue
```

And saw:
```
📊 Queue Status: default
  Total jobs: 12
  Pending: 12
  Running: 0
  Completed: 0
  Queue running: False  ← THE PROBLEM!
```

Jobs weren't executing because the **queue was paused**.

## The Fix

I've added automatic queue resume capability. Now when you run the same command, it will:

1. ✅ Submit your benchmark jobs
2. ✅ Check queue status
3. ✅ **Detect queue is paused**
4. ✅ **Automatically resume the queue**
5. ✅ Trigger job processing
6. ✅ Launch all your jobs

## How to Use the Fix

### Option 1: Rerun Your Command (Recommended)

Pull the latest changes and run your benchmark again:

```bash
# Pull the fix
git pull origin copilot/build-benchmarking-script

# Run your benchmark (now with auto-resume)
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue
```

Expected output:
```
✅ Benchmark submitted successfully!
Variants queued: 3

🔄 Triggering queue processing...

📊 Queue Status: default
  Pending: 12
  Queue running: False

⚠️  Queue is paused (queue_running=false)

🔄 Resuming queue...
✅ Queue resumed successfully

📊 Queue Status: default
  Queue running: True    ← Now running!

🔄 Triggering queue tick 1/3...
✅ Launched
🔄 Triggering queue tick 2/3...
✅ Launched
🔄 Triggering queue tick 3/3...
✅ Launched

✅ Queue processing triggered for 3 job(s)
```

### Option 2: Just Resume Your Existing Queue

If you don't want to resubmit, just resume and process the existing 12 pending jobs:

```bash
python scripts/trigger_queue.py --resume-queue --until-empty
```

This will:
1. Resume the paused queue
2. Process all 12 pending jobs
3. Show progress for each

## What Was Fixed

### Issue #1: Missing data_gcs_path ✅
- **Fixed in**: commit f569e61
- **Problem**: Jobs couldn't find training data
- **Solution**: Script now constructs GCS path from data_version

### Issue #2: Scheduler Disabled ✅
- **Fixed in**: commit 381080d
- **Problem**: Queue tick never called automatically
- **Solution**: Added manual trigger capability

### Issue #3: Queue Paused ✅
- **Fixed in**: commit 5c20285 (just now!)
- **Problem**: Queue had queue_running=false
- **Solution**: Added auto-resume when triggering

## New Capabilities

### 1. Auto-Resume Flag
```bash
# Manually resume and process
python scripts/trigger_queue.py --resume-queue --until-empty
```

### 2. Integrated Auto-Resume
The `--trigger-queue` flag in benchmark script now automatically resumes paused queues.

### 3. Better Error Messages
Clear instructions when queue is paused:
```
⚠️  Queue is paused (queue_running=false)
Run with --resume-queue flag to automatically resume
```

## Technical Details

### How Queue Got Paused

The queue.json file had:
```json
{
  "queue_running": false,  ← This prevented processing
  "entries": [...]
}
```

This can happen from:
- Manual pause in Streamlit UI
- Queue initialization with paused state
- Error handling that pauses queue

### The Fix

Added `resume_queue()` function that:
1. Loads queue.json from GCS
2. Sets `queue_running = true`
3. Saves back to GCS
4. Returns success

Integrated into both:
- `trigger_queue.py` with `--resume-queue` flag
- `benchmark_mmm.py` to auto-pass flag when using `--trigger-queue`

## Verification

After running with the fix, check Google Cloud Console:

1. **Cloud Run Jobs**: Should see jobs with status "Running" or "Succeeded"
2. **Cloud Logging**: Look for "[QUEUE] Attempting to launch job" messages
3. **GCS**: Results should appear at `gs://mmm-app-output/robyn/{revision}/{country}/{timestamp}/`

Or check in Streamlit:
- Navigate to: **Run Experiment → Queue Monitor**
- Should see jobs progressing through states: PENDING → LAUNCHING → RUNNING → SUCCEEDED

## Why Three Fixes Were Needed

1. **data_gcs_path**: Without this, jobs had no data source
2. **Manual trigger**: Without this, jobs never got picked up (scheduler disabled)
3. **Auto-resume**: Without this, paused queue blocked everything

All three are now fixed and working together!

## Future Prevention

To avoid this issue in the future:

### For Development
Always use `--trigger-queue`:
```bash
python scripts/benchmark_mmm.py --config your_config.json --trigger-queue
```

This handles:
- Scheduler disabled ✅
- Queue paused ✅
- Manual triggering ✅

### For Production
Enable Cloud Scheduler:
```terraform
# infra/terraform/envs/prod.tfvars
scheduler_enabled = true
```

Then jobs process automatically every 10 minutes.

## Documentation

For more details, see:
- **QUEUE_PAUSED_FIX.md** - Detailed explanation of this fix
- **QUEUE_PROCESSING_GUIDE.md** - Complete queue processing guide
- **BENCHMARK_SOLUTION_SUMMARY.md** - Overview of all fixes

## Summary

**Your immediate action:**
```bash
git pull origin copilot/build-benchmarking-script

python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue
```

This will now:
1. Submit your 3 benchmark variants
2. Auto-resume the paused queue
3. Launch all jobs immediately
4. Process successfully

You should see jobs running in the Google Cloud Console within 1-2 minutes!

## Need Help?

If jobs still don't run:

1. **Permission Error?** 
   If you see `403 Permission 'run.services.get' denied`:
   
   **Important**: The service name has a `-web` suffix!
   ```bash
   # Get service URL (note the -web suffix!)
   gcloud run services describe mmm-app-dev-web --region=europe-west1 --format='value(status.url)'
   
   # Or list services if unsure
   gcloud run services list --region=europe-west1
   
   # Set it
   export WEB_SERVICE_URL=<url-from-above>
   
   # Try again
   python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --trigger-queue
   ```
   See [CLOUD_RUN_PERMISSION_FIX.md](CLOUD_RUN_PERMISSION_FIX.md) for details.

2. Check Cloud Run logs for errors
3. Verify Cloud Run service is deployed
4. Ensure service account has proper permissions
5. Check that training data exists at the GCS path

But with all fixes applied, it should work! 🎉
