#!/bin/bash
set -e

echo "Starting MMM Training Job on Cloud Run..."

# Verify environment
echo "Environment Check:"
echo "- CPU cores available: $(nproc)"
echo "- Memory available: $(free -h | grep 'Mem:' | awk '{print $2}')"
echo "- R_MAX_CORES: ${R_MAX_CORES:-32}"
echo "- OMP_NUM_THREADS: ${OMP_NUM_THREADS:-32}"
echo "- OPENBLAS_NUM_THREADS: ${OPENBLAS_NUM_THREADS:-32}"

# Verify required environment variables
if [ -z "$JOB_CONFIG_GCS_PATH" ]; then
    echo "ERROR: JOB_CONFIG_GCS_PATH environment variable not set"
    exit 1
fi

echo "Job configuration: $JOB_CONFIG_GCS_PATH"

# Set up Python environment
export PYTHONPATH=/usr/bin/python3

# Verify Python dependencies
echo "Verifying Python setup..."
python3 -c "
import nevergrad, numpy, scipy, pyarrow
print(f'nevergrad: {nevergrad.__version__}')
print(f'numpy: {numpy.__version__}')
print(f'scipy: {scipy.__version__}')
print(f'pyarrow: {pyarrow.__version__}')
"

# Verify R setup
echo "Verifying R setup..."
R -q -e "
cat('R version:', R.version.string, '\n')
cat('Available cores:', parallel::detectCores(), '\n')
library(Robyn)
library(future)
library(arrow)
cat('Robyn loaded successfully\n')
"

# Create working directory
mkdir -p /tmp/training-workspace
cd /tmp/training-workspace

# Set additional performance environment variables
export OPENBLAS_CORETYPE=prescott
export BLAS=libopenblas.so
export LAPACK=libopenblas.so

# Run the training script
echo "Starting Robyn training with 32 CPUs..."
exec Rscript /app/run_all.R
