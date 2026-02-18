# Example Output: Scheduler & Automation Costs Tracking

This document shows an example of what the new cost tracking output looks like with the scheduler and automation costs breakdown.

## Sample Output

```
================================================================================
Daily Google Cloud Services Cost Report (7 days)
================================================================================

Date: 2026-02-18
--------------------------------------------------------------------------------
  mmm-app-dev-training: $0.14
    - compute_cpu: $0.09
    - compute_memory: $0.04
    - registry: $0.01
  mmm-app-dev-web: $0.15
    - user_requests: $0.13
    - registry: $0.01
    - scheduler_requests: $0.01
  mmm-app-training: $0.01
    - registry: $0.01
  mmm-app-web: $0.02
    - registry: $0.01
    - scheduler_requests: $0.01
  Daily Total: $0.32

Date: 2026-02-17
--------------------------------------------------------------------------------
  mmm-app-dev-training: $0.12
    - compute_cpu: $0.08
    - compute_memory: $0.03
    - registry: $0.01
  mmm-app-dev-web: $0.14
    - user_requests: $0.12
    - registry: $0.01
    - scheduler_requests: $0.01
  mmm-app-training: $0.01
    - registry: $0.01
  mmm-app-web: $0.02
    - registry: $0.01
    - scheduler_requests: $0.01
  Daily Total: $0.29

... (additional days)

================================================================================
Summary by Service
================================================================================

mmm-app-dev-training: $0.56
  - compute_cpu: $0.36 (64.3%)
  - compute_memory: $0.16 (28.6%)
  - registry: $0.04 (7.1%)

mmm-app-dev-web: $0.78
  - user_requests: $0.65 (83.3%)
  - scheduler_requests: $0.08 (10.3%)
  - registry: $0.05 (6.4%)

mmm-app-training: $0.05
  - registry: $0.05 (100.0%)

mmm-app-web: $0.12
  - registry: $0.07 (58.3%)
  - scheduler_requests: $0.05 (41.7%)

================================================================================
Grand Total: $1.51
Daily Average: $0.22
Monthly Projection: $6.47
================================================================================

================================================================================
Scheduler & Automation Costs Breakdown
================================================================================

Total Scheduler & Automation Costs: $0.15
Monthly Projection: $0.64

Breakdown:
  - Scheduler Service Fee: $0.02
    (Base Cloud Scheduler service charge, ~$0.10/month per job)
  - Scheduler Invocations: $0.13
    (Cloud Run container time for queue processing)
  - GitHub Actions (CI/CD): $0.00
    (Artifact Registry cleanup and other automation)

Notes:
  - Scheduler runs every 10 minutes (4,320 invocations/month)
  - Artifact cleanup runs weekly via GitHub Actions
  - These are automated operational costs
================================================================================
```

## Explanation of the New Section

### What It Shows

The **"Scheduler & Automation Costs Breakdown"** section aggregates all automation-related costs:

1. **Scheduler Service Fee** ($0.02 in example)
   - Base Cloud Scheduler service charge
   - Approximately $0.10/month per job
   - We have 2 jobs (prod + dev): ~$0.20/month
   - Pro-rated in the example (7 days = ~$0.05 total, split between services)

2. **Scheduler Invocations** ($0.13 in example)
   - Cloud Run container time for processing queue ticks
   - Runs every 10 minutes (144 times per day)
   - Each invocation takes ~5 seconds
   - Costs include CPU + memory + request fees
   - 7 days × 144 invocations/day = 1,008 invocations
   - At ~$0.000130 per invocation = $0.13

3. **GitHub Actions** ($0.00 in example)
   - Cloud Build costs for CI/CD and weekly cleanup
   - May be $0.00 if no deployments or cleanups during period
   - Typically $0.05-0.20/month when active
   - Weekly cleanup (4 runs/month) costs very little

### Monthly Projection

The section shows:
- **Total**: Sum of all automation costs in the period
- **Monthly Projection**: Extrapolated to 30 days

In this example:
- 7-day total: $0.15
- Monthly projection: $0.15 × (30/7) = $0.64

### Why This Is Useful

1. **Visibility**: See exactly what automation costs
2. **Validation**: Verify scheduler is running as expected
3. **Optimization**: Identify if costs are higher than expected
4. **Budgeting**: Include fixed automation costs in budget

### Expected Normal Values

For a typical month with minimal training activity:

| Component | Monthly Cost |
|-----------|--------------|
| Scheduler Service Fee | $0.20 |
| Scheduler Invocations | $0.50-1.00 |
| GitHub Actions | $0.05-0.20 |
| **Total Automation** | **$0.75-1.40** |

### Troubleshooting

**If values are much higher:**
- Check if scheduler interval changed
- Verify no unnecessary workflows running
- Look for failed jobs (retries increase costs)

**If values are $0.00:**
- Period may be too short (try 30 days)
- Scheduler may be paused
- Check billing data lag (24-48 hours)

## How to Use This Information

1. **Monitor Monthly**: Run the script monthly to track trends
2. **Compare**: Check if automation costs align with expectations
3. **Optimize**: If costs are unexpectedly high, investigate
4. **Budget**: Include ~$1/month for automation in budget planning

## Commands to Run

```bash
# Get 7-day report with automation breakdown
python scripts/track_daily_costs.py --days 7 --use-user-credentials

# Get 30-day report for full month view
python scripts/track_daily_costs.py --days 30 --use-user-credentials

# Export to CSV for analysis
python scripts/track_daily_costs.py --days 30 --output costs.csv --use-user-credentials
```

## Related Documentation

- [SCHEDULER_COSTS_TRACKING.md](SCHEDULER_COSTS_TRACKING.md) - Comprehensive guide
- [COST_STATUS.md](COST_STATUS.md) - Current cost status
- [docs/SCHEDULER_PAUSE_GUIDE.md](docs/SCHEDULER_PAUSE_GUIDE.md) - How to pause scheduler
