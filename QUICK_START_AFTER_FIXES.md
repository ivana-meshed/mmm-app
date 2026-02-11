# Quick Start - Benchmarks After All Fixes

This guide gets you running benchmarks quickly after all 9 fixes have been applied.

## The Latest Fix (OAuth Scope Error)

**What was wrong**: The script was using OAuth access tokens instead of ID tokens to authenticate with Cloud Run.

**What changed**: Now uses proper ID token authentication with the service URL as the audience.

**Reference**: See `OAUTH_SCOPE_FIX.md` for detailed explanation.

## Quick Setup (3 Steps)

### 1. Pull Latest Changes

```bash
git pull origin copilot/build-benchmarking-script
```

### 2. Install/Update Dependencies

```bash
pip install -r requirements.txt
```

This includes:
- ✅ google-cloud-storage
- ✅ google-cloud-run  
- ✅ google-auth (for ID tokens)
- ✅ requests
- ✅ All other dependencies

### 3. Configure Environment

```bash
# A. Configure authentication (for ID token generation)
gcloud auth application-default login

# B. Get and set service URL
gcloud run services describe mmm-app-dev-web \
  --region=europe-west1 \
  --format='value(status.url)'

# Set the URL (replace with actual URL from above)
export WEB_SERVICE_URL=https://mmm-app-dev-web-wuepn6nq5a-ew.a.run.app
```

**Note**: Use `mmm-app-dev-web` (with `-web` suffix), not just `mmm-app-dev`.

## Run Your Benchmark

```bash
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue
```

## Expected Output (Success)

```
2026-02-11 12:XX:XX - INFO - Loaded benchmark: adstock_comparison
2026-02-11 12:XX:XX - INFO - Generated 3 test variants
2026-02-11 12:XX:XX - INFO - Saved benchmark plan: gs://mmm-app-output/benchmarks/...
2026-02-11 12:XX:XX - INFO - Saved queue: gs://mmm-app-output/robyn-queues/default/queue.json

✅ Benchmark submitted successfully!
Benchmark ID: adstock_comparison_20260211_HHMMSS
Variants queued: 3
Queue: default

🔄 Triggering queue processing...

📊 Queue Status: default
  Total jobs: 3
  Pending: 3
  Running: 0
  Completed: 0
  Queue running: True

🔄 Triggering queue tick 1/3...
2026-02-11 12:XX:XX - INFO - ✅ Launched

🔄 Triggering queue tick 2/3...
2026-02-11 12:XX:XX - INFO - ✅ Launched

🔄 Triggering queue tick 3/3...
2026-02-11 12:XX:XX - INFO - ✅ Launched

✅ Queue processing triggered for 3 job(s)
```

Key indicators of success:
- ✅ No OAuth/scope errors
- ✅ "✅ Launched" messages for each tick
- ✅ Jobs transition from PENDING to LAUNCHING

## Verify in Google Cloud Console

1. Go to: https://console.cloud.google.com/run/jobs?project=datawarehouse-422511
2. You should see jobs with names like: `mmm-app-training-xxx`
3. Status should show: Running or Succeeded

## What All 9 Fixes Addressed

1. ✅ Missing data_gcs_path in queue params
2. ✅ Cloud Scheduler disabled (manual trigger added)
3. ✅ Queue paused (auto-resume added)
4. ✅ Cloud Run permission errors (documented WEB_SERVICE_URL)
5. ✅ Wrong service names (added -web suffix)
6. ✅ Missing requests library
7. ✅ Datetime deprecation warnings
8. ✅ Missing google-auth library
9. ✅ OAuth scope error (ID token authentication)

## Troubleshooting

### "invalid_scope" error still occurs

Check authentication:
```bash
# Verify ADC is configured
gcloud auth application-default print-access-token

# If empty or error, reconfigure
gcloud auth application-default login
```

### "Cannot find service" error

Make sure you're using the correct service name with `-web` suffix:
```bash
# List all services to find the right one
gcloud run services list --region=europe-west1

# Should see: mmm-app-dev-web (not mmm-app-dev)
```

### Jobs still not executing

Check that WEB_SERVICE_URL is set:
```bash
echo $WEB_SERVICE_URL
# Should output the URL, not empty
```

### Other Issues

See the comprehensive guides:
- `ALL_FIXES_SUMMARY.md` - Complete list of all fixes
- `OAUTH_SCOPE_FIX.md` - Authentication details
- `QUEUE_PROCESSING_GUIDE.md` - Manual queue operations

## Make Setup Permanent

To avoid setting environment variables each time:

```bash
# Add to your shell config (~/.zshrc or ~/.bashrc)
echo 'export WEB_SERVICE_URL=https://mmm-app-dev-web-wuepn6nq5a-ew.a.run.app' >> ~/.zshrc

# Reload
source ~/.zshrc
```

## Next Steps

After jobs complete:
```bash
# Collect results
python scripts/benchmark_mmm.py \
  --collect-results adstock_comparison_20260211_HHMMSS \
  --export-format csv
```

This will aggregate results from all variants and export to CSV for analysis.

## Success!

If you see jobs executing in Google Cloud Console and no errors in the output, congratulations! The benchmark system is now working end-to-end.

For analysis and interpretation of results, see:
- `benchmarks/README.md` - Benchmark configuration guide
- `benchmarks/WORKFLOW_EXAMPLE.md` - Complete workflow with analysis examples
