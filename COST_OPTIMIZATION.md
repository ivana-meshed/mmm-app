# Cost Optimization Guide

This document provides cost estimates for the MMM Trainer application across different machine configurations and workload scenarios.

## Cost Overview

The table below shows monthly costs for different combinations of:
- **Machine configurations**: Dev (2 vCPU/8GB), Prod (4 vCPU/16GB), 2x Prod (8 vCPU/32GB), 4x Prod (16 vCPU/64GB)
- **Workload types**: Test Run (200 iter/5 trials), Benchmark (2000 iter/5 trials), Production (10000 iter/5 trials)
- **Usage volumes**: 100, 500, 1000, 5000 calls per month (with 10, 50, 100, 500 training jobs respectively)

### Monthly Cost Estimates

| Configuration | Workload Type | 100 calls<br/>(10 jobs) | 500 calls<br/>(50 jobs) | 1000 calls<br/>(100 jobs) | 5000 calls<br/>(500 jobs) |
|---------------|---------------|-------------------------|-------------------------|---------------------------|---------------------------|
| **Dev (2 vCPU, 8GB)** | Test Run | $2.36 | $3.46 | $4.83 | $15.79 |
| | Benchmark | $3.56 | $9.46 | $16.83 | $75.79 |
| | Production | $8.96 | $36.46 | $70.83 | $345.79 |
| **Prod (4 vCPU, 16GB)** | Test Run | $2.36 | $3.46 | $4.83 | $15.79 |
| | Benchmark | $2.96 | $6.46 | $10.83 | $45.79 |
| | Production | $5.96 | $21.46 | $40.83 | $195.79 |
| **2x Prod (8 vCPU, 32GB)** | Test Run | $2.36 | $3.46 | $4.83 | $15.79 |
| | Benchmark | $2.96 | $6.46 | $10.83 | $45.79 |
| | Production | $5.96 | $21.46 | $40.83 | $195.79 |
| **4x Prod (16 vCPU, 64GB)** | Test Run | $2.36 | $3.46 | $4.83 | $15.79 |
| | Benchmark | $3.06 | $6.96 | $11.83 | $50.79 |
| | Production | $5.96 | $21.46 | $40.83 | $195.79 |

**Notes:**
- Costs include web service ($0.0017 per call), training jobs, and fixed costs ($2.09/month for GCS, Secret Manager, etc.)
- Training job ratio: 1 job per 10 web requests
- Idle cost (no usage): $2.09/month
- Web request duration: 30 seconds average

### Training Job Performance and Cost

Individual training job costs and durations for each workload type:

| Configuration | Test Run<br/>(200 iter × 5 trials) | Benchmark<br/>(2000 iter × 5 trials) | Production<br/>(10000 iter × 5 trials) |
|---------------|-------------------------------------|---------------------------------------|------------------------------------------|
| **Dev (2 vCPU, 8GB)** | 3 min, $0.01 | 33 min, $0.13 | 165 min (2.8 hrs), $0.67 |
| **Prod (4 vCPU, 16GB)** | 1 min, $0.01 | 9 min, $0.07 | 46 min, $0.37 |
| **2x Prod (8 vCPU, 32GB)** | 0.5 min, $0.01 | 5 min, $0.07 | 23 min, $0.37 |
| **4x Prod (16 vCPU, 64GB)** | 0.2 min, $0.01 | 2 min, $0.08 | 11 min, $0.37 |

**Key Insights:**
- Training jobs account for 85-96% of total costs at scale
- Dev config is most cost-effective but takes longer (best for development/overnight runs)
- Prod config (4 vCPU/16GB) offers good balance of speed and cost (current production default)
- Larger machines (8-16 vCPU) provide faster results with modest cost increase (~11-23% more than dev)
- Choose configuration based on urgency: dev for cost, larger configs for time-sensitive work

## Configuration Reference

Current infrastructure uses:
- **Dev environment**: 2 vCPU, 8GB memory for training jobs
- **Prod environment**: 4 vCPU, 16GB memory for training jobs
- **Queue execution**: Cloud Scheduler (every minute, ~$0.10/month, covered by free tier)
- **Idle cost**: $2.09/month with `min_instances=0`

To change training job resources, edit `infra/terraform/envs/dev.tfvars` or `prod.tfvars`:
```hcl
training_cpu       = "4.0"   # vCPU count
training_memory    = "16Gi"  # Memory allocation
training_max_cores = "4"     # Maximum cores
```

## Future Optimization Opportunities

### 1. Result Compression
**Potential Savings: ~50% reduction in storage and egress costs**

Compress training results before uploading to GCS.

**Implementation in `r/run_all.R`:**

```r
# After model training, compress results
library(zip)

# Compress RDS files
zip::zip(
  zipfile = "results.zip",
  files = c("OutputCollect.RDS", "InputCollect.RDS", "plots/", "one_pagers/")
)

# Upload compressed file instead of individual files
# This reduces both storage and egress costs
```

**Expected savings:**
- Storage: 50% reduction (e.g., $12.80 → $6.40 at 5000 calls/month)
- Egress: 50% reduction (e.g., $60.00 → $30.00 at 5000 calls/month)

### 2. Log Retention Policies
**Potential Savings: Minimal (first 50GB/month free)**

Configure log retention in Cloud Logging:

```bash
# Delete logs older than 30 days
gcloud logging sinks create delete-old-logs \
  logging.googleapis.com/projects/datawarehouse-422511 \
  --log-filter='timestamp<"2024-01-01T00:00:00Z"'
```

### 3. Optimize Docker Images
**Potential Savings: ~$0.05-0.10/month**

Reduce Artifact Registry storage by optimizing Docker images:

```dockerfile
# Use multi-stage builds
FROM python:3.11-slim as builder
# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.11-slim
# Copy only necessary files
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
```

### 4. Preemptible Cloud Run Jobs
**Potential Savings: Up to 50% on training job costs**

Currently not available for Cloud Run, but monitor GCP announcements for spot/preemptible instances.

## Monitoring and Cost Control

### Tracking Costs

Monitor these metrics to track cost optimization impact:

```bash
# View Cloud Run costs
gcloud billing accounts list
gcloud billing projects describe datawarehouse-422511

# Check storage usage
gsutil du -sh gs://mmm-app-output
gsutil lifecycle get gs://mmm-app-output

# View Cloud Run service metrics
gcloud run services describe mmm-app --region=europe-west1 --format=json
```

### GCP Console Dashboards

- **Cloud Run**: Monitor request count, latency, and costs
- **Cloud Storage**: Track storage usage and class distribution
- **Cloud Logging**: Monitor log volume and retention
- **Billing**: View cost breakdown by service

### Key Metrics to Track

1. **Training job costs**: Should be 85-96% of total variable costs
2. **Cache hit rate**: Target >70% for Snowflake queries
3. **Storage growth**: Monitor and apply lifecycle policies
4. **Cold start frequency**: Balance with idle costs

### Cost Alerts

Set up budget alerts in GCP Console:
```bash
# Create budget alert
gcloud billing budgets create \
  --billing-account=<ACCOUNT_ID> \
  --display-name="MMM App Monthly Budget" \
  --budget-amount=1000 \
  --threshold-rule=percent=50 \
  --threshold-rule=percent=90
```

## Adjusting Configuration

### Scaling Up for Production Workloads

To increase training performance:

1. Edit `infra/terraform/envs/prod.tfvars`:
```hcl
training_cpu       = "8.0"   # Double prod
training_memory    = "32Gi"
training_max_cores = "8"
```

2. Apply changes:
```bash
cd infra/terraform
terraform apply -var-file="envs/prod.tfvars"
```

3. Consider cost vs time trade-off (see cost overview table)

### Reverting Changes

If cold starts become unacceptable:

```bash
cd infra/terraform
# Revert min_instances to 2 in variables.tf or override in tfvars
terraform apply -var="min_instances=2" -var-file="envs/prod.tfvars"
```

## Cost Calculation Reference

**Baseline data** (from production testing):
- Dev config (2 vCPU, 8GB): 1983 seconds with 2000 iterations × 5 trials
- New Test Run (200 iterations × 5 trials) = 1/10th of baseline
- New Production (10000 iterations × 5 trials) = 5× baseline  
- Scaling is linear with (iterations × trials)
- Performance improves with CPU/memory but with diminishing returns

**Cloud Run pricing** (europe-west1):
- CPU: $0.000024 per vCPU-second
- Memory: $0.0000025 per GiB-second
- Includes per-second billing (no minimum charge)

**Fixed monthly costs**:
- GCS storage: ~$0.50-2.00/month (depends on data volume)
- Secret Manager: $0.06/month (6 secrets × $0.01)
- Cloud Scheduler: $0.10/month (covered by free tier)
- Artifact Registry: ~$0.50/month
- Total fixed: ~$2.09/month
