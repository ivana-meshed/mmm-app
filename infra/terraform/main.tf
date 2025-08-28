provider "google" {
  project = var.project_id
  region  = var.region
}

#resource "google_artifact_registry_repository" "repo" {
#  location      = var.region
#  repository_id = "mmm-repo"
#  format        = "DOCKER"
#}

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
        # Enable CPU allocation during request processing only
        "run.googleapis.com/cpu-throttling" = "false"
        # Set minimum instances for pre-warming (see section 3)
        "run.googleapis.com/min-instances" = "2"
        # Set maximum instances for scaling
        "run.googleapis.com/max-instances" = "10"
      }
    }

    spec {
      service_account_name  = google_service_account.runner.email
      container_concurrency = 1    # Keep at 1 for resource-intensive training
      timeout_seconds       = 3600 # 1 hour timeout

      containers {
        image = var.image

        # UPGRADED RESOURCE ALLOCATION
        resources {
          limits = {
            cpu    = "8"    # ⬆️ Increased from "4"
            memory = "32Gi" # ⬆️ Increased from "16Gi"
          }
          requests = {
            cpu    = "4"    # Minimum guaranteed CPU
            memory = "16Gi" # Minimum guaranteed memory
          }
        }

        # Startup probe with longer timeout for heavy containers
        startup_probe {
          tcp_socket { port = 8080 }
          period_seconds        = 30 # Check every 30 seconds
          timeout_seconds       = 10 # Wait 10 seconds per check
          failure_threshold     = 20 # Allow up to 10 minutes for startup
          initial_delay_seconds = 30 # Wait 30 seconds before first check
        }

        # Liveness probe for running containers
        liveness_probe {
          http_get {
            path = "/health" # We'll implement this endpoint
            port = 8080
          }
          period_seconds    = 60
          timeout_seconds   = 30
          failure_threshold = 3
        }

        env {
          name  = "GCS_BUCKET"
          value = var.bucket_name
        }

        env {
          name  = "APP_ROOT"
          value = "/app"
        }

        env {
          name  = "RUN_SERVICE_ACCOUNT_EMAIL"
          value = google_service_account.runner.email
        }

        # NEW: Performance optimization flags
        env {
          name  = "R_MAX_CORES"
          value = "8" # Use all available CPU cores
        }

        env {
          name  = "OPENBLAS_NUM_THREADS"
          value = "8" # Optimize BLAS operations
        }

        env {
          name  = "OMP_NUM_THREADS"
          value = "8" # OpenMP parallelization
        }

        # Enable parallel processing in Python
        env {
          name  = "PYTHONUNBUFFERED"
          value = "1"
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
