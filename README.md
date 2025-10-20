# MMM Trainer on Google Cloud – README

This repo deploys a **Streamlit** web app to **Cloud Run** that orchestrates an **R/Robyn** training pipeline.
Data is fetched from **Snowflake** with Python, passed as a CSV to R, and the model artifacts are written to **Google Cloud Storage**.
Infrastructure is managed with **Terraform**; the container image lives in **Artifact Registry**.

## High-level Architecture

- User accesses the Streamlit UI at the Cloud Run URL.
- Streamlit (Python) connects to Snowflake, exports a CSV snapshot, and calls `Rscript r/run_all.R job_cfg=/tmp/job.json`.
- R runs Robyn, using `reticulate` → system Python (`/usr/bin/python3`) for **nevergrad**.
- R uploads outputs (RDS, plots, one-pagers, metrics) to the GCS bucket.
- Logs from Python/R appear in the Streamlit UI and in **Cloud Logging**.

See the diagrams:
- **Architecture (boxes & arrows):** `mmm_architecture_v2.png`
- **Runtime sequence (swimlanes):** `mmm_sequence_v2.png`
- **Both pages PDF:** `mmm_system_design_v2.pdf`

## Repo Layout

```
app/
  0_Connect_Your_Data.py     # Streamlit UI; connects to Snowflake, writes CSV, invokes R
r/
  run_all.R            # Robyn training entrypoint (reads job_cfg and csv_path)
infra/terraform/
  main.tf, variables.tf, terraform.tfvars  # Cloud Run, SA, IAM, AR, APIs
docker/
  Dockerfile           # Multi-arch capable; installs R pkgs & Python deps
```

## Prerequisites

- Google Cloud project with billing enabled
- `gcloud` CLI authenticated (`gcloud auth login` and `gcloud config set project <PROJECT_ID>`)
- Docker with Buildx enabled (`docker buildx create --use`)
- Terraform v1.5+
- Snowflake credentials for the data source
- A GCS bucket (e.g. `mmm-app-output`) to store artifacts

## Local Run (optional)

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
- Terraform in `main.tf` configures these bindings.

### Verifying SA on the revision

```bash
gcloud run services describe mmm-trainer \
  --region ${REGION} --format='value(spec.template.spec.serviceAccountName)'
```

## Streamlit App Usage

1. Open the Cloud Run URL shown by Terraform.
2. Fill in **Snowflake** connection info.
3. Provide either a **table** (`DB.SCHEMA.TABLE`) or a **SQL query**.
4. (Optional) Upload `enriched_annotations.csv`.
5. Review/adjust variable mapping (spends/vars/context/factors/organic).
6. Click **Train**:
   - App pulls data → writes `/tmp/input_snapshot.csv` → invokes `Rscript r/run_all.R job_cfg=...`.
   - R uploads artifacts into `gs://<bucket>/robyn/<revision>/<country>/<timestamp>/`.

## Google Auth (GCS Uploads)

- On **Cloud Run**: the app uses the **service account** identity automatically (Application Default Credentials).
- Locally (if you must test R uploads): set `GOOGLE_APPLICATION_CREDENTIALS` to a JSON key and call `gcloud auth application-default login` or provide a key file.

## Reticulate / nevergrad

- Dockerfile pins `RETICULATE_PYTHON=/usr/bin/python3` and installs `numpy`, `scipy`, `nevergrad`.
- `run_all.R` checks `reticulate::py_config()` and imports `nevergrad` before running.
- If you see “Python shared library not found”, ensure `RETICULATE_AUTOCONFIGURE=0` and that `/usr/bin/python3` exists in the image.

## Streamlit on Cloud Run (CORS / 403)

- Entry point binds to `0.0.0.0` and uses `--server.port=$PORT` so Cloud Run can reach it.
- If you see a browser 403 due to proxy/CORS, set in `0_Connect_Your_Data.py` (or a `config.toml`):
  ```python
  st.set_page_config(page_title="Robyn MMM Trainer", layout="wide")
  # Streamlit 1.30+ generally OK behind Cloud Run. For older versions, you can also set:
  # os.environ["STREAMLIT_SERVER_ENABLECORS"] = "false"
  # os.environ["STREAMLIT_SERVER_ENABLEWEBSOCKETCOMPRESSION"] = "true"
  ```

## Troubleshooting

- **Build fails at Robyn**: ensure `nloptr` and `patchwork>=1.3.1` are installed first; the Dockerfile handles this.
- **nevergrad not found**: verify `pip3 show nevergrad` inside container; ensure `RETICULATE_PYTHON` points to the same python.
- **GCS auth error**: confirm Cloud Run service account has `storage.objectAdmin` and *the revision* uses that SA.
- **Duplicate dates**: the R script collapses duplicates per day; ensure your SQL produces one row/day or let the script aggregate.

## License

Apache-2.0 (or your preferred license).

