# AWS Deployment Guide

This guide provides detailed instructions for deploying the MMM application on Amazon Web Services (AWS) using ECS/Fargate, S3, and related services.

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [AWS Setup](#aws-setup)
- [Deployment Steps](#deployment-steps)
- [Local Testing](#local-testing-for-aws)
- [Comparison: GCP vs AWS](#comparison-gcp-vs-aws)
- [Troubleshooting](#troubleshooting)

---

## Overview

The AWS deployment uses the following services:
- **Amazon ECS (Fargate)**: Container orchestration for web service and training tasks
- **Amazon S3**: Object storage for data and artifacts
- **Amazon ECR**: Container registry for Docker images
- **AWS Secrets Manager**: Secure storage for sensitive configuration
- **Application Load Balancer**: HTTP/HTTPS access to the web service
- **VPC**: Isolated network environment
- **CloudWatch**: Logging and monitoring

This deployment is equivalent to the Google Cloud deployment but uses AWS-native services.

---

## Architecture

### High-Level Components

```
┌─────────────────────────────────────────────────────────────┐
│                    AWS Cloud Environment                     │
│                                                              │
│  ┌──────────────────┐        ┌─────────────────────────┐   │
│  │  Application     │        │  ECS Fargate Service    │   │
│  │  Load Balancer   │───────▶│  (Web Interface)        │   │
│  │  (ALB)           │        │  - Streamlit App        │   │
│  └──────────────────┘        │  - 2 vCPU, 4GB RAM      │   │
│          │                   └─────────────────────────┘   │
│          │                              │                   │
│          │                              ▼                   │
│          │                   ┌─────────────────────────┐   │
│  Internet Access             │  ECS Fargate Tasks      │   │
│                              │  (Training Jobs)        │   │
│                              │  - R/Robyn Training     │   │
│                              │  - 8 vCPU, 32GB RAM     │   │
│                              └─────────────────────────┘   │
│                                         │                   │
│                                         ▼                   │
│                              ┌─────────────────────────┐   │
│                              │  Amazon S3 Bucket       │   │
│                              │  - Training Data        │   │
│                              │  - Model Artifacts      │   │
│                              │  - Results              │   │
│                              └─────────────────────────┘   │
│                                                              │
│  ┌──────────────────┐        ┌─────────────────────────┐   │
│  │  AWS Secrets     │        │  Amazon ECR             │   │
│  │  Manager         │        │  - Web Image            │   │
│  │  - Snowflake Key │        │  - Training Image       │   │
│  │  - OAuth Secrets │        │  - Training Base        │   │
│  └──────────────────┘        └─────────────────────────┘   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow

1. User accesses web interface via ALB
2. Web service authenticates user (Google OAuth)
3. User connects to Snowflake and fetches data
4. Data is optimized and uploaded to S3
5. Job configuration is created and stored in S3
6. ECS Fargate task is launched for training
7. Training task reads config and data from S3
8. R/Robyn processes data and generates artifacts
9. Results are written back to S3
10. Web service displays results to user

---

## Prerequisites

### Required Software

- **AWS CLI v2**: For AWS authentication and management
  ```bash
  # Install AWS CLI
  # https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html
  aws --version
  ```

- **Docker Desktop**: For building and pushing container images
  ```bash
  docker --version
  # Enable Buildx: docker buildx create --use
  ```

- **Terraform 1.5+**: For infrastructure management
  ```bash
  terraform --version
  ```

- **Python 3.11+**: For local development
  ```bash
  python3 --version
  ```

### Required AWS Permissions

You'll need an AWS account with permissions to:
- Create and manage ECS clusters, services, and task definitions
- Create and manage ECR repositories
- Create and manage S3 buckets
- Create and manage Secrets Manager secrets
- Create and manage IAM roles and policies
- Create and manage VPC, subnets, and security groups
- Create and manage Application Load Balancers
- Create and manage CloudWatch log groups

### Required Secrets

Before deployment, you need:
- **Snowflake Private Key**: RSA private key for Snowflake authentication
- **Google OAuth Credentials**:
  - Client ID
  - Client Secret
  - Cookie Secret (random 32+ character string)

---

## AWS Setup

### For GitHub Actions CI/CD (Recommended)

If you want to use the automated GitHub Actions workflows to deploy to AWS, follow the **[GitHub Actions AWS Setup Guide](GITHUB_ACTIONS_AWS_SETUP.md)** first. This guide walks you through:
- Setting up AWS OIDC authentication for GitHub Actions
- Creating the IAM role with necessary permissions
- Creating ECR repositories
- Configuring GitHub secrets

**→ [Complete GitHub Actions AWS Setup Guide](GITHUB_ACTIONS_AWS_SETUP.md)**

### For Manual/Local Deployment

If you want to deploy manually from your local machine:

#### 1. Configure AWS CLI

```bash
# Configure AWS credentials
aws configure

# Inputs:
# - AWS Access Key ID
# - AWS Secret Access Key
# - Default region (e.g., us-east-1)
# - Default output format (json)

# Verify authentication
aws sts get-caller-identity
```

#### 2. Set Environment Variables

Create a `.env` file or export variables:

```bash
# AWS Configuration
export AWS_REGION=us-east-1
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Application Configuration
export SERVICE_NAME=mmm-app
export S3_BUCKET=mmm-app-output-aws
export ENVIRONMENT=prod  # or 'dev'

# Secrets (DO NOT commit these)
export TF_VAR_sf_private_key="$(cat path/to/snowflake_private_key.pem)"
export TF_VAR_auth_client_id="your-google-oauth-client-id"
export TF_VAR_auth_client_secret="your-google-oauth-client-secret"
export TF_VAR_auth_cookie_secret="your-random-cookie-secret"
```

#### 3. Create S3 Bucket for Terraform State

```bash
# Create bucket for Terraform state
aws s3 mb s3://mmm-tf-state --region us-east-1

# Enable versioning
aws s3api put-bucket-versioning \
  --bucket mmm-tf-state \
  --versioning-configuration Status=Enabled

# Enable encryption
aws s3api put-bucket-encryption \
  --bucket mmm-tf-state \
  --server-side-encryption-configuration '{
    "Rules": [{
      "ApplyServerSideEncryptionByDefault": {
        "SSEAlgorithm": "AES256"
      }
    }]
  }'

# Block public access
aws s3api put-public-access-block \
  --bucket mmm-tf-state \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
```

#### 4. Create S3 Bucket for Application Data

```bash
# Create application data bucket
aws s3 mb s3://${S3_BUCKET} --region ${AWS_REGION}

# Enable versioning
aws s3api put-bucket-versioning \
  --bucket ${S3_BUCKET} \
  --versioning-configuration Status=Enabled

# Block public access
aws s3api put-public-access-block \
  --bucket ${S3_BUCKET} \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
```

#### 5. Create ECR Repositories (if not using GitHub Actions)

If you're not using the automated GitHub Actions setup:

```bash
# Create ECR repositories
aws ecr create-repository --repository-name mmm-app-web --region us-east-1
aws ecr create-repository --repository-name mmm-app-training --region us-east-1
aws ecr create-repository --repository-name mmm-app-training-base --region us-east-1
```

---

## Deployment Steps

### Using GitHub Actions (Automated)

After completing the [GitHub Actions AWS Setup](GITHUB_ACTIONS_AWS_SETUP.md):

1. Push to a feature branch or manually trigger the workflow
2. Monitor progress in the Actions tab
3. Retrieve the web service URL from workflow outputs

### Using Manual Deployment

If deploying manually from your local machine:

### Step 1: Build and Push Docker Images

```bash
# Navigate to project root
cd /path/to/mmm-app

# Login to ECR
aws ecr get-login-password --region ${AWS_REGION} | \
  docker login --username AWS --password-stdin \
  ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com

# Create ECR repositories (if they don't exist)
aws ecr create-repository --repository-name mmm-app-web --region ${AWS_REGION} || true
aws ecr create-repository --repository-name mmm-app-training --region ${AWS_REGION} || true
aws ecr create-repository --repository-name mmm-app-training-base --region ${AWS_REGION} || true

# Build and push web service image
docker build -t mmm-app-web:latest -f docker/Dockerfile.web .
docker tag mmm-app-web:latest \
  ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/mmm-app-web:latest
docker push ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/mmm-app-web:latest

# Build and push training base image
docker build -t mmm-app-training-base:latest -f docker/Dockerfile.training-base .
docker tag mmm-app-training-base:latest \
  ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/mmm-app-training-base:latest
docker push ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/mmm-app-training-base:latest

# Build and push training job image
docker build \
  --build-arg BASE_REF=${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/mmm-app-training-base:latest \
  -t mmm-app-training:latest \
  -f docker/Dockerfile.training .
docker tag mmm-app-training:latest \
  ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/mmm-app-training:latest
docker push ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/mmm-app-training:latest
```

### Step 2: Deploy Infrastructure with Terraform

```bash
# Navigate to AWS Terraform directory
cd infra/terraform-aws

# Initialize Terraform
terraform init

# Select workspace (prod or dev)
terraform workspace new prod || terraform workspace select prod

# Review planned changes
terraform plan \
  -var-file="envs/prod.tfvars" \
  -var="web_image=${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/mmm-app-web:latest" \
  -var="training_image=${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/mmm-app-training:latest"

# Apply changes
terraform apply \
  -var-file="envs/prod.tfvars" \
  -var="web_image=${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/mmm-app-web:latest" \
  -var="training_image=${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/mmm-app-training:latest"
```

### Step 3: Verify Deployment

```bash
# Get the web service URL
terraform output web_service_url

# Check ECS service status
aws ecs describe-services \
  --cluster mmm-app-cluster \
  --services mmm-app-web \
  --region ${AWS_REGION}

# Check task status
aws ecs list-tasks \
  --cluster mmm-app-cluster \
  --service-name mmm-app-web \
  --region ${AWS_REGION}

# View CloudWatch logs
aws logs tail /ecs/mmm-app-web --follow --region ${AWS_REGION}
```

### Step 4: Access the Application

1. Get the load balancer URL from Terraform output:
   ```bash
   terraform output web_service_url
   ```

2. Open the URL in your web browser

3. You'll be redirected to Google OAuth for authentication

4. After authentication, you can access the Streamlit application

---

## Local Testing for AWS

### Testing with AWS Credentials

To test the application locally with AWS backend:

```bash
# Set environment variables
export CLOUD_PROVIDER=aws
export AWS_REGION=us-east-1
export S3_BUCKET=mmm-app-output-aws
export TRAINING_TASK_FAMILY=mmm-app-training
export ECS_CLUSTER=mmm-app-cluster

# Set AWS credentials (if not using AWS CLI profiles)
export AWS_ACCESS_KEY_ID=your-access-key-id
export AWS_SECRET_ACCESS_KEY=your-secret-access-key

# Or use AWS CLI profile
export AWS_PROFILE=your-profile-name

# Run Streamlit locally
streamlit run app/streamlit_app.py
```

### Testing with Docker Compose

Create a `docker-compose.yml` for local testing:

```yaml
version: '3.8'

services:
  web:
    build:
      context: .
      dockerfile: docker/Dockerfile.web
    ports:
      - "8080:8080"
    environment:
      - CLOUD_PROVIDER=aws
      - AWS_REGION=us-east-1
      - S3_BUCKET=mmm-app-output-aws
      - TRAINING_TASK_FAMILY=mmm-app-training
      - ECS_CLUSTER=mmm-app-cluster
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
```

Run with:
```bash
docker-compose up
```

---

## Comparison: GCP vs AWS

### Service Mapping

| Component | GCP | AWS |
|-----------|-----|-----|
| Container Orchestration | Cloud Run (Jobs/Service) | ECS Fargate (Tasks/Service) |
| Object Storage | Google Cloud Storage (GCS) | Amazon S3 |
| Container Registry | Artifact Registry | Elastic Container Registry (ECR) |
| Secrets Management | Secret Manager | Secrets Manager |
| Load Balancing | Cloud Run (built-in) | Application Load Balancer (ALB) |
| Networking | VPC (managed) | VPC (custom) |
| Logging | Cloud Logging | CloudWatch Logs |
| Identity & Access | IAM + Workload Identity | IAM Roles |
| Monitoring | Cloud Monitoring | CloudWatch |

### Cost Comparison

**GCP (Monthly Estimate)**:
- Cloud Run Service (2 vCPU, 4GB): ~$100-200
- Cloud Run Jobs (8 vCPU, 32GB): Pay per execution (~$0.50-2 per job)
- GCS Storage (100GB): ~$2-3
- Artifact Registry: Minimal
- Total: ~$150-250/month + job costs

**AWS (Monthly Estimate)**:
- ECS Fargate Service (2 vCPU, 4GB): ~$100-200
- ECS Fargate Tasks (8 vCPU, 32GB): Pay per execution (~$0.50-2 per task)
- S3 Storage (100GB): ~$2-3
- ALB: ~$20-25
- ECR: Minimal
- VPC (NAT Gateway): ~$30-40
- Total: ~$200-300/month + task costs

### Key Differences

1. **Networking**:
   - GCP: Cloud Run manages networking automatically
   - AWS: Requires VPC, subnets, NAT gateways, and security groups

2. **Load Balancing**:
   - GCP: Built into Cloud Run
   - AWS: Separate ALB resource required

3. **Job Execution**:
   - GCP: Cloud Run Jobs (purpose-built)
   - AWS: ECS Tasks (more manual configuration)

4. **Pricing Model**:
   - GCP: Request-based + compute time
   - AWS: Compute time + data transfer + ALB costs

---

## Troubleshooting

### Common Issues

#### 1. ECS Task Fails to Start

**Symptoms**: Task goes to STOPPED state immediately

**Solutions**:
```bash
# Check task logs
aws ecs describe-tasks \
  --cluster mmm-app-cluster \
  --tasks <task-id> \
  --region ${AWS_REGION}

# Check CloudWatch logs
aws logs tail /ecs/mmm-app-web --region ${AWS_REGION}

# Verify IAM permissions
aws iam get-role --role-name mmm-app-ecs-task-execution-role
```

#### 2. Cannot Access Web Service

**Symptoms**: Timeout or connection refused when accessing ALB URL

**Solutions**:
```bash
# Check ECS service status
aws ecs describe-services \
  --cluster mmm-app-cluster \
  --services mmm-app-web

# Check target group health
aws elbv2 describe-target-health \
  --target-group-arn <target-group-arn>

# Verify security groups
aws ec2 describe-security-groups \
  --group-ids <security-group-id>
```

#### 3. S3 Access Denied

**Symptoms**: 403 errors when accessing S3 buckets

**Solutions**:
```bash
# Verify task role has S3 permissions
aws iam get-role-policy \
  --role-name mmm-app-web-service-task-role \
  --policy-name mmm-app-web-service-s3-access

# Test S3 access
aws s3 ls s3://${S3_BUCKET}/ --region ${AWS_REGION}
```

#### 4. Secrets Manager Access Denied

**Symptoms**: Cannot retrieve secrets at runtime

**Solutions**:
```bash
# Verify secrets exist
aws secretsmanager list-secrets --region ${AWS_REGION}

# Test secret retrieval
aws secretsmanager get-secret-value \
  --secret-id mmm-app-sf-private-key \
  --region ${AWS_REGION}

# Check IAM permissions
aws iam get-role-policy \
  --role-name mmm-app-ecs-task-execution-role \
  --policy-name mmm-app-ecs-task-execution-secrets
```

#### 5. Docker Push Fails

**Symptoms**: Authentication or permission errors when pushing to ECR

**Solutions**:
```bash
# Re-authenticate with ECR
aws ecr get-login-password --region ${AWS_REGION} | \
  docker login --username AWS --password-stdin \
  ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com

# Verify ECR repository exists
aws ecr describe-repositories --region ${AWS_REGION}

# Check ECR permissions
aws ecr get-repository-policy \
  --repository-name mmm-app-web \
  --region ${AWS_REGION}
```

---

## Automated Deployment with GitHub Actions

The repository includes GitHub Actions workflows for automated deployment:

### Manual Deployment

You can manually trigger deployment and choose the target:

1. Go to GitHub Actions
2. Select "CI (AWS)" or "CI (GCP)" workflow
3. Click "Run workflow"
4. Choose deployment target:
   - `both`: Deploy to both GCP and AWS
   - `gcp-only`: Deploy only to GCP
   - `aws-only`: Deploy only to AWS

### Required GitHub Secrets

Configure these in your GitHub repository settings:

- `AWS_ROLE_ARN`: ARN of the IAM role for GitHub Actions
- `SF_PRIVATE_KEY`: Snowflake private key
- `GOOGLE_OAUTH_CLIENT_ID`: Google OAuth client ID
- `GOOGLE_OAUTH_CLIENT_SECRET`: Google OAuth client secret
- `STREAMLIT_COOKIE_SECRET`: Cookie secret for Streamlit

---

## Additional Resources

- [AWS ECS Documentation](https://docs.aws.amazon.com/ecs/)
- [AWS Fargate Documentation](https://docs.aws.amazon.com/fargate/)
- [Terraform AWS Provider](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)
- [AWS CLI Reference](https://docs.aws.amazon.com/cli/)

---

## Support

For issues or questions:
1. Check CloudWatch Logs for detailed error messages
2. Review AWS documentation
3. Open an issue in the GitHub repository
