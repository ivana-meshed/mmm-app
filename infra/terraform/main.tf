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

resource "google_service_account" "scheduler" {
  account_id   = "robyn-queue-scheduler"
  display_name = "Robyn Queue Scheduler"
}

data "google_secret_manager_secret" "sf_password" {
  project   = var.project_id
  secret_id = "sf-password"
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

# Allow Scheduler SA to invoke your web Cloud Run service
resource "google_cloud_run_service_iam_member" "web_invoker" {
  service  = google_cloud_run_service_iam_member.web_service_sa.name
  project  = google_cloud_run_service_iam_member.web_service_sa.project
  location = google_cloud_run_service_iam_member.web_service_sa.location
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_secret_manager_secret_iam_member" "sf_password_access" {
  secret_id = data.google_secret_manager_secret.sf_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.web_service_sa.email}"
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

resource "google_project_service" "scheduler" {
  project            = var.project_id
  service            = "cloudscheduler.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "secretmanager" {
  project            = var.project_id
  service            = "secretmanager.googleapis.com"
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

        env {
          name  = "DEFAULT_QUEUE_NAME"
          value = "default"
        }
        env {
          name  = "QUEUE_ROOT"
          value = "robyn-queues"
        }
        env {
          name  = "SAFE_LAG_SECONDS_AFTER_RUNNING"
          value = "5"
        }

        env {
          name  = "SF_USER"
          value = var.sf_user
        }

        env {
          name  = "SF_ACCOUNT"
          value = var.sf_account
        }

        env {
          name  = "SF_WAREHOUSE"
          value = var.sf_warehouse
        }

        env {
          name  = "SF_DATABASE"
          value = var.sf_database
        }

        env {
          name  = "SF_SCHEMA"
          value = var.sf_schema
        }

        env {
          name  = "SF_ROLE"
          value = var.sf_role
        }
        env {
          name = "SF_PASSWORD"
          value_from {
            secret_key_ref {
              name = data.google_secret_manager_secret.sf_password.secret_id
              key  = "latest"
            }
          }
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
      timeout         = "21600s"
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
          name  = "JOB_CONFIG_GCS_PATH"
          value = "gs://${var.bucket_name}/training-configs/latest/job_config.json"
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


###############################################################
# Scheduler to trigger queue ticks
###############################################################

resource "google_cloud_scheduler_job" "robyn_queue_tick" {
  name             = "robyn-queue-tick"
  description      = "Advance Robyn training queue (headless)"
  schedule         = "*/1 * * * *" # every minute
  time_zone        = "Etc/UTC"
  attempt_deadline = "320s"

  http_target {
    http_method = "GET"
    uri         = "${google_cloud_run_service.web_service.status[0].url}?queue_tick=1&name=${var.queue_name}"
    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = google_cloud_run_service.web_service.status[0].url
    }
  }

  depends_on = [
    google_project_service.scheduler,
    google_cloud_run_v2_service_iam_member.web_invoker
  ]
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
