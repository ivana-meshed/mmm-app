project_id   = "datawarehouse-422511"
region       = "europe-west1"
service_name = "mmm-app" # web => mmm-app-web
bucket_name  = "mmm-app-output"
#web_image      = "europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-web:latest"
#training_image = "europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-training:latest"
deployer_sa = "github-deployer@datawarehouse-422511.iam.gserviceaccount.com"

scheduler_job_name = "robyn-queue-tick"
queue_name         = "default"

sf_user      = "IPENC"
sf_account   = "AMXUZTH-AWS_BRIDGE"
sf_warehouse = "SMALL_WH"
sf_database  = "MESHED_BUYCYCLE"
sf_schema    = "GROWTH"
sf_role      = "ACCOUNTADMIN"

# Training job resource sizing (cost optimization)
# Reduced from 8 vCPU/32GB to 4 vCPU/16GB for 50% cost savings
# Change to 2.0/8Gi for 75% savings (but longer training time)
# Note: Cloud Run with 4.0 vCPU may only provide 3 actual cores due to scheduling
training_cpu       = "4.0"
training_memory    = "16Gi"
training_max_cores = "3"  # Conservative to avoid cgroups quota issues

# Google OAuth allowed domains (comma-separated)
# Example: allowed_domains = "mesheddata.com,example.com"
# Default: allowed_domains = "mesheddata.com"

