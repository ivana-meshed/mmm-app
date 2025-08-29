provider "google" {
  project = var.project_id
  region  = var.region
}
provider "google-beta" {
  project = var.project_id
  region  = var.region
}

#resource "google_artifact_registry_repository" "repo" {
#  location      = var.region
#  repository_id = "mmm-repo"
#  format        = "DOCKER"
#}



##############################################################
resource "google_service_account" "runner" {
  account_id   = "mmm-trainer-sa"
  display_name = "Service Account for MMM Trainer"
}
resource "google_service_account_iam_member" "allow_deployer_actas" {
  service_account_id = google_service_account.runner.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.deployer_sa}"
}

# If you prefer project-level role for Cloud Run deploys
resource "google_project_iam_member" "deployer_run_admin" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${var.deployer_sa}"
}

# NEW: allow the Cloud Run SA to pull images from Artifact Registry
resource "google_project_iam_member" "sa_ar_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${var.deployer_sa}" #member  = "serviceAccount:${google_service_account.runner.email}"
}

# Allow the SA to write to your bucket (bucket must already exist)
resource "google_storage_bucket_iam_member" "sa_writer" {
  bucket = var.bucket_name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${var.deployer_sa}" #"serviceAccount:${google_service_account.runner.email}"
}


resource "google_storage_bucket_iam_member" "runtime_sa_object_creator" {
  bucket = var.bucket_name
  role   = "roles/storage.objectCreator" # or "roles/storage.objectAdmin"
  member = "serviceAccount:mmm-trainer-sa@datawarehouse-422511.iam.gserviceaccount.com"
}

resource "google_storage_bucket_iam_member" "runtime_sa_object_admin" {
  bucket = var.bucket_name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runner.email}"
}

# Allow the runtime SA to mint signing tokens for itself (needed for V4 signed URLs)
resource "google_service_account_iam_member" "runner_token_creator" {
  service_account_id = google_service_account.runner.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.runner.email}"
}
# (Optional) fallback allow the deployer to sign on behalf of the runtime SA
resource "google_service_account_iam_member" "deployer_token_creator" {
  service_account_id = google_service_account.runner.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${var.deployer_sa}"
}
# (Recommended) make sure the IAM Credentials API is enabled
resource "google_project_service" "iamcredentials" {
  project            = var.project_id
  service            = "iamcredentials.googleapis.com"
  disable_on_destroy = false
}


resource "google_project_service" "run" {
  project            = var.project_id
  service            = "run.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "ar" {
  project            = var.project_id
  service            = "artifactregistry.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "cloudbuild" {
  project            = var.project_id
  service            = "cloudbuild.googleapis.com"
  disable_on_destroy = false
}


resource "google_cloud_run_service" "svc" {
  name     = var.service_name
  location = var.region

  template {
    metadata {
      annotations = {
        "run.googleapis.com/cpu-throttling" = "false"
        # NEW: Pre-warming configuration
        "run.googleapis.com/min-instances" = var.min_instances
        "run.googleapis.com/max-instances" = var.max_instances
        # Allocate CPU during startup for warming
        "run.googleapis.com/cpu-throttling" = "false"
        # Increase startup timeout for warming
        "run.googleapis.com/timeout" = "600s"
      }
    }

    spec {
      service_account_name  = google_service_account.runner.email
      container_concurrency = 8
      timeout_seconds       = 3600

      containers {
        image = var.image

        resources {
          limits = {
            cpu    = var.cpu_limit
            memory = var.memory_limit
          }
          requests = {
            cpu    = "2"   # Minimum for warming
            memory = "8Gi" # Minimum for warming
          }
        }

        # NEW: Extended startup probe for warming
        startup_probe {
          http_get {
            path = "/health" # Our health endpoint
            port = 8080
          }
          period_seconds        = 15 # Check every 15 seconds
          timeout_seconds       = 10 # 10 seconds per check
          failure_threshold     = 30 # Allow up to 7.5 minutes for warmup
          initial_delay_seconds = 10 # Wait 10 seconds before first check
        }

        # Liveness probe for running containers
        liveness_probe {
          http_get {
            path = "/health"
            port = 8080
          }
          period_seconds        = 60
          timeout_seconds       = 10
          failure_threshold     = 3
          initial_delay_seconds = 120 # Wait 2 minutes after startup
        }

        # ... [existing environment variables] ...

        # NEW: Warming configuration
        env {
          name  = "ENABLE_WARMING"
          value = "true"
        }

        env {
          name  = "WARMING_TIMEOUT"
          value = "100" # 5 minutes
        }
      }
    }
  }

  traffic {
    percent         = 100
    latest_revision = true
  }

  depends_on = [
    google_project_service.run,
    google_project_service.ar,
    google_project_service.cloudbuild,
    google_project_iam_member.deployer_run_admin,
    google_project_iam_member.sa_ar_reader,
  ]
}


# Add autoscaling configuration
resource "google_cloud_run_service_iam_member" "autoscaling_admin" {
  location = google_cloud_run_service.svc.location
  service  = google_cloud_run_service.svc.name
  role     = "roles/run.admin"
  member   = "serviceAccount:${google_service_account.runner.email}"
}

resource "google_cloud_run_service_iam_member" "invoker" {
  location = google_cloud_run_service.svc.location
  service  = google_cloud_run_service.svc.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "url" {
  value = google_cloud_run_service.svc.status[0].url
}
