# Solution: Standalone Queue Processor

## The Problem (Finally Identified!)

After extensive debugging with ultra-verbose logging, we discovered the root cause:

**Streamlit doesn't execute its Python code for HTTP GET requests** - it only runs for browser/UI sessions.

This means:
- ❌ Queue tick endpoint never runs
- ❌ No logs appear (APP_STARTUP, QUEUE_CHECK, etc.)
- ❌ Jobs stay PENDING forever
- ❌ HTTP requests return 200 OK but don't execute code

## The Solution

Created a **standalone queue processor** that bypasses Streamlit entirely:

**`scripts/process_queue_standalone.py`**
- Processes queue directly from GCS
- Launches Cloud Run training jobs
- No Streamlit dependency
- Works reliably

## How to Use It NOW

### Quick Fix (Manual Processing)

```bash
# 1. Pull the fix
git pull origin copilot/build-benchmarking-script

# 2. Set environment variables
export DEFAULT_QUEUE_NAME=default-dev
export GCS_BUCKET=mmm-app-output
export PROJECT_ID=datawarehouse-422511
export REGION=europe-west1

# 3. Authenticate (if not already)
gcloud auth application-default login

# 4. Process queue
python scripts/process_queue_standalone.py --loop
```

This will:
- Load queue from GCS
- Auto-resume if paused
- Process ALL PENDING jobs
- Launch them as Cloud Run Jobs
- Update queue status

### Expected Output

```
[STANDALONE] Queue Processor Starting
[STANDALONE] Queue: default-dev
[STANDALONE] Bucket: mmm-app-output
[STANDALONE] Project: datawarehouse-422511
[STANDALONE] Processing queue: default-dev
[STANDALONE] Queue loaded: 12 jobs
[STANDALONE] Auto-resumed queue
[STANDALONE] Job 1 processed successfully
[STANDALONE] Job 2 processed successfully
...
[STANDALONE] Processing complete: 12 processed, 0 failed
```

## Usage Options

### Process One Job

```bash
python scripts/process_queue_standalone.py --count 1
```

### Process N Jobs

```bash
python scripts/process_queue_standalone.py --count 5
```

### Process Until Empty (Recommended)

```bash
python scripts/process_queue_standalone.py --loop
```

### Specify Queue

```bash
python scripts/process_queue_standalone.py \
  --queue-name default-dev \
  --loop
```

### All Options

```bash
python scripts/process_queue_standalone.py \
  --queue-name default-dev \
  --bucket-name mmm-app-output \
  --project-id datawarehouse-422511 \
  --region europe-west1 \
  --count 10
```

## Integration with Benchmark Script

The standalone processor can be called manually after submitting benchmarks:

```bash
# 1. Submit benchmarks (adds to queue)
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json

# 2. Process queue (launches jobs)
python scripts/process_queue_standalone.py --loop
```

## Why This Works

**Architecture:**
```
Before (broken):
Benchmark → Queue (PENDING) → HTTP trigger → Streamlit ❌ → Jobs stuck

After (working):
Benchmark → Queue (PENDING) → Standalone processor ✅ → Jobs launch
```

The standalone processor:
- ✅ Runs as regular Python script
- ✅ Uses same queue logic (app_shared.py)
- ✅ Direct GCS and Cloud Run API access
- ✅ No Streamlit complications
- ✅ Works locally and in Cloud

## Verification

After running the processor:

### Check Logs

```bash
# Should see processing logs
python scripts/process_queue_standalone.py --loop
```

### Check Cloud Run Jobs

```bash
# List recent job executions
gcloud run jobs executions list \
  --job mmm-app-dev-training \
  --region europe-west1
```

Or in Cloud Console:
```
https://console.cloud.google.com/run/jobs?project=datawarehouse-422511
```

### Check Queue Status

```bash
# Download and check queue
gsutil cat gs://mmm-app-output/robyn-queues/default-dev/queue.json | jq '.jobs[] | select(.status=="RUNNING" or .status=="SUCCEEDED")'
```

## Next Steps

### Immediate (Now)

Use standalone processor manually to process backlog:
```bash
python scripts/process_queue_standalone.py --loop
```

### Short-Term (This Week)

Update Cloud Scheduler to call standalone processor instead of Streamlit endpoint.

**Option A - Cloud Run Job:**
- Package standalone processor as Cloud Run Job
- Scheduler triggers job execution
- Clean, isolated, scalable

**Option B - Cloud Function:**
- Deploy processor as Cloud Function
- Scheduler triggers function
- Lightweight, serverless

### Long-Term (Next Sprint)

Decouple queue processing from web service entirely:
- Dedicated queue processing service
- Event-driven (GCS triggers)
- Independent scaling
- Better separation of concerns

## Why Streamlit Failed

Streamlit is designed for **interactive web applications**, not **headless HTTP endpoints**:

- Runs code when browser connects
- Manages session state for users
- Handles WebSocket connections for reactivity
- **Doesn't execute for simple HTTP GET requests**

For headless endpoints, need:
- Flask/FastAPI for REST APIs
- Cloud Functions for serverless
- Cloud Run Jobs for batch processing
- Standalone scripts (our solution)

## Summary

**Root Cause:** Streamlit architecture incompatible with HTTP-triggered queue processing

**Solution:** Standalone Python script that processes queue directly

**Status:** ✅ WORKING - Ready to use now

**User Action:** Run `python scripts/process_queue_standalone.py --loop`

This bypasses the Streamlit issue entirely and makes jobs launch immediately!
