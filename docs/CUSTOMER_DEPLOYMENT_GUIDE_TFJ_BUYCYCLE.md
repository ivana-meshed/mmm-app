# CUSTOMER DEPLOYMENT GUIDE - TFJ Buycycle GmbH

## License Information

- **Licensed to**: TFJ Buycycle GmbH
- **License ID**: LIC-TFJ-001
- **Valid From**: February 1, 2026
- **Valid Until**: February 1, 2028
- **Software**: MMM Trainer v1.0.0
- **Licensor**: Meshed Data Consulting

---

## Pre-Deployment Checklist

Before you begin, ensure you have:

- [ ] Distribution archive (`tfj-buycycle-v1.0.0-FINAL.tar.gz`)
- [ ] Checksum file for verification
- [ ] Signed LICENSE_AUTHORIZATION.txt
- [ ] Google Cloud Platform account with billing enabled
- [ ] Snowflake account and credentials
- [ ] GitHub repository (for CI/CD)
- [ ] Basic understanding of Docker, Terraform, and Cloud Run

---

## Phase 1: Verify Distribution Package

### Step 1.1: Verify Archive Integrity

```bash
# Verify checksum
sha256sum tfj-buycycle-v1.0.0-FINAL.tar.gz
# Compare with checksum provided by Meshed Data Consulting

# Extract archive
tar -xzf tfj-buycycle-v1.0.0-FINAL.tar.gz
cd tfj-buycycle-v1.0.0/mmm-app
```

### Step 1.2: Verify File Checksums

```bash
# Verify all files match expected checksums
sha256sum -c ../CHECKSUMS.txt

# Expected output: All files should show "OK"
```

### Step 1.3: Review License Documents

```bash
# Read your license authorization
cat LICENSE_AUTHORIZATION.txt

# Review main license terms
less LICENSE

# Check watermark manifest
cat WATERMARK_MANIFEST.txt
```

**IMPORTANT**: The watermark does not affect functionality. All code will execute normally.

---

## Phase 2: Google Cloud Platform Setup

### Step 2.1: Create GCP Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create new project (e.g., `tfj-mmm-prod`)
3. Note your **Project ID** and **Project Number**

### Step 2.2: Enable Required APIs

```bash
# Set project
gcloud config set project YOUR_PROJECT_ID

# Enable APIs
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com
```

### Step 2.3: Create Artifact Registry

```bash
# Create Docker repository
gcloud artifacts repositories create mmm-repo \
  --repository-format=docker \
  --location=europe-west1 \
  --description="MMM Trainer container images"
```

### Step 2.4: Create GCS Bucket

```bash
# Create bucket for model outputs
gsutil mb -l europe-west1 gs://tfj-mmm-output
```

### Step 2.5: Create Service Accounts

```bash
# Web service runtime SA
gcloud iam service-accounts create mmm-web-service-sa \
  --display-name="MMM Web Service Runtime SA"

# Training job runtime SA
gcloud iam service-accounts create mmm-training-job-sa \
  --display-name="MMM Training Job Runtime SA"

# GitHub deployer SA
gcloud iam service-accounts create github-deployer \
  --display-name="GitHub Actions Deployer"
```

### Step 2.6: Grant IAM Roles

```bash
PROJECT_ID="YOUR_PROJECT_ID"

# Web service SA roles
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:mmm-web-service-sa@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:mmm-web-service-sa@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"

# Training job SA roles
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:mmm-training-job-sa@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:mmm-training-job-sa@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"

# GitHub deployer SA roles
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:github-deployer@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/run.admin"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:github-deployer@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/artifactregistry.writer"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:github-deployer@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountUser"
```

---

## Phase 3: Snowflake Setup

### Step 3.1: Create Service Account

In Snowflake, create a service account for MMM Trainer:

```sql
-- Create user
CREATE USER mmm_service_user
  PASSWORD = 'STRONG_PASSWORD_HERE'
  DEFAULT_ROLE = MMM_ROLE
  DEFAULT_WAREHOUSE = MMM_WAREHOUSE;

-- Create role
CREATE ROLE MMM_ROLE;
GRANT ROLE MMM_ROLE TO USER mmm_service_user;

-- Grant warehouse access
GRANT USAGE ON WAREHOUSE MMM_WAREHOUSE TO ROLE MMM_ROLE;

-- Grant database and schema access
GRANT USAGE ON DATABASE YOUR_DATABASE TO ROLE MMM_ROLE;
GRANT USAGE ON SCHEMA YOUR_DATABASE.YOUR_SCHEMA TO ROLE MMM_ROLE;

-- Grant select on required tables
GRANT SELECT ON ALL TABLES IN SCHEMA YOUR_DATABASE.YOUR_SCHEMA TO ROLE MMM_ROLE;
```

### Step 3.2: Generate Key Pair (for key-based auth - recommended)

```bash
# Generate private key
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out sf_private_key.pem -nocrypt

# Generate public key
openssl rsa -in sf_private_key.pem -pubout -out sf_public_key.pub

# Assign public key to Snowflake user
# Copy content of sf_public_key.pub and run in Snowflake:
ALTER USER mmm_service_user SET RSA_PUBLIC_KEY='<paste public key here>';
```

---

## Phase 4: GitHub Repository Setup

### Step 4.1: Create Private Repository

1. Go to GitHub and create a **private** repository
2. Name it (e.g., `tfj-mmm-trainer`)
3. Initialize it (optional README)

### Step 4.2: Push Code to Repository

```bash
# Initialize git (if not already)
cd /path/to/tfj-buycycle-v1.0.0/mmm-app
git init

# Add remote
git remote add origin git@github.com:tfj-buycycle/tfj-mmm-trainer.git

# Create .gitignore if not exists (should already be there)
# Add all files
git add .
git commit -m "Initial commit - MMM Trainer v1.0.0 (LIC-TFJ-001)"

# Push to main
git branch -M main
git push -u origin main

# Create dev branch
git checkout -b dev
git push -u origin dev
```

### Step 4.3: Set Up Workload Identity Federation

```bash
# Create Workload Identity Pool
gcloud iam workload-identity-pools create github-pool \
  --location=global \
  --display-name="GitHub Actions Pool"

# Create provider
gcloud iam workload-identity-pools providers create-oidc github-oidc \
  --location=global \
  --workload-identity-pool=github-pool \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository_owner=='YOUR_GITHUB_ORG'"

# Grant permissions
PROJECT_NUMBER="YOUR_PROJECT_NUMBER"
gcloud iam service-accounts add-iam-policy-binding \
  github-deployer@$PROJECT_ID.iam.gserviceaccount.com \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/attribute.repository/YOUR_GITHUB_ORG/tfj-mmm-trainer"
```

### Step 4.4: Configure GitHub Secrets

Go to: Repository → Settings → Secrets and variables → Actions

Add these secrets:

| Secret Name | Value | Notes |
|-------------|-------|-------|
| `SF_PRIVATE_KEY` | Content of `sf_private_key.pem` | Snowflake private key |
| `GOOGLE_OAUTH_CLIENT_ID` | Your OAuth client ID | For Streamlit auth |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Your OAuth client secret | For Streamlit auth |
| `STREAMLIT_COOKIE_SECRET` | Random 32-char string | Generate with `openssl rand -hex 16` |

### Step 4.5: Configure GitHub Workflows

```bash
# Copy config template
cd .github/workflows
cp config.example.txt config.yml

# Edit config.yml with your settings
nano config.yml
```

Update these values in `config.yml`:

```yaml
gcp:
  project_id: "tfj-mmm-prod"
  project_number: "YOUR_PROJECT_NUMBER"
  region: "europe-west1"
  artifact_repo: "mmm-repo"
  web_image: "mmm-web"
  training_image: "mmm-training"
  service_name_prod: "mmm-app"
  service_name_dev: "mmm-app-dev"
  bucket: "tfj-mmm-output"
  wif_pool: "github-pool"
  wif_provider: "github-oidc"
  deployer_sa: "github-deployer@tfj-mmm-prod.iam.gserviceaccount.com"
  web_runtime_sa: "mmm-web-service-sa@tfj-mmm-prod.iam.gserviceaccount.com"
  training_runtime_sa: "mmm-training-job-sa@tfj-mmm-prod.iam.gserviceaccount.com"
```

**IMPORTANT**: DO NOT commit `config.yml` - it's in `.gitignore`

---

## Phase 5: Terraform Setup

### Step 5.1: Configure Terraform Backend

```bash
cd infra/terraform

# Create GCS bucket for Terraform state
gsutil mb -l europe-west1 gs://tfj-mmm-terraform-state

# Initialize Terraform
terraform init -backend-config="bucket=tfj-mmm-terraform-state" -backend-config="prefix=envs/prod"
```

### Step 5.2: Update Terraform Variables

Edit `infra/terraform/envs/prod.tfvars`:

```hcl
project_id      = "tfj-mmm-prod"
region          = "europe-west1"
service_name    = "mmm-app"
bucket_name     = "tfj-mmm-output"
```

### Step 5.3: Store Secrets in Secret Manager

```bash
# Snowflake private key
gcloud secrets create sf-private-key-persistent \
  --data-file=sf_private_key.pem \
  --replication-policy=automatic

# OAuth credentials
echo -n "YOUR_OAUTH_CLIENT_ID" | gcloud secrets create streamlit-auth-client-id --data-file=-
echo -n "YOUR_OAUTH_CLIENT_SECRET" | gcloud secrets create streamlit-auth-client-secret --data-file=-
echo -n "YOUR_COOKIE_SECRET" | gcloud secrets create streamlit-auth-cookie-secret --data-file=-
```

---

## Phase 6: Initial Deployment (Dev Environment)

### Step 6.1: Deploy to Dev

```bash
# Commit config.yml (locally, not in git)
git checkout dev
git add .
git commit -m "Configure dev environment"
git push origin dev
```

This triggers `.github/workflows/ci-dev.yml`:
- Builds Docker images
- Pushes to Artifact Registry
- Deploys to Cloud Run (dev service)
- Sets up Cloud Run Jobs

### Step 6.2: Verify Dev Deployment

```bash
# Check workflow status
# Go to: GitHub → Actions tab

# Get service URL
gcloud run services describe mmm-app-dev-web \
  --region=europe-west1 \
  --format="value(status.url)"

# Test the URL in browser
```

---

## Phase 7: Configure Streamlit Application

### Step 7.1: Test Snowflake Connection

1. Open the deployed web application URL
2. Go to "Connect Data" page
3. Enter Snowflake credentials:
   - Account: `YOUR_ACCOUNT.snowflakecomputing.com`
   - User: `mmm_service_user`
   - Warehouse: `MMM_WAREHOUSE`
   - Database: `YOUR_DATABASE`
   - Schema: `YOUR_SCHEMA`
4. Test connection

### Step 7.2: Map Data Columns

1. Go to "Map Data" page
2. Select your marketing data table
3. Map columns to required fields:
   - Date column
   - Dependent variable (e.g., revenue)
   - Media spend columns
   - Context variables
4. Save mapping

---

## Phase 8: Run First Training Job

### Step 8.1: Prepare Training Data

1. Go to "Review Data" page
2. Verify data looks correct
3. Check for missing values
4. Preview statistics

### Step 8.2: Configure Experiment

1. Go to "Run Experiment" page
2. Configure:
   - Model parameters
   - Hyperparameters
   - Adstock transformation
   - Date range
3. Submit experiment

### Step 8.3: Monitor Training

```bash
# List jobs
gcloud run jobs list --region=europe-west1

# Get execution logs
gcloud run jobs executions logs VIEW EXECUTION_NAME --region=europe-west1
```

### Step 8.4: View Results

1. Go to "View Results" page
2. Select your experiment
3. Review model outputs:
   - Response curves
   - Contribution analysis
   - Model fit statistics

---

## Phase 9: Production Deployment

### Step 9.1: Test Thoroughly in Dev

- [ ] Run multiple experiments
- [ ] Verify results accuracy
- [ ] Test all UI pages
- [ ] Check error handling
- [ ] Validate data pipeline

### Step 9.2: Deploy to Production

```bash
# Switch to main branch
git checkout main
git merge dev
git push origin main
```

This triggers `.github/workflows/ci.yml` → deploys to production.

### Step 9.3: Configure Production Access

```bash
# Set up IAM for authorized users
gcloud run services add-iam-policy-binding mmm-app-web \
  --region=europe-west1 \
  --member="user:analyst@tfj-buycycle.com" \
  --role="roles/run.invoker"
```

---

## Phase 10: Ongoing Operations

### Monitoring

```bash
# View logs
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=mmm-app-web" --limit 50

# View metrics
# Go to: Cloud Console → Cloud Run → Select service → Metrics
```

### Backups

```bash
# Backup GCS bucket
gsutil -m rsync -r gs://tfj-mmm-output gs://tfj-mmm-backup
```

### Updates

When Meshed Data Consulting provides updates:
1. Receive new distribution package
2. Verify checksums
3. Review changelog
4. Test in dev environment
5. Deploy to production

---

## Support &amp; Troubleshooting

### Common Issues

**Issue**: "Permission denied" errors
- **Solution**: Verify service account IAM roles

**Issue**: Snowflake connection fails
- **Solution**: Check private key, user permissions, network access

**Issue**: Training job fails
- **Solution**: Check logs, verify data format, ensure sufficient resources

**Issue**: GitHub Actions fails
- **Solution**: Verify Workload Identity Federation, check GitHub Secrets

### Getting Help

- **Email**: support@mesheddata.com
- **License ID**: LIC-TFJ-001
- **Response Time**: Within 24 hours (business days)

---

## Security Best Practices

✅ **DO**:
- Use private GitHub repository
- Enable 2FA on all accounts
- Use service accounts (not personal accounts)
- Rotate secrets regularly
- Monitor access logs
- Keep distribution package secure
- Follow principle of least privilege

❌ **DON'T**:
- Commit secrets to git
- Share license with unauthorized parties
- Redistribute software
- Use in production without testing
- Expose Cloud Run services publicly without auth
- Store credentials in code

---

## License Compliance

Per your LICENSE_AUTHORIZATION.txt:

✅ **Permitted**:
- Install on your infrastructure
- Modify for internal use
- Use for internal business purposes
- Access by authorized TFJ Buycycle GmbH employees

❌ **Prohibited**:
- Redistribute to third parties
- Offer as a service to others
- Remove license notices
- Sublicense to others
- Use after expiration (2028-02-01)

---

## Timeline Estimate

| Phase | Duration | Notes |
|-------|----------|-------|
| GCP Setup | 1-2 days | Account creation, APIs, IAM |
| Snowflake Setup | 1 day | User creation, permissions |
| GitHub Setup | 1 day | Repository, WIF, secrets |
| Dev Deployment | 1-2 days | Initial deployment, testing |
| Configuration | 2-3 days | Data mapping, first runs |
| Production Deployment | 1 day | After thorough dev testing |
| **Total** | **1-2 weeks** | Depending on complexity |

---

## Success Checklist

- [ ] Distribution verified and extracted
- [ ] GCP project configured
- [ ] Snowflake connection working
- [ ] GitHub repository set up
- [ ] Workload Identity Federation working
- [ ] Dev environment deployed
- [ ] First training job successful
- [ ] Results validated
- [ ] Production deployed
- [ ] Team trained on usage
- [ ] Documentation reviewed
- [ ] Support contact saved

---

**Welcome to MMM Trainer!**

You're now ready to leverage advanced Marketing Mix Modeling for data-driven marketing decisions.

For questions or support, contact Meshed Data Consulting:
- Email: support@mesheddata.com
- License ID: LIC-TFJ-001
