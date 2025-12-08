# Debugging Queue Background Execution

The queue should run automatically via Cloud Scheduler every minute. If it's not working, follow these steps:

## Quick Check: Is the Cloud Scheduler Running?

1. Open Google Cloud Console
2. Navigate to **Cloud Scheduler** (search for it in the top search bar)
3. Look for job named `robyn-queue-tick`
4. Check:
   - **Status**: Should be `ENABLED`
   - **Last run**: Should be within the last minute
   - **Next run**: Should show upcoming time
   - **Success/Failure**: Check if recent runs succeeded

## Step-by-Step Debugging

### Step 1: Verify Cloud Scheduler exists and is enabled

```bash
gcloud scheduler jobs describe robyn-queue-tick \
  --project=datawarehouse-422511 \
  --location=europe-west1
```

Expected output should include:
- `state: ENABLED`
- `schedule: '*/1 * * * *'` (every minute)
- `lastAttemptTime` showing recent execution

**If job doesn't exist**: The Terraform deployment may not have completed. Check CI/CD logs.

### Step 2: Check Cloud Scheduler execution history

In Cloud Console:
1. Go to **Cloud Scheduler**
2. Click on `robyn-queue-tick` job
3. Click **VIEW** next to "Logs"
4. Look for recent executions

Or via command line:
```bash
gcloud logging read "resource.type=cloud_scheduler_job 
  AND resource.labels.job_id=robyn-queue-tick" \
  --project=datawarehouse-422511 \
  --limit=10 \
  --format=json
```

### Step 3: Check if queue tick endpoint is being called

Check Cloud Run logs for queue tick requests:

```bash
gcloud logging read "resource.type=cloud_run_revision 
  AND resource.labels.service_name=mmm-app-web
  AND (textPayload=~\"QUEUE_TICK\" OR textPayload=~\"queue_tick\")" \
  --project=datawarehouse-422511 \
  --limit=20 \
  --format=json
```

You should see log entries like:
- `[QUEUE_TICK] Endpoint called for queue 'default' in bucket 'mmm-app-output'`
- `[QUEUE_TICK] Completed successfully: {...}`

**If no logs appear**: The scheduler is not reaching the endpoint (check Step 4).

### Step 4: Verify OIDC authentication is configured

The scheduler needs permission to invoke the Cloud Run service:

```bash
gcloud run services get-iam-policy mmm-app-web \
  --project=datawarehouse-422511 \
  --region=europe-west1 \
  | grep robyn-queue-scheduler
```

Should show:
```
- members:
  - serviceAccount:robyn-queue-scheduler@datawarehouse-422511.iam.gserviceaccount.com
  role: roles/run.invoker
```

**If missing**: Re-run Terraform to fix IAM bindings.

### Step 5: Check queue state in GCS

The queue must have:
1. Jobs in PENDING state
2. `queue_running` flag set to `true`

Check queue file:
```bash
gsutil cat gs://mmm-app-output/robyn-queues/default/queue.json | jq '.'
```

Look for:
- `"queue_running": true` (if false, queue is paused)
- `"entries"` array with jobs in `"status": "PENDING"`

**If queue_running is false**:
1. Go to the app → Queue Monitor tab
2. Click the "▶️ Start Queue" button
3. Verify the queue starts processing

**If no PENDING jobs**:
The queue is empty - add jobs via the Queue Builder or Run Models tab.

### Step 6: Manually trigger Cloud Scheduler (Test)

Force the scheduler to run immediately:

```bash
gcloud scheduler jobs run robyn-queue-tick \
  --project=datawarehouse-422511 \
  --location=europe-west1
```

Then immediately check Cloud Run logs (Step 3) to see if it processed.

### Step 7: Test the endpoint directly (Advanced)

You can test the queue tick endpoint directly by visiting it with authentication:

1. Get an OIDC token:
```bash
TOKEN=$(gcloud auth print-identity-token \
  --audiences=https://mmm-app-web-wuepn6nq5a-ew.a.run.app)
```

2. Call the endpoint:
```bash
curl -H "Authorization: Bearer $TOKEN" \
  "https://mmm-app-web-wuepn6nq5a-ew.a.run.app?queue_tick=1&name=default"
```

This should return JSON with the tick result.

## Common Issues and Solutions

### Issue 1: Cloud Scheduler not deployed
**Symptom**: Job doesn't exist in Cloud Scheduler
**Solution**: Deploy via Terraform:
```bash
cd infra/terraform
terraform init
terraform apply -var-file="envs/prod.tfvars"
```

### Issue 2: Queue is paused
**Symptom**: Logs show "queue is paused"
**Solution**: 
- Go to Queue Monitor tab
- Click "▶️ Start Queue" button

### Issue 3: Wrong queue name
**Symptom**: Scheduler runs but no jobs process
**Solution**: Scheduler is configured for queue `default`. If using different name:
- Option A: Use queue name `default` in the app
- Option B: Update `TF_VAR_queue_name` in `.github/workflows/ci.yml` and redeploy

### Issue 4: Authentication failures
**Symptom**: Scheduler logs show 401/403 errors
**Solution**: 
- Verify IAM binding (Step 4)
- Re-run Terraform to fix permissions

### Issue 5: No jobs in queue
**Symptom**: Everything works but nothing runs
**Solution**: 
- Verify jobs are in PENDING state (Step 5)
- Add jobs via Queue Builder in the app

## Need More Help?

If none of the above resolves the issue, collect these details:

1. Output of Step 1 (Cloud Scheduler description)
2. Last 10 Cloud Scheduler logs (Step 2)
3. Last 20 Cloud Run logs for queue_tick (Step 3)
4. Queue state JSON (Step 5)
5. Screenshot of Queue Monitor tab showing queue state

Share these with the development team for further investigation.
