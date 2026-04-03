# Example Output from track_daily_costs.py

This file shows example output from the cost tracking script to help developers understand what to expect.

## Console Output Example

```
Fetching cost data for project: datawarehouse-422511
Date range: Last 30 days

Querying BigQuery billing export...
Retrieved 1,234 billing records

================================================================================
Daily Google Cloud Services Cost Report (30 days)
================================================================================

Date: 2026-02-09
--------------------------------------------------------------------------------
  mmm-app-dev-training: $12.45
    - compute_cpu: $8.20
    - compute_memory: $3.25
    - registry: $0.50
    - storage: $0.50
  mmm-app-dev-web: $2.15
    - scheduler_requests: $1.20
    - user_requests: $0.45
    - compute_cpu: $0.30
    - compute_memory: $0.10
    - registry: $0.05
    - storage: $0.05
  mmm-app-training: $45.80
    - compute_cpu: $30.50
    - compute_memory: $12.30
    - registry: $1.50
    - storage: $1.50
  mmm-app-web: $5.25
    - scheduler_requests: $2.80
    - user_requests: $1.50
    - compute_cpu: $0.60
    - compute_memory: $0.25
    - registry: $0.05
    - storage: $0.05
  Daily Total: $65.65

Date: 2026-02-08
--------------------------------------------------------------------------------
  mmm-app-dev-training: $8.30
    - compute_cpu: $5.50
    - compute_memory: $2.20
    - registry: $0.30
    - storage: $0.30
  mmm-app-dev-web: $2.00
    - scheduler_requests: $1.10
    - user_requests: $0.40
    - compute_cpu: $0.30
    - compute_memory: $0.10
    - registry: $0.05
    - storage: $0.05
  mmm-app-training: $52.10
    - compute_cpu: $34.70
    - compute_memory: $14.00
    - registry: $1.70
    - storage: $1.70
  mmm-app-web: $5.50
    - scheduler_requests: $2.90
    - user_requests: $1.60
    - compute_cpu: $0.65
    - compute_memory: $0.25
    - registry: $0.05
    - storage: $0.05
  Daily Total: $67.90

[... additional days ...]

================================================================================
Summary by Service
================================================================================

mmm-app-dev-training: $374.10
  - compute_cpu: $246.00 (65.8%)
  - compute_memory: $97.50 (26.1%)
  - registry: $15.00 (4.0%)
  - storage: $15.00 (4.0%)

mmm-app-dev-web: $64.50
  - scheduler_requests: $36.00 (55.8%)
  - user_requests: $13.50 (20.9%)
  - compute_cpu: $9.00 (14.0%)
  - compute_memory: $3.00 (4.7%)
  - registry: $1.50 (2.3%)
  - storage: $1.50 (2.3%)

mmm-app-training: $1,374.00
  - compute_cpu: $915.00 (66.6%)
  - compute_memory: $369.00 (26.9%)
  - registry: $45.00 (3.3%)
  - storage: $45.00 (3.3%)

mmm-app-web: $157.50
  - scheduler_requests: $84.00 (53.3%)
  - user_requests: $45.00 (28.6%)
  - compute_cpu: $18.00 (11.4%)
  - compute_memory: $7.50 (4.8%)
  - registry: $1.50 (1.0%)
  - storage: $1.50 (1.0%)

================================================================================
Grand Total: $1,970.10
Daily Average: $65.67
Monthly Projection: $1,970.10
================================================================================
```

## CSV Output Example

When using `--output costs.csv`, the file will contain:

```csv
date,service,category,cost
2026-02-09,mmm-app-dev-training,compute_cpu,8.20
2026-02-09,mmm-app-dev-training,compute_memory,3.25
2026-02-09,mmm-app-dev-training,registry,0.50
2026-02-09,mmm-app-dev-training,storage,0.50
2026-02-09,mmm-app-dev-web,scheduler_requests,1.20
2026-02-09,mmm-app-dev-web,user_requests,0.45
2026-02-09,mmm-app-dev-web,compute_cpu,0.30
2026-02-09,mmm-app-dev-web,compute_memory,0.10
2026-02-09,mmm-app-dev-web,registry,0.05
2026-02-09,mmm-app-dev-web,storage,0.05
2026-02-09,mmm-app-training,compute_cpu,30.50
2026-02-09,mmm-app-training,compute_memory,12.30
2026-02-09,mmm-app-training,registry,1.50
2026-02-09,mmm-app-training,storage,1.50
2026-02-09,mmm-app-web,scheduler_requests,2.80
2026-02-09,mmm-app-web,user_requests,1.50
2026-02-09,mmm-app-web,compute_cpu,0.60
2026-02-09,mmm-app-web,compute_memory,0.25
2026-02-09,mmm-app-web,registry,0.05
2026-02-09,mmm-app-web,storage,0.05
2026-02-08,mmm-app-dev-training,compute_cpu,5.50
2026-02-08,mmm-app-dev-training,compute_memory,2.20
...
```

## JSON Output Example

When using `--json`, the output is structured as:

```json
{
  "2026-02-09": {
    "mmm-app-dev-training": {
      "compute_cpu": 8.20,
      "compute_memory": 3.25,
      "registry": 0.50,
      "storage": 0.50
    },
    "mmm-app-dev-web": {
      "scheduler_requests": 1.20,
      "user_requests": 0.45,
      "compute_cpu": 0.30,
      "compute_memory": 0.10,
      "registry": 0.05,
      "storage": 0.05
    },
    "mmm-app-training": {
      "compute_cpu": 30.50,
      "compute_memory": 12.30,
      "registry": 1.50,
      "storage": 1.50
    },
    "mmm-app-web": {
      "scheduler_requests": 2.80,
      "user_requests": 1.50,
      "compute_cpu": 0.60,
      "compute_memory": 0.25,
      "registry": 0.05,
      "storage": 0.05
    }
  },
  "2026-02-08": {
    "mmm-app-dev-training": {
      "compute_cpu": 5.50,
      "compute_memory": 2.20,
      "registry": 0.30,
      "storage": 0.30
    },
    ...
  }
}
```

## Interpreting the Results

### Service Breakdown

- **mmm-app-training** (prod): Production training jobs - typically the highest cost
- **mmm-app-web** (prod): Production web service - includes user and scheduler requests
- **mmm-app-dev-training** (dev): Development training jobs - lower volume than prod
- **mmm-app-dev-web** (dev): Development web service - testing and development

### Cost Categories

- **compute_cpu**: CPU usage costs (vCPU-seconds)
  - Typically 65-70% of total costs
  - Scales with job duration and vCPU allocation

- **compute_memory**: Memory usage costs (GB-seconds)
  - Typically 25-30% of total costs
  - Scales with job duration and memory allocation

- **scheduler_requests**: Automated queue tick requests
  - Predictable and consistent
  - Production: ~144 requests/day (every 10 minutes)
  - Development: ~144 requests/day

- **user_requests**: User-initiated requests
  - Variable based on actual usage
  - Includes web UI interactions, API calls, data queries

- **registry**: Artifact Registry storage and operations
  - Container image storage (~$0.10/GB/month)
  - Shared across all services

- **storage**: Cloud Storage costs
  - Training data, model artifacts, temporary files
  - Shared across all services

### Cost Patterns to Watch

1. **High Training Costs**: If training costs are consistently high, consider:
   - Optimizing iterations/trials parameters
   - Using smaller test datasets for development
   - Reducing frequency of training jobs

2. **High Scheduler Costs**: If scheduler costs are unexpected:
   - Verify queue tick frequency (should be every 10 minutes)
   - Check if there are stuck jobs causing long-running requests

3. **High User Request Costs**: If user request costs spike:
   - May indicate increased actual usage (good!)
   - Could indicate inefficient queries or data processing
   - Consider caching frequently accessed data

4. **High Storage Costs**: If storage costs grow over time:
   - Implement lifecycle policies for old data
   - Archive or delete unused training results
   - Use cheaper storage classes for infrequently accessed data

## Use Cases

### Weekly Cost Review

```bash
# Generate weekly report every Monday
python scripts/track_daily_costs.py --days 7
```

### Monthly Budget Tracking

```bash
# Export to CSV for spreadsheet analysis
python scripts/track_daily_costs.py --days 30 --output monthly_costs.csv
```

### Cost Comparison Between Periods

```bash
# Last 7 days
python scripts/track_daily_costs.py --days 7 > week1.txt

# Previous 7 days
python scripts/track_daily_costs.py --days 14 > week2.txt

# Compare the totals
diff week1.txt week2.txt
```

### Automated Alerts

```bash
# Check if costs exceed threshold
TOTAL=$(python scripts/track_daily_costs.py --days 1 --json | \
        jq '[.[].[] | values] | add')

if (( $(echo "$TOTAL > 100" | bc -l) )); then
    echo "ALERT: Daily costs exceeded $100: $TOTAL"
fi
```
