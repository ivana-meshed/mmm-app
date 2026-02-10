# Quick Fix: Permission Denied Error

If you see this error when running `track_daily_costs.py`:

```
Error: Permission denied when accessing BigQuery
Access Denied: Project datawarehouse-422511: User does not have bigquery.jobs.create permission
```

## ⚠️ IMPORTANT: If You Just Granted Permissions

**IAM permissions can take 2-5 minutes to propagate!**

If you just ran the `gcloud projects add-iam-policy-binding` command:
1. ✅ **WAIT 2-5 MINUTES** before trying again
2. Clear your credentials cache:
   ```bash
   gcloud auth application-default revoke
   gcloud auth application-default login
   ```
3. Wait another 1-2 minutes, then try the script again

## Quick Solutions

### Option 1: Grant Yourself Permissions (if you're an admin)

```bash
# Replace YOUR_EMAIL with your actual email
gcloud projects add-iam-policy-binding datawarehouse-422511 \
  --member="user:YOUR_EMAIL@example.com" \
  --role="roles/bigquery.user"
```

**Then WAIT 2-5 minutes** for IAM to propagate, and re-authenticate:
```bash
gcloud auth application-default revoke
gcloud auth application-default login
```

### Option 2: Grant Dataset-Level Access

Sometimes project-level permissions aren't enough. Try dataset-level:

```bash
# Check if you can access the dataset
bq show --format=prettyjson datawarehouse-422511:mmm_billing
```

If that fails, grant dataset access via Console:
1. Go to [BigQuery Console](https://console.cloud.google.com/bigquery?project=datawarehouse-422511&ws=!1m4!1m3!3m2!1sdatawarehouse-422511!2smmm_billing)
2. Find dataset: `mmm_billing`
3. Click **Share** → **Permissions**
4. Add your user with **BigQuery Data Viewer** role
5. **WAIT 2-5 minutes** then try again

### Option 3: Request Access (if you're not an admin)

Send this message to your GCP administrator:

```
Hi,

I need access to run the MMM cost tracking script. Could you please grant me:

Role: BigQuery User
Project: datawarehouse-422511
Dataset: mmm_billing

Or grant me these specific permissions:
- bigquery.jobs.create
- bigquery.tables.getData

After granting, please note that IAM changes can take 2-5 minutes to propagate.

Thank you!
```

### Option 4: Use Service Account

If you have a service account key:

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account-key.json"
python scripts/track_daily_costs.py
```

## Verify Your Permissions

After granting permissions and waiting, verify they're active:

```bash
# Check your current roles
gcloud projects get-iam-policy datawarehouse-422511 \
  --flatten="bindings[].members" \
  --filter="bindings.members:user:YOUR_EMAIL@example.com"

# Should list datasets (if permissions are active)
bq ls --project_id=datawarehouse-422511

# Should show the billing dataset (if access granted)
bq show mmm_billing
```

## Still Not Working After Waiting?

### 1. Check Authentication

Make sure you're using the right Google account:
```bash
gcloud auth list
gcloud config get-value project
```

### 2. Clear Credential Cache

Old credentials might be cached:
```bash
# Revoke and re-authenticate
gcloud auth application-default revoke
gcloud auth application-default login

# Wait 1-2 minutes, then try script again
```

### 3. Check for Organization Policies

Your organization might have policies blocking access:
```bash
# Check if you can access BigQuery at all
gcloud services list --enabled --filter="bigquery"
```

If this command fails, contact your GCP administrator about organization policies.

### 4. Verify Table Exists

Check if the billing export table exists:
```bash
bq show datawarehouse-422511:mmm_billing.gcp_billing_export_resource_v1_01B2F0_BCBFB7_2051C5
```

If this fails with "Not found", the billing export might not be configured.

## Common Issues

### Issue: "I granted permissions but still get errors"
**Solution**: IAM propagation takes 2-5 minutes. Clear cache and wait:
```bash
gcloud auth application-default revoke
gcloud auth application-default login
# Wait 2-5 minutes before trying again
```

### Issue: "I have Owner role but still can't access"
**Solution**: Owner role doesn't automatically grant BigQuery access. You need:
- Project-level: `roles/bigquery.user`
- OR Dataset-level: `roles/bigquery.dataViewer`

### Issue: "Commands work but script still fails"
**Solution**: You might have an environment variable set:
```bash
# Check for conflicting credentials
echo $GOOGLE_APPLICATION_CREDENTIALS

# If set, unset it temporarily
unset GOOGLE_APPLICATION_CREDENTIALS

# Re-authenticate
gcloud auth application-default login
```

## Need More Help?

- Full documentation: [COST_TRACKING_README.md](COST_TRACKING_README.md)
- Check troubleshooting section for more solutions
- Contact your GCP administrator for permission issues

## Timeline: How Long Should Each Step Take?

1. Grant permissions: **Instant**
2. IAM propagation: **2-5 minutes**
3. Re-authenticate: **30 seconds**
4. Test script: **10 seconds**

**Total expected time: 5-10 minutes** (including waiting for IAM)
