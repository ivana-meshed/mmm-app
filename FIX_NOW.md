# FIX NOW

## The Issue

Script was getting 403 error even with impersonation.

## The Fix - UPDATED!

**Good news:** You don't need to unset `GOOGLE_APPLICATION_CREDENTIALS`!

Just run:
```bash
python scripts/process_queue_simple.py --loop
```

## What Changed

The script now uses impersonated credentials **explicitly**:
- Creates impersonated credentials for `mmm-web-service-sa`
- Passes these credentials to all GCS and Cloud Run clients
- Ignores `GOOGLE_APPLICATION_CREDENTIALS` environment variable

## User Action

```bash
# Pull latest code
git pull origin copilot/build-benchmarking-script

# Run script - keeps your environment as-is
python scripts/process_queue_simple.py --loop
```

Jobs will launch successfully!

## Why This Works

- Script explicitly uses impersonated service account credentials
- Service account has all necessary permissions
- Your `GOOGLE_APPLICATION_CREDENTIALS` can stay set for other work
- No conflicts!
