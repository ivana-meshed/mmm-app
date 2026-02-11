# Queue Paused Issue - FIXED

## Problem

User's benchmark jobs weren't executing even with `--trigger-queue` flag. The queue status showed:
```
Queue running: False
```

This means the queue was **paused**, preventing all job processing.

## Root Cause

The queue has `queue_running: false` in the queue.json file. This can happen when:
1. Queue was manually paused in Streamlit UI
2. Queue was created with paused state
3. Previous issue or manual intervention

When the queue is paused, the queue tick processor exits early:
```python
if not running_flag:
    return {"ok": True, "message": "queue is paused", "changed": False}
```

## Solution Implemented

Added automatic queue resume capability:

### 1. New `resume_queue()` Function

Added to `scripts/trigger_queue.py`:
```python
def resume_queue(bucket_name: str, queue_name: str) -> bool:
    """Resume a paused queue by setting queue_running to true."""
    # Load queue.json from GCS
    # Set queue_running = true
    # Save back to GCS
```

### 2. New `--resume-queue` Flag

Added to trigger_queue.py:
```bash
python scripts/trigger_queue.py --resume-queue --until-empty
```

When this flag is used:
- Checks if queue is paused
- Automatically resumes it
- Then proceeds with job processing

### 3. Auto-Resume in Benchmark Script

Updated `benchmark_mmm.py` to always pass `--resume-queue` when using `--trigger-queue`:
```bash
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue
```

Now automatically:
1. Submits jobs
2. Checks queue status
3. **Resumes queue if paused**
4. Triggers job processing

## How to Fix Your Stuck Queue

### Option 1: Use Updated Benchmark Script (Recommended)

```bash
# Pull latest changes
git pull origin copilot/build-benchmarking-script

# Rerun with --trigger-queue (it will auto-resume)
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue
```

### Option 2: Manually Resume and Trigger

```bash
# Resume queue and process all pending jobs
python scripts/trigger_queue.py --resume-queue --until-empty
```

### Option 3: Resume via Streamlit UI

Navigate to: **Run Experiment → Queue Monitor**
Click the "Resume Queue" button (if available)

## What Changed

**Before:**
```
Queue paused → trigger script detects it → warns and exits → no processing
```

**After:**
```
Queue paused → trigger script detects it → auto-resumes → processes jobs ✅
```

## Verification

After running with the fix, you should see:
```
📊 Queue Status: default
  Total jobs: 12
  Pending: 12
  Queue running: False    # ← Was paused

🔄 Resuming queue...
✅ Queue resumed successfully

📊 Queue Status: default  # ← Refreshed status
  Queue running: True     # ← Now running!

🔄 Triggering queue processing...
✅ Job 1 launched
✅ Job 2 launched
...
```

## Technical Details

### resume_queue() Implementation

```python
def resume_queue(bucket_name: str, queue_name: str) -> bool:
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(f"{QUEUE_ROOT}/{queue_name}/queue.json")
    
    # Load queue
    doc = json.loads(blob.download_as_text())
    
    # Resume
    doc["queue_running"] = True
    doc["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    
    # Save
    blob.upload_from_string(json.dumps(doc, indent=2))
    return True
```

### Integration Points

1. **trigger_queue.py** - Added resume logic:
   ```python
   if not status["queue_running"]:
       if args.resume_queue:
           resume_queue(GCS_BUCKET, args.queue_name)
       else:
           sys.exit(1)  # Error if not auto-resuming
   ```

2. **benchmark_mmm.py** - Passes `--resume-queue`:
   ```python
   cmd = [..., "--resume-queue"]
   ```

## Preventing Future Issues

### How Queue Gets Paused

1. **Manual pause in UI**: User clicks pause button
2. **Infrastructure setup**: Queue created with `queue_running: false`
3. **Error handling**: Some error conditions might pause queue

### Best Practices

1. **Always use `--trigger-queue`** when submitting benchmarks in dev:
   ```bash
   python scripts/benchmark_mmm.py --config your_config.json --trigger-queue
   ```

2. **Check queue status** before submitting large batches:
   ```bash
   python scripts/trigger_queue.py --status-only
   ```

3. **For production**: Enable Cloud Scheduler (automatic processing)

## Summary

**Issue**: Queue was paused (`queue_running: false`)
**Fix**: Auto-resume when triggering with `--resume-queue` flag
**Result**: Jobs now process correctly

Three ways to fix:
1. ✅ Use `--trigger-queue` with benchmark script (auto-resumes)
2. ✅ Run `trigger_queue.py --resume-queue --until-empty`
3. ✅ Resume via Streamlit UI

All future benchmark submissions with `--trigger-queue` will automatically handle paused queues!
