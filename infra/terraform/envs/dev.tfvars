project_id   = "datawarehouse-422511"
region       = "europe-west1"
service_name = "mmm-app-dev"
bucket_name  = "mmm-app-output"
#web_image      = "europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-web:${var.image_tag}"
#training_image = "europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-training:latest"
deployer_sa = "github-deployer@datawarehouse-422511.iam.gserviceaccount.com"

scheduler_job_name = "robyn-queue-tick-dev"
queue_name         = "default-dev"

# Scheduler control: Disabled – queue ticks are now triggered via Cloud Tasks
# (event-driven) instead of a periodic Cloud Scheduler, eliminating idle
# wake-ups when the queue is empty.
# Manual trigger still works: GET /?queue_tick=1&name=default-dev
scheduler_enabled = false  # Disabled in favour of event-driven Cloud Tasks
scheduler_interval_minutes = 30  # Unused (kept for reference)

# Cloud Tasks queue for event-driven queue tick processing
cloud_tasks_queue_name = "robyn-queue-tick-dev"
queue_tick_interval_seconds = 300  # Re-check running jobs every 5 minutes

# Cost optimization: Scale-to-zero configuration
min_instances = 0 # Eliminates idle costs, adds 1-3s cold start
max_instances = 10

# Snowflake (you can reuse prod or set dev values)
sf_user      = "IPENC"
sf_account   = "AMXUZTH-AWS_BRIDGE"
sf_warehouse = "SMALL_WH"
sf_database  = "MESHED_BUYCYCLE"
sf_schema    = "GROWTH"
sf_role      = "ACCOUNTADMIN"

# Training job resource sizing
# Testing with 8 vCPU to bypass Cloud Run platform quotas that affect lower tiers
# With strong override fix (PR #161), now consistently uses all 8 cores
# Higher vCPU tiers are scheduled onto less-constrained host pools
training_cpu       = "8.0"
training_memory    = "32Gi"
training_max_cores = "8"  # Now consistently provides all 8 cores

# Google OAuth allowed domains (comma-separated)
# Example: allowed_domains = "mesheddata.com,example.com"
# Default: allowed_domains = "mesheddata.com"

