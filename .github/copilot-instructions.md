# GitHub Copilot Instructions for MMM Trainer

This repository contains a Streamlit web application that orchestrates an R/Robyn training pipeline for Marketing Mix Modeling (MMM), deployed on Google Cloud Run.

## Project Overview

- **Frontend**: Streamlit (Python) web application
- **Backend**: R/Robyn for MMM training
- **Data Source**: Snowflake
- **Cloud Platform**: Google Cloud (Cloud Run, Cloud Storage, Artifact Registry)
- **Infrastructure**: Terraform for IaC
- **Container**: Multi-arch Docker image (linux/amd64, linux/arm64)

## Repository Structure

```
app/                    # Streamlit application and Python modules
  streamlit_app.py      # Main Streamlit UI
  trainer.py            # Training orchestration logic
  snowflake_utils.py    # Snowflake connection utilities
  data_processor.py     # Data processing logic
  gcp_secrets.py        # GCP Secret Manager integration
  pages/                # Streamlit multi-page components

r/                      # R scripts for Robyn MMM
  run_all.R             # Main R entrypoint for training

docker/                 # Container configuration
  Dockerfile            # Multi-arch container build

infra/terraform/        # Infrastructure as Code
  main.tf               # Terraform main configuration
  variables.tf          # Variable definitions
  terraform.tfvars      # Variable values

scripts/                # Utility scripts
docs/                   # Documentation and diagrams
.github/workflows/      # CI/CD workflows
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
- `streamlit>=1.36.0` - Web UI framework
- `pandas` - Data manipulation
- `snowflake-connector-python` - Snowflake database connector
- `google-cloud-secret-manager` - GCP secrets management
- `google-cloud-storage` - GCS file operations
- `protobuf<5` - Protocol buffers compatibility

When adding new dependencies:
1. Add to `requirements.txt`
2. Ensure compatibility with existing packages
3. Test in Docker build context
4. Consider impact on container image size

### R Code Standards

- R code lives in the `r/` directory
- Main entrypoint is `r/run_all.R`
- Uses Robyn package for MMM
- Requires `reticulate` for Python integration (nevergrad)
- Environment variable `RETICULATE_PYTHON=/usr/bin/python3` must be set

### Docker & Container Best Practices

- Multi-architecture support (linux/amd64, linux/arm64)
- Base image considerations for R packages
- Python dependencies must be compatible with R's reticulate
- Entry point must bind to `0.0.0.0` and use `$PORT` environment variable
- Service account authentication handled via Application Default Credentials

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
- Cloud Run - Web application hosting
- Cloud Storage - Model artifacts storage
- Artifact Registry - Container image registry
- Secret Manager - Credentials and secrets

### Infrastructure & Terraform

- Terraform configurations in `infra/terraform/`
- Always use `terraform fmt` before committing
- Test infrastructure changes in dev environment first
- Variables are separated in `variables.tf` and `terraform.tfvars`
- State should be stored remotely (GCS backend)

### CI/CD Workflows

**Workflows**:
- `ci.yml` - Production CI/CD (triggers on push to main)
- `ci-dev.yml` - Development CI/CD

**Deployment Process**:
1. Authenticate via OIDC (Workload Identity Federation)
2. Build multi-arch Docker image
3. Push to Artifact Registry
4. Deploy to Cloud Run with appropriate service account
5. Verify deployment health

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

**GCS Interactions**:
```python
from google.cloud import storage

client = storage.Client()
bucket = client.bucket(bucket_name)
blob = bucket.blob(blob_path)
blob.upload_from_string(data)
```

**Snowflake Connections**:
```python
import snowflake.connector

conn = snowflake.connector.connect(
    user=user,
    password=password,
    account=account,
    warehouse=warehouse,
    database=database,
    schema=schema
)
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

- Update README.md when changing architecture or setup process
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
4. Test locally with Docker if possible: `docker build -t mmm-local -f docker/Dockerfile .`
5. Verify changes don't break existing functionality
6. Update documentation if needed
7. Ensure no secrets are committed

## Getting Help

- Check `README.md` for setup and deployment instructions
- Review `docs/` for architecture diagrams and design documents
- Examine existing code patterns before implementing new features
- CI/CD workflows in `.github/workflows/` show build and deployment process
