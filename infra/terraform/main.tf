provider "google" {
  project = var.project_id
  region  = var.region
}
provider "google-beta" {
  project = var.project_id
  region  = var.region
}

##############################################################
# Service Accounts
##############################################################
resource "google_service_account" "web_service_sa" {
  account_id   = "mmm-web-service-sa"
  display_name = "Service Account for MMM Web Service"
}

resource "google_service_account" "training_job_sa" {
  account_id   = "mmm-training-job-sa"
  display_name = "Service Account for MMM Training Jobs"
}

# Allow web service to execute training jobs
#resource "google_service_account_iam_member" "web_service_job_executor" {
#  service_account_id = google_service_account.training_job_sa.name
#  role               = "roles/run.invoker"
#  member             = "serviceAccount:${google_service_account.web_service_sa.email}"
#}

resource "google_project_iam_member" "web_service_job_admin" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.web_service_sa.email}"
}

# Allow deployer to use both service accounts
resource "google_service_account_iam_member" "allow_deployer_actas_web" {
  service_account_id = google_service_account.web_service_sa.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.deployer_sa}"
}

resource "google_service_account_iam_member" "allow_deployer_actas_training" {
  service_account_id = google_service_account.training_job_sa.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.deployer_sa}"
}

# Deployer permissions
resource "google_project_iam_member" "deployer_run_admin" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${var.deployer_sa}"
}

# Artifact Registry access for both SAs
resource "google_project_iam_member" "web_sa_ar_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.web_service_sa.email}"
}

resource "google_project_iam_member" "training_sa_ar_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.training_job_sa.email}"
}

# GCS bucket access for both SAs
resource "google_storage_bucket_iam_member" "web_sa_bucket_access" {
  bucket = var.bucket_name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.web_service_sa.email}"
}

resource "google_storage_bucket_iam_member" "training_sa_bucket_access" {
  bucket = var.bucket_name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.training_job_sa.email}"
}
# needs provider google or google-beta >= recent
resource "google_artifact_registry_repository_iam_member" "web_ar_reader" {
  project    = var.project_id
  location   = var.region
  repository = "mmm-repo"
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.web_service_sa.email}"
}

resource "google_artifact_registry_repository_iam_member" "train_ar_reader" {
  project    = var.project_id
  location   = var.region
  repository = "mmm-repo"
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.training_job_sa.email}"
}

# Token creator permissions for signed URLs
resource "google_service_account_iam_member" "web_sa_token_creator" {
  service_account_id = google_service_account.web_service_sa.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.web_service_sa.email}"
}

resource "google_service_account_iam_member" "training_sa_token_creator" {
  service_account_id = google_service_account.training_job_sa.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.training_job_sa.email}"
}

##############################################################
# APIs
##############################################################
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

resource "google_project_service" "iamcredentials" {
  project            = var.project_id
  service            = "iamcredentials.googleapis.com"
  disable_on_destroy = false
}

##############################################################
# Cloud Run Service (Streamlit Web Interface)
##############################################################
resource "google_cloud_run_service" "web_service" {
  name     = "${var.service_name}-web"
  location = var.region

  template {
    metadata {
      annotations = {
        "run.googleapis.com/cpu-throttling" = "false"
        "run.googleapis.com/min-instances"  = var.min_instances
        "run.googleapis.com/max-instances"  = var.max_instances
        "run.googleapis.com/timeout"        = "300s"
      }
    }

    spec {
      service_account_name  = google_service_account.web_service_sa.email
      container_concurrency = 10
      timeout_seconds       = 300

      containers {
        image = var.web_image

        resources {
          limits = {
            cpu    = "2.0"
            memory = "4Gi"
          }
          requests = {
            cpu    = "1.0"
            memory = "2Gi"
          }
        }

        ports {
          container_port = 8080
        }

        env {
          name  = "GCS_BUCKET"
          value = var.bucket_name
        }

        env {
          name  = "TRAINING_JOB_NAME"
          value = google_cloud_run_v2_job.training_job.name
        }

        env {
          name  = "PROJECT_ID"
          value = var.project_id
        }

        env {
          name  = "REGION"
          value = var.region
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
  ]
}

##############################################################
# Cloud Run Job v2 (Heavy Training Workload)
##############################################################
resource "google_cloud_run_v2_job" "training_job" {
  name     = "${var.service_name}-training"
  location = var.region

  template {
    template {
      service_account = google_service_account.training_job_sa.email
      max_retries     = 1

      containers {
        name  = "training-container"
        image = var.training_image

        resources {
          limits = {
            cpu    = "8.0"  # Maximum available CPUs
            memory = "32Gi" # Maximum available memory
          }
        }

        env {
          name  = "GCS_BUCKET"
          value = var.bucket_name
        }

        env {
          name  = "R_MAX_CORES"
          value = "8"
        }

        env {
          name  = "OMP_NUM_THREADS"
          value = "8"
        }

        env {
          name  = "OPENBLAS_NUM_THREADS"
          value = "8"
        }

        env {
          name  = "PROJECT_ID"
          value = var.project_id
        }

        env {
          name  = "REGION"
          value = var.region
        }
      }
    }
  }

  depends_on = [
    google_project_service.run,
    google_project_service.ar,
  ]
}
resource "google_service_account_iam_member" "web_can_act_as_training_sa" {
  service_account_id = google_service_account.training_job_sa.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.web_service_sa.email}"
}
resource "google_cloud_run_v2_job_iam_member" "training_job_runner" {
  provider = google-beta
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.training_job.name
  role     = "roles/run.developer" # includes run.jobs.run
  member   = "serviceAccount:${google_service_account.web_service_sa.email}"
}

##############################################################
# IAM for public access
##############################################################
resource "google_cloud_run_service_iam_member" "web_service_invoker" {
  location = google_cloud_run_service.web_service.location
  service  = google_cloud_run_service.web_service.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

##############################################################
# Outputs
##############################################################
output "web_service_url" {
  value = google_cloud_run_service.web_service.status[0].url
}

output "training_job_name" {
  value = google_cloud_run_v2_job.training_job.name
}
