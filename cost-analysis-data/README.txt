MMM App Cost Analysis Data Collection
======================================

Collection Date: 20260202_125552
Project: datawarehouse-422511
Region: europe-west1

Files in this directory:
------------------------
20260202_125552_gcs_storage.txt          - GCS bucket usage and storage by prefix
20260202_125552_artifact_registry.txt    - Docker image sizes and tags
20260202_125552_job_executions.txt       - Training job execution history
20260202_125552_service_details.txt      - Cloud Run service configurations
20260202_125552_logging_volume.txt       - Log volume estimates
20260202_125552_secret_manager.txt       - Secret Manager details
20260202_125552_scheduler_jobs.txt       - Cloud Scheduler job details

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
