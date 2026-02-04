#!/bin/bash

# Cost Analysis Data Collection Script
# This script collects actual usage data to refine cost estimates for both dev and prod environments
# Run this script with appropriate GCP credentials (gcloud auth login)

set -e

PROJECT_ID="datawarehouse-422511"
REGION="europe-west1"
BUCKET="mmm-app-output"
REGISTRY_REPO="mmm-repo"
OUTPUT_DIR="cost-analysis-data"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "=========================================="
echo "MMM App Cost Analysis Data Collection"
echo "=========================================="
echo "Project: $PROJECT_ID"
echo "Region: $REGION"
echo "Timestamp: $TIMESTAMP"
echo ""

# Create output directory
mkdir -p "$OUTPUT_DIR"

echo "Collecting data... This may take a few minutes."
echo ""

# 1. GCS Storage Usage
echo "[1/7] Collecting GCS storage usage..."
{
  echo "=== GCS Storage Analysis ==="
  echo ""
  echo "Total bucket size:"
  gsutil du -sh gs://$BUCKET || echo "Failed to get bucket size"
  echo ""
  echo "Storage by prefix (dev vs prod):"
  echo "Production (default queue):"
  gsutil du -sh gs://$BUCKET/training-configs/default/ 2>/dev/null || echo "No data"
  echo "Development (default-dev queue):"
  gsutil du -sh gs://$BUCKET/training-configs/default-dev/ 2>/dev/null || echo "No data"
  echo ""
  echo "Recent objects (last 100):"
  gsutil ls -lR gs://$BUCKET | tail -100
} > "$OUTPUT_DIR/${TIMESTAMP}_gcs_storage.txt" 2>&1

# 2. Artifact Registry Storage
echo "[2/7] Collecting Artifact Registry data..."
{
  echo "=== Artifact Registry Analysis ==="
  echo ""
  echo "All images and tags:"
  gcloud artifacts docker images list \
    $REGION-docker.pkg.dev/$PROJECT_ID/$REGISTRY_REPO \
    --include-tags --format="table(package,version,size)" || echo "Failed to list images"
  echo ""
  echo "Repository size:"
  gcloud artifacts repositories describe $REGISTRY_REPO \
    --location=$REGION \
    --format="get(sizeBytes)" || echo "Failed to get repo size"
} > "$OUTPUT_DIR/${TIMESTAMP}_artifact_registry.txt" 2>&1

# 3. Cloud Run Job Execution History
echo "[3/7] Collecting Cloud Run job execution history..."
{
  echo "=== Cloud Run Job Executions ==="
  echo ""
  echo "Production training job (mmm-app-training) - Last 100 executions:"
  gcloud run jobs executions list \
    --job=mmm-app-training \
    --region=$REGION \
    --limit=100 \
    --format="table(name,createTime,completionTime,runningDuration(),status)" || echo "No prod job data"
  echo ""
  echo "Development training job (mmm-app-dev-training) - Last 100 executions:"
  gcloud run jobs executions list \
    --job=mmm-app-dev-training \
    --region=$REGION \
    --limit=100 \
    --format="table(name,createTime,completionTime,runningDuration(),status)" || echo "No dev job data"
} > "$OUTPUT_DIR/${TIMESTAMP}_job_executions.txt" 2>&1

# 4. Cloud Run Service Metrics
echo "[4/7] Collecting Cloud Run service metrics..."
{
  echo "=== Cloud Run Service Details ==="
  echo ""
  echo "Production web service (mmm-app-web):"
  gcloud run services describe mmm-app-web --region=$REGION \
    --format="table(status.url,status.traffic,metadata.annotations)" || echo "No prod web service"
  echo ""
  echo "Development web service (mmm-app-dev-web):"
  gcloud run services describe mmm-app-dev-web --region=$REGION \
    --format="table(status.url,status.traffic,metadata.annotations)" || echo "No dev web service"
} > "$OUTPUT_DIR/${TIMESTAMP}_service_details.txt" 2>&1

# 5. Cloud Logging Volume Estimates
echo "[5/7] Estimating Cloud Logging volume..."
{
  echo "=== Cloud Logging Volume Analysis ==="
  echo ""
  echo "Production web service logs (last 1000 entries, last 30 days):"
  LOG_COUNT_PROD_WEB=$(gcloud logging read \
    'resource.type="cloud_run_service" AND resource.labels.service_name="mmm-app-web"' \
    --limit=1000 \
    --format="value(timestamp)" \
    --freshness=30d 2>/dev/null | wc -l)
  echo "Log entry count (sample): $LOG_COUNT_PROD_WEB"
  echo ""
  echo "Development web service logs (last 1000 entries, last 30 days):"
  LOG_COUNT_DEV_WEB=$(gcloud logging read \
    'resource.type="cloud_run_service" AND resource.labels.service_name="mmm-app-dev-web"' \
    --limit=1000 \
    --format="value(timestamp)" \
    --freshness=30d 2>/dev/null | wc -l)
  echo "Log entry count (sample): $LOG_COUNT_DEV_WEB"
  echo ""
  echo "Production training job logs (last 1000 entries, last 30 days):"
  LOG_COUNT_PROD_JOB=$(gcloud logging read \
    'resource.type="cloud_run_job" AND resource.labels.job_name="mmm-app-training"' \
    --limit=1000 \
    --format="value(timestamp)" \
    --freshness=30d 2>/dev/null | wc -l)
  echo "Log entry count (sample): $LOG_COUNT_PROD_JOB"
  echo ""
  echo "Development training job logs (last 1000 entries, last 30 days):"
  LOG_COUNT_DEV_JOB=$(gcloud logging read \
    'resource.type="cloud_run_job" AND resource.labels.job_name="mmm-app-dev-training"' \
    --limit=1000 \
    --format="value(timestamp)" \
    --freshness=30d 2>/dev/null | wc -l)
  echo "Log entry count (sample): $LOG_COUNT_DEV_JOB"
  echo ""
  echo "Note: Log volume in GB needs to be calculated from actual log size, not just entry count."
} > "$OUTPUT_DIR/${TIMESTAMP}_logging_volume.txt" 2>&1

# 6. Secret Manager Usage
echo "[6/7] Collecting Secret Manager details..."
{
  echo "=== Secret Manager Analysis ==="
  echo ""
  echo "Active secrets:"
  gcloud secrets list --format="table(name,createTime)" || echo "Failed to list secrets"
  echo ""
  echo "Secret access counts would require audit logs analysis"
} > "$OUTPUT_DIR/${TIMESTAMP}_secret_manager.txt" 2>&1

# 7. Cloud Scheduler Jobs
echo "[7/7] Collecting Cloud Scheduler job details..."
{
  echo "=== Cloud Scheduler Jobs ==="
  echo ""
  gcloud scheduler jobs list --location=$REGION \
    --format="table(name,schedule,state,lastAttemptTime)" || echo "Failed to list scheduler jobs"
} > "$OUTPUT_DIR/${TIMESTAMP}_scheduler_jobs.txt" 2>&1

# Generate summary
echo ""
echo "=========================================="
echo "Data Collection Complete!"
echo "=========================================="
echo ""
echo "Output files created in: $OUTPUT_DIR/"
echo ""
echo "Files created:"
ls -lh "$OUTPUT_DIR/${TIMESTAMP}"_*.txt
echo ""
echo "Next steps:"
echo "1. Review the collected data files"
echo "2. Send these files to the cost analysis team"
echo "3. Update COST_ANALYSIS_DEV_PROD.md with actual usage data"
echo ""
echo "For detailed billing data, consider:"
echo "- Setting up BigQuery billing export in GCP Console"
echo "- Running the SQL queries from COST_ANALYSIS_DEV_PROD.md"
echo "- Checking Snowflake query history with provided SQL"
echo ""

# Create a summary README
cat > "$OUTPUT_DIR/README.txt" <<EOF
MMM App Cost Analysis Data Collection
======================================

Collection Date: $TIMESTAMP
Project: $PROJECT_ID
Region: $REGION

Files in this directory:
------------------------
${TIMESTAMP}_gcs_storage.txt          - GCS bucket usage and storage by prefix
${TIMESTAMP}_artifact_registry.txt    - Docker image sizes and tags
${TIMESTAMP}_job_executions.txt       - Training job execution history
${TIMESTAMP}_service_details.txt      - Cloud Run service configurations
${TIMESTAMP}_logging_volume.txt       - Log volume estimates
${TIMESTAMP}_secret_manager.txt       - Secret Manager details
${TIMESTAMP}_scheduler_jobs.txt       - Cloud Scheduler job details

How to use this data:
---------------------
1. Review each file for actual usage patterns
2. Compare with estimates in docs/COST_ANALYSIS_DEV_PROD.md
3. Calculate actual costs based on GCP pricing
4. Update cost documentation with real data

Key metrics to extract:
-----------------------
- GCS: Total storage (GB) split by dev/prod prefixes
- Artifact Registry: Total image storage (GB)
- Job Executions: Number of jobs per month (dev vs prod)
- Job Duration: Average duration per environment
- Log Volume: Estimate GB/month from entry counts and average size

Additional data needed:
-----------------------
- Snowflake query history (run SQL from COST_ANALYSIS_DEV_PROD.md)
- GitHub Actions build history (check GitHub Actions tab)
- Actual billing data from GCP Console → Billing

For questions, see docs/COST_ANALYSIS_DEV_PROD.md section:
"Data Collection Instructions"
EOF

echo "Summary README created: $OUTPUT_DIR/README.txt"
echo ""
