# FIX NOW

## The Issue

You have `GOOGLE_APPLICATION_CREDENTIALS` environment variable set. This takes precedence over impersonation and causes 403 errors.

## The Fix

```bash
# 1. Unset the environment variable
unset GOOGLE_APPLICATION_CREDENTIALS

# 2. Run the script
python scripts/process_queue_simple.py --loop
```

That's it! Jobs will launch successfully.

## Why

- `GOOGLE_APPLICATION_CREDENTIALS` points to a service account key that doesn't have `run.jobs.run` permission
- Unsetting it allows the impersonated service account to be used
- The impersonated service account (`mmm-web-service-sa`) has all necessary permissions

## Permanent Fix

Add to your `~/.bashrc` or `~/.zshrc`:

```bash
# Don't set GOOGLE_APPLICATION_CREDENTIALS for local dev
# unset GOOGLE_APPLICATION_CREDENTIALS
```
