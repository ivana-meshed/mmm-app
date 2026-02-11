# OAuth Scope Error Fix

## Problem

When running the benchmark script with `--trigger-queue`, you encountered this error:

```
Fatal error: ('invalid_scope: Invalid OAuth scope or ID token audience provided.', 
{'error': 'invalid_scope', 'error_description': 'Invalid OAuth scope or ID token audience provided.'})
```

## What Went Wrong

The trigger script was trying to authenticate to Cloud Run using an **OAuth access token**, but Cloud Run services require **ID token** authentication with the service URL as the audience.

### Technical Details

**Wrong approach (before fix):**
```python
credentials, project = default()
credentials.refresh(AuthRequest())
token = credentials.token  # This is an access token ❌
```

Access tokens are used for Google Cloud APIs, but not for invoking Cloud Run services.

**Correct approach (after fix):**
```python
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

auth_req = google_requests.Request()
id_token_value = id_token.fetch_id_token(auth_req, service_url)  # ID token with audience ✅
```

ID tokens are specific to the target service (Cloud Run URL) and properly authenticated.

## Solution

The fix has been implemented in commit `a467abb`. The script now:

1. Uses `google.oauth2.id_token.fetch_id_token()` instead of regular credentials
2. Sets the service URL as the audience for the ID token
3. Properly authenticates with Cloud Run services

## How to Fix Your Issue

### 1. Pull Latest Changes

```bash
git pull origin copilot/build-benchmarking-script
```

### 2. Ensure Authentication is Configured

**For local development:**
```bash
gcloud auth application-default login
```

This creates Application Default Credentials (ADC) that the script uses to generate ID tokens.

**For Cloud/production:**
The service account running the script must have:
- `roles/run.invoker` role on the target Cloud Run service
- Proper Workload Identity configuration

### 3. Run Benchmark

```bash
export WEB_SERVICE_URL=https://mmm-app-dev-web-xxx.run.app

python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue
```

### Expected Output

With the fix, you should see:
```
✅ Benchmark submitted successfully!
Benchmark ID: adstock_comparison_20260211_HHMMSS
Variants queued: 3

🔄 Triggering queue processing...

📊 Queue Status: default
  Queue running: True

🔄 Triggering queue tick 1/3...
✅ Launched

🔄 Triggering queue tick 2/3...
✅ Launched

🔄 Triggering queue tick 3/3...
✅ Launched

✅ Queue processing triggered for 3 job(s)
```

## Why This Error Happened

Cloud Run services have a specific authentication model:

1. **API calls** (like querying service info) use OAuth access tokens
2. **Service invocation** (calling the service endpoint) requires ID tokens

The original code was using approach #1 for operation #2, which doesn't work.

## References

- [Cloud Run Service-to-Service Authentication](https://cloud.google.com/run/docs/authenticating/service-to-service)
- [ID Tokens vs Access Tokens](https://cloud.google.com/docs/authentication/token-types)

## Related Issues

This fix is part of the complete benchmark execution solution. See:
- `ALL_FIXES_SUMMARY.md` - Complete list of all fixes
- `CLOUD_RUN_PERMISSION_FIX.md` - Permission requirements
- `MISSING_GOOGLE_AUTH_FIX.md` - Dependency installation

## Verification

To verify the fix worked:

1. Check that queue processing triggers without errors
2. Look for jobs in Google Cloud Run Jobs console
3. Monitor job execution status in Streamlit UI

If you still get authentication errors, check:
- ADC is properly configured: `gcloud auth application-default print-access-token`
- Service account has proper roles
- Service URL is correct in WEB_SERVICE_URL

## Success Criteria

✅ No "invalid_scope" error
✅ Queue ticks trigger successfully  
✅ Jobs transition from PENDING to LAUNCHING
✅ Jobs appear in Cloud Run Jobs console
