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

variable "min_instances" {
  description = "Minimum number of instances for pre-warming"
  type        = number
  default     = 2
}

variable "max_instances" {
  description = "Maximum number of instances for scaling"
  type        = number
  default     = 10
}

variable "sf_password" {
  type      = string
  sensitive = true
  default   = null # set via TF var or CI, or skip and add secret version via gcloud in CI
}



