variable "project_id" { default = "datawarehouse-422511" }
variable "region" { default = "europe-west1" }
variable "service_name" { default = "mmm-trainer-sa" }
variable "image" { default = "europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-app:latest" }
variable "bucket_name" { default = "mmm-app-output" }
variable "deployer_sa" { default = "github-deployer@datawarehouse-422511.iam.gserviceaccount.com" }
