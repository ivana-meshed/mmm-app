aws_region      = "us-east-1"
environment     = "prod"
service_name    = "mmm-app"
s3_bucket_name  = "mmm-app-output-aws"

# Scheduler and queue configuration
scheduler_job_name = "robyn-queue-tick"
queue_name         = "default"

# Snowflake configuration
sf_user      = "IPENC"
sf_account   = "AMXUZTH-AWS_BRIDGE"
sf_warehouse = "SMALL_WH"
sf_database  = "MESHED_BUYCYCLE"
sf_schema    = "GROWTH"
sf_role      = "ACCOUNTADMIN"

# Resource sizing
web_cpu           = 2048  # 2 vCPUs
web_memory        = 4096  # 4 GB
training_cpu      = 8192  # 8 vCPUs
training_memory   = 32768 # 32 GB
min_instances     = 2
max_instances     = 10

# Networking
vpc_cidr           = "10.0.0.0/16"
availability_zones = ["us-east-1a", "us-east-1b"]

# Google OAuth allowed domains (comma-separated)
# Example: allowed_domains = "mesheddata.com,example.com"
# Default: allowed_domains = "mesheddata.com"
