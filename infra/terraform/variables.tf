variable "project_id" { default = "datawarehouse-422511" }
variable "region" { default = "europe-west1" }
variable "service_name" { default = "mmm-trainer-sa" }
#variable "image" { default = "europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-app:latest" }
variable "bucket_name" { default = "mmm-app-output" }
variable "web_image" { default = "europe-west1-docker.pkg.dev@datawarehouse-422511.iam.gserviceaccount.com" }
variable "training_image" { default = "europe-west1-docker.pkg.dev@datawarehouse-422511.iam.gserviceaccount.com" }
variable "deployer_sa" { default = "github-deployer@datawarehouse-422511.iam.gserviceaccount.com" }

variable "sf_user" {
  type    = string
  default = "IPENC"
}
variable "sf_account" {
  type    = string
  default = "AMXUZTH-AWS_BRIDGE"
}
variable "sf_warehouse" {
  type    = string
  default = "SMALL_WH"
}
variable "sf_database" {
  type    = string
  default = "MESHED_BUYCYCLE"
}
variable "sf_schema" {
  type    = string
  default = "GROWTH"
}
variable "sf_role" {
  type    = string
  default = "ACCOUNTADMIN"
}

variable "scheduler_job_name" {
  type    = string
  default = "robyn-queue-tick"
} # dev: "robyn-queue-tick-dev"

variable "queue_name" {
  type    = string
  default = "default"
}

# NEW: Resource sizing variables
variable "cpu_limit" {
  description = "CPU limit for Cloud Run service"
  type        = string
  default     = "8"
}

variable "memory_limit" {
  description = "Memory limit for Cloud Run service"
  type        = string
  default     = "8Gi"
}

# Training job resource sizing variables
variable "training_cpu" {
  description = "CPU limit for training job (4.0 recommended for 50% cost savings, 2.0 for 75%)"
  type        = string
  default     = "4.0"
}

variable "training_memory" {
  description = "Memory limit for training job (16Gi recommended, 8Gi for maximum savings)"
  type        = string
  default     = "16Gi"
}

variable "training_max_cores" {
  description = "Maximum cores for R/Robyn training (should match training_cpu)"
  type        = string
  default     = "4"
}

variable "min_instances" {
  description = "Minimum number of instances for pre-warming. Set to 0 to eliminate idle costs (adds cold start latency)"
  type        = number
  default     = 0
}

variable "max_instances" {
  description = "Maximum number of instances for scaling"
  type        = number
  default     = 10
}

variable "sf_private_key" {
  type        = string
  sensitive   = true
  description = "RSA private key PEM for Snowflake"
}

variable "auth_cookie_secret" {
  type      = string
  sensitive = true
}
variable "auth_client_id" {
  type      = string
  sensitive = true
}
variable "auth_client_secret" {
  type      = string
  sensitive = true
}

variable "allowed_domains" {
  type        = string
  default     = "mesheddata.com, buycycle.com"
  description = "Comma-separated list of allowed email domains for Google OAuth authentication"
}
