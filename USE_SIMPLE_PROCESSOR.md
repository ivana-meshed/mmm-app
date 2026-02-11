# Use The Simple Queue Processor

## The Issue

The first standalone processor (`process_queue_standalone.py`) had Streamlit dependencies that caused import errors:
```
WARNING streamlit.runtime.caching.cache_data_api: No runtime found
Traceback (most recent call last):
  File "process_queue_standalone.py", line 28
```

## The Solution

Use **`process_queue_simple.py`** instead - it's truly standalone with ZERO dependencies on app modules!

## How To Use

### Quick Start

```bash
# Pull latest code
git pull origin copilot/build-benchmarking-script

# Process all pending jobs
python scripts/process_queue_simple.py --loop
```

### Options

**Process all jobs (loop until empty):**
```bash
python scripts/process_queue_simple.py --loop
```

**Process one job:**
```bash
python scripts/process_queue_simple.py --count 1
```

**Process 5 jobs:**
```bash
python scripts/process_queue_simple.py --count 5
```

**Custom queue/bucket:**
```bash
python scripts/process_queue_simple.py \
  --queue-name default-dev \
  --bucket mmm-app-output \
  --training-job-name mmm-app-dev-training \
  --loop
```

### Environment Variables

You can also set these (optional):
```bash
export DEFAULT_QUEUE_NAME=default-dev
export GCS_BUCKET=mmm-app-output
export PROJECT_ID=datawarehouse-422511

python scripts/process_queue_simple.py --loop
```

## Expected Output

```
============================================================
MMM Queue Processor (Standalone)
============================================================
Queue: default-dev
Bucket: mmm-app-output
Project: datawarehouse-422511
Region: europe-west1
Training Job: mmm-app-dev-training
Mode: loop until empty
============================================================
2024-02-11 17:20:00,123 - INFO - Loaded queue 'default-dev' from GCS
2024-02-11 17:20:00,456 - INFO - 📊 Queue Status: default-dev
2024-02-11 17:20:00,457 - INFO -   Total: 12
2024-02-11 17:20:00,457 - INFO -   Pending: 12
2024-02-11 17:20:00,457 - INFO -   Running: 0
2024-02-11 17:20:00,789 - INFO - Processing job 1/12
2024-02-11 17:20:00,790 - INFO -   Country: de
2024-02-11 17:20:00,790 - INFO -   Revision: 20251211_115528
2024-02-11 17:20:01,234 - INFO - ✅ Launched job: mmm-app-dev-training
2024-02-11 17:20:01,235 - INFO - ✅ Job launched successfully
...
============================================================
✅ Processed 12 job(s)
============================================================
```

## Verification

After running, check that jobs are executing:

**Option 1 - Cloud Console:**
```
https://console.cloud.google.com/run/jobs?project=datawarehouse-422511
```

Look for active job executions.

**Option 2 - Command line:**
```bash
gcloud run jobs executions list \
  --job=mmm-app-dev-training \
  --region=europe-west1 \
  --limit=10
```

**Option 3 - Queue status:**
```bash
python scripts/process_queue_simple.py --count 0
```

This loads the queue and shows status without processing jobs.

## Why This Works

**No Dependencies:**
- ✅ Doesn't import from app modules
- ✅ No Streamlit decorators
- ✅ Pure Python + Google Cloud libraries
- ✅ Works immediately

**Self-Contained:**
- ✅ All queue logic inline
- ✅ All job launching inline
- ✅ All status management inline
- ✅ One file, no imports

## Troubleshooting

**"Authentication failed":**
```bash
gcloud auth application-default login
```

**"Permission denied":**
Make sure your user has:
- `roles/storage.objectAdmin` on GCS bucket
- `roles/run.developer` on Cloud Run Jobs

**"Job not found":**
Check the job name matches what's deployed:
```bash
gcloud run jobs list --region=europe-west1
```

Use `--training-job-name` flag if different.

## Next Steps

After jobs complete (15-30 minutes):
1. Check Cloud Console for completed executions
2. Verify results in GCS: `gs://mmm-app-output/robyn-results/`
3. Collect results:
   ```bash
   python scripts/benchmark_mmm.py --collect-results <benchmark_id>
   ```

## Summary

**Use this:**
```bash
python scripts/process_queue_simple.py --loop
```

**Not this:**
```bash
python scripts/process_queue_standalone.py --loop  # Has import errors!
```

The **simple** processor works immediately with no dependencies!
