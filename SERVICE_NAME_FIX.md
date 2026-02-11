# Service Name Fix - Important Update

## The Issue You Encountered

You tried to get the Cloud Run service URL using the commands from our error messages:

```bash
gcloud run services describe mmm-app-dev --region=europe-west1
# No output or error

gcloud run services describe mmm-app --region=europe-west1
ERROR: Cannot find service [mmm-app]
```

Both commands failed because **the service names were wrong**!

## The Fix

The Cloud Run services have a `-web` suffix that was missing from our documentation:

**Wrong Service Names** (what we told you):
- ❌ `mmm-app-dev`
- ❌ `mmm-app`

**Correct Service Names** (what actually exists):
- ✅ `mmm-app-dev-web`
- ✅ `mmm-app-web`

## How to Get the URL Now

### Option 1: Use the Correct Service Name

```bash
# For development (note the -web suffix!)
gcloud run services describe mmm-app-dev-web \
  --region=europe-west1 \
  --format='value(status.url)'

# This should return something like:
# https://mmm-app-dev-web-wuepn6nq5a-ew.a.run.app
```

### Option 2: List All Services

If you're unsure which service to use:

```bash
gcloud run services list --region=europe-west1

# Look for services with "mmm" and "-web" in the name
```

### Option 3: Cloud Console

1. Go to: https://console.cloud.google.com/run?project=datawarehouse-422511
2. Look for a service with `-web` in the name:
   - `mmm-app-dev-web` (development)
   - `mmm-app-web` (production)
3. Click on it and copy the URL

## Complete Working Example

```bash
# 1. Get the URL (with correct service name)
gcloud run services describe mmm-app-dev-web \
  --region=europe-west1 \
  --format='value(status.url)'

# Output: https://mmm-app-dev-web-wuepn6nq5a-ew.a.run.app

# 2. Set the environment variable
export WEB_SERVICE_URL=https://mmm-app-dev-web-wuepn6nq5a-ew.a.run.app

# 3. Run your benchmark
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue
```

## What Changed

I've fixed:
1. ✅ Service names in `scripts/trigger_queue.py`
2. ✅ All gcloud commands in error messages
3. ✅ All documentation files:
   - `CLOUD_RUN_PERMISSION_FIX.md`
   - `QUEUE_PROCESSING_GUIDE.md`
   - `COMPLETE_FIX_SUMMARY.md`
4. ✅ Added service listing option as alternative

## Why This Happened

The terraform configuration defines the service as:
```terraform
resource "google_cloud_run_service" "web_service" {
  name = "${var.service_name}-web"  # Adds -web suffix!
}
```

So:
- `service_name = "mmm-app-dev"` → creates `mmm-app-dev-web`
- `service_name = "mmm-app"` → creates `mmm-app-web`

Our code and docs were using the variable name instead of the actual service name.

## Apologies

Sorry for the confusion! The wrong service names in our documentation made it impossible for you to get the URL. This is now fixed, and the correct commands with the `-web` suffix should work.

## Next Steps

1. **Pull the latest changes** (if not already done):
   ```bash
   git pull origin copilot/build-benchmarking-script
   ```

2. **Get the URL with the correct service name**:
   ```bash
   gcloud run services describe mmm-app-dev-web --region=europe-west1 --format='value(status.url)'
   ```

3. **Set the environment variable**:
   ```bash
   export WEB_SERVICE_URL=<url-from-step-2>
   ```

4. **Run your benchmark**:
   ```bash
   python scripts/benchmark_mmm.py \
     --config benchmarks/adstock_comparison.json \
     --trigger-queue
   ```

This should now work! 🎉
