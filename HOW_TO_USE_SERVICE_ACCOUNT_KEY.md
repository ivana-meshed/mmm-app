# How to Use the Service Account Key File

**Quick Answer:** This is the simplest way to run the queue processor. No impersonation, no waiting, just works!

## What You Need

The service account key for: `mmm-web-service-sa@datawarehouse-422511.iam.gserviceaccount.com`

## Step 1: Create the Key

Run this command to create a new key file:

```bash
gcloud iam service-accounts keys create mmm-web-service-sa-key.json \
  --iam-account=mmm-web-service-sa@datawarehouse-422511.iam.gserviceaccount.com
```

This creates `mmm-web-service-sa-key.json` in your current directory.

## Step 2: Use the Key

Set the environment variable to point to your key file:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=$PWD/mmm-web-service-sa-key.json
```

**Note:** Use the full path to the key file. `$PWD` gives you the current directory.

## Step 3: Run the Script

Now just run the queue processor:

```bash
python scripts/process_queue_simple.py --loop
```

## What You'll See

The script will:
1. Detect your key file
2. Use it automatically (no impersonation)
3. Log: "Using service account credentials from: /path/to/key.json"
4. Process all pending jobs
5. Launch each as a Cloud Run job

Expected output:
```
2026-02-11 18:00:00,000 - INFO - Using service account credentials from: /path/to/mmm-web-service-sa-key.json
2026-02-11 18:00:00,000 - INFO - ============================================================
2026-02-11 18:00:00,000 - INFO - MMM Queue Processor (Standalone)
...
📊 Queue Status: default-dev
  Total: 21
  Pending: 21
Processing job 1/21
✅ Launched job: mmm-app-dev-training
✅ Job launched successfully
...
✅ Processed 21 job(s)
```

## That's It!

No impersonation setup needed. No waiting for IAM propagation. Just works immediately!

## Security Note

The key file grants full access as the service account. Keep it secure:
- Don't commit it to git (add `*-key.json` to `.gitignore`)
- Don't share it
- Store it safely on your local machine

## Troubleshooting

**If you get permission errors:**
Make sure the service account has the necessary roles:
- `roles/run.admin` - Execute Cloud Run jobs
- `roles/storage.objectAdmin` - Read/write GCS
- `roles/artifactregistry.reader` - Pull container images

These should already be configured in the infrastructure.

## Why This is Best

✅ **Simple** - Just set an environment variable
✅ **Fast** - Works immediately, no waiting
✅ **Reliable** - No impersonation complexity
✅ **No conflicts** - Doesn't affect your other credentials

You can keep your existing `GOOGLE_APPLICATION_CREDENTIALS` for other work and just change it when running this script!
