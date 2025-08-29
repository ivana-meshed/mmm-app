variable "project_id" { default = "datawarehouse-422511" }
variable "region" { default = "europe-west1" }
variable "service_name" { default = "mmm-trainer-sa" }
variable "image" { default = "europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-app:latest" }
variable "bucket_name" { default = "mmm-app-output" }
variable "deployer_sa" { default = "github-deployer@datawarehouse-422511.iam.gserviceaccount.com" }

# NEW: Resource sizing variables
variable "cpu_limit" {
  description = "CPU limit for Cloud Run service"
  type        = string
  default     = "16"
}

variable "memory_limit" {
  description = "Memory limit for Cloud Run service"
  type        = string
  default     = "32Gi"
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
