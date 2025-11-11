# MMM Application Architecture

## Overview

The MMM (Marketing Mix Modeling) application is a Streamlit-based web application deployed on Google Cloud Platform that enables users to run Robyn MMM experiments using Snowflake data. The system consists of a web interface for configuration and monitoring, and Cloud Run Jobs for executing R-based Robyn training.

## System Components

### 1. Web Application (Streamlit)

**Location**: `app/`

The web interface provides:
- Snowflake data connection and querying
- Data mapping and metadata management
- Experiment configuration (single and batch)
- Job queue management
- Results visualization

**Key Files**:
- `streamlit_app.py` - Main entry point and home page
- `pages/` - Multi-page Streamlit application
  - `Connect_Data.py` - Snowflake connection setup
  - `Map_Data.py` - Data column mapping and metadata
  - `Review_Data.py` - Data validation and preview
  - `3_Prepare_Training_Data.py` / `3_Prepare_Training_Datav2.py` - Data preparation
  - `Run_Experiment.py` - Single/batch experiment configuration
  - `5_View_Results.py` - Results visualization
  - `6_View_Best_Results.py` - Best model results

### 2. Training Jobs (Cloud Run Jobs)

**Location**: `r/` and Docker configurations

Training jobs:
- Run Robyn MMM experiments in R
- Execute in isolated Cloud Run Job containers
- Process data from GCS
- Write results back to GCS

**Key Components**:
- `r/run_all.R` - Main R script for training
- `docker/Dockerfile.training` - Training job container
- `docker/Dockerfile.training-base` - Base image with R dependencies
- `docker/training_entrypoint.sh` - Job entrypoint script

### 3. Shared Infrastructure

#### Configuration (`app/config/`)

Centralized settings for:
- GCP project configuration
- Snowflake connection parameters
- Cloud Run settings
- Storage buckets
- Authentication

#### Utilities (`app/utils/`)

Common operations:
- `gcs_utils.py` - Google Cloud Storage operations
- `snowflake_connector.py` - Snowflake connections and queries

#### Core Modules

- `app_shared.py` - Shared helper functions (job management, queue operations, GCS operations)
- `data_processor.py` - Data optimization and Parquet conversion
- `gcp_secrets.py` - Secret Manager integration

## Data Flow

### Single Job Execution

```
User Input (Web UI)
    ↓
Query Snowflake
    ↓
Convert to Parquet + Optimize
    ↓
Upload to GCS (training-data/)
    ↓
Create Job Config (JSON)
    ↓
Upload Config to GCS (training-configs/latest/)
    ↓
Trigger Cloud Run Job
    ↓
Job reads config from GCS
    ↓
Job processes data
    ↓
Results written to GCS (robyn/{revision}/{country}/{timestamp}/)
    ↓
Web UI displays results
```

### Batch Queue Execution

```
User creates Queue (CSV upload or form)
    ↓
Queue saved to GCS (robyn-queues/{queue_name}/queue.json)
    ↓
User starts queue processing
    ↓
Scheduler ticks queue:
  - Lease next PENDING job
  - Execute job (same as single job)
  - Update job status (RUNNING → SUCCEEDED/FAILED)
  - Move finished jobs to job_history.csv
    ↓
Repeat until queue empty
```

## Storage Structure (GCS)

```
gs://{bucket}/
├── training-data/
│   └── {timestamp}/
│       └── input_data.parquet
├── training-configs/
│   ├── {timestamp}/
│   │   └── job_config.json
│   └── latest/
│       └── job_config.json
├── robyn/
│   └── {revision}/
│       └── {country}/
│           └── {timestamp}/
│               ├── robyn_console.log
│               ├── status.json
│               ├── timings.csv
│               └── results/
├── datasets/
│   └── {country}/
│       └── {timestamp}/
│           └── raw.parquet
├── metadata/
│   └── {country}/
│       └── {timestamp}/
│           └── mapping.json
├── robyn-queues/
│   └── {queue_name}/
│       └── queue.json
└── robyn-jobs/
    └── job_history.csv
```

## Deployment

### Environments

The application supports two environments:

1. **Production** (`main` branch)
   - Workspace: `prod`
   - Service: `mmm-app-web`
   - Job: `mmm-app-training`

2. **Development** (`feat-*`, `copilot/*`, `dev` branches)
   - Workspace: `dev`
   - Service: `mmm-app-dev-web`
   - Job: `mmm-app-dev-training`

### CI/CD Pipeline

**Location**: `.github/workflows/`

- `ci.yml` - Main branch deployment
- `ci-dev.yml` - Development branch deployment

**Pipeline Steps**:
1. Authenticate to GCP using Workload Identity Federation
2. Build and push Docker images (web service + training job)
3. Run tests
4. Format code (black + isort)
5. Terraform init/plan/apply
6. Deploy to Cloud Run

### Infrastructure as Code

**Location**: `infra/terraform/`

Terraform manages:
- Cloud Run services and jobs
- Service accounts and IAM
- Secret Manager secrets
- Cloud Storage buckets
- Cloud Scheduler (for queue ticking)

## Authentication

### GCP Authentication

- **CI/CD**: Workload Identity Federation with GitHub OIDC
- **Runtime**: Service accounts with minimal required permissions
  - `WEB_RUNTIME_SA` - Web service runtime
  - `TRAINING_RUNTIME_SA` - Training job runtime

### Snowflake Authentication

Supports two methods:
1. **Key-pair authentication** (preferred)
   - Private key stored in Secret Manager
   - More secure than password
2. **Password authentication** (fallback)
   - For development/testing

### User Authentication

- Google OAuth integration
- Domain restriction (e.g., `@mesheddata.com`)
- Configured via Streamlit's authentication system

## Security Best Practices

1. **Secrets Management**
   - All secrets stored in Secret Manager
   - No secrets in code or environment variables
   - Rotation supported through Secret Manager versions

2. **Least Privilege**
   - Service accounts have minimal required permissions
   - IAM roles follow principle of least privilege

3. **Network Security**
   - Private networking where possible
   - Authentication required for web access
   - Cloud Run services use HTTPS

4. **Data Security**
   - Data encrypted at rest (GCS)
   - Data encrypted in transit (HTTPS)
   - Snowflake connections use secure protocols

## Monitoring and Logging

- **Application Logs**: Cloud Logging
- **Job Status**: Tracked in `job_history.csv` and `status.json`
- **Metrics**: Cloud Monitoring (CPU, memory, request latency)
- **Alerts**: Can be configured via Cloud Monitoring

## Development Workflow

1. Clone repository
2. Create feature branch (`feat-*` or `copilot/*`)
3. Make changes
4. Push to trigger CI/CD
5. Review deployment in dev environment
6. Merge to `main` for production deployment

## Testing

**Location**: `tests/`

- Unit tests for job configuration
- Integration tests for resampling
- Metadata validation tests
- Run with: `python3 -m unittest tests.test_single_job_config -v`

## Configuration

All configuration is centralized in `app/config/settings.py`:
- Environment variables
- GCP project settings
- Snowflake parameters
- Default values
- Schema definitions

## Common Operations

### Adding a New Experiment Parameter

1. Add to `QUEUE_PARAM_COLUMNS` in `app/config/settings.py`
2. Update `build_job_config_from_params()` in `app_shared.py`
3. Update R script (`r/run_all.R`) to use the parameter
4. Update UI forms in relevant pages

### Adding a New Page

1. Create `app/pages/{N}_{Name}.py`
2. Use `require_login_and_domain()` for auth
3. Import shared functions from `app_shared.py`
4. Follow existing page patterns

### Debugging Failed Jobs

1. Check job_history.csv for job status
2. View Cloud Run Jobs logs in GCP Console
3. Download `robyn_console.log` from GCS results folder
4. Check `status.json` for error details

## Future Improvements

- [ ] Extract more common utilities to reduce code duplication
- [ ] Add API endpoints for programmatic access
- [ ] Implement caching for expensive operations
- [ ] Add more comprehensive error handling
- [ ] Improve job queue concurrency control
- [ ] Add data quality checks and validation
- [ ] Implement result comparison tools
