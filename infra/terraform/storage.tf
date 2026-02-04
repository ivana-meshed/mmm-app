# GCS Bucket with lifecycle policies for cost optimization
# This file implements cost reduction strategy #2 from Cost estimate.csv

# Note: If the bucket already exists and was created outside Terraform,
# import it with: terraform import google_storage_bucket.mmm_output mmm-app-output

resource "google_storage_bucket" "mmm_output" {
  name     = var.bucket_name
  location = var.region
  project  = var.project_id

  # Uniform bucket-level access for better security and management
  uniform_bucket_level_access {
    enabled = true
  }

  # Lifecycle rules for automatic cost optimization
  lifecycle_rule {
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
    condition {
      age                   = 30
      matches_prefix        = ["training_data/"]
    }
  }

  lifecycle_rule {
    action {
      type          = "SetStorageClass"
      storage_class = "COLDLINE"
    }
    condition {
      age                   = 90
      matches_prefix        = ["training_data/"]
    }
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age                   = 180
      matches_prefix        = [
        "training_data/de/",
        "training_data/fr/",
        "training_data/es/"
      ]
    }
  }

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age                   = 7
      matches_prefix        = ["training-configs/"]
    }
  }

  depends_on = [
    google_project_service.storage
  ]
}

# Enable Cloud Storage API if not already enabled
resource "google_project_service" "storage" {
  project            = var.project_id
  service            = "storage-component.googleapis.com"
  disable_on_destroy = false
}

# Output bucket name for reference
output "gcs_bucket_name" {
  value       = google_storage_bucket.mmm_output.name
  description = "Name of the GCS bucket for MMM application data"
}

# Cost savings from lifecycle policies:
# - Standard Storage: $0.020/GB/month
# - Nearline Storage: $0.010/GB/month (30-89 days) - 50% savings
# - Coldline Storage: $0.004/GB/month (90+ days) - 80% savings
#
# For 28.74GB current storage with minimal access to old data:
# - Before: 28.74GB × $0.020 = $0.57/month
# - After (assuming 10GB hot, 10GB nearline, 8.74GB coldline):
#   10GB × $0.020 + 10GB × $0.010 + 8.74GB × $0.004 = $0.20 + $0.10 + $0.03 = $0.33/month
# - Savings: $0.24/month ($2.88/year)
