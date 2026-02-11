# User Action: Diagnose Queue Processing with New Logging

## What I Did

I've added **comprehensive logging** throughout the entire queue processing flow. This will show us exactly where the process is breaking and why jobs aren't executing.

## What You Need to Do Now

### Step 1: Pull the Latest Code

```bash
git pull origin copilot/build-benchmarking-script
```

This gets the version with extensive logging.

### Step 2: Resubmit Your Benchmark

```bash
export WEB_SERVICE_URL=https://mmm-app-dev-web-wuepn6nq5a-ew.a.run.app
export DEFAULT_QUEUE_NAME=default-dev

python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue
```

### Step 3: Wait 30-60 Seconds

Give the logs time to appear in Cloud Logging.

### Step 4: View the Logs

Run this command to see all queue processing logs:

```bash
gcloud logging read \
  "resource.type=cloud_run_revision 
   resource.labels.service_name=mmm-app-dev-web" \
  --limit=100 \
  --format=json | jq -r '.[] | select(.textPayload) | .textPayload' | grep -E "(QUEUE|LAUNCHER)"
```

**If jq isn't installed**, use this simpler version:

```bash
gcloud logging read \
  "resource.type=cloud_run_revision 
   resource.labels.service_name=mmm-app-dev-web
   textPayload=~\"QUEUE\"" \
  --limit=100 \
  --format="value(textPayload)"
```

### Step 5: Share the Output

Send me the log output. It will show us exactly where the process is breaking.

## What to Look For

The logs will show a clear sequence. Here's what we're checking:

### ✅ If You See This - Endpoint is Working:
```
[QUEUE_TICK_ENTRY] ========== QUEUE TICK ENDPOINT CALLED ==========
[QUEUE_TICK_START] _safe_tick_once called for queue: default-dev
```

### ✅ If You See This - Job Found:
```
[QUEUE_TICK_FOUND] Found PENDING job at index 0
[QUEUE_TICK_FOUND] Job ID: 123
```

### ✅ If You See This - Launcher Called:
```
[LAUNCHER_ENTRY] ========== prepare_and_launch_job() CALLED ==========
[LAUNCHER_ENTRY] Country: de
[LAUNCHER_BUCKET] Final bucket: mmm-app-output
```

### ❌ If You See This - Problem Found:
```
[LAUNCHER_ERROR] ...
[QUEUE_ERROR] ...
[QUEUE_TICK_ERROR] ...
```

Any of these ERROR prefixes will show us the exact problem.

## Quick Diagnosis

**No logs at all?**
→ Endpoint not being called. Check URL and authentication.

**Has `[QUEUE_TICK_ENTRY]` but stops there?**
→ Exception in queue tick handler. Look for ERROR logs.

**Has `[QUEUE_TICK_FOUND]` but no `[LAUNCHER_ENTRY]`?**
→ Launcher not being called. Check for errors.

**Has `[LAUNCHER_ENTRY]` but no success?**
→ Launcher failing. The ERROR logs will show why.

## Alternative: View in Cloud Console

If command line is difficult:

1. Go to: https://console.cloud.google.com/logs/query?project=datawarehouse-422511
2. Use this query:
   ```
   resource.type="cloud_run_revision"
   resource.labels.service_name="mmm-app-dev-web"
   (textPayload=~"QUEUE" OR textPayload=~"LAUNCHER")
   ```
3. Click "Run Query"
4. Screenshot or copy the log entries

## What Happens Next

Once you share the logs:

1. **I'll identify** the exact point where it breaks
2. **I'll see** any error messages or exceptions
3. **I'll understand** why the launcher isn't working
4. **I'll fix** the specific issue revealed
5. **You'll retest** with continued logging
6. **We'll verify** jobs actually start running

## Why This Will Work

The new logging covers **every single step**:
- Is the endpoint being called? ✓
- Is it finding the queue? ✓
- Are there pending jobs? ✓
- Is the launcher being called? ✓
- What parameters is it receiving? ✓
- Where does it fail? ✓
- What's the error? ✓

**Failures cannot hide anymore.** The logs will reveal the exact issue.

## Important Note

As you requested: **I'm not saying it's fixed until you confirm it actually works.**

This logging update is specifically to **diagnose** the issue, not fix it yet. Once we see the logs, we'll know exactly what needs to be fixed.

## Summary

1. ✅ Pull latest code (has comprehensive logging)
2. ✅ Resubmit benchmark
3. ✅ Wait 30-60 seconds
4. ✅ Run logging command
5. ✅ Share output with me

Then we'll fix the actual issue!
