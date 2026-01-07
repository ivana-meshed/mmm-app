# GitHub Actions Workflows

This directory contains CI/CD workflows for automated deployment to Google Cloud Platform.

## Quick Start for Customers

1. **Copy the configuration template:**
   ```bash
   cp .github/workflows/config.example.yml .github/workflows/config.yml
   ```

2. **Edit `config.yml`** with your GCP project details:
   - Update `project_id`, `project_number`, `region`
   - Update service account emails
   - Update bucket and service names

3. **Configure GitHub Secrets** (Settings > Secrets and variables > Actions):
   - `SF_PRIVATE_KEY` - Your Snowflake private key
   - `GOOGLE_OAUTH_CLIENT_ID` - Google OAuth client ID
   - `GOOGLE_OAUTH_CLIENT_SECRET` - Google OAuth client secret
   - `STREAMLIT_COOKIE_SECRET` - Streamlit auth cookie secret

4. **Set up Workload Identity Federation:**
   - Follow instructions in `docs/DEPLOYMENT_GUIDE.md`
   - This allows GitHub Actions to deploy to your GCP project without service account keys

5. **Test in development** before deploying to production:
   - Push to a `feat-*` or `dev` branch to trigger dev deployment
   - Verify successful deployment to your dev environment
   - Push to `main` to deploy to production

## Workflows

### `ci.yml` - Production CI/CD
- **Triggers:** Push to `main` branch
- **Environment:** Production
- **Deploys to:** Service name from `config.yml` (production)
- **Terraform config:** `infra/terraform/envs/prod.tfvars`

### `ci-dev.yml` - Development CI/CD
- **Triggers:** Push to `feat-*`, `copilot/*`, or `dev` branches
- **Environment:** Development
- **Deploys to:** Service name from `config.yml` (development)
- **Terraform config:** `infra/terraform/envs/dev.tfvars`

## Configuration Files

- **`config.example.yml`** - Template with documentation (commit this)
- **`config.yml`** - Your actual settings (**DO NOT COMMIT** - in .gitignore)
- **`ci.yml`** - Production workflow (references config.yml)
- **`ci-dev.yml`** - Development workflow (references config.yml)

## What These Workflows Do

1. **Build Docker Images:**
   - Web service image (`Dockerfile.web`)
   - Training job base image (`Dockerfile.training-base`)
   - Training job image (`Dockerfile.training`)

2. **Run Tests:**
   - Python unit tests
   - Code formatting checks

3. **Deploy to GCP:**
   - Push images to Artifact Registry
   - Deploy Cloud Run web service
   - Register Cloud Run training jobs
   - Update Terraform-managed infrastructure

4. **Verify Deployment:**
   - Print service URLs and status
   - Run health checks

## Security Notes

- **Never commit `config.yml`** - it's in `.gitignore` for a reason
- **Use GitHub Secrets** for all sensitive data
- **Workload Identity Federation** eliminates need for service account keys
- **Review permissions** granted to service accounts

## Customization

You can customize these workflows for your needs:

- Modify build steps in the workflows
- Add additional testing or validation steps
- Change deployment regions or resources
- Add notifications (Slack, email, etc.)

## Troubleshooting

**Workflow fails on first run:**
- Ensure Workload Identity Federation is configured
- Verify GitHub Secrets are set correctly
- Check that `config.yml` values match your GCP project

**Permission errors:**
- Verify service account IAM roles in GCP Console
- Check that Workload Identity bindings are correct

**Image build failures:**
- Check Docker buildx is available
- Verify Artifact Registry repository exists
- Ensure deployer service account can push to registry

## Documentation

For detailed setup and deployment instructions, see:
- `docs/DEPLOYMENT_GUIDE.md` - Complete deployment walkthrough
- `docs/REQUIREMENTS.md` - Prerequisites and dependencies
- `DEVELOPMENT.md` - Local development setup

## Support

For licensing and support questions, see your LICENSE_AUTHORIZATION.txt file for contact information.
