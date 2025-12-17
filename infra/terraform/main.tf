provider "google" {
  project = var.project_id
  region  = var.region
}
provider "google-beta" {
  project = var.project_id
  region  = var.region
}

##############################################################
# Locals for computed values
##############################################################
locals {
  # Compute the Cloud Run service URL based on service name, region, and project
  # The hash suffix (wuepn6nq5a-ew) is stable for services in the same project/region
  # We use the actual Cloud Run URL pattern: https://<service>-<hash>-<region-abbr>.a.run.app
  # For this project in europe-west1, the hash is: wuepn6nq5a-ew
  web_service_url   = "https://${var.service_name}-web-wuepn6nq5a-ew.a.run.app"
  auth_redirect_uri = "${local.web_service_url}/oauth2callback"
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


# Snowflake private key secret (for environment-based config)
resource "google_secret_manager_secret" "sf_private_key" {
  secret_id = "sf-private-key"

  replication {
    user_managed {
      replicas {
        location = var.region # europe-west1
      }
    }
  }

  depends_on = [google_project_service.secretmanager]
}

resource "google_secret_manager_secret_version" "sf_private_key_version" {
  secret      = google_secret_manager_secret.sf_private_key.id
  secret_data = var.sf_private_key # From tfvars/CI
  enabled     = true
  depends_on  = [google_project_service.secretmanager]
}

# Grant web SA read access to environment-based key
resource "google_secret_manager_secret_iam_member" "sf_private_key_access" {
  secret_id = google_secret_manager_secret.sf_private_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.web_service_sa.email}"
}

# Persistent private key secret (user-uploaded keys)
resource "google_secret_manager_secret" "sf_private_key_persistent" {
  secret_id = "sf-private-key-persistent"

  replication {
    user_managed {
      replicas {
        location = var.region # europe-west1
      }
    }
  }

  depends_on = [google_project_service.secretmanager]
}

# Grant web SA permissions to read, add versions, and delete the persistent key secret
resource "google_secret_manager_secret_iam_member" "sf_private_key_persistent_accessor" {
  secret_id = google_secret_manager_secret.sf_private_key_persistent.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.web_service_sa.email}"
}

resource "google_project_iam_member" "sf_private_key_project_admin" {
  project = var.project_id
  role    = "roles/secretmanager.admin"
  member  = "serviceAccount:${google_service_account.web_service_sa.email}"
}
resource "google_secret_manager_secret_iam_member" "sf_private_key_persistent_version_adder" {
  secret_id = google_secret_manager_secret.sf_private_key_persistent.id
  role      = "roles/secretmanager.secretVersionAdder"
  member    = "serviceAccount:${google_service_account.web_service_sa.email}"
}

resource "google_secret_manager_secret_iam_member" "sf_private_key_persistent_version_manager" {
  secret_id = google_secret_manager_secret.sf_private_key_persistent.id
  role      = "roles/secretmanager.secretVersionManager"
  member    = "serviceAccount:${google_service_account.web_service_sa.email}"
}

# Allow web service to execute training jobs
#resource "google_service_account_iam_member" "web_service_job_executor" {
#  service_account_id = google_service_account.training_job_sa.name
#  role               = "roles/run.invoker"
#  member             = "serviceAccount:${google_service_account.web_service_sa.email}"
#}

# Allow web service to execute and monitor training jobs
# The run.admin role includes:
# - run.jobs.run (to execute jobs)
# - run.executions.get (to view execution status)
# - run.executions.list (to list executions)
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

resource "google_cloud_run_service_iam_member" "scheduler_can_invoke_web" {
  project  = var.project_id
  location = google_cloud_run_service.web_service.location
  service  = google_cloud_run_service.web_service.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

# Let the Terraform deployer impersonate (act as) the Scheduler SA
resource "google_service_account_iam_member" "allow_deployer_actas_scheduler" {
  service_account_id = google_service_account.scheduler.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.deployer_sa}"
}

# Allow the deployer to create/update Cloud Scheduler jobs
resource "google_project_iam_member" "deployer_scheduler_admin" {
  project = var.project_id
  role    = "roles/cloudscheduler.admin"
  member  = "serviceAccount:${var.deployer_sa}"
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

resource "google_secret_manager_secret" "auth_client_id" {
  secret_id = "streamlit-auth-client-id"
  replication {
    user_managed {
      replicas {
        location = var.region # europe-west1
      }
    }
  }
  depends_on = [google_project_service.secretmanager]
}

resource "google_secret_manager_secret_version" "auth_client_id_v" {
  secret      = google_secret_manager_secret.auth_client_id.id
  secret_data = var.auth_client_id
}

resource "google_secret_manager_secret" "auth_client_secret" {
  secret_id = "streamlit-auth-client-secret"
  replication {
    user_managed {
      replicas {
        location = var.region # europe-west1
      }
    }
  }
  depends_on = [google_project_service.secretmanager]
}

resource "google_secret_manager_secret_version" "auth_client_secret_v" {
  secret      = google_secret_manager_secret.auth_client_secret.id
  secret_data = var.auth_client_secret
}

resource "google_secret_manager_secret" "auth_cookie_secret" {
  secret_id = "streamlit-auth-cookie-secret"
  replication {
    user_managed {
      replicas {
        location = var.region # europe-west1
      }
    }
  }
  depends_on = [google_project_service.secretmanager]
}

resource "google_secret_manager_secret_version" "auth_cookie_secret_v" {
  secret      = google_secret_manager_secret.auth_cookie_secret.id
  secret_data = var.auth_cookie_secret
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
        #"deploy.kubernetes.io/revision-sha" = substr(var.web_image, length(var.web_image) - 40, 40)
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
          value = var.queue_name
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
          name  = "SF_PRIVATE_KEY_SECRET"
          value = google_secret_manager_secret.sf_private_key.secret_id
        }
        env {
          name  = "R_MAX_CORES"
          value = var.training_max_cores
        }
        env {
          name  = "SF_PERSISTENT_KEY_SECRET"
          value = google_secret_manager_secret.sf_private_key_persistent.secret_id
        }
        # Example for google_cloud_run_service or v2 resource; adapt to your existing block.
        # Add these env vars (or "secret env vars") so the container can read them
        env {
          name  = "AUTH_CLIENT_ID"
          value = "projects/${var.project_id}/secrets/streamlit-auth-client-id/versions/latest"
        }
        env {
          name  = "AUTH_CLIENT_SECRET"
          value = "projects/${var.project_id}/secrets/streamlit-auth-client-secret/versions/latest"
        }
        env {
          name  = "AUTH_COOKIE_SECRET"
          value = "projects/${var.project_id}/secrets/streamlit-auth-cookie-secret/versions/latest"
        }
        # The redirect URI is computed based on the service name and region
        # This ensures the correct redirect URI is used for both dev and prod environments
        env {
          name  = "AUTH_REDIRECT_URI"
          value = local.auth_redirect_uri
        }
        # Allowed domains for Google OAuth authentication (comma-separated)
        env {
          name  = "ALLOWED_DOMAINS"
          value = var.allowed_domains
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

resource "google_secret_manager_secret_iam_member" "web_sa_can_read_auth_id" {
  secret_id = google_secret_manager_secret.auth_client_id.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.web_service_sa.email}"
}
resource "google_secret_manager_secret_iam_member" "web_sa_can_read_auth_secret" {
  secret_id = google_secret_manager_secret.auth_client_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.web_service_sa.email}"
}
resource "google_secret_manager_secret_iam_member" "web_sa_can_read_cookie_secret" {
  secret_id = google_secret_manager_secret.auth_cookie_secret.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.web_service_sa.email}"
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

      # Note: Cloud Run v2 Jobs use Gen2 execution environment by default
      # Gen2 provides improved resource allocation and fewer platform quotas

      containers {
        name  = "training-container"
        image = var.training_image

        resources {
          limits = {
            cpu    = var.training_cpu    # Configurable: 8.0 (recommended) for better core allocation
            memory = var.training_memory # Configurable: 32Gi (recommended) or 16Gi for cost savings
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
          value = var.training_max_cores # Matches training_cpu
        }

        env {
          name  = "OMP_NUM_THREADS"
          value = var.training_max_cores
        }

        env {
          name  = "OPENBLAS_NUM_THREADS"
          value = var.training_max_cores
        }

        env {
          name  = "PROJECT_ID"
          value = var.project_id
        }

        env {
          name  = "REGION"
          value = var.region
        }

        # Enable automatic core diagnostics when core allocation is problematic
        # Set to "always" to force diagnostic output on every run
        # Set to "auto" (default) to only run when core discrepancy detected
        # Set to "never" to disable diagnostics
        env {
          name  = "ROBYN_DIAGNOSE_CORES"
          value = "auto"
        }

        # Override parallelly core detection (experimental)
        # When set, forces parallelly to use this many cores instead of auto-detection
        # This works around parallelly rejecting Cloud Run's cgroups quota format
        # Set to match training_max_cores (e.g., "8") to use all allocated vCPUs
        # Leave empty to use default auto-detection behavior
        env {
          name  = "PARALLELLY_OVERRIDE_CORES"
          value = var.training_max_cores
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
  name             = var.scheduler_job_name
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
    google_cloud_run_service.web_service,
    google_cloud_run_service_iam_member.scheduler_can_invoke_web
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
