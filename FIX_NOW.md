# FIX NOW

## The Issue

Script fails with impersonation even after granting permission.

Error: `Permission 'iam.serviceAccounts.getAccessToken' denied`

## The Best Fix: Use Service Account Key File

If you have access to the service account key file, use it directly:

```bash
# Point to the service account key file  
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/mmm-web-service-sa-key.json

# Run the script
python scripts/process_queue_simple.py --loop
```

The script will automatically detect and use the key file. **Jobs will launch immediately!**

## Alternative: Wait for IAM Propagation

If you already ran:
```bash
gcloud iam service-accounts add-iam-policy-binding \
  mmm-web-service-sa@datawarehouse-422511.iam.gserviceaccount.com \
  --member="user:$(gcloud config get-value account)" \
  --role="roles/iam.serviceAccountTokenCreator"
```

**IAM changes can take 2-3 minutes to propagate.** Wait a bit, then retry:

```bash
python scripts/process_queue_simple.py --loop
```

## Why Service Account Key is Better

- Works immediately (no IAM propagation delay)
- No impersonation complexity
- Direct authentication
- Simpler and more reliable

The script is designed to use either method - it will automatically use a key file if `GOOGLE_APPLICATION_CREDENTIALS` points to the right service account.
