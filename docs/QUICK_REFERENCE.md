# Deployment Quick Reference

## First-Time Setup

**For GitHub Actions CI/CD**:
- **GCP**: [GitHub Actions GCP Setup Guide](GITHUB_ACTIONS_GCP_SETUP.md) - Configure Workload Identity Federation
- **AWS**: [GitHub Actions AWS Setup Guide](GITHUB_ACTIONS_AWS_SETUP.md) - Configure OIDC authentication

## Choose Your Deployment

### 🚀 Quick Deploy Commands

| Target | Command | Use Case |
|--------|---------|----------|
| **Both Clouds** | Push to `main` (auto) | Multi-cloud redundancy |
| **GCP Only** | Manual workflow → `gcp-only` | GCP-focused deployment |
| **AWS Only** | Manual workflow → `aws-only` | AWS-focused deployment |

---

## Environment Variables Cheat Sheet

### For GCP Deployment
```bash
export CLOUD_PROVIDER=gcp
export PROJECT_ID=your-gcp-project
export REGION=europe-west1
export GCS_BUCKET=mmm-app-output
export TRAINING_JOB_NAME=mmm-app-training
```

### For AWS Deployment
```bash
export CLOUD_PROVIDER=aws
export AWS_REGION=us-east-1
export S3_BUCKET=mmm-app-output-aws
export TRAINING_TASK_FAMILY=mmm-app-training
export ECS_CLUSTER=mmm-app-cluster
```

---

## Local Testing

### Test with GCP
```bash
export CLOUD_PROVIDER=gcp
gcloud auth application-default login
streamlit run app/streamlit_app.py
```

### Test with AWS
```bash
export CLOUD_PROVIDER=aws
aws configure
streamlit run app/streamlit_app.py
```

---

## Manual Deployment

### Deploy to GCP
```bash
cd infra/terraform
terraform apply -var-file="envs/prod.tfvars"
```

### Deploy to AWS
```bash
cd infra/terraform-aws
terraform apply -var-file="envs/prod.tfvars"
```

---

## Service URLs

After deployment, get your service URLs:

### GCP
```bash
cd infra/terraform
terraform output web_service_url
```

### AWS
```bash
cd infra/terraform-aws
terraform output web_service_url
```

---

## Key Files by Cloud

| Purpose | GCP | AWS |
|---------|-----|-----|
| Infrastructure | `infra/terraform/` | `infra/terraform-aws/` |
| CI/CD Prod | `.github/workflows/ci.yml` | `.github/workflows/ci-aws.yml` |
| CI/CD Dev | `.github/workflows/ci-dev.yml` | `.github/workflows/ci-aws-dev.yml` |
| Prod Config | `infra/terraform/envs/prod.tfvars` | `infra/terraform-aws/envs/prod.tfvars` |
| Dev Config | `infra/terraform/envs/dev.tfvars` | `infra/terraform-aws/envs/dev.tfvars` |

---

## Troubleshooting Quick Fixes

### Check Logs
```bash
# GCP
gcloud logging read "resource.type=cloud_run_revision" --limit 50

# AWS
aws logs tail /ecs/mmm-app-web --follow
```

### Check Service Status
```bash
# GCP
gcloud run services describe mmm-app-web --region europe-west1

# AWS
aws ecs describe-services --cluster mmm-app-cluster --services mmm-app-web
```

### Verify Environment
```bash
echo $CLOUD_PROVIDER  # Should be "gcp" or "aws"
```

---

## Cost Estimates (Monthly)

| Service | GCP | AWS |
|---------|-----|-----|
| Web Service (2 CPU, 4GB) | ~$100-200 | ~$100-200 |
| Training (per job) | ~$0.50-2 | ~$0.50-2 |
| Storage (100GB) | ~$2-3 | ~$2-3 |
| Networking | Included | ~$50-65 (ALB + NAT) |
| **Total** | **~$150-250** | **~$200-300** |

---

## Need More Help?

- 📖 [Full AWS Deployment Guide](AWS_DEPLOYMENT.md)
- 📖 [Multi-Cloud Deployment Guide](DEPLOYMENT_SELECTOR.md)
- 📖 [Development Guide](../DEVELOPMENT.md)
- 📖 [Main README](../README.md)
