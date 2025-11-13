# GitHub Actions GCP Setup Guide

This guide walks through setting up Google Cloud Platform (GCP) authentication for GitHub Actions using Workload Identity Federation (WIF), which is the recommended secure method that doesn't require storing long-lived service account keys.

## Prerequisites

- GCP project with billing enabled
- `gcloud` CLI installed and authenticated
- GitHub repository admin access
- Project Owner or sufficient IAM permissions in GCP

## Step 1: Enable Required APIs

Enable the necessary Google Cloud APIs for the project:

```bash
# Set your project ID
export PROJECT_ID=your-project-id
gcloud config set project $PROJECT_ID

# Get your project number
export PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
echo "Project Number: $PROJECT_NUMBER"

# Enable required APIs
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com \
  cloudscheduler.googleapis.com \
  iamcredentials.googleapis.com
```

## Step 2: Create Workload Identity Pool and Provider

This allows GitHub Actions to authenticate with GCP without storing service account keys.

### Create Workload Identity Pool

```bash
# Create the pool
gcloud iam workload-identity-pools create github-pool \
  --location=global \
  --description="Pool for GitHub Actions" \
  --display-name="GitHub Actions Pool"

# Verify creation
gcloud iam workload-identity-pools describe github-pool \
  --location=global
```

### Create Workload Identity Provider

```bash
# Create provider for GitHub Actions
gcloud iam workload-identity-pools providers create-oidc github-oidc \
  --location=global \
  --workload-identity-pool=github-pool \
  --issuer-uri=https://token.actions.githubusercontent.com \
  --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
  --attribute-condition="assertion.repository_owner=='ivana-meshed'"

# Verify creation
gcloud iam workload-identity-pools providers describe github-oidc \
  --location=global \
  --workload-identity-pool=github-pool
```

**Note**: The `attribute-condition` restricts access to repositories owned by `ivana-meshed`. Adjust if needed.

## Step 3: Create Service Accounts

Create service accounts for GitHub Actions deployment and runtime operations.

### Deployer Service Account (for GitHub Actions)

```bash
# Create deployer service account
gcloud iam service-accounts create github-deployer \
  --description="Service account for GitHub Actions deployments" \
  --display-name="GitHub Deployer"

# Get the service account email
export DEPLOYER_SA=github-deployer@${PROJECT_ID}.iam.gserviceaccount.com
echo "Deployer SA: $DEPLOYER_SA"
```

### Runtime Service Accounts

```bash
# Create web service runtime account
gcloud iam service-accounts create mmm-web-service-sa \
  --description="Service account for MMM web service" \
  --display-name="MMM Web Service SA"

# Create training job runtime account
gcloud iam service-accounts create mmm-training-job-sa \
  --description="Service account for MMM training jobs" \
  --display-name="MMM Training Job SA"

# Create scheduler service account
gcloud iam service-accounts create robyn-queue-scheduler \
  --description="Service account for queue scheduler" \
  --display-name="Robyn Queue Scheduler"

# Export the service account emails
export WEB_SA=mmm-web-service-sa@${PROJECT_ID}.iam.gserviceaccount.com
export TRAINING_SA=mmm-training-job-sa@${PROJECT_ID}.iam.gserviceaccount.com
export SCHEDULER_SA=robyn-queue-scheduler@${PROJECT_ID}.iam.gserviceaccount.com

echo "Web SA: $WEB_SA"
echo "Training SA: $TRAINING_SA"
echo "Scheduler SA: $SCHEDULER_SA"
```

## Step 4: Configure Workload Identity Federation

Allow the GitHub Actions workflow to impersonate the deployer service account:

```bash
# Allow GitHub Actions from your repository to impersonate the deployer SA
gcloud iam service-accounts add-iam-policy-binding $DEPLOYER_SA \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-pool/attribute.repository/ivana-meshed/mmm-app" \
  --role="roles/iam.workloadIdentityUser"

# Verify the binding
gcloud iam service-accounts get-iam-policy $DEPLOYER_SA
```

## Step 5: Grant Permissions to Deployer Service Account

The deployer needs permissions to deploy and manage resources:

```bash
# Cloud Run admin (to deploy services and jobs)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$DEPLOYER_SA" \
  --role="roles/run.admin"

# Service Account User (to act as other service accounts)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$DEPLOYER_SA" \
  --role="roles/iam.serviceAccountUser"

# Artifact Registry admin (to push images)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$DEPLOYER_SA" \
  --role="roles/artifactregistry.admin"

# Storage admin (to manage GCS buckets)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$DEPLOYER_SA" \
  --role="roles/storage.admin"

# Secret Manager admin (to create/update secrets)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$DEPLOYER_SA" \
  --role="roles/secretmanager.admin"

# Cloud Scheduler admin (to create/update scheduled jobs)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$DEPLOYER_SA" \
  --role="roles/cloudscheduler.admin"

# IAM admin (for Terraform to manage IAM policies)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$DEPLOYER_SA" \
  --role="roles/resourcemanager.projectIamAdmin"
```

## Step 6: Create Artifact Registry Repository

Create a repository to store container images:

```bash
# Set region
export REGION=europe-west1

# Create Artifact Registry repository
gcloud artifacts repositories create mmm-repo \
  --repository-format=docker \
  --location=$REGION \
  --description="MMM application container images"

# Verify creation
gcloud artifacts repositories describe mmm-repo \
  --location=$REGION
```

## Step 7: Create GCS Bucket for Terraform State

```bash
# Create bucket for Terraform state
gsutil mb -l $REGION gs://mmm-tf-state/

# Enable versioning
gsutil versioning set on gs://mmm-tf-state/

# Set lifecycle policy (optional - keep last 10 versions)
cat > lifecycle.json <<EOF
{
  "lifecycle": {
    "rule": [
      {
        "action": {"type": "Delete"},
        "condition": {
          "numNewerVersions": 10
        }
      }
    ]
  }
}
EOF

gsutil lifecycle set lifecycle.json gs://mmm-tf-state/
rm lifecycle.json
```

## Step 8: Create GCS Bucket for Application Data

```bash
# Create application data bucket
export GCS_BUCKET=mmm-app-output
gsutil mb -l $REGION gs://$GCS_BUCKET/

# Enable versioning
gsutil versioning set on gs://$GCS_BUCKET/

# Set uniform bucket-level access
gsutil uniformbucketlevelaccess set on gs://$GCS_BUCKET/
```

## Step 9: Configure GitHub Secrets

Add the following secrets to your GitHub repository:

1. Go to your GitHub repository
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Add these secrets:

### Required Secrets

| Secret Name | Description | How to Get |
|------------|-------------|-----------|
| `SF_PRIVATE_KEY` | Snowflake private key (PEM format) | Your Snowflake private key file content |
| `GOOGLE_OAUTH_CLIENT_ID` | Google OAuth client ID | From Google Cloud Console → APIs & Services → Credentials |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Google OAuth client secret | From Google Cloud Console → APIs & Services → Credentials |
| `STREAMLIT_COOKIE_SECRET` | Cookie encryption secret | Generate with: `openssl rand -hex 32` |

**Note**: The GitHub workflows use Workload Identity Federation, so you don't need to store service account keys as secrets.

## Step 10: Update GitHub Workflow Files

The workflow files in `.github/workflows/` are already configured to use Workload Identity Federation. Verify these environment variables match your setup:

```yaml
# In ci.yml and ci-dev.yml
env:
  PROJECT_ID: your-project-id  # Update this
  PROJECT_NUMBER: "your-project-number"  # Update this
  REGION: europe-west1
  WIF_POOL: github-pool
  WIF_PROVIDER: github-oidc
  SA_EMAIL: github-deployer@your-project-id.iam.gserviceaccount.com  # Update this
```

## Step 11: Verify the Setup

### Test WIF Configuration

```bash
# Get the WIF provider resource name
gcloud iam workload-identity-pools providers describe github-oidc \
  --location=global \
  --workload-identity-pool=github-pool \
  --format='value(name)'

# This should output something like:
# projects/123456789/locations/global/workloadIdentityPools/github-pool/providers/github-oidc
```

### Verify Service Account Bindings

```bash
# Check deployer SA bindings
gcloud projects get-iam-policy $PROJECT_ID \
  --flatten="bindings[].members" \
  --format="table(bindings.role)" \
  --filter="bindings.members:$DEPLOYER_SA"

# Check WIF binding on deployer SA
gcloud iam service-accounts get-iam-policy $DEPLOYER_SA
```

## Step 12: Test GitHub Actions Workflow

Now you can test the deployment:

### Option A: Push to Main Branch
```bash
git checkout main
git push origin main
```

### Option B: Manual Trigger
1. Go to **Actions** tab in GitHub
2. Select **CI (GCP)** workflow
3. Click **Run workflow**
4. Select branch and deployment target
5. Click **Run workflow**

## Troubleshooting

### Error: "Failed to generate Google Cloud access token"

**Cause**: Workload Identity Federation is not configured correctly.

**Solution**: Verify the WIF binding:
```bash
gcloud iam service-accounts get-iam-policy $DEPLOYER_SA
```

Ensure there's a binding with:
- `principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-pool/attribute.repository/ivana-meshed/mmm-app`
- `roles/iam.workloadIdentityUser`

### Error: "Permission denied" during deployment

**Cause**: The deployer service account doesn't have sufficient permissions.

**Solution**: Verify all necessary roles are granted:
```bash
gcloud projects get-iam-policy $PROJECT_ID \
  --flatten="bindings[].members" \
  --format="table(bindings.role)" \
  --filter="bindings.members:$DEPLOYER_SA"
```

### Error: "Artifact Registry repository not found"

**Cause**: The repository hasn't been created.

**Solution**: Create the repository:
```bash
gcloud artifacts repositories create mmm-repo \
  --repository-format=docker \
  --location=$REGION
```

### Error: "API not enabled"

**Cause**: Required APIs haven't been enabled.

**Solution**: Enable all required APIs:
```bash
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  storage.googleapis.com
```

### Error: "Invalid JWT" or "Token verification failed"

**Cause**: The GitHub Actions workflow configuration doesn't match the WIF provider.

**Solution**: Verify:
1. The repository name in the WIF provider attribute condition matches your repository
2. The workflow has `id-token: write` permission
3. The WIF provider is configured with the correct issuer URI

## Summary

After completing these steps, your GitHub Actions workflows will be able to:

✅ Authenticate with GCP using Workload Identity Federation (no service account keys)  
✅ Push Docker images to Artifact Registry  
✅ Deploy Cloud Run services and jobs  
✅ Manage secrets in Secret Manager  
✅ Create and manage GCS buckets  
✅ Configure Cloud Scheduler jobs  

## Quick Reference

```bash
# Get WIF provider resource name (needed for workflow)
gcloud iam workload-identity-pools providers describe github-oidc \
  --location=global \
  --workload-identity-pool=github-pool \
  --format='value(name)'

# Get project number
gcloud projects describe $PROJECT_ID --format='value(projectNumber)'

# List service accounts
gcloud iam service-accounts list

# Verify deployer permissions
gcloud projects get-iam-policy $PROJECT_ID \
  --flatten="bindings[].members" \
  --filter="bindings.members:github-deployer@$PROJECT_ID.iam.gserviceaccount.com"
```

## Next Steps

After setup is complete:
1. Trigger the workflow manually or push to main branch
2. Monitor the workflow in the Actions tab
3. If successful, Terraform will create/update all GCP infrastructure
4. Get the Cloud Run service URL from Terraform outputs or the GCP Console

For the full deployment and local development guide, see:
- [DEVELOPMENT.md](../DEVELOPMENT.md) - Local development setup
- [README.md](../README.md) - Project overview and deployment
- [DEPLOYMENT_SELECTOR.md](DEPLOYMENT_SELECTOR.md) - Multi-cloud deployment options

## Additional Resources

- [Workload Identity Federation Documentation](https://cloud.google.com/iam/docs/workload-identity-federation)
- [GitHub Actions with GCP](https://github.com/google-github-actions/auth)
- [Cloud Run Documentation](https://cloud.google.com/run/docs)
- [Artifact Registry Documentation](https://cloud.google.com/artifact-registry/docs)
