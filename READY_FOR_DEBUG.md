# Ready for Debug - What to Do Now

## Current Situation

Your logs showed only:
```
[QUEUE] Auto-refresh skipped: queue_running is False
```

This means the queue tick HTTP endpoint is **never being called**. The HTTP requests are succeeding (200 OK), but Streamlit isn't processing them as queue tick requests.

## What I Just Added

**Debug logging** at the very first steps to see exactly what's happening:

1. **Checkpoint 1:** Log `st.query_params` when handler is called
2. **Checkpoint 2:** Log if query params are empty or populated

This will show us definitively why the endpoint isn't working.

## What You Need to Do

### Step 1: Pull the Debug Code

```bash
git pull origin copilot/build-benchmarking-script
```

### Step 2: Resubmit Your Benchmark

```bash
# Make sure environment variables are set
export WEB_SERVICE_URL=https://mmm-app-dev-web-wuepn6nq5a-ew.a.run.app
export DEFAULT_QUEUE_NAME=default-dev

# Resubmit
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue
```

### Step 3: Wait 30-60 Seconds

Let the HTTP requests complete and logs propagate.

### Step 4: Check Debug Logs

```bash
gcloud logging read \
  "resource.type=cloud_run_revision 
   resource.labels.service_name=mmm-app-dev-web
   (textPayload=~\"QUEUE_CHECK\" OR textPayload=~\"QUEUE_HANDLER\")" \
  --limit=50 \
  --format="value(textPayload)"
```

### Step 5: Share the Output

Copy and paste the log output. It will show one of three patterns:

**Pattern A - Empty Query Params** (Most Likely):
```
[QUEUE_CHECK] st.query_params: {}
[QUEUE_HANDLER] No query params - returning None
```

**Pattern B - Query Params Working**:
```
[QUEUE_CHECK] st.query_params: {'queue_tick': '1', 'name': 'default-dev'}
[QUEUE_TICK_ENTRY] QUEUE TICK ENDPOINT CALLED
```

**Pattern C - No Logs**:
```
(nothing)
```

## What Happens After You Share Logs

Based on which pattern you see, I'll implement the specific fix:

**For Pattern A:**
- Implement alternative to st.query_params
- Use request headers or path-based routing
- Test and verify

**For Pattern B:**
- Debug further down the chain
- Find where launcher fails
- Fix that specific issue

**For Pattern C:**
- Check service routing
- Verify deployment
- Fix configuration

## Quick Commands Reference

```bash
# Pull code
git pull origin copilot/build-benchmarking-script

# Set env vars
export WEB_SERVICE_URL=https://mmm-app-dev-web-wuepn6nq5a-ew.a.run.app
export DEFAULT_QUEUE_NAME=default-dev

# Run benchmark
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue

# Wait, then check logs
gcloud logging read \
  "resource.type=cloud_run_revision 
   resource.labels.service_name=mmm-app-dev-web
   (textPayload=~\"QUEUE_CHECK\" OR textPayload=~\"QUEUE_HANDLER\")" \
  --limit=50 \
  --format="value(textPayload)"
```

## Important Notes

✅ **Debug logging added** - Will show exactly what's happening
✅ **Three patterns identified** - One MUST match your logs
✅ **Fix ready for each pattern** - Will implement based on your output
❌ **Not claiming it's fixed yet** - Waiting for your confirmation

## Why This Will Work

The debug logging covers the FIRST checkpoints where the endpoint processing begins. One of these MUST be failing:

1. Is the handler function called? → `[QUEUE_CHECK]` logs
2. Are query params populated? → `[QUEUE_HANDLER]` logs

We'll see which one fails and fix it specifically.

## Summary

**Status:** Ready for debugging
**Your Action:** Run commands above, share log output
**My Action:** Analyze logs, implement fix, verify

**Let's figure this out together!**

See **ISSUE_13_DEBUG_GUIDE.md** for more details.
