# Issue #13 Debug Guide - Queue Tick Endpoint Not Called

## The Problem

Your logs show only:
```
[QUEUE] Auto-refresh skipped: queue_running is False
```

But NO:
- `[QUEUE_TICK_ENTRY]` logs
- `[QUEUE_TICK_START]` logs
- `[LAUNCHER_ENTRY]` logs

This means the HTTP queue tick endpoint is **never being called**, despite the trigger script reporting:
```
✅ Triggered 3 queue tick(s) successfully
```

## What We Added

Debug logging at the very first steps to see why the endpoint isn't processing:

1. **`[QUEUE_CHECK]`** - Logs `st.query_params` when handler function is called
2. **`[QUEUE_HANDLER]`** - Logs query params as received by processing function

These will show us exactly what's happening (or not happening).

## How to View Debug Logs

```bash
# Pull the debug logging
git pull origin copilot/build-benchmarking-script

# Resubmit benchmark
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue

# Wait 30-60 seconds

# View debug logs
gcloud logging read \
  "resource.type=cloud_run_revision 
   resource.labels.service_name=mmm-app-dev-web
   (textPayload=~\"QUEUE_CHECK\" OR textPayload=~\"QUEUE_HANDLER\")" \
  --limit=50 \
  --format="value(textPayload)"
```

## Expected Log Patterns

### Pattern A: Empty Query Params (Most Likely)

```
[QUEUE_CHECK] handle_queue_tick_if_requested() called
[QUEUE_CHECK] st.query_params type: <class 'dict'>
[QUEUE_CHECK] st.query_params value: {}
[QUEUE_CHECK] st.query_params bool: False
[QUEUE_HANDLER] handle_queue_tick_from_query_params() called
[QUEUE_HANDLER] query_params type: <class 'dict'>
[QUEUE_HANDLER] query_params value: {}
[QUEUE_HANDLER] query_params bool: False
[QUEUE_HANDLER] No query params - returning None
```

**What this means:**
- The handler function IS being called
- But `st.query_params` is empty
- Streamlit query params don't work in headless HTTP mode
- Need alternative approach

**Next Step:**
- Implement alternative (use request path, headers, or environment variable)

### Pattern B: Query Params Working

```
[QUEUE_CHECK] handle_queue_tick_if_requested() called
[QUEUE_CHECK] st.query_params: {'queue_tick': '1', 'name': 'default-dev'}
[QUEUE_CHECK] queue_tick param: 1
[QUEUE_CHECK] name param: default-dev
[QUEUE_HANDLER] handle_queue_tick_from_query_params() called
[QUEUE_HANDLER] query_params: {'queue_tick': '1', 'name': 'default-dev'}
[QUEUE_TICK_ENTRY] ========== QUEUE TICK ENDPOINT CALLED ==========
```

**What this means:**
- Query params ARE being passed correctly
- Handler IS processing them
- Issue is further down the chain
- Need to debug why jobs aren't launching

**Next Step:**
- Check for errors after `[QUEUE_TICK_ENTRY]`
- Debug launcher or job creation

### Pattern C: No Logs at All

```
(no QUEUE_CHECK or QUEUE_HANDLER logs)
```

**What this means:**
- Handler function not being called at all
- Request not reaching the service
- Service routing/deployment issue
- Wrong service/revision receiving requests

**Next Step:**
- Verify service URL is correct
- Check service deployment status
- Verify authentication working
- Check if requests reaching Cloud Run at all

## Diagnosis Flow

1. **Run the debug logs command** (see above)
2. **Identify which pattern** matches your output
3. **Share the pattern** with the context
4. **We'll implement** the specific fix for that pattern

## Why This Will Work

The debug logging covers the FIRST TWO STEPS in the process:
1. ✅ Is `handle_queue_tick_if_requested()` called?
2. ✅ Are query params populated in `st.query_params`?

One of these MUST fail for the endpoint to not work. The logs will definitively show which one.

## What Happens Next

Based on your log pattern:

**Pattern A → Fix #1:** Implement alternative to st.query_params
- Use request headers or path-based routing
- Or environment variable to trigger tick
- Test and verify

**Pattern B → Fix #2:** Debug launcher or job creation
- Add more logging after QUEUE_TICK_ENTRY
- Find where it fails
- Fix that specific issue

**Pattern C → Fix #3:** Service routing issue
- Verify deployment
- Check service configuration
- Fix routing/authentication

## Summary

**Current Status:**
- ❌ Queue tick endpoint not being called
- ❌ Jobs stay PENDING forever
- ✅ Debug logging added to diagnose

**Your Action:**
1. Pull debug code
2. Resubmit benchmark
3. Check debug logs
4. Share output

**Our Action:**
1. Analyze your log pattern
2. Implement specific fix
3. Verify it works

**Not claiming it's fixed until you confirm jobs actually run!**
