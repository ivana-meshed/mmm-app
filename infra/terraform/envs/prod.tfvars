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
# Using 8 vCPU to bypass Cloud Run platform quotas that affect lower tiers
# 8 vCPU tier typically provides better core allocation (6-8 actual cores)
# Higher vCPU tiers are scheduled onto less-constrained host pools
# Cost: ~$1.17/hour = ~$5.85 per 30-min job (vs $2.92 at 4 vCPU)
# Expected: 3-4x performance improvement = net cost savings per unit of work
training_cpu       = "8.0"
training_memory    = "32Gi"
training_max_cores = "8"  # Should provide 6-8 actual cores

# Google OAuth allowed domains (comma-separated)
# Example: allowed_domains = "mesheddata.com,example.com"
# Default: allowed_domains = "mesheddata.com"

