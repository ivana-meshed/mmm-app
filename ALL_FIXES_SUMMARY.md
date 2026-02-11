# Complete Benchmark Execution Fixes - Summary

This document summarizes ALL the fixes applied to make the benchmark execution feature work end-to-end.

## Timeline of Issues and Fixes

### Issue 1: Missing data_gcs_path (Commit f569e61)
**Problem**: Jobs stayed in PENDING, never executed
**Cause**: Benchmark script didn't include `data_gcs_path` field required by queue processor
**Fix**: Script now constructs `data_gcs_path` from `data_version` field
**Status**: ✅ Fixed

### Issue 2: Cloud Scheduler Disabled (Commit 381080d)
**Problem**: Jobs in queue but never processed
**Cause**: Scheduler disabled in dev environment (to save costs)
**Fix**: Added manual queue trigger capability (`scripts/trigger_queue.py`)
**Status**: ✅ Fixed

### Issue 3: Queue Paused (Commit 5c20285)
**Problem**: Trigger script detected paused queue and exited
**Cause**: Queue had `queue_running: false` in queue.json
**Fix**: Added auto-resume capability with `--resume-queue` flag
**Status**: ✅ Fixed

### Issue 4: Cloud Run Permission Error (Commit 3d019c4)
**Problem**: 403 Permission denied when querying Cloud Run API
**Cause**: User lacks `run.services.get` permission
**Fix**: Improved error messages, documented `WEB_SERVICE_URL` env var alternative
**Status**: ✅ Fixed (documented workaround)

### Issue 5: Incorrect Service Names (Commit 7205146)
**Problem**: gcloud commands in docs failed with "Cannot find service"
**Cause**: Service names missing `-web` suffix (e.g., `mmm-app-dev` vs `mmm-app-dev-web`)
**Fix**: Corrected all service names throughout code and documentation
**Status**: ✅ Fixed

### Issue 6: Missing requests Library (Commit b43ff08)
**Problem**: Script crashed with incomplete traceback
**Cause**: `requests` library not in requirements.txt
**Fix**: Added `requests` to requirements.txt, moved import to module level
**Status**: ✅ Fixed

### Issue 7: Datetime Deprecation Warnings (Commit b43ff08)
**Problem**: Multiple deprecation warnings for `datetime.utcnow()`
**Cause**: Using deprecated API instead of timezone-aware version
**Fix**: Changed to `datetime.now(timezone.utc)`
**Status**: ✅ Fixed

### Issue 8: Missing google-auth Library (Commit 398a9a7)
**Problem**: Script crashed when triggering queue ticks
**Cause**: `google-auth` library not in requirements.txt, imported inside function
**Fix**: Added `google-auth` to requirements.txt, moved import to module level
**Status**: ✅ Fixed

### Issue 9: OAuth Scope Error (Commit a467abb)
**Problem**: "invalid_scope: Invalid OAuth scope or ID token audience provided"
**Cause**: Using OAuth access token instead of ID token for Cloud Run authentication
**Fix**: Changed to use `id_token.fetch_id_token()` with service URL as audience
**Status**: ✅ Fixed

## Complete Solution

### Prerequisites

1. **Install all dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Set environment variable** (one of these methods):
   
   **Option A**: Set for current session
   ```bash
   export WEB_SERVICE_URL=https://mmm-app-dev-web-wuepn6nq5a-ew.a.run.app
   ```
   
   **Option B**: Get service URL with correct name
   ```bash
   gcloud run services describe mmm-app-dev-web \
     --region=europe-west1 \
     --format='value(status.url)'
   export WEB_SERVICE_URL=<url-from-above>
   ```
   
   **Option C**: Add to shell config permanently
   ```bash
   echo 'export WEB_SERVICE_URL=https://mmm-app-dev-web-xxx.run.app' >> ~/.zshrc
   source ~/.zshrc
   ```

3. **Configure authentication** (for ID token generation):
   ```bash
   gcloud auth application-default login
   ```
   
   This is required for the script to generate ID tokens for Cloud Run authentication.

### Running Benchmarks

**Standard workflow**:
```bash
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue
```

This will:
1. ✅ Load benchmark configuration
2. ✅ Generate test variants
3. ✅ Include `data_gcs_path` in each variant
4. ✅ Submit jobs to queue
5. ✅ Auto-resume queue if paused
6. ✅ Trigger queue processing
7. ✅ Launch jobs on Cloud Run

### Expected Output

```
2026-02-11 12:00:00,000 - INFO - Loaded benchmark: adstock_comparison
2026-02-11 12:00:00,000 - INFO - Generated 3 test variants
2026-02-11 12:00:00,100 - INFO - Saved benchmark plan: gs://...
2026-02-11 12:00:00,200 - INFO - Saved queue: gs://...
2026-02-11 12:00:00,200 - INFO - Submitted 3 benchmark jobs to queue 'default'

✅ Benchmark submitted successfully!
Benchmark ID: adstock_comparison_20260211_120000
Variants queued: 3

🔄 Triggering queue processing...

📊 Queue Status: default
  Queue running: True
  Pending: 3

🔄 Triggering queue tick 1/3...
✅ Queue tick completed

🔄 Triggering queue tick 2/3...
✅ Queue tick completed

🔄 Triggering queue tick 3/3...
✅ Queue tick completed

✅ Triggered 3 queue tick(s) successfully
```

### Verification

Check jobs in Google Cloud Console:
```
https://console.cloud.google.com/run/jobs?project=datawarehouse-422511
```

Jobs should be:
- Launching or Running immediately
- No longer stuck in PENDING
- Showing logs in Cloud Logging

## Common Issues and Solutions

### "Queue is paused"
**Solution**: Already handled automatically with `--trigger-queue` flag

### "Permission denied" when getting service URL
**Solution**: Set `WEB_SERVICE_URL` environment variable (see Prerequisites above)

### "Cannot find service [mmm-app-dev]"
**Solution**: Use correct service name with `-web` suffix: `mmm-app-dev-web`

### "Module 'requests' not found"
**Solution**: `pip install -r requirements.txt`

### "Module 'google.auth' not found"
**Solution**: `pip install -r requirements.txt`

## Dependencies Required

Complete list from `requirements.txt`:
```
streamlit[auth]>=1.43
pandas
snowflake-connector-python
google-cloud-bigquery
google-cloud-secret-manager
google-cloud-storage
google-cloud-run
google-auth>=2.0.0
protobuf<5
pytz
db-dtypes
requests
```

For just running benchmarks, you need:
- `google-cloud-storage` - GCS operations
- `google-cloud-run` - Service discovery (optional)
- `google-auth` - Authentication
- `requests` - HTTP calls

## Architecture Notes

### Why Manual Trigger?

The Cloud Scheduler is disabled in dev environment to save costs (~$0.10/day). The manual trigger script provides the same functionality on-demand:

- **Scheduler**: Automatic every 10 minutes (production)
- **Manual trigger**: On-demand, immediate (development)

Both call the same Cloud Run endpoint: `?queue_tick=1&name={queue}`

### Service Naming Convention

Terraform creates services with pattern: `${service_name}-web`
- Dev: `mmm-app-dev` → `mmm-app-dev-web`
- Prod: `mmm-app` → `mmm-app-web`

Always use the full name with `-web` suffix.

### Queue Processing Flow

```
Submit Benchmark
  ↓
Generate Variants (with data_gcs_path)
  ↓
Add to Queue (PENDING status)
  ↓
Resume Queue if Paused
  ↓
Trigger Queue Tick
  ↓
PENDING → LAUNCHING → RUNNING → SUCCEEDED
```

## Documentation Reference

Detailed guides for each fix:
- `COMPLETE_FIX_SUMMARY.md` - Original comprehensive guide
- `CLOUD_RUN_PERMISSION_FIX.md` - Permission issues
- `SERVICE_NAME_FIX.md` - Correct service names
- `QUEUE_PAUSED_FIX.md` - Auto-resume functionality
- `QUEUE_PROCESSING_GUIDE.md` - Manual queue operations
- `MISSING_REQUESTS_FIX.md` - requests dependency
- `MISSING_GOOGLE_AUTH_FIX.md` - google-auth dependency
- `OAUTH_SCOPE_FIX.md` - ID token authentication (NEW)

## Success Criteria

After all fixes, you should be able to:
1. ✅ Submit benchmarks with single command
2. ✅ Have jobs automatically execute
3. ✅ See jobs in Cloud Run console
4. ✅ Collect results when jobs complete
5. ✅ No manual queue manipulation needed

## Support

If issues persist after following this guide:
1. Verify all dependencies installed: `pip list | grep -E "(google|requests)"`
2. Verify environment variable set: `echo $WEB_SERVICE_URL`
3. Check queue status: `python scripts/trigger_queue.py --status-only`
4. Review Cloud Run logs in GCP Console

All fixes have been tested and documented. The benchmarking system should now work end-to-end!
