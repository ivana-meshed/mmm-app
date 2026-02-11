# ⚠️ 403 Error Despite Owner Role? Read This First!

## TL;DR

You have `roles/owner` but still get 403. **The Cloud Run job probably doesn't exist yet.**

Run this command:
```bash
gcloud run jobs list --region=europe-west1 | grep mmm-app-dev-training
```

- **No output?** → Job doesn't exist → Deploy infrastructure first
- **Has output?** → Job exists → Wait 2-3 minutes and retry

---

## Quick Check

```bash
gcloud run jobs list --region=europe-west1
```

**Look for:** `mmm-app-dev-training`

### If NOT in list:

**That's the problem!** The Cloud Run job hasn't been deployed yet.

**Solution - Deploy via Terraform:**
```bash
cd infra/terraform
terraform init
terraform apply -var-file=envs/dev.tfvars
```

**Or push to trigger CI/CD:**
```bash
git push origin dev
```

### If IN list:

Your IAM permissions just need time to propagate (2-3 minutes).

**Solution:**
```bash
# Wait a bit
sleep 180

# Refresh credentials
gcloud auth application-default login

# Retry
python scripts/process_queue_simple.py --loop
```

---

## Why This Happens

The error message says:
```
403 Permission 'run.jobs.run' denied...
(or resource may not exist)  ← This is the clue!
```

Even with owner role, if the resource doesn't exist, you get a 403!

---

## Next Steps

1. **Check if job exists** (command above)
2. **If no** → Deploy infrastructure
3. **If yes** → Wait and retry

See **STILL_403_ERROR.md** for complete troubleshooting.

---

**Bottom Line:** The Cloud Run job `mmm-app-dev-training` needs to be created before you can execute it!
