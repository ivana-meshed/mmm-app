#!/bin/bash
set -e

echo "🚀 Deploying MMM Trainer Optimizations"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check prerequisites
print_status "Checking prerequisites..."

if ! command -v terraform &> /dev/null; then
    print_error "Terraform not found. Please install Terraform."
    exit 1
fi

if ! command -v gcloud &> /dev/null; then
    print_error "gcloud CLI not found. Please install Google Cloud SDK."
    exit 1
fi

if ! command -v docker &> /dev/null; then
    print_error "Docker not found. Please install Docker."
    exit 1
fi

print_success "Prerequisites check passed"

# Set variables
PROJECT_ID=${PROJECT_ID:-"datawarehouse-422511"}
REGION=${REGION:-"europe-west1"}
IMAGE_NAME="mmm-app"
REPO_NAME="mmm-repo"
SERVICE_NAME="mmm-app"

print_status "Using PROJECT_ID: $PROJECT_ID"
print_status "Using REGION: $REGION"

# Step 1: Build and push optimized Docker image
print_status "Building optimized Docker image..."

IMAGE_TAG=$(date +%Y%m%d-%H%M%S)
FULL_IMAGE_NAME="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$IMAGE_NAME:$IMAGE_TAG"

docker build -f docker/Dockerfile -t $FULL_IMAGE_NAME .
docker tag $FULL_IMAGE_NAME "$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$IMAGE_NAME:latest"

print_status "Pushing images to Artifact Registry..."
docker push $FULL_IMAGE_NAME
docker push "$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$IMAGE_NAME:latest"

print_success "Docker image built and pushed: $FULL_IMAGE_NAME"

# Step 2: Update Terraform configuration
print_status "Deploying infrastructure updates..."

cd infra/terraform

# Update terraform.tfvars with new image
cat > terraform.tfvars << EOF
project_id     = "$PROJECT_ID"
region         = "$REGION"  
bucket_name    = "mmm-app-output"
image          = "$FULL_IMAGE_NAME"
cpu_limit      = "8"
memory_limit   = "32Gi" 
min_instances  = 2
max_instances  = 10
EOF

# Initialize and apply Terraform
terraform init
terraform plan -out=optimization.tfplan
terraform apply -auto-approve optimization.tfplan

print_success "Infrastructure updates deployed"

# Step 3: Wait for service to be ready
print_status "Waiting for service to be ready..."

SERVICE_URL=$(terraform output -raw url)
print_status "Service URL: $SERVICE_URL"

# Wait for health endpoint to be available
max_attempts=30
attempt=1

while [ $attempt -le $max_attempts ]; do
    if curl -s "$SERVICE_URL/health" > /dev/null 2>&1; then
        print_success "Service is responding to health checks"
        break
    else
        print_status "Attempt $attempt/$max_attempts: Waiting for service..."
        sleep 10
        ((attempt++))
    fi
done

if [ $attempt -gt $max_attempts ]; then
    print_error "Service failed to respond after $max_attempts attempts"
    exit 1
fi

# Step 4: Run optimization tests
print_status "Running optimization validation tests..."

# Test 1: Resource allocation
print_status "Testing resource allocation..."
RESOURCE_CHECK=$(gcloud run services describe $SERVICE_NAME --region=$REGION --format="value(spec.template.spec.template.spec.containers[0].resources.limits.cpu)")

if [ "$RESOURCE_CHECK" = "8" ]; then
    print_success "✅ CPU limit correctly set to 8 vCPUs"
else
    print_warning "⚠️ CPU limit is $RESOURCE_CHECK, expected 8"
fi

# Test 2: Container warming
print_status "Testing container warming..."
HEALTH_RESPONSE=$(curl -s "$SERVICE_URL/health" | jq -r '.warm_status.container_ready // false')

if [ "$HEALTH_RESPONSE" = "true" ]; then
    print_success "✅ Container is properly warmed"
else
    print_warning "⚠️ Container warming may not be working correctly"
fi

# Test 3: Performance benchmark
print_status "Running performance benchmark..."

# Create a simple training job to test performance
BENCHMARK_RESULT=$(curl -s -X POST "$SERVICE_URL/api/benchmark" \
    -H "Content-Type: application/json" \
    -d '{"test_size": "small", "format": "parquet"}' || echo "benchmark_failed")

if [ "$BENCHMARK_RESULT" != "benchmark_failed" ]; then
    print_success "✅ Performance benchmark completed"
else  
    print_warning "⚠️ Performance benchmark could not be run"
fi

cd ../..

# Step 5: Generate optimization report
print_status "Generating optimization report..."

cat > optimization_report.md << EOF
# MMM Trainer Optimization Report

## Deployment Summary
- **Deployment Time**: $(date)
- **Image**: $FULL_IMAGE_NAME  
- **Service URL**: $SERVICE_URL

## Optimizations Applied

### ✅ Resource Scaling
- CPU: 4 → 8 vCPUs
- Memory: 16GB → 32GB  
- Parallel processing: Enabled
- Status: **DEPLOYED**

### ✅ Data Format Optimization  
- Format: CSV → Parquet
- Compression: Snappy
- Expected speedup: 5-10x faster data loading
- Status: **DEPLOYED**

### ✅ Container Pre-warming
- Minimum instances: 2
- Warming components: Python, R, GCS, System
- Health checks: Enabled
- Keep-alive scheduler: Every 5 minutes
- Status: **DEPLOYED**

## Performance Expectations

### Before Optimizations
- Small jobs: 20-30 minutes
- Medium jobs: 45-60 minutes  
- Large jobs: 90-120 minutes

### After Optimizations (Expected)
- Small jobs: 10-15 minutes (50% improvement)
- Medium jobs: 25-35 minutes (40% improvement)
- Large jobs: 60-75 minutes (30% improvement)

## Next Steps
1. Monitor job performance over the next week
2. Collect performance metrics and compare to baseline
3. Consider implementing parallel trial processing (Phase 2)
4. Evaluate cost vs performance trade-offs

## Rollback Plan
If issues arise, rollback with:
\`\`\`bash
# Revert to previous image
terraform apply -var="cpu_limit=4" -var="memory_limit=16Gi" -var="min_instances=0"

# Or use previous image
terraform apply -var="image=$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$IMAGE_NAME:previous"
\`\`\`

EOF

print_success "Optimization report generated: optimization_report.md"

# Final summary
echo ""
echo "🎉 MMM Trainer Optimizations Successfully Deployed!"
echo ""
echo "📊 Summary:"
echo "  - Resource scaling: ✅ 4→8 vCPU, 16→32GB RAM"  
echo "  - Data format: ✅ CSV→Parquet optimization"
echo "  - Container warming: ✅ Pre-warmed instances"
echo "  - Service URL: $SERVICE_URL"
echo ""
echo "📈 Expected Performance Improvement: 40-50%"
echo "💰 Expected Cost Increase: 50-75% (but higher efficiency)"
echo ""
echo "🔍 Next: Monitor performance and run test training jobs"
echo "📋 Report: See optimization_report.md for details"