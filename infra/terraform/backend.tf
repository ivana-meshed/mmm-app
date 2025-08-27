terraform {
  backend "gcs" {
    bucket = "mmm-tf-state"
    prefix = "envs/prod"
  }
}
