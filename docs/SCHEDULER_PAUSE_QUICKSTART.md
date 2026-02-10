# Quick Start: Pause Scheduler for Cost Monitoring

## Immediate Steps to Pause Scheduler

### For Production Environment

1. **Edit the configuration file:**
   ```bash
   cd infra/terraform
   nano envs/prod.tfvars
   ```

2. **Change this line:**
   ```terraform
   scheduler_enabled = true
   ```
   
   **To:**
   ```terraform
   scheduler_enabled = false
   ```

3. **Apply the change:**
   ```bash
   terraform apply -var-file=envs/prod.tfvars
   ```

4. **Confirm the change:**
   - Type `yes` when prompted
   - Wait ~30 seconds for scheduler to be deleted

### For Development Environment

Same steps but use `envs/dev.tfvars` instead.

## Verify Scheduler is Paused

```bash
# Check if scheduler exists
gcloud scheduler jobs list --location=europe-west1

# Should show no job named "robyn-queue-tick" (prod) or "robyn-queue-tick-dev" (dev)
```

## Monitor Costs

```bash
# Track costs over the next 24-48 hours
python scripts/track_daily_costs.py --days 2 --use-user-credentials

# Compare with previous costs
python scripts/analyze_idle_costs.py --days 7 --use-user-credentials
```

## Expected Results

### Before (Scheduler Active)
```
mmm-app-web: $1.92/day
  - compute_cpu: $1.31 (68.3%)
  - compute_memory: $0.58 (30.2%)
  - Instance active: ~20-24 hours/day
```

### After (Scheduler Paused, No User Traffic)
```
mmm-app-web: $0.10-0.30/day
  - compute_cpu: $0.05-0.15 (50%)
  - compute_memory: $0.05-0.15 (50%)
  - Instance active: ~1-4 hours/day
```

### Cost Reduction
- **Expected savings**: 80-90% when idle
- **Monthly impact**: ~$30-50/month per service
- **Annual impact**: ~$360-600/year per service

## Re-enable Scheduler

When ready to resume automatic queue processing:

1. **Edit the configuration file:**
   ```bash
   nano envs/prod.tfvars
   ```

2. **Change back:**
   ```terraform
   scheduler_enabled = true
   ```

3. **Apply:**
   ```bash
   terraform apply -var-file=envs/prod.tfvars
   ```

## What to Watch For

### ✅ Normal Behavior When Paused
- Web UI still accessible
- Manual job submission works
- Jobs stay in "queued" state
- No automatic job processing

### ⚠️ Action Required
- **Manual queue processing**: Jobs won't start automatically
- **Check queue periodically**: Visit UI or trigger manually
- **Document baseline**: Track costs before AND after pausing

### 🚫 Issues to Report
- Web UI becomes inaccessible
- Errors when submitting jobs
- Costs don't decrease after 24 hours
- Any unexpected behavior

## Monitoring Schedule

| Day | Action | Command |
|-----|--------|---------|
| Day 0 | Baseline measurement | `track_daily_costs.py --days 1` |
| Day 1 | Pause scheduler | `terraform apply -var="scheduler_enabled=false"` |
| Day 2 | Check costs (1 day paused) | `track_daily_costs.py --days 2` |
| Day 3 | Check costs (2 days paused) | `track_daily_costs.py --days 3` |
| Day 4 | Final analysis | `analyze_idle_costs.py --days 7` |
| Day 5 | Resume scheduler | `terraform apply -var="scheduler_enabled=true"` |

## Quick Reference Commands

```bash
# Check current costs
python scripts/track_daily_costs.py --days 1 --use-user-credentials

# Pause scheduler (from terraform directory)
terraform apply -var-file=envs/prod.tfvars -var="scheduler_enabled=false"

# Check scheduler status
gcloud scheduler jobs list --location=europe-west1

# Resume scheduler
terraform apply -var-file=envs/prod.tfvars -var="scheduler_enabled=true"

# Deep analysis
python scripts/analyze_idle_costs.py --days 7 --use-user-credentials

# Debug mode (if issues)
python scripts/track_daily_costs.py --days 1 --use-user-credentials --debug
```

## Get Help

- **Scheduler Pause Guide**: `docs/SCHEDULER_PAUSE_GUIDE.md` (detailed guide)
- **Idle Cost Analysis**: `docs/IDLE_COST_ANALYSIS.md` (technical deep-dive)
- **Executive Summary**: `docs/IDLE_COST_EXECUTIVE_SUMMARY.md` (overview)

## Summary

**In 3 Steps:**
1. Edit `envs/prod.tfvars`: Set `scheduler_enabled = false`
2. Run `terraform apply -var-file=envs/prod.tfvars`
3. Monitor with `track_daily_costs.py` for 24-48 hours

**Expected Outcome:**
- 80-90% cost reduction during idle periods
- Isolates scheduler cost impact from CPU throttling
- Enables data-driven optimization decisions

**Time to Complete:**
- Setup: 5 minutes
- Monitoring: 24-48 hours
- Total: 2-3 days for complete analysis
