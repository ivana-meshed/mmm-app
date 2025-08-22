# Base with R 4.3 and system libs
FROM rocker/r-ver:4.3.1

# System deps for R packages (nloptr, rstan/prophet, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev\
    libcurl4-openssl-dev libssl-dev libxml2-dev libgit2-dev libicu-dev \
    libfontconfig1-dev libharfbuzz-dev libfribidi-dev libfreetype6-dev \
    libpng-dev libtiff5-dev libjpeg-dev \
    cmake gfortran pkg-config libnlopt-dev libblas-dev liblapack-dev \
    build-essential git curl && \
    rm -rf /var/lib/apt/lists/*

# Python deps
# option B
RUN pip3 install --no-cache-dir \
    streamlit snowflake-connector-python google-cloud-storage google-cloud-secret-manager pandas numpy scipy nevergrad
# install uv so reticulate/Robyn can find it

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# reticulate sometimes looks here for uv → make a symlink
RUN mkdir -p /root/.cache/R/reticulate/uv/bin \
    && ln -sf "$(command -v uv)" /root/.cache/R/reticulate/uv/bin/uv \
    && uv --version

# tell reticulate to use the system python where nevergrad is installed
ENV RETICULATE_PYTHON=/usr/bin/python3
ENV RETICULATE_AUTOCONFIGURE=0
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

# Build-time sanity check: fail the build if imports don’t work
RUN python3 - <<'PY'
import sys, ctypes, glob, nevergrad, numpy, scipy
print("OK python:", sys.executable)
print("libpython candidates:", glob.glob("/usr/lib/*/libpython*.so*"))
print("nevergrad:", nevergrad.__version__, "numpy:", numpy.__version__, "scipy:", scipy.__version__)
PY

RUN R -q -e "options(Ncpus=parallel::detectCores()); \
    install.packages(c('jsonlite','dplyr','tidyr','lubridate','readr','stringr', \
    'googleCloudStorageR','mime','reticulate','remotes','prophet', \
    'ggplot2','data.table','glmnet','doParallel'), \
    repos='https://cloud.r-project.org'); \
    install.packages('nloptr', repos='https://cloud.r-project.org', type='source')"

RUN R -q -e "options(Ncpus=parallel::detectCores()); \
    if (!requireNamespace('patchwork', quietly=TRUE) || \
    utils::packageVersion('patchwork') < '1.3.1') { \
    remotes::install_github('thomasp85/patchwork', upgrade='never'); \
    }; \
    cat('patchwork version:', as.character(utils::packageVersion('patchwork')), '\n')"

# R base deps + nloptr first
RUN R -q -e "options(Ncpus=parallel::detectCores()); \
    install.packages(c('jsonlite','dplyr','tidyr','lubridate','readr','stringr','googleCloudStorageR','mime','reticulate','remotes','prophet'), repos='https://cloud.r-project.org'); \
    install.packages('nloptr', repos='https://cloud.r-project.org', type='source')"

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
