# Still Getting 403 Error Despite Owner Role? 🤔

## The Situation

You have:
- ✅ `roles/owner` (includes ALL permissions)
- ✅ `roles/run.developer` (includes `run.jobs.run`)

But still get:
```
403 Permission 'run.jobs.run' denied on resource 
'projects/datawarehouse-422511/locations/europe-west1/jobs/mmm-app-dev-training'
(or resource may not exist).
```

This is puzzling because `roles/owner` should give you access to everything! Let's troubleshoot.

---

## The Key Clue

Notice the error says: **(or resource may not exist)**

This is the important part! The Cloud Run job might not exist yet.

---

## 5 Possible Causes

### 1. The Job Doesn't Exist (Most Likely!)

**The Issue:**
The Cloud Run job `mmm-app-dev-training` hasn't been deployed yet.

**Check:**
```bash
gcloud run jobs list --region=europe-west1
```

Or specifically:
```bash
gcloud run jobs describe mmm-app-dev-training --region=europe-west1
```

**If you get `NOT_FOUND` or don't see the job:**
→ **That's the problem!** The infrastructure hasn't been deployed.

**Solution:**
The Cloud Run job needs to be created first. This is typically done via:
- Terraform: `cd infra/terraform && terraform apply`
- CI/CD: Push to main branch to trigger deployment
- Manual: Create the job in Cloud Console

### 2. IAM Propagation Delay

**The Issue:**
You just added `roles/run.developer`. IAM changes take 2-3 minutes to propagate globally.

**Check:**
How long ago did you add the role?

**Solution:**
```bash
# Wait 2-3 minutes
sleep 180

# Then retry
python scripts/process_queue_simple.py --loop
```

### 3. Cached Credentials

**The Issue:**
Application Default Credentials are cached and might not reflect the new permissions.

**Check:**
```bash
# Check ADC location
gcloud auth application-default print-access-token
```

**Solution:**
```bash
# Refresh credentials
gcloud auth application-default login

# Set quota project
gcloud auth application-default set-quota-project datawarehouse-422511

# Retry
python scripts/process_queue_simple.py --loop
```

### 4. Wrong Project Configuration

**The Issue:**
Your gcloud might be using a different project.

**Check:**
```bash
gcloud config get-value project
```

**Expected:** `datawarehouse-422511`

**Solution:**
```bash
# Set correct project
gcloud config set project datawarehouse-422511

# Refresh credentials with correct project
gcloud auth application-default login

# Retry
python scripts/process_queue_simple.py --loop
```

### 5. Wrong Region

**The Issue:**
The job might be in a different region.

**Check:**
```bash
# List all Cloud Run jobs in all regions
gcloud run jobs list --format="table(name,region)"
```

**Solution:**
Update the script to use the correct region, or deploy the job to `europe-west1`.

---

## Step-by-Step Troubleshooting

Follow these steps in order:

### Step 1: Check If Job Exists

```bash
gcloud run jobs list --region=europe-west1
```

**Look for:** `mmm-app-dev-training`

- **If NOT found:** → Go to "Deploy Infrastructure" below
- **If found:** → Continue to Step 2

### Step 2: Wait for Propagation

If you just added the IAM role:

```bash
# Wait 2-3 minutes
echo "Waiting for IAM propagation..."
sleep 180

# Retry
python scripts/process_queue_simple.py --loop
```

**If still failing:** → Continue to Step 3

### Step 3: Refresh Credentials

```bash
# Re-authenticate
gcloud auth application-default login

# Ensure correct project
gcloud config set project datawarehouse-422511

# Set quota project
gcloud auth application-default set-quota-project datawarehouse-422511

# Retry
python scripts/process_queue_simple.py --loop
```

**If still failing:** → Continue to Step 4

### Step 4: Verify Everything

```bash
# Check your email
gcloud config get-value account

# Check your project  
gcloud config get-value project

# Check your roles
gcloud projects get-iam-policy datawarehouse-422511 \
  --flatten="bindings[].members" \
  --filter="bindings.members:user:$(gcloud config get-value account)" \
  --format="table(bindings.role)"

# Check if job exists
gcloud run jobs describe mmm-app-dev-training --region=europe-west1
```

### Step 5: Try Direct Test

```bash
# Try to execute the job directly
gcloud run jobs execute mmm-app-dev-training \
  --region=europe-west1 \
  --wait
```

**If this works:** The issue is with the script's authentication
**If this fails too:** Check the error message carefully

---

## Deploy Infrastructure (If Job Doesn't Exist)

If the Cloud Run job doesn't exist, you need to deploy it:

### Option 1: Terraform (Recommended)

```bash
cd infra/terraform

# Initialize
terraform init

# Plan (review changes)
terraform plan -var-file=envs/dev.tfvars

# Apply
terraform apply -var-file=envs/dev.tfvars
```

### Option 2: CI/CD

Push your code to trigger the deployment:

```bash
git push origin dev  # For dev environment
# or
git push origin main  # For prod environment
```

The CI/CD pipeline will create the Cloud Run job.

### Option 3: Check Deployment Status

```bash
# Check recent deployments
gcloud run jobs list --region=europe-west1

# Check CI/CD status
# (Check GitHub Actions in the repository)
```

---

## Quick Diagnostic Checklist

```
□ Waited 2-3 minutes after adding IAM role
□ Refreshed ADC: gcloud auth application-default login
□ Verified project: gcloud config get-value project
□ Checked job exists: gcloud run jobs describe mmm-app-dev-training
□ Confirmed region is europe-west1
□ Tried direct execution: gcloud run jobs execute
```

---

## What If Nothing Works?

If you've tried everything:

1. **Check organization policies:**
   ```bash
   gcloud resource-manager org-policies list --project=datawarehouse-422511
   ```

2. **Check service account:**
   The job might need to run as a specific service account with permissions.

3. **Check job configuration:**
   ```bash
   gcloud run jobs describe mmm-app-dev-training --region=europe-west1 --format=yaml
   ```

4. **Enable Cloud Run API:**
   ```bash
   gcloud services enable run.googleapis.com
   ```

5. **Check billing:**
   Ensure the project has billing enabled.

---

## Summary

**Most Likely Cause:** The Cloud Run job `mmm-app-dev-training` doesn't exist yet.

**Quick Check:**
```bash
gcloud run jobs list --region=europe-west1 | grep mmm-app-dev-training
```

**If no output:** Deploy the infrastructure first!

**If job exists:** Wait 2-3 minutes for IAM propagation, then refresh credentials.

---

**Next:** See PERMISSIONS_ERROR.md for more details on IAM permissions.
