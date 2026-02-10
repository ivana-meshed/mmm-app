# Quick Fix: Permission Denied Error

If you see this error when running `track_daily_costs.py`:

```
Error: Permission denied when accessing BigQuery
Access Denied: Project datawarehouse-422511: User does not have bigquery.jobs.create permission
```

## Quick Solutions

### Option 1: Grant Yourself Permissions (if you're an admin)

```bash
# Replace YOUR_EMAIL with your actual email
gcloud projects add-iam-policy-binding datawarehouse-422511 \
  --member="user:YOUR_EMAIL@example.com" \
  --role="roles/bigquery.user"
```

Then re-authenticate:
```bash
gcloud auth application-default login
```

### Option 2: Request Access (if you're not an admin)

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

Thank you!
```

### Option 3: Use Service Account

If you have a service account key:

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account-key.json"
python scripts/track_daily_costs.py
```

## Verify It Works

After getting permissions, test with:

```bash
# Should list datasets
bq ls --project_id=datawarehouse-422511

# Should show the billing dataset
bq show mmm_billing

# Run the script
python scripts/track_daily_costs.py --days 7
```

## Still Not Working?

1. Check you're using the right Google account:
   ```bash
   gcloud auth list
   ```

2. Make sure you're authenticated:
   ```bash
   gcloud auth application-default login
   ```

3. Verify project access:
   ```bash
   gcloud projects describe datawarehouse-422511
   ```

4. Check the full error message and see [COST_TRACKING_README.md](COST_TRACKING_README.md) for detailed troubleshooting.

## Need Help?

- Full documentation: [COST_TRACKING_README.md](COST_TRACKING_README.md)
- Check troubleshooting section for more solutions
- Contact your GCP administrator for permission issues
