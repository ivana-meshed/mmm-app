#!/usr/bin/env Rscript

## ---------- ENV ----------
Sys.setenv(
    RETICULATE_PYTHON = "/usr/bin/python3",
    RETICULATE_AUTOCONFIGURE = "0",
    TZ = "Europe/Berlin",
    R_MAX_CORES = Sys.getenv("R_MAX_CORES", "32"),
    OMP_NUM_THREADS = Sys.getenv("OMP_NUM_THREADS", "32"),
    OPENBLAS_NUM_THREADS = Sys.getenv("OPENBLAS_NUM_THREADS", "32")
)

# Force rebuild timestamp: 2025-12-17T10:18
suppressPackageStartupMessages({
    library(jsonlite)
    library(dplyr)
    library(tidyr)
    library(lubridate)
    library(readr)
    library(stringr)
    # library(Robyn)
    library(googleCloudStorageR)
    library(mime)
    library(reticulate)
    library(arrow)
    library(future)
    library(future.apply)
    library(parallel)
})

suppressPackageStartupMessages({
    library(systemfonts)
    library(ggplot2)
})

`%||%` <- function(a, b) {
    if (is.null(a)) {
        return(b)
    }
    if (length(a) == 0) {
        return(b)
    }
    if (is.character(a) && length(a) == 1L && !nzchar(a)) {
        return(b)
    }
    if (all(is.na(a))) {
        return(b)
    }
    a
}


library(Robyn)

## ---------- LOG HELPERS ----------
safe_write <- function(txt, path) {
    dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
    writeLines(txt, path, useBytes = TRUE)
}

flush_and_ship_log <- function(step = NULL) {
    if (!is.null(step)) message(sprintf("📌 LOG SNAPSHOT @ %s: %s", Sys.time(), step))
    try(flush.console(), silent = TRUE)
    try(gcs_put_safe(log_file, file.path(gcs_prefix, "console.log")), silent = TRUE)
}

log_df_snapshot <- function(df, root, max_rows = 20) {
    # head
    hpath <- file.path(root, "debug", "df_head.csv")
    tryCatch(
        {
            suppressWarnings(write.csv(utils::head(df, max_rows), hpath, row.names = FALSE))
            gcs_put_safe(hpath, file.path(gcs_prefix, "debug/df_head.csv"))
        },
        error = function(e) message("df_head snapshot failed: ", conditionMessage(e))
    )

    # structure
    spath <- file.path(root, "debug", "df_str.txt")
    try(
        {
            cap <- utils::capture.output(str(df))
            safe_write(cap, spath)
            gcs_put_safe(spath, file.path(gcs_prefix, "debug/df_str.txt"))
        },
        silent = TRUE
    )

    # summary + NA counts + date range
    sumtxt <- c(
        sprintf("Rows x Cols: %s x %s", nrow(df), ncol(df)),
        "",
        "Names:",
        paste(names(df), collapse = ", "),
        "",
        "Classes:",
        paste(vapply(df, function(x) class(x)[1], character(1)), collapse = ", "),
        "",
        "NA counts (top 40):"
    )
    na_tbl <- sort(colSums(is.na(df)), decreasing = TRUE)
    na_lines <- utils::capture.output(print(utils::head(na_tbl, 40)))
    if ("date" %in% names(df)) {
        dr <- sprintf("Date range: %s → %s", as.character(min(df$date, na.rm = TRUE)), as.character(max(df$date, na.rm = TRUE)))
    } else {
        dr <- "Date range: <no 'date' column>"
    }
    sumtxt <- c(sumtxt, na_lines, "", dr)

    sumpath <- file.path(root, "debug", "df_summary.txt")
    safe_write(sumtxt, sumpath)
    gcs_put_safe(sumpath, file.path(gcs_prefix, "debug/df_summary.txt"))
}

log_ic_snapshot_files <- function(ic, root, tag = "preflight") {
    # Compact “print(ic)” and fields
    ipath <- file.path(root, "debug", sprintf("InputCollect_%s.txt", tag))
    try(
        {
            lines <- c(
                sprintf("=== InputCollect (%s) ===", tag),
                paste("is.null:", is.null(ic)),
                if (!is.null(ic)) {
                    c(
                        paste("adstock:", ic$adstock %||% "<NULL>"),
                        paste("dep_var:", ic$dep_var %||% "<NULL>"),
                        paste("paid_media_vars:", paste(ic$paid_media_vars %||% character(), collapse = ", ")),
                        paste("paid_media_spends:", paste(ic$paid_media_spends %||% character(), collapse = ", ")),
                        paste("organic_vars:", paste(ic$organic_vars %||% character(), collapse = ", ")),
                        paste("context_vars:", paste(ic$context_vars %||% character(), collapse = ", ")),
                        paste("factor_vars:", paste(ic$factor_vars %||% character(), collapse = ", ")),
                        paste("window:", as.character(ic$window_start %||% NA), "→", as.character(ic$window_end %||% NA)),
                        paste("hyperparameters keys:", paste(setdiff(names(ic$hyperparameters %||% list()), ""), collapse = ", "))
                    )
                }
            )
            safe_write(lines, ipath)
            gcs_put_safe(ipath, file.path(gcs_prefix, sprintf("debug/InputCollect_%s.txt", tag)))
        },
        silent = TRUE
    )

    # dt_input head
    if (!is.null(ic) && is.data.frame(ic$dt_input)) {
        dipath <- file.path(root, "debug", sprintf("InputCollect_%s_dt_input_head.csv", tag))
        try(
            {
                write.csv(utils::head(ic$dt_input, 20), dipath, row.names = FALSE)
                gcs_put_safe(dipath, file.path(gcs_prefix, sprintf("debug/InputCollect_%s_dt_input_head.csv", tag)))
            },
            silent = TRUE
        )
    }
}

log_hyperparameters <- function(hp, root) {
    jpath <- file.path(root, "debug", "hyperparameters.json")
    try(
        {
            json <- jsonlite::toJSON(hp, auto_unbox = TRUE, pretty = TRUE, null = "null")
            writeLines(json, jpath)
            gcs_put_safe(jpath, file.path(gcs_prefix, "debug/hyperparameters.json"))
        },
        silent = TRUE
    )
}

log_cfg_copy <- function(cfg, root) {
    cpath <- file.path(root, "debug", "job_config.copy.json")
    try(
        {
            writeLines(jsonlite::toJSON(cfg, auto_unbox = TRUE, pretty = TRUE), cpath)
            gcs_put_safe(cpath, file.path(gcs_prefix, "debug/job_config.copy.json"))
        },
        silent = TRUE
    )
}

# Define write_trace used later in onepagers error handler (was referenced but not defined)
write_trace <- function(title, e) {
    p <- file.path(dir_path, "debug", paste0(gsub("[^A-Za-z0-9_-]", "_", title), "_error.txt"))
    lines <- c(
        sprintf("[%s] %s", Sys.time(), title),
        paste("Message:", conditionMessage(e)),
        "",
        "--- traceback() ---"
    )
    tb <- utils::capture.output(traceback())
    safe_write(c(lines, tb), p)
    gcs_put_safe(p, file.path(gcs_prefix, "debug", basename(p)))
}



HAVE_FORECAST <- requireNamespace("forecast", quietly = TRUE)

# ---------- DYNAMIC CORE DETECTION ----------
# Cloud Run's actual core availability doesn't always match vCPU allocation
# We need to be conservative to avoid "X simultaneous processes spawned" errors
# which occur when Robyn's .check_ncores() validation fails

# Run diagnostic script if core allocation looks suspicious
# This helps investigate why Cloud Run may be limiting cores
diagnostic_enabled <- Sys.getenv("ROBYN_DIAGNOSE_CORES", "auto")

# Get requested cores from environment (set by terraform)
requested_cores <- as.numeric(Sys.getenv("R_MAX_CORES", "32"))

# Override parallelly detection to force use of requested cores
# This works around parallelly package rejecting Cloud Run's cgroups quota (8.342 CPUs)
# which it considers "out of range" and falls back to 2 cores
# See: https://github.com/ivana-meshed/mmm-app/blob/main/docs/8_VCPU_TEST_RESULTS.md
override_cores <- Sys.getenv("PARALLELLY_OVERRIDE_CORES", "")
if (nzchar(override_cores)) {
    override_value <- as.numeric(override_cores)
    if (!is.na(override_value) && override_value > 0) {
        cat(sprintf("\n🔧 Overriding parallelly core detection with %d cores (PARALLELLY_OVERRIDE_CORES)\n", override_value))
        # Load parallelly package first
        library(parallelly)
        # Set R_PARALLELLY_AVAILABLECORES_FALLBACK environment variable
        # This is checked by parallelly BEFORE it tries cgroups detection
        Sys.setenv(R_PARALLELLY_AVAILABLECORES_FALLBACK = override_value)
        # Also set the option as a backup
        options(parallelly.availableCores.fallback = override_value)
        cat(sprintf("   Set R_PARALLELLY_AVAILABLECORES_FALLBACK=%d\n", override_value))
    }
}

# Detect actual available cores using multiple methods
available_cores_parallelly <- parallelly::availableCores()
available_cores_parallel <- parallel::detectCores()

# Quick check: if there's a significant discrepancy, run diagnostics
should_diagnose <- FALSE
if (diagnostic_enabled == "always") {
    should_diagnose <- TRUE
} else if (diagnostic_enabled == "auto") {
    # Auto-diagnose if available cores are much less than requested
    if (available_cores_parallelly < (requested_cores * 0.5) ||
        available_cores_parallel < (requested_cores * 0.5)) {
        should_diagnose <- TRUE
    }
}

if (should_diagnose) {
    cat("\n⚠️  Core allocation discrepancy detected - running diagnostics...\n")

    # Try to find the diagnostic script in multiple locations
    script_locations <- c(
        # Deployment location (matching Dockerfile.training COPY location)
        "/app/diagnose_cores.R",
        # Same directory as this script (if running locally)
        file.path(dirname(tryCatch(sys.frame(1)$ofile, error = function(e) "")), "diagnose_cores.R"),
        # r/ subdirectory from current working directory
        file.path("r", "diagnose_cores.R"),
        # Alternative deployment path
        "/app/r/diagnose_cores.R"
    )

    diagnostic_script <- NULL
    for (loc in script_locations) {
        if (file.exists(loc)) {
            diagnostic_script <- loc
            break
        }
    }

    if (!is.null(diagnostic_script)) {
        tryCatch(
            {
                source(diagnostic_script, local = TRUE)
            },
            error = function(e) {
                cat(sprintf("⚠️  Diagnostic script failed: %s\n", conditionMessage(e)))
            }
        )
    } else {
        cat(sprintf(
            "⚠️  Diagnostic script not found. Tried: %s\n",
            paste(script_locations, collapse = ", ")
        ))
    }
}

cat(sprintf("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"))
cat(sprintf("🔧 CORE DETECTION ANALYSIS\n"))
cat(sprintf("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"))
cat(sprintf("📊 Environment Configuration:\n"))
cat(sprintf("  - R_MAX_CORES (requested):           %d\n", requested_cores))
cat(sprintf("  - OMP_NUM_THREADS:                   %s\n", Sys.getenv("OMP_NUM_THREADS", "not set")))
cat(sprintf("  - OPENBLAS_NUM_THREADS:              %s\n\n", Sys.getenv("OPENBLAS_NUM_THREADS", "not set")))
cat(sprintf("🔍 Detection Methods:\n"))
cat(sprintf("  - parallelly::availableCores():      %d (cgroup-aware)\n", available_cores_parallelly))
cat(sprintf("  - parallel::detectCores():           %d (system CPUs)\n", available_cores_parallel))

# Use the most conservative estimate between the two methods
# This accounts for Cloud Run's unpredictable core allocation
available_cores <- min(available_cores_parallelly, available_cores_parallel)

# Strategy: Only apply -1 buffer if we're close to the requested amount
# If Cloud Run is severely limiting cores (e.g., 2 when 8 requested), use what's available
# The -1 buffer is only needed when we're at risk of the "X processes spawned" error
actual_cores <- min(requested_cores, available_cores)

# Apply -1 safety buffer only if we have enough cores (> 2) and we're using the requested amount
# This prevents wasting cores when already constrained by Cloud Run
if (actual_cores > 2 && actual_cores >= requested_cores) {
    # We're at or above requested, apply safety buffer
    safe_cores <- max(1, actual_cores - 1)
    buffer_applied <- TRUE
} else {
    # Already constrained by Cloud Run, use what we have
    safe_cores <- max(1, actual_cores)
    buffer_applied <- FALSE
}

cat(sprintf("  - Conservative estimate:              %d\n", available_cores))
cat(sprintf("  - Actual cores to use:                %d\n", actual_cores))
cat(sprintf("  - Safety buffer applied:              %s\n", ifelse(buffer_applied, "Yes (-1)", "No")))
cat(sprintf("  - Final cores for training:           %d\n\n", safe_cores))

# Additional diagnostic information
cat(sprintf("💡 Core Allocation Analysis:\n"))
if (available_cores < requested_cores) {
    discrepancy_pct <- round(100 * (requested_cores - available_cores) / requested_cores, 1)
    cat(sprintf(
        "  ⚠️  CORE SHORTFALL: Requested %d but only %d available (%.1f%% shortfall)\n",
        requested_cores, available_cores, discrepancy_pct
    ))

    # Check if this looks like a Cloud Run cgroups quota issue
    if (available_cores == 2 && requested_cores >= 4) {
        cat(sprintf("  🔍 This pattern (2 cores with %d vCPU) suggests Cloud Run cgroups quota limitation\n", requested_cores))
        cat(sprintf("  💡 Recommendation: Consider using training_cpu=4.0 or training_cpu=2.0 in Terraform\n"))
        cat(sprintf("     to match actual core availability and reduce costs\n"))
    } else if (available_cores < (requested_cores * 0.6)) {
        cat(sprintf("  🔍 Available cores are significantly less than requested\n"))
        cat(sprintf("  💡 Recommendation: Adjust training_max_cores to %d in Terraform configuration\n", available_cores))
    }
} else if (available_cores >= requested_cores) {
    cat(sprintf(
        "  ✅ Core allocation is good: %d cores available for %d requested\n",
        available_cores, requested_cores
    ))
    if (buffer_applied) {
        cat(sprintf("  ℹ️  Using %d cores (safety buffer -1) to prevent Robyn validation errors\n", safe_cores))
    }
} else {
    cat(sprintf("  ✅ Using all %d available cores\n", available_cores))
}
cat(sprintf("\n"))
cat(sprintf("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"))

# Set max_cores for use in robyn_run()
max_cores <- safe_cores

# Set up future plan for parallel processing
plan(multisession, workers = max_cores)

cat(sprintf("✅ Parallel processing initialized with %d workers\n\n", max_cores))

## ---------- HELPERS ----------
should_add_n_searches <- function(dtf, spend_cols, thr = 0.15) {
    if (!"N_SEARCHES" %in% names(dtf) || length(spend_cols) == 0) {
        return(FALSE)
    }
    ts <- rowSums(dtf[, spend_cols, drop = FALSE], na.rm = TRUE)
    cval <- suppressWarnings(abs(cor(dtf$N_SEARCHES, ts, use = "complete.obs")))
    isTRUE(!is.na(cval) && cval < thr)
}

# Download gs://bucket/path -> local_path
gcs_download <- function(gcs_path, local_path) {
    stopifnot(grepl("^gs://", gcs_path))
    path_parts <- sub("^gs://", "", gcs_path)
    bits <- strsplit(path_parts, "/", fixed = TRUE)[[1]]
    bucket <- bits[1]
    object <- paste(bits[-1], collapse = "/")
    googleCloudStorageR::gcs_get_object(
        object_name = object,
        bucket = bucket,
        saveToDisk = local_path,
        overwrite = TRUE
    )
    if (!file.exists(local_path)) stop("Failed to download: ", gcs_path)
    message("Downloaded: ", gcs_path, " -> ", local_path)
}

gcs_put <- function(local_file, object_path, upload_type = c("simple", "resumable")) {
    upload_type <- match.arg(upload_type)
    lf <- normalizePath(local_file, mustWork = FALSE)
    if (!file.exists(lf)) stop("Local file does not exist: ", lf)
    if (grepl("^gs://", object_path)) stop("object_path must be a key, not gs://")
    bkt <- gcs_get_global_bucket()
    if (is.null(bkt) || bkt == "") stop("No bucket set: call gcs_global_bucket(...)")
    typ <- mime::guess_type(lf)
    if (is.na(typ) || typ == "") typ <- "application/octet-stream"
    googleCloudStorageR::gcs_upload(
        file = lf, name = object_path, bucket = bkt, type = typ,
        upload_type = upload_type, predefinedAcl = "bucketLevel"
    )
}
gcs_put_safe <- function(...) {
    tryCatch(gcs_put(...), error = function(e) {
        message("GCS upload failed (non-fatal): ", conditionMessage(e))
    })
}

filter_by_country <- function(dx, country) {
    cn <- toupper(country)
    for (col in c("COUNTRY", "COUNTRY_CODE", "MARKET", "COUNTRY_ISO", "LOCALE")) {
        if (col %in% names(dx)) {
            vals <- unique(toupper(dx[[col]]))
            if (cn %in% vals) {
                message("→ Filtering by ", col, " == ", cn)
                dx <- dx[toupper(dx[[col]]) == cn, , drop = FALSE]
                break
            }
        }
    }
    dx
}

fill_day <- function(x) {
    all <- tibble(date = seq(min(x$date, na.rm = TRUE), max(x$date, na.rm = TRUE), by = "day"))
    full <- dplyr::left_join(all, x, by = "date")
    num <- names(full)[sapply(full, is.numeric)]
    full[num] <- lapply(full[num], function(v) tidyr::replace_na(v, 0))
    full
}

safe_parse_numbers <- function(df, cols) {
    for (cl in intersect(cols, names(df))) {
        x <- df[[cl]]
        if (is.character(x)) {
            df[[cl]] <- readr::parse_number(x)
        } else if (is.factor(x)) {
            df[[cl]] <- readr::parse_number(as.character(x))
        } else if (is.numeric(x)) {
            df[[cl]] <- as.numeric(x)
        } else {
            df[[cl]] <- suppressWarnings(readr::parse_number(as.character(x)))
        }
    }
    df
}

## ---------- GCS AUTH (must happen before any gcs_* calls) ----------
options(
    googleAuthR.scopes.selected = c(
        "https://www.googleapis.com/auth/devstorage.read_write"
    )
)

ensure_gcs_auth <- local({
    authed <- FALSE
    function() {
        if (authed) {
            return(invisible(TRUE))
        }

        # 1) Use a JSON key if provided (local dev / CI)
        creds <- Sys.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if (nzchar(creds) && file.exists(creds)) {
            googleCloudStorageR::gcs_auth(json_file = creds)
        } else {
            # 2) Cloud Run default service account via metadata server
            googleAuthR::gar_gce_auth(
                scopes = "https://www.googleapis.com/auth/devstorage.read_write"
            )
            googleCloudStorageR::gcs_auth(token = googleAuthR::gar_token())
        }
        authed <<- TRUE
        invisible(TRUE)
    }
})


get_cfg_from_env <- function() {
    cfg_path <- Sys.getenv("JOB_CONFIG_GCS_PATH", unset = "")
    if (cfg_path == "") {
        # Fallback when Python client didn’t pass overrides
        bucket <- Sys.getenv("GCS_BUCKET", unset = "mmm-app-output")
        cfg_path <- sprintf("gs://%s/training-configs/latest/job_config.json", bucket)
        message("JOB_CONFIG_GCS_PATH not set; falling back to ", cfg_path)
    }
    tmp <- tempfile(fileext = ".json")
    gcs_download(cfg_path, tmp)
    on.exit(unlink(tmp), add = TRUE)
    jsonlite::fromJSON(tmp)
}

to_scalar <- function(x) {
    x <- suppressWarnings(as.numeric(x))
    if (length(x) == 0) {
        return(NA_real_)
    }
    if (length(x) > 1) {
        return(sum(x, na.rm = TRUE))
    }
    x
}

## ---------- LOAD CFG ----------
message("Loading configuration from Cloud Run Jobs environment...")
ensure_gcs_auth()
cfg <- get_cfg_from_env()

country <- cfg$country
revision <- cfg$revision
date_input <- cfg$date_input # This is an actual date value, not a column name
date_var_name <- cfg$date_var %||% "date" # This is the column name to look for
iter <- as.numeric(cfg$iterations)
trials <- as.numeric(cfg$trials)
train_size <- as.numeric(cfg$train_size)
timestamp <- cfg$timestamp %||% {
    # Use CET (Central European Time) timezone to match Google Cloud Storage
    cet_time <- as.POSIXlt(Sys.time(), tz = "Europe/Paris")
    format(cet_time, "%m%d_%H%M%S")
}

# NEW: Training date range
start_data_date <- as.Date(cfg$start_date %||% "2024-01-01")
end_data_date <- as.Date(cfg$end_date %||% Sys.Date())

# NEW: dep_var and dep_var_type from config
dep_var_from_cfg <- cfg$dep_var %||% "UPLOAD_VALUE"
dep_var_type_from_cfg <- cfg$dep_var_type %||% "revenue"

# NEW: hyperparameter preset
hyperparameter_preset <- cfg$hyperparameter_preset %||% "Meshed recommend"

# NEW: custom hyperparameters (if preset is "Custom")
custom_hyperparameters <- cfg$custom_hyperparameters %||% list()

# NEW: resample parameters
resample_freq <- cfg$resample_freq %||% "none"
# Column aggregation strategies from metadata (passed as JSON string or dict)
column_agg_strategies <- cfg$column_agg_strategies %||% list()

# Parse column_agg_strategies if it's a JSON string
if (is.character(column_agg_strategies) && nzchar(column_agg_strategies)) {
    tryCatch(
        {
            column_agg_strategies <- jsonlite::fromJSON(column_agg_strategies)
        },
        error = function(e) {
            message("Warning: Could not parse column_agg_strategies JSON: ", conditionMessage(e))
            column_agg_strategies <- list()
        }
    )
}

message("→ Column aggregation strategies loaded: ", length(column_agg_strategies), " columns")
if (length(column_agg_strategies) > 0) {
    # Count aggregations by type
    agg_counts <- table(unlist(column_agg_strategies))
    message("   Aggregations by type: ", paste(names(agg_counts), "=", agg_counts, collapse = ", "))
}

# Helper function to parse comma-separated strings from config
parse_csv_config <- function(x) {
    if (is.null(x) || length(x) == 0 || all(is.na(x))) {
        return(character(0))
    }
    if (is.list(x) || (is.character(x) && length(x) > 1)) {
        # Already a list/vector - filter out NA and empty strings
        result <- as.character(x)
        result <- result[!is.na(result) & nzchar(trimws(result))]
        return(result)
    }
    if (is.character(x) && length(x) == 1) {
        # Split comma-separated string and filter out empty/NA values
        result <- trimws(unlist(strsplit(x, ",")))
        result <- result[!is.na(result) & nzchar(result)]
        return(result)
    } else {
        result <- as.character(x)
        result <- result[!is.na(result) & nzchar(trimws(result))]
        return(result)
    }
}

# Parse variable lists from config (they come as comma-separated strings from Python)
paid_media_spends_cfg <- parse_csv_config(cfg$paid_media_spends)
paid_media_vars_cfg <- parse_csv_config(cfg$paid_media_vars)
context_vars_cfg <- parse_csv_config(cfg$context_vars)
factor_vars_cfg <- parse_csv_config(cfg$factor_vars)
organic_vars_cfg <- parse_csv_config(cfg$organic_vars)

dir_path <- path.expand(file.path("~/budget/datasets", revision, country, timestamp))
dir.create(dir_path, recursive = TRUE, showWarnings = FALSE)
gcs_prefix <- file.path("robyn", revision, country, timestamp)

# after you set: dir_path, gcs_prefix
job_started <- Sys.time()
status_json <- file.path(dir_path, "status.json")
writeLines(
    jsonlite::toJSON(
        list(state = "RUNNING", start_time = as.character(job_started)),
        auto_unbox = TRUE
    ),
    status_json
)
gcs_put_safe(status_json, file.path(gcs_prefix, "status.json"))


## ---------- LOGGING ----------
log_file <- file.path(dir_path, "console.log")
dir.create(dirname(log_file), recursive = TRUE, showWarnings = FALSE)
log_con_out <- file(log_file, open = "wt")
log_con_err <- file(log_file, open = "at")
sink(log_con_out, split = TRUE)
sink(log_con_err, type = "message")

cleanup <- function() {
    try(sink(type = "message"), silent = TRUE)
    try(sink(), silent = TRUE)
    try(close(log_con_err), silent = TRUE)
    try(close(log_con_out), silent = TRUE)
    try(gcs_put_safe(log_file, file.path(gcs_prefix, "console.log")), silent = TRUE)
}

## Global panic trap: log any uncaught error before the process exits
install_panic_trap <- function() {
    options(error = function() {
        err <- geterrmessage()
        payload <- list(
            state = "FAILED",
            step = "uncaught_error",
            when = as.character(Sys.time()),
            message = err
        )
        # files
        ptxt <- file.path(dir_path, "panic_error.txt")
        pjson <- file.path(dir_path, "panic_error.json")
        safe_write(c("UNCAUGHT ERROR", as.character(Sys.time()), err), ptxt)
        writeLines(jsonlite::toJSON(payload, auto_unbox = TRUE, pretty = TRUE), pjson)
        # status.json best-effort
        try(writeLines(jsonlite::toJSON(
            c(list(state = "FAILED"), payload),
            auto_unbox = TRUE, pretty = TRUE
        ), status_json), silent = TRUE)
        # ship artifacts
        try(gcs_put_safe(ptxt, file.path(gcs_prefix, "panic_error.txt")), silent = TRUE)
        try(gcs_put_safe(pjson, file.path(gcs_prefix, "panic_error.json")), silent = TRUE)
        try(gcs_put_safe(status_json, file.path(gcs_prefix, "status.json")), silent = TRUE)
        try(gcs_put_safe(log_file, file.path(gcs_prefix, "console.log")), silent = TRUE)
    })
}

on.exit(cleanup(), add = TRUE)
install_panic_trap()


## ---------- PYTHON / NEVERGRAD ----------
reticulate::use_python("/usr/bin/python3", required = TRUE)
cat("---- reticulate::py_config() ----\n")
print(reticulate::py_config())
cat("-------------------------------\n")
if (!reticulate::py_module_available("nevergrad")) stop("nevergrad not importable via reticulate.")

## ---------- GCS AUTH ----------
options(googleAuthR.scopes.selected = "https://www.googleapis.com/auth/devstorage.read_write")
if (nzchar(Sys.getenv("GOOGLE_APPLICATION_CREDENTIALS")) &&
    file.exists(Sys.getenv("GOOGLE_APPLICATION_CREDENTIALS"))) {
    googleCloudStorageR::gcs_auth(json_file = Sys.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
} else {
    token <- googleAuthR::gar_gce_auth(scopes = "https://www.googleapis.com/auth/devstorage.read_write")
    googleCloudStorageR::gcs_auth(token = googleAuthR::gar_token())
}
googleCloudStorageR::gcs_global_bucket(cfg$gcs_bucket %||% "mmm-app-output")
options(googleCloudStorageR.predefinedAcl = "bucketLevel")
message("Using GCS bucket: ", googleCloudStorageR::gcs_get_global_bucket())

## ---------- PARAMS ECHO ----------
cat(
    "✅ Cloud Run Job Parameters\n",
    "  iter       :", iter, "\n",
    "  trials     :", trials, "\n",
    "  country    :", country, "\n",
    "  revision   :", revision, "\n",
    "  date_input :", date_input, "\n",
    "  train_size :", paste(train_size, collapse = ","), "\n",
    "  max_cores  :", max_cores, "\n"
)

## ---------- LOAD DATA ----------
if (!is.null(cfg$data_gcs_path) && nzchar(cfg$data_gcs_path)) {
    message("→ Downloading training data from GCS: ", cfg$data_gcs_path)
    temp_data <- tempfile(fileext = ".parquet")

    ensure_gcs_auth()
    gcs_download(cfg$data_gcs_path, temp_data)
    df <- arrow::read_parquet(temp_data, as_data_frame = TRUE)
    if (!is.data.frame(df) || nrow(df) == 0) {
        stop("Failed to load data from parquet file: ", cfg$data_gcs_path)
    }
    unlink(temp_data)
    message(sprintf("✅ Data loaded: %s rows, %s columns", format(nrow(df), big.mark = ","), ncol(df)))

    log_cfg_copy(cfg, dir_path)
    log_df_snapshot(df, dir_path)
    flush_and_ship_log("after data load")
} else {
    stop("No data_gcs_path provided in configuration.")
}

# (Optional) annotations — download if present so they’re next to outputs
if (!is.null(cfg$annotations_gcs_path) && nzchar(cfg$annotations_gcs_path)) {
    ann_local <- file.path(dir_path, "enriched_annotations.csv")
    try(gcs_download(cfg$annotations_gcs_path, ann_local), silent = TRUE)
}

df <- as.data.frame(df)
names(df) <- toupper(names(df))

## ---------- DATE & CLEAN ----------
# Use date_var_name to find the date column name (convert to uppercase since all names are uppercase now)
# If date_var is not in config, try to find a date column automatically
date_var_name_upper <- toupper(date_var_name)
message("========================================")
message("→ STEP 1: Looking for date column")
message("   date_var from config: '", date_var_name, "'")
message("   date_var uppercased: '", date_var_name_upper, "'")
message("   Total columns in df: ", ncol(df))
message("   Total rows in df: ", nrow(df))
message("   First 30 column names: ", paste(head(names(df), 30), collapse = ", "), if (length(names(df)) > 30) "..." else "")
message("   Checking if 'date' (lowercase) already exists: ", "date" %in% names(df))
message("   Checking if 'DATE' (uppercase) exists: ", "DATE" %in% names(df))

# Try to find the date column - check in order of preference
date_col_found <- NULL
if (date_var_name_upper %in% names(df)) {
    date_col_found <- date_var_name_upper
    message("   ✓ Found exact match: '", date_col_found, "'")
} else {
    message("   ✗ Configured date column '", date_var_name_upper, "' not found")
    # Try common date column names
    common_date_names <- c("DATE", "DS", "DATUM", "FECHA", "DATA")
    message("   Trying common date column names: ", paste(common_date_names, collapse = ", "))
    for (name in common_date_names) {
        if (name %in% names(df)) {
            date_col_found <- name
            message("   ✓ Found date column by common name: '", date_col_found, "'")
            break
        }
    }
}

if (is.null(date_col_found)) {
    message("   ✗✗✗ FATAL: No date column found!")
    message("   Expected: '", date_var_name_upper, "'")
    message("   Tried common names: DATE, DS, DATUM, FECHA, DATA")
    message("   ALL available columns: ", paste(names(df), collapse = ", "))
    stop("No date column found. Expected: ", date_var_name_upper, ". Tried common names: DATE, DS, DATUM, FECHA, DATA. Available columns: ", paste(names(df), collapse = ", "))
}

# Convert the date column in place
message("→ STEP 2: Converting '", date_col_found, "' to Date type")
message("   Column class before: ", paste(class(df[[date_col_found]]), collapse = ", "))
message("   First 3 values: ", paste(head(df[[date_col_found]], 3), collapse = ", "))
df[[date_col_found]] <- if (inherits(df[[date_col_found]], "POSIXt")) as.Date(df[[date_col_found]]) else as.Date(as.character(df[[date_col_found]]))
message("   Column class after: ", paste(class(df[[date_col_found]]), collapse = ", "))
message("   First 3 values after: ", paste(head(df[[date_col_found]], 3), collapse = ", "))

# Rename to lowercase 'date'
message("→ STEP 3: Renaming '", date_col_found, "' to 'date'")
message("   Columns before rename: ", paste(head(names(df), 30), collapse = ", "), if (length(names(df)) > 30) "..." else "")
names(df)[names(df) == date_col_found] <- "date"
message("   Columns after rename: ", paste(head(names(df), 30), collapse = ", "), if (length(names(df)) > 30) "..." else "")
message("   Verification: 'date' in names(df) = ", "date" %in% names(df))
message("   Verification: 'DATE' in names(df) = ", "DATE" %in% names(df))

# Verify date column exists and has valid data
if (!"date" %in% names(df)) {
    message("   ✗✗✗ FATAL: 'date' column was not created successfully!")
    message("   Current columns: ", paste(names(df), collapse = ", "))
    stop("FATAL: 'date' column was not created successfully. Current columns: ", paste(names(df), collapse = ", "))
}
if (nrow(df) == 0) {
    stop("FATAL: Dataframe has 0 rows after date column creation")
}
message("✅ Date column created successfully: ", nrow(df), " rows, range: ", min(df$date, na.rm = TRUE), " to ", max(df$date, na.rm = TRUE))
message("   Final columns after date processing: ", paste(head(names(df), 30), collapse = ", "), if (length(names(df)) > 30) "..." else "")
message("========================================")

message("→ STEP 4: Filtering by country: ", country)
df <- filter_by_country(df, country)

# Verify date column still exists after filtering
message("   After filter_by_country:")
message("   - Rows: ", nrow(df))
message("   - 'date' exists: ", "date" %in% names(df))
message("   - Columns: ", paste(head(names(df), 30), collapse = ", "), if (length(names(df)) > 30) "..." else "")
if (!"date" %in% names(df)) {
    message("   ✗✗✗ FATAL: 'date' column disappeared after filter_by_country!")
    message("   Current columns: ", paste(names(df), collapse = ", "))
    stop("FATAL: 'date' column disappeared after filter_by_country. Current columns: ", paste(names(df), collapse = ", "))
}
if (nrow(df) == 0) {
    stop("FATAL: No data remaining after filtering by country: ", country)
}

message("→ STEP 5: Checking for duplicated dates")
if (anyDuplicated(df$date)) {
    message("   Found ", sum(duplicated(df$date)), " duplicated dates - will collapse")
    sum_or_first <- function(x) if (is.numeric(x)) sum(x, na.rm = TRUE) else dplyr::first(x)

    # Verify date column exists before trying to group by it
    if (!"date" %in% names(df)) {
        message("   ✗✗✗ FATAL: 'date' column missing before deduplication!")
        message("   Current columns: ", paste(names(df), collapse = ", "))
        stop("FATAL: 'date' column missing before deduplication. Current columns: ", paste(names(df), collapse = ", "))
    }

    message("   Columns before deduplication: ", paste(head(names(df), 30), collapse = ", "), if (length(names(df)) > 30) "..." else "")
    message("   About to call: df %>% dplyr::group_by(date) %>% dplyr::summarise(...)")

    df <- df %>%
        dplyr::group_by(date) %>%
        dplyr::summarise(dplyr::across(everything(), sum_or_first), .groups = "drop")

    message("   After deduplication: ", nrow(df), " rows")
    message("   Columns after deduplication: ", paste(head(names(df), 30), collapse = ", "), if (length(names(df)) > 30) "..." else "")
    message("   'date' exists after deduplication: ", "date" %in% names(df))
} else {
    message("   No duplicated dates found")
}

df <- fill_day(df)

cost_cols <- union(grep("_COST$", names(df), value = TRUE), grep("_COSTS$", names(df), value = TRUE))
df <- safe_parse_numbers(df, cost_cols)

## ---------- FEATURE ENGINEERING ----------
# NOTE: Custom tag aggregates (e.g., GA_SMALL_COST_CUSTOM, GA_CAMPAIGN_COST_CUSTOM)
# and TOTAL columns (e.g., GA_TOTAL_COST, GA_TOTAL_SESSIONS) are now created
# automatically in the Python mapping workflow (Map_Your_Data.py) when the user
# clicks "Apply mapping changes". These columns should already exist in the
# dataframe at this point.
#
# Legacy aggregations below are kept for backward compatibility with older data
# that doesn't have the new automatic aggregations.


## ---------- WINDOW / FLAGS ----------
# Dates are now sourced from config (start_data_date, end_data_date); previous hardcoded assignments have been removed.
df <- df %>% filter(date >= start_data_date, date <= end_data_date)
df$DOW <- wday(df$date, label = TRUE)
df$IS_WEEKEND <- ifelse(df$DOW %in% c("Sat", "Sun"), 1, 0)

# CHECK: Skip country if critical columns have no data in the training window
# A country should be skipped if:
# 1. The dependent variable has zero variance (all zeros or all same value)
# 2. ALL paid media spend columns have zero variance (no media spend data)
skip_country <- FALSE
skip_reason <- character(0)

# Check dep_var
if (dep_var_from_cfg %in% names(df)) {
    dep_var_data <- df[[dep_var_from_cfg]]
    if (is.numeric(dep_var_data) && dplyr::n_distinct(dep_var_data, na.rm = TRUE) <= 1) {
        skip_country <- TRUE
        non_na_vals <- unique(na.omit(dep_var_data))
        dep_val <- if (length(non_na_vals) > 0) non_na_vals[1] else "all NA"
        skip_reason <- c(skip_reason, paste0("dep_var '", dep_var_from_cfg, "' has zero variance (all values = ", dep_val, ")"))
    }
} else {
    skip_country <- TRUE
    skip_reason <- c(skip_reason, paste0("dep_var '", dep_var_from_cfg, "' not found in data"))
}

# Check paid_media_spends - skip if ALL have zero variance
if (length(paid_media_spends_cfg) > 0) {
    media_cols_in_data <- intersect(paid_media_spends_cfg, names(df))
    if (length(media_cols_in_data) == 0) {
        skip_country <- TRUE
        skip_reason <- c(skip_reason, "no paid_media_spends columns found in data")
    } else {
        # Check variance of each media column
        media_zero_var <- sapply(media_cols_in_data, function(col) {
            x <- df[[col]]
            is.numeric(x) && dplyr::n_distinct(x, na.rm = TRUE) <= 1
        })
        if (all(media_zero_var)) {
            skip_country <- TRUE
            skip_reason <- c(skip_reason, paste0("all ", length(media_cols_in_data), " paid_media_spends columns have zero variance"))
        }
    }
}

if (skip_country) {
    message("⏭️ SKIPPING COUNTRY: ", country)
    message("   Reason(s): ", paste(skip_reason, collapse = "; "))
    message("   Training window: ", start_data_date, " to ", end_data_date)
    message("   Rows in window: ", nrow(df))

    # Write skip notification to GCS
    skip_file <- file.path(dir_path, "SKIPPED.txt")
    writeLines(c(
        paste0("Country: ", country),
        paste0("Revision: ", revision),
        paste0("Training window: ", start_data_date, " to ", end_data_date),
        paste0("Rows in window: ", nrow(df)),
        "",
        "Skip reason(s):",
        paste0("  - ", skip_reason)
    ), skip_file)
    gcs_put_safe(skip_file, file.path(gcs_prefix, "SKIPPED.txt"))

    # Update status.json to SKIPPED state
    writeLines(
        jsonlite::toJSON(
            list(
                state = "SKIPPED",
                start_time = as.character(job_started),
                end_time = as.character(Sys.time()),
                skip_reason = paste(skip_reason, collapse = "; ")
            ),
            auto_unbox = TRUE, pretty = TRUE
        ),
        status_json
    )
    gcs_put_safe(status_json, file.path(gcs_prefix, "status.json"))

    flush_and_ship_log("country skipped - no usable data")
    message("✅ Country skipped successfully. Exiting without error.")
    quit(save = "no", status = 0)
}

# Drop zero-variance columns AFTER date filtering to ensure we only consider
# variance within the actual training window, not the full dataset
# IMPORTANT: Never drop critical columns even if they have zero variance:
# - dep_var: the dependent variable
# - paid_media_spends: columns needed for media mix modeling
# - paid_media_vars: media variable columns
protected_cols <- unique(c("date", dep_var_from_cfg, paid_media_spends_cfg, paid_media_vars_cfg))
num_cols <- setdiff(names(df), protected_cols)
zero_var <- num_cols[sapply(df[num_cols], function(x) is.numeric(x) && dplyr::n_distinct(x, na.rm = TRUE) <= 1)]
if (length(zero_var)) {
    df <- df[, !(names(df) %in% zero_var), drop = FALSE]
    cat("ℹ️ Dropped zero-variance:", paste(zero_var, collapse = ", "), "\n")
}
# Note: TV_IS_ON may be added here but will be filtered out later if it has
# zero variance (e.g., all 0s). The zero_var_check() function will remove it
# from context_vars and factor_vars before calling robyn_inputs().
if (!"TV_IS_ON" %in% names(df)) df$TV_IS_ON <- 0

## ---------- RESAMPLING ----------
# Apply resampling if configured (Weekly or Monthly aggregation)
message("→ Resampling configuration: freq=", resample_freq)
if (resample_freq != "none" && resample_freq %in% c("W", "M")) {
    message("→ Applying resampling to data with per-column aggregations from metadata...")

    # Log pre-resample state
    pre_resample_rows <- nrow(df)
    pre_resample_date_range <- paste(min(df$date), "to", max(df$date))
    message("   Pre-resample: ", pre_resample_rows, " rows, date range: ", pre_resample_date_range)

    tryCatch(
        {
            # Separate numeric and non-numeric columns
            date_col <- "date"
            numeric_cols <- names(df)[sapply(df, is.numeric)]
            non_numeric_cols <- setdiff(names(df), c(date_col, numeric_cols))

            # Create time period grouping based on frequency
            if (resample_freq == "W") {
                # Weekly aggregation (week starts on Monday)
                message("   Using weekly aggregation (weeks start on Monday)")
                df$resample_period <- floor_date(df$date, unit = "week", week_start = 1)
            } else {
                # Monthly aggregation
                message("   Using monthly aggregation")
                df$resample_period <- floor_date(df$date, unit = "month")
            }

            # Aggregate numeric columns using per-column strategies
            # Note: na.rm=TRUE removes missing values during aggregation. This is intentional
            # to handle gaps in data, but be aware that this silently removes NAs.
            # Count NAs before aggregation for logging
            na_counts_before <- colSums(is.na(df[numeric_cols]))
            total_nas <- sum(na_counts_before)
            if (total_nas > 0) {
                message("   ℹ️ Note: ", total_nas, " NA values found in numeric columns before resampling")
                top_na_cols <- head(sort(na_counts_before[na_counts_before > 0], decreasing = TRUE), 5)
                if (length(top_na_cols) > 0) {
                    message("   Top columns with NAs: ", paste(names(top_na_cols), "=", top_na_cols, collapse = ", "))
                }
            }

            # Build per-column aggregation expressions
            # For each numeric column, use the aggregation strategy from metadata or default to sum
            agg_exprs <- list()
            agg_summary <- list()

            for (col in numeric_cols) {
                # Get aggregation strategy from metadata, default to "sum"
                agg_strategy <- column_agg_strategies[[col]] %||% "sum"

                # Map aggregation strategy to R function
                agg_func <- switch(agg_strategy,
                    "sum" = sum,
                    "mean" = mean,
                    "max" = max,
                    "min" = min,
                    "auto" = first, # For categorical/flag columns, take first value
                    {
                        # Default case: unsupported aggregation type, default to sum
                        message("   ⚠️ WARNING: Unsupported aggregation type '", agg_strategy, "' for column '", col, "', defaulting to 'sum'")
                        sum
                    }
                )

                # Create aggregation expression for this column
                agg_exprs[[col]] <- quo(agg_func(!!sym(col), na.rm = TRUE))

                # Track aggregation types for summary
                agg_summary[[agg_strategy]] <- (agg_summary[[agg_strategy]] %||% 0) + 1
            }

            # Log aggregation summary
            if (length(agg_summary) > 0) {
                agg_summary_str <- paste(names(agg_summary), "=", agg_summary, collapse = ", ")
                message("   Column aggregations: ", agg_summary_str)
            } else {
                message("   Using default 'sum' aggregation for all numeric columns")
            }

            # Apply aggregations using the dynamically built expressions
            df_resampled <- df %>%
                group_by(resample_period) %>%
                summarise(!!!agg_exprs, .groups = "drop")

            # Handle non-numeric columns (take first value in each period)
            if (length(non_numeric_cols) > 0) {
                df_non_numeric <- df %>%
                    group_by(resample_period) %>%
                    summarise(
                        across(all_of(non_numeric_cols), ~ first(.x)),
                        .groups = "drop"
                    )
                df_resampled <- left_join(df_resampled, df_non_numeric, by = "resample_period")
            }

            # Rename period column back to date
            df_resampled <- df_resampled %>%
                rename(date = resample_period)

            # Ensure date is Date type
            df_resampled$date <- as.Date(df_resampled$date)

            # Replace original dataframe
            df <- df_resampled

            # Log post-resample state
            post_resample_rows <- nrow(df)
            post_resample_date_range <- paste(min(df$date), "to", max(df$date))
            message("✅ Resampling complete:")
            message("   Post-resample: ", post_resample_rows, " rows, date range: ", post_resample_date_range)
            message(
                "   Rows reduced from ", pre_resample_rows, " to ", post_resample_rows,
                " (", round(100 * (1 - post_resample_rows / pre_resample_rows), 1), "% reduction)"
            )

            # Log snapshot after resampling
            log_df_snapshot(df, dir_path, max_rows = 20)
            flush_and_ship_log("after resampling")
        },
        error = function(e) {
            msg <- paste("Resampling failed:", conditionMessage(e))
            message("❌ ", msg)

            # Log the error
            resample_err_file <- file.path(dir_path, "resample_error.txt")
            writeLines(c(
                "RESAMPLING ERROR",
                paste0("When: ", Sys.time()),
                paste0("Frequency: ", resample_freq),
                paste0("Message: ", conditionMessage(e)),
                "",
                "Stack trace:",
                paste(capture.output(traceback()), collapse = "\n")
            ), resample_err_file)
            gcs_put_safe(resample_err_file, file.path(gcs_prefix, basename(resample_err_file)))

            # Continue without resampling (use original data)
            message("⚠️ Continuing with original (non-resampled) data")
        }
    )
} else {
    message("→ No resampling applied (freq=", resample_freq, ")")
}

## ---------- DRIVERS ----------
paid_media_spends <- intersect(paid_media_spends_cfg, names(df))
paid_media_vars <- intersect(paid_media_vars_cfg, names(df))
stopifnot(length(paid_media_spends) == length(paid_media_vars))

keep_idx <- vapply(seq_along(paid_media_spends), function(i) sum(df[[paid_media_spends[i]]], na.rm = TRUE) > 0, logical(1))
paid_media_spends <- paid_media_spends[keep_idx]
paid_media_vars <- paid_media_vars[keep_idx]

context_vars <- intersect(context_vars_cfg, names(df))
factor_vars <- intersect(factor_vars_cfg, names(df))

# Auto-add factor_vars to context_vars (requirement 6)
if (length(factor_vars) > 0) {
    context_vars <- unique(c(context_vars, factor_vars))
}

org_base <- intersect(organic_vars_cfg %||% "ORGANIC_TRAFFIC", names(df))
organic_vars <- if (should_add_n_searches(df, paid_media_spends) && "N_SEARCHES" %in% names(df)) unique(c(org_base, "N_SEARCHES")) else org_base

# Filter out zero-variance columns from context_vars and factor_vars
# This prevents robyn_inputs() from failing with "no-variance" error
# after data filtering/resampling may have reduced variance
zero_var_check <- function(var_list, data) {
    if (length(var_list) == 0) {
        return(character(0))
    }
    has_variance <- vapply(var_list, function(v) {
        if (!v %in% names(data)) {
            return(FALSE)
        }
        x <- data[[v]]
        # Check variance for both numeric and factor/character columns
        dplyr::n_distinct(x, na.rm = TRUE) > 1
    }, logical(1))
    removed <- var_list[!has_variance]
    if (length(removed) > 0) {
        message("ℹ️ Removed zero-variance variables: ", paste(removed, collapse = ", "))
    }
    var_list[has_variance]
}

context_vars <- zero_var_check(context_vars, df)
factor_vars <- zero_var_check(factor_vars, df)
organic_vars <- zero_var_check(organic_vars, df)

adstock <- cfg$adstock %||% "geometric"

cat(
    "✅ Drivers\n",
    "  paid_media_spends:", paste(paid_media_spends, collapse = ", "), "\n",
    "  paid_media_vars  :", paste(paid_media_vars, collapse = ", "), "\n",
    "  context_vars     :", paste(context_vars, collapse = ", "), "\n",
    "  factor_vars      :", paste(factor_vars, collapse = ", "), "\n",
    "  organic_vars     :", paste(organic_vars, collapse = ", "), "\n",
    "  adstock          :", adstock, "\n"
)

## ---------- DEFINE HYPERPARAMETER PRESETS FUNCTION ----------
# Define this function before we use it
get_hyperparameter_ranges <- function(preset, adstock_type, var_name) {
    # Check if preset is "Custom" and custom_hyperparameters is provided
    if (preset == "Custom" && length(custom_hyperparameters) > 0) {
        # First check for variable-specific custom hyperparameters (new format)
        var_alphas_key <- paste0(var_name, "_alphas")
        if (!is.null(custom_hyperparameters[[var_alphas_key]])) {
            # Variable-specific hyperparameters found
            if (adstock_type == "geometric") {
                return(list(
                    alphas = custom_hyperparameters[[var_alphas_key]],
                    gammas = custom_hyperparameters[[paste0(var_name, "_gammas")]] %||% c(0.6, 0.9),
                    thetas = custom_hyperparameters[[paste0(var_name, "_thetas")]] %||% c(0.1, 0.4)
                ))
            } else if (adstock_type %in% c("weibull_cdf", "weibull_pdf")) {
                return(list(
                    alphas = custom_hyperparameters[[var_alphas_key]],
                    gammas = custom_hyperparameters[[paste0(var_name, "_gammas")]] %||% c(0.3, 1),
                    shapes = custom_hyperparameters[[paste0(var_name, "_shapes")]] %||% c(0.5, 2.5),
                    scales = custom_hyperparameters[[paste0(var_name, "_scales")]] %||% c(0.001, 0.15)
                ))
            }
        }

        # Fall back to global custom hyperparameters (old format for backward compatibility)
        if (adstock_type == "geometric") {
            return(list(
                alphas = c(
                    custom_hyperparameters$alphas_min %||% 1.0,
                    custom_hyperparameters$alphas_max %||% 3.0
                ),
                gammas = c(
                    custom_hyperparameters$gammas_min %||% 0.6,
                    custom_hyperparameters$gammas_max %||% 0.9
                ),
                thetas = c(
                    custom_hyperparameters$thetas_min %||% 0.1,
                    custom_hyperparameters$thetas_max %||% 0.4
                )
            ))
        } else if (adstock_type %in% c("weibull_cdf", "weibull_pdf")) {
            return(list(
                alphas = c(
                    custom_hyperparameters$alphas_min %||% 0.5,
                    custom_hyperparameters$alphas_max %||% 3.0
                ),
                gammas = c(
                    custom_hyperparameters$gammas_min %||% 0.3,
                    custom_hyperparameters$gammas_max %||% 1.0
                ),
                shapes = c(
                    custom_hyperparameters$shapes_min %||% 0.5,
                    custom_hyperparameters$shapes_max %||% 2.5
                ),
                scales = c(
                    custom_hyperparameters$scales_min %||% 0.001,
                    custom_hyperparameters$scales_max %||% 0.15
                )
            ))
        }
    }

    # Default ranges (for geometric adstock)
    if (adstock_type == "geometric") {
        if (preset == "Facebook recommend") {
            # Facebook's recommended ranges for geometric
            if (var_name == "ORGANIC_TRAFFIC") {
                list(alphas = c(0.5, 3), gammas = c(0.3, 1), thetas = c(0, 0.3))
            } else if (var_name == "TV_COST") {
                list(alphas = c(0.5, 3), gammas = c(0.3, 1), thetas = c(0.3, 0.8))
            } else {
                list(alphas = c(0.5, 3), gammas = c(0.3, 1), thetas = c(0, 0.3))
            }
        } else if (preset == "Meshed recommend") {
            # Meshed's customized ranges
            if (var_name == "ORGANIC_TRAFFIC") {
                list(alphas = c(0.5, 2.0), gammas = c(0.3, 0.7), thetas = c(0.9, 0.99))
            } else if (var_name == "TV_COST") {
                list(alphas = c(0.8, 2.2), gammas = c(0.6, 0.99), thetas = c(0.7, 0.95))
            } else if (var_name == "PARTNERSHIP_COSTS") {
                list(alphas = c(0.65, 2.25), gammas = c(0.45, 0.875), thetas = c(0.3, 0.625))
            } else {
                list(alphas = c(1.0, 3.0), gammas = c(0.6, 0.9), thetas = c(0.1, 0.4))
            }
        } else {
            # Custom preset fallback - use Meshed defaults
            if (var_name == "ORGANIC_TRAFFIC") {
                list(alphas = c(0.5, 2.0), gammas = c(0.3, 0.7), thetas = c(0.9, 0.99))
            } else if (var_name == "TV_COST") {
                list(alphas = c(0.8, 2.2), gammas = c(0.6, 0.99), thetas = c(0.7, 0.95))
            } else if (var_name == "PARTNERSHIP_COSTS") {
                list(alphas = c(0.65, 2.25), gammas = c(0.45, 0.875), thetas = c(0.3, 0.625))
            } else {
                list(alphas = c(1.0, 3.0), gammas = c(0.6, 0.9), thetas = c(0.1, 0.4))
            }
        }
    } else if (adstock_type %in% c("weibull_cdf", "weibull_pdf")) {
        # Weibull adstock ranges (from Robyn documentation)
        # Weibull needs: alphas, gammas (for Hill saturation), shapes, scales (for Weibull adstock)
        if (preset == "Facebook recommend") {
            list(alphas = c(0.5, 3), gammas = c(0.3, 1), shapes = c(0.0001, 2), scales = c(0, 0.1))
        } else if (preset == "Meshed recommend") {
            # Meshed customizations for Weibull
            list(alphas = c(0.5, 3), gammas = c(0.3, 1), shapes = c(0.5, 2.5), scales = c(0.001, 0.15))
        } else {
            # Custom fallback
            list(alphas = c(0.5, 3), gammas = c(0.3, 1), shapes = c(0.5, 2.5), scales = c(0.001, 0.15))
        }
    } else {
        # Fallback
        list(alphas = c(0.5, 3), gammas = c(0.3, 1), thetas = c(0, 0.5))
    }
}

# Log data dimensions before robyn_inputs
message("→ Data ready for robyn_inputs:")
message("   Rows: ", nrow(df))
message("   Columns: ", ncol(df))
message("   Date range: ", min(df$date), " to ", max(df$date))
message("   dep_var: ", dep_var_from_cfg, " (type: ", dep_var_type_from_cfg, ")")
message("   Checking if all driver variables exist in data:")
all_drivers <- unique(c(paid_media_spends, paid_media_vars, context_vars, factor_vars, organic_vars))
missing_drivers <- setdiff(all_drivers, names(df))
if (length(missing_drivers) > 0) {
    message("   ⚠️ WARNING: Missing variables in data: ", paste(missing_drivers, collapse = ", "))
} else {
    message("   ✅ All driver variables found in data")
}

# IMPORTANT: Rebuild hyperparameters based on ACTUAL filtered variables
# This ensures hyperparameters match the variables after zero-variance filtering
message("→ Rebuilding hyperparameters for filtered variables...")

# According to Robyn documentation:
# - Paid media & organic variables need hyperparameters (alphas + gammas/thetas or shapes/scales)
# - Prophet variables DO NOT need hyperparameters (handled separately by Prophet)
# - Context variables typically do NOT need hyperparameters (unless lagged effect)
hyper_vars_filtered <- c(paid_media_vars, organic_vars)
message("   Variables for hyperparameters: ", paste(hyper_vars_filtered, collapse = ", "))
message("   Adstock type: ", adstock)

# Rebuild hyperparameters list for the filtered variables
hyperparameters_filtered <- list()
for (v in hyper_vars_filtered) {
    spec <- get_hyperparameter_ranges(hyperparameter_preset, adstock, v)
    hyperparameters_filtered[[paste0(v, "_alphas")]] <- spec$alphas

    if (adstock == "geometric") {
        hyperparameters_filtered[[paste0(v, "_gammas")]] <- spec$gammas
        hyperparameters_filtered[[paste0(v, "_thetas")]] <- spec$thetas
    } else {
        # Weibull uses alphas, gammas, shapes and scales (4 params per variable)
        hyperparameters_filtered[[paste0(v, "_gammas")]] <- spec$gammas
        hyperparameters_filtered[[paste0(v, "_shapes")]] <- spec$shapes
        hyperparameters_filtered[[paste0(v, "_scales")]] <- spec$scales
    }
}
hyperparameters_filtered[["train_size"]] <- train_size

message("   Rebuilt hyperparameters: ", length(hyperparameters_filtered), " keys")
message(
    "   Expected: ", length(hyper_vars_filtered), " variables × ",
    if (adstock == "geometric") "3" else "4", " params + train_size"
)
message(
    "   Calculation: ", length(hyper_vars_filtered), " vars (",
    length(paid_media_vars), " paid_media + ", length(organic_vars), " organic) × ",
    if (adstock == "geometric") "3 (alphas,gammas,thetas)" else "4 (alphas,gammas,shapes,scales)",
    " + 1 (train_size) = ", length(hyper_vars_filtered) * (if (adstock == "geometric") 3 else 4) + 1
)

# First: call robyn_inputs WITHOUT hyperparameters
message("→ Calling robyn_inputs (preflight, without hyperparameters)...")
InputCollect <- tryCatch(
    {
        robyn_inputs(
            dt_input = df,
            date_var = "date",
            dep_var = dep_var_from_cfg, # From config
            dep_var_type = dep_var_type_from_cfg, # From config
            prophet_vars = c("trend", "season", "holiday", "weekday"),
            prophet_country = toupper(country),
            paid_media_spends = paid_media_spends,
            paid_media_vars = paid_media_vars,
            context_vars = context_vars,
            factor_vars = factor_vars,
            organic_vars = organic_vars,
            window_start = start_data_date,
            window_end = end_data_date,
            adstock = adstock
            # hyperparameters = hyperparameters
        )
    },
    error = function(e) {
        msg <- conditionMessage(e)
        message("❌ robyn_inputs() FAILED: ", msg)
        message("Call: ", paste(deparse(conditionCall(e)), collapse = " "))

        # Write error file
        err_file <- file.path(dir_path, "robyn_inputs_error.txt")
        writeLines(c(
            "robyn_inputs() FAILED",
            paste0("When: ", Sys.time()),
            paste0("Message: ", msg),
            paste0("Call: ", paste(deparse(conditionCall(e)), collapse = " ")),
            paste0("Class: ", paste(class(e), collapse = ", ")),
            "",
            "Stack trace:",
            paste(capture.output(traceback()), collapse = "\n")
        ), err_file)
        gcs_put_safe(err_file, file.path(gcs_prefix, basename(err_file)))

        # Return NULL so we can check it below
        return(NULL)
    }
)

# Preflight IC snapshot (NULL-friendly)
message("Preflight InputCollect is NULL? ", is.null(InputCollect))
log_ic_snapshot_files(InputCollect, dir_path, tag = "preflight")
flush_and_ship_log("after preflight robyn_inputs")

# Preflight is used only for validation - hyperparameters are already built
# using the filtered variables before the preflight
message("Preflight InputCollect is NULL? ", is.null(InputCollect))

# Check if preflight robyn_inputs succeeded - if not, exit early
if (is.null(InputCollect)) {
    err_msg <- "Preflight robyn_inputs() returned NULL"
    message("FATAL: ", err_msg)

    writeLines(
        jsonlite::toJSON(list(
            state = "FAILED",
            step = "preflight_robyn_inputs",
            start_time = as.character(job_started),
            end_time = as.character(Sys.time()),
            error = err_msg
        ), auto_unbox = TRUE, pretty = TRUE),
        status_json
    )
    gcs_put_safe(status_json, file.path(gcs_prefix, "status.json"))
    cleanup()
    quit(status = 1)
}

log_ic_snapshot_files(InputCollect, dir_path, tag = "preflight")
message("paid_media_vars (preflight): ", paste(InputCollect$paid_media_vars, collapse = ", "))
message("organic_vars (preflight): ", paste(InputCollect$organic_vars, collapse = ", "))

# Use the filtered hyperparameters built earlier
hyperparameters <- hyperparameters_filtered

# Hyperparameters were already built earlier using hyperparameters_filtered
# Log them here for reference
message("Pre-built hyperparameters (", hyperparameter_preset, " preset): ", length(hyperparameters), " keys")

log_hyperparameters(hyperparameters, dir_path)
flush_and_ship_log("after hyperparameters build")


## ---------- NOW CALL robyn_inputs WITH hyperparameters ----------
InputCollect <- tryCatch(
    {
        robyn_inputs(
            dt_input = df,
            date_var = "date",
            dep_var = dep_var_from_cfg, # From config
            dep_var_type = dep_var_type_from_cfg, # From config
            prophet_vars = c("trend", "season", "holiday", "weekday"),
            prophet_country = toupper(country),
            paid_media_spends = paid_media_spends,
            paid_media_vars = paid_media_vars,
            context_vars = context_vars,
            factor_vars = factor_vars,
            organic_vars = organic_vars,
            window_start = start_data_date,
            window_end = end_data_date,
            adstock = adstock,
            hyperparameters = hyperparameters # ← PASS IT HERE
        )
    },
    error = function(e) {
        msg <- conditionMessage(e)
        message("❌ robyn_inputs() FAILED: ", msg)
        message("Call: ", paste(deparse(conditionCall(e)), collapse = " "))

        err_file <- file.path(dir_path, "robyn_inputs_error.txt")
        writeLines(c(
            "robyn_inputs() FAILED",
            paste0("When: ", Sys.time()),
            paste0("Message: ", msg),
            paste0("Call: ", paste(deparse(conditionCall(e)), collapse = " ")),
            paste0("Class: ", paste(class(e), collapse = ", ")),
            "",
            "Stack trace:",
            paste(capture.output(traceback()), collapse = "\n")
        ), err_file)
        gcs_put_safe(err_file, file.path(gcs_prefix, basename(err_file)))

        return(NULL)
    }
)
# Already prints a textual snapshot; also persist files to debug/
log_ic_snapshot_files(InputCollect, dir_path, tag = "with_hp")
flush_and_ship_log("after robyn_inputs with hyperparameters")

# Check if robyn_inputs succeeded
if (is.null(InputCollect)) {
    err_msg <- "robyn_inputs() returned NULL"
    message("FATAL: ", err_msg)

    writeLines(
        jsonlite::toJSON(list(
            state = "FAILED",
            step = "robyn_inputs",
            start_time = as.character(job_started),
            end_time = as.character(Sys.time()),
            error = err_msg
        ), auto_unbox = TRUE, pretty = TRUE),
        status_json
    )
    gcs_put_safe(status_json, file.path(gcs_prefix, "status.json"))
    cleanup()
    quit(status = 1)
}

# Verify critical slots exist
critical_slots <- c("dt_input", "dt_mod", "dt_modRollWind", "paid_media_vars", "paid_media_spends", "hyperparameters")
missing_slots <- critical_slots[!sapply(critical_slots, function(s) !is.null(InputCollect[[s]]))]

if (length(missing_slots) > 0) {
    err_msg <- paste("InputCollect missing critical slots:", paste(missing_slots, collapse = ", "))
    message("FATAL: ", err_msg)

    writeLines(
        jsonlite::toJSON(list(
            state = "FAILED",
            step = "robyn_inputs_validation",
            start_time = as.character(job_started),
            end_time = as.character(Sys.time()),
            error = err_msg
        ), auto_unbox = TRUE, pretty = TRUE),
        status_json
    )
    gcs_put_safe(status_json, file.path(gcs_prefix, "status.json"))
    cleanup()
    quit(status = 1)
}

message("✅ robyn_inputs() succeeded")
message("   - dt_input: ", nrow(InputCollect$dt_input), " rows x ", ncol(InputCollect$dt_input), " cols")
message("   - dt_mod: NOT NULL (ready for robyn_run)")
message("   - Date range: ", as.character(min(InputCollect$dt_input$date)), " to ", as.character(max(InputCollect$dt_input$date)))
message("   - Hyperparameters: ", length(InputCollect$hyperparameters), " keys")
# Verify critical slots exist
critical_slots <- c("dt_input", "paid_media_vars", "paid_media_spends", "hyperparameters")
missing_slots <- critical_slots[!sapply(critical_slots, function(s) !is.null(InputCollect[[s]]))]

if (length(missing_slots) > 0) {
    err_msg <- paste("InputCollect missing critical slots:", paste(missing_slots, collapse = ", "))
    message("FATAL: ", err_msg)

    writeLines(
        jsonlite::toJSON(list(
            state = "FAILED",
            step = "robyn_inputs_validation",
            start_time = as.character(job_started),
            end_time = as.character(Sys.time()),
            error = err_msg
        ), auto_unbox = TRUE, pretty = TRUE),
        status_json
    )
    gcs_put_safe(status_json, file.path(gcs_prefix, "status.json"))
    cleanup()
    quit(status = 1)
}

message("✅ robyn_inputs() succeeded")
message("   - dt_input: ", nrow(InputCollect$dt_input), " rows x ", ncol(InputCollect$dt_input), " cols")
message("   - Date range: ", as.character(min(InputCollect$dt_input$date)), " to ", as.character(max(InputCollect$dt_input$date)))
message("   - Hyperparameters: ", length(InputCollect$hyperparameters), " keys")

# No need to attach afterward—it's already populated

## 3) Log a compact snapshot of ALL InputCollect variables
log_InputCollect <- function(ic) {
    cat("\n================= InputCollect snapshot =================\n")
    cat("adstock        :", ic$adstock, "\n")
    cat("dep_var        :", ic$dep_var, "\n")
    cat("dep_var_type   :", ic$dep_var_type, "\n")
    # date window
    if (!is.null(ic$window_start) && !is.null(ic$window_end)) {
        cat("window         :", as.character(ic$window_start), "→", as.character(ic$window_end), "\n")
    } else if (!is.null(ic$dt_input$date)) {
        cat("window         :", as.character(min(ic$dt_input$date)), "→", as.character(max(ic$dt_input$date)), "\n")
    }
    # prophet vars
    if (!is.null(ic$prophet_vars)) cat("prophet_vars   :", paste(ic$prophet_vars, collapse = ", "), "\n")
    if (!is.null(ic$prophet_country)) cat("prophet_country:", ic$prophet_country, "\n")
    # drivers
    if (!is.null(ic$paid_media_spends)) cat("paid_spends    :", paste(ic$paid_media_spends, collapse = ", "), "\n")
    if (!is.null(ic$paid_media_vars)) cat("paid_vars      :", paste(ic$paid_media_vars, collapse = ", "), "\n")
    if (!is.null(ic$organic_vars)) cat("organic_vars   :", paste(ic$organic_vars, collapse = ", "), "\n")
    if (!is.null(ic$context_vars)) cat("context_vars   :", paste(ic$context_vars, collapse = ", "), "\n")
    if (!is.null(ic$factor_vars)) cat("factor_vars    :", paste(ic$factor_vars, collapse = ", "), "\n")

    # dt_input shape & columns
    if (is.data.frame(ic$dt_input)) {
        cat("dt_input       : data.frame[", nrow(ic$dt_input), " x ", ncol(ic$dt_input), "]\n", sep = "")
        # show a concise list of columns
        cn <- names(ic$dt_input)
        cat("dt_input cols  : ", paste(utils::head(cn, 30), collapse = ", "),
            if (length(cn) > 30) sprintf(" ... (+%d)", length(cn) - 30) else "", "\n",
            sep = ""
        )
    }

    # hyperparameters summary
    cat("\n-- hyperparameters --\n")
    if (!is.null(ic$hyperparameters) && length(ic$hyperparameters)) {
        # print train_size first if present
        if (!is.null(ic$hyperparameters$train_size)) {
            cat("train_size     : [", paste(ic$hyperparameters$train_size, collapse = ", "), "]\n", sep = "")
        }
        # then each channel’s alpha/gamma/theta
        keys <- setdiff(names(ic$hyperparameters), "train_size")
        for (k in sort(keys)) {
            v <- ic$hyperparameters[[k]]
            sh <- paste0("[", paste(v, collapse = ", "), "]")
            cat(sprintf("%-16s: %s\n", k, sh))
        }
    } else {
        cat("<none>\n")
    }

    # dump all top-level keys with class & size (for completeness)
    cat("\n-- all fields --\n")
    for (nm in names(ic)) {
        v <- ic[[nm]]
        shape <- if (is.null(v)) {
            "NULL"
        } else if (is.data.frame(v)) {
            sprintf("data.frame[%d x %d]", nrow(v), ncol(v))
        } else if (is.list(v)) {
            sprintf("list(len=%d)", length(v))
        } else if (is.atomic(v)) {
            sprintf("%s(len=%d)", class(v)[1], length(v))
        } else {
            class(v)[1]
        }
        cat(sprintf("  - %-18s %s\n", nm, shape))
    }
    cat("=========================================================\n\n")
}
log_InputCollect(InputCollect)

# --- Sanity guards so robyn_run never starts with a bad InputCollect ---
if (is.null(InputCollect) || !is.list(InputCollect)) {
    stop("robyn_inputs() returned NULL/invalid InputCollect.", call. = FALSE)
}
if (is.null(InputCollect$hyperparameters)) {
    # check missing keys to give a crisp message
    req <- as.vector(outer(
        c(paid_media_vars, organic_vars),
        c("_alphas", "_gammas", "_thetas"), paste0
    ))
    miss <- setdiff(req, names(hyperparameters))
    extra <- setdiff(names(hyperparameters), c(req, "train_size"))
    msg <- c(
        "InputCollect$hyperparameters is NULL after the attach step.",
        if (length(miss)) paste("Missing keys:", paste(miss, collapse = ", ")),
        if (length(extra)) paste("Unexpected keys:", paste(extra, collapse = ", "))
    )
    stop(paste(msg, collapse = "\n"), call. = FALSE)
}
# quick shape check
bad_len <- names(InputCollect$hyperparameters)[
    vapply(InputCollect$hyperparameters, length, integer(1)) == 0
]
if (length(bad_len)) {
    stop("Zero-length HP entries: ", paste(bad_len, collapse = ", "), call. = FALSE)
}

alloc_end <- max(InputCollect$dt_input$date)
alloc_start <- alloc_end - 364



## ---------- TRAIN (exact error capture; hard stop on failure) ----------
cat(sprintf("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"))
cat(sprintf("🚀 ROBYN TRAINING PREPARATION\n"))
cat(sprintf("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"))

cat(sprintf("📊 Training Parameters:\n"))
cat(sprintf("  - Iterations:      %d\n", iter))
cat(sprintf("  - Trials:          %d\n", trials))
cat(sprintf("  - Cores requested: %d\n", max_cores))
cat(sprintf("  - Time validation: TRUE\n"))
cat(sprintf("  - Penalty factor:  TRUE\n\n"))

cat(sprintf("💻 System Information:\n"))
cat(sprintf("  - R version:           %s\n", R.version$version.string))
cat(sprintf("  - Platform:            %s\n", R.version$platform))
cat(sprintf("  - OS:                  %s\n", Sys.info()["sysname"]))
cat(sprintf(
    "  - Available memory:    %s\n",
    if (file.exists("/sys/fs/cgroup/memory/memory.limit_in_bytes")) {
        paste0(round(as.numeric(readLines("/sys/fs/cgroup/memory/memory.limit_in_bytes")[1]) / 1024^3, 1), " GB")
    } else {
        "Unknown"
    }
))
cat(sprintf("  - CPU cores (system):  %d\n", parallel::detectCores()))
cat(sprintf("  - CPU cores (actual):  %d\n", parallelly::availableCores()))
cat(sprintf("  - Cores for training:  %d (safe buffer applied)\n", max_cores))
cat(sprintf(
    "  - Future plan:         %s with %d workers\n\n",
    class(future::plan())[1], future::nbrOfWorkers()
))

cat(sprintf("📁 Data Dimensions:\n"))
cat(sprintf("  - Rows:    %d\n", nrow(InputCollect$dt_input)))
cat(sprintf("  - Columns: %d\n", ncol(InputCollect$dt_input)))
cat(sprintf(
    "  - Date range: %s to %s\n",
    min(InputCollect$dt_input$date),
    max(InputCollect$dt_input$date)
))
cat(sprintf(
    "  - Days:    %d\n\n",
    as.numeric(difftime(max(InputCollect$dt_input$date),
        min(InputCollect$dt_input$date),
        units = "days"
    ))
))

cat(sprintf("🎯 Model Configuration:\n"))
cat(sprintf("  - Dependent variable: %s\n", InputCollect$dep_var))
cat(sprintf("  - Paid media spends:  %d variables\n", length(InputCollect$paid_media_spends)))
cat(sprintf("  - Paid media vars:    %d variables\n", length(InputCollect$paid_media_vars)))
cat(sprintf(
    "  - Context vars:       %d variables\n",
    if (!is.null(InputCollect$context_vars)) length(InputCollect$context_vars) else 0
))
cat(sprintf(
    "  - Organic vars:       %d variables\n",
    if (!is.null(InputCollect$organic_vars)) length(InputCollect$organic_vars) else 0
))
cat(sprintf(
    "  - Factor vars:        %d variables\n\n",
    if (!is.null(InputCollect$factor_vars)) length(InputCollect$factor_vars) else 0
))

cat(sprintf("⚙️  Hyperparameters:\n"))
if (!is.null(InputCollect$hyperparameters)) {
    for (hp_name in names(InputCollect$hyperparameters)) {
        hp_val <- InputCollect$hyperparameters[[hp_name]]
        if (length(hp_val) <= 3) {
            cat(sprintf("  - %-20s: %s\n", hp_name, paste(hp_val, collapse = ", ")))
        } else {
            cat(sprintf("  - %-20s: %d values\n", hp_name, length(hp_val)))
        }
    }
}
cat(sprintf("\n"))

cat(sprintf("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"))
cat(sprintf("🎬 Starting robyn_run() with %d cores...\n", max_cores))
cat(sprintf("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"))

message("→ Starting Robyn training with ", max_cores, " cores on Cloud Run Jobs...")
t0 <- Sys.time()

robyn_err_txt <- file.path(dir_path, "robyn_run_error.txt")
robyn_err_json <- file.path(dir_path, "robyn_run_error.json")
.format_calls <- function(cs) vapply(cs, function(z) paste0(deparse(z, nlines = 3L), collapse = " "), character(1))

flush_and_ship_log("before robyn_run")
OutputModels <- tryCatch(
    withCallingHandlers(
        robyn_run(
            InputCollect = InputCollect,
            iterations = iter,
            trials = trials,
            ts_validation = TRUE,
            add_penalty_factor = TRUE,
            cores = max_cores
        ),
        warning = function(w) {
            message("⚠️ [robyn_run warning] ", conditionMessage(w))
            invokeRestart("muffleWarning")
        }
    ),
    error = function(e) {
        calls_chr <- .format_calls(sys.calls())
        elapsed <- as.numeric(difftime(Sys.time(), t0, units = "secs"))

        # Enhanced error information with core detection details
        error_details <- c(
            "robyn_run() FAILED",
            paste0("When     : ", as.character(Sys.time())),
            paste0("Elapsed  : ", round(elapsed, 2), " sec"),
            paste0("Message  : ", conditionMessage(e)),
            paste0("Call     : ", paste(deparse(conditionCall(e)), collapse = " ")),
            paste0("Class    : ", paste(class(e), collapse = ", ")),
            "",
            "--- Core Detection Information ---",
            paste0("Requested cores (R_MAX_CORES): ", requested_cores),
            paste0("Available (parallelly):        ", available_cores_parallelly),
            paste0("Available (parallel):          ", available_cores_parallel),
            paste0("Conservative estimate:         ", available_cores),
            paste0("Cores passed to robyn_run:     ", max_cores),
            paste0("Future workers:                ", future::nbrOfWorkers()),
            paste0("System CPU count:              ", parallel::detectCores()),
            "",
            "--- Approximate R call stack (inner→outer) ---",
            paste(rev(calls_chr), collapse = "\n")
        )

        writeLines(error_details, robyn_err_txt)

        err_payload <- list(
            state = "FAILED", step = "robyn_run",
            timestamp = as.character(Sys.time()),
            training_started_at = as.character(t0),
            elapsed_seconds = elapsed,
            message = conditionMessage(e),
            call = paste(deparse(conditionCall(e)), collapse = " "),
            class = unname(class(e)),
            stack_inner_to_outer = as.list(calls_chr),
            params = list(
                iterations = iter,
                trials = trials,
                cores = max_cores
            ),
            core_detection = list(
                requested_cores = requested_cores,
                available_parallelly = available_cores_parallelly,
                available_parallel = available_cores_parallel,
                conservative_estimate = available_cores,
                used_cores = max_cores,
                future_workers = future::nbrOfWorkers(),
                system_cpu_count = parallel::detectCores()
            )
        )
        writeLines(jsonlite::toJSON(err_payload, auto_unbox = TRUE, pretty = TRUE), robyn_err_json)
        gcs_put_safe(robyn_err_txt, file.path(gcs_prefix, basename(robyn_err_txt)))
        gcs_put_safe(robyn_err_json, file.path(gcs_prefix, basename(robyn_err_json)))
        # reflect failure in status.json
        try(
            {
                writeLines(jsonlite::toJSON(list(
                    state = "FAILED",
                    start_time = as.character(job_started),
                    end_time = as.character(Sys.time()),
                    failed_step = "robyn_run",
                    error_message = conditionMessage(e)
                ), auto_unbox = TRUE, pretty = TRUE), status_json)
                gcs_put_safe(status_json, file.path(gcs_prefix, "status.json"))
            },
            silent = TRUE
        )

        # HARD stop (avoid “wrapup: argument 'e' missing” & avoid continuing)
        stop(conditionMessage(e), call. = FALSE)
    }
)

flush_and_ship_log("after robyn_run")

# Check if robyn_run() returned NULL (silent failure)
if (is.null(OutputModels)) {
    elapsed <- as.numeric(difftime(Sys.time(), t0, units = "secs"))
    error_msg <- "robyn_run() returned NULL - no models were generated. This typically indicates a silent failure during training."

    error_details <- c(
        "robyn_run() RETURNED NULL",
        paste0("When     : ", as.character(Sys.time())),
        paste0("Elapsed  : ", round(elapsed, 2), " sec"),
        paste0("Message  : ", error_msg),
        "",
        "Possible causes:",
        "1. Data validation failed silently",
        "2. All hyperparameter trials failed",
        "3. Insufficient memory during training",
        "4. Optimization algorithm failed to converge",
        "5. Internal Robyn error was suppressed",
        "",
        "--- Core Detection Information ---",
        paste0("Requested cores (R_MAX_CORES): ", requested_cores),
        paste0("Available (parallelly):        ", available_cores_parallelly),
        paste0("Available (parallel):          ", available_cores_parallel),
        paste0("Conservative estimate:         ", available_cores),
        paste0("Cores passed to robyn_run:     ", max_cores),
        paste0("Future workers:                ", future::nbrOfWorkers()),
        paste0("System CPU count:              ", parallel::detectCores())
    )

    writeLines(error_details, robyn_err_txt)

    err_payload <- list(
        state = "FAILED", step = "robyn_run",
        timestamp = as.character(Sys.time()),
        training_started_at = as.character(t0),
        elapsed_seconds = elapsed,
        message = error_msg,
        return_value = "NULL",
        class = "NULL_RETURN",
        params = list(
            iterations = iter,
            trials = trials,
            cores = max_cores
        ),
        core_detection = list(
            requested_cores = requested_cores,
            available_parallelly = available_cores_parallelly,
            available_parallel = available_cores_parallel,
            conservative_estimate = available_cores,
            used_cores = max_cores,
            future_workers = future::nbrOfWorkers(),
            system_cpu_count = parallel::detectCores()
        )
    )

    writeLines(jsonlite::toJSON(err_payload, auto_unbox = TRUE, pretty = TRUE), robyn_err_json)
    gcs_put_safe(robyn_err_txt, file.path(gcs_prefix, basename(robyn_err_txt)))
    gcs_put_safe(robyn_err_json, file.path(gcs_prefix, basename(robyn_err_json)))

    # Update status.json
    try(
        {
            writeLines(jsonlite::toJSON(list(
                state = "FAILED",
                start_time = as.character(job_started),
                end_time = as.character(Sys.time()),
                failed_step = "robyn_run",
                error_message = error_msg
            ), auto_unbox = TRUE, pretty = TRUE), status_json)
            gcs_put_safe(status_json, file.path(gcs_prefix, "status.json"))
        },
        silent = TRUE
    )

    stop(error_msg, call. = FALSE)
}

training_time <- as.numeric(difftime(Sys.time(), t0, units = "mins"))
message("✅ Training completed in ", round(training_time, 2), " minutes")
message("✅ OutputModels object created with class: ", class(OutputModels)[1])

## ---------- APPEND R TRAINING TIME TO timings.csv --

ensure_gcs_auth() # <--- add this line

## ---------- APPEND R TRAINING TIME TO timings.csv ----------
timings_obj <- file.path(gcs_prefix, "timings.csv")
timings_local <- file.path(tempdir(), "timings.csv")
message("Appending training time to: gs://", googleCloudStorageR::gcs_get_global_bucket(), "/", timings_obj)

r_row <- data.frame(
    Step = "R training (robyn_run)",
    `Time (s)` = round(training_time * 60, 2),
    check.names = FALSE
)

# Small retry loop in case timings.csv hasn’t been uploaded yet
had_existing <- FALSE
for (i in 1:5) {
    ok <- tryCatch(
        {
            googleCloudStorageR::gcs_get_object(
                object_name = timings_obj,
                bucket = googleCloudStorageR::gcs_get_global_bucket(),
                saveToDisk = timings_local,
                overwrite = TRUE
            )
            TRUE
        },
        error = function(e) FALSE
    )
    if (ok && file.exists(timings_local)) {
        had_existing <- TRUE
        break
    }
    Sys.sleep(2) # wait a bit then try again
}

if (had_existing) {
    old <- try(readr::read_csv(timings_local, show_col_types = FALSE), silent = TRUE)
    if (inherits(old, "try-error")) {
        out <- r_row
    } else {
        if ("Step" %in% names(old)) old <- dplyr::filter(old, Step != "R training (robyn_run)")
        out <- dplyr::bind_rows(old, r_row)
    }
} else {
    out <- r_row
}

readr::write_csv(out, timings_local, na = "")
gcs_put_safe(timings_local, timings_obj)

# Verify for the logs (best-effort)
try(
    {
        ver_local <- tempfile(fileext = ".csv")
        googleCloudStorageR::gcs_get_object(
            object_name = timings_obj,
            bucket = googleCloudStorageR::gcs_get_global_bucket(),
            saveToDisk = ver_local,
            overwrite = TRUE
        )
        ver <- readr::read_csv(ver_local, show_col_types = FALSE)
        message(
            "timings.csv now has ", nrow(ver), " rows: ",
            paste(ver$Step, collapse = " | ")
        )
    },
    silent = TRUE
)


readr::write_csv(out, timings_local)
gcs_put_safe(timings_local, timings_obj)

saveRDS(OutputModels, file.path(dir_path, "OutputModels.RDS"))
saveRDS(InputCollect, file.path(dir_path, "InputCollect.RDS"))
gcs_put_safe(file.path(dir_path, "OutputModels.RDS"), file.path(gcs_prefix, "OutputModels.RDS"))
gcs_put_safe(file.path(dir_path, "InputCollect.RDS"), file.path(gcs_prefix, "InputCollect.RDS"))

## ---------- OUTPUTS & ONEPAGERS ----------
flush_and_ship_log("before robyn_outputs")
OutputCollect <- tryCatch(
    {
        robyn_outputs(
            InputCollect, OutputModels,
            pareto_fronts = 2, csv_out = "pareto",
            min_candidates = 5, clusters = FALSE,
            export = TRUE, plot_folder = dir_path,
            plot_pareto = FALSE, cores = NULL
        )
    },
    error = function(e) {
        msg <- conditionMessage(e)
        message("❌ robyn_outputs() FAILED: ", msg)

        # Write error file
        err_file <- file.path(dir_path, "robyn_outputs_error.txt")
        writeLines(c(
            "robyn_outputs() FAILED",
            paste0("When: ", Sys.time()),
            paste0("Message: ", msg),
            "",
            "Stack trace:",
            paste(capture.output(traceback()), collapse = "\n")
        ), err_file)
        gcs_put_safe(err_file, file.path(gcs_prefix, basename(err_file)))

        return(NULL)
    }
)
flush_and_ship_log("after robyn_outputs")

# Check if robyn_outputs succeeded
if (is.null(OutputCollect)) {
    err_msg <- "robyn_outputs() returned NULL or failed"
    message("FATAL: ", err_msg)

    writeLines(
        jsonlite::toJSON(list(
            state = "FAILED",
            step = "robyn_outputs",
            start_time = as.character(job_started),
            end_time = as.character(Sys.time()),
            error = err_msg
        ), auto_unbox = TRUE, pretty = TRUE),
        status_json
    )
    gcs_put_safe(status_json, file.path(gcs_prefix, "status.json"))
    cleanup()
    quit(status = 1)
}

# DEBUG: Log what variables are in xDecompAgg to diagnose organic_vars issue
if (!is.null(OutputCollect$xDecompAgg)) {
    all_vars_in_decomp <- unique(OutputCollect$xDecompAgg$rn)
    message("📊 Variables in xDecompAgg (", length(all_vars_in_decomp), " total):")
    message("   ", paste(all_vars_in_decomp, collapse = ", "))

    # Check specifically for organic_vars
    organic_in_decomp <- intersect(organic_vars, all_vars_in_decomp)
    organic_missing <- setdiff(organic_vars, all_vars_in_decomp)

    if (length(organic_in_decomp) > 0) {
        message("✅ Organic vars found in decomposition: ", paste(organic_in_decomp, collapse = ", "))
    }
    if (length(organic_missing) > 0) {
        message("⚠️  Organic vars MISSING from decomposition: ", paste(organic_missing, collapse = ", "))
        message("   This means these variables were either:")
        message("   1. Filtered out due to zero variance")
        message("   2. Not included in the model by Robyn")
        message("   3. Had zero coefficients and were dropped")
    }
}

saveRDS(OutputCollect, file.path(dir_path, "OutputCollect.RDS"))
gcs_put_safe(file.path(dir_path, "OutputCollect.RDS"), file.path(gcs_prefix, "OutputCollect.RDS"))

## ---------- EXTRACT PARQUET DATA FROM OUTPUTCOLLECT ----------
message("→ Extracting compressed data from OutputCollect.RDS to parquet files...")
output_models_data_dir <- file.path(dir_path, "output_models_data")
tryCatch(
    {
        # Source the extraction helper - try multiple locations
        extract_script <- NULL
        candidates <- c(
            "/app/extract_output_models_data.R", # Docker container location
            "r/extract_output_models_data.R" # Local development location
        )

        for (candidate in candidates) {
            if (file.exists(candidate)) {
                extract_script <- candidate
                break
            }
        }

        if (!is.null(extract_script)) {
            source(extract_script)

            # Extract parquet data from OutputCollect
            created_files <- extract_output_models_data(
                oc_path = file.path(dir_path, "OutputCollect.RDS"),
                out_dir = output_models_data_dir
            )

            # Upload parquet files to GCS
            for (pq_file in created_files) {
                # Get relative path from dir_path
                rel_path <- gsub(paste0("^", dir_path, "/?"), "", pq_file)
                gcs_put_safe(pq_file, file.path(gcs_prefix, rel_path))
            }

            message("✅ OutputCollect data extraction complete, uploaded ", length(created_files), " parquet files")
        } else {
            message("⚠️ Could not find extract_output_models_data.R, skipping parquet extraction")
        }
    },
    error = function(e) {
        message("⚠️ Failed to extract OutputCollect data to parquet: ", conditionMessage(e))
        # Non-fatal: continue with execution
    }
)

## ---------- GENERATE MODEL SUMMARY ----------
message("→ Generating model summary...")
# Source the helper script - try multiple locations
extract_summary_script <- NULL

# 1. Try same directory as this script
tryCatch(
    {
        candidate <- file.path(
            dirname(normalizePath(sys.frame(1)$ofile, mustWork = FALSE)),
            "extract_model_summary.R"
        )
        if (file.exists(candidate)) {
            extract_summary_script <- candidate
        }
    },
    error = function(e) {
        # sys.frame(1)$ofile not available in this context
        # Will try other locations instead
    }
)

# 2. Try /app directory (Docker container location)
if (is.null(extract_summary_script)) {
    candidate <- "/app/extract_model_summary.R"
    if (file.exists(candidate)) {
        extract_summary_script <- candidate
    }
}

# 3. Try r/ subdirectory (local development)
if (is.null(extract_summary_script)) {
    candidate <- "r/extract_model_summary.R"
    if (file.exists(candidate)) {
        extract_summary_script <- candidate
    }
}

if (!is.null(extract_summary_script)) {
    message("   Using extract_model_summary.R from: ", extract_summary_script)
    source(extract_summary_script)
} else {
    message(
        "⚠️ Could not find extract_model_summary.R, ",
        "skipping summary generation"
    )
}

if (exists("extract_model_summary")) {
    tryCatch(
        {
            model_summary <- extract_model_summary(
                output_collect = OutputCollect,
                input_collect = InputCollect,
                country = country,
                revision = revision,
                timestamp = timestamp,
                training_time_mins = round(training_time, 2)
            )
            summary_path <- file.path(dir_path, "model_summary.json")
            save_model_summary(model_summary, summary_path)
            gcs_put_safe(
                summary_path,
                file.path(gcs_prefix, "model_summary.json")
            )
            message("✅ Model summary generated and uploaded")
        },
        error = function(e) {
            message(
                "⚠️ Failed to generate model summary: ",
                conditionMessage(e)
            )
            # Non-fatal: continue with execution
        }
    )
}

best_id <- OutputCollect$resultHypParam$solID[1]
writeLines(
    c(
        best_id,
        paste("Iterations:", iter),
        paste("Trials:", trials),
        paste("Training time (mins):", round(training_time, 2))
    ),
    con = file.path(dir_path, "best_model_id.txt")
)
gcs_put_safe(file.path(dir_path, "best_model_id.txt"), file.path(gcs_prefix, "best_model_id.txt"))

flush_and_ship_log("before onepagers")
# onepagers for top models
# baseline_level = 0: Shows ALL variables as individual bars in waterfall chart
# including intercept, trend, Prophet vars (seasonality/holiday), context_vars, and organic_vars
# (no aggregation into baseline component)
message("🎨 Generating onepagers with baseline_level = 0")
message("   Expected organic_vars to appear: ", paste(organic_vars, collapse = ", "))
top_models <- OutputCollect$resultHypParam$solID[
    1:min(3, nrow(OutputCollect$resultHypParam))
]
for (m in top_models) {
    message("   Generating onepager for model: ", m)
    tryCatch(
        robyn_onepagers(
            InputCollect,
            OutputCollect,
            select_model = m,
            plot_folder = dir_path,
            export = TRUE,
            baseline_level = 0
        ),
        error = function(e) {
            write_trace("Allocator error", e)
            NULL
        }
    )
    message("Files in dir_path: ", paste(list.files(dir_path, pattern = "onepager|plot", recursive = TRUE), collapse = ", "))
}

# onepagers for top models
top_models <- OutputCollect$resultHypParam$solID[
    1:min(3, nrow(OutputCollect$resultHypParam))
]
for (m in top_models) {
    try(
        robyn_onepagers(
            InputCollect,
            OutputCollect,
            select_model = m,
            export = TRUE,
            baseline_level = 0
        ),
        silent = TRUE
    )
}
flush_and_ship_log("after onepagers")
# Onepagers: try PNG first, then PDF (restored fallback)
all_files <- list.files(dir_path, recursive = TRUE, full.names = TRUE)
escaped_id <- gsub("\\.", "\\\\.", best_id)
png_pat <- paste0("(?i)(onepager).*", escaped_id, ".*\\.png$")
pdf_pat <- paste0("(?i)(onepager).*", escaped_id, ".*\\.pdf$")

cand_png <- all_files[grepl(png_pat, all_files, perl = TRUE)]
cand_pdf <- all_files[grepl(pdf_pat, all_files, perl = TRUE)]

if (length(cand_png)) {
    canonical <- file.path(dir_path, paste0(best_id, ".png"))
    file.copy(cand_png[1], canonical, overwrite = TRUE)
    gcs_put_safe(canonical, file.path(gcs_prefix, paste0(best_id, ".png")))
} else if (length(cand_pdf)) {
    canonical <- file.path(dir_path, paste0(best_id, ".pdf"))
    file.copy(cand_pdf[1], canonical, overwrite = TRUE)
    gcs_put_safe(canonical, file.path(gcs_prefix, paste0(best_id, ".pdf")))
} else {
    pdf_pat <- paste0("(?i)(onepager).*", escaped_id, ".*\\.pdf$")
    cand_pdf <- all_files[
        grepl(pdf_pat, all_files, perl = TRUE)
    ]
    if (!length(cand_pdf)) {
        cand_pdf <- all_files[basename(all_files) == paste0(best_id, ".pdf")]
    }
    if (length(cand_pdf)) {
        canonical <- file.path(dir_path, paste0(best_id, ".pdf"))
        file.copy(cand_pdf[1], canonical, overwrite = TRUE)
        gcs_put_safe(
            canonical,
            file.path(gcs_prefix, paste0(best_id, ".pdf"))
        )
    } else {
        message("No onepager image/pdf found for best_id=", best_id)
    }
}


## ---------- ALLOCATOR ----------

flush_and_ship_log("before robyn_allocator")
alloc_end <- max(InputCollect$dt_input$date)
alloc_start <- alloc_end - 364

# Read budget parameters from config
budget_scenario_cfg <- cfg$budget_scenario %||% "max_historical_response"
expected_spend_cfg <- cfg$expected_spend %||% NULL
# Explicitly convert expected_spend to numeric to avoid scientific notation issues
if (!is.null(expected_spend_cfg)) {
    expected_spend_cfg <- as.numeric(expected_spend_cfg)
}
channel_budgets_cfg <- cfg$channel_budgets %||% list()

# Map UI scenario values to Robyn allocator scenario values
# UI uses: "max_historical_response" or "max_response_expected_spend"
# Robyn accepts: "max_response" or "target_efficiency"
# For both UI scenarios, we use "max_response" in Robyn
# The difference is whether expected_spend is NULL (historical) or a value (custom)
robyn_scenario <- "max_response"

cat("\n========== BUDGET CONFIGURATION ==========\n")
cat(paste0("UI scenario: ", budget_scenario_cfg, "\n"))
cat(paste0("Robyn scenario: ", robyn_scenario, "\n"))
cat(paste0("expected_spend: ", if (is.null(expected_spend_cfg)) "NULL (use historical)" else format(expected_spend_cfg, scientific = FALSE, big.mark = ","), "\n"))
if (length(channel_budgets_cfg) > 0) {
    cat(paste0("channel_budgets: ", paste(names(channel_budgets_cfg), "=", unlist(channel_budgets_cfg), collapse = ", "), "\n"))
}

# Set up channel constraints
# Default: NULL (let Robyn use its internal defaults)
low_bounds <- NULL
up_bounds <- NULL

# If per-channel budgets are specified, adjust constraints to enforce them
# Note: This block only runs if channel_budgets is NOT empty
if (length(channel_budgets_cfg) > 0 && !is.null(expected_spend_cfg)) {
    # Mode 3: Custom budget WITH per-channel constraints
    cat("\n💰 MODE 3: Custom Budget WITH Per-Channel Constraints\n")
    cat(sprintf("  Total budget (expected_spend): %s\n", expected_spend_cfg))

    # Calculate historical spend for the allocator date range
    # Filter data to the allocator window
    alloc_data <- InputCollect$dt_input[InputCollect$dt_input$date >= alloc_start &
        InputCollect$dt_input$date <= alloc_end, ]

    # Calculate historical total spend across all paid media channels
    historical_spends <- sapply(InputCollect$paid_media_spends, function(ch) {
        sum(alloc_data[[ch]], na.rm = TRUE)
    })
    historical_total <- sum(historical_spends)

    cat(sprintf("  Historical total spend (in date range): %.2f\n", historical_total))

    # Calculate total of specified channel budgets
    total_channel_budgets <- sum(sapply(channel_budgets_cfg, as.numeric))
    cat(sprintf("  Sum of channel budgets: %s\n", total_channel_budgets))

    # Warn if channel budgets don't sum to expected_spend
    if (abs(total_channel_budgets - expected_spend_cfg) > 0.01 * expected_spend_cfg) {
        cat(sprintf(
            "  ⚠️  WARNING: Sum of channel budgets (%.0f) differs from expected_spend (%.0f) by %.1f%%\n",
            total_channel_budgets, expected_spend_cfg,
            100 * abs(total_channel_budgets - expected_spend_cfg) / expected_spend_cfg
        ))
    }

    # Normalize channel budgets to sum to expected_spend
    normalization_factor <- expected_spend_cfg / total_channel_budgets
    cat(sprintf("  Normalization factor: %.6f (to make budgets sum to expected_spend)\n", normalization_factor))

    # Initialize bounds as multipliers
    # Robyn's channel_constr_low/up are MULTIPLIERS of historical spend, not proportions
    low_bounds <- rep(0, length(InputCollect$paid_media_spends))
    up_bounds <- rep(0, length(InputCollect$paid_media_spends))

    cat("\n  Channel-specific constraints (as multipliers of historical spend):\n")

    # For each channel with a specified budget, calculate multiplier bounds
    for (channel_name in names(channel_budgets_cfg)) {
        # Find the index of this channel in paid_media_spends
        channel_idx <- which(InputCollect$paid_media_spends == channel_name)

        if (length(channel_idx) > 0) {
            channel_budget <- as.numeric(channel_budgets_cfg[[channel_name]])

            # Normalize the budget so all budgets sum to expected_spend
            normalized_budget <- channel_budget * normalization_factor

            # Get historical spend for this channel
            channel_historical <- historical_spends[channel_idx]

            # Calculate the multiplier: desired_spend / historical_spend
            # This tells Robyn how much to scale this channel relative to history
            if (channel_historical > 0) {
                target_multiplier <- normalized_budget / channel_historical

                # Set tight bounds around the target multiplier
                # Use 5% tolerance to allow some optimization flexibility
                tolerance <- 0.05
                low_bounds[channel_idx] <- max(0, target_multiplier * (1 - tolerance))
                up_bounds[channel_idx] <- target_multiplier * (1 + tolerance)

                cat(sprintf(
                    "    %s: budget=%.0f, historical=%.0f, multiplier=%.3f, bounds=[%.3f, %.3f]\n",
                    channel_name, normalized_budget, channel_historical, target_multiplier,
                    low_bounds[channel_idx], up_bounds[channel_idx]
                ))
            } else {
                cat(sprintf(
                    "    ⚠️  %s: budget=%.0f but historical spend = 0, setting bounds to [0, 0]\n",
                    channel_name, normalized_budget
                ))
                low_bounds[channel_idx] <- 0
                up_bounds[channel_idx] <- 0
            }
        } else {
            cat(sprintf("    ⚠️  WARNING: Channel '%s' in channel_budgets not found in paid_media_spends\n", channel_name))
        }
    }

    # Verify that applying these multipliers would give us approximately the expected total
    projected_total <- sum(historical_spends * up_bounds)
    cat(sprintf("\n  Validation: Projected total spend = %.2f (target: %.2f)\n", projected_total, expected_spend_cfg))

    if (abs(projected_total - expected_spend_cfg) > 0.1 * expected_spend_cfg) {
        cat("  ⚠️  WARNING: Projected total differs from expected_spend by more than 10%!\n")
        cat("  ⚠️  This suggests the multiplier approach may not perfectly enforce the budget.\n")
    }
} else if (!is.null(expected_spend_cfg) && length(channel_budgets_cfg) == 0) {
    # Mode 2: Custom total budget WITHOUT per-channel constraints
    cat("\n💰 MODE 2: Custom Total Budget WITHOUT Per-Channel Constraints\n")
    cat(sprintf("  Total budget (expected_spend): %s\n", format(expected_spend_cfg, scientific = FALSE, big.mark = ",")))

    # Calculate historical spend for comparison
    alloc_data <- InputCollect$dt_input[InputCollect$dt_input$date >= alloc_start &
        InputCollect$dt_input$date <= alloc_end, ]
    historical_spends <- sapply(InputCollect$paid_media_spends, function(ch) {
        sum(alloc_data[[ch]], na.rm = TRUE)
    })
    historical_total <- sum(historical_spends)

    cat(sprintf("  Historical total spend (in date range): %.2f\n", historical_total))

    # If custom budget is significantly lower than historical spend,
    # we need to set permissive channel constraints to avoid conflicts
    budget_ratio <- expected_spend_cfg / historical_total

    if (budget_ratio < 0.9) {
        # Custom budget is lower than historical - allow channels to decrease significantly
        # Set lower bound to 0.01 (1% of historical) to allow major reductions
        # Set upper bound based on budget ratio to ensure total budget is feasible
        low_bounds <- rep(0.01, length(InputCollect$paid_media_spends))
        up_bounds <- rep(min(budget_ratio * 2, 2.0), length(InputCollect$paid_media_spends))
        cat(sprintf("  ⚠️  Custom budget (%.0f) is %.1f%% of historical spend\n", expected_spend_cfg, budget_ratio * 100))
        cat(sprintf(
            "  Setting permissive channel constraints: [%.2f, %.2f] to make budget feasible\n",
            low_bounds[1], up_bounds[1]
        ))
        cat("  Note: Channels can be reduced to 1% of historical to fit within total budget\n")
    } else {
        # Custom budget is close to or higher than historical - use default flexibility
        cat("  Channel constraints: NULL (using Robyn defaults for flexibility)\n")
        cat("  Note: Allocator will optimize channel mix to maximize response within the total budget\n")
    }
} else {
    # Mode 1: Historical budget (default)
    cat("\n💰 MODE 1: Historical Budget (default)\n")
    cat("  expected_spend: NULL (using historical spend patterns)\n")
    cat("  Channel constraints: NULL (using Robyn defaults)\n")
}
cat("==========================================\n\n")

# Log the actual values being passed to robyn_allocator
cat("📊 Calling robyn_allocator with:\n")
cat(sprintf("  scenario: %s\n", robyn_scenario))
cat(sprintf("  total_budget: %s\n", if (is.null(expected_spend_cfg)) "NULL" else format(expected_spend_cfg, scientific = FALSE, big.mark = ",")))
cat(sprintf("  channel_constr_low: %s\n", if (is.null(low_bounds)) "NULL" else paste0("[", paste(sprintf("%.3f", low_bounds), collapse = ", "), "]")))
cat(sprintf("  channel_constr_up: %s\n", if (is.null(up_bounds)) "NULL" else paste0("[", paste(sprintf("%.3f", up_bounds), collapse = ", "), "]")))
cat(sprintf("  date_range: %s to %s\n\n", alloc_start, alloc_end))

AllocatorCollect <- try(
    robyn_allocator(
        InputCollect = InputCollect, OutputCollect = OutputCollect,
        select_model = best_id, date_range = c(alloc_start, alloc_end),
        total_budget = expected_spend_cfg, scenario = robyn_scenario,
        channel_constr_low = low_bounds, channel_constr_up = up_bounds,
        export = TRUE
    ),
    silent = TRUE
)
flush_and_ship_log("after robyn_allocator")

# Log allocator error if it failed
if (inherits(AllocatorCollect, "try-error")) {
    err_msg <- conditionMessage(attr(AllocatorCollect, "condition"))
    cat(paste0("\n❌ robyn_allocator FAILED with error: ", err_msg, "\n\n"))

    # Write error to file for debugging
    alloc_err_file <- file.path(dir_path, "allocator_error.txt")
    writeLines(c(
        "ALLOCATOR ERROR",
        paste0("When: ", Sys.time()),
        paste0("Error: ", err_msg),
        paste0("UI budget_scenario: ", budget_scenario_cfg),
        paste0("Robyn scenario: ", robyn_scenario),
        paste0("total_budget: ", if (is.null(expected_spend_cfg)) "NULL" else expected_spend_cfg),
        paste0("date_range: ", alloc_start, " to ", alloc_end),
        "",
        "Stack trace:",
        paste(capture.output(traceback()), collapse = "\n")
    ), alloc_err_file)
    try(gcs_put_safe(alloc_err_file, file.path(gcs_prefix, "allocator_error.txt")), silent = TRUE)
}

## ---------- METRICS + PLOT ----------
message("→ Extracting metrics from best model: ", best_id)
best_row <- OutputCollect$resultHypParam[OutputCollect$resultHypParam$solID == best_id, ]
message("   best_row extracted, nrow=", nrow(best_row), ", ncol=", ncol(best_row))

alloc_tbl <- if (!inherits(AllocatorCollect, "try-error")) AllocatorCollect$result_allocator else NULL
message(
    "   AllocatorCollect status: ",
    if (inherits(AllocatorCollect, "try-error")) "ERROR" else "OK",
    ", alloc_tbl is NULL: ", is.null(alloc_tbl)
)

total_response <- to_scalar(if (!is.null(alloc_tbl)) alloc_tbl$total_response else NA_real_)
total_spend <- to_scalar(if (!is.null(alloc_tbl)) alloc_tbl$total_spend else NA_real_)
message("   Allocator metrics: total_response=", total_response, ", total_spend=", total_spend)

metrics_txt <- file.path(dir_path, "allocator_metrics.txt")
metrics_csv <- file.path(dir_path, "allocator_metrics.csv")

message("→ Writing metrics text file...")
writeLines(c(
    paste("Model ID:", best_id),
    paste("Training Time (mins):", round(training_time, 2)),
    paste("Max Cores Used:", max_cores),
    paste("R2 (train):", round(best_row$rsq_train %||% NA_real_, 4)),
    paste("NRMSE (train):", round(best_row$nrmse_train %||% NA_real_, 4)),
    paste("R2 (validation):", round(best_row$rsq_val %||% NA_real_, 4)),
    paste("NRMSE (validation):", round(best_row$nrmse_val %||% NA_real_, 4)),
    paste("R2 (test):", round(best_row$rsq_test %||% NA_real_, 4)),
    paste("NRMSE (test):", round(best_row$nrmse_test %||% NA_real_, 4)),
    paste("DECOMP.RSSD (train):", round(best_row$decomp.rssd %||% NA_real_, 4)),
    paste("Allocator Total Response:", round(total_response, 2)),
    paste("Allocator Total Spend:", round(total_spend, 2))
), con = metrics_txt)
gcs_put_safe(metrics_txt, file.path(gcs_prefix, "allocator_metrics.txt"))
message("✅ Metrics text file written")

message("→ Creating metrics dataframe...")
tryCatch(
    {
        metrics_df <- data.frame(
            model_id = best_id,
            training_time_mins = round(training_time, 2),
            max_cores_used = max_cores,
            r2_train = round(best_row$rsq_train %||% NA_real_, 4),
            nrmse_train = round(best_row$nrmse_train %||% NA_real_, 4),
            r2_val = round(best_row$rsq_val %||% NA_real_, 4),
            nrmse_val = round(best_row$nrmse_val %||% NA_real_, 4),
            r2_test = round(best_row$rsq_test %||% NA_real_, 4),
            nrmse_test = round(best_row$nrmse_test %||% NA_real_, 4),
            decomp_rssd_train = round(best_row$decomp.rssd %||% NA_real_, 4),
            allocator_total_response = round(total_response, 2),
            allocator_total_spend = round(total_spend, 2),
            stringsAsFactors = FALSE
        )
        message("✅ metrics_df created successfully, dimensions: ", nrow(metrics_df), " x ", ncol(metrics_df))

        message("→ Writing metrics CSV...")
        write.csv(metrics_df, metrics_csv, row.names = FALSE)
        message("✅ Metrics CSV written to: ", metrics_csv)

        gcs_put_safe(metrics_csv, file.path(gcs_prefix, "allocator_metrics.csv"))
        message("✅ Metrics CSV uploaded to GCS")
    },
    error = function(e) {
        msg <- paste("Failed to create or write metrics_df:", conditionMessage(e))
        message("❌ ", msg)

        # Log detailed error
        metrics_err_file <- file.path(dir_path, "metrics_error.txt")
        writeLines(c(
            "METRICS ERROR",
            paste0("When: ", Sys.time()),
            paste0("Message: ", conditionMessage(e)),
            paste0("best_id: ", best_id),
            paste0("best_row class: ", class(best_row)),
            paste0("best_row nrow: ", nrow(best_row)),
            paste0("best_row ncol: ", if (is.data.frame(best_row)) ncol(best_row) else "N/A"),
            paste0("training_time: ", training_time),
            paste0("max_cores: ", max_cores),
            "",
            "Stack trace:",
            paste(capture.output(traceback()), collapse = "\n"),
            "",
            "best_row structure:",
            paste(capture.output(str(best_row)), collapse = "\n")
        ), metrics_err_file)
        gcs_put_safe(metrics_err_file, file.path(gcs_prefix, basename(metrics_err_file)))

        # Continue despite error (don't fail the entire job)
        message("⚠️ Continuing without metrics CSV")
    }
)

# Allocator plot (restored)
alloc_dir <- file.path(dir_path, paste0("allocator_plots_", timestamp))
dir.create(alloc_dir, showWarnings = FALSE)

# Only plot if AllocatorCollect succeeded
if (!inherits(AllocatorCollect, "try-error")) {
    try(
        {
            png(file.path(alloc_dir, paste0("allocator_", best_id, "_365d.png")), width = 1200, height = 800)
            plot(AllocatorCollect)
            dev.off()
            gcs_put_safe(
                file.path(alloc_dir, paste0("allocator_", best_id, "_365d.png")),
                file.path(gcs_prefix, paste0("allocator_plots_", timestamp, "/allocator_", best_id, "_365d.png"))
            )
            message("✅ Allocator plot created successfully")
        },
        silent = TRUE
    )
} else {
    message("⚠️ Skipping allocator plot - AllocatorCollect failed: ", conditionMessage(attr(AllocatorCollect, "condition")))
}

## ---------- UPLOAD EVERYTHING ----------
flush_and_ship_log("before final upload")
for (f in list.files(dir_path, recursive = TRUE, full.names = TRUE)) {
    rel <- sub(paste0("^", normalizePath(dir_path), "/?"), "", normalizePath(f))
    gcs_put_safe(f, file.path(gcs_prefix, rel))
}

cat(
    "✅ Cloud Run Job completed successfully!\n",
    "Outputs in gs://", googleCloudStorageR::gcs_get_global_bucket(), "/", gcs_prefix, "/\n",
    "Training time: ", round(training_time, 2), " minutes using ", max_cores, " cores\n", # nolint
    sep = ""
)

job_finished <- Sys.time()
writeLines(
    jsonlite::toJSON(
        list(
            state = "SUCCEEDED",
            start_time = as.character(job_started),
            end_time = as.character(job_finished),
            duration_minutes = round(as.numeric(difftime(job_finished, job_started, units = "mins")), 2) # nolint
        ),
        auto_unbox = TRUE
    ),
    status_json
)
gcs_put_safe(status_json, file.path(gcs_prefix, "status.json"))
