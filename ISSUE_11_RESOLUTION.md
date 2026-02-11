# Issue #11 Resolution - Jobs Not Launching

## Your Problem

Jobs remained stuck in PENDING even though everything looked correct:

```
📊 Queue Status: default-dev
  Pending: 3
  Running: 0
  Queue running: True  ← Queue was running
  
✅ Triggered 3 queue tick(s) successfully  ← Ticks succeeded
```

You suspected it might be related to Google credentials.

## Root Cause Found

**It wasn't a credentials issue. It was a session state dependency bug.**

The launcher function (`prepare_and_launch_job`) tried to access `st.session_state["gcs_bucket"]`, but when the queue tick endpoint is called via HTTP (headless mode), there is NO session state. This caused a silent failure.

### The Bug

```python
# Old code (broken)
def prepare_and_launch_job(params: dict) -> dict:
    gcs_bucket = params.get("gcs_bucket") or st.session_state["gcs_bucket"]  # ← CRASHES in headless mode!
```

### Why It Failed Silently

The exception was caught but not logged clearly, so jobs just stayed PENDING forever.

## The Fix

Made the launcher work without session state by using a safe fallback chain:

```python
# New code (fixed)
def prepare_and_launch_job(params: dict) -> dict:
    # Try params first (works in headless mode)
    gcs_bucket = params.get("gcs_bucket")
    
    # Try session state if not in params (works in UI mode)
    if not gcs_bucket:
        try:
            gcs_bucket = st.session_state.get("gcs_bucket")
        except (AttributeError, RuntimeError):
            pass  # No session state - that's fine in headless mode
    
    # Fallback to environment variable
    if not gcs_bucket:
        gcs_bucket = GCS_BUCKET
    
    # Validate and log
    if not gcs_bucket:
        raise ValueError("GCS bucket not available")
    
    logger.info(f"[LAUNCHER] Using GCS bucket: {gcs_bucket}")
```

### Why This Fix Works

Your benchmark script already sets `gcs_bucket` in the params dict (from commit 0df86e3), so the launcher can now find it without needing session state.

## What to Do Now

### Step 1: Pull the Fix

```bash
git pull origin copilot/build-benchmarking-script
```

### Step 2: Resubmit Your Benchmark

```bash
export WEB_SERVICE_URL=https://mmm-app-dev-web-wuepn6nq5a-ew.a.run.app
export DEFAULT_QUEUE_NAME=default-dev

python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue
```

### Step 3: Wait 2-5 Minutes

Jobs need time to transition: PENDING → LAUNCHING → RUNNING

### Step 4: Verify Jobs Are Running

**Option 1 - Command line:**
```bash
python scripts/trigger_queue.py --status-only
```

**Expected output:**
```
📊 Queue Status: default-dev
  Pending: 2     ← Should decrease
  Running: 1     ← Should increase!
  Completed: 0
```

**Option 2 - Google Cloud Console:**

Visit:
```
https://console.cloud.google.com/run/jobs?project=datawarehouse-422511
```

Look for:
- Job name: `mmm-app-dev-training`
- Status: **ACTIVE** (green)
- Recent executions listed

### Step 5: Check Logs (If Needed)

If jobs still aren't running, check the logs:

```bash
gcloud logging read \
  "resource.type=cloud_run_revision resource.labels.service_name=mmm-app-dev-web" \
  --limit=50 \
  --format=json | jq -r '.[] | select(.textPayload | contains("LAUNCHER")) | .textPayload'
```

Look for:
- `[LAUNCHER] Using GCS bucket: mmm-app-output` ← Good!
- `[LAUNCHER] Data GCS path: gs://...` ← Good!
- `[LAUNCHER] Job params: country=de, revision=...` ← Good!
- Any ERROR messages ← Report these if you see them

## What Should Happen

After pulling the fix and resubmitting:

1. **Immediately**: Jobs added to queue with status PENDING
2. **After 30-60 seconds**: First job transitions to LAUNCHING
3. **After 1-2 minutes**: Job transitions to RUNNING
4. **After 2-5 minutes**: All 3 jobs should be RUNNING or completed
5. **After 15-30 minutes**: Jobs complete with status SUCCEEDED

## Complete Environment Setup

For reference, here's your complete setup:

```bash
# Dependencies
pip install -r requirements.txt

# Environment variables
export WEB_SERVICE_URL=https://mmm-app-dev-web-wuepn6nq5a-ew.a.run.app
export DEFAULT_QUEUE_NAME=default-dev

# Authentication
gcloud auth application-default login
```

## Make It Permanent

Add to your `~/.zshrc` or `~/.bashrc`:

```bash
export WEB_SERVICE_URL=https://mmm-app-dev-web-wuepn6nq5a-ew.a.run.app
export DEFAULT_QUEUE_NAME=default-dev
```

## All 11 Issues Now Fixed

This was issue #11 in the sequence. All previous issues are also resolved:

1. ✅ Missing data_gcs_path (f569e61)
2. ✅ Scheduler disabled (381080d)
3. ✅ Queue paused (5c20285)
4. ✅ Permission errors (3d019c4)
5. ✅ Wrong service names (7205146)
6. ✅ Missing requests (b43ff08)
7. ✅ Datetime deprecation (b43ff08)
8. ✅ Missing google-auth (398a9a7)
9. ✅ OAuth scope error (a467abb)
10. ✅ Queue name mismatch (0df86e3)
11. ✅ **Session state dependency (aa3ca23)** ← Just fixed!

## What If It Still Doesn't Work?

If after pulling the fix and resubmitting, jobs are still PENDING after 5 minutes:

1. **Check the launcher logs** (command above)
2. **Look for error messages** containing "[LAUNCHER]" or "[QUEUE_ERROR]"
3. **Report the specific error** you see in the logs

The logs will now show exactly what's happening when jobs try to launch, making it much easier to diagnose any remaining issues.

## Technical Summary

**The problem:** Headless HTTP requests don't have Streamlit session state
**The solution:** Use params dict instead of session state
**Why it works:** Benchmark script sets all required data in params
**Impact:** Jobs can now launch in headless mode (queue tick via HTTP)

## Confidence Level

This fix addresses the exact root cause of jobs not launching:
- ✅ Session state dependency removed
- ✅ Launcher now stateless and headless-compatible
- ✅ Safe fallback chain (params → session → env)
- ✅ Proper logging added for debugging
- ✅ Benchmark script provides all needed data in params

**Jobs should launch successfully now!**

Please pull the fix, resubmit, wait 2-5 minutes, and confirm whether jobs are running.
