# MMM Trainer on Google Cloud – README

This repo deploys a **Streamlit** web app to **Cloud Run** that orchestrates an **R/Robyn** training pipeline.
Data is fetched from **Snowflake** with Python, passed as a CSV to R, and the model artifacts are written to **Google Cloud Storage**.
Infrastructure is managed with **Terraform**; the container image lives in **Artifact Registry**.

## High-level Architecture

- User accesses the Streamlit UI at the Cloud Run URL.
- Streamlit (Python) connects to Snowflake, exports a CSV snapshot, and calls `Rscript r/run_all.R job_cfg=/tmp/job.json`.
- R runs Robyn, using `reticulate` → system Python (`/usr/bin/python3`) for **nevergrad**.
- R uploads outputs (RDS, plots, one-pagers, metrics) to the GCS bucket.
- Logs from Python/R appear in the Streamlit UI and in **Cloud Logging**.

See the diagrams:
- **Architecture (boxes & arrows):** `mmm_architecture_v2.png`
- **Runtime sequence (swimlanes):** `mmm_sequence_v2.png`
- **Both pages PDF:** `mmm_system_design_v2.pdf`

## Repo Layout

```
app/
  streamlit_app.py          # Streamlit UI entry point
  config/
    settings.py             # Centralized configuration (env vars, GCP, Snowflake)
    __init__.py
  utils/
    gcs_utils.py            # GCS operations (upload, download, read/write)
    snowflake_connector.py  # Snowflake connection and query utilities
  pages/                    # Streamlit multi-page app
    Connect_Data.py       # Snowflake connection setup
    Map_Data.py           # Data mapping and metadata
    Review_Data.py        # Data validation
    3_Prepare_Training_Data.py  # Data preparation
    Run_Experiment.py     # Job configuration and execution
    5_View_Results.py       # Results visualization
    6_View_Best_Results.py  # Best model selection
  app_shared.py             # Shared helper functions
  data_processor.py         # Data optimization and Parquet conversion
  gcp_secrets.py            # Secret Manager integration
  snowflake_utils.py        # Backward compatibility wrapper
r/
  run_all.R                 # Robyn training entrypoint (reads job_cfg and csv_path)
infra/terraform/
  main.tf                   # Cloud Run, service accounts, IAM, storage
  variables.tf
  envs/
    prod.tfvars             # Production environment config
    dev.tfvars              # Development environment config
docker/
  Dockerfile.web            # Web service container
  Dockerfile.training       # Training job container
  Dockerfile.training-base  # Base image with R dependencies
  training_entrypoint.sh    # Training job entrypoint
.github/workflows/
  ci.yml                    # Production CI/CD (main branch)
  ci-dev.yml                # Development CI/CD (feat-*, dev branches)
tests/                      # Unit and integration tests
docs/                       # Additional documentation
```

For detailed architecture documentation, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Deploying to a New Company/Project

### Quick Start: Requirements

For a concise list of basic requirements to deploy and maintain this repository, see **[REQUIREMENTS.md](docs/REQUIREMENTS.md)**. This includes:

- Required tools and software (gcloud, Terraform, Docker, Git)
- Required accounts and access (GCP, GitHub, Snowflake)
- Required GCP APIs and resources
- Required credentials and secrets
- Ongoing maintenance considerations
- Quick reference deployment checklist

### Detailed Deployment Guide

For step-by-step instructions on deploying this application to a new Google Cloud project, see the **[Deployment Guide](docs/DEPLOYMENT_GUIDE.md)**. This comprehensive guide covers:

- GCP project setup and API enablement
- Workload Identity Federation configuration for GitHub Actions
- Service accounts and IAM permissions
- Artifact Registry and GCS bucket creation
- Google OAuth and Snowflake configuration
- GitHub repository secrets setup
- Terraform configuration and deployment
- Post-deployment verification and troubleshooting

## Prerequisites

- Google Cloud project with billing enabled
- `gcloud` CLI authenticated (`gcloud auth login` and `gcloud config set project <PROJECT_ID>`)
- Docker with Buildx enabled (`docker buildx create --use`)
- Terraform v1.5+
- Snowflake credentials for the data source
- A GCS bucket (e.g. `mmm-app-output`) to store artifacts

For a complete list of prerequisites, see [REQUIREMENTS.md](docs/REQUIREMENTS.md).

## Local Development

For detailed local development setup, testing, and troubleshooting, see **[DEVELOPMENT.md](DEVELOPMENT.md)**.

Quick start for local development:
```bash
# Clone and setup
git clone https://github.com/ivana-meshed/mmm-app.git
cd mmm-app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure GCP
gcloud auth application-default login
export PROJECT_ID=<your-project-id>
export GCS_BUCKET=mmm-app-output
export TRAINING_JOB_NAME=mmm-app-training

# Run Streamlit
streamlit run app/streamlit_app.py
```

## Local Run (Docker, optional)

```bash
# Build the container locally
docker build -t mmm-local -f docker/Dockerfile .

# Run locally
docker run --rm -p 8080:8080 mmm-local
# Open http://localhost:8080
```

## Build & Push Image (Artifact Registry)

```bash
export REGION=europe-west1
export PROJECT_ID=<your-project-id>
export REPO=mmm-repo
export IMAGE=${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/mmm-app:latest

gcloud auth configure-docker ${REGION}-docker.pkg.dev
docker buildx build --platform linux/amd64,linux/arm64 -t ${IMAGE} -f docker/Dockerfile --push .
```

## Deploy with Terraform

Edit `infra/terraform/terraform.tfvars`:
```hcl
project_id  = "<your-project-id>"
region      = "europe-west1"
bucket_name = "mmm-app-output"
image       = "europe-west1-docker.pkg.dev/<your-project-id>/mmm-repo/mmm-app:latest"
service_name = "mmm-trainer"
```
Then:
```bash
cd infra/terraform
terraform init
terraform apply -var-file="terraform.tfvars"
# Outputs: Cloud Run URL
```

### Service Account & IAM

- Cloud Run service MUST use the **mmm-trainer-sa** (or your chosen SA).
- Grant roles:
  - `roles/artifactregistry.reader` to pull images
  - `roles/storage.objectAdmin` on the artifact bucket (to upload Robyn outputs)
  - `roles/secretmanager.secretAccessor` to read persistent private keys
  - `roles/secretmanager.secretVersionAdder` to save persistent private keys
  - `roles/secretmanager.admin` to delete persistent private keys (optional, for "Clear Saved Key" feature)
- Terraform in `main.tf` configures these bindings.

For more details on persistent private key storage, see [docs/persistent_private_key.md](docs/persistent_private_key.md).

### Verifying SA on the revision

```bash
gcloud run services describe mmm-trainer \
  --region ${REGION} --format='value(spec.template.spec.serviceAccountName)'
```

## Streamlit App Usage

1. Open the Cloud Run URL shown by Terraform.
2. **Sign in** with your authorized Google account (see [Google Authentication](#google-authentication) below).
3. Fill in **Snowflake** connection info.
4. Provide your Snowflake private key (PEM format):
   - Upload a `.pem` file, or paste the key directly
   - Optionally check **"Save this key for future sessions"** to persist it in Google Secret Manager
   - In future sessions, saved keys are loaded automatically
5. Provide either a **table** (`DB.SCHEMA.TABLE`) or a **SQL query**.
6. (Optional) Upload `enriched_annotations.csv`.
7. Review/adjust variable mapping (spends/vars/context/factors/organic).
8. Click **Train**:
   - App pulls data → writes `/tmp/input_snapshot.csv` → invokes `Rscript r/run_all.R job_cfg=...`.
   - R uploads artifacts into `gs://<bucket>/robyn/<revision>/<country>/<timestamp>/`.
   - A `model_summary.json` file is automatically generated with candidate models, Pareto models, and performance metrics.

## Model Summaries

Every training run automatically generates a summary file (`model_summary.json`) capturing:
- All candidate models and their performance metrics
- Pareto optimal models (if any were identified)
- Best model information
- Training configuration and metadata

These summaries are stored in GCS alongside other run artifacts and can be aggregated by country for easy historical tracking.

**Learn more:** See [docs/MODEL_SUMMARY.md](docs/MODEL_SUMMARY.md) for detailed documentation on summary file structure, fields, and usage.

## Google Authentication

The application uses Google OAuth to restrict access to authorized email domains.

### Configuring Allowed Domains

By default, the application allows users from `mesheddata.com`. To add additional domains:

1. Update the `allowed_domains` variable in your Terraform configuration:
   ```hcl
   # In infra/terraform/envs/prod.tfvars
   allowed_domains = "mesheddata.com,example.com"
   ```

2. Apply the Terraform changes:
   ```bash
   cd infra/terraform
   terraform apply -var-file="envs/prod.tfvars"
   ```

For detailed instructions, see [docs/google_auth_domain_configuration.md](docs/google_auth_domain_configuration.md).

## Google Auth (GCS Uploads)

- On **Cloud Run**: the app uses the **service account** identity automatically (Application Default Credentials).
- Locally (if you must test R uploads): set `GOOGLE_APPLICATION_CREDENTIALS` to a JSON key and call `gcloud auth application-default login` or provide a key file.

## Reticulate / nevergrad

- Dockerfile pins `RETICULATE_PYTHON=/usr/bin/python3` and installs `numpy`, `scipy`, `nevergrad`.
- `run_all.R` checks `reticulate::py_config()` and imports `nevergrad` before running.
- If you see “Python shared library not found”, ensure `RETICULATE_AUTOCONFIGURE=0` and that `/usr/bin/python3` exists in the image.

## Streamlit on Cloud Run (CORS / 403)

- Entry point binds to `0.0.0.0` and uses `--server.port=$PORT` so Cloud Run can reach it.
- If you see a browser 403 due to proxy/CORS, set in `streamlit_app.py` (or a `config.toml`):
  ```python
  st.set_page_config(page_title="Robyn MMM Trainer", layout="wide")
  # Streamlit 1.30+ generally OK behind Cloud Run. For older versions, you can also set:
  # os.environ["STREAMLIT_SERVER_ENABLECORS"] = "false"
  # os.environ["STREAMLIT_SERVER_ENABLEWEBSOCKETCOMPRESSION"] = "true"
  ```

## API Access

The application provides REST-like API endpoints for programmatic access:

### Training API
Submit a training job programmatically:
```bash
curl "https://your-app-url.run.app/?api=train&country=fr&iterations=2000&trials=5"
```

### Status API
Check job status:
```bash
curl "https://your-app-url.run.app/?api=status&job_id=abc123"
```

### Metadata API
Retrieve metadata for a country:
```bash
curl "https://your-app-url.run.app/?api=metadata&country=fr&version=latest"
```

All API responses follow a standardized format:
```json
{
  "status": "success|error",
  "timestamp": "2025-11-18T14:00:00",
  "data": { ... },
  "message": "Optional message"
}
```

## Troubleshooting

- **Build fails at Robyn**: ensure `nloptr` and `patchwork>=1.3.1` are installed first; the Dockerfile handles this.
- **nevergrad not found**: verify `pip3 show nevergrad` inside container; ensure `RETICULATE_PYTHON` points to the same python.
- **GCS auth error**: confirm Cloud Run service account has `storage.objectAdmin` and *the revision* uses that SA.
- **Duplicate dates**: the R script collapses duplicates per day; ensure your SQL produces one row/day or let the script aggregate.
- **Training only uses 2 cores on 8 vCPU**: This is a known issue with the R `parallelly` package rejecting Cloud Run's cgroups quota. An override is implemented via the `PARALLELLY_OVERRIDE_CORES` environment variable. See [docs/PARALLELLY_OVERRIDE_FIX.md](docs/PARALLELLY_OVERRIDE_FIX.md) and [docs/TROUBLESHOOTING_PARALLELLY_OVERRIDE.md](docs/TROUBLESHOOTING_PARALLELLY_OVERRIDE.md) for details.

For more troubleshooting guidance, see [docs/DEPLOYMENT_GUIDE.md#troubleshooting](docs/DEPLOYMENT_GUIDE.md#troubleshooting).

## GCS Data Management Scripts

The repository includes scripts for managing test data and cleaning up the GCS bucket:

- **`scripts/collect_gcs_data_examples.py`** - Collect data structure examples from GCS
- **`scripts/generate_test_data.py`** - Generate synthetic test data
- **`scripts/upload_test_data.py`** - Upload test data to GCS
- **`scripts/delete_non_revision_data.py`** - Clean up non-revision data

For detailed documentation and usage instructions:
- [scripts/QUICKSTART.md](scripts/QUICKSTART.md) - Quick start guide
- [scripts/README_GCS_SCRIPTS.md](scripts/README_GCS_SCRIPTS.md) - Complete documentation

## Documentation

| Document | Description |
|----------|-------------|
| [README.md](README.md) | Project overview and quick start |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System architecture and components |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Local development setup and testing |
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) | **Basic requirements for deployment and maintenance** |
| [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md) | **Complete deployment guide for new projects** |
| [docs/MODEL_SUMMARY.md](docs/MODEL_SUMMARY.md) | Model summary file structure |
| [docs/OUTPUT_MODELS_PARQUET.md](docs/OUTPUT_MODELS_PARQUET.md) | OutputModels parquet data extraction |
| [docs/PARALLELLY_OVERRIDE_FIX.md](docs/PARALLELLY_OVERRIDE_FIX.md) | **Fix for 8 vCPU core allocation issue** |
| [docs/TROUBLESHOOTING_PARALLELLY_OVERRIDE.md](docs/TROUBLESHOOTING_PARALLELLY_OVERRIDE.md) | **Diagnostic checklist for parallelly override** |
| [docs/8_VCPU_TEST_RESULTS.md](docs/8_VCPU_TEST_RESULTS.md) | Analysis of 8 vCPU upgrade testing |
| [docs/google_auth_domain_configuration.md](docs/google_auth_domain_configuration.md) | OAuth domain configuration |
| [docs/persistent_private_key.md](docs/persistent_private_key.md) | Snowflake key storage |
| [COST_OPTIMIZATION.md](COST_OPTIMIZATION.md) | Cost optimization strategies |
| [scripts/README_GCS_SCRIPTS.md](scripts/README_GCS_SCRIPTS.md) | **GCS data management scripts** |
| [scripts/QUICKSTART.md](scripts/QUICKSTART.md) | **Quick start for data collection** |

## License

Apache-2.0 (or your preferred license).

