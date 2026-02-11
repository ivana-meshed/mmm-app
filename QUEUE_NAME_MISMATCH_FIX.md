# URGENT: Queue Name Mismatch - Why Jobs Aren't Running

## The Problem

Your benchmarks aren't running because of a **queue name mismatch**:

- 📤 **Jobs submitted to**: `default` queue
- 🔍 **Service monitoring**: `default-dev` queue  
- ❌ **Result**: Jobs never processed!

## Why This Happened

The dev environment uses a different queue name than production:

**Production:**
- Queue: `default`

**Development:**
- Queue: `default-dev` ← Different!

Your benchmark script used the hardcoded default (`"default"`) instead of the dev queue (`"default-dev"`).

## Immediate Fix (Two Options)

### Option 1: Resubmit to Correct Queue (Recommended)

```bash
# Pull latest fix
git pull origin copilot/build-benchmarking-script

# Set queue name environment variable
export DEFAULT_QUEUE_NAME=default-dev

# Resubmit benchmark (will now use default-dev)
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue
```

The script now auto-detects `DEFAULT_QUEUE_NAME` from the environment!

### Option 2: Specify Queue Name Explicitly

```bash
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --queue-name default-dev \
  --trigger-queue
```

This works immediately without pulling the fix.

## What Changed in the Fix

**Before (broken):**
```python
parser.add_argument(
    "--queue-name",
    default="default",  # ← Always "default"
)
```

**After (fixed):**
```python
parser.add_argument(
    "--queue-name",
    default=os.getenv("DEFAULT_QUEUE_NAME", "default"),  # ← Auto-detect
)
```

Now the script reads from the same `DEFAULT_QUEUE_NAME` environment variable that the web service uses!

## Verify the Fix

After resubmitting, jobs should appear in Google Cloud Console:

```
https://console.cloud.google.com/run/jobs?project=datawarehouse-422511
```

Look for "Active" jobs in the `mmm-app-training` job.

## What Happened to Your Previous Jobs

Your 30 pending jobs are stuck in the `default` queue, which isn't being monitored in the dev environment.

**You can either:**

1. **Ignore them** - They won't interfere with new jobs
2. **Clear them** (optional):
   ```bash
   # Download the queue
   gsutil cp gs://mmm-app-output/robyn-queues/default/queue.json /tmp/queue.json
   
   # Edit and remove old jobs (or just delete the file)
   
   # Re-upload
   gsutil cp /tmp/queue.json gs://mmm-app-output/robyn-queues/default/queue.json
   ```

## Environment-Specific Queue Names

**Dev:** `default-dev`
**Prod:** `default`

The web service knows which queue to monitor via the `DEFAULT_QUEUE_NAME` environment variable (set by terraform).

Now the benchmark script also reads this variable!

## Complete Working Command

```bash
# 1. Pull fix
git pull origin copilot/build-benchmarking-script

# 2. Set environment (add to ~/.zshrc for permanence)
export DEFAULT_QUEUE_NAME=default-dev
export WEB_SERVICE_URL=https://mmm-app-dev-web-wuepn6nq5a-ew.a.run.app

# 3. Run benchmark (auto-uses correct queue)
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue

# Expected output:
# Queue: default-dev  ← Correct!
# ✅ Triggered 3 queue tick(s) successfully
```

## Verification

After 2-5 minutes, check status:

```bash
python scripts/trigger_queue.py --status-only
```

**Expected:**
```
📊 Queue Status: default-dev  ← Correct queue!
  Pending: 0          ← Jobs moved to running
  Running: 3          ← Your jobs!
```

And Cloud Console should show "Active" jobs.

## Why This Matters

Queue names must match between:
1. Where jobs are **submitted** (benchmark script)
2. Where jobs are **processed** (web service queue tick)

The mismatch meant:
- ❌ Jobs submitted to `default`
- ❌ Queue ticks processed `default-dev`
- ❌ Jobs never found

Now both use the same source of truth: `DEFAULT_QUEUE_NAME` environment variable!

## Summary

**Root cause**: Hardcoded queue name in script didn't match dev environment  
**Fix**: Auto-detect queue name from environment variable  
**Action**: Pull fix, set `DEFAULT_QUEUE_NAME=default-dev`, resubmit

Your next benchmark will work correctly! 🎉

---

**See also:**
- WHAT_TO_DO_NOW.md - General next steps
- SUCCESS_VERIFICATION.md - How to verify jobs
