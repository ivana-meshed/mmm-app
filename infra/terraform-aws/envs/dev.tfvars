aws_region      = "us-east-1"
environment     = "dev"
service_name    = "mmm-app-dev"
s3_bucket_name  = "mmm-app-output-aws-dev"

# Scheduler and queue configuration
scheduler_job_name = "robyn-queue-tick-dev"
queue_name         = "default-dev"

# Snowflake configuration (you can reuse prod or set dev values)
sf_user      = "IPENC"
sf_account   = "AMXUZTH-AWS_BRIDGE"
sf_warehouse = "SMALL_WH"
sf_database  = "MESHED_BUYCYCLE"
sf_schema    = "GROWTH"
sf_role      = "ACCOUNTADMIN"

# Resource sizing (can be smaller for dev)
web_cpu           = 1024  # 1 vCPU
web_memory        = 2048  # 2 GB
training_cpu      = 4096  # 4 vCPUs
training_memory   = 16384 # 16 GB
min_instances     = 1
max_instances     = 5

# Networking
vpc_cidr           = "10.0.0.0/16"
availability_zones = ["us-east-1a", "us-east-1b"]

# Google OAuth allowed domains (comma-separated)
# Example: allowed_domains = "mesheddata.com,example.com"
# Default: allowed_domains = "mesheddata.com"
