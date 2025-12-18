project_id   = "datawarehouse-422511"
region       = "europe-west1"
service_name = "mmm-app-dev"
bucket_name  = "mmm-app-output"
#web_image      = "europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-web:${var.image_tag}"
#training_image = "europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-training:latest"
deployer_sa = "github-deployer@datawarehouse-422511.iam.gserviceaccount.com"

scheduler_job_name = "robyn-queue-tick-dev"
queue_name         = "default-dev"

# Snowflake (you can reuse prod or set dev values)
sf_user      = "IPENC"
sf_account   = "AMXUZTH-AWS_BRIDGE"
sf_warehouse = "SMALL_WH"
sf_database  = "MESHED_BUYCYCLE"
sf_schema    = "GROWTH"
sf_role      = "ACCOUNTADMIN"

# Training job resource sizing
# Testing with 8 vCPU to bypass Cloud Run platform quotas that affect lower tiers
# 8 vCPU tier typically provides better core allocation (6-8 actual cores)
# Higher vCPU tiers are scheduled onto less-constrained host pools
training_cpu       = "8.0"
training_memory    = "32Gi"
training_max_cores = "8"  # Test if 8 vCPU provides 6-8 actual cores

# Google OAuth allowed domains (comma-separated)
# Example: allowed_domains = "mesheddata.com,example.com"
# Default: allowed_domains = "mesheddata.com"

