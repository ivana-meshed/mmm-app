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

# NEW: Make warming script executable
RUN chmod +x /app/warm_container.py
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV APP_ROOT=/app

ENV PORT=8080
EXPOSE 8080
CMD ["/app/entrypoint.sh"]


# ✅ Use a shell entry so $PORT expands; bind to 0.0.0.0
# Also fail fast if the app file is missing.
#CMD ["bash","-lc","test -f streamlit_app.py || { echo 'Missing /app/streamlit_app.py'; ls -la; exit 1; }; python3 -m streamlit run streamlit_app.py --server.address=0.0.0.0 --server.port=${PORT}"]