# FIX NOW

## The Issue

You need permission to impersonate the service account.

Error: `Permission 'iam.serviceAccounts.getAccessToken' denied`

## The Fix

Run this command to grant yourself permission:

```bash
gcloud iam service-accounts add-iam-policy-binding \
  mmm-web-service-sa@datawarehouse-422511.iam.gserviceaccount.com \
  --member="user:$(gcloud config get-value account)" \
  --role="roles/iam.serviceAccountTokenCreator"
```

This grants you the `iam.serviceAccountTokenCreator` role, which allows impersonation.

## Then Run the Script

```bash
python scripts/process_queue_simple.py --loop
```

All 21 jobs will launch!

## Why This is Needed

The script uses impersonated credentials to authenticate as `mmm-web-service-sa`, which has all the necessary permissions to:
- Execute Cloud Run jobs
- Read/write GCS queues  
- Pull container images

Your user account needs permission to impersonate this service account.

## Note

You can keep your `GOOGLE_APPLICATION_CREDENTIALS` environment variable set - the script handles this automatically.
