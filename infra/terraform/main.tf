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


resource "google_cloud_run_v2_service" "svc" {
  provider = google-beta
  name     = var.service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  deletion_protection = false

  template {
    service_account = google_service_account.runner.email
    timeout         = "3600s"

    # Concurrency (v2 field)
    max_instance_request_concurrency = 64

    # Scaling replaces min/max instance annotations
    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    containers {
      image = var.image

      # Resources; cpu_idle=false disables CPU throttling in v2
      resources {
        limits = {
          cpu    = "8"
          memory = "32Gi"
        }
        cpu_idle = false
      }

      # ✅ STARTUP: consider the app "started" when HTTP / responds.
      # If you prefer to wait for Streamlit's stcore to mount,
      # point this to "/_stcore/health" and increase thresholds.
      startup_probe {
        tcp_socket { port = 8080 }
        period_seconds        = 2
        timeout_seconds       = 1
        failure_threshold     = 10
        initial_delay_seconds = 0
      }

      # ✅ LIVENESS: keep watching Streamlit’s health afterwards
      liveness_probe {
        http_get { path = "/_stcore/health" }
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

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  depends_on = [
    google_project_service.run,
    google_project_service.ar,
    google_project_iam_member.deployer_run_admin,
    google_project_iam_member.sa_ar_reader,
  ]
}


# Add autoscaling configuration
resource "google_cloud_run_v2_service_iam_member" "autoscaling_admin" {
  provider = google-beta
  location = google_cloud_run_v2_service.svc.location
  name     = google_cloud_run_v2_service.svc.name
  role     = "roles/run.admin"
  member   = "serviceAccount:${google_service_account.runner.email}"
}

# Public access (replace your v1 IAM resource)
resource "google_cloud_run_v2_service_iam_member" "invoker" {
  provider = google-beta
  name     = google_cloud_run_v2_service.svc.name
  location = google_cloud_run_v2_service.svc.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

output "url" {
  value = google_cloud_run_v2_service.svc.uri
}
