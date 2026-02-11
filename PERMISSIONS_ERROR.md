# 🚨 Permissions Error - You Were Right!

## The Error You're Seeing

```
403 Permission 'run.jobs.run' denied on resource 'projects/datawarehouse-422511/locations/europe-west1/jobs/mmm-app-dev-training'
```

## You Were Correct!

**You asked:** "are you sure it's not a GOOGLE CREDENTIALS issue?"

**Answer:** **YES, you're absolutely right! It IS a Google credentials/permissions issue!**

## What's Happening

The script successfully:
- ✅ Authenticates with Google Cloud (ADC is working)
- ✅ Loads the queue from GCS (can read from storage)
- ✅ Prepares to launch jobs

But fails when:
- ❌ Trying to execute Cloud Run jobs
- ❌ Missing IAM permission: `run.jobs.run`

## Understanding the Issue

### Authentication vs Authorization

**Authentication (Who you are):**
- You have this! ✅
- Provided by: `gcloud auth application-default login`
- Proves your identity to Google Cloud

**Authorization (What you can do):**
- You're missing this! ❌
- Provided by: IAM roles and permissions
- Controls which actions you can perform

### The Missing Permission

To run Cloud Run jobs, you need:
- **Permission:** `run.jobs.run`
- **Typically granted by roles:**
  - `roles/run.admin` (Cloud Run Admin)
  - `roles/run.developer` (Cloud Run Developer)
  - `roles/editor` (Editor - includes many permissions)
  - `roles/owner` (Owner - includes all permissions)

## Check Your Current Permissions

Run this command to see what permissions you have:

```bash
gcloud projects get-iam-policy datawarehouse-422511 \
  --flatten="bindings[].members" \
  --filter="bindings.members:user:$(gcloud config get-value account)"
```

Look for roles like:
- `roles/run.developer`
- `roles/run.admin`
- `roles/editor`
- `roles/owner`

If you don't see any of these, that's why you're getting the error!

## Solutions

### Option 1: Request IAM Role (Recommended)

**For User:**
Contact your GCP project administrator and request the `Cloud Run Developer` role.

**For Administrator:**
Grant the user the necessary role:

```bash
# Get user's email
USER_EMAIL="user@example.com"

# Grant Cloud Run Developer role
gcloud projects add-iam-policy-binding datawarehouse-422511 \
  --member="user:${USER_EMAIL}" \
  --role="roles/run.developer"
```

**Alternatives:**
- `roles/run.admin` - Full Cloud Run permissions
- `roles/editor` - Broader permissions (includes Cloud Run)

### Option 2: Use Service Account with Permissions

If you have a service account key file with the right permissions:

```bash
# Authenticate with service account
gcloud auth activate-service-account --key-file=/path/to/key.json

# Set as active account
gcloud config set account SERVICE_ACCOUNT_EMAIL
```

### Option 3: Impersonate Service Account

If you have permission to impersonate a service account:

```bash
# Set impersonation
gcloud config set auth/impersonate_service_account mmm-training-job-sa@datawarehouse-422511.iam.gserviceaccount.com

# Run the script
python scripts/process_queue_simple.py --loop

# Unset impersonation when done
gcloud config unset auth/impersonate_service_account
```

### Option 4: Use Cloud Console

If you have console access but not API access:
1. Go to https://console.cloud.google.com/run/jobs?project=datawarehouse-422511
2. Find `mmm-app-dev-training`
3. Click "Execute" manually

## For Administrators

### Check Who Needs Access

```bash
# List current IAM bindings for Cloud Run
gcloud projects get-iam-policy datawarehouse-422511 \
  --flatten="bindings[].members" \
  --filter="bindings.role:roles/run.*"
```

### Grant Access

```bash
# Grant Cloud Run Developer
gcloud projects add-iam-policy-binding datawarehouse-422511 \
  --member="user:USER_EMAIL" \
  --role="roles/run.developer"

# Or grant Cloud Run Admin
gcloud projects add-iam-policy-binding datawarehouse-422511 \
  --member="user:USER_EMAIL" \
  --role="roles/run.admin"
```

### Service Account Option

Create a service account with the right permissions:

```bash
# Create service account
gcloud iam service-accounts create mmm-queue-processor \
  --display-name="MMM Queue Processor" \
  --project=datawarehouse-422511

# Grant Cloud Run Developer role
gcloud projects add-iam-policy-binding datawarehouse-422511 \
  --member="serviceAccount:mmm-queue-processor@datawarehouse-422511.iam.gserviceaccount.com" \
  --role="roles/run.developer"

# Grant Storage Object Admin (for GCS access)
gcloud projects add-iam-policy-binding datawarehouse-422511 \
  --member="serviceAccount:mmm-queue-processor@datawarehouse-422511.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"

# Create key
gcloud iam service-accounts keys create mmm-queue-processor-key.json \
  --iam-account=mmm-queue-processor@datawarehouse-422511.iam.gserviceaccount.com

# User can then use this key
```

## Verify the Fix

After permissions are granted, test with:

```bash
# Try to execute the job directly
gcloud run jobs execute mmm-app-dev-training \
  --region=europe-west1 \
  --wait

# If that works, the queue processor will work too
python scripts/process_queue_simple.py --loop
```

## Expected Output After Fix

```
2026-02-11 17:42:04,089 - INFO - Processing job 1/21
2026-02-11 17:42:04,658 - INFO -   Country: de
2026-02-11 17:42:05,129 - INFO - Saved queue to GCS
2026-02-11 17:42:06,500 - INFO - ✅ Launched job: mmm-app-dev-training
2026-02-11 17:42:06,500 - INFO - ✅ Job launched successfully
2026-02-11 17:42:06,500 - INFO - Execution: mmm-app-dev-training-abc123
...
```

## Summary

**The Issue:** IAM permissions, not authentication
**The Permission Needed:** `run.jobs.run`
**The Role Needed:** `roles/run.developer` or `roles/run.admin`
**The Solution:** Have admin grant you the appropriate IAM role

**You were absolutely correct - this is a Google credentials/permissions issue!**

The code is working perfectly. Your account just needs the right IAM role to execute Cloud Run jobs.

## Quick Reference

```bash
# Check your permissions
gcloud projects get-iam-policy datawarehouse-422511 \
  --flatten="bindings[].members" \
  --filter="bindings.members:user:$(gcloud config get-value account)"

# Test if you can run jobs
gcloud run jobs execute mmm-app-dev-training --region=europe-west1 --wait

# If admin needs to grant access
gcloud projects add-iam-policy-binding datawarehouse-422511 \
  --member="user:USER_EMAIL" \
  --role="roles/run.developer"
```

**Need help?** Contact your GCP project administrator to grant you Cloud Run permissions.
