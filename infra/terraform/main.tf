provider "google" {
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
        "run.googleapis.com/min-instances"  = var.min_instances
        "run.googleapis.com/max-instances"  = var.max_instances
        # Add session affinity to reduce rate limiting
        "run.googleapis.com/sessionAffinity" = "true"
        # Increase timeout
        "run.googleapis.com/timeout" = "3600s"
      }
    }

    spec {
      service_account_name  = google_service_account.runner.email
      container_concurrency = 1 # Important: Keep at 1 for training jobs
      timeout_seconds       = 3600

      containers {
        image = var.image

        resources {
          limits = {
            cpu    = "8"
            memory = "32Gi"
          }
          requests = {
            cpu    = "4"
            memory = "16Gi"
          }
        }

        # Improved startup probe
        startup_probe {
          http_get {
            path = "/" # Use root path instead of /health for now
            port = 8080
          }
          period_seconds        = 10
          timeout_seconds       = 8
          failure_threshold     = 30
          initial_delay_seconds = 10
        }

        # Liveness probe
        liveness_probe {
          http_get {
            path = "/"
            port = 8080
          }
          period_seconds        = 60
          timeout_seconds       = 10
          failure_threshold     = 3
          initial_delay_seconds = 120
        }

        # Environment variables
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

        env {
          name  = "R_MAX_CORES"
          value = "8"
        }

        env {
          name  = "OPENBLAS_NUM_THREADS"
          value = "8"
        }

        env {
          name  = "OMP_NUM_THREADS"
          value = "8"
        }

        env {
          name  = "PYTHONUNBUFFERED"
          value = "1"
        }

        # Add Streamlit specific config
        env {
          name  = "STREAMLIT_SERVER_HEADLESS"
          value = "true"
        }

        env {
          name  = "STREAMLIT_SERVER_ENABLE_CORS"
          value = "false"
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
