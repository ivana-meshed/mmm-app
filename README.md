# PR #170 - MMM Benchmarking System

## Overview

This PR implements a comprehensive benchmarking system for systematically testing Marketing Mix Modeling (MMM) configurations. The goal is to identify optimal model configurations through structured testing of different parameters and approaches.

### Parent Application

This MMM Trainer deploys a **Streamlit** web app to **Cloud Run** that orchestrates an **R/Robyn** training pipeline.
Data is fetched from **Snowflake** with Python, and model artifacts are written to **Google Cloud Storage**.
Infrastructure is managed with **Terraform**; container images live in **Artifact Registry**.

## PR #170 Requirements

### Original Problem Statement

"It's hard to tell which Robyn configuration is better for a given goal (fit vs allocation), and we can't systematically evaluate whether our assumptions hold across datasets. This makes onboarding and tuning subjective and non-reproducible."

### Solution Implemented

A benchmarking system that:
1. **Runs queued MMM configs** based on existing selected_columns.json files
2. **Writes results table** with model config, performance metrics, and allocation metrics
3. **Supports systematic testing** of 5 test dimensions:
   - Adstock types (geometric, Weibull CDF, Weibull PDF)
   - Train/val/test splits (different ratios)
   - Time aggregation (daily vs weekly)
   - Spend→variable mappings (spend vs proxy)
   - Seasonality window variations
4. **Single command execution** for comprehensive testing
5. **Result collection and analysis** for decision-making

### Use Cases

- **Preconfigure customer models** with tested defaults
- **Learn generalizable MMM patterns** across datasets
- **Systematic evaluation** of configuration assumptions
- **Reproducible benchmarking** with version-controlled configs

## Quick Start

### One-Line Command (Recommended)

Complete end-to-end workflow - submit, process, and analyze:

```bash
# Test run (default - 10 iterations, 1 trial per variant)
python scripts/run_full_benchmark.py \
  --path gs://mmm-app-output/training_data/de/N_UPLOADS_WEB/20260122_113141/selected_columns.json

# Full production run (1000 iterations, 3 trials per variant)
python scripts/run_full_benchmark.py \
  --path gs://mmm-app-output/training_data/de/N_UPLOADS_WEB/20260122_113141/selected_columns.json \
  --full-run
```

This single command:
1. Loads your selected_columns.json configuration
2. Generates comprehensive benchmark (54 variants: 3 adstock × 3 train_splits × 2 time_agg × 3 spend_var_mapping)
3. Submits all jobs to queue
4. Processes queue until complete
5. Analyzes results and generates visualizations

**Output:**
- CSV: `./benchmark_analysis/results_*.csv`
- Plots: `./benchmark_analysis/*.png` (6 visualization plots)

### Manual Workflow (Alternative)

If you prefer step-by-step control:

```bash
# 1. Run benchmarks
python scripts/benchmark_mmm.py --all-benchmarks --test-run-all

# 2. Process queue
python scripts/process_queue_simple.py --loop --cleanup

# 3. Analyze results
python scripts/analyze_benchmark_results.py --benchmark-id <id> --output-dir ./analysis
```

See **USAGE_GUIDE.md** for detailed instructions and **ANALYSIS_GUIDE.md** for analysis workflows.

## Documentation

### Essential Documentation (5 files)

1. **README.md** (this file) - PR requirements and quick start
2. **IMPLEMENTATION_GUIDE.md** - What was implemented and how it works
3. **USAGE_GUIDE.md** - How to execute benchmarks
4. **ANALYSIS_GUIDE.md** - How to analyze results
5. **ARCHITECTURE.md** - System architecture

### Benchmark Configurations

Located in `benchmarks/` directory:
- `adstock_comparison.json` - Test adstock types
- `train_val_test_splits.json` - Test split ratios
- `time_aggregation.json` - Test daily vs weekly
- `spend_var_mapping.json` - Test spend→var mappings
- `comprehensive_benchmark.json` - Cartesian combinations

## Cost Monitoring and Optimization

This repository includes comprehensive cost monitoring and optimization tools. **Current costs: $8.87/month** (94% reduction from baseline).

See detailed documentation:

- **[COST_STATUS.md](COST_STATUS.md)** - **PRIMARY** cost documentation with current status, actual costs, and monitoring guide
- **[COST_OPTIMIZATION.md](COST_OPTIMIZATION.md)** - Detailed optimization analysis and implementation guide
- **[docs/IDLE_COST_ANALYSIS.md](docs/IDLE_COST_ANALYSIS.md)** - Technical deep-dive on idle cost analysis

### Quick Cost Analysis

Track your daily Google Cloud costs broken down by service:

```bash
# Daily cost tracking (last 7 days)
python scripts/track_daily_costs.py --days 7 --use-user-credentials

# Deep-dive idle cost analysis
python scripts/analyze_idle_costs.py --days 7 --use-user-credentials

# Export to CSV for spreadsheet analysis
python scripts/track_daily_costs.py --days 30 --output costs.csv
```

**New:** Scripts now include a dedicated "Scheduler & Automation Costs" breakdown showing:
- Cloud Scheduler service fees and invocations
- GitHub Actions costs (weekly cleanup automation)
- See [SCHEDULER_COSTS_TRACKING.md](SCHEDULER_COSTS_TRACKING.md) for details

### Cost Optimization Status

All major cost optimizations have been applied:

- ✅ **Scale-to-zero enabled** (min_instances=0) - Eliminates idle costs
- ✅ **CPU throttling enabled** - Reduces CPU allocation when idle
- ✅ **Scheduler enabled** (10-minute intervals) - Automated job processing
- ✅ **Resource optimization** (1 vCPU, 2 GB for web; 8 vCPU, 32 GB for training)
- ✅ **GCS lifecycle policies** - Automatic storage class transitions
- ✅ **Artifact Registry cleanup** - Weekly cleanup of old images

**Result:** $10/month idle costs, $0.50 per production job (94% reduction from $160 baseline)

**Documentation:**
- 📋 [COST_DOCUMENTATION_FINAL.md](COST_DOCUMENTATION_FINAL.md) - **START HERE** - Comprehensive summary
- 📊 [COST_STATUS.md](COST_STATUS.md) - Technical deep-dive and monitoring
- 📈 [COST_ESTIMATES_UPDATED.md](COST_ESTIMATES_UPDATED.md) - Detailed cost tables for planning
- 🎫 [JIRA_COST_SUMMARY.md](JIRA_COST_SUMMARY.md) - JIRA-ready summaries

## Prerequisites

- Google Cloud project with billing enabled
- `gcloud` CLI authenticated (`gcloud auth login` and `gcloud config set project <PROJECT_ID>`)
- Docker with Buildx enabled (`docker buildx create --use`)
- Terraform v1.5+
- Snowflake credentials for the data source
- A GCS bucket (e.g. `mmm-app-output`) to store artifacts

For a complete list of prerequisites, see [REQUIREMENTS.md](docs/REQUIREMENTS.md).

## Local Development

For detailed local development setup, testing, and troubleshooting, see **[DEVELOPMENT.md](DEVELOPMENT.md)**.

Quick start for local development:
```bash
# Clone and setup
git clone https://github.com/ivana-meshed/mmm-app.git
cd mmm-app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure GCP
gcloud auth application-default login
export PROJECT_ID=<your-project-id>
export GCS_BUCKET=mmm-app-output
export TRAINING_JOB_NAME=mmm-app-training

# Run Streamlit
streamlit run app/streamlit_app.py
```

## Local Run (Docker, optional)

```bash
# Build the container locally
docker build -t mmm-local -f docker/Dockerfile .

# Run locally
docker run --rm -p 8080:8080 mmm-local
# Open http://localhost:8080
```

## Build & Push Image (Artifact Registry)

```bash
export REGION=europe-west1
export PROJECT_ID=<your-project-id>
export REPO=mmm-repo
export IMAGE=${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/mmm-app:latest

gcloud auth configure-docker ${REGION}-docker.pkg.dev
docker buildx build --platform linux/amd64,linux/arm64 -t ${IMAGE} -f docker/Dockerfile --push .
```

## Deploy with Terraform

Edit `infra/terraform/terraform.tfvars`:
```hcl
project_id  = "<your-project-id>"
region      = "europe-west1"
bucket_name = "mmm-app-output"
image       = "europe-west1-docker.pkg.dev/<your-project-id>/mmm-repo/mmm-app:latest"
service_name = "mmm-trainer"
```
Then:
```bash
cd infra/terraform
terraform init
terraform apply -var-file="terraform.tfvars"
# Outputs: Cloud Run URL
```

### Service Account & IAM

- Cloud Run service MUST use the **mmm-trainer-sa** (or your chosen SA).
- Grant roles:
  - `roles/artifactregistry.reader` to pull images
  - `roles/storage.objectAdmin` on the artifact bucket (to upload Robyn outputs)
  - `roles/secretmanager.secretAccessor` to read persistent private keys
  - `roles/secretmanager.secretVersionAdder` to save persistent private keys
  - `roles/secretmanager.admin` to delete persistent private keys (optional, for "Clear Saved Key" feature)
- Terraform in `main.tf` configures these bindings.

For more details on persistent private key storage, see [docs/persistent_private_key.md](docs/persistent_private_key.md).

### Verifying SA on the revision

```bash
gcloud run services describe mmm-trainer \
  --region ${REGION} --format='value(spec.template.spec.serviceAccountName)'
```

## Streamlit App Usage

1. Open the Cloud Run URL shown by Terraform.
2. **Sign in** with your authorized Google account (see [Google Authentication](#google-authentication) below).
3. Fill in **Snowflake** connection info.
4. Provide your Snowflake private key (PEM format):
   - Upload a `.pem` file, or paste the key directly
   - Optionally check **"Save this key for future sessions"** to persist it in Google Secret Manager
   - In future sessions, saved keys are loaded automatically
5. Provide either a **table** (`DB.SCHEMA.TABLE`) or a **SQL query**.
6. (Optional) Upload `enriched_annotations.csv`.
7. Review/adjust variable mapping (spends/vars/context/factors/organic).
8. Click **Train**:
   - App pulls data → writes `/tmp/input_snapshot.csv` → invokes `Rscript r/run_all.R job_cfg=...`.
   - R uploads artifacts into `gs://<bucket>/robyn/<revision>/<country>/<timestamp>/`.
   - A `model_summary.json` file is automatically generated with candidate models, Pareto models, and performance metrics.

## Model Summaries

Every training run automatically generates a summary file (`model_summary.json`) capturing:
- All candidate models and their performance metrics
- Pareto optimal models (if any were identified)
- Best model information
- Training configuration and metadata

These summaries are stored in GCS alongside other run artifacts and can be aggregated by country for easy historical tracking.

**Learn more:** See [docs/MODEL_SUMMARY.md](docs/MODEL_SUMMARY.md) for detailed documentation on summary file structure, fields, and usage.

## Google Authentication

The application uses Google OAuth to restrict access to authorized email domains.

### Configuring Allowed Domains

By default, the application allows users from `mesheddata.com`. To add additional domains:

1. Update the `allowed_domains` variable in your Terraform configuration:
   ```hcl
   # In infra/terraform/envs/prod.tfvars
   allowed_domains = "mesheddata.com,example.com"
   ```

2. Apply the Terraform changes:
   ```bash
   cd infra/terraform
   terraform apply -var-file="envs/prod.tfvars"
   ```

For detailed instructions, see [docs/google_auth_domain_configuration.md](docs/google_auth_domain_configuration.md).

## Google Auth (GCS Uploads)

- On **Cloud Run**: the app uses the **service account** identity automatically (Application Default Credentials).
- Locally (if you must test R uploads): set `GOOGLE_APPLICATION_CREDENTIALS` to a JSON key and call `gcloud auth application-default login` or provide a key file.

## Reticulate / nevergrad

- Dockerfile pins `RETICULATE_PYTHON=/usr/bin/python3` and installs `numpy`, `scipy`, `nevergrad`.
- `run_all.R` checks `reticulate::py_config()` and imports `nevergrad` before running.
- If you see “Python shared library not found”, ensure `RETICULATE_AUTOCONFIGURE=0` and that `/usr/bin/python3` exists in the image.

## Streamlit on Cloud Run (CORS / 403)

- Entry point binds to `0.0.0.0` and uses `--server.port=$PORT` so Cloud Run can reach it.
- If you see a browser 403 due to proxy/CORS, set in `streamlit_app.py` (or a `config.toml`):
  ```python
  st.set_page_config(page_title="Robyn MMM Trainer", layout="wide")
  # Streamlit 1.30+ generally OK behind Cloud Run. For older versions, you can also set:
  # os.environ["STREAMLIT_SERVER_ENABLECORS"] = "false"
  # os.environ["STREAMLIT_SERVER_ENABLEWEBSOCKETCOMPRESSION"] = "true"
  ```

## API Access

The application provides REST-like API endpoints for programmatic access:

### Training API
Submit a training job programmatically:
```bash
curl "https://your-app-url.run.app/?api=train&country=fr&iterations=2000&trials=5"
```

### Status API
Check job status:
```bash
curl "https://your-app-url.run.app/?api=status&job_id=abc123"
```

### Metadata API
Retrieve metadata for a country:
```bash
curl "https://your-app-url.run.app/?api=metadata&country=fr&version=latest"
```

All API responses follow a standardized format:
```json
{
  "status": "success|error",
  "timestamp": "2025-11-18T14:00:00",
  "data": { ... },
  "message": "Optional message"
}
```

## Cost Tracking

Track daily Google Cloud costs with detailed breakdowns by service and cost category:

```bash
# View last 30 days of costs (default)
python scripts/track_daily_costs.py

# Export to CSV for analysis
python scripts/track_daily_costs.py --days 7 --output costs.csv

# JSON output for automation
python scripts/track_daily_costs.py --days 30 --json
```

The script breaks down costs by:
- **Services**: mmm-app-web, mmm-app-dev-web, mmm-app-training, mmm-app-dev-training
- **Categories**: user requests, scheduler requests, compute (CPU/memory), storage, registry

See [scripts/COST_TRACKING_README.md](scripts/COST_TRACKING_README.md) for detailed documentation.

**Note**: Requires BigQuery billing export to be enabled. See the documentation for setup instructions.

## Troubleshooting

- **Build fails at Robyn**: ensure `nloptr` and `patchwork>=1.3.1` are installed first; the Dockerfile handles this.
- **nevergrad not found**: verify `pip3 show nevergrad` inside container; ensure `RETICULATE_PYTHON` points to the same python.
- **GCS auth error**: confirm Cloud Run service account has `storage.objectAdmin` and *the revision* uses that SA.
- **Duplicate dates**: the R script collapses duplicates per day; ensure your SQL produces one row/day or let the script aggregate.
- **Training only uses 2 cores on 8 vCPU**: This is a known issue with the R `parallelly` package rejecting Cloud Run's cgroups quota. An override is implemented via the `PARALLELLY_OVERRIDE_CORES` environment variable. See [docs/PARALLELLY_OVERRIDE_FIX.md](docs/PARALLELLY_OVERRIDE_FIX.md) and [docs/TROUBLESHOOTING_PARALLELLY_OVERRIDE.md](docs/TROUBLESHOOTING_PARALLELLY_OVERRIDE.md) for details.

For more troubleshooting guidance, see [docs/DEPLOYMENT_GUIDE.md#troubleshooting](docs/DEPLOYMENT_GUIDE.md#troubleshooting).

## Documentation

### Customer Documentation

| Document | Description |
|----------|-------------|
| [docs/CUSTOMER_DEPLOYMENT_GUIDE.pdf](docs/CUSTOMER_DEPLOYMENT_GUIDE.pdf) | **📄 Complete customer deployment guide (PDF, 21 pages)** - Cost estimates, technical requirements, deployment steps, and maintenance procedures |

### Technical Documentation

| Document | Description |
|----------|-------------|
| [README.md](README.md) | Project overview and quick start |
| [LICENSE](LICENSE) | **Proprietary software license** |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System architecture and components |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Local development setup and testing |
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) | **Basic requirements for deployment and maintenance** |
| [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md) | **Complete deployment guide for new projects** |
| [docs/LICENSING.md](docs/LICENSING.md) | **Licensing guide for administrators and customers** |
| [docs/LICENSING_FAQ.md](docs/LICENSING_FAQ.md) | **Frequently asked questions about licensing** |
| [docs/LICENSING_IMPLEMENTATION_GUIDE.md](docs/LICENSING_IMPLEMENTATION_GUIDE.md) | **Step-by-step guide for implementing licensing** |
| [docs/LICENSE_AGREEMENT_TEMPLATE.txt](docs/LICENSE_AGREEMENT_TEMPLATE.txt) | Customizable license agreement template |
| [docs/LICENSE_HEADER_TEMPLATE.md](docs/LICENSE_HEADER_TEMPLATE.md) | Source code copyright header templates |
| [docs/MODEL_SUMMARY.md](docs/MODEL_SUMMARY.md) | Model summary file structure |
| [docs/OUTPUT_MODELS_PARQUET.md](docs/OUTPUT_MODELS_PARQUET.md) | OutputModels parquet data extraction |
| [docs/PARALLELLY_OVERRIDE_FIX.md](docs/PARALLELLY_OVERRIDE_FIX.md) | **Fix for 8 vCPU core allocation issue** |
| [docs/TROUBLESHOOTING_PARALLELLY_OVERRIDE.md](docs/TROUBLESHOOTING_PARALLELLY_OVERRIDE.md) | **Diagnostic checklist for parallelly override** |
| [docs/8_VCPU_TEST_RESULTS.md](docs/8_VCPU_TEST_RESULTS.md) | Analysis of 8 vCPU upgrade testing |
| [docs/google_auth_domain_configuration.md](docs/google_auth_domain_configuration.md) | OAuth domain configuration |
| [docs/persistent_private_key.md](docs/persistent_private_key.md) | Snowflake key storage |
| [COST_STATUS.md](COST_STATUS.md) | **PRIMARY cost documentation** - Current costs ($8.87/month), optimization status, monitoring guide |
| [COST_OPTIMIZATION.md](COST_OPTIMIZATION.md) | Detailed cost optimization analysis and implementation guide |

## License

This software is proprietary and confidential. Copyright (c) 2024-2026 Meshed Data Consulting. All Rights Reserved.

**This is NOT open source software.**

The MMM Trainer is licensed under a custom proprietary license that:
- Allows installation on customer infrastructure with explicit written authorization
- Prohibits redistribution or making the software available to third parties
- Restricts use to internal business purposes only

### For Potential Licensees

To obtain a license to use this software:

1. Review the [LICENSE](LICENSE) file for complete terms
2. Contact fethu@mesheddata.com to discuss your requirements
3. Receive written authorization to install and use the software

### For License Administrators

See [docs/LICENSING.md](docs/LICENSING.md) for:
- License distribution procedures
- Customer onboarding guidelines
- Compliance and audit procedures
- Creating clean distributions without git history

See [scripts/prepare_distribution.sh](scripts/prepare_distribution.sh) for automated distribution package creation.


## Implementation Details

### Core Components

**1. Benchmarking Script** (`scripts/benchmark_mmm.py`)
- Generates test variants from config files
- Submits jobs to Cloud Run queue
- Collects and exports results
- CLI with --test-run, --test-run-all, --all-benchmarks flags

**2. Queue Processor** (`scripts/process_queue_simple.py`)
- Monitors GCS job queue
- Creates job config JSON and uploads to GCS
- Sets JOB_CONFIG_GCS_PATH for R script
- Passes output_timestamp for consistent result paths
- Verifies results after completion

**3. R Script Updates** (`r/run_all.R`)
- Prioritizes output_timestamp from config
- Reads config from GCS JSON file
- Saves results to exact logged paths

### Key Features

**Result Path Consistency:**
- Python generates timestamp once
- Uploads complete config JSON to GCS
- R reads config from GCS (not env vars)
- Results saved where Python logs them

**Multiple Test Modes:**
- `--test-run`: First variant, 10 iterations (5-10 min)
- `--test-run-all`: All variants, 10 iterations each (15-30 min)
- Full mode: All variants, full iterations (1-2 hours)

**Result Collection:**
- Gathers metrics from all completed runs
- Exports to CSV or Parquet
- Includes rsq_val, nrmse_val, decomp_rssd, etc.

## Command Examples

```bash
# List available benchmarks
python scripts/benchmark_mmm.py --list-configs

# Preview without submitting
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --dry-run

# Quick test single benchmark
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --test-run

# Test all variants of single benchmark
python scripts/benchmark_mmm.py --config benchmarks/adstock_comparison.json --test-run-all

# Run all benchmarks quickly
python scripts/benchmark_mmm.py --all-benchmarks --test-run-all

# Full benchmark execution
python scripts/benchmark_mmm.py --all-benchmarks

# Collect and analyze results
python scripts/benchmark_mmm.py --collect-results <benchmark_id> --export-format csv
```

## System Requirements

- Python 3.9+
- GCP authentication (impersonated service account)
- Access to mmm-app-output GCS bucket
- Cloud Run Jobs API enabled
- Required Python packages in requirements.txt

## Related Documentation

- **Deployment Guide:** `docs/DEPLOYMENT_GUIDE.md`
- **System Architecture:** See ARCHITECTURE.md and architecture diagrams
- **Cost Analysis:** `docs/IDLE_COST_ANALYSIS.md`
- **GCS Scripts:** `scripts/README_GCS_SCRIPTS.md`

## License

See LICENSE file for details.
