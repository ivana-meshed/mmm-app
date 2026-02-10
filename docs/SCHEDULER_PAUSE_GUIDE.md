# Cloud Scheduler Pause Guide

## Overview

The Cloud Scheduler can be paused to help isolate cost factors and monitor the cost impact of different configuration changes independently.

## Why Pause the Scheduler?

The scheduler wakes up the Cloud Run web service every 10 minutes to check for queued training jobs. This results in:
- Continuous instance activity (144 wake-ups per day)
- Instance stays warm for ~15 minutes after each wake-up
- With 10-minute intervals, instances are almost always warm
- Prevents true scale-to-zero behavior

**Use Cases for Pausing:**
1. **Cost Monitoring**: Isolate the cost impact of CPU throttling vs. scheduler wake-ups
2. **A/B Testing**: Compare costs with and without scheduler
3. **Idle Periods**: When no training jobs are expected for extended periods
4. **Cost Optimization**: Verify baseline costs without scheduler activity

## How to Pause the Scheduler

### Option 1: Edit tfvars File (Recommended)

**For Production:**
```bash
cd infra/terraform
nano envs/prod.tfvars
```

Change:
```terraform
scheduler_enabled = true
```

To:
```terraform
scheduler_enabled = false
```

Then apply:
```bash
terraform apply -var-file=envs/prod.tfvars
```

**For Development:**
```bash
cd infra/terraform
nano envs/dev.tfvars
```

Change the same line and apply:
```bash
terraform apply -var-file=envs/dev.tfvars
```

### Option 2: Command-Line Override (Temporary)

For quick testing without editing files:

```bash
cd infra/terraform

# Pause scheduler
terraform apply -var-file=envs/prod.tfvars -var="scheduler_enabled=false"

# Resume scheduler
terraform apply -var-file=envs/prod.tfvars -var="scheduler_enabled=true"
```

## Impact of Pausing

### ✅ What Still Works
- Web UI access via browser
- Manual training job submission
- Manual job status checking
- Data upload and processing
- All interactive features

### ❌ What Stops Working
- **Automatic queue processing**: Training jobs won't start automatically
- **Queue advancement**: Jobs will remain in "queued" state
- **Scheduled job execution**: No periodic checks for new jobs

### 📊 Expected Cost Impact

With scheduler paused, you should see:
- ~20-30% reduction in idle costs (varies by usage pattern)
- Fewer compute hours per day
- More true "scale-to-zero" behavior

**Before (Scheduler Active):**
```
mmm-app-web: $1.92/day
  - compute_cpu: $1.31 (68.3%)
  - compute_memory: $0.58 (30.2%)
  - Instance hours: ~20-24 hours/day
```

**After (Scheduler Paused, no user traffic):**
```
mmm-app-web: $0.10/day
  - compute_cpu: $0.05 (50%)
  - compute_memory: $0.05 (50%)
  - Instance hours: ~1-2 hours/day (occasional checks)
```

## Cost Monitoring Experiment

### Step-by-Step A/B Test

**Phase 1: Baseline (1-2 days)**
1. Keep scheduler enabled
2. Run cost tracking:
   ```bash
   python scripts/track_daily_costs.py --days 2 --use-user-credentials
   ```
3. Note the daily costs with scheduler active

**Phase 2: Pause Scheduler (2-3 days)**
1. Pause scheduler via tfvars
2. Apply terraform changes
3. Monitor costs:
   ```bash
   python scripts/track_daily_costs.py --days 3 --use-user-credentials
   ```
4. Compare to baseline

**Phase 3: Analysis**
1. Run deep-dive analysis:
   ```bash
   python scripts/analyze_idle_costs.py --days 7 --use-user-credentials
   ```
2. Calculate actual cost reduction
3. Decide on optimal configuration

## Manual Job Processing

When scheduler is paused, you need to manually trigger queue processing:

### Option 1: Via Web UI
1. Go to the web UI
2. Navigate to "View Results" or "Run Experiment" page
3. The UI checks the queue on page load

### Option 2: Direct API Call
```bash
# Get your service URL
SERVICE_URL="https://mmm-app-web-<hash>-ew.a.run.app"

# Trigger queue check
curl "${SERVICE_URL}?queue_tick=1&name=default"
```

### Option 3: Manual Scheduler Creation

Create a one-time job in Cloud Scheduler:
```bash
gcloud scheduler jobs create http manual-queue-tick \
  --location=europe-west1 \
  --schedule="0 9 * * *" \
  --uri="${SERVICE_URL}?queue_tick=1&name=default" \
  --oidc-service-account-email=<scheduler-sa-email>
```

## Re-enabling the Scheduler

To resume automatic queue processing:

1. Edit the tfvars file:
   ```terraform
   scheduler_enabled = true
   ```

2. Apply changes:
   ```bash
   cd infra/terraform
   terraform apply -var-file=envs/prod.tfvars
   ```

3. Verify scheduler is running:
   ```bash
   gcloud scheduler jobs list --location=europe-west1
   ```

## Best Practices

### ✅ Do
- Pause during extended idle periods (weekends, holidays)
- Monitor costs for at least 24-48 hours after pausing
- Document baseline costs before making changes
- Test in dev environment first
- Communicate with team about manual job processing

### ❌ Don't
- Pause if you need automatic job processing
- Forget to re-enable if expecting queued jobs
- Pause without monitoring impact
- Make multiple config changes simultaneously (hard to isolate cause)

## Troubleshooting

### Scheduler Still Shows in Console After Pausing
- Terraform may take 1-2 minutes to delete the job
- Refresh the Cloud Scheduler page
- Check terraform state: `terraform state list | grep scheduler`

### Jobs Not Processing After Re-enabling
- Wait 10 minutes for first scheduler tick
- Check scheduler job status in GCP console
- Verify service account permissions
- Check Cloud Run logs for errors

### Cost Not Reducing as Expected
- Check for other services causing wake-ups (load balancers, health checks)
- Verify no user traffic during monitoring period
- Run debug analysis:
  ```bash
  python scripts/track_daily_costs.py --days 1 --use-user-credentials --debug
  ```

## Related Documentation

- [Idle Cost Analysis](./IDLE_COST_ANALYSIS.md) - Detailed cost breakdown
- [Executive Summary](./IDLE_COST_EXECUTIVE_SUMMARY.md) - High-level cost overview
- [Cost Tracking README](../scripts/COST_TRACKING_README.md) - Cost monitoring tools

## Example Workflow

```bash
# 1. Check current costs
python scripts/track_daily_costs.py --days 7 --use-user-credentials

# 2. Pause scheduler to test
cd infra/terraform
nano envs/prod.tfvars  # Set scheduler_enabled = false
terraform apply -var-file=envs/prod.tfvars

# 3. Monitor for 48 hours
sleep 172800  # Or just wait...

# 4. Check new costs
python scripts/track_daily_costs.py --days 2 --use-user-credentials

# 5. Compare and decide
python scripts/analyze_idle_costs.py --days 7 --use-user-credentials

# 6. Re-enable if needed
nano envs/prod.tfvars  # Set scheduler_enabled = true
terraform apply -var-file=envs/prod.tfvars
```

## Summary

Pausing the scheduler is a powerful tool for:
- 📊 Understanding your cost structure
- 🧪 A/B testing configuration changes
- 💰 Reducing costs during idle periods
- 🔍 Isolating cost factors

Use it wisely as part of your cost optimization strategy!
