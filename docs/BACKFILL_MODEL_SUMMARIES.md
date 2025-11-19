# Backfilling Model Summaries

## Overview

This guide explains how to generate `model_summary.json` files for existing Robyn model runs that don't have them yet.

## Background

- **New runs** (after PR #82): Automatically generate `model_summary.json` during training
- **Existing runs** (before PR #82): Need to be backfilled using the backfill process

## Automatic Backfilling (CI/CD)

**The backfill process runs automatically during each deployment** - no manual action required!

### Production (main branch)
- Triggered on: Push to `main` branch
- Job: `mmm-app-training`
- Timeout: 6 hours (sufficient for large datasets)

### Development (feature branches)
- Triggered on: Push to `feat-*`, `copilot/*`, `dev` branches  
- Job: `mmm-app-dev-training`
- Timeout: 6 hours (sufficient for large datasets)

### How it works
1. After deployment completes, CI/CD executes the training container as a one-time job
2. Runs `backfill_summaries.R` which calls Python aggregation script
3. Step 1: Generates missing summaries (scans all runs in GCS)
4. Step 2: Aggregates by country into `model_summary/{country}/`
5. **The deployment will wait for backfill to complete before finishing**

### Checking if backfill ran
```bash
# View recent Cloud Run job executions
gcloud run jobs executions list \
  --job mmm-app-training \
  --region europe-west1 \
  --limit 5

# View logs from the latest execution
gcloud logging read \
  "resource.type=cloud_run_job AND resource.labels.job_name=mmm-app-training" \
  --limit 100 \
  --format json
```

## Manual Backfilling (Optional)

Manual backfilling is only needed in rare cases (e.g., debugging, testing, or if CI/CD failed).

### Testing a Specific Run

To test if summary generation works for a specific run:

```bash
# Test a specific run path
python scripts/aggregate_model_summaries.py \
  --bucket mmm-app-output \
  --test-run robyn/r100/de/1104_082103

# This will:
# 1. Check if the run path format is valid
# 2. Check if OutputCollect.RDS exists
# 3. Check if model_summary.json already exists
# 4. Attempt to generate the summary if missing
# 5. Aggregate summaries for the country (creates model_summary/{country}/summary.json)
```

Example output:
```
============================================================
TEST MODE: Testing run robyn/r100/de/1104_082103
============================================================
✓ Run path format is valid
  Revision: r100
  Country: de
  Timestamp: 1104_082103
✓ OutputCollect.RDS found at robyn/r100/de/1104_082103/OutputCollect.RDS
○ model_summary.json does NOT exist at robyn/r100/de/1104_082103/model_summary.json
  Will attempt to generate...
✅ SUCCESS: Summary generated at robyn/r100/de/1104_082103/model_summary.json

Aggregating summaries for country: de
✅ Country summary aggregated at model_summary/de/summary.json
============================================================
```

### Option 1: Using the convenience script

```bash
# From repository root
./scripts/manual_backfill.sh
```

This script will:
- Validate your gcloud authentication
- Execute the backfill job
- Show progress and results
- Provide troubleshooting help if it fails

### Option 2: Direct gcloud command

```bash
gcloud run jobs execute mmm-app-training \
  --region europe-west1 \
  --task-timeout 3600 \
  --args="Rscript,/app/backfill_summaries.R,--bucket,mmm-app-output,--project,datawarehouse-422511" \
  --wait
```

### Option 3: Filter by country or revision

```bash
# Backfill only for a specific country
gcloud run jobs execute mmm-app-training \
  --region europe-west1 \
  --args="Rscript,/app/backfill_summaries.R,--bucket,mmm-app-output,--country,US" \
  --wait

# Backfill only for a specific revision
gcloud run jobs execute mmm-app-training \
  --region europe-west1 \
  --args="Rscript,/app/backfill_summaries.R,--bucket,mmm-app-output,--revision,v1" \
  --wait
```

## Verifying Results

### Check individual summaries

```bash
# List all model_summary.json files
gsutil ls -r gs://mmm-app-output/robyn/**/model_summary.json | wc -l

# Check a specific run
gsutil cat gs://mmm-app-output/robyn/v1/US/1234567890/model_summary.json | jq .
```

### Check aggregated summaries

```bash
# List all country summaries
gsutil ls gs://mmm-app-output/model_summary/*/summary.json

# View a specific country summary
gsutil cat gs://mmm-app-output/model_summary/US/summary.json | jq .
```

### Count runs with/without summaries

```python
# Run this in Python with google-cloud-storage installed
from google.cloud import storage

client = storage.Client()
bucket = client.bucket("mmm-app-output")

# List all OutputCollect.RDS files (indicates a run exists)
runs = []
for blob in bucket.list_blobs(prefix="robyn/"):
    if blob.name.endswith("OutputCollect.RDS"):
        run_path = "/".join(blob.name.split("/")[:4])
        runs.append(run_path)

# Check which have summaries
with_summary = 0
without_summary = 0

for run_path in runs:
    summary_blob = bucket.blob(f"{run_path}/model_summary.json")
    if summary_blob.exists():
        with_summary += 1
    else:
        without_summary += 1
        print(f"Missing: {run_path}")

print(f"\nTotal runs: {len(runs)}")
print(f"With summary: {with_summary}")
print(f"Without summary: {without_summary}")
print(f"Coverage: {100 * with_summary / len(runs):.1f}%")
```

## Troubleshooting

### Issue: Backfill job times out

**Cause**: Too many runs to process in 1 hour

**Solution**: Run backfill for specific countries or revisions
```bash
# Process one country at a time
for country in US UK DE FR; do
  gcloud run jobs execute mmm-app-training \
    --region europe-west1 \
    --args="Rscript,/app/backfill_summaries.R,--bucket,mmm-app-output,--country,$country" \
    --wait
done
```

### Issue: Backfill succeeds but summaries not created

**Possible causes**:
1. RDS files are corrupted or missing
2. R script errors not being logged
3. GCS permission issues

**Debug steps**:
```bash
# 1. Check Cloud Run logs
gcloud logging read \
  "resource.type=cloud_run_job AND resource.labels.job_name=mmm-app-training" \
  --limit 200 \
  --format json > backfill_logs.json

# 2. Look for errors
cat backfill_logs.json | jq -r '.[] | select(.severity=="ERROR") | .textPayload'

# 3. Check service account permissions
gcloud projects get-iam-policy datawarehouse-422511 \
  --flatten="bindings[].members" \
  --filter="bindings.members:*mmm-app-training*"
```

### Issue: "No module named 'google'" error

**Cause**: Python dependencies not installed in training container

**Solution**: Ensure requirements.txt includes google-cloud-storage and container is rebuilt

### Issue: Aggregation step fails

**Cause**: No summaries to aggregate, or GCS write permission issues

**Check**:
```bash
# Verify summaries were created
gsutil ls -r gs://mmm-app-output/robyn/**/model_summary.json | head -10

# Try aggregation manually (requires google-cloud-storage)
python scripts/aggregate_model_summaries.py \
  --bucket mmm-app-output \
  --aggregate
```

## Expected Timeline

For a typical repository with 100 runs:
- **Backfill generation**: ~5-10 minutes per run = 8-16 hours total
- **Aggregation**: ~10-30 seconds per country

**Recommendation**: Run backfill during off-hours or in batches by country.

## Post-Backfill Verification

After backfill completes:

1. **Check coverage**:
   ```bash
   # Should show high percentage
   gsutil ls -r gs://mmm-app-output/robyn/**/model_summary.json | wc -l
   gsutil ls -r gs://mmm-app-output/robyn/**/OutputCollect.RDS | wc -l
   ```

2. **Verify aggregated summaries**:
   ```bash
   # Should have one file per country
   gsutil ls gs://mmm-app-output/model_summary/
   ```

3. **Spot check a few summaries**:
   ```bash
   gsutil cat gs://mmm-app-output/robyn/v1/US/1234567890/model_summary.json | jq '.best_model'
   ```

## Maintenance

- **New runs**: Automatically create summaries (no action needed)
- **Failed backfill**: Re-run manually (idempotent - won't duplicate)
- **Re-aggregation**: Can be run anytime to update country summaries

```bash
# Re-aggregate without generating new summaries
python scripts/aggregate_model_summaries.py \
  --bucket mmm-app-output \
  --aggregate
```

## Support

If backfill continues to fail:
1. Check [docs/MODEL_SUMMARY.md](MODEL_SUMMARY.md) for feature documentation
2. Review logs from Cloud Run job executions
3. Verify GCS bucket permissions and structure
4. Open a GitHub issue with error logs
