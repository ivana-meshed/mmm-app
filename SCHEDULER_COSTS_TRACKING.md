# Scheduler and Automation Costs Tracking

## Overview

The cost tracking scripts have been enhanced to explicitly capture and report scheduler run costs and GitHub Actions automation costs.

## What's New

### 1. Enhanced Cost Categories

**New tracked categories:**
- `scheduler_service` - Base Cloud Scheduler service fee (~$0.10/month per job)
- `scheduler_requests` - Cloud Run invocations triggered by scheduler
- `github_actions` - CI/CD automation costs (Cloud Build)

### 2. New Output Section

The scripts now include a dedicated "Scheduler & Automation Costs Breakdown" section:

```
================================================================================
Scheduler & Automation Costs Breakdown
================================================================================

Total Scheduler & Automation Costs: $X.XX
Monthly Projection: $X.XX

Breakdown:
  - Scheduler Service Fee: $X.XX
    (Base Cloud Scheduler service charge, ~$0.10/month per job)
  - Scheduler Invocations: $X.XX
    (Cloud Run container time for queue processing)
  - GitHub Actions (CI/CD): $X.XX
    (Artifact Registry cleanup and other automation)

Notes:
  - Scheduler runs every 10 minutes (4,320 invocations/month)
  - Artifact cleanup runs weekly via GitHub Actions
  - These are automated operational costs
================================================================================
```

## Usage

Run the cost tracking script as usual:

```bash
# Track last 7 days
python scripts/track_daily_costs.py --days 7 --use-user-credentials

# Track last 30 days with output file
python scripts/track_daily_costs.py --days 30 --output costs.csv --use-user-credentials

# Analyze idle costs
python scripts/analyze_idle_costs.py --days 7 --use-user-credentials
```

The new breakdown will automatically appear at the end of the output.

## What Costs Are Captured

### Scheduler Costs

**Cloud Scheduler Service:**
- Base service fee: $0.10/month per job
- We have 2 jobs (prod + dev): ~$0.20/month
- Appears in BigQuery as "Cloud Scheduler" service

**Scheduler Invocations:**
- Runs every 10 minutes
- 4,320 invocations/month (144 per day)
- Each invocation triggers Cloud Run container
- Average 5 seconds per invocation
- Cost includes CPU + memory + request fees
- Typically ~$0.50-1.00/month

### GitHub Actions Costs

**Weekly Artifact Registry Cleanup:**
- Runs via GitHub Actions workflow
- Uses Cloud Build service
- Workflow: `.github/workflows/cost-optimization.yml`
- Runs weekly on Sundays at 2 AM UTC
- Typically very low cost (<$0.10/month)

**CI/CD Deployments:**
- Container image builds
- Terraform deployments
- Appears as Cloud Build costs in billing

## Technical Details

### BigQuery Query Enhancements

The scripts now include these additional filters:

```sql
-- Cloud Build (for GitHub Actions)
OR service.description LIKE '%Cloud Build%'
OR sku.description LIKE '%Cloud Build%'
OR resource.name LIKE '%github%'
```

### Cost Categorization Logic

```python
# Cloud Build costs (GitHub Actions workflows)
if "build" in sku_lower or "cloud build" in sku_lower:
    return "github_actions"
```

## Expected Monthly Costs

Based on current configuration:

| Cost Category | Monthly Estimate | Notes |
|---------------|-----------------|-------|
| Scheduler Service Fee | $0.20 | 2 jobs × $0.10/month |
| Scheduler Invocations | $0.50-1.00 | 4,320 invocations/month |
| GitHub Actions | $0.05-0.20 | Weekly cleanup + CI/CD |
| **Total Automation** | **$0.75-1.40** | Fixed operational costs |

## Troubleshooting

### "No scheduler or automation costs found"

If you see this message:
- The billing period may be too short (try 7-30 days)
- Scheduler may not have run during the period
- Base service fees appear at month-end in billing

### Cloud Build costs not showing

- GitHub Actions costs may be very small (<$0.01)
- Check longer time periods (30 days)
- Ensure workflows have actually run during the period

## Benefits

1. **Better Visibility** - Clear breakdown of automation costs
2. **Cost Attribution** - Know exactly what schedulers and workflows cost
3. **Optimization Insights** - Identify if automation costs are higher than expected
4. **Budget Planning** - Accurate fixed cost estimates

## Related Documentation

- [COST_STATUS.md](COST_STATUS.md) - Current cost status and optimization
- [COST_OPTIMIZATION.md](COST_OPTIMIZATION.md) - Detailed cost analysis
- [.github/workflows/cost-optimization.yml](.github/workflows/cost-optimization.yml) - Cleanup workflow
- [docs/SCHEDULER_PAUSE_GUIDE.md](docs/SCHEDULER_PAUSE_GUIDE.md) - How to pause scheduler
