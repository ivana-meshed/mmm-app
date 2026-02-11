# Queue Processing for Benchmarks

## Problem

Benchmark jobs are submitted to the queue but never execute because the Cloud Scheduler is disabled in the development environment.

## How Queue Processing Works

```
1. Submit Jobs → Queue (PENDING status)
2. Cloud Scheduler triggers queue tick every 10 minutes
3. Queue tick processes one job: PENDING → LAUNCHING → RUNNING → SUCCEEDED
4. Repeat step 3 for remaining jobs
```

**Issue**: Step 2 doesn't happen when scheduler is disabled!

## Solutions

### Option 1: Enable Cloud Scheduler (Production)

**Best for**: Production environments with continuous job processing needs.

**Steps**:
1. Edit `infra/terraform/envs/dev.tfvars` (or `prod.tfvars`):
   ```terraform
   scheduler_enabled = true  # Change from false to true
   ```

2. Apply terraform changes:
   ```bash
   cd infra/terraform
   terraform apply -var-file=envs/dev.tfvars
   ```

**Cost**: ~$0.10 per day (Cloud Scheduler pricing)

**Pros**:
- Automatic processing every 10 minutes
- No manual intervention needed
- Works as designed

**Cons**:
- Adds ongoing cost
- Overkill for occasional development testing

### Option 2: Manual Queue Trigger (Development)

**Best for**: Development/testing where jobs are submitted occasionally.

**Method 1 - Automatic trigger with benchmark submission**:
```bash
# Submit benchmark and immediately trigger queue processing
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue
```

This will:
1. Submit all benchmark variants to queue
2. Automatically trigger queue ticks to process them
3. Process continues until all jobs are launched

**Method 2 - Manual trigger after submission**:
```bash
# 1. Submit benchmark normally
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json

# 2. Manually trigger queue processing
python scripts/trigger_queue.py --until-empty
```

**Method 3 - Check status and trigger selectively**:
```bash
# Check queue status
python scripts/trigger_queue.py --status-only

# Trigger specific number of jobs
python scripts/trigger_queue.py --count 3

# Process everything pending
python scripts/trigger_queue.py --until-empty
```

## Using trigger_queue.py

### Basic Usage

```bash
# Process one job
python scripts/trigger_queue.py

# Process 5 jobs
python scripts/trigger_queue.py --count 5

# Process all pending jobs
python scripts/trigger_queue.py --until-empty

# Check status without processing
python scripts/trigger_queue.py --status-only
```

### Advanced Options

```bash
# Use specific queue
python scripts/trigger_queue.py --queue-name default-dev

# Adjust delay between ticks (default: 5 seconds)
python scripts/trigger_queue.py --until-empty --delay 10

# Process with custom settings
python scripts/trigger_queue.py \
  --queue-name default-dev \
  --count 3 \
  --delay 15
```

### What It Does

1. **Checks queue status**: Shows pending/running/completed counts
2. **Gets Cloud Run service URL**: Finds your deployed service
3. **Calls queue tick endpoint**: `?queue_tick=1&name={queue_name}`
4. **Authenticates**: Uses Application Default Credentials
5. **Processes jobs**: Each tick processes one job
6. **Shows progress**: Reports success/failure for each tick

## Requirements

### Environment Variables

The scripts need these environment variables (or use defaults):

```bash
export PROJECT_ID=datawarehouse-422511
export REGION=europe-west1
export GCS_BUCKET=mmm-app-output
export QUEUE_ROOT=robyn-queues
```

### Authentication

```bash
# For local development
gcloud auth application-default login

# For Cloud Run/GCE (automatic)
# Uses service account credentials
```

### Python Dependencies

```bash
pip install google-cloud-storage google-cloud-run requests google-auth
```

## Workflow Examples

### Example 1: Quick Development Test

```bash
# Submit and immediately process
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue

# That's it! Jobs will be launched automatically
```

### Example 2: Batch Testing

```bash
# Submit multiple benchmarks
python scripts/benchmark_mmm.py --config benchmarks/test1.json
python scripts/benchmark_mmm.py --config benchmarks/test2.json
python scripts/benchmark_mmm.py --config benchmarks/test3.json

# Process all at once
python scripts/trigger_queue.py --until-empty
```

### Example 3: Controlled Processing

```bash
# Submit benchmark
python scripts/benchmark_mmm.py --config benchmarks/my_test.json

# Check what's queued
python scripts/trigger_queue.py --status-only

# Process jobs one at a time with monitoring
python scripts/trigger_queue.py --count 1
# ... check Streamlit for job status ...
python scripts/trigger_queue.py --count 1
# ... repeat as needed ...
```

## Monitoring

### Check Queue Status

```bash
# Quick status check
python scripts/trigger_queue.py --status-only
```

Output:
```
📊 Queue Status: default
  Total jobs: 5
  Pending: 3
  Running: 1
  Completed: 1
  Queue running: true

  Status breakdown:
    PENDING: 3
    RUNNING: 1
    SUCCEEDED: 1
```

### Monitor in Streamlit

Navigate to: **Run Experiment → Queue Monitor**

Shows:
- All jobs in queue
- Current status
- Execution details
- Logs and errors

### Check Cloud Logging

```bash
# View queue tick logs
gcloud logging read "resource.type=cloud_run_revision AND textPayload=~QUEUE" --limit 50
```

## Troubleshooting

### "Queue does not exist"

**Problem**: No jobs have been submitted yet.

**Solution**: Submit a benchmark first:
```bash
python scripts/benchmark_mmm.py --config benchmarks/your_config.json
```

### "Queue is paused (queue_running=false)"

**Problem**: Queue has been manually paused.

**Solution**: Resume queue via Streamlit UI or manually edit queue JSON in GCS.

### "Failed to get service URL"

**Problem**: Can't find Cloud Run service.

**Solutions**:
1. Set environment variable:
   ```bash
   export WEB_SERVICE_URL=https://your-service-url.run.app
   ```

2. Ensure you have correct project/region:
   ```bash
   export PROJECT_ID=datawarehouse-422511
   export REGION=europe-west1
   ```

3. Check service name matches (dev: `mmm-app-dev`, prod: `mmm-app`)

### "No pending jobs to process"

**Problem**: Queue is empty or all jobs are complete.

**Check**: 
```bash
python scripts/trigger_queue.py --status-only
```

If jobs are RUNNING, wait for them to complete. If PENDING, try triggering again.

### Jobs stay in PENDING

**Possible causes**:
1. Scheduler disabled and no manual trigger
2. Queue paused (`queue_running: false`)
3. Missing `data_gcs_path` in job params (fixed in recent commits)

**Solutions**:
1. Use `--trigger-queue` or manual trigger
2. Check queue status and resume if needed
3. Resubmit with latest script version

## Comparison: Scheduler vs Manual

| Aspect | Cloud Scheduler | Manual Trigger |
|--------|----------------|----------------|
| **Setup** | One-time terraform apply | No setup needed |
| **Cost** | ~$0.10/day | Free |
| **Automation** | Fully automatic (every 10 min) | Manual command |
| **Use Case** | Production, continuous use | Development, occasional use |
| **Latency** | Up to 10 minutes | Immediate |
| **Maintenance** | None | Run command each time |

## Recommendations

### For Production
✅ **Enable Cloud Scheduler**
- Set `scheduler_enabled = true`
- Apply terraform
- Forget about it - works automatically

### For Development
✅ **Use Manual Trigger**
- Keep scheduler disabled (save costs)
- Use `--trigger-queue` flag when submitting
- Or run `trigger_queue.py` when convenient

### For Testing
✅ **Mixed Approach**
- Submit multiple benchmarks
- Batch process with `--until-empty`
- Monitor in Streamlit

## Summary

**Quick Fix for Current Issue**:
```bash
# If jobs are already stuck in queue:
python scripts/trigger_queue.py --until-empty

# For new benchmarks:
python scripts/benchmark_mmm.py \
  --config benchmarks/your_config.json \
  --trigger-queue
```

**Long-term Solution**:
- Development: Keep using manual trigger
- Production: Enable Cloud Scheduler

Both approaches work correctly with the fixed benchmark script!
