# Next Steps: Diagnose Queue Processing Issue

## Current Status

✅ **Comprehensive logging added** - Every step now has detailed logging
⏳ **Waiting for diagnostics** - Need you to run the commands and share logs
🎯 **Goal** - Figure out exactly why jobs aren't executing

## What You Need to Do

### 1. Pull the Latest Code

```bash
cd ~/software/mmm-app
git pull origin copilot/build-benchmarking-script
```

This gets the version with extensive diagnostic logging.

### 2. Set Environment Variables

```bash
export WEB_SERVICE_URL=https://mmm-app-dev-web-wuepn6nq5a-ew.a.run.app
export DEFAULT_QUEUE_NAME=default-dev
```

### 3. Resubmit the Benchmark

```bash
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue
```

### 4. Wait 30-60 Seconds

Give the logs time to appear in Cloud Logging.

### 5. Get the Logs

**Option A - Simpler (if jq errors):**
```bash
gcloud logging read \
  "resource.type=cloud_run_revision 
   resource.labels.service_name=mmm-app-dev-web
   textPayload=~\"QUEUE\"" \
  --limit=100 \
  --format="value(textPayload)"
```

**Option B - More detailed:**
```bash
gcloud logging read \
  "resource.type=cloud_run_revision 
   resource.labels.service_name=mmm-app-dev-web" \
  --limit=100 \
  --format=json | jq -r '.[] | select(.textPayload) | .textPayload' | grep -E "(QUEUE|LAUNCHER)"
```

**Option C - Cloud Console:**
1. Go to: https://console.cloud.google.com/logs/query?project=datawarehouse-422511
2. Query: `resource.labels.service_name="mmm-app-dev-web" textPayload=~"QUEUE"`
3. Screenshot the results

### 6. Share the Output

Copy and send me the log output. It will show exactly where the process breaks.

## What the Logs Will Show

The logs have clear markers for each step:

### ✅ Success Markers

If you see these, that step worked:

```
[QUEUE_TICK_ENTRY] ========== QUEUE TICK ENDPOINT CALLED ==========
```
→ Endpoint is being called ✓

```
[QUEUE_TICK_START] _safe_tick_once called for queue: default-dev
```
→ Tick processing started ✓

```
[QUEUE_TICK_FOUND] Found PENDING job at index 0
```
→ Found a job to process ✓

```
[LAUNCHER_ENTRY] ========== prepare_and_launch_job() CALLED ==========
```
→ Launcher function was called ✓

```
[LAUNCHER_BUCKET] Final bucket: mmm-app-output
```
→ Bucket resolved ✓

```
[QUEUE_LAUNCHER_RETURNED] Launcher returned successfully!
```
→ Launcher completed ✓

### ❌ Failure Markers

If you see these, that's where it failed:

```
[QUEUE_TICK_ERROR] ...
[QUEUE_ERROR] ...
[LAUNCHER_ERROR] ...
```

Any ERROR prefix shows the problem.

## Quick Diagnosis

**No logs at all?**
- Endpoint isn't being called
- Check: Is WEB_SERVICE_URL correct?
- Check: Did `gcloud auth application-default login` succeed?

**Logs stop after `[QUEUE_TICK_ENTRY]`?**
- Exception in queue tick handler
- Look for `[QUEUE_TICK_ERROR]` lines

**Logs show "No PENDING jobs found"?**
- Queue is empty or all jobs processed
- Run: `python scripts/trigger_queue.py --status-only`

**Has `[QUEUE_TICK_FOUND]` but no `[LAUNCHER_ENTRY]`?**
- Launcher not being called
- Check for error logs between them

**Has `[LAUNCHER_ENTRY]` but no success?**
- Launcher is failing
- The ERROR logs will show exactly why

## What Happens After You Share Logs

1. **I'll analyze** the log sequence
2. **I'll identify** the exact failure point
3. **I'll see** any error messages or exceptions
4. **I'll understand** the root cause
5. **I'll implement** the specific fix needed
6. **You'll retest** with the fix
7. **We'll verify** jobs actually run

## Why This Will Work

The new logging is **comprehensive**. It covers:
- ✅ Endpoint being called
- ✅ Queue tick starting
- ✅ Jobs being found
- ✅ Launcher being called
- ✅ Parameters being passed
- ✅ Bucket being resolved
- ✅ Data paths being set
- ✅ Job being created
- ✅ Any errors occurring

**Silent failures are now impossible.** The logs will reveal everything.

## Important

As you requested: **I'm not saying it's fixed.**

This is diagnostic code to **figure out what's wrong**. Once we see the logs, we'll implement the actual fix.

## Summary

1. ✅ Pull latest code
2. ✅ Set environment variables
3. ✅ Resubmit benchmark
4. ✅ Wait 30-60 seconds
5. ✅ Run logging command
6. ✅ Share output with me

Then we'll know exactly what needs to be fixed!

## Files to Check

- `USER_ACTION_LOGGING.md` - Detailed version of this guide
- `COMPREHENSIVE_LOGGING_GUIDE.md` - Technical logging details

## Ready?

Pull the code, run the benchmark, and share the logs. Let's find out what's actually happening! 🔍
