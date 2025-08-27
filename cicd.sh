# 0) Vars
PROJECT_ID=datawarehouse-422511
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
POOL="github-pool"
PROVIDER="github-oidc"
REPO="ivana-meshed/mmm-app"                 # e.g. my-org/my-repo
BRANCH="refs/heads/main"        # or a tag: refs/tags/v1.2.3
SA_ID="mmm-deployer"  # Service Account ID (name)

PROJECT_ID="datawarehouse-422511"
PROJECT_NUMBER="321233323695"
POOL_ID="github-pool"
PROVIDER_ID="github-oidc"
REPO="ivana-meshed/mmm-app"
SA_EMAIL="gh-actions-deployer@${PROJECT_ID}.iam.gserviceaccount.com"

#############################################################################
PROJECT_ID="datawarehouse-422511"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
REGION="europe-west1"
POOL="github-pool"
PROVIDER="github-oidc"
REPO="ivana-meshed/mmm-app"   # org/repo
DEPLOYER_SA_ID="github-deployer"
DEPLOYER_SA="${DEPLOYER_SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"

# Pool
gcloud iam workload-identity-pools describe "$POOL" --location=global \
  || gcloud iam workload-identity-pools create "$POOL" \
       --location=global --display-name="GitHub OIDC pool"

# Provider (maps GitHub claims we’ll use)
gcloud iam workload-identity-pools providers describe "$PROVIDER" \
  --workload-identity-pool="$POOL" --location=global \
  || gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
       --workload-identity-pool="$POOL" --location=global \
       --display-name="GitHub OIDC Provider" \
       --issuer-uri="https://token.actions.githubusercontent.com" \
       --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref"


gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER_SA" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO}"

gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com iam.googleapis.com serviceusage.googleapis.com

# Cloud Run deploy/admin

gcloud storage buckets add-iam-policy-binding gs://mmm-app-output \
  --member="serviceAccount:${DEPLOYER_SA}" \
  --role="roles/storage.objectAdmin"

# Artifact Registry admin (or reader if TF only reads)
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DEPLOYER_SA}" \
  --role="roles/artifactregistry.admin"

# Can create/manage service accounts for the runtime SA
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DEPLOYER_SA}" \
  --role="roles/iam.serviceAccountAdmin"

# Allow TF to grant bucket object permissions
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${DEPLOYER_SA}" \
  --role="roles/storage.admin"



##############################################################################



# Ensure the pool exists
gcloud iam workload-identity-pools create "$POOL_ID" \
  --location=global \
  --display-name="GitHub OIDC pool" \
  --project="$PROJECT_ID" || true

# Create/update the OIDC provider with correct issuer, mappings, and condition
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
  --project="$PROJECT_ID" \
  --location=global \
  --workload-identity-pool="$POOL_ID" \
  --display-name="GitHub OIDC Provider" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref,attribute.actor=assertion.actor,attribute.workflow=assertion.workflow" \
  --attribute-condition="attribute.repository=='${REPO}' && (attribute.ref.startsWith('refs/heads/') || attribute.ref.startsWith('refs/pull/'))" 

gcloud iam workload-identity-pools providers update-oidc "$PROVIDER_ID" \
  --project="$PROJECT_ID" \
  --location=global \
  --workload-identity-pool="$POOL_ID" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref,attribute.actor=assertion.actor,attribute.workflow=assertion.workflow" \
  --attribute-condition="attribute.repository=='${REPO}' && (attribute.ref.startsWith('refs/heads/') || attribute.ref.startsWith('refs/pull/'))"


# 2) Create the deployer SA and grant it least-privilege roles
gcloud iam service-accounts create $SA_ID --display-name="GitHub Actions Deployer"

SA_EMAIL="$SA_ID@$PROJECT_ID.iam.gserviceaccount.com"
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/run.admin"
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/artifactregistry.admin"
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/storage.admin"
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/iam.serviceAccountUser"

# 3) Let your GitHub repo impersonate the SA
POOL_ID=$(gcloud iam workload-identity-pools describe $POOL --location=global --format='value(name)')
gcloud iam service-accounts add-iam-policy-binding $SA_EMAIL \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${POOL_ID}/attribute.repository:ivana-meshed/mmm-app"


In GitHub → Settings → Secrets and variables → Actions → Variables, add:

GCP_PROJECT_ID = datawarehouse-422511

GAR_REGION = europe-west1

GAR_REPO = mmm-repo

CLOUD_RUN_SERVICE_STG = mmm-app-staging

CLOUD_RUN_SERVICE_PROD = mmm-app

CLOUD_RUN_REGION = europe-west1

And in Secrets, add:

WIF_PROVIDER = projects/…/locations/global/workloadIdentityPools/github/providers/github

WIF_SERVICE_ACCOUNT = gh-actions-deployer@datawarehouse-422511.iam.gserviceaccount.com

