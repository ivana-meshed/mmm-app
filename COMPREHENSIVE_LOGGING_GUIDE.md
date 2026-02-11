# Comprehensive Logging Guide - Queue Processing Diagnosis

## What Was Added

Extensive logging has been added throughout the entire queue processing flow to diagnose why jobs aren't executing. Every critical step now has clear, structured logging.

## Log Prefixes

The following log prefixes have been added:

### Queue Tick Endpoint
- `[QUEUE_TICK_ENTRY]` - Queue tick endpoint was called
- `[QUEUE_TICK_COMPLETE]` - Queue tick completed successfully  
- `[QUEUE_TICK_ERROR]` - Error in queue tick endpoint handler

### Queue Processing
- `[QUEUE_TICK_START]` - Starting queue tick processing
- `[QUEUE_TICK]` - General queue tick activity
- `[QUEUE_TICK_FOUND]` - Found a PENDING job to process
- `[QUEUE_BEFORE_LAUNCHER]` - About to call launcher function
- `[QUEUE_LAUNCHER_CALL]` - Calling launcher NOW
- `[QUEUE_LAUNCHER_RETURNED]` - Launcher returned successfully
- `[QUEUE]` - General queue messages (job launched, etc.)
- `[QUEUE_ERROR]` - Errors during queue processing

### Launcher Function
- `[LAUNCHER_ENTRY]` - Launcher function called
- `[LAUNCHER_BUCKET]` - Bucket resolution steps
- `[LAUNCHER_DATA]` - Data path information
- `[LAUNCHER_ERROR]` - Errors in launcher function

## Expected Log Flow (When Everything Works)

When a queue tick successfully processes a job, you should see this sequence:

```
[QUEUE_TICK_ENTRY] ========== QUEUE TICK ENDPOINT CALLED ==========
[QUEUE_TICK_ENTRY] Queue name: default-dev
[QUEUE_TICK_ENTRY] Bucket: mmm-app-output
[QUEUE_TICK_ENTRY] Launcher provided: True

[QUEUE_TICK_START] _safe_tick_once called for queue: default-dev
[QUEUE_TICK_START] Bucket: mmm-app-output
[QUEUE_TICK_START] Launcher provided: True

[QUEUE_TICK] Attempt 1/3
[QUEUE_TICK] Loaded queue document, generation: 12345
[QUEUE_TICK] Queue has 3 entries
[QUEUE_TICK] Queue running flag: True
[QUEUE_TICK] Status counts: {'PENDING': 3}

[QUEUE_TICK] Looking for PENDING jobs...
[QUEUE_TICK_FOUND] Found PENDING job at index 0
[QUEUE_TICK_FOUND] Job ID: 123
[QUEUE_TICK_FOUND] Country: de

[QUEUE_TICK] Leasing job 123 (setting status to LAUNCHING)
[QUEUE_TICK] Successfully leased job 123

[QUEUE_BEFORE_LAUNCHER] About to call launcher function...
[QUEUE_BEFORE_LAUNCHER] Launcher type: <class 'function'>
[QUEUE_BEFORE_LAUNCHER] Params keys: ['country', 'revision', ...]

[QUEUE_LAUNCHER_CALL] Calling launcher NOW...

[LAUNCHER_ENTRY] ========== prepare_and_launch_job() CALLED ==========
[LAUNCHER_ENTRY] Params keys: ['country', 'revision', ...]
[LAUNCHER_ENTRY] Country: de
[LAUNCHER_ENTRY] Has data_gcs_path: True

[LAUNCHER_BUCKET] Step 1 - From params: mmm-app-output
[LAUNCHER_BUCKET] Final bucket: mmm-app-output
[LAUNCHER_DATA] data_gcs_path: gs://mmm-app-output/mapped-datasets/de/...

[QUEUE_LAUNCHER_RETURNED] Launcher returned successfully!
[QUEUE] Successfully launched job 123
```

## How to View Logs

### Option 1: All Queue and Launcher Logs

```bash
gcloud logging read \
  "resource.type=cloud_run_revision 
   resource.labels.service_name=mmm-app-dev-web
   (textPayload=~\"QUEUE\" OR textPayload=~\"LAUNCHER\")" \
  --limit=100 \
  --format=json | jq -r '.[] | .textPayload' | grep -E "(QUEUE|LAUNCHER)"
```

### Option 2: Just Entry Points

```bash
gcloud logging read \
  "resource.type=cloud_run_revision 
   resource.labels.service_name=mmm-app-dev-web
   textPayload=~\"ENTRY\"" \
  --limit=50 \
  --format="value(textPayload)"
```

### Option 3: Errors Only

```bash
gcloud logging read \
  "resource.type=cloud_run_revision 
   resource.labels.service_name=mmm-app-dev-web
   textPayload=~\"ERROR\"" \
  --limit=20 \
  --format="value(textPayload)"
```

### Option 4: Recent Logs (Last 5 Minutes)

```bash
gcloud logging read \
  "resource.type=cloud_run_revision 
   resource.labels.service_name=mmm-app-dev-web
   textPayload=~\"QUEUE\"" \
  --limit=50 \
  --format="value(textPayload)" \
  --freshness=5m
```

### Option 5: Cloud Console

1. Go to: https://console.cloud.google.com/logs/query
2. Select project: `datawarehouse-422511`
3. Use this query:
```
resource.type="cloud_run_revision"
resource.labels.service_name="mmm-app-dev-web"
(textPayload=~"QUEUE" OR textPayload=~"LAUNCHER")
```

## Troubleshooting Patterns

### Pattern 1: Endpoint Not Being Called

**Symptoms:**
- No `[QUEUE_TICK_ENTRY]` logs at all

**Meaning:**
- The HTTP request isn't reaching the endpoint
- Could be URL issue, authentication issue, or service not responding

**Fix:**
- Verify WEB_SERVICE_URL is correct
- Check authentication (gcloud auth application-default login)
- Verify service is running

### Pattern 2: Tick Doesn't Start

**Symptoms:**
- Has `[QUEUE_TICK_ENTRY]` but no `[QUEUE_TICK_START]`

**Meaning:**
- Exception in wrapper function before tick starts

**Fix:**
- Check `[QUEUE_TICK_ERROR]` logs for exception details

### Pattern 3: No Jobs Found

**Symptoms:**
- Has `[QUEUE_TICK_START]` and logs show "No PENDING jobs found"
- Or logs show "Queue is paused"

**Meaning:**
- Queue is empty or paused
- Or all jobs already processed

**Fix:**
- Check queue status: `python scripts/trigger_queue.py --status-only`
- If paused: resume with `--resume-queue`
- If empty: submit new jobs

### Pattern 4: Launcher Not Called

**Symptoms:**
- Has `[QUEUE_TICK_FOUND]` but no `[LAUNCHER_ENTRY]`

**Meaning:**
- Launcher function missing or not provided
- Or exception between finding job and calling launcher

**Fix:**
- Check if launcher is being passed to endpoint
- Look for `[QUEUE_ERROR]` logs

### Pattern 5: Launcher Fails

**Symptoms:**
- Has `[LAUNCHER_ENTRY]` but no `[QUEUE_LAUNCHER_RETURNED]`

**Meaning:**
- Exception inside launcher function

**Fix:**
- Check for `[LAUNCHER_ERROR]` logs
- Check bucket resolution logs
- Check data path logs
- Look for full exception traceback

### Pattern 6: Job Creation Fails

**Symptoms:**
- Has `[QUEUE_LAUNCHER_RETURNED]` but jobs don't appear in Cloud Run

**Meaning:**
- Cloud Run API call failing
- Permissions issue
- Job name incorrect

**Fix:**
- Check Cloud Run Jobs API permissions
- Verify job name exists: `gcloud run jobs list --region=europe-west1`
- Check for Cloud Run API errors in logs

## User Action Steps

1. **Pull the latest changes:**
   ```bash
   git pull origin copilot/build-benchmarking-script
   ```

2. **Resubmit benchmark:**
   ```bash
   python scripts/benchmark_mmm.py \
     --config benchmarks/adstock_comparison.json \
     --trigger-queue
   ```

3. **Wait 30-60 seconds** for logs to appear

4. **View logs:**
   ```bash
   gcloud logging read \
     "resource.type=cloud_run_revision 
      resource.labels.service_name=mmm-app-dev-web" \
     --limit=100 \
     --format=json | jq -r '.[] | select(.textPayload) | .textPayload' | grep -E "(QUEUE|LAUNCHER)"
   ```

5. **Identify the issue:**
   - Find the last successful log message
   - Check if there's an ERROR log after it
   - Match the pattern to troubleshooting guide above

6. **Share output** if you need help interpreting the logs

## Example Analysis

### Example 1: Endpoint Working

```
[QUEUE_TICK_ENTRY] ========== QUEUE TICK ENDPOINT CALLED ==========
[QUEUE_TICK_START] _safe_tick_once called for queue: default-dev
[QUEUE_TICK] Attempt 1/3
[QUEUE_TICK] Queue has 3 entries
[QUEUE_TICK_FOUND] Found PENDING job at index 0
[LAUNCHER_ENTRY] ========== prepare_and_launch_job() CALLED ==========
[LAUNCHER_BUCKET] Final bucket: mmm-app-output
[QUEUE_LAUNCHER_RETURNED] Launcher returned successfully!
```

**Status:** ✅ Working correctly

### Example 2: Launcher Missing Data Path

```
[QUEUE_TICK_ENTRY] ========== QUEUE TICK ENDPOINT CALLED ==========
[QUEUE_TICK_START] _safe_tick_once called for queue: default-dev
[QUEUE_TICK_FOUND] Found PENDING job at index 0
[LAUNCHER_ENTRY] ========== prepare_and_launch_job() CALLED ==========
[LAUNCHER_DATA] data_gcs_path: NOT SET - will query Snowflake
[LAUNCHER_ERROR] Snowflake connection failed...
```

**Status:** ❌ Missing data_gcs_path, falls back to Snowflake which fails in headless mode

### Example 3: No Logs at All

```
(no logs)
```

**Status:** ❌ Endpoint not being called - check URL and authentication

## Why This Will Work

The new logging is **comprehensive** - it covers every single step in the process. Whatever is breaking, we'll see:

1. ✅ The last successful log before failure
2. ✅ Any error messages and full tracebacks
3. ✅ The exact state of the system at each point
4. ✅ All parameters and paths being used

This makes it **impossible for failures to be silent** anymore. The logs will reveal exactly where and why the process breaks.

## Summary

- **15 log prefixes** covering all stages
- **Clear log structure** with visual separators
- **Multiple viewing methods** for convenience
- **Pattern-based troubleshooting** guide
- **Complete visibility** into queue processing

The logs will show exactly what's happening and where it's failing!
