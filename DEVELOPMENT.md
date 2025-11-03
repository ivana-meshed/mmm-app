# Local Development Guide

This guide provides step-by-step instructions for setting up and testing the MMM Streamlit application locally in a development environment.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Repository Structure](#repository-structure)
- [Quick Start](#quick-start)
- [Detailed Setup](#detailed-setup)
  - [1. Environment Setup](#1-environment-setup)
  - [2. Python Dependencies](#2-python-dependencies)
  - [3. Google Cloud Setup](#3-google-cloud-setup)
  - [4. Snowflake Configuration](#4-snowflake-configuration)
  - [5. Running Locally](#5-running-locally)
- [Docker Development](#docker-development)
  - [Building Images](#building-images)
  - [Running Containers](#running-containers)
- [Testing](#testing)
- [CI/CD Pipeline](#cicd-pipeline)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

Before starting, ensure you have the following installed:

### Required Software
- **Python 3.11+**: Main application language
- **Docker Desktop**: For containerized development
  - Enable Buildx: `docker buildx create --use`
- **R 4.3+**: For Robyn training (optional for web-only dev)
- **Google Cloud SDK (gcloud)**: For GCP integration
  - Install: https://cloud.google.com/sdk/docs/install
- **Terraform 1.5+**: For infrastructure management
- **Git**: Version control

### Optional Tools
- **make**: For using Makefile commands
- **VSCode** or **PyCharm**: Recommended IDEs

---

## Repository Structure

```
mmm-app/
├── app/                          # Streamlit application code
│   ├── streamlit_app.py         # Main web interface
│   ├── pages/                   # Streamlit multi-page app
│   ├── app_shared.py            # Shared utilities
│   ├── data_processor.py        # Data processing logic
│   ├── snowflake_utils.py       # Snowflake connectivity
│   └── gcp_secrets.py           # GCP Secret Manager integration
├── r/                           # R training scripts
│   ├── run_all.R                # Main Robyn training script
│   └── helpers.R                # R utility functions
├── docker/                      # Docker configurations
│   ├── Dockerfile.web           # Web service image
│   ├── Dockerfile.training      # Training job image
│   ├── Dockerfile.training-base # Base image for training
│   ├── web_entrypoint.sh        # Web service entrypoint
│   └── training_entrypoint.sh   # Training job entrypoint
├── infra/terraform/             # Infrastructure as Code
│   ├── main.tf                  # Main Terraform configuration
│   ├── variables.tf             # Variable definitions
│   ├── terraform.tfvars         # Production values
│   └── envs/                    # Environment-specific configs
├── .github/workflows/           # CI/CD pipelines
│   ├── ci.yml                   # Production deployment
│   └── ci-dev.yml               # Development deployment
├── requirements.txt             # Python dependencies
├── Makefile                     # Development commands
└── README.md                    # Project overview
```

---

## Quick Start

For a rapid local setup (web interface only, no training):

```bash
# 1. Clone the repository
git clone https://github.com/ivana-meshed/mmm-app.git
cd mmm-app

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up Google Cloud authentication
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID

# 5. Set environment variables
export PROJECT_ID=YOUR_PROJECT_ID
export REGION=europe-west1
export GCS_BUCKET=mmm-app-output
export TRAINING_JOB_NAME=mmm-app-training

# 6. Run Streamlit locally
streamlit run app/streamlit_app.py
```

The app will open at http://localhost:8501

---

## Detailed Setup

### 1. Environment Setup

#### Clone Repository
```bash
git clone https://github.com/ivana-meshed/mmm-app.git
cd mmm-app
```

#### Create Python Virtual Environment
```bash
# Create virtual environment
python3 -m venv .venv

# Activate virtual environment
source .venv/bin/activate  # Linux/macOS
# OR
.venv\Scripts\activate     # Windows PowerShell
```

#### Install Development Tools (Optional)
```bash
# Install code quality tools
pip install black pylint flake8 mypy isort

# Install testing tools (if tests exist)
pip install pytest pytest-cov
```

---

### 2. Python Dependencies

#### Install Core Dependencies
```bash
# Install all required packages
pip install -r requirements.txt
```

#### Verify Installation
```bash
# Test critical imports
python3 -c "
import streamlit
import pandas
import snowflake.connector
from google.cloud import storage, secret_manager, run_v2
print('✅ All dependencies installed successfully')
"
```

---

### 3. Google Cloud Setup

#### Authenticate with Google Cloud
```bash
# Login to Google Cloud
gcloud auth login

# Set up Application Default Credentials (ADC)
gcloud auth application-default login

# Set default project
gcloud config set project YOUR_PROJECT_ID

# Verify authentication
gcloud auth list
```

#### Set Environment Variables
Create a `.env` file or export variables:

```bash
# Core GCP Configuration
export PROJECT_ID=YOUR_PROJECT_ID
export REGION=europe-west1
export GCS_BUCKET=mmm-app-output

# Cloud Run Configuration
export TRAINING_JOB_NAME=mmm-app-dev-training
export SERVICE_NAME=mmm-app-dev

# Optional: For local development
export GOOGLE_APPLICATION_CREDENTIALS="pat/to/cred.json"  # Uses ADC
```

#### Create GCS Bucket (if needed)
```bash
# Create bucket for outputs
gcloud storage buckets create gs://${GCS_BUCKET} \
  --location=${REGION} \
  --uniform-bucket-level-access

# Verify bucket exists
gcloud storage buckets list
```

#### Enable Required APIs
```bash
# Enable necessary Google Cloud services
gcloud services enable \
  run.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com
```

---

### 4. Snowflake Configuration

The app requires Snowflake credentials for data access.

#### Option A: Using Environment Variables
```bash
export SNOWFLAKE_ACCOUNT=your_account
export SNOWFLAKE_USER=your_username
export SNOWFLAKE_WAREHOUSE=your_warehouse
export SNOWFLAKE_DATABASE=your_database
export SNOWFLAKE_SCHEMA=your_schema
```

#### Option B: Using Streamlit Secrets (Recommended for Local Dev)
Create `app/.streamlit/secrets.toml`:

```toml
[auth]
redirect_uri = "https://mmm-app-dev-web-wuepn6nq5a-ew.a.run.app/oauth2callback"
cookie_secret = "xxx"
client_id = "xxx"
client_secret = "xxx"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
providers = ["google"]

[snowflake]
account = "IPENC"
user = "AMXUZTH-AWS_BRIDGE"
warehouse = "SMALL_WH"
database = "MESHED_BUYCYCLE"
schema = "GROWTH"
```

⚠️ **Important**: Never commit secrets files! They're in `.gitignore`.

#### Private Key Authentication
For Snowflake private key authentication:

1. Generate or obtain your private key (`private_key.pem`)
2. Place it securely (not in the repo)
3. In the Streamlit UI, upload the key or paste its contents
4. Optionally save to Google Secret Manager for persistence

---

### Local Development: Example Secret and Env Files

To simplify local development setup, the repository includes template files for secrets and environment variables.

#### Using Template Files

**1. Streamlit Secrets Template**

Copy the example secrets file and fill in your credentials:

```bash
# Copy the template
cp app/.streamlit/secrets.example.toml app/.streamlit/secrets.toml

# Edit the file and replace all REPLACE_ME values with your actual credentials
# The file is gitignored and will not be committed
```

The `secrets.toml` file should contain:
- **OAuth configuration** (`auth` section): `redirect_uri`, `cookie_secret`, `client_id`, `client_secret`, `server_metadata_url`, `providers`
- **Snowflake configuration** (`snowflake` section): `account`, `user`, `warehouse`, `database`, `schema`, optional `password`

**2. Environment Variables Template**

Copy the example .env file and configure for your environment:

```bash
# Copy the template
cp .env.example .env

# Edit the file and replace REPLACE_ME values with your configuration

# Load environment variables into your shell
set -a; source .env; set +a
```

Alternatively, use with tools like:
- **docker-compose**: Automatically loaded if `.env` exists in the same directory
- **direnv**: Configure `.envrc` to source `.env` file
- **VSCode**: Use `.env` file in launch configurations

**3. Security Notes**

⚠️ **Sensitive values that should NEVER be committed:**
- `SF_PASSWORD` - Snowflake password (use private key auth when possible)
- Private key files (`.pem` files)
- `AUTH_CLIENT_SECRET` - OAuth client secret
- `AUTH_COOKIE_SECRET` - Cookie encryption secret

✅ **For deployed environments (Cloud Run), use:**
- **Google Secret Manager** for secrets like private keys, passwords, OAuth credentials
- **GitHub Secrets** for CI/CD pipeline secrets
- **Environment variables in Cloud Run** for non-sensitive configuration

Both `app/.streamlit/secrets.toml` and `.env` are already in `.gitignore` to prevent accidental commits.

---

### 5. Running Locally

#### Start the Web Interface
```bash
# Ensure you're in the project root and virtual environment is activated
cd /path/to/mmm-app
source .venv/bin/activate

# Run Streamlit
streamlit run app/streamlit_app.py

# Or with custom port
streamlit run app/streamlit_app.py --server.port=8080
```

The application will be available at:
- **Default**: http://localhost:8501
- **Custom port**: http://localhost:8080

#### Application Workflow
1. **Connect Your Data** (Page 1):
   - Enter Snowflake credentials
   - Upload or paste private key
   - Test connection

2. **Map Your Data** (Page 2):
   - Select data source (table or SQL query)
   - Map columns to Robyn requirements
   - Preview data
   - Save configuration

3. **Experiment** (Page 3):
   - Configure training parameters
   - Submit training jobs to Cloud Run Jobs
   - Monitor execution

4. **Results** (Pages 4-5):
   - View training results
   - Analyze model outputs
   - Download artifacts from GCS

---

## Docker Development

For a more production-like environment or to test the full stack including R training.

### Building Images

#### Build Web Service Image
```bash
# Build for local platform
docker build -t mmm-web:local -f docker/Dockerfile.web .

# Build for multiple platforms (requires buildx)
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t mmm-web:local \
  -f docker/Dockerfile.web \
  .
```

#### Build Training Image
```bash
# First build the base image (contains R + dependencies)
docker build -t mmm-training-base:local -f docker/Dockerfile.training-base .

# Then build the training image
docker build \
  --build-arg BASE_REF=mmm-training-base:local \
  -t mmm-training:local \
  -f docker/Dockerfile.training \
  .
```

#### Build Legacy Single Image (Not Recommended)
```bash
# Old monolithic Dockerfile (includes both web and training)
docker build -t mmm-app:local -f docker/Dockerfile .
```

---

### Running Containers

#### Run Web Service Container
```bash
# Basic run
docker run --rm -p 8080:8080 \
  -e PROJECT_ID=YOUR_PROJECT_ID \
  -e REGION=europe-west1 \
  -e TRAINING_JOB_NAME=mmm-app-training \
  -e GCS_BUCKET=mmm-app-output \
  mmm-web:local

# Run with Google Cloud credentials mounted
docker run --rm -p 8080:8080 \
  -e PROJECT_ID=YOUR_PROJECT_ID \
  -e REGION=europe-west1 \
  -e TRAINING_JOB_NAME=mmm-app-training \
  -e GCS_BUCKET=mmm-app-output \
  -e GOOGLE_APPLICATION_CREDENTIALS=/tmp/keys/gcp-key.json \
  -v $HOME/.config/gcloud:/tmp/keys:ro \
  mmm-web:local
```

Access the app at http://localhost:8080

#### Test Training Container Locally
```bash
# Run training container (requires job config in GCS)
docker run --rm \
  -e PROJECT_ID=YOUR_PROJECT_ID \
  -e REGION=europe-west1 \
  -e GCS_BUCKET=mmm-app-output \
  -e JOB_CONFIG_GCS_PATH=gs://mmm-app-output/training-configs/latest/job_config.json \
  -e GOOGLE_APPLICATION_CREDENTIALS=/tmp/keys/gcp-key.json \
  -v $HOME/.config/gcloud:/tmp/keys:ro \
  mmm-training:local
```

---

## Testing

### Code Quality Checks

The repository includes a `Makefile` for common development tasks:

#### Format Code
```bash
# Auto-format with Black and isort
make format
```

#### Lint Code
```bash
# Run pylint and flake8
make lint
```

#### Type Check
```bash
# Run mypy for type checking
make typecheck
```

#### Run All Checks
```bash
# Format + lint + typecheck
make fix
```

### Manual Testing

#### Test Web Interface
1. Start the Streamlit app: `streamlit run app/streamlit_app.py`
2. Navigate through all pages
3. Test Snowflake connection
4. Verify GCP integrations (Secret Manager, GCS)
5. Check error handling

#### Test Data Processing
```bash
# In Python REPL or script
python3 -c "
from app.data_processor import DataProcessor
dp = DataProcessor()
# Add your test logic here
"
```

#### Test Docker Images
```bash
# Build and run image
docker build -t mmm-web:test -f docker/Dockerfile.web .
docker run --rm -p 8080:8080 mmm-web:test

# Check logs
docker logs <container_id>
```

### Integration Testing

Test the full workflow:
1. Connect to Snowflake
2. Fetch sample data
3. Map columns
4. Submit a training job (if Cloud Run Jobs is available)
5. Monitor job execution
6. View results in GCS

---

## CI/CD Pipeline

The repository uses GitHub Actions for automated deployments.

### Pipeline Files
- **`.github/workflows/ci.yml`**: Production deployment (main branch)
- **`.github/workflows/ci-dev.yml`**: Development deployment (copilot/* branches)

### Pipeline Workflow
1. **Checkout code**
2. **Authenticate to GCP** via Workload Identity Federation
3. **Build Docker images**:
   - Web service (`mmm-web`)
   - Training job (`mmm-training`)
4. **Push to Artifact Registry**
5. **Deploy infrastructure** with Terraform:
   - Cloud Run Service (web interface)
   - Cloud Run Job (training)
   - Service accounts
   - IAM bindings
   - Secrets

### Testing CI Locally

#### Validate Terraform
```bash
cd infra/terraform

# Initialize
terraform init

# Validate syntax
terraform validate

# Format check
terraform fmt -check

# Plan (dry run)
terraform plan -var-file="terraform.tfvars"
```

#### Simulate Build Process
```bash
# Build images as CI does
docker buildx build \
  --platform linux/amd64 \
  -t test-web \
  -f docker/Dockerfile.web \
  .

docker buildx build \
  --platform linux/amd64 \
  -t test-training \
  -f docker/Dockerfile.training \
  .
```

---

## Troubleshooting

### Common Issues

#### 1. Streamlit Won't Start
**Error**: `ModuleNotFoundError: No module named 'streamlit'`

**Solution**:
```bash
# Ensure virtual environment is activated
source .venv/bin/activate

# Reinstall dependencies
pip install -r requirements.txt
```

---

#### 2. Google Cloud Authentication Fails
**Error**: `google.auth.exceptions.DefaultCredentialsError`

**Solution**:
```bash
# Re-authenticate
gcloud auth login
gcloud auth application-default login

# Verify project
gcloud config get-value project

# Check credentials
gcloud auth list
```

---

#### 3. Snowflake Connection Error
**Error**: `Failed to connect to Snowflake`

**Solution**:
- Verify credentials are correct
- Check network connectivity
- Ensure private key format is correct (PEM format)
- Test connection with `snowsql` CLI

---

#### 4. Docker Build Fails
**Error**: Various build errors

**Solution**:
```bash
# Clear Docker cache
docker builder prune -a

# Rebuild without cache
docker build --no-cache -t mmm-web:local -f docker/Dockerfile.web .

# Check disk space
df -h
```

---

#### 5. GCS Bucket Access Denied
**Error**: `403 Forbidden` when accessing GCS

**Solution**:
```bash
# Verify bucket exists
gcloud storage buckets describe gs://${GCS_BUCKET}

# Check IAM permissions
gcloud storage buckets get-iam-policy gs://${GCS_BUCKET}

# Grant yourself permissions (if owner)
gcloud storage buckets add-iam-policy-binding gs://${GCS_BUCKET} \
  --member="user:your-email@example.com" \
  --role="roles/storage.objectAdmin"
```

---

#### 6. Port Already in Use
**Error**: `Address already in use: 8501`

**Solution**:
```bash
# Find process using the port
lsof -i :8501

# Kill the process
kill -9 <PID>

# Or use a different port
streamlit run app/streamlit_app.py --server.port=8502
```

---

#### 7. R/Robyn Training Fails
**Error**: Errors related to R packages or nevergrad

**Solution**:
- Use Docker for consistent R environment
- Check `docker/Dockerfile.training-base` for R setup
- Verify `RETICULATE_PYTHON` environment variable
- Ensure nevergrad is installed: `pip3 show nevergrad`

---

### Getting Help

1. **Check logs**:
   - Streamlit: Terminal output
   - Docker: `docker logs <container_id>`
   - Cloud Run: GCP Console > Cloud Run > Logs

2. **Review documentation**:
   - [README.md](README.md): Project overview
   - [docs/](docs/): Architecture and deployment docs

3. **Verify environment**:
   ```bash
   # Python version
   python3 --version
   
   # Installed packages
   pip list
   
   # GCP configuration
   gcloud config list
   
   # Docker version
   docker --version
   ```

---

## Additional Resources

### Key Files to Review
- **`app/streamlit_app.py`**: Main application entry point
- **`app/app_shared.py`**: Shared utilities and helpers
- **`r/run_all.R`**: R training script
- **`infra/terraform/main.tf`**: Infrastructure definition

### External Documentation
- [Streamlit Documentation](https://docs.streamlit.io/)
- [Google Cloud Run](https://cloud.google.com/run/docs)
- [Terraform GCP Provider](https://registry.terraform.io/providers/hashicorp/google/latest/docs)
- [Robyn MMM](https://github.com/facebookexperimental/Robyn)

---

## Best Practices

### Development Workflow
1. **Create feature branch**: `git checkout -b feature/your-feature`
2. **Make changes** in small, focused commits
3. **Test locally**: Run app, check functionality
4. **Format code**: `make format`
5. **Run linters**: `make lint`
6. **Test in Docker**: Build and run container
7. **Commit changes**: `git commit -m "Description"`
8. **Push to GitHub**: `git push origin feature/your-feature`
9. **Create PR**: GitHub will trigger CI/CD on `copilot/*` branches

### Code Quality
- Use **type hints** in Python code
- Follow **PEP 8** style guidelines
- Write **docstrings** for functions
- Keep functions **small and focused**
- Add **error handling** for external services

### Security
- **Never commit secrets** (keys, passwords, tokens)
- Use **Google Secret Manager** for production secrets
- Use **`.streamlit/secrets.toml`** for local secrets
- Keep `.gitignore` up to date

---

## Summary

This guide covers:
- ✅ Prerequisites and dependencies
- ✅ Local environment setup
- ✅ Running the Streamlit web interface
- ✅ Docker development and testing
- ✅ Understanding the CI/CD pipeline
- ✅ Troubleshooting common issues

For production deployment, refer to [README.md](README.md) and the Terraform configuration in `infra/terraform/`.

Happy developing! 🚀
