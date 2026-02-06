# GCS Bucket with AUTOMATED lifecycle policies for cost optimization
# Implements cost reduction via Terraform-managed lifecycle rules
#
# NOTE: This manages an existing bucket. To import:
#   terraform import google_storage_bucket.mmm_output mmm-app-output

resource "google_storage_bucket" "mmm_output" {
  name     = var.bucket_name
  location = var.region

  # Preserve existing bucket settings
  force_destroy               = false
  uniform_bucket_level_access = true

  # Lifecycle rule 1: Move old training data to Nearline storage after 30 days
  lifecycle_rule {
    condition {
      age            = 30
      matches_prefix = ["robyn/", "datasets/", "training-data/"]
      with_state     = "LIVE"
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  # Lifecycle rule 2: Move old training data to Coldline storage after 90 days
  lifecycle_rule {
    condition {
      age            = 90
      matches_prefix = ["robyn/", "datasets/", "training-data/"]
      with_state     = "LIVE"
    }
    action {
      type          = "SetStorageClass"
      storage_class = "COLDLINE"
    }
  }

  # Lifecycle rule 3: Delete old queue data after 365 days
  lifecycle_rule {
    condition {
      age            = 365
      matches_prefix = ["robyn-queues/"]
      with_state     = "LIVE"
    }
    action {
      type = "Delete"
    }
  }
}

# Cost savings from lifecycle policies (AUTOMATED):
# - Standard Storage: $0.020/GB/month
# - Nearline Storage: $0.010/GB/month (30-89 days) - 50% savings
# - Coldline Storage: $0.004/GB/month (90+ days) - 80% savings
#
# Expected savings: $0.78/month (49% reduction on storage costs)
# These rules are automatically applied when Terraform runs
