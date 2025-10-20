gcloud config get-value project
gcloud projects list --format="table(projectId,name)"


export PROJECT_ID=datawarehouse-422511
export REGION=europe-west1
export BUCKET=mmm-app-output

# If you use local key file for GCS:
#export GOOGLE_APPLICATION_CREDENTIALS="$HOME/Downloads/datawarehouse-422511-cf521161e016.json"
gcloud auth login
gcloud auth application-default login
export GOOGLE_APPLICATION_CREDENTIALS=""   # let ADC use your user creds

# Where to save artifacts in GCS:
export GCS_BUCKET="mmm-app-output"

# Optional (your app defaults to /app):
export APP_ROOT="$(pwd)/app"

streamlit run app/0_Connect_Your_Data.py



cd mmm-app/

gcloud storage buckets create gs://${BUCKET} \
  --location=${REGION} \
  --uniform-bucket-level-access

gcloud services enable artifactregistry.googleapis.com

# rebuild
#docker build -t mmm-app:local -f docker/Dockerfile .

# run locally
#docker run --rm -e PORT=8080 -p 8080:8080 mmm-app:local

# open http://localhost:8080

docker buildx create --use --name mmmbuilder || docker buildx use mmmbuilder
docker buildx inspect --bootstrap

docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t ${REGION}-docker.pkg.dev/${PROJECT_ID}/mmm-repo/mmm-app:latest \
  -f docker/Dockerfile \
  . \
  --push


gcloud auth configure-docker ${REGION}-docker.pkg.dev
#docker tag mmm-app:local ${REGION}-docker.pkg.dev/${PROJECT_ID}/mmm-repo/mmm-app:latest
#docker push ${REGION}-docker.pkg.dev/${PROJECT_ID}/mmm-repo/mmm-app:latest


# Create a Docker repo named "mmm-repo" in your region
gcloud artifacts repositories create mmm-repo \
  --repository-format=docker \
  --location=${REGION} \
  --description="Repo for MMM app images"

# (Re)configure docker auth for Artifact Registry
gcloud services enable run.googleapis.com
# (Nice to have)
gcloud services enable cloudbuild.googleapis.com


# Terraform apply
cd infra/terraform
terraform init

# If file is named terraform.tfvars or *.auto.tfvars, you can omit -var-file
terraform plan   -var-file="terraform.tfvars"
terraform apply  -var-file="terraform.tfvars"

#terraform destroy -var-file="terraform.tfvars"




docker buildx build --platform linux/amd64,linux/arm64 -t ${REGION}-docker.pkg.dev/${PROJECT_ID}/mmm-repo/mmm-app:latest -f docker/Dockerfile . --push
terraform taint google_cloud_run_service.svc
terraform apply -var-file="terraform.tfvars"

cd ../..
docker buildx build --platform linux/amd64,linux/arm64 -t ${REGION}-docker.pkg.dev/${PROJECT_ID}/mmm-repo/mmm-app:latest -f docker/Dockerfile . --push
cd infra/terraform
terraform apply -replace=google_cloud_run_service.svc


cd ../..
docker buildx build --platform linux/amd64,linux/arm64 -t ${REGION}-docker.pkg.dev/${PROJECT_ID}/mmm-repo/mmm-app:latest -f docker/Dockerfile . --push
cd infra/terraform
terraform apply -replace=google_cloud_run_service.svc -auto-approve
terraform apply -replace=google_cloud_run_service.svc -auto-approve
