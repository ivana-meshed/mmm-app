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
# Testing: Cloud Run with 8 vCPU only provides 2 actual cores due to cgroups quota
# Using 4 vCPU as intermediate test to check if more cores become available
# This provides better cost/performance ratio while investigating core allocation
training_cpu       = "4.0"
training_memory    = "16Gi"
training_max_cores = "4"  # Test if 4 vCPU provides more than 2 cores

# Google OAuth allowed domains (comma-separated)
# Example: allowed_domains = "mesheddata.com,example.com"
# Default: allowed_domains = "mesheddata.com"

