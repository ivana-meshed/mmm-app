project_id   = "datawarehouse-422511"
region       = "europe-west1"
service_name = "mmm-app" # web => mmm-app-web
bucket_name  = "mmm-app-output"
#web_image      = "europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-web:latest"
#training_image = "europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-training:latest"
deployer_sa = "github-deployer@datawarehouse-422511.iam.gserviceaccount.com"

scheduler_job_name = "robyn-queue-tick"
queue_name         = "default"

# Scheduler control: Set to false to pause scheduler for cost monitoring
# When paused, training jobs won't auto-process from queue (manual trigger required)
# scheduler_enabled = false  # Uncomment to pause scheduler
scheduler_enabled = false

# Cost optimization: Scale-to-zero configuration
min_instances = 0 # Eliminates idle costs, adds 1-3s cold start
max_instances = 10

sf_user      = "IPENC"
sf_account   = "AMXUZTH-AWS_BRIDGE"
sf_warehouse = "SMALL_WH"
sf_database  = "MESHED_BUYCYCLE"
sf_schema    = "GROWTH"
sf_role      = "ACCOUNTADMIN"

# Training job resource sizing
# Using 8 vCPU to bypass Cloud Run platform quotas that affect lower tiers
# With strong override fix (PR #161), now consistently uses all 8 cores
# Higher vCPU tiers are scheduled onto less-constrained host pools
# Cost: ~$0.98/hour = ~$0.20 per 12-min benchmark job (vs $2.92 at 4 vCPU, 30-min)
# Performance: 2.5× faster than original 30-min runs = significant cost savings
training_cpu       = "8.0"
training_memory    = "32Gi"
training_max_cores = "8"  # Now consistently provides all 8 cores

# Google OAuth allowed domains (comma-separated)
# Example: allowed_domains = "mesheddata.com,example.com"
# Default: allowed_domains = "mesheddata.com"

