#!/bin/bash
set -euo pipefail

# ── timestamp used for workspace directory name ──────────────────────────────
TRAINING_TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# ── logging helper with timestamp ────────────────────────────────────────────
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "════════════════════════════════════════════════════════════"
log "  MMM Training Job — starting at ${TRAINING_TIMESTAMP}"
log "════════════════════════════════════════════════════════════"

# ── workspace: use a real filesystem path, NOT /tmp (tmpfs) ──────────────────
# /tmp in Cloud Run is a tmpfs (RAM-backed); files written there count against
# the container's memory limit and can cause OOM kills (signal 9).
# We use /workspace instead so all output and temp files hit the writable
# overlay layer, which does NOT consume the same memory pool as tmpfs.
export RUN_WORKSPACE="/workspace/run-${TRAINING_TIMESTAMP}"
mkdir -p "${RUN_WORKSPACE}"

# Redirect R temp files away from /tmp so they don't consume RAM-backed storage
export TMPDIR="${RUN_WORKSPACE}/tmp"
mkdir -p "${TMPDIR}"

log "RUN_WORKSPACE : ${RUN_WORKSPACE}"
log "TMPDIR        : ${TMPDIR}"

# ── CRITICAL: Disable R CMD check core limit ──────────────────────────────────
export _R_CHECK_LIMIT_CORES_=FALSE

# ── CRITICAL: Set parallelly override BEFORE R starts ─────────────────────────
if [ -n "${PARALLELLY_OVERRIDE_CORES:-}" ]; then
    export R_PARALLELLY_AVAILABLECORES_FALLBACK="${PARALLELLY_OVERRIDE_CORES}"
    log "🔧 Parallelly override: _R_CHECK_LIMIT_CORES_=FALSE  R_PARALLELLY_AVAILABLECORES_FALLBACK=${PARALLELLY_OVERRIDE_CORES}"
fi

# ── environment check ─────────────────────────────────────────────────────────
log "── Environment ──────────────────────────────────────────────"
log "CPU cores available : $(nproc)"
log "Memory total        : $(free -h | awk '/^Mem:/{print $2}')"
log "Memory available    : $(free -h | awk '/^Mem:/{print $7}')"
log "R_MAX_CORES         : ${R_MAX_CORES:-8}"
log "OMP_NUM_THREADS     : ${OMP_NUM_THREADS:-8}"
log "OPENBLAS_NUM_THREADS: ${OPENBLAS_NUM_THREADS:-8}"
log "Disk (workspace)    : $(df -h /workspace 2>/dev/null | awk 'NR==2{print $4" free of "$2}' || echo 'unknown')"

# ── required environment variables ────────────────────────────────────────────
: "${GCS_BUCKET:=mmm-app-output}"
: "${JOB_CONFIG_GCS_PATH:=gs://${GCS_BUCKET}/training-configs/latest/job_config.json}"
export JOB_CONFIG_GCS_PATH

log "── Job Configuration ────────────────────────────────────────"
log "JOB_CONFIG_GCS_PATH : ${JOB_CONFIG_GCS_PATH}"
log "GCS_BUCKET          : ${GCS_BUCKET}"
log "PROJECT_ID          : ${PROJECT_ID:-<unset>}"
log "REGION              : ${REGION:-<unset>}"

# ── Python environment ────────────────────────────────────────────────────────
export PYTHONPATH=/usr/bin/python3

log "── Python verification ──────────────────────────────────────"
python3 -c "
import nevergrad, numpy, scipy, pyarrow
print(f'  nevergrad : {nevergrad.__version__}')
print(f'  numpy     : {numpy.__version__}')
print(f'  scipy     : {scipy.__version__}')
print(f'  pyarrow   : {pyarrow.__version__}')
"

# ── R environment verification ────────────────────────────────────────────────
log "── R verification ───────────────────────────────────────────"
R -q -e "
cat('  R version   :', R.version.string, '\n')
cat('  Cores       :', parallel::detectCores(), '\n')
library(Robyn); library(future)
cat('  Robyn       : loaded OK\n')
"

# ── additional BLAS performance settings ──────────────────────────────────────
export OPENBLAS_CORETYPE=prescott
export BLAS=libopenblas.so
export LAPACK=libopenblas.so

# ── change into workspace so any relative-path writes land outside /tmp ───────
cd "${RUN_WORKSPACE}"

log "── Memory snapshot (pre-training) ───────────────────────────"
free -h

log "════════════════════════════════════════════════════════════"
log "🚀 Launching Rscript /app/run_all.R …"
log "════════════════════════════════════════════════════════════"

exec Rscript /app/run_all.R
