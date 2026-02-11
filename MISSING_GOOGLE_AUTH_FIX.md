# Missing google-auth Dependency - Fix Guide

## Problem

When running the benchmark script with `--trigger-queue`, it crashes after starting to trigger queue ticks:

```
🔄 Triggering queue tick 1/3...
Traceback (most recent call last):
  File "/Users/ivanapenc/software/mmm-app/scripts/trigger_queue.py", line 434, in 
```

## Root Cause

The `trigger_queue.py` script uses Google Authentication libraries (`google-auth`) to authenticate HTTP requests to Cloud Run, but this dependency was not listed in `requirements.txt`.

The import happened **inside a function**, so the error only occurred at runtime when the function was called, not when the script started.

## Solution

### Quick Fix

```bash
# 1. Pull latest changes
git pull origin copilot/build-benchmarking-script

# 2. Install the missing dependency
pip install google-auth

# OR install all requirements
pip install -r requirements.txt

# 3. Verify it's working
python -c "from google.auth import default; print('✅ google-auth installed')"

# 4. Run your benchmark
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue
```

### What Was Fixed

1. **Added to requirements.txt**:
   ```
   google-auth>=2.0.0
   ```

2. **Moved imports to module level**:
   - Previously: Import happened inside `trigger_queue_via_http()` function
   - Now: Import happens at module startup with proper error checking

3. **Better error handling**:
   - Script now fails fast at startup if dependency is missing
   - Clear error message tells you what to install
   - Full traceback printed for any errors

## Expected Behavior After Fix

### If google-auth is missing:
```
2026-02-11 12:00:00,000 - ERROR - google-auth library not installed. Install with: pip install google-auth
```

Script exits immediately with clear instructions.

### If everything is installed:
```
2026-02-11 12:00:00,000 - INFO - Checking queue status for 'default'...
2026-02-11 12:00:00,500 - INFO - Getting Cloud Run service URL...
2026-02-11 12:00:00,501 - INFO - Using WEB_SERVICE_URL from environment
2026-02-11 12:00:00,501 - INFO - ✅ Service URL: https://mmm-app-dev-web-xxx.run.app
2026-02-11 12:00:00,501 - INFO - Will trigger 3 queue tick(s)

🔄 Triggering queue tick 1/3...
✅ Queue tick completed
```

## Why This Happened

The script uses Google's authentication system to make authenticated HTTP requests to your Cloud Run service. This requires the `google-auth` library, but it was accidentally omitted from the requirements file.

## Dependencies Now Required

After this fix, the complete list of dependencies for the trigger script includes:

- `google-cloud-storage` - For reading queue data from GCS
- `google-cloud-run` - For service discovery (optional)
- `google-auth` - For authentication (NEW)
- `requests` - For HTTP calls

All are now properly listed in `requirements.txt`.

## Related Fixes

This is the second missing dependency fix:
1. First fix: Added `requests` library
2. **This fix**: Added `google-auth` library

Both were being imported inside functions rather than at module level, causing runtime failures.

## Verification

After installing, verify all dependencies:

```bash
python -c "
import google.cloud.storage
import google.cloud.run_v2
import google.auth
import requests
print('✅ All dependencies installed correctly')
"
```

## Next Steps

Once you've installed the dependencies and pulled the latest code:

1. Set your `WEB_SERVICE_URL` environment variable (if not already set)
2. Run the benchmark with `--trigger-queue` flag
3. Jobs should now process successfully!

If you encounter other issues, check the other fix guides:
- `CLOUD_RUN_PERMISSION_FIX.md` - Permission issues
- `MISSING_REQUESTS_FIX.md` - Missing requests library
- `SERVICE_NAME_FIX.md` - Incorrect service names
