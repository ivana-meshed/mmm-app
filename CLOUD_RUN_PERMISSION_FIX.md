# Cloud Run Permission Error - Quick Fix

## Problem

When running the benchmark script with `--trigger-queue`, you get:
```
403 Permission 'run.services.get' denied on resource 'projects/.../services/mmm-app'
```

## Root Cause

The trigger script tries to automatically discover the Cloud Run service URL by querying the Cloud Run API. This requires the `run.services.get` IAM permission, which you don't have.

## Solution

Set the `WEB_SERVICE_URL` environment variable to bypass the API query.

### Option 1: Get URL via gcloud (Recommended)

If you have gcloud access with proper permissions:

```bash
# For development service
gcloud run services describe mmm-app-dev \
  --region=europe-west1 \
  --format='value(status.url)'

# OR for production service
gcloud run services describe mmm-app \
  --region=europe-west1 \
  --format='value(status.url)'

# This will output something like:
# https://mmm-app-dev-abc123-ew.a.run.app
```

Then set it:
```bash
export WEB_SERVICE_URL=https://mmm-app-dev-abc123-ew.a.run.app

# Now run your benchmark
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue
```

### Option 2: Get URL from Cloud Console

1. Go to Cloud Console: https://console.cloud.google.com/run?project=datawarehouse-422511
2. Find the service: `mmm-app-dev` or `mmm-app`
3. Click on it to see details
4. Copy the URL from the top of the page
5. Set the environment variable:

```bash
export WEB_SERVICE_URL=<url-from-console>

# Now run your benchmark
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue
```

### Option 3: Ask Someone with Access

If you don't have access to gcloud or Cloud Console, ask a team member who has the `run.services.get` permission to get the URL for you.

## Making it Permanent

To avoid setting this every time, add it to your shell profile:

```bash
# Add to ~/.zshrc or ~/.bashrc
echo 'export WEB_SERVICE_URL=https://mmm-app-dev-abc123-ew.a.run.app' >> ~/.zshrc

# Reload
source ~/.zshrc
```

Or create a `.env` file in your project:
```bash
# .env file
WEB_SERVICE_URL=https://mmm-app-dev-abc123-ew.a.run.app
PROJECT_ID=datawarehouse-422511
REGION=europe-west1
GCS_BUCKET=mmm-app-output
```

Then load it before running:
```bash
source .env
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --trigger-queue
```

## Verification

After setting `WEB_SERVICE_URL`, you should see:
```
2026-02-11 11:33:49 - INFO - Getting Cloud Run service URL...
2026-02-11 11:33:49 - INFO - Using WEB_SERVICE_URL from environment
2026-02-11 11:33:49 - INFO - ✅ Service URL: https://mmm-app-dev-abc123-ew.a.run.app
```

## Why This Happens

The trigger script needs to call the Cloud Run web service to trigger queue processing. It tries to auto-discover the service URL using the Cloud Run API, but this requires IAM permissions:

- `run.services.get` - To query service information
- `run.services.list` - To list services (optional)

If you don't have these permissions, the script can't auto-discover the URL, so you need to provide it via the `WEB_SERVICE_URL` environment variable.

## Alternative: Request IAM Permission

If you want to avoid setting the environment variable, you can request the IAM permission from your admin:

**Role needed**: `roles/run.viewer` (Cloud Run Viewer)

This role includes:
- `run.services.get`
- `run.services.list`
- And other read-only Cloud Run permissions

## Troubleshooting

### "Service URL works in browser but script fails"

The script also needs authentication to call the service. Make sure you're authenticated:
```bash
gcloud auth application-default login
```

### "Still getting 403 after setting WEB_SERVICE_URL"

This means the service call itself is failing, not the URL discovery. Check:
1. Is the URL correct?
2. Are you authenticated? (`gcloud auth application-default login`)
3. Does your account have permission to invoke the service?

### "How do I know which service to use?"

- Development: `mmm-app-dev` (use this for testing)
- Production: `mmm-app` (use this for production work)

If unsure, ask your team or check which environment you're working in.

## Summary

**Quick fix:**
```bash
# Get the URL (ask admin or use gcloud)
export WEB_SERVICE_URL=https://mmm-app-dev-abc123-ew.a.run.app

# Run benchmark
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue
```

**Permanent fix:**
Add `export WEB_SERVICE_URL=...` to your `~/.zshrc` or `~/.bashrc`

**Long-term fix:**
Request `roles/run.viewer` IAM role from your admin
