# Implementation Code: Critical Optimizations

## 1. Resource Scaling (4→8 vCPU)

### Terraform Infrastructure Updates

**File: `infra/terraform/main.tf`**
```hcl
resource "google_cloud_run_service" "svc" {
  name     = var.service_name
  location = var.region

  template {
    metadata {
      annotations = {
        # Enable CPU allocation during request processing only
        "run.googleapis.com/cpu-throttling" = "false"
        # Set minimum instances for pre-warming (see section 3)
        "run.googleapis.com/min-instances" = "2"
        # Set maximum instances for scaling
        "run.googleapis.com/max-instances" = "10"
      }
    }
    
    spec {
      service_account_name  = google_service_account.runner.email
      container_concurrency = 1  # Keep at 1 for resource-intensive training
      timeout_seconds       = 3600  # 1 hour timeout

      containers {
        image = var.image
        
        # UPGRADED RESOURCE ALLOCATION
        resources {
          limits = {
            cpu    = "8"      # ⬆️ Increased from "4"
            memory = "32Gi"   # ⬆️ Increased from "16Gi"
          }
          requests = {
            cpu    = "4"      # Minimum guaranteed CPU
            memory = "16Gi"   # Minimum guaranteed memory
          }
        }

        # Startup probe with longer timeout for heavy containers
        startup_probe {
          tcp_socket { port = 8080 }
          period_seconds        = 30   # Check every 30 seconds
          timeout_seconds       = 10   # Wait 10 seconds per check
          failure_threshold     = 20   # Allow up to 10 minutes for startup
          initial_delay_seconds = 30   # Wait 30 seconds before first check
        }

        # Liveness probe for running containers
        liveness_probe {
          http_get {
            path = "/health"  # We'll implement this endpoint
            port = 8080
          }
          period_seconds    = 60
          timeout_seconds   = 30
          failure_threshold = 3
        }

        env {
          name  = "GCS_BUCKET"
          value = var.bucket_name
        }

        env {
          name  = "APP_ROOT"
          value = "/app"
        }
        
        env {
          name  = "RUN_SERVICE_ACCOUNT_EMAIL"
          value = google_service_account.runner.email
        }

        # NEW: Performance optimization flags
        env {
          name  = "R_MAX_CORES"
          value = "8"  # Use all available CPU cores
        }

        env {
          name  = "OPENBLAS_NUM_THREADS"
          value = "8"  # Optimize BLAS operations
        }

        env {
          name  = "OMP_NUM_THREADS"
          value = "8"  # OpenMP parallelization
        }
        
        # Enable parallel processing in Python
        env {
          name  = "PYTHONUNBUFFERED"
          value = "1"
        }
      }
    }
  }

  traffic {
    percent         = 100
    latest_revision = true
  }

  depends_on = [
    google_project_service.run,
    google_project_service.ar,
    google_project_service.cloudbuild,
    google_project_iam_member.sa_ar_reader,
  ]
}

# Add autoscaling configuration
resource "google_cloud_run_service_iam_member" "autoscaling_admin" {
  location = google_cloud_run_service.svc.location
  service  = google_cloud_run_service.svc.name
  role     = "roles/run.admin"
  member   = "serviceAccount:${google_service_account.runner.email}"
}
```

**File: `infra/terraform/variables.tf`** (Update defaults)
```hcl
variable "project_id" { default = "datawarehouse-422511" }
variable "region" { default = "europe-west1" }
variable "service_name" { default = "mmm-trainer-sa" }
variable "image" { default = "europe-west1-docker.pkg.dev/datawarehouse-422511/mmm-repo/mmm-app:latest" }
variable "bucket_name" { default = "mmm-app-output" }
variable "deployer_sa" { default = "github-deployer@datawarehouse-422511.iam.gserviceaccount.com" }

# NEW: Resource sizing variables
variable "cpu_limit" {
  description = "CPU limit for Cloud Run service"
  type        = string
  default     = "8"
}

variable "memory_limit" {
  description = "Memory limit for Cloud Run service"  
  type        = string
  default     = "32Gi"
}

variable "min_instances" {
  description = "Minimum number of instances for pre-warming"
  type        = number
  default     = 2
}

variable "max_instances" {
  description = "Maximum number of instances for scaling"
  type        = number
  default     = 10
}
```

### Docker Container Optimization

**File: `docker/Dockerfile`** (Add performance optimizations)
```dockerfile
# Base with R 4.3 and system libs
FROM rocker/r-ver:4.3.1

# System deps for R packages (nloptr, rstan/prophet, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev\
    libcurl4-openssl-dev libssl-dev libxml2-dev libgit2-dev libicu-dev \
    libfontconfig1-dev libharfbuzz-dev libfribidi-dev libfreetype6-dev \
    libpng-dev libtiff5-dev libjpeg-dev \
    cmake gfortran pkg-config libnlopt-dev libblas-dev liblapack-dev \
    build-essential git curl \
    # NEW: Add performance libraries
    libopenblas-dev libomp-dev htop \
    && rm -rf /var/lib/apt/lists/*

# NEW: Configure system for high-performance computing
RUN echo 'export OMP_NUM_THREADS=${OMP_NUM_THREADS:-8}' >> /etc/environment && \
    echo 'export OPENBLAS_NUM_THREADS=${OPENBLAS_NUM_THREADS:-8}' >> /etc/environment && \
    echo 'export MKL_NUM_THREADS=${MKL_NUM_THREADS:-8}' >> /etc/environment

# Python deps with performance libraries
RUN pip3 install --no-cache-dir \
    streamlit snowflake-connector-python google-cloud-storage google-cloud-secret-manager \
    pandas numpy scipy nevergrad \
    # NEW: Add performance libraries
    numba pyarrow fastparquet joblib psutil

# Install uv so reticulate/Robyn can find it
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# Reticulate sometimes looks here for uv → make a symlink
RUN mkdir -p /root/.cache/R/reticulate/uv/bin \
    && ln -sf "$(command -v uv)" /root/.cache/R/reticulate/uv/bin/uv \
    && uv --version

# Tell reticulate to use the system python where nevergrad is installed
ENV RETICULATE_PYTHON=/usr/bin/python3
ENV RETICULATE_AUTOCONFIGURE=0
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

# NEW: Performance environment variables
ENV R_MAX_CORES=8
ENV OPENBLAS_NUM_THREADS=8
ENV OMP_NUM_THREADS=8

# Build-time sanity check: fail the build if imports don't work
RUN python3 - <<'PY'
import sys, ctypes, glob, nevergrad, numpy, scipy, pyarrow
print("OK python:", sys.executable)
print("libpython candidates:", glob.glob("/usr/lib/*/libpython*.so*"))
print("nevergrad:", nevergrad.__version__, "numpy:", numpy.__version__, "scipy:", scipy.__version__)
print("pyarrow:", pyarrow.__version__)
PY

# R base deps + performance packages
RUN R -q -e "options(Ncpus=parallel::detectCores()); \
    install.packages(c('jsonlite','dplyr','tidyr','lubridate','readr','stringr', \
    'googleCloudStorageR','mime','reticulate','remotes','prophet', \
    'ggplot2','data.table','glmnet','doParallel', \
    # NEW: Performance packages \
    'arrow','future','future.apply','parallel','foreach'), \
    repos='https://cloud.r-project.org'); \
    install.packages('nloptr', repos='https://cloud.r-project.org', type='source')"

# Make sure ggplot2 is new enough; then install patchwork v1.3.1
RUN R -q -e "options(Ncpus=parallel::detectCores()); \
    repos <- c(CRAN='https://cloud.r-project.org'); \
    if (!requireNamespace('ggplot2', quietly=TRUE) || utils::packageVersion('ggplot2') < '3.5.1') { \
    install.packages('ggplot2', repos=repos); \
    }; \
    if (!requireNamespace('patchwork', quietly=TRUE) || utils::packageVersion('patchwork') < '1.3.1') { \
    remotes::install_github('thomasp85/patchwork@v1.3.1', upgrade='never', dependencies=TRUE); \
    }; \
    stopifnot(utils::packageVersion('ggplot2') >= '3.5.1'); \
    stopifnot(utils::packageVersion('patchwork') >= '1.3.1'); \
    cat('ggplot2:', as.character(utils::packageVersion('ggplot2')), \
    ' patchwork:', as.character(utils::packageVersion('patchwork')),'\n')"

# Robyn from GitHub (avoid Suggests to keep it light)
RUN R -q -e "options(Ncpus=parallel::detectCores()); \
    if (!requireNamespace('Robyn', quietly=TRUE)) ok <- try(remotes::install_github('facebookexperimental/Robyn', subdir='R', upgrade='never', dependencies=c('Depends','Imports')), silent=TRUE); \
    if (!requireNamespace('Robyn', quietly=TRUE)) { message('Robyn install failed'); quit(status=1) }"

WORKDIR /app
COPY app/ /app/
COPY r/   /app/r/
COPY requirements.txt /app/
ENV APP_ROOT=/app

# Cloud Run will inject PORT. Default to 8080 for local runs.
ENV PORT=8080

# Expose for local testing
EXPOSE 8080

# ✅ Use a shell entry so $PORT expands; bind to 0.0.0.0
# Also fail fast if the app file is missing.
CMD ["bash","-lc","test -f streamlit_app.py || { echo 'Missing /app/streamlit_app.py'; ls -la; exit 1; }; python3 -m streamlit run streamlit_app.py --server.address=0.0.0.0 --server.port=${PORT}"]
```

## 2. Data Format Switch (CSV→Parquet)

### Python Data Processing Updates

**File: `app/data_processor.py`** (New file)
```python
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import io
import os
from typing import Optional, Dict, Any
import logging
from google.cloud import storage

logger = logging.getLogger(__name__)

class DataProcessor:
    """Optimized data processor with Parquet support"""
    
    def __init__(self, gcs_bucket: str = None):
        self.gcs_bucket = gcs_bucket or os.getenv("GCS_BUCKET", "mmm-app-output")
        self.storage_client = storage.Client()
        
    def csv_to_parquet(self, csv_data: pd.DataFrame, 
                      output_path: str = None) -> str:
        """Convert CSV DataFrame to Parquet format with optimization"""
        
        # Optimize data types for better compression and speed
        df_optimized = self._optimize_dtypes(csv_data)
        
        # Create Parquet file in memory
        table = pa.Table.from_pandas(df_optimized)
        
        # Use memory buffer for Cloud environment
        buffer = io.BytesIO()
        
        # Write with optimal compression settings
        pq.write_table(
            table, 
            buffer, 
            compression='snappy',  # Good balance of speed vs compression
            use_dictionary=True,   # Better for categorical data
            row_group_size=50000,  # Optimize for typical MMM dataset sizes
            use_byte_stream_split=True  # Better compression for floats
        )
        
        buffer.seek(0)
        
        if output_path:
            # Save to local file
            with open(output_path, 'wb') as f:
                f.write(buffer.read())
            buffer.seek(0)
        
        logger.info(f"Converted DataFrame to Parquet: {len(df_optimized):,} rows, "
                   f"Original CSV size: {df_optimized.memory_usage(deep=True).sum() / 1024**2:.1f} MB")
        
        return buffer
    
    def _optimize_dtypes(self, df: pd.DataFrame) -> pd.DataFrame:
        """Optimize DataFrame data types for better performance"""
        df_opt = df.copy()
        
        for col in df_opt.columns:
            col_data = df_opt[col]
            
            # Skip date columns
            if col.lower() in ['date', 'timestamp'] or pd.api.types.is_datetime64_any_dtype(col_data):
                continue
                
            # Optimize numeric columns
            if pd.api.types.is_numeric_dtype(col_data):
                # Check if it's actually integers
                if col_data.dtype in ['float64', 'float32']:
                    # Check if all values are integers (no decimal part)
                    if col_data.notna().all() and (col_data % 1 == 0).all():
                        # Convert to smallest possible integer type
                        col_min, col_max = col_data.min(), col_data.max()
                        if col_min >= 0:
                            if col_max <= 255:
                                df_opt[col] = col_data.astype('uint8')
                            elif col_max <= 65535:
                                df_opt[col] = col_data.astype('uint16')
                            elif col_max <= 4294967295:
                                df_opt[col] = col_data.astype('uint32')
                            else:
                                df_opt[col] = col_data.astype('uint64')
                        else:
                            if col_min >= -128 and col_max <= 127:
                                df_opt[col] = col_data.astype('int8')
                            elif col_min >= -32768 and col_max <= 32767:
                                df_opt[col] = col_data.astype('int16')
                            elif col_min >= -2147483648 and col_max <= 2147483647:
                                df_opt[col] = col_data.astype('int32')
                            else:
                                df_opt[col] = col_data.astype('int64')
                    else:
                        # Keep as float but optimize precision
                        if col_data.dtype == 'float64':
                            # Check if float32 is sufficient
                            if (col_data.astype('float32') == col_data).all():
                                df_opt[col] = col_data.astype('float32')
                        
            # Optimize string/categorical columns
            elif pd.api.types.is_object_dtype(col_data):
                # Convert to category if few unique values
                unique_ratio = col_data.nunique() / len(col_data)
                if unique_ratio < 0.5:  # Less than 50% unique values
                    df_opt[col] = col_data.astype('category')
        
        memory_reduction = (1 - df_opt.memory_usage(deep=True).sum() / 
                           df.memory_usage(deep=True).sum()) * 100
        
        logger.info(f"Memory usage reduced by {memory_reduction:.1f}%")
        return df_opt
    
    def upload_to_gcs(self, data_buffer: io.BytesIO, 
                     gcs_path: str) -> str:
        """Upload Parquet buffer to GCS"""
        bucket = self.storage_client.bucket(self.gcs_bucket)
        blob = bucket.blob(gcs_path)
        
        data_buffer.seek(0)
        blob.upload_from_file(data_buffer, content_type='application/octet-stream')
        
        logger.info(f"Uploaded Parquet file to gs://{self.gcs_bucket}/{gcs_path}")
        return f"gs://{self.gcs_bucket}/{gcs_path}"
    
    def read_parquet_from_gcs(self, gcs_path: str) -> pd.DataFrame:
        """Read Parquet file from GCS"""
        bucket = self.storage_client.bucket(self.gcs_bucket)
        blob = bucket.blob(gcs_path)
        
        # Download to memory buffer
        buffer = io.BytesIO()
        blob.download_to_file(buffer)
        buffer.seek(0)
        
        # Read Parquet from buffer
        df = pd.read_parquet(buffer)
        
        logger.info(f"Loaded Parquet file from GCS: {len(df):,} rows, "
                   f"{len(df.columns)} columns")
        
        return df
```

### Updated Streamlit Application

**File: `app/streamlit_app.py`** (Update data processing section)
```python
import json, os, subprocess, tempfile, time, shlex
import streamlit as st
import pandas as pd
import snowflake.connector as sf
from data_processor import DataProcessor  # NEW: Import our data processor

st.set_page_config(page_title="Robyn MMM Trainer", layout="wide")
st.title("Robyn MMM Trainer")

APP_ROOT = os.environ.get("APP_ROOT", "/app")
RSCRIPT  = os.path.join(APP_ROOT, "r", "run_all.R")

# NEW: Initialize data processor
@st.cache_resource
def get_data_processor():
    return DataProcessor()

data_processor = get_data_processor()

# ... [existing Snowflake and configuration code] ...

def build_job_json(tmp_dir, csv_path=None, parquet_path=None, annotations_path=None):
    """Updated to support both CSV and Parquet paths"""
    job = {
        "country": country,
        "iterations": int(iterations),
        "trials": int(trials),
        "train_size": parse_train_size(train_size),
        "revision": revision,
        "date_input": date_input,
        "gcs_bucket": gcs_bucket,
        "table": table,
        "query": query,
        # NEW: Support both formats
        "csv_path": csv_path,
        "parquet_path": parquet_path,  # NEW: Parquet path for faster loading
        "paid_media_spends": [s.strip() for s in paid_media_spends.split(",") if s.strip()],
        "paid_media_vars": [s.strip() for s in paid_media_vars.split(",") if s.strip()],
        "context_vars": [s.strip() for s in context_vars.split(",") if s.strip()],
        "factor_vars": [s.strip() for s in factor_vars.split(",") if s.strip()],
        "organic_vars": [s.strip() for s in organic_vars.split(",") if s.strip()],
        "snowflake": {
            "user": sf_user,
            "password": None,
            "account": sf_account,
            "warehouse": sf_wh,
            "database": sf_db,
            "schema": sf_schema,
            "role": sf_role
        },
        "annotations_csv": annotations_path,
        "cache_snapshot": True,
        # NEW: Performance flags
        "use_parquet": True,
        "parallel_processing": True
    }
    job_path = os.path.join(tmp_dir, "job.json")
    with open(job_path, "w") as f:
        json.dump(job, f)
    return job_path

if st.button("Train"):
    if not os.path.isfile(RSCRIPT):
        st.error(f"R script not found at: {RSCRIPT}")
    else:
        with st.spinner("Training… this may take a few minutes."):
            with tempfile.TemporaryDirectory() as td:
                # 1) Query data from Snowflake
                sql = effective_sql()
                csv_path = None
                parquet_path = None
                
                if sql:
                    if not sf_password:
                        st.error("Password is required to pull data from Snowflake.")
                        st.stop()
                    try:
                        st.write("Querying Snowflake…")
                        df = run_sql(sql)
                        
                        # NEW: Create both CSV (for compatibility) and Parquet (for speed)
                        csv_path = os.path.join(td, "input_snapshot.csv")
                        parquet_path = os.path.join(td, "input_snapshot.parquet")
                        
                        # Save CSV for backward compatibility
                        df.to_csv(csv_path, index=False)
                        
                        # NEW: Create optimized Parquet file
                        st.write("Optimizing data format (CSV → Parquet)...")
                        parquet_buffer = data_processor.csv_to_parquet(df, parquet_path)
                        
                        # Show optimization results
                        csv_size = os.path.getsize(csv_path) / 1024**2
                        parquet_size = os.path.getsize(parquet_path) / 1024**2
                        compression_ratio = (1 - parquet_size / csv_size) * 100
                        
                        st.success(f"Data optimization complete:")
                        st.write(f"- Original CSV: {csv_size:.1f} MB")
                        st.write(f"- Optimized Parquet: {parquet_size:.1f} MB")
                        st.write(f"- Size reduction: {compression_ratio:.1f}%")
                        st.write(f"- Pulled {len(df):,} rows from Snowflake")
                        
                    except Exception as e:
                        st.error(f"Query failed: {e}")
                        st.stop()

                # 2) Optional annotations upload
                annotations_path = None
                if ann_file is not None:
                    annotations_path = os.path.join(td, "enriched_annotations.csv")
                    with open(annotations_path, "wb") as f:
                        f.write(ann_file.read())

                # 3) Build job.json with both CSV and Parquet paths
                job_cfg = build_job_json(
                    td, 
                    csv_path=csv_path, 
                    parquet_path=parquet_path,  # NEW: Pass Parquet path
                    annotations_path=annotations_path
                )

                # 4) Set environment variables for performance
                env = os.environ.copy()
                if sf_password:
                    env["SNOWFLAKE_PASSWORD"] = sf_password
                
                # NEW: Performance environment variables
                env["R_MAX_CORES"] = str(os.cpu_count() or 4)
                env["OMP_NUM_THREADS"] = str(os.cpu_count() or 4)
                env["OPENBLAS_NUM_THREADS"] = str(os.cpu_count() or 4)

                cmd = ["Rscript", RSCRIPT, f"job_cfg={job_cfg}"]

                # 5) Execute training
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=env,
                )

                # Store results for download
                st.session_state["train_log_text"] = result.stdout or "(no output)"
                st.session_state["train_exit_code"] = int(result.returncode)

        # Show results
        if result.returncode == 0:
            st.success("Training finished. Artifacts should be in your GCS bucket.")
        else:
            st.error("Training failed. Download the run log for details.")

        st.download_button(
            "Download training log",
            data=result.stdout or "(no output)",
            file_name="robyn_run.log",
            mime="text/plain",
            key="dl_robyn_run_log",
        )

# ... [rest of existing code] ...
```

### Updated R Script for Parquet Support

**File: `r/run_all.R`** (Update data loading section)
```r
#!/usr/bin/env Rscript

## ---------- ENV ---------- 
Sys.setenv(
  RETICULATE_PYTHON = "/usr/bin/python3",
  RETICULATE_AUTOCONFIGURE = "0",
  TZ = "Europe/Berlin",
  # NEW: Performance settings
  R_MAX_CORES = Sys.getenv("R_MAX_CORES", "4"),
  OMP_NUM_THREADS = Sys.getenv("OMP_NUM_THREADS", "4"),
  OPENBLAS_NUM_THREADS = Sys.getenv("OPENBLAS_NUM_THREADS", "4")
)

suppressPackageStartupMessages({
  library(jsonlite)
  library(dplyr)
  library(tidyr)
  library(lubridate)
  library(readr)
  library(stringr)
  library(Robyn)
  library(googleCloudStorageR)
  library(mime)
  library(reticulate)
  library(arrow)        # NEW: For Parquet support
  library(future)       # NEW: For parallel processing
  library(future.apply) # NEW: For parallel apply functions
  library(parallel)     # NEW: For parallel processing
})

# NEW: Configure parallel processing
max_cores <- as.numeric(Sys.getenv("R_MAX_CORES", "4"))
plan(multisession, workers = max_cores)

# ... [existing helper functions] ...

## ---------- 1) LOAD DATA (PARQUET PREFERRED) ----------
cfg <- get_cfg()

# NEW: Check for Parquet file first (faster loading)
if (!is.null(cfg$parquet_path) && file.exists(cfg$parquet_path)) {
  message("→ Reading optimized Parquet file: ", cfg$parquet_path)
  
  # Load Parquet file (much faster than CSV)
  df <- arrow::read_parquet(
    cfg$parquet_path,
    # Optimize memory usage
    as_data_frame = TRUE
  )
  
  message(sprintf("✅ Parquet loaded: %s rows, %s columns in %.2f seconds",
                 format(nrow(df), big.mark = ","),
                 ncol(df),
                 proc.time()[["elapsed"]]))
  
} else if (!is.null(cfg$csv_path) && file.exists(cfg$csv_path)) {
  message("→ Reading CSV provided by Streamlit: ", cfg$csv_path)
  
  # Fallback to CSV with optimized reading
  df <- readr::read_csv(
    cfg$csv_path,
    col_types = cols(.default = col_guess()),
    locale = locale(encoding = "UTF-8"),
    lazy = FALSE,
    show_col_types = FALSE
  )
  
  message(sprintf("✅ CSV loaded: %s rows, %s columns", 
                 format(nrow(df), big.mark = ","),
                 ncol(df)))
  
} else {
  stop(paste(
    "Neither parquet_path nor csv_path found in job.json or files missing.",
    "Streamlit must provide input data."
  ))
}

# Convert to data.frame and ensure consistent column names
df <- as.data.frame(df)
names(df) <- toupper(names(df))

# ... [rest of existing data processing code] ...

## ---------- 7) HYPERPARAMS WITH PARALLEL OPTIMIZATION ----------
hyper_vars <- c(paid_media_vars, organic_vars)
hyperparameters <- list()

# NEW: Use parallel processing for hyperparameter setup if needed
if (length(hyper_vars) > 10) {
  message("→ Setting up hyperparameters in parallel...")
  
  hyperparameters <- future_lapply(hyper_vars, function(v) {
    if (v == "ORGANIC_TRAFFIC") {
      list(
        alphas = c(0.5, 2.0),
        gammas = c(0.3, 0.7), 
        thetas = c(0.9, 0.99)
      )
    } else if (v == "TV_COST") {
      list(
        alphas = c(0.8, 2.2),
        gammas = c(0.6, 0.99),
        thetas = c(0.7, 0.95)
      )
    } else if (v == "PARTNERSHIP_COSTS") {
      list(
        alphas = c(0.65, 2.25),
        gammas = c(0.45, 0.875),
        thetas = c(0.3, 0.625)
      )
    } else {
      list(
        alphas = c(1.0, 3.0),
        gammas = c(0.6, 0.9),
        thetas = c(0.1, 0.4)
      )
    }
  }, future.seed = TRUE)
  
  # Convert to named list
  names(hyperparameters) <- hyper_vars
  
  # Flatten the structure for Robyn
  hyperparameters_flat <- list()
  for (v in names(hyperparameters)) {
    hyperparameters_flat[[paste0(v, "_alphas")]] <- hyperparameters[[v]]$alphas
    hyperparameters_flat[[paste0(v, "_gammas")]] <- hyperparameters[[v]]$gammas
    hyperparameters_flat[[paste0(v, "_thetas")]] <- hyperparameters[[v]]$thetas
  }
  hyperparameters <- hyperparameters_flat
  
} else {
  # Original sequential approach for smaller parameter sets
  for (v in hyper_vars) {
    if (v == "ORGANIC_TRAFFIC") {
      hyperparameters[[paste0(v, "_alphas")]] <- c(0.5, 2.0)
      hyperparameters[[paste0(v, "_gammas")]] <- c(0.3, 0.7)
      hyperparameters[[paste0(v, "_thetas")]] <- c(0.9, 0.99)
    } else if (v == "TV_COST") {
      hyperparameters[[paste0(v, "_alphas")]] <- c(0.8, 2.2)
      hyperparameters[[paste0(v, "_gammas")]] <- c(0.6, 0.99)
      hyperparameters[[paste0(v, "_thetas")]] <- c(0.7, 0.95)
    } else if (v == "PARTNERSHIP_COSTS") {
      hyperparameters[[paste0(v, "_alphas")]] <- c(0.65, 2.25)
      hyperparameters[[paste0(v, "_gammas")]] <- c(0.45, 0.875)
      hyperparameters[[paste0(v, "_thetas")]] <- c(0.3, 0.625)
    } else {
      hyperparameters[[paste0(v, "_alphas")]] <- c(1.0, 3.0)
      hyperparameters[[paste0(v, "_gammas")]] <- c(0.6, 0.9)
      hyperparameters[[paste0(v, "_thetas")]] <- c(0.1, 0.4)
    }
  }
}

hyperparameters[["train_size"]] <- train_size

# NEW: Configure Robyn for parallel processing
InputCollect <- robyn_inputs(
  InputCollect = InputCollect,
  hyperparameters = hyperparameters
)

## ---------- 8) PARALLEL TRAINING WITH OPTIMIZED SETTINGS ----------
message("→ Starting optimized Robyn training with ", max_cores, " cores...")

# NEW: Use all available cores for Robyn training
OutputModels <- robyn_run(
  InputCollect = InputCollect,
  iterations = iter,
  trials = trials,
  ts_validation = TRUE,
  add_penalty_factor = TRUE,
  cores = max_cores  # Use all available CPU cores
)

# ... [rest of existing code] ...
```

## 3. Container Pre-warming Implementation

### Health Check Endpoint

**File: `app/health.py`** (New file)
```python
"""
Health check and warming endpoint for Cloud Run
"""
import os
import time
import psutil
import streamlit as st
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class HealthChecker:
    def __init__(self):
        self.startup_time = datetime.now()
        self.warm_status = {
            'container_ready': False,
            'dependencies_loaded': False,
            'r_ready': False,
            'gcs_authenticated': False
        }
        
    def check_container_health(self):
        """Comprehensive health check for the container"""
        health_status = {
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'uptime_seconds': (datetime.now() - self.startup_time).total_seconds(),
            'checks': {}
        }
        
        try:
            # Check system resources
            health_status['checks']['cpu_usage'] = psutil.cpu_percent(interval=1)
            health_status['checks']['memory_usage'] = psutil.virtual_memory().percent
            health_status['checks']['disk_usage'] = psutil.disk_usage('/').percent
            
            # Check if R is available
            try:
                import subprocess
                result = subprocess.run(['R', '--version'], 
                                      capture_output=True, text=True, timeout=5)
                health_status['checks']['r_available'] = result.returncode == 0
                self.warm_status['r_ready'] = result.returncode == 0
            except Exception as e:
                health_status['checks']['r_available'] = False
                health_status['checks']['r_error'] = str(e)
            
            # Check GCS authentication
            try:
                from google.cloud import storage
                client = storage.Client()
                bucket_name = os.getenv('GCS_BUCKET', 'mmm-app-output')
                bucket = client.bucket(bucket_name)
                # Try to list one object (lightweight operation)
                list(client.list_blobs(bucket, max_results=1))
                health_status['checks']['gcs_authenticated'] = True
                self.warm_status['gcs_authenticated'] = True
            except Exception as e:
                health_status['checks']['gcs_authenticated'] = False
                health_status['checks']['gcs_error'] = str(e)
            
            # Check Python dependencies
            try:
                import pandas, numpy, pyarrow, streamlit
                health_status['checks']['python_deps'] = True
                self.warm_status['dependencies_loaded'] = True
            except ImportError as e:
                health_status['checks']['python_deps'] = False
                health_status['checks']['python_deps_error'] = str(e)
            
            # Overall container readiness
            self.warm_status['container_ready'] = all([
                health_status['checks'].get('r_available', False),
                health_status['checks'].get('gcs_authenticated', False), 
                health_status['checks'].get('python_deps', False)
            ])
            
            health_status['warm_status'] = self.warm_status
            
            # Determine overall health
            critical_checks = ['r_available', 'gcs_authenticated', 'python_deps']
            if not all(health_status['checks'].get(check, False) for check in critical_checks):
                health_status['status'] = 'unhealthy'
            
        except Exception as e:
            health_status['status'] = 'error'
            health_status['error'] = str(e)
            
        return health_status

# Global health checker instance
health_checker = HealthChecker()

def create_health_page():
    """Create Streamlit page for health checks"""
    st.set_page_config(
        page_title="Health Check", 
        page_icon="🏥", 
        layout="wide"
    )
    
    st.title("🏥 Container Health Status")
    
    # Auto-refresh every 10 seconds
    if st.button("🔄 Refresh Health Status") or 'auto_refresh' not in st.session_state:
        st.session_state['auto_refresh'] = True
        st.rerun()
    
    health_status = health_checker.check_container_health()
    
    # Display overall status
    if health_status['status'] == 'healthy':
        st.success("✅ Container is healthy and ready")
    elif health_status['status'] == 'unhealthy':
        st.warning("⚠️ Container has health issues")
    else:
        st.error("❌ Container health check failed")
    
    # Display detailed metrics in columns
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric("CPU Usage", f"{health_status['checks'].get('cpu_usage', 0):.1f}%")
        st.metric("Memory Usage", f"{health_status['checks'].get('memory_usage', 0):.1f}%")
        
    with col2:
        st.metric("Disk Usage", f"{health_status['checks'].get('disk_usage', 0):.1f}%")
        uptime = health_status.get('uptime_seconds', 0)
        st.metric("Uptime", f"{uptime/60:.1f} min")
        
    with col3:
        r_status = "✅" if health_status['checks'].get('r_available') else "❌"
        st.metric("R Available", r_status)
        gcs_status = "✅" if health_status['checks'].get('gcs_authenticated') else "❌"
        st.metric("GCS Auth", gcs_status)
    
    # Warm status indicators
    st.subheader("🔥 Warm-up Status")
    warm_status = health_status.get('warm_status', {})
    
    for component, status in warm_status.items():
        icon = "✅" if status else "⏳"
        st.write(f"{icon} {component.replace('_', ' ').title()}: {'Ready' if status else 'Not Ready'}")
    
    # Detailed health check results
    with st.expander("🔍 Detailed Health Checks"):
        st.json(health_status)
        
    # Auto-refresh countdown
    st.write("---")
    st.write("Page will auto-refresh every 30 seconds when container is warming up")
    
    return health_status

if __name__ == "__main__":
    create_health_page()
```

**File: `app/pages/health.py`** (Streamlit page)
```python
from health import create_health_page
create_health_page()
```

### Container Warming Script

**File: `app/warm_container.py`** (New file)  
```python
"""
Container warming script to pre-load dependencies and authenticate services
"""
import os
import sys
import time
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
import psutil

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ContainerWarmer:
    def __init__(self):
        self.warming_tasks = []
        self.warming_status = {}
        
    def warm_python_environment(self):
        """Pre-import critical Python packages"""
        logger.info("🐍 Warming Python environment...")
        start_time = time.time()
        
        try:
            # Import data processing libraries
            import pandas as pd
            import numpy as np
            import pyarrow as pa
            import pyarrow.parquet as pq
            
            # Import cloud libraries
            from google.cloud import storage, secretmanager
            from google.auth import default
            
            # Import streamlit
            import streamlit as st
            
            # Import snowflake
            import snowflake.connector
            
            # Pre-create commonly used objects
            _ = storage.Client()  # Initialize GCS client
            _ = pd.DataFrame({'test': [1, 2, 3]})  # Pre-allocate pandas
            _ = np.array([1, 2, 3])  # Pre-allocate numpy
            
            elapsed = time.time() - start_time
            logger.info(f"✅ Python environment warmed in {elapsed:.2f}s")
            self.warming_status['python'] = True
            
        except Exception as e:
            logger.error(f"❌ Failed to warm Python environment: {e}")
            self.warming_status['python'] = False
    
    def warm_r_environment(self):
        """Pre-load R and critical packages"""
        logger.info("📊 Warming R environment...")
        start_time = time.time()
        
        try:
            # R script to pre-load packages
            r_warmup_script = """
            # Suppress package startup messages
            suppressPackageStartupMessages({
                library(jsonlite)
                library(dplyr) 
                library(tidyr)
                library(readr)
                library(arrow)
                library(lubridate)
                library(stringr)
                library(googleCloudStorageR)
                library(reticulate)
                library(parallel)
                library(future)
            })
            
            # Configure parallel processing
            max_cores <- as.numeric(Sys.getenv("R_MAX_CORES", "4"))
            plan(multisession, workers = max_cores)
            
            # Configure reticulate  
            Sys.setenv(RETICULATE_PYTHON = "/usr/bin/python3")
            Sys.setenv(RETICULATE_AUTOCONFIGURE = "0")
            
            # Test basic functionality
            df <- data.frame(x = 1:10, y = letters[1:10])
            result <- df %>% filter(x > 5)
            
            cat("R environment warmed successfully\\n")
            """
            
            # Execute R warming script
            result = subprocess.run(
                ['R', '--slave', '--vanilla', '-e', r_warmup_script],
                capture_output=True,
                text=True,
                timeout=60  # 1 minute timeout
            )
            
            if result.returncode == 0:
                elapsed = time.time() - start_time
                logger.info(f"✅ R environment warmed in {elapsed:.2f}s")
                self.warming_status['r'] = True
            else:
                logger.error(f"❌ R warming failed: {result.stderr}")
                self.warming_status['r'] = False
                
        except subprocess.TimeoutExpired:
            logger.error("❌ R environment warming timed out")
            self.warming_status['r'] = False
        except Exception as e:
            logger.error(f"❌ Failed to warm R environment: {e}")
            self.warming_status['r'] = False
    
    def warm_gcs_authentication(self):
        """Pre-authenticate with Google Cloud Services"""
        logger.info("☁️ Warming GCS authentication...")
        start_time = time.time()
        
        try:
            from google.cloud import storage
            from google.auth import default
            
            # Get default credentials
            credentials, project = default()
            
            # Initialize storage client
            storage_client = storage.Client(credentials=credentials)
            
            # Test bucket access
            bucket_name = os.getenv('GCS_BUCKET', 'mmm-app-output')
            bucket = storage_client.bucket(bucket_name)
            
            # Perform lightweight operation to verify access
            list(storage_client.list_blobs(bucket, max_results=1))
            
            elapsed = time.time() - start_time
            logger.info(f"✅ GCS authentication warmed in {elapsed:.2f}s")
            self.warming_status['gcs'] = True
            
        except Exception as e:
            logger.error(f"❌ Failed to warm GCS authentication: {e}")
            self.warming_status['gcs'] = False
    
    def warm_system_resources(self):
        """Warm system-level resources"""
        logger.info("🖥️ Warming system resources...")
        start_time = time.time()
        
        try:
            # Get system information
            cpu_count = os.cpu_count()
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            logger.info(f"System specs: {cpu_count} CPUs, {memory.total//1024**3}GB RAM")
            
            # Set optimal environment variables
            os.environ['R_MAX_CORES'] = str(cpu_count)
            os.environ['OMP_NUM_THREADS'] = str(cpu_count) 
            os.environ['OPENBLAS_NUM_THREADS'] = str(cpu_count)
            
            # Pre-allocate some memory to reduce fragmentation
            dummy_data = [0] * 1000000  # Allocate ~8MB
            del dummy_data
            
            elapsed = time.time() - start_time
            logger.info(f"✅ System resources warmed in {elapsed:.2f}s")
            self.warming_status['system'] = True
            
        except Exception as e:
            logger.error(f"❌ Failed to warm system resources: {e}")
            self.warming_status['system'] = False
    
    def run_parallel_warming(self):
        """Run all warming tasks in parallel"""
        logger.info("🔥 Starting container warming process...")
        total_start_time = time.time()
        
        # Define warming tasks
        warming_tasks = [
            ('system', self.warm_system_resources),
            ('python', self.warm_python_environment),
            ('gcs', self.warm_gcs_authentication),
            ('r', self.warm_r_environment)  # R last as it's most time-consuming
        ]
        
        # Execute tasks in parallel
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_task = {
                executor.submit(task_func): task_name 
                for task_name, task_func in warming_tasks
            }
            
            for future in as_completed(future_to_task):
                task_name = future_to_task[future]
                try:
                    future.result()
                    logger.info(f"✅ {task_name} warming completed")
                except Exception as e:
                    logger.error(f"❌ {task_name} warming failed: {e}")
        
        total_elapsed = time.time() - total_start_time
        successful_tasks = sum(1 for status in self.warming_status.values() if status)
        total_tasks = len(self.warming_status)
        
        logger.info(f"🎯 Container warming completed: {successful_tasks}/{total_tasks} tasks successful in {total_elapsed:.2f}s")
        
        # Write warming status to file for health checks
        try:
            import json
            status_file = '/tmp/warming_status.json'
            with open(status_file, 'w') as f:
                json.dump({
                    'warming_status': self.warming_status,
                    'warming_time': total_elapsed,
                    'timestamp': time.time()
                }, f)
        except Exception as e:
            logger.warning(f"Could not write warming status: {e}")
        
        return self.warming_status

def main():
    """Main warming function"""
    warmer = ContainerWarmer()
    status = warmer.run_parallel_warming()
    
    # Exit with error code if critical components failed
    critical_components = ['python', 'gcs']
    if not all(status.get(comp, False) for comp in critical_components):
        logger.error("❌ Critical components failed to warm")
        sys.exit(1)
    else:
        logger.info("✅ Container is warm and ready")
        sys.exit(0)

if __name__ == "__main__":
    main()
```

### Updated Dockerfile with Pre-warming

**File: `docker/Dockerfile`** (Add warming integration)
```dockerfile
# ... [existing Dockerfile content until WORKDIR] ...

WORKDIR /app
COPY app/ /app/
COPY r/   /app/r/
COPY requirements.txt /app/

# NEW: Make warming script executable
RUN chmod +x /app/warm_container.py

ENV APP_ROOT=/app

# NEW: Create warming entrypoint script
RUN cat > /app/entrypoint.sh << 'EOF'
#!/bin/bash
set -e

echo "🚀 Starting MMM Trainer container..."

# Check if this is a warmup request
if [ "$WARMUP_ONLY" = "true" ]; then
    echo "🔥 Running warmup only..."
    python3 /app/warm_container.py
    exit 0
fi

# Always run warming on startup
echo "🔥 Warming container..."
python3 /app/warm_container.py &
WARMUP_PID=$!

# Start the main application
echo "🌐 Starting Streamlit application..."
test -f streamlit_app.py || { echo 'Missing /app/streamlit_app.py'; ls -la; exit 1; }

# Start streamlit in background
python3 -m streamlit run streamlit_app.py --server.address=0.0.0.0 --server.port=${PORT} &
STREAMLIT_PID=$!

# Wait for warmup to complete (with timeout)
if wait $WARMUP_PID; then
    echo "✅ Container warming completed successfully"
else
    echo "⚠️ Container warming completed with issues"
fi

# Wait for streamlit to exit
wait $STREAMLIT_PID
EOF

RUN chmod +x /app/entrypoint.sh

# Cloud Run will inject PORT. Default to 8080 for local runs.
ENV PORT=8080

# Expose for local testing
EXPOSE 8080

# NEW: Use the warming-enabled entrypoint
CMD ["/app/entrypoint.sh"]
```

### Cloud Run Configuration for Pre-warming

**File: `infra/terraform/main.tf`** (Add pre-warming configuration)
```hcl
# ... [existing Cloud Run service configuration] ...

resource "google_cloud_run_service" "svc" {
  name     = var.service_name
  location = var.region

  template {
    metadata {
      annotations = {
        "run.googleapis.com/cpu-throttling" = "false"
        # NEW: Pre-warming configuration
        "run.googleapis.com/min-instances" = var.min_instances
        "run.googleapis.com/max-instances" = var.max_instances
        # Allocate CPU during startup for warming
        "run.googleapis.com/cpu-throttling" = "false"
        # Increase startup timeout for warming
        "run.googleapis.com/timeout" = "600s"  
      }
    }
    
    spec {
      service_account_name  = google_service_account.runner.email
      container_concurrency = 1
      timeout_seconds       = 3600

      containers {
        image = var.image
        
        resources {
          limits = {
            cpu    = var.cpu_limit
            memory = var.memory_limit
          }
          requests = {
            cpu    = "2"      # Minimum for warming
            memory = "8Gi"    # Minimum for warming
          }
        }

        # NEW: Extended startup probe for warming
        startup_probe {
          http_get {
            path = "/health"  # Our health endpoint
            port = 8080
          }
          period_seconds        = 15   # Check every 15 seconds
          timeout_seconds       = 10   # 10 seconds per check
          failure_threshold     = 30   # Allow up to 7.5 minutes for warmup
          initial_delay_seconds = 10   # Wait 10 seconds before first check
        }

        # Liveness probe for running containers
        liveness_probe {
          http_get {
            path = "/health"
            port = 8080
          }
          period_seconds      = 60
          timeout_seconds     = 10
          failure_threshold   = 3
          initial_delay_seconds = 120  # Wait 2 minutes after startup
        }

        # ... [existing environment variables] ...

        # NEW: Warming configuration
        env {
          name  = "ENABLE_WARMING"
          value = "true"
        }

        env {
          name  = "WARMING_TIMEOUT"
          value = "300"  # 5 minutes
        }
      }
    }
  }

  traffic {
    percent         = 100
    latest_revision = true
  }

  depends_on = [
    google_project_service.run,
    google_project_service.ar,
    google_project_service.cloudbuild,
    google_project_iam_member.sa_ar_reader,
  ]
}

# NEW: Cloud Scheduler job for keeping instances warm
resource "google_cloud_scheduler_job" "warmup_job" {
  name             = "mmm-warmup-job"
  description      = "Keep MMM trainer instances warm"
  schedule         = "*/5 * * * *"  # Every 5 minutes
  time_zone        = "Europe/London"
  attempt_deadline = "60s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "GET"
    uri         = "${google_cloud_run_service.svc.status[0].url}/health"
    
    headers = {
      "User-Agent" = "Cloud-Scheduler-Warmup"
    }
  }

  depends_on = [
    google_cloud_run_service.svc
  ]
}

# Enable Cloud Scheduler API
resource "google_project_service" "scheduler" {
  project            = var.project_id  
  service            = "cloudscheduler.googleapis.com"
  disable_on_destroy = false
}
```

### Testing and Validation Scripts

**File: `scripts/test_optimizations.py`** (New file)
```python
#!/usr/bin/env python3
"""
Test script to validate optimization implementations
"""
import time
import requests
import pandas as pd
import pyarrow.parquet as pq
import subprocess
import os
import json

def test_resource_scaling():
    """Test that the container is using upgraded resources"""
    print("🧪 Testing Resource Scaling...")
    
    try:
        import psutil
        cpu_count = psutil.cpu_count()
        memory = psutil.virtual_memory()
        
        print(f"✅ CPU cores available: {cpu_count}")
        print(f"✅ Total memory: {memory.total // 1024**3} GB")
        
        # Verify environment variables are set
        r_cores = os.getenv('R_MAX_CORES', 'Not set')
        omp_threads = os.getenv('OMP_NUM_THREADS', 'Not set') 
        
        print(f"✅ R_MAX_CORES: {r_cores}")
        print(f"✅ OMP_NUM_THREADS: {omp_threads}")
        
        if cpu_count >= 8 and memory.total >= 30 * 1024**3:  # 30GB+
            print("✅ Resource scaling test PASSED")
            return True
        else:
            print("❌ Resource scaling test FAILED - insufficient resources")
            return False
            
    except Exception as e:
        print(f"❌ Resource scaling test ERROR: {e}")
        return False

def test_parquet_performance():
    """Test Parquet vs CSV performance"""
    print("\n🧪 Testing Parquet Performance...")
    
    try:
        # Create test dataset
        test_data = pd.DataFrame({
            'date': pd.date_range('2024-01-01', periods=10000, freq='D'),
            'cost_column_1': np.random.rand(10000) * 1000,
            'cost_column_2': np.random.rand(10000) * 500, 
            'impressions': np.random.randint(0, 100000, 10000),
            'category': np.random.choice(['A', 'B', 'C', 'D'], 10000)
        })
        
        # Test CSV writing/reading
        csv_start = time.time()
        test_data.to_csv('/tmp/test.csv', index=False)
        csv_write_time = time.time() - csv_start
        
        csv_start = time.time() 
        csv_loaded = pd.read_csv('/tmp/test.csv')
        csv_read_time = time.time() - csv_start
        
        # Test Parquet writing/reading
        parquet_start = time.time()
        test_data.to_parquet('/tmp/test.parquet', compression='snappy')
        parquet_write_time = time.time() - parquet_start
        
        parquet_start = time.time()
        parquet_loaded = pd.read_parquet('/tmp/test.parquet') 
        parquet_read_time = time.time() - parquet_start
        
        # Compare file sizes
        csv_size = os.path.getsize('/tmp/test.csv') / 1024**2  # MB
        parquet_size = os.path.getsize('/tmp/test.parquet') / 1024**2  # MB
        
        print(f"📊 CSV - Write: {csv_write_time:.3f}s, Read: {csv_read_time:.3f}s, Size: {csv_size:.1f}MB")
        print(f"📊 Parquet - Write: {parquet_write_time:.3f}s, Read: {parquet_read_time:.3f}s, Size: {parquet_size:.1f}MB")
        
        read_speedup = csv_read_time / parquet_read_time
        size_reduction = (1 - parquet_size / csv_size) * 100
        
        print(f"✅ Read speedup: {read_speedup:.1f}x")
        print(f"✅ Size reduction: {size_reduction:.1f}%")
        
        if read_speedup > 1.5 and size_reduction > 20:
            print("✅ Parquet performance test PASSED")
            return True
        else:
            print("❌ Parquet performance test FAILED")
            return False
            
    except Exception as e:
        print(f"❌ Parquet performance test ERROR: {e}")
        return False

def test_container_warming():
    """Test container warming functionality"""
    print("\n🧪 Testing Container Warming...")
    
    try:
        # Test health endpoint
        response = requests.get('http://localhost:8080/health', timeout=10)
        
        if response.status_code == 200:
            print("✅ Health endpoint accessible")
            
            # Check if warming status file exists
            if os.path.exists('/tmp/warming_status.json'):
                with open('/tmp/warming_status.json', 'r') as f:
                    warming_status = json.load(f)
                
                print(f"✅ Warming status: {warming_status}")
                
                successful_components = sum(1 for status in warming_status['warming_status'].values() if status)
                total_components = len(warming_status['warming_status'])
                
                if successful_components >= 3:  # At least 3 of 4 components
                    print("✅ Container warming test PASSED")
                    return True
                else:
                    print("❌ Container warming test FAILED - insufficient components warmed")
                    return False
            else:
                print("⚠️ Warming status file not found - container may not be fully warmed")
                return False
        else:
            print(f"❌ Health endpoint returned status {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Container warming test ERROR: {e}")
        return False

def test_r_performance():
    """Test R environment performance"""
    print("\n🧪 Testing R Performance...")
    
    try:
        r_test_script = """
        # Test parallel processing
        library(parallel)
        library(future)
        
        # Check core configuration
        max_cores <- as.numeric(Sys.getenv("R_MAX_CORES", "1"))
        cat("R_MAX_CORES:", max_cores, "\\n")
        
        # Test parallel computation
        start_time <- Sys.time()
        plan(multisession, workers = max_cores)
        
        # Parallel computation test
        result <- future_lapply(1:1000, function(x) {
            sum(rnorm(1000))
        }, future.seed = TRUE)
        
        end_time <- Sys.time()
        elapsed <- as.numeric(end_time - start_time)
        
        cat("Parallel computation time:", elapsed,


