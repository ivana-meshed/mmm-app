# Ultra-Verbose Logging Guide - Issue #13

## The Problem

When checking for debug logs, **NO logs appear at all**:
```bash
$ gcloud logging read "... (textPayload=~\"QUEUE_CHECK\" OR textPayload=~\"QUEUE_HANDLER\")" ...
(nothing)
```

This means the handler functions we added debug logging to aren't being called.

## What We Added

**Ultra-verbose startup logging** at the very beginning of `streamlit_app.py` to trace execution from the first line:

### 10 Logging Checkpoints

1. **App Startup** - First line executed
2. **Python Version** - Environment info
3. **Streamlit Import** - Module loading
4. **Page Config** - Streamlit setup
5. **Query Params Check** - Type, value, boolean, keys
6. **Before Import** - app_split_helpers import
7. **After Import** - Import succeeded
8. **Before Handler** - About to call handler
9. **Handler Execution** - Inside handler (existing)
10. **After Handler** - Handler returned

Each checkpoint uses the `[APP_STARTUP]` prefix for easy filtering.

### Query Params Logging

Special attention to query params:
```python
logger.info(f"[APP_STARTUP] st.query_params type: {type(query_params_obj)}")
logger.info(f"[APP_STARTUP] st.query_params value: {query_params_obj}")
logger.info(f"[APP_STARTUP] st.query_params bool: {bool(query_params_obj)}")
if query_params_obj:
    logger.info(f"[APP_STARTUP] st.query_params keys: {list(query_params_obj.keys())}")
```

## How to View Logs

```bash
gcloud logging read \
  "resource.type=cloud_run_revision 
   resource.labels.service_name=mmm-app-dev-web
   textPayload=~\"APP_STARTUP\"" \
  --limit=50 \
  --format="value(textPayload)"
```

## Three Scenarios

### Scenario A: No APP_STARTUP Logs

**What you see:**
```
(nothing at all)
```

**What it means:**
- Streamlit app does NOT execute for HTTP GET requests
- Only runs for browser/UI sessions
- This is a fundamental architecture issue

**Why it happens:**
- Streamlit is designed for interactive web UIs
- May not execute full Python code for non-browser requests
- HTTP requests might return cached/static response

**What's next:**
- Need different architecture for HTTP-triggered processing
- Options:
  1. Add FastAPI/Flask endpoint alongside Streamlit
  2. Use Cloud Functions to process queue
  3. Use Cloud Tasks with dedicated endpoint
  4. File-based triggers (GCS event when queue updated)
  5. Cloud Scheduler calling Cloud Function instead of web service

**Impact:** Major architectural change needed.

### Scenario B: Has APP_STARTUP, Empty Query Params

**What you see:**
```
[APP_STARTUP] Streamlit app starting...
[APP_STARTUP] Python version: 3.x.x
[APP_STARTUP] Streamlit imported successfully
[APP_STARTUP] Page config set
[APP_STARTUP] st.query_params type: <class 'dict'>
[APP_STARTUP] st.query_params value: {}
[APP_STARTUP] st.query_params bool: False
[APP_STARTUP] app_split_helpers imported successfully
[APP_STARTUP] About to call handle_queue_tick_if_requested()...
[APP_STARTUP] handle_queue_tick_if_requested() returned
```

**What it means:**
- App DOES execute for HTTP requests ✅
- But `st.query_params` is empty/doesn't work in HTTP mode
- Handler checks for params, finds none, returns early

**Why it happens:**
- Streamlit's `st.query_params` may only work for browser sessions
- HTTP requests might not populate query params the same way
- Different execution context between UI and HTTP

**What's next:**
- Use alternative to `st.query_params`:
  1. Environment variable (set at deployment)
  2. Request headers (if accessible)
  3. Path-based routing (e.g., `/queue_tick`)
  4. File-based flag in GCS
  5. Check for specific HTTP headers

**Impact:** Moderate fix, stay with current architecture.

### Scenario C: Has APP_STARTUP, Query Params Work

**What you see:**
```
[APP_STARTUP] Streamlit app starting...
[APP_STARTUP] Page config set
[APP_STARTUP] st.query_params type: <class 'dict'>
[APP_STARTUP] st.query_params value: {'queue_tick': '1', 'name': 'default-dev'}
[APP_STARTUP] st.query_params bool: True
[APP_STARTUP] st.query_params keys: ['queue_tick', 'name']
[APP_STARTUP] About to call handle_queue_tick_if_requested()...
[QUEUE_CHECK] handle_queue_tick_if_requested() called
[QUEUE_CHECK] st.query_params: {'queue_tick': '1', 'name': 'default-dev'}
[QUEUE_HANDLER] query_params: {'queue_tick': '1', 'name': 'default-dev'}
[QUEUE_TICK_ENTRY] QUEUE TICK ENDPOINT CALLED
(more logs...)
[APP_STARTUP] handle_queue_tick_if_requested() returned
```

**What it means:**
- App runs ✅
- Query params work ✅
- Issue is in handler or launcher logic

**What's next:**
- Check for subsequent logs:
  - `[QUEUE_TICK_FOUND]` - Job found?
  - `[LAUNCHER_ENTRY]` - Launcher called?
  - `[LAUNCHER_ERROR]` - Error in launcher?
- Debug specific issue in processing chain
- Use existing comprehensive logging

**Impact:** Continue with current approach, fix specific bug.

## Diagnosis Flow

```
Pull code → Submit benchmark → Wait → Check logs
                                        ↓
                        ┌───────────────┴───────────────┐
                        ↓                               ↓
                 No APP_STARTUP logs          Has APP_STARTUP logs
                        ↓                               ↓
               Scenario A                    Check query_params value
             Architecture issue                          ↓
                                        ┌────────────────┴────────────────┐
                                        ↓                                 ↓
                                  Empty {}                        Has values
                                        ↓                                 ↓
                                  Scenario B                        Scenario C
                              Fix query params                  Debug handler
```

## User Action

1. Pull latest code:
```bash
git pull origin copilot/build-benchmarking-script
```

2. Resubmit benchmark:
```bash
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue
```

3. Wait 30-60 seconds

4. Check for startup logs:
```bash
gcloud logging read \
  "resource.type=cloud_run_revision 
   resource.labels.service_name=mmm-app-dev-web
   textPayload=~\"APP_STARTUP\"" \
  --limit=50 \
  --format="value(textPayload)"
```

5. Share what you see (or don't see)

## Why This Works

The logging is at the **very first line** of the Streamlit app, before any imports or setup. If the app executes AT ALL for HTTP requests, we'll see logs.

The `[APP_STARTUP]` logs are unavoidable - they happen before any conditional logic, error handling, or query param checks.

**If we see NO APP_STARTUP logs, it definitively proves the app doesn't run for HTTP requests.**

## Critical Question

**Does the Streamlit app execute its Python code when receiving HTTP GET requests?**

This logging will answer definitively. Everything else depends on this answer.

## What Happens Next

Based on the scenario:
- **Scenario A** → Architectural discussion and alternatives
- **Scenario B** → Fix query params approach
- **Scenario C** → Continue debugging handlers

## Summary

- Added ultra-verbose logging at first line
- 10 checkpoints from startup to handler return
- Will reveal if app runs for HTTP
- Will show query params status
- Will guide next steps

**Not claiming it's fixed - this is diagnosis to understand the architecture's viability.**
