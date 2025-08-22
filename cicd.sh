# 0) Vars
PROJECT_ID=datawarehouse-422511
PROJECT_ID="your-project-id"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
POOL="github-pool"
PROVIDER="github-oidc"
REPO="ivana-meshed/mmm-app"                 # e.g. my-org/my-repo
BRANCH="refs/heads/main"        # or a tag: refs/tags/v1.2.3
SA_ID="mmm-deployer"  # Service Account ID (name)



# 1) Create a pool & provider that trusts your GitHub org/repo
gcloud iam workload-identity-pools create $POOL --location=global \
  --display-name="GitHub OIDC Pool"

gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
  --project="$PROJECT_ID" \
  --location="global" \
  --workload-identity-pool="$POOL" \
  --display-name="GitHub OIDC Provider" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.aud=assertion.aud,attribute.repository=assertion.repository,attribute.ref=assertion.ref,attribute.workflow=assertion.workflow" \
  --attribute-condition="assertion.repository=='$REPO' && assertion.ref=='$BRANCH'"


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

