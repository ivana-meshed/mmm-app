variable "aws_region" {
  description = "AWS region for resources"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name (prod, dev)"
  type        = string
  default     = "prod"
}

variable "service_name" {
  description = "Base name for all services"
  type        = string
  default     = "mmm-app"
}

variable "s3_bucket_name" {
  description = "S3 bucket name for application data"
  type        = string
  default     = "mmm-app-output"
}

variable "web_image" {
  description = "Docker image URI for web service"
  type        = string
}

variable "training_image" {
  description = "Docker image URI for training job"
  type        = string
}

variable "web_cpu" {
  description = "CPU units for web service (1024 = 1 vCPU)"
  type        = number
  default     = 2048
}

variable "web_memory" {
  description = "Memory for web service in MiB"
  type        = number
  default     = 4096
}

variable "training_cpu" {
  description = "CPU units for training task (1024 = 1 vCPU)"
  type        = number
  default     = 8192
}

variable "training_memory" {
  description = "Memory for training task in MiB"
  type        = number
  default     = 32768
}

variable "min_instances" {
  description = "Minimum number of web service tasks"
  type        = number
  default     = 2
}

variable "max_instances" {
  description = "Maximum number of web service tasks"
  type        = number
  default     = 10
}

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

variable "scheduler_job_name" {
  type    = string
  default = "robyn-queue-tick"
}

variable "queue_name" {
  type    = string
  default = "default"
}

variable "vpc_cidr" {
  description = "CIDR block for VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "Availability zones for resources"
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]
}
