# Job Exists But Still Getting 403 Error

## The Situation

You've confirmed:
- ✅ Cloud Run job `mmm-app-dev-training` exists in `europe-west1`
- ✅ You have `roles/owner` (includes ALL permissions)
- ✅ You have `roles/run.developer` (includes `run.jobs.run`)
- ❌ Script still gives 403 error

This narrows down the issue significantly!

## Critical First Test

Before anything else, test if YOU can manually execute the job:

```bash
gcloud run jobs execute mmm-app-dev-training --region=europe-west1 --wait
```

**Two possible outcomes:**

### If This WORKS ✅
The issue is with how the **script** authenticates, not your credentials.
→ Skip to "Script Authentication Issues" section below

### If This FAILS ❌
The issue is with your **credentials**, not the script.
→ Continue to "Credential Issues" section below

---

## Most Likely Cause: Cached Credentials

### Why This Happens

**Timeline:**
1. You ran `gcloud auth application-default login` before having permissions
2. A token was cached with your old (limited) permissions
3. Admin granted you `roles/owner` and `roles/run.developer`
4. **But your cached token still has the old permissions!**
5. The script uses the cached token → 403 error

**The token is cached here:**
```
~/.config/gcloud/application_default_credentials.json
```

It won't refresh until it expires (typically 1 hour) or you manually refresh it.

### The Solution: Complete ADC Refresh

```bash
# Step 1: Remove the cached credentials
rm -rf ~/.config/gcloud/application_default_credentials.json

# Step 2: Get fresh credentials with your current permissions
gcloud auth application-default login

# Step 3: Set the quota project explicitly
gcloud auth application-default set-quota-project datawarehouse-422511

# Step 4: Verify it worked
gcloud auth application-default print-access-token

# Step 5: Retry the script
python scripts/process_queue_simple.py --loop
```

**Expected result:** Jobs will now launch successfully!

---

## Other Possible Causes

### 2. Quota Project Mismatch

Your ADC might be using the wrong project for billing/quota.

**Check current quota project:**
```bash
gcloud auth application-default print-access-token --format=json | jq .
```

**Set it explicitly:**
```bash
gcloud auth application-default set-quota-project datawarehouse-422511
```

### 3. IAM Policy Has Conditions

Rare with `roles/owner`, but possible. Check if your role bindings have conditions:

```bash
gcloud projects get-iam-policy datawarehouse-422511 \
  --format=json | \
  jq '.bindings[] | select(.members[] | contains("user:ivana.penc@gmail.com"))'
```

Look for `"condition"` fields. If present, they might be blocking you (time-based, IP-based, etc.).

### 4. Script Authentication Issue

If manual `gcloud` execution works but the script doesn't, the script might not be using ADC correctly.

**Check what the script is doing:**
```python
# The script should use Application Default Credentials
from google.auth import default
from google.cloud import run_v2

credentials, project = default()
```

**Verify the script is using the right project:**
```bash
export GOOGLE_CLOUD_PROJECT=datawarehouse-422511
export GOOGLE_APPLICATION_CREDENTIALS=""  # Use ADC, not a key file
```

---

## Verification Steps

After refreshing credentials, verify:

**1. Test manual execution:**
```bash
gcloud run jobs execute mmm-app-dev-training --region=europe-west1 --wait
```

**2. Check ADC is working:**
```bash
gcloud auth application-default print-access-token
```

**3. Verify project:**
```bash
gcloud config get-value project
# Should show: datawarehouse-422511
```

**4. Run the script:**
```bash
python scripts/process_queue_simple.py --loop
```

---

## Quick Troubleshooting Checklist

Run through these in order:

- [ ] Job exists: `gcloud run jobs list --region=europe-west1`
- [ ] Manual execution works: `gcloud run jobs execute mmm-app-dev-training --region=europe-west1`
- [ ] Refresh ADC: Remove cache and re-login
- [ ] Set quota project: `gcloud auth application-default set-quota-project datawarehouse-422511`
- [ ] Verify project: `gcloud config get-value project`
- [ ] Check for IAM conditions
- [ ] Retry script

---

## Summary

**Most Likely Issue:** Cached ADC credentials from before IAM role was granted

**Quick Fix:**
```bash
rm -rf ~/.config/gcloud/application_default_credentials.json
gcloud auth application-default login
gcloud auth application-default set-quota-project datawarehouse-422511
python scripts/process_queue_simple.py --loop
```

**If that doesn't work:** Check the other causes above or see `STILL_403_ERROR.md` for more comprehensive troubleshooting.
