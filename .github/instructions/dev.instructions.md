# GitHub Copilot Instructions for Development Environment

This file contains development environment-specific instructions for the MMM Trainer application. These instructions supplement the general repository instructions in `.github/copilot-instructions.md`.

## Development Environment Overview

The **dev environment** is used for testing and validating changes before they reach production. It deploys to a separate Cloud Run service with its own configuration and resources.

### Key Differences from Production

- **Service Name**: `mmm-app-dev` (vs. `mmm-app` in production)
- **CI/CD Workflow**: `.github/workflows/ci-dev.yml` (vs. `ci.yml`)
- **Terraform Config**: `infra/terraform/envs/dev.tfvars` (vs. `prod.tfvars`)
- **Branch Triggers**: `feat-*`, `copilot/*`, `dev` branches (vs. `main` only)
- **Queue Name**: `default-dev` (vs. `default`)
- **Scheduler Job**: `robyn-queue-tick-dev` (vs. `robyn-queue-tick`)

## Development Branch Workflow

### Branch Naming Conventions

- **Feature branches**: `feat-*` (e.g., `feat-new-visualization`)
- **Copilot branches**: `copilot/*` (e.g., `copilot/fix-data-validation`)
- **Dev branch**: `dev` (integration branch for testing multiple features)

### CI/CD Behavior

When you push to any dev branch (`feat-*`, `copilot/*`, or `dev`):
1. The `ci-dev.yml` workflow triggers automatically
2. Images are built with the commit SHA as the tag
3. Deployment targets the `mmm-app-dev` Cloud Run service
4. Changes are isolated from production

### Testing Changes in Dev

Before merging to `main`:
1. Push your feature branch to trigger dev deployment
2. Test thoroughly in the dev environment
3. Verify Cloud Run logs for any issues
4. Check that data flows correctly from Snowflake to GCS
5. Ensure R/Robyn training jobs complete successfully

## Development-Specific Configuration

### Terraform Variables (dev.tfvars)

When modifying infrastructure for dev:
- Edit `infra/terraform/envs/dev.tfvars` (NOT `prod.tfvars`)
- Service name must remain `mmm-app-dev`
- Queue and scheduler names must use `-dev` suffix
- Test Terraform changes in dev before applying to prod

### Environment Variables

Dev-specific environment variables in `ci-dev.yml`:
```yaml
SERVICE_NAME: mmm-app-dev
TF_VAR_scheduler_job_name: robyn-queue-tick-dev
TF_VAR_queue_name: default-dev
```

Always verify these match `dev.tfvars` configuration.

### Cloud Resources

Dev environment resources:
- **Cloud Run Service**: `mmm-app-dev` (in `europe-west1`)
- **GCS Bucket**: `mmm-app-output` (shared with prod, use prefixes)
- **Artifact Registry**: `mmm-repo` (shared, tagged with commit SHA)
- **Service Accounts**: Same as prod (proper IAM separation by service)

## Development Best Practices

### Local Development

For rapid iteration without deploying:
1. Use `streamlit run app/streamlit_app.py` locally
2. Configure GCP credentials: `gcloud auth application-default login`
3. Set environment variables for local testing:
   ```bash
   export PROJECT_ID=datawarehouse-422511
   export GCS_BUCKET=mmm-app-output
   export TRAINING_JOB_NAME=mmm-app-training
   ```
4. Test changes locally before pushing to trigger CI/CD

### Docker Testing

Test container builds locally before pushing:
```bash
# Build web service
docker build -f docker/Dockerfile.web -t mmm-web-local .

# Build training image
docker build -f docker/Dockerfile.training -t mmm-training-local .

# Run locally
docker run -p 8080:8080 \
  -e PORT=8080 \
  -e PROJECT_ID=datawarehouse-422511 \
  mmm-web-local
```

### Code Quality for Dev Branches

Even in dev branches, maintain code quality:
- Run `make format` before committing
- Run `make check` to verify linting and type checking
- Run `make test` to ensure tests pass
- Follow the same Python standards (Black, isort, line length 80)

### Debugging in Dev

When debugging issues in the dev environment:
1. Check Cloud Run logs for the `mmm-app-dev` service
2. Use `gcloud logging read` to filter dev service logs:
   ```bash
   gcloud logging read "resource.labels.service_name=mmm-app-dev" --limit 50
   ```
3. Verify GCS bucket contents for training artifacts
4. Check Snowflake connection and query execution
5. Review R script logs in Cloud Logging

### Terraform Development Workflow

When making infrastructure changes:
1. Edit `infra/terraform/envs/dev.tfvars`
2. Test locally with Terraform:
   ```bash
   cd infra/terraform
   terraform init
   terraform plan -var-file=envs/dev.tfvars
   ```
3. Push to a `feat-*` branch to trigger CI/CD
4. Review the Terraform plan step in GitHub Actions
5. Verify deployment in dev before creating PR to main

### Concurrency and Deployment Safety

Dev deployment uses concurrency control:
```yaml
concurrency:
  group: terraform-dev
  cancel-in-progress: false
```

This means:
- Only one dev deployment runs at a time
- New pushes wait for current deployment to finish
- Prevents race conditions in Terraform state

## Common Dev Environment Tasks

### Adding New Features

1. Create a feature branch: `git checkout -b feat-your-feature`
2. Make changes following code standards
3. Test locally with Streamlit
4. Push to trigger dev deployment: `git push origin feat-your-feature`
5. Monitor CI/CD workflow in GitHub Actions
6. Test deployed feature in `mmm-app-dev` Cloud Run service
7. Iterate as needed
8. Create PR to `main` when ready

### Fixing Bugs

1. Create a branch: `git checkout -b feat-fix-bug-name` or use Copilot branch
2. Reproduce bug locally if possible
3. Implement fix with minimal changes
4. Add or update tests to prevent regression
5. Deploy to dev and verify fix
6. Create PR with clear description of bug and fix

### Updating Dependencies

When updating Python packages in dev:
1. Update `requirements.txt` with new versions
2. Check compatibility with existing packages
3. Test locally: `pip install -r requirements.txt`
4. Test Docker build locally
5. Push to dev branch to trigger full CI/CD
6. Monitor for any breaking changes in dev deployment

### Infrastructure Changes

For changes to Cloud Run, IAM, or other infrastructure:
1. Always test in dev first using `dev.tfvars`
2. Review Terraform plan carefully in CI/CD logs
3. Verify changes in GCP Console after deployment
4. Document changes in PR description
5. Get review approval before merging to main

## Security Considerations for Dev

- Dev uses the same service accounts as prod (proper IAM scope)
- Never commit secrets to feature branches
- Use Secret Manager for all sensitive data
- Dev environment is not a "free pass" for security issues
- Follow same security best practices as production

## Performance Testing

Dev environment is suitable for:
- Functional testing of new features
- Integration testing with Snowflake and GCS
- R/Robyn training job validation
- UI/UX testing with Streamlit

Dev environment is NOT suitable for:
- Load testing (use dedicated load testing infrastructure)
- Large-scale data processing benchmarks
- Production data volumes (use test datasets)

## Monitoring and Observability

Monitor dev deployments:
- **Cloud Run Metrics**: Check request latency, error rates
- **Cloud Logging**: Filter by `mmm-app-dev` service name
- **GitHub Actions**: Monitor CI/CD workflow status
- **GCS**: Verify training artifacts are created correctly

## Good Tasks for Dev Environment

Copilot works well for dev tasks like:
- Implementing new Streamlit UI features
- Adding data validation logic
- Improving error handling
- Writing unit and integration tests
- Refactoring code for better structure
- Updating documentation
- Adding logging and debugging aids
- Performance optimizations

## Tasks Requiring Human Review in Dev

Even in dev, be cautious with:
- Terraform infrastructure changes
- IAM role modifications
- Service account permissions
- Database schema changes (Snowflake)
- Major architectural changes
- Workflow modifications (ci-dev.yml)

## Verification Checklist for Dev PRs

Before creating a PR from a dev branch to main:
1. ✅ All tests pass locally (`make test`)
2. ✅ Code is formatted (`make format`)
3. ✅ Linting passes (`make check`)
4. ✅ Dev deployment successful in CI/CD
5. ✅ Feature tested in `mmm-app-dev` Cloud Run service
6. ✅ No secrets committed
7. ✅ Documentation updated if needed
8. ✅ Clean commit history (squash if needed)
9. ✅ PR description explains changes clearly

## Getting Help with Dev Issues

When stuck in dev environment:
- Check `.github/workflows/ci-dev.yml` for workflow details
- Review `infra/terraform/envs/dev.tfvars` for configuration
- Compare with prod configuration to identify differences
- Check Cloud Run logs for runtime errors
- Review Terraform state for infrastructure issues
- Ask for help with infrastructure or deployment problems

## Resources

- General repository instructions: `.github/copilot-instructions.md`
- Development setup guide: `DEVELOPMENT.md`
- Architecture documentation: `ARCHITECTURE.md`
- CI/CD workflow: `.github/workflows/ci-dev.yml`
- Dev Terraform config: `infra/terraform/envs/dev.tfvars`
