# MMM Trainer - Basic Requirements

This document provides a concise list of basic requirements for deploying and maintaining the MMM Trainer application.

## Overview

The MMM Trainer is a Streamlit-based web application deployed on Google Cloud Platform (GCP) that orchestrates R/Robyn Marketing Mix Modeling experiments using Snowflake data.

For detailed deployment instructions, see the **[Deployment Guide](DEPLOYMENT_GUIDE.md)**.

---

## Prerequisites for Deployment

### 1. Required Tools & Software

Install the following tools on your local machine or CI/CD environment:

| Tool | Minimum Version | Purpose |
|------|----------------|---------|
| [Google Cloud SDK (gcloud)](https://cloud.google.com/sdk/docs/install) | Latest | GCP CLI operations and authentication |
| [Terraform](https://www.terraform.io/downloads) | ≥ 1.5.0 | Infrastructure as Code deployment |
| [Docker](https://docs.docker.com/get-docker/) | Latest | Container builds (with Buildx enabled) |
| [Git](https://git-scm.com/) | Latest | Version control |
| [Python](https://www.python.org/downloads/) | ≥ 3.11 | Local development (optional) |
| [R](https://www.r-project.org/) | ≥ 4.3 | Local R development (optional) |

**Docker Buildx Setup:**
```bash
docker buildx create --use
```

---

### 2. Required Accounts & Access

You must have access to the following services with appropriate permissions:

#### Google Cloud Platform (GCP)
- **GCP Project** with billing enabled
- **Project Owner** or **Editor** role (for initial setup)
- Ability to create service accounts and assign IAM roles
- Ability to enable APIs

#### GitHub
- **GitHub Account** with access to the repository
- **Repository Admin** access (to configure secrets and workflows)

#### Snowflake
- **Snowflake Account** with:
  - User credentials (username + password OR username + RSA private key)
  - Access to a warehouse for compute
  - Read access to the data source (database/schema/tables)
  - Appropriate role with SELECT permissions

---

### 3. Required GCP APIs

The following Google Cloud APIs must be enabled in your project:

- `run.googleapis.com` - Cloud Run (web service and training jobs)
- `artifactregistry.googleapis.com` - Container image registry
- `cloudbuild.googleapis.com` - Container builds
- `cloudscheduler.googleapis.com` - Queue processing scheduler
- `secretmanager.googleapis.com` - Secrets management
- `storage.googleapis.com` - Cloud Storage (GCS buckets)
- `iamcredentials.googleapis.com` - Service account credentials
- `iam.googleapis.com` - IAM management
- `cloudresourcemanager.googleapis.com` - Project resource management

**Enable all at once:**
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

---

### 4. Required GCP Resources

The following GCP resources must be created before deployment:

| Resource | Purpose | Example Name |
|----------|---------|--------------|
| **GCS Bucket (Terraform State)** | Store Terraform state files | `your-project-id-tf-state` |
| **GCS Bucket (App Outputs)** | Store training data and model artifacts | `mmm-app-output` |
| **Artifact Registry Repository** | Store Docker images | `mmm-repo` |
| **Workload Identity Pool** | GitHub Actions OIDC authentication | `github-pool` |
| **OIDC Provider** | GitHub OIDC provider | `github-oidc` |
| **Deployer Service Account** | CI/CD deployment account | `github-deployer` |

---

### 5. Required Credentials & Secrets

You need the following credentials before deployment:

#### Google OAuth (for user authentication)
- **OAuth Client ID** - Created in GCP Console
- **OAuth Client Secret** - Created in GCP Console
- **Cookie Secret** - Random 32-byte hex string (`openssl rand -hex 32`)

#### Snowflake
- **Account Identifier** (e.g., `XXXXXXX-NN00000`)
- **Username**
- **Password** OR **RSA Private Key** (PEM format) - *Private key is recommended*
- **Warehouse Name**
- **Database Name**
- **Schema Name**
- **Role Name** - Should NOT be `ACCOUNTADMIN` in production

#### GitHub Secrets
The following must be configured in GitHub repository secrets:

| Secret Name | Description |
|-------------|-------------|
| `SF_PRIVATE_KEY` | Snowflake RSA private key (PEM format) |
| `GOOGLE_OAUTH_CLIENT_ID` | OAuth Client ID from GCP |
| `GOOGLE_OAUTH_CLIENT_SECRET` | OAuth Client Secret from GCP |
| `STREAMLIT_COOKIE_SECRET` | Random 32-byte hex string for cookie encryption |

---

### 6. Required Configuration Files

Before deployment, you must configure the following files in the repository:

| File | Purpose |
|------|---------|
| `infra/terraform/backend.tf` | Terraform state bucket configuration |
| `infra/terraform/envs/prod.tfvars` | Production environment variables |
| `infra/terraform/envs/dev.tfvars` | Development environment variables |
| `.github/workflows/ci.yml` | Production CI/CD workflow settings |
| `.github/workflows/ci-dev.yml` | Development CI/CD workflow settings |

---

## Prerequisites for Ongoing Maintenance

### 1. Technical Knowledge

Team members maintaining this application should have:

- **Basic understanding of:**
  - Google Cloud Platform (Cloud Run, GCS, IAM)
  - Terraform for infrastructure management
  - Docker containerization
  - Git and GitHub workflows
  - CI/CD concepts

- **Familiarity with:**
  - Python (Streamlit applications)
  - R programming (for Robyn MMM)
  - Snowflake data warehouse
  - YAML configuration

### 2. Access Requirements

Maintainers need:

- **GCP Project Access:**
  - At minimum: `Viewer` role for monitoring
  - For deployments: `Editor` or specific roles (Cloud Run Admin, Storage Admin, etc.)
  - For debugging: `Logs Viewer`, `Monitoring Viewer`

- **GitHub Repository Access:**
  - At minimum: `Write` access for code changes
  - For releases: `Maintain` or `Admin` access

- **Snowflake Access:**
  - Read access to data sources
  - Ability to test queries

### 3. Monitoring & Operations

Regular maintenance tasks include:

- **Monitoring:**
  - Cloud Run service health and logs
  - Training job execution and success rates
  - GCS storage usage and costs
  - Snowflake query performance

- **Cost Management:**
  - Review monthly GCP billing
  - Optimize Cloud Run resource allocation
  - Implement GCS lifecycle policies
  - Monitor Snowflake warehouse usage

- **Security:**
  - Rotate secrets periodically (OAuth, Snowflake keys)
  - Review and update IAM permissions
  - Keep dependencies updated
  - Monitor security advisories

- **Updates:**
  - Update Python dependencies (`requirements.txt`)
  - Update R packages (`Dockerfile.training-base`)
  - Update Terraform provider versions
  - Apply GCP platform updates

### 4. Backup & Disaster Recovery

Ensure the following are backed up or recoverable:

- **Terraform State:** Stored in GCS with versioning enabled
- **Model Artifacts:** Stored in GCS with lifecycle policies
- **Configuration:** Version controlled in Git
- **Secrets:** Documented recovery process via Secret Manager

### 5. Development Tools (Optional)

For local development and testing:

- **Python Virtual Environment:** For testing Streamlit changes
- **Local Docker:** For container testing
- **R Studio / R Environment:** For R script development
- **Code Editor:** VSCode, PyCharm, or similar

---

## Quick Reference: Minimum Setup Checklist

Use this checklist for new deployments:

- [ ] **1. Install required tools** (gcloud, terraform, docker, git)
- [ ] **2. Create GCP project** with billing enabled
- [ ] **3. Enable required GCP APIs**
- [ ] **4. Create Terraform state bucket** (`your-project-tf-state`)
- [ ] **5. Create Artifact Registry** repository (`mmm-repo`)
- [ ] **6. Set up Workload Identity Federation** for GitHub Actions
- [ ] **7. Create deployer service account** (`github-deployer`)
- [ ] **8. Grant deployer SA permissions** (Cloud Run Admin, Storage Admin, etc.)
- [ ] **9. Create output GCS bucket** (`mmm-app-output`)
- [ ] **10. Configure Google OAuth** (consent screen + client ID/secret)
- [ ] **11. Generate Snowflake RSA key pair** (or obtain password)
- [ ] **12. Configure GitHub secrets** (SF_PRIVATE_KEY, OAuth, cookie secret)
- [ ] **13. Update Terraform configs** (backend.tf, prod.tfvars, dev.tfvars)
- [ ] **14. Update CI/CD workflows** (ci.yml, ci-dev.yml)
- [ ] **15. Deploy via GitHub Actions** (push to main branch)
- [ ] **16. Verify deployment** (Cloud Run service, logs, functionality)

---

## Cost Estimates

Expected monthly costs (varies by usage):

| Configuration | Training Resources | Estimated Monthly Cost |
|--------------|-------------------|----------------------|
| **Standard (Default)** | 4 vCPU / 16GB RAM | $10-50 (typical)<br/>$200 (high volume) |
| **High Performance** | 8 vCPU / 32GB RAM | $10-50 (typical)<br/>$200 (high volume) |
| **Max Performance** | 16 vCPU / 64GB RAM | $10-50 (typical)<br/>$200 (high volume) |

**Note:** Faster machines save time but have similar costs due to per-second billing. Choose based on urgency needs.

Costs include:
- Cloud Run service (web interface)
- Cloud Run Jobs (training executions)
- Cloud Storage (artifacts and data)
- Artifact Registry (container images)
- Cloud Scheduler (queue processing)
- Networking (egress)

**Note:** Snowflake costs are separate and depend on warehouse size and usage.

For detailed cost optimization strategies, see [COST_OPTIMIZATION.md](../COST_OPTIMIZATION.md).

---

## Support & Additional Resources

| Resource | Description |
|----------|-------------|
| [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) | Comprehensive deployment instructions |
| [ARCHITECTURE.md](../ARCHITECTURE.md) | System architecture and components |
| [DEVELOPMENT.md](../DEVELOPMENT.md) | Local development setup |
| [README.md](../README.md) | Project overview and quick start |
| [COST_OPTIMIZATION.md](../COST_OPTIMIZATION.md) | Cost management strategies |

---

## License

Apache-2.0
