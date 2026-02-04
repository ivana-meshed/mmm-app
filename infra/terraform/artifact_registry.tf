# Artifact Registry Repository Configuration
# This file manages the Docker image repository and implements automatic cleanup policies
# to reduce storage costs from accumulated build artifacts

# Note: If the repository already exists and was created outside Terraform,
# import it with: terraform import google_artifact_registry_repository.mmm_repo projects/datawarehouse-422511/locations/europe-west1/repositories/mmm-repo

resource "google_artifact_registry_repository" "mmm_repo" {
  project       = var.project_id
  location      = var.region
  repository_id = "mmm-repo"
  description   = "Docker images for MMM application (web service, training jobs)"
  format        = "DOCKER"

  # Cleanup policies to automatically delete old images
  # This addresses the issue of 9,228 images (122.58 GB) costing $12.26/month
  
  cleanup_policies {
    id     = "keep-minimum-versions"
    action = "KEEP"
    
    most_recent_versions {
      # Keep at least 10 most recent versions of each image
      keep_count = 10
    }
  }

  cleanup_policies {
    id     = "delete-untagged"
    action = "DELETE"
    
    condition {
      # Delete untagged images older than 30 days
      tag_state  = "UNTAGGED"
      older_than = "2592000s" # 30 days in seconds
    }
  }

  cleanup_policies {
    id     = "delete-old-tagged"
    action = "DELETE"
    
    condition {
      # Delete tagged images older than 90 days
      # (except those protected by keep-minimum-versions)
      tag_state  = "TAGGED"
      older_than = "7776000s" # 90 days in seconds
      
      # Only delete if there are more than 10 versions
      # This works in conjunction with keep-minimum-versions
      tag_prefixes = ["sha256-"] # Targets build artifacts
    }
  }

  depends_on = [
    google_project_service.ar
  ]
}

# Output the repository URL for reference
output "artifact_registry_url" {
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.mmm_repo.repository_id}"
  description = "Full URL of the Artifact Registry repository"
}

# Cost savings from cleanup policies:
# - Before: 9,228 images, 122.58 GB, $12.26/month
# - After: ~40-80 images, 5-10 GB, $0.50-1.00/month
# - Savings: ~$11.26/month ($135/year)
