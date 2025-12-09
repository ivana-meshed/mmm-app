# GitHub Copilot Instructions for MMM Trainer

This repository contains a Streamlit web application that orchestrates an R/Robyn training pipeline for Marketing Mix Modeling (MMM), deployed on Google Cloud Run.

## Project Overview

- **Frontend**: Streamlit (Python) web application with multi-page interface
- **Backend**: R/Robyn for MMM training executed via Cloud Run Jobs
- **Data Source**: Snowflake
- **Cloud Platform**: Google Cloud (Cloud Run, Cloud Run Jobs, Cloud Storage, Artifact Registry)
- **Infrastructure**: Terraform for IaC with separate prod and dev environments
- **Container**: Multi-stage Docker builds with separate web and training images
- **Branch Strategy**: 
  - `main` - Production environment (deployed via ci.yml)
  - `dev` - Development environment (deployed via ci-dev.yml)
  - `feat-*`, `copilot/*` - Feature branches (deployed to dev via ci-dev.yml)

## Repository Structure

```
app/                          # Streamlit application and Python modules
  streamlit_app.py            # Main Streamlit UI (production)
  streamlit_app_dev.py        # Development variant with additional features
  config/                     # Centralized configuration
    settings.py               # Environment variables, GCP, Snowflake settings
    __init__.py
  utils/                      # Shared utility modules
    gcs_utils.py              # Google Cloud Storage operations
    snowflake_connector.py    # Snowflake connection and query utilities
    __init__.py
  pages/                      # Streamlit multi-page components
    Connect_Data.py           # Snowflake connection setup
    Map_Data.py               # Data column mapping and metadata
    Review_Data.py            # Data validation and preview
    Run_Experiment.py         # Single/batch experiment configuration
    View_Results.py           # Results visualization
    View_Best_Results.py      # Best model selection and comparison
  app_shared.py               # Shared helper functions (job management, queue)
  app_split_helpers.py        # Split testing helpers
  data_processor.py           # Data optimization and Parquet conversion
  snowflake_utils.py          # Backward compatibility wrapper
  gcp_secrets.py              # GCP Secret Manager integration
  api_endpoint.py             # API endpoint handlers
  warm_container.py           # Container warm-up logic
  to-do/                      # Deprecated/WIP pages

r/                            # R scripts for Robyn MMM
  run_all.R                   # Main R entrypoint for training
  helpers.R                   # R utility functions

docker/                       # Container configurations
  Dockerfile.web              # Web service container
  Dockerfile.training         # Training job container (references base)
  Dockerfile.training-base    # Base image with R dependencies and packages
  Dockerfile                  # Legacy multi-purpose Dockerfile
  web_entrypoint.sh           # Web service entrypoint script
  training_entrypoint.sh      # Training job entrypoint script
  entrypoint.sh               # Legacy entrypoint

infra/terraform/              # Infrastructure as Code
  main.tf                     # Main Terraform configuration
  variables.tf                # Variable definitions
  envs/                       # Environment-specific configs
    prod.tfvars               # Production values
    dev.tfvars                # Development values

tests/                        # Unit and integration tests
scripts/                      # Utility scripts
docs/                         # Documentation and diagrams
data/                         # Sample data for development
.github/workflows/            # CI/CD workflows
  ci.yml                      # Production CI/CD (main branch)
  ci-dev.yml                  # Development CI/CD (dev, feat-*, copilot/* branches)

# Documentation files
README.md                     # Project overview
ARCHITECTURE.md               # System architecture documentation
DEVELOPMENT.md                # Local development guide
FEATURE_SINGLE_JOB.md         # Single job execution feature documentation
IMPLEMENTATION_SUMMARY.md     # Implementation details
```

## Development Guidelines

### Python Code Standards

- **Formatter**: Use Black with line length 80 (`black --line-length 80`)
- **Import Sorting**: Use isort with Black profile (`isort --profile black --line-length 80`)
- **Linting**: Use pylint and flake8 (both configured for max line length 80)
- **Type Checking**: Use mypy for type hints
- **Testing**: Use pytest with coverage reporting

### Code Quality Commands

```bash
# Format code
make format

# Run linting
make lint

# Type checking
make typecheck

# Run all checks
make check

# Format and check
make fix

# Run tests
make test
```

### Python Dependencies

Key dependencies include:
- `streamlit[auth]>=1.43` - Web UI framework with authentication support
- `pandas` - Data manipulation
- `snowflake-connector-python` - Snowflake database connector
- `google-cloud-secret-manager` - GCP secrets management
- `google-cloud-storage` - GCS file operations
- `google-cloud-run` - Cloud Run Jobs API for training execution
- `protobuf<5` - Protocol buffers compatibility

When adding new dependencies:
1. Add to `requirements.txt` (web dependencies)
2. Add to `docker/requirements-training.txt` if needed for training jobs
3. Ensure compatibility with existing packages
4. Test in Docker build context
5. Consider impact on container image size

### R Code Standards

- R code lives in the `r/` directory
- Main entrypoint is `r/run_all.R`
- Uses Robyn package for MMM
- Requires `reticulate` for Python integration (nevergrad)
- Environment variable `RETICULATE_PYTHON=/usr/bin/python3` must be set

### Docker & Container Best Practices

- **Multi-stage builds**: Separate web and training containers
  - `Dockerfile.web` - Lightweight web service (Streamlit only)
  - `Dockerfile.training-base` - Base image with R, system deps, and R packages
  - `Dockerfile.training` - Training job (extends base, adds Python deps)
- **Architecture**: Primarily linux/amd64 for Cloud Run compatibility
- **Entrypoints**: 
  - `web_entrypoint.sh` - Web service startup
  - `training_entrypoint.sh` - Training job execution
- Python dependencies must be compatible with R's reticulate
- Entry point must bind to `0.0.0.0` and use `$PORT` environment variable
- Service account authentication handled via Application Default Credentials
- Training jobs run as Cloud Run Jobs, not Cloud Run services

### Google Cloud Integration

**Authentication**:
- Cloud Run uses service account identity (Application Default Credentials)
- Local development requires `gcloud auth application-default login`
- Workload Identity Federation for GitHub Actions

**Service Accounts**:
- `mmm-web-service-sa` - Web service runtime SA
- `mmm-training-job-sa` - Training job runtime SA
- `github-deployer` - GitHub Actions deployment SA

**Required IAM Roles**:
- `roles/artifactregistry.reader` - Pull container images
- `roles/storage.objectAdmin` - Upload/download artifacts from GCS
- `roles/secretmanager.secretAccessor` - Access secrets

**Cloud Services**:
- Cloud Run - Web application hosting (always-on service)
- Cloud Run Jobs - Training execution (on-demand batch jobs)
- Cloud Storage - Model artifacts storage, training data, job configs
- Artifact Registry - Container image registry
- Secret Manager - Credentials and secrets

### Infrastructure & Terraform

- Terraform configurations in `infra/terraform/`
- **Environment-specific configs**:
  - `envs/prod.tfvars` - Production environment (deployed from `main` branch)
  - `envs/dev.tfvars` - Development environment (deployed from `dev`, `feat-*`, `copilot/*` branches)
- Always use `terraform fmt` before committing
- Test infrastructure changes in dev environment first
- Variables are separated in `variables.tf` and environment-specific `.tfvars` files
- State is stored remotely in GCS backend
- Concurrency control prevents simultaneous deployments to same environment

### CI/CD Workflows

**Workflows**:
- `ci.yml` - Production CI/CD
  - Triggers on push to `main` branch
  - Deploys to production environment using `envs/prod.tfvars`
  - Builds and pushes production container images
  - Updates Cloud Run service and registers Cloud Run Jobs
- `ci-dev.yml` - Development CI/CD
  - Triggers on push to `dev`, `feat-*`, `copilot/*` branches
  - Deploys to development environment using `envs/dev.tfvars`
  - Allows testing changes before merging to main
  - Uses same infrastructure pattern as production

**Deployment Process**:
1. Authenticate via OIDC (Workload Identity Federation)
2. Build web and training Docker images
3. Push to Artifact Registry with environment-specific tags
4. Run Terraform apply with appropriate .tfvars file
5. Deploy Cloud Run service (web)
6. Register/update Cloud Run Jobs (training)
7. Verify deployment health

**Concurrency Control**:
- Dev deployments use `terraform-dev` concurrency group
- Prod deployments use `terraform-prod` concurrency group (implied)
- Prevents race conditions during simultaneous deployments

### Testing Strategy

- Write unit tests for Python modules in `tests/` directory
- Use pytest for test execution
- Aim for meaningful coverage of business logic
- Mock external dependencies (Snowflake, GCS, etc.)
- Test data processing logic independently from UI

### Common Patterns

**Streamlit App Structure**:
```python
import streamlit as st

st.set_page_config(page_title="...", layout="wide")

# Use session state for maintaining state across reruns
if 'key' not in st.session_state:
    st.session_state.key = value

# Modular functions for each UI component
def render_component():
    # Component logic
    pass
```

**GCS Interactions (using utils)**:
```python
from app.utils.gcs_utils import (
    upload_to_gcs, 
    download_from_gcs, 
    read_json_from_gcs,
    write_json_to_gcs
)

# Upload file
upload_to_gcs(bucket_name, local_path, gcs_path)

# Read JSON config
config = read_json_from_gcs(bucket_name, config_path)
```

**Snowflake Connections (using utils)**:
```python
from app.utils.snowflake_connector import get_snowflake_connection, execute_query

# Get connection
conn = get_snowflake_connection(
    account=account,
    user=user,
    password=password,
    warehouse=warehouse,
    database=database,
    schema=schema
)

# Execute query and get DataFrame
df = execute_query(conn, "SELECT * FROM table")
```

**Cloud Run Jobs Execution**:
```python
from google.cloud import run_v2

# Create job execution request
client = run_v2.JobsClient()
request = run_v2.RunJobRequest(name=job_name)

# Execute job
operation = client.run_job(request=request)
```

### Error Handling

- Always catch and handle specific exceptions
- Provide user-friendly error messages in Streamlit UI
- Log errors for debugging (Cloud Logging in production)
- Validate inputs before processing
- Handle missing or malformed data gracefully

### Security Considerations

- Never commit secrets or credentials to the repository
- Use Google Cloud Secret Manager for sensitive data
- Service accounts should follow principle of least privilege
- Validate and sanitize user inputs (SQL injection prevention)
- Use parameterized queries for Snowflake

### Documentation

- **README.md** - Project overview and quick start
- **ARCHITECTURE.md** - Detailed system architecture and data flows
- **DEVELOPMENT.md** - Local development setup guide
- **FEATURE_SINGLE_JOB.md** - Single job execution feature documentation
- Update relevant documentation when changing architecture or setup
- Document complex logic with inline comments
- Update diagrams in `docs/` when architecture changes
- Use docstrings for public functions and classes

## Good Tasks for Copilot

Copilot coding agent works well for:
- Bug fixes in Python or R code
- Adding new Streamlit UI components
- Implementing data validation logic
- Writing unit tests
- Refactoring for code quality improvements
- Documentation updates
- Adding error handling
- Performance optimizations

## Tasks Requiring Human Oversight

Be cautious with:
- Changes to infrastructure (Terraform)
- Security-sensitive code (authentication, authorization)
- Database schema changes
- Major architectural changes
- GCP IAM and permissions
- Production deployment configurations

## Verification Steps

Before submitting a PR:
1. Run `make format` to format code
2. Run `make check` to lint and type-check
3. Run `make test` to execute tests
4. Test locally with Docker if possible:
   - Web: `docker build -t mmm-web -f docker/Dockerfile.web .`
   - Training: `docker build -t mmm-training-base -f docker/Dockerfile.training-base .`
5. Verify changes don't break existing functionality
6. Update documentation if needed (README.md, ARCHITECTURE.md, etc.)
7. Ensure no secrets are committed
8. Test in dev environment before merging to main

## Branch Workflow

- **Development**: Work on `feat-*` or `copilot/*` branches
- **Testing**: Push to `dev` or feature branch triggers dev deployment (ci-dev.yml)
- **Production**: Merge to `main` triggers production deployment (ci.yml)
- All feature branches should be tested in dev environment first
- Review Terraform plan output in CI logs before approving deployments

## Getting Help

- Check **README.md** for setup and deployment overview
- Review **ARCHITECTURE.md** for system architecture and data flows
- Read **DEVELOPMENT.md** for local development setup and troubleshooting
- Review **FEATURE_SINGLE_JOB.md** for job execution details
- Examine existing code patterns in `app/utils/` before implementing new features
- CI/CD workflows in `.github/workflows/` show build and deployment process
- Check `infra/terraform/envs/` for environment-specific configurations
