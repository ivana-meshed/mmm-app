# Missing Requests Dependency Fix

## The Issue You Encountered

When running the benchmark with `--trigger-queue`, the script crashed with:

```
Traceback (most recent call last):
  File "/Users/ivanapenc/software/mmm-app/scripts/trigger_queue.py", line 422, in 
```

The traceback was incomplete, making it hard to diagnose, but the root cause was a **missing dependency**.

## Root Cause

The `trigger_queue.py` script uses the `requests` library to make HTTP calls to the Cloud Run service, but `requests` was not in `requirements.txt`.

The script tried to import `requests` inside a function:
```python
def trigger_queue_via_http(...):
    import requests  # This would fail if requests not installed!
    ...
```

## The Fix

We've fixed two issues:

### 1. Added `requests` to requirements.txt

```diff
 streamlit[auth]>=1.43
 pandas
 ...
+requests
```

### 2. Improved Import Checking

Now the script checks for `requests` at import time (top of file) and exits immediately with a clear error if missing:

```python
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    logger.error("requests library not installed. Install with: pip install requests")
    sys.exit(1)
```

### 3. Fixed Datetime Deprecation Warnings

Also fixed all the `datetime.utcnow()` warnings you were seeing:
```python
# Before (deprecated)
datetime.utcnow().isoformat()

# After (recommended)
datetime.now(timezone.utc).isoformat()
```

## How to Fix Your Setup

1. **Pull the latest changes**:
   ```bash
   git pull origin copilot/build-benchmarking-script
   ```

2. **Install/update dependencies**:
   ```bash
   pip install -r requirements.txt
   # or if using virtual environment:
   source .venv/bin/activate  # or on Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Verify requests is installed**:
   ```bash
   python -c "import requests; print('requests installed:', requests.__version__)"
   ```

4. **Run your benchmark again**:
   ```bash
   python scripts/benchmark_mmm.py \
     --config benchmarks/adstock_comparison.json \
     --trigger-queue
   ```

## Expected Output

After installing dependencies, you should see:

```
✅ Benchmark submitted successfully!
Benchmark ID: adstock_comparison_20260211_104726
Variants queued: 3

🔄 Triggering queue processing...

📊 Queue Status: default
  Queue running: True

🔄 Triggering queue tick 1/3...
✅ Queue tick completed

🔄 Triggering queue tick 2/3...
✅ Queue tick completed

🔄 Triggering queue tick 3/3...
✅ Queue tick completed

✅ Queue processing triggered for 3 job(s)
```

## What Changed

**requirements.txt**:
- Added `requests` library

**scripts/trigger_queue.py**:
- Import `requests` at module level
- Exit immediately if `requests` is missing
- Better error messages

**scripts/benchmark_mmm.py**:
- Fixed deprecated `datetime.utcnow()` usage (no more warnings!)
- Improved subprocess error output (shows both stdout and stderr)

## Why This Happened

The `requests` library is a commonly used HTTP library, often assumed to be installed. However, it's not part of Python's standard library and must be explicitly listed in `requirements.txt`.

We were importing it inside a function, which delayed the error until the function was called, making it harder to diagnose.

## Summary

**The problem**: Missing `requests` dependency caused runtime error  
**The fix**: Added to requirements.txt + better import checking  
**Action needed**: `pip install -r requirements.txt`

Once you install the dependencies, everything should work! 🎉
