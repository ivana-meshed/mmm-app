# Cost Tracking Script - Usage Guide

## Overview

The `get_comprehensive_costs.sh` script provides complete visibility into Cloud Run costs across all cost drivers for the MMM Trainer application.

## What It Tracks

### 1. Training Jobs
- **Production** (`mmm-app-training`)
- **Development** (`mmm-app-dev-training`)
- Execution counts, durations, success/failure rates
- CPU and memory costs per job
- Average cost per job
- Monthly projections

### 2. Web Services
- **Production** (`mmm-app-web`)
- **Development** (`mmm-app-dev-web`)
- Resource configuration (CPU, memory, min/max instances)
- Idle costs (when min_instances > 0)
- Request-based cost estimation

### 3. Scheduler Invocations
- **Production** (`robyn-queue-tick`)
- **Development** (`robyn-queue-tick-dev`)
- Invocation frequency and schedule
- Container time consumed per month
- Cost breakdown (CPU, memory, invocations)

### 4. Deployment Impact
- Active revision counts
- Deployment churn cost estimation
- Recommendations for optimization

### 5. Artifact Registry
- Container image storage costs
- Repository size tracking

### 6. Cost Breakdown
- **By environment:** Production vs Development
- **By component:** Training, Web, Scheduler, Storage
- **Monthly projections** from any time period

## Usage

### Basic Usage

```bash
# Analyze last 30 days (default)
./scripts/get_comprehensive_costs.sh
```

### Custom Time Periods

```bash
# Last 7 days
DAYS_BACK=7 ./scripts/get_comprehensive_costs.sh

# Last 90 days
DAYS_BACK=90 ./scripts/get_comprehensive_costs.sh

# Last 14 days
./scripts/get_comprehensive_costs.sh 14
```

### Environment Variables

You can override defaults via environment variables:

```bash
# Custom project and region
PROJECT_ID=my-project-id REGION=us-central1 ./scripts/get_comprehensive_costs.sh

# Custom time period
DAYS_BACK=60 ./scripts/get_comprehensive_costs.sh
```

## Requirements

### Required Tools

- **gcloud CLI** - Configured with appropriate permissions
  ```bash
  gcloud auth login
  gcloud config set project datawarehouse-422511
  ```

- **bc** - For cost calculations (usually pre-installed)

### Optional Tools

- **jq** - For JSON parsing (recommended, but not required)
  ```bash
  # macOS
  brew install jq
  
  # Ubuntu/Debian
  sudo apt-get install jq
  ```

If `jq` is not available, the script will use fallback parsing (less accurate but functional).

### Required Permissions

Your gcloud user or service account needs:

- `roles/run.viewer` - View Cloud Run services and jobs
- `roles/cloudscheduler.viewer` - View scheduler jobs
- `roles/artifactregistry.reader` - View artifact registry

## Example Output

```
===========================================
Comprehensive Cloud Run Cost Analysis
===========================================
Project: datawarehouse-422511
Region: europe-west1
Period: Last 30 days

===========================================
TRAINING JOBS
===========================================

Analyzing: PROD_TRAINING
  Total executions: 3
  Configuration: 8.0 vCPU, 32 GB
  Successful: 3
  Failed: 0
  Total duration: 1233 seconds (20 minutes)
  Average duration: 6 minutes per job

  Cost Breakdown:
    CPU cost:    $0.24
    Memory cost: $0.10
    Total cost:  $0.34
    Per job:     $0.11

Analyzing: DEV_TRAINING
  Total executions: 125
  Configuration: 8.0 vCPU, 32 GB
  Successful: 125
  Failed: 0
  Total duration: 84974 seconds (1416 minutes)
  Average duration: 11 minutes per job

  Cost Breakdown:
    CPU cost:    $16.32
    Memory cost: $6.80
    Total cost:  $23.11
    Per job:     $0.18

===========================================
COST SUMMARY (Last 30 days)
===========================================

Training Jobs:
  Production: 3 jobs, $0.34
  Development: 125 jobs, $23.11
  Subtotal: $23.45

Web Services & Schedulers:
  Production idle: $0.00
  Development idle: $0.00
  Production scheduler: $1.88
  Development scheduler: $1.88
  Subtotal: $3.76

Artifact Registry: $1.00

Total (30 days): $28.21
Projected monthly (30 days): $28.21

===========================================
COST BREAKDOWN BY ENVIRONMENT
===========================================

Production: $2.22
Development: $24.99
Shared (Artifact Registry): $1.00
```

## Understanding the Output

### Training Jobs Section

Shows detailed execution statistics:
- **Total executions:** Number of jobs run in the period
- **Configuration:** vCPU and memory allocation
- **Success/Failure rate:** Job completion statistics
- **Duration metrics:** Total and average run times
- **Cost breakdown:** CPU, memory, and per-job costs

### Web Services Section

Shows service configuration and idle costs:
- **Configuration:** CPU, memory, scaling settings
- **Idle costs:** Cost when min_instances > 0 (should be $0 with scale-to-zero)
- **Request costs:** Depend on actual usage (scheduler + user requests)

### Scheduler Section

Shows queue tick costs:
- **Schedule:** Cron expression and frequency
- **Invocations:** Per hour/day/month counts
- **Container time:** Total seconds of web service activation
- **Cost breakdown:** CPU, memory, and invocation costs

### Deployment Impact

Explains deployment churn:
- **Active revisions:** Number of revisions currently deployed
- **Cost impact:** During deployment, old and new revisions run simultaneously
- **Recommendations:** Strategies to reduce deployment frequency

### Cost Summary

Consolidates all costs:
- **By component:** Training, web, scheduler, storage
- **By environment:** Production vs development breakdown
- **Monthly projection:** Scales costs to 30-day period if different

## Interpreting Results

### Expected Cost Ranges (After Optimization)

**Typical Month (Light Usage):**
```
Production: $2-5/month
  - Training: 3-10 jobs × $0.20 = $0.60-2.00
  - Web idle: $0 (scale-to-zero)
  - Scheduler: $1.88 (every 10 minutes)
  
Development: $20-30/month
  - Training: 100-150 jobs × $0.18 = $18-27
  - Web idle: $0 (scale-to-zero)
  - Scheduler: $1.88 (every 10 minutes)
  
Shared: $1-2/month
  - Artifact Registry storage
  
Total: $23-37/month
```

**Heavy Usage Month:**
```
Production: $10-20/month
  - Training: 50-100 jobs
  - Scheduler: $1.88
  
Development: $50-100/month
  - Training: 250-500 jobs
  - Scheduler: $1.88
  
Total: $60-120/month
```

### Cost Anomalies to Watch For

**Training costs higher than expected:**
- Check job failure rates (failed jobs still incur costs)
- Verify job configuration hasn't changed (CPU/memory)
- Look for duplicate job submissions

**Scheduler costs higher than expected:**
- Verify schedule is `*/10 * * * *` (every 10 minutes)
- Check for duplicate scheduler jobs
- Ensure old scheduler jobs are deleted

**Idle costs > $0:**
- Check min_instances setting (should be 0)
- Verify Terraform applied correctly
- May indicate scale-to-zero isn't working

**Deployment impact mentioned:**
- High revision counts indicate frequent deployments
- Consider implementing deployment frequency optimization
- See recommendations in executive summary

## Troubleshooting

### Script Fails with "gcloud: command not found"

Install and configure gcloud CLI:
```bash
# macOS
brew install google-cloud-sdk

# Ubuntu/Debian
sudo snap install google-cloud-cli --classic

# Configure
gcloud auth login
gcloud config set project datawarehouse-422511
```

### Script Shows "No executions found"

Possible causes:
1. **Time period too short** - Try increasing DAYS_BACK
2. **No jobs run recently** - This is normal for low-traffic periods
3. **Permission issue** - Verify you have `roles/run.viewer`
4. **Wrong service names** - Service names may have changed

### Cost Calculations Seem Wrong

Possible causes:
1. **Missing `bc` command** - Install: `sudo apt-get install bc` (Linux) or `brew install bc` (macOS)
2. **jq not installed** - Script uses fallback parsing (less accurate)
3. **Date parsing issues** - The script handles both GNU and BSD date formats

### Permission Denied Errors

Grant required roles:
```bash
# For your user account
gcloud projects add-iam-policy-binding datawarehouse-422511 \
  --member="user:your-email@example.com" \
  --role="roles/run.viewer"

gcloud projects add-iam-policy-binding datawarehouse-422511 \
  --member="user:your-email@example.com" \
  --role="roles/cloudscheduler.viewer"

gcloud projects add-iam-policy-binding datawarehouse-422511 \
  --member="user:your-email@example.com" \
  --role="roles/artifactregistry.reader"
```

## Integration with Monitoring

### Scheduled Runs

Run the script monthly and save results:

```bash
#!/bin/bash
# monthly-cost-report.sh

DATE=$(date +%Y-%m)
OUTPUT_FILE="cost-reports/cost-report-${DATE}.txt"

mkdir -p cost-reports
./scripts/get_comprehensive_costs.sh > "$OUTPUT_FILE"

echo "Cost report saved to: $OUTPUT_FILE"

# Optional: Send via email
# mail -s "MMM Trainer Monthly Cost Report" admin@example.com < "$OUTPUT_FILE"
```

Schedule with cron:
```cron
# Run on the 1st of each month at 9 AM
0 9 1 * * /path/to/monthly-cost-report.sh
```

### Cost Alerts

Set up alerts based on script output:

```bash
#!/bin/bash
# cost-alert-check.sh

COST=$(./scripts/get_comprehensive_costs.sh | grep "Total (30 days):" | awk '{print $4}' | tr -d '$')
THRESHOLD=60

if (( $(echo "$COST > $THRESHOLD" | bc -l) )); then
  echo "ALERT: Monthly cost $COST exceeds threshold $THRESHOLD"
  # Send notification
  # curl -X POST webhook_url -d "Cost alert: $COST"
fi
```

## Related Documentation

- **COST_REDUCTION_EXECUTIVE_SUMMARY.md** - Complete cost optimization overview
- **COST_OPTIMIZATION.md** - Cost optimization guide with detailed formulas
- **docs/COST_OPTIMIZATIONS_SUMMARY.md** - Historical cost optimization implementations

## Support

For issues or questions:
1. Check this README for common problems
2. Review the executive summary for cost context
3. Verify gcloud permissions and configuration
4. Check script output for specific error messages

## Change Log

- **2026-02-05:** Initial version created
  - Comprehensive cost tracking across all drivers
  - Production vs development breakdown
  - Deployment frequency analysis
  - Artifact registry costs
  - Monthly projections
