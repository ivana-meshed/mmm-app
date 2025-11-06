# Multi-Cloud Deployment Guide

This document explains how to deploy and manage the MMM application across both Google Cloud Platform (GCP) and Amazon Web Services (AWS).

## Quick Overview

The MMM application supports deployment on two cloud platforms:

| Feature | GCP | AWS |
|---------|-----|-----|
| Web Service | Cloud Run Service | ECS Fargate Service |
| Training Jobs | Cloud Run Jobs | ECS Fargate Tasks |
| Storage | Google Cloud Storage | Amazon S3 |
| Container Registry | Artifact Registry | ECR |
| Secrets | Secret Manager | Secrets Manager |
| Networking | Managed by Cloud Run | VPC + ALB |

## Deployment Options

### Option 1: Deploy to Both Clouds

Deploy to both GCP and AWS simultaneously for redundancy and flexibility.

**When to use:**
- Multi-region availability requirements
- Vendor diversification strategy
- Cost optimization through cloud arbitrage
- Testing and validation across platforms

**How to deploy:**
```bash
# GitHub Actions (manual trigger)
# 1. Go to Actions tab
# 2. Select workflow (CI or CI Dev)
# 3. Click "Run workflow"
# 4. Choose "both" as deployment target

# Or push to main/dev branch (deploys to both by default)
git push origin main
```

### Option 2: Deploy to GCP Only

Use only Google Cloud Platform infrastructure.

**When to use:**
- Already using GCP for other services
- Prefer serverless simplicity (Cloud Run)
- Lower networking complexity
- Cloud Run's automatic scaling benefits

**How to deploy:**
```bash
# GitHub Actions (manual trigger)
# 1. Go to Actions tab
# 2. Select "CI (GCP)" or "CI (GCP Dev)" workflow
# 3. Click "Run workflow"
# 4. Choose "gcp-only" as deployment target

# Or deploy manually with Terraform
cd infra/terraform
terraform apply -var-file="envs/prod.tfvars"
```

### Option 3: Deploy to AWS Only

Use only Amazon Web Services infrastructure.

**When to use:**
- Already using AWS for other services
- Need fine-grained VPC control
- Prefer ECS/Fargate for container orchestration
- AWS-specific compliance requirements

**How to deploy:**
```bash
# GitHub Actions (manual trigger)
# 1. Go to Actions tab
# 2. Select "CI (AWS)" or "CI (AWS Dev)" workflow
# 3. Click "Run workflow"
# 4. Choose "aws-only" as deployment target

# Or deploy manually with Terraform
cd infra/terraform-aws
terraform apply -var-file="envs/prod.tfvars"
```

## Environment Configuration

### Environment Variables

Set `CLOUD_PROVIDER` environment variable to control which cloud the application uses:

```bash
# For GCP (default)
export CLOUD_PROVIDER=gcp

# For AWS
export CLOUD_PROVIDER=aws
```

### Local Development

#### Testing with GCP Backend

```bash
# Set GCP environment
export CLOUD_PROVIDER=gcp
export PROJECT_ID=your-gcp-project
export REGION=europe-west1
export GCS_BUCKET=mmm-app-output
export TRAINING_JOB_NAME=mmm-app-training

# Authenticate with GCP
gcloud auth application-default login

# Run application
streamlit run app/streamlit_app.py
```

#### Testing with AWS Backend

```bash
# Set AWS environment
export CLOUD_PROVIDER=aws
export AWS_REGION=us-east-1
export S3_BUCKET=mmm-app-output-aws
export TRAINING_TASK_FAMILY=mmm-app-training
export ECS_CLUSTER=mmm-app-cluster

# Configure AWS credentials
aws configure

# Run application
streamlit run app/streamlit_app.py
```

## CI/CD Configuration

### GitHub Actions Workflows

The repository includes four CI/CD workflows:

1. **ci.yml** (GCP Production)
   - Triggers: Push to `main` branch
   - Deploys to GCP production environment
   - Can be manually triggered with deployment target selection

2. **ci-dev.yml** (GCP Development)
   - Triggers: Push to `feat-*`, `copilot/*`, `dev` branches
   - Deploys to GCP development environment
   - Can be manually triggered with deployment target selection

3. **ci-aws.yml** (AWS Production)
   - Triggers: Push to `main` branch
   - Deploys to AWS production environment
   - Can be manually triggered with deployment target selection

4. **ci-aws-dev.yml** (AWS Development)
   - Triggers: Push to `feat-*`, `copilot/*`, `dev` branches
   - Deploys to AWS development environment
   - Can be manually triggered with deployment target selection

### Manual Workflow Trigger

To manually trigger a deployment:

1. Navigate to the **Actions** tab in GitHub
2. Select the desired workflow
3. Click **Run workflow**
4. Choose deployment target:
   - `both`: Deploy to both GCP and AWS
   - `gcp-only`: Deploy only to GCP
   - `aws-only`: Deploy only to AWS
5. Click **Run workflow**

### Required GitHub Secrets

Configure these secrets in your repository settings:

**For GCP:**
- `WIF_POOL`: Workload Identity Federation pool
- `WIF_PROVIDER`: Workload Identity Federation provider

**For AWS:**
- `AWS_ROLE_ARN`: IAM role ARN for GitHub Actions

**Common:**
- `SF_PRIVATE_KEY`: Snowflake private key
- `GOOGLE_OAUTH_CLIENT_ID`: Google OAuth client ID
- `GOOGLE_OAUTH_CLIENT_SECRET`: Google OAuth client secret
- `STREAMLIT_COOKIE_SECRET`: Cookie secret for sessions

## Application Architecture

### Cloud Provider Abstraction

The application uses abstraction layers to support both cloud providers:

#### Storage Abstraction (`app/utils/cloud_storage.py`)

```python
from app.utils.cloud_storage import (
    upload_to_cloud,
    download_from_cloud,
    read_json_from_cloud,
    write_json_to_cloud,
)

# Works with both GCS and S3 based on CLOUD_PROVIDER env var
upload_to_cloud("bucket-name", "/local/file", "remote/path")
```

#### Secrets Abstraction (`app/utils/cloud_secrets.py`)

```python
from app.utils.cloud_secrets import get_secret, update_secret

# Works with both GCP Secret Manager and AWS Secrets Manager
secret_value = get_secret("my-secret-name")
```

#### Orchestration Abstraction (`app/utils/container_orchestration.py`)

```python
from app.utils.container_orchestration import (
    run_training_job,
    get_job_execution,
)

# Works with both Cloud Run Jobs and ECS Tasks
execution_id = run_training_job()
status = get_job_execution(execution_id)
```

## Cost Comparison

### Monthly Estimates

**GCP (Baseline: 2 CPUs, 4GB web + 100GB storage)**
- Cloud Run Service: ~$100-200
- Cloud Run Jobs: ~$0.50-2 per execution
- GCS Storage: ~$2-3
- Artifact Registry: Minimal
- **Total**: ~$150-250/month + job costs

**AWS (Baseline: 2 CPUs, 4GB web + 100GB storage)**
- ECS Fargate Service: ~$100-200
- ECS Fargate Tasks: ~$0.50-2 per execution
- S3 Storage: ~$2-3
- ALB: ~$20-25
- NAT Gateway: ~$30-40
- ECR: Minimal
- **Total**: ~$200-300/month + task costs

### Cost Optimization Tips

1. **Use Spot Instances (AWS)**
   - ECS Fargate Spot can reduce costs by 50-70%
   - Suitable for non-critical training jobs

2. **Optimize Instance Sizes**
   - Start with smaller instances
   - Scale based on actual usage

3. **Use Storage Classes**
   - GCS: Standard → Nearline → Coldline
   - S3: Standard → Intelligent-Tiering → Glacier

4. **Monitor and Alert**
   - Set up billing alerts
   - Review cost anomalies regularly

## Monitoring and Logging

### GCP Monitoring

```bash
# View logs
gcloud logging read "resource.type=cloud_run_revision" --limit 50

# View metrics
gcloud monitoring dashboards list
```

### AWS Monitoring

```bash
# View ECS logs
aws logs tail /ecs/mmm-app-web --follow

# View ECS metrics
aws cloudwatch get-metric-statistics \
  --namespace AWS/ECS \
  --metric-name CPUUtilization \
  --dimensions Name=ServiceName,Value=mmm-app-web \
  --statistics Average \
  --start-time 2024-01-01T00:00:00Z \
  --end-time 2024-01-02T00:00:00Z \
  --period 3600
```

## Migration Between Clouds

### GCP to AWS Migration

1. **Export data from GCS to S3**
   ```bash
   # Install gsutil and aws CLI
   gsutil -m rsync -r gs://mmm-app-output s3://mmm-app-output-aws
   ```

2. **Update application configuration**
   ```bash
   export CLOUD_PROVIDER=aws
   # Update other AWS-specific variables
   ```

3. **Deploy to AWS**
   ```bash
   cd infra/terraform-aws
   terraform apply -var-file="envs/prod.tfvars"
   ```

### AWS to GCP Migration

1. **Export data from S3 to GCS**
   ```bash
   aws s3 sync s3://mmm-app-output-aws gs://mmm-app-output
   ```

2. **Update application configuration**
   ```bash
   export CLOUD_PROVIDER=gcp
   # Update other GCP-specific variables
   ```

3. **Deploy to GCP**
   ```bash
   cd infra/terraform
   terraform apply -var-file="envs/prod.tfvars"
   ```

## Troubleshooting

### Common Issues

#### Wrong Cloud Provider

**Symptom**: Application fails to connect to cloud services

**Solution**:
```bash
# Check CLOUD_PROVIDER environment variable
echo $CLOUD_PROVIDER

# Verify it matches your deployment
# Should be "gcp" for GCP or "aws" for AWS
```

#### Mixed Configuration

**Symptom**: Application tries to use GCS bucket on AWS (or vice versa)

**Solution**:
```bash
# Ensure all environment variables are consistent
# For AWS:
export CLOUD_PROVIDER=aws
export S3_BUCKET=mmm-app-output-aws
export TRAINING_TASK_FAMILY=mmm-app-training

# For GCP:
export CLOUD_PROVIDER=gcp
export GCS_BUCKET=mmm-app-output
export TRAINING_JOB_NAME=mmm-app-training
```

## Additional Resources

- [AWS Deployment Guide](AWS_DEPLOYMENT.md)
- [GCP Deployment (README)](../README.md)
- [Development Guide](../DEVELOPMENT.md)
- [Architecture Documentation](../ARCHITECTURE.md)

## Support

For deployment issues:
1. Check workflow logs in GitHub Actions
2. Review cloud provider logs (Cloud Logging / CloudWatch)
3. Verify environment variables are set correctly
4. Open an issue in the GitHub repository
