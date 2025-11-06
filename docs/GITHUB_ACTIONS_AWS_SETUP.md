# GitHub Actions AWS Setup Guide

This guide walks through setting up AWS authentication for GitHub Actions using OpenID Connect (OIDC), which is the recommended secure method that doesn't require storing long-lived AWS credentials.

## Prerequisites

- AWS account with administrator access
- AWS CLI configured locally
- GitHub repository admin access

## Step 1: Create OIDC Identity Provider in AWS

This allows GitHub Actions to authenticate with AWS without storing credentials.

```bash
# Get your AWS account ID
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "AWS Account ID: $AWS_ACCOUNT_ID"

# Create OIDC provider for GitHub Actions
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

**Note**: If you get an error that the provider already exists, you can skip this step.

## Step 2: Create IAM Role for GitHub Actions

Create a trust policy that allows GitHub Actions from your repository to assume the role.

### Create Trust Policy File

Create a file named `github-actions-trust-policy.json`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::YOUR_ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:ivana-meshed/mmm-app:*"
        }
      }
    }
  ]
}
```

**Replace `YOUR_ACCOUNT_ID`** with your actual AWS account ID:

```bash
# Replace YOUR_ACCOUNT_ID in the file
sed -i "s/YOUR_ACCOUNT_ID/$AWS_ACCOUNT_ID/g" github-actions-trust-policy.json
```

### Create the IAM Role

```bash
# Create the role
aws iam create-role \
  --role-name GitHubActionsDeployerRole \
  --assume-role-policy-document file://github-actions-trust-policy.json \
  --description "Role for GitHub Actions to deploy MMM app to AWS"

# Get the role ARN (save this for later)
ROLE_ARN=$(aws iam get-role --role-name GitHubActionsDeployerRole --query 'Role.Arn' --output text)
echo "Role ARN: $ROLE_ARN"
```

## Step 3: Attach Permissions to the Role

The role needs permissions to:
- Push/pull images to/from ECR
- Deploy ECS services and tasks
- Manage S3 buckets
- Create/update Secrets Manager secrets
- Manage VPC resources (for initial setup)
- Execute Terraform operations

### Option A: Use Managed Policies (Easier, Less Secure)

For a quick start, you can attach AWS managed policies:

```bash
# ECR permissions
aws iam attach-role-policy \
  --role-name GitHubActionsDeployerRole \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser

# ECS permissions
aws iam attach-role-policy \
  --role-name GitHubActionsDeployerRole \
  --policy-arn arn:aws:iam::aws:policy/AmazonECS_FullAccess

# S3 permissions
aws iam attach-role-policy \
  --role-name GitHubActionsDeployerRole \
  --policy-arn arn:aws:iam::aws:policy/AmazonS3FullAccess

# Secrets Manager permissions
aws iam attach-role-policy \
  --role-name GitHubActionsDeployerRole \
  --policy-arn arn:aws:iam::aws:policy/SecretsManagerReadWrite

# IAM permissions (for Terraform to create roles)
aws iam attach-role-policy \
  --role-name GitHubActionsDeployerRole \
  --policy-arn arn:aws:iam::aws:policy/IAMFullAccess

# VPC permissions (for Terraform to create networking)
aws iam attach-role-policy \
  --role-name GitHubActionsDeployerRole \
  --policy-arn arn:aws:iam::aws:policy/AmazonVPCFullAccess

# CloudWatch permissions
aws iam attach-role-policy \
  --role-name GitHubActionsDeployerRole \
  --policy-arn arn:aws:iam::aws:policy/CloudWatchLogsFullAccess

# Load Balancer permissions
aws iam attach-role-policy \
  --role-name GitHubActionsDeployerRole \
  --policy-arn arn:aws:iam::aws:policy/ElasticLoadBalancingFullAccess
```

### Option B: Create Custom Policy (More Secure, Recommended for Production)

Create a file named `github-actions-permissions-policy.json`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ecr:*",
        "ecs:*",
        "s3:*",
        "secretsmanager:*",
        "iam:*",
        "ec2:*",
        "elasticloadbalancing:*",
        "logs:*",
        "application-autoscaling:*",
        "cloudwatch:*"
      ],
      "Resource": "*"
    }
  ]
}
```

Then create and attach the policy:

```bash
# Create the policy
aws iam create-policy \
  --policy-name GitHubActionsDeployerPolicy \
  --policy-document file://github-actions-permissions-policy.json

# Attach it to the role
aws iam attach-role-policy \
  --role-name GitHubActionsDeployerRole \
  --policy-arn arn:aws:iam::$AWS_ACCOUNT_ID:policy/GitHubActionsDeployerPolicy
```

## Step 4: Create S3 Bucket for Terraform State

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

## Step 5: Create ECR Repositories

GitHub Actions will push images to these repositories:

```bash
# Create repositories
aws ecr create-repository \
  --repository-name mmm-app-web \
  --region us-east-1

aws ecr create-repository \
  --repository-name mmm-app-training \
  --region us-east-1

aws ecr create-repository \
  --repository-name mmm-app-training-base \
  --region us-east-1

# Enable image scanning
aws ecr put-image-scanning-configuration \
  --repository-name mmm-app-web \
  --image-scanning-configuration scanOnPush=true \
  --region us-east-1

aws ecr put-image-scanning-configuration \
  --repository-name mmm-app-training \
  --image-scanning-configuration scanOnPush=true \
  --region us-east-1

aws ecr put-image-scanning-configuration \
  --repository-name mmm-app-training-base \
  --image-scanning-configuration scanOnPush=true \
  --region us-east-1
```

## Step 6: Add GitHub Secrets

Add the role ARN to your GitHub repository secrets:

1. Go to your GitHub repository
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Add the following secret:
   - **Name**: `AWS_ROLE_ARN`
   - **Value**: The role ARN from Step 2 (format: `arn:aws:iam::123456789012:role/GitHubActionsDeployerRole`)

You should already have these secrets (they're shared with GCP workflows):
- `SF_PRIVATE_KEY`
- `GOOGLE_OAUTH_CLIENT_ID`
- `GOOGLE_OAUTH_CLIENT_SECRET`
- `STREAMLIT_COOKIE_SECRET`

## Step 7: Verify the Setup

Test the OIDC authentication locally (optional):

```bash
# This won't work locally, but you can verify the role exists
aws iam get-role --role-name GitHubActionsDeployerRole

# Verify the OIDC provider exists
aws iam list-open-id-connect-providers
```

## Step 8: Test GitHub Actions Workflow

Now you can trigger the workflow:

### Option A: Push to a Feature Branch
```bash
git checkout -b test-aws-deployment
git push origin test-aws-deployment
```

### Option B: Manual Trigger
1. Go to **Actions** tab in GitHub
2. Select **CI (AWS Dev)** workflow
3. Click **Run workflow**
4. Select branch and deployment target
5. Click **Run workflow**

## Troubleshooting

### Error: "Could not load credentials"

**Cause**: The `AWS_ROLE_ARN` secret is not set or is incorrect.

**Solution**: Verify the secret in GitHub Settings → Secrets and ensure it matches the role ARN from Step 2.

### Error: "Not authorized to perform sts:AssumeRoleWithWebIdentity"

**Cause**: The trust policy doesn't allow your repository to assume the role.

**Solution**: Check the trust policy includes the correct repository name: `repo:ivana-meshed/mmm-app:*`

```bash
# View current trust policy
aws iam get-role --role-name GitHubActionsDeployerRole --query 'Role.AssumeRolePolicyDocument'
```

### Error: "Access Denied" during deployment

**Cause**: The role doesn't have sufficient permissions.

**Solution**: Verify all necessary policies are attached:

```bash
# List attached policies
aws iam list-attached-role-policies --role-name GitHubActionsDeployerRole
```

### Error: "Repository does not exist" (ECR)

**Cause**: ECR repositories haven't been created.

**Solution**: Run the commands in Step 5 to create ECR repositories.

## Summary

After completing these steps, your GitHub Actions workflows will be able to:

✅ Authenticate with AWS using OIDC (no long-lived credentials)
✅ Push Docker images to ECR
✅ Deploy infrastructure with Terraform
✅ Create and manage AWS resources

## Quick Reference

```bash
# Get role ARN (needed for GitHub secret)
aws iam get-role --role-name GitHubActionsDeployerRole --query 'Role.Arn' --output text

# Verify ECR repositories
aws ecr describe-repositories --region us-east-1

# Check S3 buckets
aws s3 ls

# View GitHub secrets (requires GitHub CLI)
gh secret list
```

## Next Steps

After setup is complete:
1. Trigger the workflow manually or push to a feature branch
2. Monitor the workflow in the Actions tab
3. If successful, Terraform will create all AWS infrastructure
4. Get the web service URL from Terraform outputs

For the full deployment guide, see [AWS_DEPLOYMENT.md](AWS_DEPLOYMENT.md).
