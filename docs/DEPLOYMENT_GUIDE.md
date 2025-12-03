# MMM Trainer Deployment Guide

This guide provides step-by-step instructions for replicating and deploying the MMM Trainer application in a new Google Cloud Platform (GCP) project. It covers all infrastructure setup, CI/CD configuration, and deployment procedures.

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Step 1: GCP Project Setup](#step-1-gcp-project-setup)
- [Step 2: Enable Required APIs](#step-2-enable-required-apis)
- [Step 3: Create Terraform State Bucket](#step-3-create-terraform-state-bucket)
- [Step 4: Create Artifact Registry Repository](#step-4-create-artifact-registry-repository)
- [Step 5: Set Up Workload Identity Federation](#step-5-set-up-workload-identity-federation)
- [Step 6: Create Service Accounts](#step-6-create-service-accounts)
- [Step 7: Configure IAM Permissions](#step-7-configure-iam-permissions)
- [Step 8: Create GCS Bucket for Outputs](#step-8-create-gcs-bucket-for-outputs)
- [Step 9: Configure Google OAuth](#step-9-configure-google-oauth)
- [Step 10: Configure Snowflake](#step-10-configure-snowflake)
- [Step 11: Set Up GitHub Repository](#step-11-set-up-github-repository)
- [Step 12: Update Configuration Files](#step-12-update-configuration-files)
- [Step 13: Deploy the Application](#step-13-deploy-the-application)
- [Step 14: Post-Deployment Verification](#step-14-post-deployment-verification)
- [Environment Configuration Reference](#environment-configuration-reference)
- [Troubleshooting](#troubleshooting)
- [Cost Optimization](#cost-optimization)

---

## Overview

The MMM Trainer application consists of:

1. **Web Interface**: A Streamlit-based Cloud Run Service for user interaction
2. **Training Jobs**: Cloud Run Jobs for R/Robyn model training
3. **Storage**: GCS bucket for data, models, and artifacts
4. **Queue Scheduler**: Cloud Scheduler for batch job processing
5. **Secrets**: Google Secret Manager for sensitive data

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              GitHub Actions                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────────┐   │
│  │ Build Docker │───▶│ Push to      │───▶│ Terraform Apply          │   │
│  │ Images       │    │ Artifact Reg │    │ (Deploy Cloud Run)       │   │
│  └──────────────┘    └──────────────┘    └──────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
           ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
           │ Cloud Run    │ │ Cloud Run    │ │ Cloud        │
           │ Service      │ │ Jobs         │ │ Scheduler    │
           │ (Web UI)     │ │ (Training)   │ │ (Queue Tick) │
           └──────────────┘ └──────────────┘ └──────────────┘
                    │               │               │
                    └───────────────┼───────────────┘
                                    ▼
           ┌──────────────────────────────────────────────────┐
           │                 GCS Bucket                        │
           │  ├── training-data/                              │
           │  ├── training-configs/                           │
           │  ├── robyn/ (model outputs)                      │
           │  └── robyn-queues/                               │
           └──────────────────────────────────────────────────┘
                                    │
                                    ▼
           ┌──────────────────────────────────────────────────┐
           │              Snowflake Data Warehouse             │
           └──────────────────────────────────────────────────┘
```

---

## Prerequisites

Before starting, ensure you have:

### Required Tools

| Tool | Version | Purpose |
|------|---------|---------|
| [Google Cloud SDK (gcloud)](https://cloud.google.com/sdk/docs/install) | Latest | GCP CLI operations |
| [Terraform](https://www.terraform.io/downloads) | ≥ 1.5.0 | Infrastructure as Code |
| [Docker](https://docs.docker.com/get-docker/) | Latest | Container builds (with Buildx) |
| [Git](https://git-scm.com/) | Latest | Version control |

### Required Accounts & Access

- **Google Cloud Platform** account with billing enabled
- **GitHub** account with repository access
- **Snowflake** account with appropriate credentials

### Required Information

Gather the following information before starting:

| Item | Description | Example |
|------|-------------|---------|
| `PROJECT_ID` | Your GCP project ID | `my-mmm-project` |
| `PROJECT_NUMBER` | Your GCP project number | `123456789012` |
| `REGION` | GCP region for deployment | `europe-west1` |
| `GITHUB_ORG` | Your GitHub organization/username | `my-company` |
| `GITHUB_REPO` | Repository name | `mmm-app` |
| Snowflake credentials | Account, user, warehouse, etc. | See [Step 10](#step-10-configure-snowflake) |

---

## Step 1: GCP Project Setup

### Create a New Project (or use existing)

```bash
# Set your project ID
export PROJECT_ID="your-project-id"
export REGION="europe-west1"

# Create a new project (optional)
gcloud projects create $PROJECT_ID --name="MMM Trainer"

# Set as active project
gcloud config set project $PROJECT_ID

# Link billing account (required)
# First, list available billing accounts to find your billing account ID
gcloud billing accounts list
# The output shows billing account IDs in format: XXXXXX-XXXXXX-XXXXXX (e.g., 01A2B3-C4D5E6-F7G8H9)
# Replace the placeholder below with your actual billing account ID
gcloud billing projects link $PROJECT_ID --billing-account=YOUR-BILLING-ACCOUNT-ID

# Get your project number
export PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
echo "Project Number: $PROJECT_NUMBER"
```

---

## Step 2: Enable Required APIs

Enable all necessary Google Cloud APIs:

```bash
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  iamcredentials.googleapis.com \
  iam.googleapis.com \
  cloudresourcemanager.googleapis.com
```

Wait for all APIs to be enabled (this may take a few minutes).

---

## Step 3: Create Terraform State Bucket

Terraform requires a GCS bucket to store its state file:

```bash
# Create the bucket
export TF_STATE_BUCKET="${PROJECT_ID}-tf-state"
gcloud storage buckets create gs://${TF_STATE_BUCKET} \
  --location=${REGION} \
  --uniform-bucket-level-access

# Enable versioning for state protection
gcloud storage buckets update gs://${TF_STATE_BUCKET} --versioning
```

Update `infra/terraform/backend.tf` with your bucket name:

```hcl
terraform {
  backend "gcs" {
    bucket = "your-project-id-tf-state"  # Replace with your bucket name
  }
}
```

---

## Step 4: Create Artifact Registry Repository

Create a Docker repository for container images:

```bash
export ARTIFACT_REPO="mmm-repo"

gcloud artifacts repositories create ${ARTIFACT_REPO} \
  --repository-format=docker \
  --location=${REGION} \
  --description="MMM Trainer Docker images"

# Verify creation
gcloud artifacts repositories list --location=${REGION}
```

---

## Step 5: Set Up Workload Identity Federation

Workload Identity Federation allows GitHub Actions to authenticate with GCP without service account keys.

### Create the Workload Identity Pool

```bash
export WIF_POOL="github-pool"
export WIF_PROVIDER="github-oidc"
export GITHUB_ORG="your-github-org"  # Your GitHub org or username
export GITHUB_REPO="mmm-app"          # Your repository name

# Create the Workload Identity Pool
gcloud iam workload-identity-pools create ${WIF_POOL} \
  --location="global" \
  --display-name="GitHub Actions Pool"

# Verify pool creation
gcloud iam workload-identity-pools describe ${WIF_POOL} --location="global"
```

### Create the OIDC Provider

```bash
# Create the provider
gcloud iam workload-identity-pools providers create-oidc ${WIF_PROVIDER} \
  --location="global" \
  --workload-identity-pool=${WIF_POOL} \
  --display-name="GitHub OIDC Provider" \
  --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
  --issuer-uri="https://token.actions.githubusercontent.com"

# Get the full provider resource name (needed for GitHub Actions)
gcloud iam workload-identity-pools providers describe ${WIF_PROVIDER} \
  --location="global" \
  --workload-identity-pool=${WIF_POOL} \
  --format='value(name)'
```

The provider resource name will be in this format:
```
projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/providers/github-oidc
```

---

## Step 6: Create Service Accounts

Create the required service accounts:

### 1. GitHub Deployer Service Account

This account is used by GitHub Actions to deploy infrastructure:

```bash
export DEPLOYER_SA="github-deployer"

# Create the service account
gcloud iam service-accounts create ${DEPLOYER_SA} \
  --display-name="GitHub Deployer for MMM App"

# Get the full email
export DEPLOYER_SA_EMAIL="${DEPLOYER_SA}@${PROJECT_ID}.iam.gserviceaccount.com"
echo "Deployer SA: $DEPLOYER_SA_EMAIL"
```

### 2. Web Service Account (created by Terraform)

The web service account (`mmm-web-service-sa`) is created by Terraform. No manual creation needed.

### 3. Training Job Service Account (created by Terraform)

The training job service account (`mmm-training-job-sa`) is created by Terraform. No manual creation needed.

### 4. Scheduler Service Account (created by Terraform)

The scheduler service account (`robyn-queue-scheduler`) is created by Terraform. No manual creation needed.

---

## Step 7: Configure IAM Permissions

### Grant Permissions to Deployer Service Account

```bash
# Cloud Run Admin (to deploy services and jobs)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${DEPLOYER_SA_EMAIL}" \
  --role="roles/run.admin"

# Artifact Registry Admin (to push images)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${DEPLOYER_SA_EMAIL}" \
  --role="roles/artifactregistry.admin"

# Storage Admin (to manage buckets)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${DEPLOYER_SA_EMAIL}" \
  --role="roles/storage.admin"

# Secret Manager Admin (to manage secrets)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${DEPLOYER_SA_EMAIL}" \
  --role="roles/secretmanager.admin"

# Service Account User (to impersonate runtime SAs)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${DEPLOYER_SA_EMAIL}" \
  --role="roles/iam.serviceAccountUser"

# Cloud Scheduler Admin (to create scheduler jobs)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${DEPLOYER_SA_EMAIL}" \
  --role="roles/cloudscheduler.admin"

# Service Account Admin (to create service accounts via Terraform)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${DEPLOYER_SA_EMAIL}" \
  --role="roles/iam.serviceAccountAdmin"

# IAM Security Admin (to manage IAM bindings)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${DEPLOYER_SA_EMAIL}" \
  --role="roles/iam.securityAdmin"
```

### Allow GitHub Actions to Impersonate Deployer SA

```bash
# Allow the GitHub OIDC pool to use the deployer SA
gcloud iam service-accounts add-iam-policy-binding ${DEPLOYER_SA_EMAIL} \
  --project=${PROJECT_ID} \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/attribute.repository/${GITHUB_ORG}/${GITHUB_REPO}"
```

---

## Step 8: Create GCS Bucket for Outputs

Create the bucket for storing training data and model outputs:

```bash
export GCS_BUCKET="mmm-app-output"  # Or your preferred name

# Create the bucket
gcloud storage buckets create gs://${GCS_BUCKET} \
  --location=${REGION} \
  --uniform-bucket-level-access

# Apply lifecycle rules for cost optimization (optional)
cat > /tmp/lifecycle.json << 'EOF'
{
  "lifecycle": {
    "rule": [
      {
        "action": {"type": "SetStorageClass", "storageClass": "NEARLINE"},
        "condition": {
          "age": 30,
          "matchesPrefix": ["robyn/", "datasets/", "training-data/"]
        }
      },
      {
        "action": {"type": "SetStorageClass", "storageClass": "COLDLINE"},
        "condition": {
          "age": 90,
          "matchesPrefix": ["robyn/", "datasets/", "training-data/"]
        }
      }
    ]
  }
}
EOF

gcloud storage buckets update gs://${GCS_BUCKET} --lifecycle-file=/tmp/lifecycle.json
```

---

## Step 9: Configure Google OAuth

To enable user authentication, set up Google OAuth:

### Create OAuth Consent Screen

1. Go to [Google Cloud Console > APIs & Services > OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent)
2. Select **Internal** (for organization users) or **External** (for public access)
3. Fill in:
   - **App name**: `MMM Trainer`
   - **User support email**: Your email
   - **Authorized domains**: Your domain (e.g., `yourcompany.com`)
   - **Developer contact email**: Your email
4. Add scopes: `email`, `profile`, `openid`
5. Save and continue

### Create OAuth Client ID

1. Go to [APIs & Services > Credentials](https://console.cloud.google.com/apis/credentials)
2. Click **Create Credentials** > **OAuth client ID**
3. Select **Web application**
4. Fill in:
   - **Name**: `MMM Trainer Web Client`
   - **Authorized JavaScript origins**: 
     - Your Cloud Run URL (e.g., `https://mmm-app-web-abc123xyz-ew.a.run.app`)
   - **Authorized redirect URIs**:
     - Your Cloud Run URL + `/oauth2callback` (e.g., `https://mmm-app-web-abc123xyz-ew.a.run.app/oauth2callback`)

> **Note**: You'll get the actual Cloud Run URL after deployment. You can initially create the OAuth client with placeholder URLs and update them after the first deployment, or deploy first without OAuth and then configure it.

5. Click **Create**
6. Save the **Client ID** and **Client Secret**

### Generate Cookie Secret

Generate a random secret for cookie encryption:

```bash
openssl rand -hex 32
```

Save this value for GitHub Secrets configuration.

---

## Step 10: Configure Snowflake

### Gather Snowflake Information

You need the following Snowflake credentials:

| Parameter | Description | Example |
|-----------|-------------|---------|
| `SF_USER` | Snowflake username | `MY_USER` |
| `SF_ACCOUNT` | Snowflake account identifier | `XXXXXXX-NN00000` |
| `SF_WAREHOUSE` | Compute warehouse | `COMPUTE_WH` |
| `SF_DATABASE` | Target database | `MY_DATABASE` |
| `SF_SCHEMA` | Target schema | `PUBLIC` |
| `SF_ROLE` | User role (see note below) | `DATA_READER` |

> **⚠️ Security Note**: Avoid using `ACCOUNTADMIN` in production. Create a custom role with minimal required permissions:
> ```sql
> -- Create a custom role for the MMM application
> CREATE ROLE mmm_reader;
> GRANT USAGE ON WAREHOUSE your_warehouse TO ROLE mmm_reader;
> GRANT USAGE ON DATABASE your_database TO ROLE mmm_reader;
> GRANT USAGE ON SCHEMA your_database.your_schema TO ROLE mmm_reader;
> GRANT SELECT ON ALL TABLES IN SCHEMA your_database.your_schema TO ROLE mmm_reader;
> GRANT ROLE mmm_reader TO USER your_user;
> ```

### Generate RSA Key Pair (Recommended)

For secure key-pair authentication:

```bash
# Generate private key
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out snowflake_key.pem -nocrypt

# Generate public key
openssl rsa -in snowflake_key.pem -pubout -out snowflake_key.pub

# Display public key (for Snowflake configuration)
cat snowflake_key.pub
```

### Configure Key in Snowflake

```sql
-- In Snowflake
ALTER USER YOUR_USERNAME SET RSA_PUBLIC_KEY='MIIBIjANBgkqh...';
```

The private key content (from `snowflake_key.pem`) will be stored as a GitHub Secret.

---

## Step 11: Set Up GitHub Repository

### Fork or Clone the Repository

```bash
git clone https://github.com/ivana-meshed/mmm-app.git
cd mmm-app
```

### Configure GitHub Secrets

Go to your repository's **Settings > Secrets and variables > Actions** and add:

| Secret Name | Description | Value |
|-------------|-------------|-------|
| `SF_PRIVATE_KEY` | Snowflake RSA private key (PEM format) | Contents of `snowflake_key.pem` |
| `GOOGLE_OAUTH_CLIENT_ID` | OAuth Client ID | From Step 9 |
| `GOOGLE_OAUTH_CLIENT_SECRET` | OAuth Client Secret | From Step 9 |
| `STREAMLIT_COOKIE_SECRET` | Random 32-byte hex string | From `openssl rand -hex 32` |

### Verify Workflow Permissions

1. Go to **Settings > Actions > General**
2. Under **Workflow permissions**, select:
   - **Read and write permissions**
   - Check **Allow GitHub Actions to create and approve pull requests**

---

## Step 12: Update Configuration Files

### Update Terraform Variables

Edit `infra/terraform/envs/prod.tfvars`:

```hcl
project_id   = "your-project-id"        # Your GCP project ID
region       = "europe-west1"           # Your preferred region
service_name = "mmm-app"                # Service name prefix
bucket_name  = "mmm-app-output"         # Your GCS bucket name
deployer_sa  = "github-deployer@your-project-id.iam.gserviceaccount.com"

scheduler_job_name = "robyn-queue-tick"
queue_name         = "default"

# Snowflake configuration
sf_user      = "YOUR_SF_USER"
sf_account   = "YOUR_SF_ACCOUNT"
sf_warehouse = "YOUR_WAREHOUSE"
sf_database  = "YOUR_DATABASE"
sf_schema    = "YOUR_SCHEMA"
sf_role      = "YOUR_ROLE"

# Training job resources
training_cpu       = "4.0"
training_memory    = "16Gi"
training_max_cores = "4"

# Google OAuth allowed domains (comma-separated)
allowed_domains = "yourcompany.com"
```

### Update Terraform Backend

Edit `infra/terraform/backend.tf`:

```hcl
terraform {
  backend "gcs" {
    bucket = "your-project-id-tf-state"  # Your TF state bucket
  }
}
```

### Update CI/CD Workflow

Edit `.github/workflows/ci.yml` with your project settings:

```yaml
env:
  PROJECT_ID: your-project-id
  PROJECT_NUMBER: "123456789012"  # Your project number
  REGION: europe-west1
  ARTIFACT_REPO: mmm-repo
  WEB_IMAGE: mmm-web
  TRAINING_IMAGE: mmm-training
  SERVICE_NAME: mmm-app
  BUCKET: mmm-app-output
  WIF_POOL: github-pool
  WIF_PROVIDER: github-oidc
  SA_EMAIL: github-deployer@your-project-id.iam.gserviceaccount.com
  WEB_RUNTIME_SA: mmm-web-service-sa@your-project-id.iam.gserviceaccount.com
  TRAINING_RUNTIME_SA: mmm-training-job-sa@your-project-id.iam.gserviceaccount.com
```

Also update `.github/workflows/ci-dev.yml` with the same settings but using `mmm-app-dev` as the service name.

### Update Cloud Run URL in main.tf (Optional)

The Cloud Run URL hash suffix is project-specific. After your first deployment, update the `locals` block in `infra/terraform/main.tf`:

```hcl
locals {
  # Cloud Run generates a unique hash for each service
  # The URL format is: https://{service-name}-{hash}-{region-abbr}.a.run.app
  # Region abbreviations: europe-west1 = ew, us-central1 = uc, etc.
  # Get the actual URL after first deployment: gcloud run services describe {service}-web --region={region} --format='value(status.url)'
  web_service_url   = "https://${var.service_name}-web-HASH-ew.a.run.app"
  auth_redirect_uri = "${local.web_service_url}/oauth2callback"
}
```

> **Note**: The region abbreviation in the URL (e.g., `ew` for `europe-west1`, `uc` for `us-central1`) is generated by Google Cloud. After deployment, run `gcloud run services describe mmm-app-web --region=$REGION --format='value(status.url)'` to get the actual URL.

---

## Step 13: Deploy the Application

### Initial Deployment

Push to the `main` branch to trigger the CI/CD pipeline:

```bash
git add .
git commit -m "Configure for new project deployment"
git push origin main
```

### Monitor the Deployment

1. Go to **Actions** tab in your GitHub repository
2. Watch the CI workflow execution
3. Check each step for errors

### Manual Deployment (Alternative)

If you prefer to deploy manually:

```bash
# Authenticate
gcloud auth login
gcloud auth application-default login
gcloud config set project $PROJECT_ID

# Configure Docker
gcloud auth configure-docker ${REGION}-docker.pkg.dev

# Build and push images
docker buildx build \
  --platform linux/amd64 \
  -t ${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/mmm-web:latest \
  -f docker/Dockerfile.web \
  --push .

docker buildx build \
  --platform linux/amd64 \
  -t ${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/mmm-training-base:latest \
  -f docker/Dockerfile.training-base \
  --push .

docker buildx build \
  --platform linux/amd64 \
  --build-arg BASE_REF=${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/mmm-training-base:latest \
  -t ${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}/mmm-training:latest \
  -f docker/Dockerfile.training \
  --push .

# Deploy with Terraform
cd infra/terraform
terraform init -backend-config="prefix=envs/prod"
terraform workspace new prod || terraform workspace select prod
terraform plan -var-file="envs/prod.tfvars"
terraform apply -var-file="envs/prod.tfvars"
```

---

## Step 14: Post-Deployment Verification

### Verify Cloud Run Services

```bash
# List Cloud Run services
gcloud run services list --region=${REGION}

# Get web service URL
gcloud run services describe mmm-app-web \
  --region=${REGION} \
  --format='value(status.url)'
```

### Verify Cloud Run Jobs

```bash
# List Cloud Run jobs
gcloud run jobs list --region=${REGION}

# Verify training job
gcloud run jobs describe mmm-app-training --region=${REGION}
```

### Verify Cloud Scheduler

```bash
# List scheduler jobs
gcloud scheduler jobs list --location=${REGION}
```

### Test the Application

1. Open the Cloud Run service URL in your browser
2. Sign in with your Google account
3. Navigate through the pages to verify functionality
4. Try connecting to Snowflake
5. Submit a test training job

### Verify Logs

```bash
# View Cloud Run logs
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=mmm-app-web" \
  --limit=50 \
  --format='table(timestamp,textPayload)'
```

---

## Environment Configuration Reference

### Terraform Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `project_id` | GCP project ID | - |
| `region` | GCP region | `europe-west1` |
| `service_name` | Service name prefix | `mmm-app` |
| `bucket_name` | GCS bucket for outputs | `mmm-app-output` |
| `deployer_sa` | Deployer service account email | - |
| `training_cpu` | Training job CPU limit | `4.0` |
| `training_memory` | Training job memory limit | `16Gi` |
| `training_max_cores` | R training max cores | `4` |
| `min_instances` | Min Cloud Run instances | `0` |
| `max_instances` | Max Cloud Run instances | `10` |
| `allowed_domains` | OAuth allowed domains | `mesheddata.com` |

### GitHub Secrets Required

| Secret | Description |
|--------|-------------|
| `SF_PRIVATE_KEY` | Snowflake RSA private key |
| `GOOGLE_OAUTH_CLIENT_ID` | Google OAuth client ID |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Google OAuth client secret |
| `STREAMLIT_COOKIE_SECRET` | Cookie encryption secret |

### Environment Variables (Cloud Run)

| Variable | Description |
|----------|-------------|
| `PROJECT_ID` | GCP project ID |
| `REGION` | GCP region |
| `GCS_BUCKET` | Output bucket name |
| `TRAINING_JOB_NAME` | Training job name |
| `SF_USER` | Snowflake username |
| `SF_ACCOUNT` | Snowflake account |
| `SF_WAREHOUSE` | Snowflake warehouse |
| `SF_DATABASE` | Snowflake database |
| `SF_SCHEMA` | Snowflake schema |
| `SF_ROLE` | Snowflake role |

---

## Troubleshooting

### Common Issues

#### 1. Workload Identity Federation Errors

**Error**: `Unable to acquire impersonation credentials`

**Solution**:
```bash
# Verify the IAM binding
gcloud iam service-accounts get-iam-policy ${DEPLOYER_SA_EMAIL}

# Check the attribute mapping
gcloud iam workload-identity-pools providers describe ${WIF_PROVIDER} \
  --location="global" \
  --workload-identity-pool=${WIF_POOL}
```

#### 2. Terraform State Lock

**Error**: `Error acquiring the state lock`

**Solution**:
```bash
# Force unlock (use with caution)
terraform force-unlock <LOCK_ID>
```

#### 3. Image Pull Errors

**Error**: `Failed to pull image`

**Solution**:
```bash
# Verify image exists
gcloud artifacts docker images list ${REGION}-docker.pkg.dev/${PROJECT_ID}/${ARTIFACT_REPO}

# Check IAM permissions
gcloud artifacts repositories get-iam-policy ${ARTIFACT_REPO} --location=${REGION}
```

#### 4. OAuth Redirect Mismatch

**Error**: `redirect_uri_mismatch`

**Solution**:
1. Get the actual Cloud Run URL
2. Update OAuth redirect URIs in Google Cloud Console
3. Update `AUTH_REDIRECT_URI` in Terraform or environment

#### 5. Snowflake Connection Failed

**Error**: `Failed to connect to Snowflake`

**Solution**:
- Verify private key format (must be PKCS8 PEM)
- Check Snowflake account identifier format
- Verify network access (Snowflake may need IP whitelisting)

### Getting Logs

```bash
# Cloud Run service logs
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=mmm-app-web" --limit=100

# Cloud Run job logs
gcloud logging read "resource.type=cloud_run_job" --limit=100

# Terraform logs (verbose)
TF_LOG=DEBUG terraform apply
```

### Rollback Procedure

```bash
# Rollback to previous image
gcloud run services update-traffic mmm-app-web \
  --region=${REGION} \
  --to-revisions=mmm-app-web-PREVIOUS_REVISION=100
```

---

## Cost Optimization

### Resource Sizing

For cost-effective configurations:

| Configuration | Training CPU | Training Memory | Estimated Monthly Cost |
|--------------|--------------|-----------------|----------------------|
| High Performance | 8.0 | 32Gi | ~$150-200 |
| Balanced (default) | 4.0 | 16Gi | ~$75-100 |
| Cost Optimized | 2.0 | 8Gi | ~$40-50 |

### Min Instances

Set `min_instances = 0` to avoid idle costs (adds cold start latency of ~5-10 seconds).

### Storage Lifecycle

Apply lifecycle rules to automatically move old data to cheaper storage classes:
- **Nearline** (after 30 days): 50% cheaper
- **Coldline** (after 90 days): 80% cheaper

See [COST_OPTIMIZATION.md](../COST_OPTIMIZATION.md) for detailed cost analysis.

---

## Next Steps

After deployment:

1. **Test the full workflow**: Connect to Snowflake, run a training job, view results
2. **Set up monitoring**: Configure Cloud Monitoring alerts
3. **Configure backups**: Set up GCS bucket versioning
4. **Document for your team**: Create internal runbooks and access procedures
5. **Set up development environment**: Deploy to dev using `ci-dev.yml`

For local development, see [DEVELOPMENT.md](../DEVELOPMENT.md).

For architecture details, see [ARCHITECTURE.md](../ARCHITECTURE.md).
