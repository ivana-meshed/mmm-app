#!/bin/bash
# deploy.sh — build, push, and deploy MMM to production.
#
# Usage:
#   ./scripts/deploy.sh [--env prod|dev] [--skip-build] [--skip-tf]
#
# Defaults to --env prod. Logs are written to ./run_<timestamp>/.
#
# Prerequisites:
#   - gcloud CLI installed and on PATH
#   - docker installed and on PATH
#   - terraform installed and on PATH
#   - Sufficient IAM permissions (Artifact Registry Writer, Cloud Run Developer,
#     and whatever Terraform manages in the project)

set -euo pipefail

## ── defaults ────────────────────────────────────────────────────────────────
ENV="prod"
SKIP_BUILD=false
SKIP_TF=false
PROJECT_ID="datawarehouse-422511"
REGION="europe-west1"
REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/mmm-repo"

## ── argument parsing ────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env)      ENV="$2"; shift 2 ;;
        --skip-build) SKIP_BUILD=true; shift ;;
        --skip-tf)  SKIP_TF=true; shift ;;
        -h|--help)
            echo "Usage: $0 [--env prod|dev] [--skip-build] [--skip-tf]"
            exit 0 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

if [[ "$ENV" != "prod" && "$ENV" != "dev" ]]; then
    echo "ERROR: --env must be 'prod' or 'dev'" >&2
    exit 1
fi

TFVARS="infra/terraform/envs/${ENV}.tfvars"
if [[ ! -f "$TFVARS" ]]; then
    echo "ERROR: $TFVARS not found" >&2
    exit 1
fi

## ── timestamped log dir ─────────────────────────────────────────────────────
TS=$(date +%Y%m%d_%H%M%S)
LOG_DIR="./run_${TS}"
mkdir -p "${LOG_DIR}"
echo "Logs: ${LOG_DIR}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

## ── 1. authenticate ─────────────────────────────────────────────────────────
log "Authenticating with gcloud..."
gcloud auth login
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

## ── 2. build images ─────────────────────────────────────────────────────────
WEB_IMAGE="${REGISTRY}/mmm-web:latest"
BASE_IMAGE="${REGISTRY}/mmm-training-base:latest"
TRAINING_IMAGE="${REGISTRY}/mmm-training:latest"

if [[ "$SKIP_BUILD" == "false" ]]; then
    log "Building web image..."
    docker build \
        --platform linux/amd64 \
        -f docker/Dockerfile.web \
        -t "${WEB_IMAGE}" \
        . 2>&1 | tee "${LOG_DIR}/build_web.log"

    log "Building training base image..."
    docker build \
        --platform linux/amd64 \
        -f docker/Dockerfile.training-base \
        -t "${BASE_IMAGE}" \
        . 2>&1 | tee "${LOG_DIR}/build_training_base.log"

    log "Building training job image..."
    docker build \
        --platform linux/amd64 \
        -f docker/Dockerfile.training \
        --build-arg BASE_REF="${BASE_IMAGE}" \
        -t "${TRAINING_IMAGE}" \
        . 2>&1 | tee "${LOG_DIR}/build_training.log"

    ## ── 3. push images ──────────────────────────────────────────────────────
    log "Pushing web image..."
    docker push "${WEB_IMAGE}" 2>&1 | tee "${LOG_DIR}/push_web.log"

    log "Pushing training base image..."
    docker push "${BASE_IMAGE}" 2>&1 | tee "${LOG_DIR}/push_training_base.log"

    log "Pushing training job image..."
    docker push "${TRAINING_IMAGE}" 2>&1 | tee "${LOG_DIR}/push_training.log"
else
    log "Skipping image build/push (--skip-build)"
fi

## ── 4. terraform ────────────────────────────────────────────────────────────
if [[ "$SKIP_TF" == "false" ]]; then
    log "Running Terraform (env=${ENV})..."
    cd infra/terraform

    terraform init \
        -input=false \
        -reconfigure \
        -backend-config="prefix=envs/${ENV}" \
        2>&1 | tee "../../${LOG_DIR}/tf_init.log"

    terraform workspace new "${ENV}" 2>/dev/null || terraform workspace select "${ENV}"

    terraform apply \
        -var-file="envs/${ENV}.tfvars" \
        -var "web_image=${WEB_IMAGE}" \
        -var "training_image=${TRAINING_IMAGE}" \
        -auto-approve \
        2>&1 | tee "../../${LOG_DIR}/tf_apply.log"

    cd ../..
else
    log "Skipping Terraform (--skip-tf)"
fi

log "Done. Logs written to ${LOG_DIR}/"
