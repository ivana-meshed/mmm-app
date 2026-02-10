# Daily Cost Tracking for MMM Trainer

This document describes how to use the `track_daily_costs.py` script to monitor daily Google Cloud costs for the MMM Trainer application.

## Overview

The script provides detailed cost tracking broken down by:

### Service Breakdown
- **mmm-app-dev-web**: Development web service
- **mmm-app-web**: Production web service
- **mmm-app-training**: Production training jobs
- **mmm-app-dev-training**: Development training jobs

### Cost Category Breakdown
Within each service, costs are categorized as:
- **user_requests**: Costs from user interactions with the web UI
- **scheduler_requests**: Costs from automated queue tick invocations
- **compute_cpu**: CPU usage costs (Cloud Run vCPU seconds)
- **compute_memory**: Memory usage costs (Cloud Run memory GB-seconds)
- **registry**: Artifact Registry storage and operations (shared across services)
- **storage**: Cloud Storage costs (shared across services)
- **scheduler_service**: Cloud Scheduler service costs
- **other**: Other uncategorized costs

## Prerequisites

### 1. BigQuery Billing Export

The script requires BigQuery billing export to be enabled. To check or enable:

1. Go to [GCP Billing Console](https://console.cloud.google.com/billing)
2. Select your billing account (`01B2F0-BCBFB7-2051C5`)
3. Navigate to **Billing export** → **BigQuery export**
4. Verify the configuration:
   - Dataset: `mmm_billing`
   - Table: `gcp_billing_export_resource_v1_01B2F0_BCBFB7_2051C5`
   - Project: `datawarehouse-422511`
5. If not configured, enable it and wait 24 hours for data to populate

### 2. GCP Permissions

You need the following IAM permissions:
- `bigquery.jobs.create` (to run queries)
- `bigquery.tables.getData` (to read billing data)
- Role: **BigQuery Data Viewer** on the billing dataset

### 3. Authentication

Authenticate with Google Cloud:

```bash
# For local development
gcloud auth application-default login

# For service accounts (in production)
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account-key.json"
```

### 4. Python Dependencies

The script requires `google-cloud-bigquery`:

```bash
pip install google-cloud-bigquery
```

This dependency is already included in the project's `requirements.txt`.

## Usage

### Basic Usage

Track costs for the last 30 days (default):

```bash
python scripts/track_daily_costs.py
```

### Custom Time Range

Track costs for a specific number of days:

```bash
# Last 7 days
python scripts/track_daily_costs.py --days 7

# Last 90 days
python scripts/track_daily_costs.py --days 90
```

### Export to CSV

Save cost data to a CSV file for further analysis:

```bash
python scripts/track_daily_costs.py --days 30 --output costs_report.csv
```

The CSV will contain columns: `date`, `service`, `category`, `cost`

### JSON Output

Output results as JSON (useful for automation):

```bash
python scripts/track_daily_costs.py --days 7 --json > costs.json
```

### Custom Project

Specify a different GCP project:

```bash
python scripts/track_daily_costs.py --project my-project-id
```

## Output Format

### Console Output

The script prints a detailed daily report:

```
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

...

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

### CSV Output

When using `--output`, the CSV format is:

```csv
date,service,category,cost
2026-02-09,mmm-app-dev-training,compute_cpu,8.20
2026-02-09,mmm-app-dev-training,compute_memory,3.25
2026-02-09,mmm-app-dev-web,scheduler_requests,1.20
...
```

This format is ideal for:
- Importing into Excel or Google Sheets
- Creating custom visualizations
- Time series analysis
- Trend detection

## Cost Categories Explained

### User Requests
- Web UI page loads
- Data queries to Snowflake
- File downloads from GCS
- API endpoint calls

These costs vary based on actual user activity.

### Scheduler Requests
- Queue tick invocations (every 10 minutes)
- Automated job status checks
- Queue processing operations

These costs are predictable and consistent:
- Production: 144 invocations/day (every 10 minutes)
- Development: 144 invocations/day

### Compute (CPU & Memory)
- Cloud Run container execution time
- Training job computation
- Web service request processing

Costs scale with:
- Job execution duration
- Number of concurrent requests
- Resource allocation (vCPU, memory)

### Registry
- Artifact Registry storage
- Container image storage
- Image pull operations

Costs are shared across all services:
- Approximately $0.10/GB/month for storage
- Free egress within same region

### Storage
- GCS bucket storage costs
- Training data storage
- Model artifacts storage
- Temporary files

Costs depend on:
- Total data stored (GB)
- Storage class (Standard, Nearline, Coldline)
- Data retrieval frequency

### Scheduler Service
- Cloud Scheduler service costs
- Job execution management

Typically covered by free tier ($0.10/month for 3 jobs).

## Environment Variables

You can override default configuration with environment variables:

```bash
# Override project ID
export PROJECT_ID="my-project-id"

# Override billing dataset
export BILLING_DATASET="my_billing_dataset"

# Override billing account number
export BILLING_ACCOUNT_NUM="01XXXX_YYYYYY_ZZZZZZ"

# Run script with overrides
python scripts/track_daily_costs.py
```

## Automation

### Daily Cost Report via Cron

Add to crontab for daily reports:

```bash
# Run daily at 9 AM and email results
0 9 * * * cd /path/to/mmm-app && python scripts/track_daily_costs.py --days 1 | mail -s "Daily MMM Cost Report" team@example.com
```

### Weekly CSV Export

Export weekly cost data for analysis:

```bash
# Every Monday at 10 AM
0 10 * * 1 cd /path/to/mmm-app && python scripts/track_daily_costs.py --days 7 --output /reports/weekly_costs_$(date +\%Y\%m\%d).csv
```

### CI/CD Integration

Add to GitHub Actions for cost tracking on deployments:

```yaml
- name: Track costs after deployment
  run: |
    python scripts/track_daily_costs.py --days 7 --json > cost_report.json
    cat cost_report.json
```

## Troubleshooting

### Error: "Billing export table not found"

**Problem**: BigQuery billing export is not configured or table name is incorrect.

**Solution**:
1. Verify billing export is enabled in GCP Console
2. Check the dataset name: `mmm_billing`
3. Check the table name: `gcp_billing_export_resource_v1_01B2F0_BCBFB7_2051C5`
4. Wait 24 hours after enabling for data to populate

### Error: "Failed to initialize BigQuery client"

**Problem**: Authentication is not configured.

**Solution**:
```bash
gcloud auth application-default login
```

### No billing data found

**Problem**: Either no costs were incurred, or data has not been exported yet.

**Solution**:
1. Check if services are running (they should have some costs)
2. Wait 24 hours after enabling billing export
3. Verify you're querying the correct date range
4. Check BigQuery console to see if data exists

### Permission denied errors

**Problem**: Your GCP user/service account lacks necessary permissions.

**Solution**:
Grant the **BigQuery Data Viewer** role on the billing dataset:

```bash
gcloud projects add-iam-policy-binding datawarehouse-422511 \
  --member="user:your-email@example.com" \
  --role="roles/bigquery.dataViewer"
```

## Best Practices

### 1. Regular Monitoring
- Run the script weekly to track cost trends
- Export to CSV for historical analysis
- Set up alerts for unusual cost spikes

### 2. Cost Optimization
Use the script output to identify:
- High-cost services that could be optimized
- Unexpected cost categories
- Trends over time (increasing/decreasing)

### 3. Budget Planning
- Use monthly projections for budget planning
- Compare actual vs. estimated costs
- Track cost impacts of infrastructure changes

### 4. Team Visibility
- Share weekly cost reports with the team
- Include cost summaries in sprint retrospectives
- Make cost awareness part of development culture

## Comparison with Existing Scripts

This script complements the existing cost tracking scripts:

| Script | Purpose | Data Source | Best For |
|--------|---------|-------------|----------|
| `get_actual_costs.sh` | Historical actual costs | BigQuery billing | Detailed billing analysis |
| `get_comprehensive_costs.sh` | Estimated costs with breakdowns | Cloud Run API | Quick cost estimates, infrastructure analysis |
| `track_daily_costs.py` | **Daily cost tracking by service** | **BigQuery billing** | **Daily monitoring, service-level breakdown** |

### When to Use Each Script

**Use `track_daily_costs.py`** (this script) when:
- You want daily cost breakdowns by specific services
- You need to track costs by category (requests, compute, storage)
- You want to export data to CSV for analysis
- You're setting up automated daily/weekly reports

**Use `get_actual_costs.sh`** when:
- You want a quick overview of total costs
- You're checking billing data for the first time
- You need to verify BigQuery export is working

**Use `get_comprehensive_costs.sh`** when:
- You want detailed infrastructure analysis
- You're estimating costs for upcoming periods
- You need deployment frequency impact analysis
- You want to analyze training job patterns

## Related Documentation

- [COST_OPTIMIZATION.md](../COST_OPTIMIZATION.md) - Cost optimization strategies
- [ARCHITECTURE.md](../ARCHITECTURE.md) - System architecture and cost drivers
- [DEVELOPMENT.md](../DEVELOPMENT.md) - Development environment setup

## Support

For questions or issues with the cost tracking script:
1. Check the Troubleshooting section above
2. Review the GCP Billing export configuration
3. Consult the GCP Billing API documentation
4. Contact the infrastructure team

## Future Enhancements

Potential improvements to the script:
- [ ] Add cost alerts/thresholds
- [ ] Generate charts/visualizations
- [ ] Compare costs across time periods
- [ ] Budget variance reporting
- [ ] Cost forecasting based on trends
- [ ] Integration with Slack/email notifications
- [ ] Dashboard integration (e.g., Grafana)
