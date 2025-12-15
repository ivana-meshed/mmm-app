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

# Training job resource sizing
# Upgraded to 8 vCPU/32GB for reliable core availability and faster training
# 8 vCPU should provide at least 6-8 actual cores more consistently
training_cpu       = "8.0"
training_memory    = "32Gi"
training_max_cores = "8"  # Max cores - Cloud Run with 8 vCPU should provide enough

# Google OAuth allowed domains (comma-separated)
# Example: allowed_domains = "mesheddata.com,example.com"
# Default: allowed_domains = "mesheddata.com"

