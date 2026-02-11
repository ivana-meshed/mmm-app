# Benchmark Queue Execution - COMPLETE SOLUTION

## TL;DR - Quick Fix

Your benchmark jobs aren't executing because the Cloud Scheduler is disabled. Here's the fix:

```bash
# For your current stuck jobs:
python scripts/trigger_queue.py --until-empty

# For new benchmarks:
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue
```

Done! Jobs will process immediately.

## What Was Wrong

### Issue #1: Missing data_gcs_path ✅ FIXED
- **Problem**: Benchmark script didn't include `data_gcs_path` field
- **Impact**: Queue processor couldn't find training data
- **Fixed**: Commit `f569e61` - Script now constructs path from `data_version`

### Issue #2: Scheduler Disabled ✅ FIXED
- **Problem**: Cloud Scheduler disabled in dev environment
- **Impact**: Queue tick never called → jobs stay PENDING forever
- **Fixed**: Commit `381080d` - Added manual trigger capability

## Complete Solution

### For Development (Recommended)

Keep scheduler disabled (save costs) and use manual trigger:

**Option A - Auto-trigger with submission:**
```bash
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json \
  --trigger-queue
```

**Option B - Manual trigger after submission:**
```bash
# Submit
python scripts/benchmark_mmm.py \
  --config benchmarks/adstock_comparison.json

# Trigger
python scripts/trigger_queue.py --until-empty
```

### For Production

Enable Cloud Scheduler for automatic processing:

1. Edit `infra/terraform/envs/prod.tfvars`:
   ```terraform
   scheduler_enabled = true
   ```

2. Apply terraform:
   ```bash
   cd infra/terraform
   terraform apply -var-file=envs/prod.tfvars
   ```

Jobs will then process automatically every 10 minutes.

## New Tools

### 1. trigger_queue.py

Manual queue processing script with multiple modes:

```bash
# Process all pending jobs
python scripts/trigger_queue.py --until-empty

# Process 3 jobs
python scripts/trigger_queue.py --count 3

# Check status without processing
python scripts/trigger_queue.py --status-only

# Use specific queue
python scripts/trigger_queue.py --queue-name default-dev --until-empty
```

### 2. Enhanced benchmark_mmm.py

Now includes `--trigger-queue` flag:

```bash
# Old way (jobs stay pending)
python scripts/benchmark_mmm.py --config mytest.json

# New way (jobs process immediately)
python scripts/benchmark_mmm.py --config mytest.json --trigger-queue
```

## How Queue Processing Works

### With Scheduler Enabled (Production)
```
1. Submit jobs → Queue (PENDING)
2. Scheduler calls ?queue_tick=1 every 10 minutes
3. Queue tick processes one job
4. Job: PENDING → LAUNCHING → RUNNING → SUCCEEDED
5. Repeat for remaining jobs
```

### With Manual Trigger (Development)
```
1. Submit jobs → Queue (PENDING)
2. Run: python scripts/trigger_queue.py --until-empty
3. Script calls ?queue_tick=1 repeatedly
4. Jobs process immediately
```

Both use the exact same queue processing logic!

## Detailed Documentation

See these files for complete information:

- **QUEUE_PROCESSING_GUIDE.md** - Comprehensive guide with all options
- **FIXING_STUCK_BENCHMARK_QUEUE.md** - Quick fix reference
- **FEATURE_BENCHMARKING.md** - Benchmarking feature overview

## Troubleshooting

### Jobs still not processing?

1. **Check queue status:**
   ```bash
   python scripts/trigger_queue.py --status-only
   ```

2. **Verify jobs are in queue:**
   - Should show pending jobs
   - Queue should be running (`queue_running: true`)

3. **Check for errors:**
   - Look at script output
   - Check Cloud Logging for queue tick errors

### Common Issues

**"No pending jobs"**
- All jobs already processed or none submitted
- Check status to see if jobs completed

**"Queue is paused"**
- Queue has `queue_running: false`
- Resume via Streamlit UI or edit queue JSON

**"Failed to get service URL"**
- Set `WEB_SERVICE_URL` environment variable
- Or ensure correct `PROJECT_ID` and `REGION`

## Architecture Decisions

### Why Manual Trigger?

✅ **Pros:**
- Zero ongoing cost (scheduler costs ~$0.10/day)
- Immediate execution (no waiting for 10-min scheduler tick)
- Full control over when jobs run
- Perfect for dev/testing workflow

❌ **Cons:**
- Requires manual command
- Not suitable for automated/continuous workflows

### Why Not Change Architecture?

The current queue + scheduler architecture is solid:
- ✅ Handles concurrent access safely
- ✅ Prevents duplicate job execution
- ✅ Tracks job state properly
- ✅ Integrates with UI monitoring
- ✅ Production-ready with scheduler enabled

Manual trigger is just an additional option, not a replacement!

## Cost Comparison

| Approach | Daily Cost | Monthly Cost | Use Case |
|----------|-----------|--------------|----------|
| Manual Trigger | $0 | $0 | Dev/testing |
| Cloud Scheduler | ~$0.10 | ~$3 | Production |

For occasional development use, manual trigger saves ~$3/month while providing better control.

## Next Steps

### Immediate Action

Process your stuck jobs:
```bash
python scripts/trigger_queue.py --until-empty
```

### Going Forward

For development:
```bash
# Always use --trigger-queue flag
python scripts/benchmark_mmm.py \
  --config benchmarks/your_test.json \
  --trigger-queue
```

For production:
```bash
# Enable scheduler once
cd infra/terraform
terraform apply -var-file=envs/prod.tfvars

# Then just submit normally
python scripts/benchmark_mmm.py --config benchmarks/your_test.json
```

## Summary

Two fixes were needed:
1. ✅ Add `data_gcs_path` to job params (f569e61)
2. ✅ Add manual queue trigger (381080d)

Your benchmarks will now work correctly with either:
- Manual trigger (dev) - immediate, free
- Cloud Scheduler (prod) - automatic, $0.10/day

Both approaches work perfectly with the fixed benchmark script!

## Questions?

- See QUEUE_PROCESSING_GUIDE.md for detailed usage
- See FIXING_STUCK_BENCHMARK_QUEUE.md for quick reference
- Check Cloud Logging for execution details
