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
    try(gcs_put_safe(log_file, file.path(gcs_prefix, "robyn_console.log")), silent = TRUE)
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

max_cores <- as.numeric(Sys.getenv("R_MAX_CORES", "32"))
plan(multisession, workers = max_cores)

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
date_input <- cfg$date_input
iter <- as.numeric(cfg$iterations)
trials <- as.numeric(cfg$trials)
train_size <- as.numeric(cfg$train_size)
timestamp <- cfg$timestamp %||% format(Sys.time(), "%m%d_%H%M%S")

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
log_file <- file.path(dir_path, "robyn_console.log")
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
    try(gcs_put_safe(log_file, file.path(gcs_prefix, "robyn_console.log")), silent = TRUE)
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
        try(gcs_put_safe(log_file, file.path(gcs_prefix, "robyn_console.log")), silent = TRUE)
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
if ("DATE" %in% names(df)) {
    df$date <- if (inherits(df$DATE, "POSIXt")) as.Date(df$DATE) else as.Date(as.character(df$DATE))
    df$DATE <- NULL
} else if ("date" %in% names(df)) {
    df$date <- as.Date(df[["date"]])
    df[["date"]] <- NULL
} else {
    stop("No DATE/date column in data")
}

df <- filter_by_country(df, country)

if (anyDuplicated(df$date)) {
    message("→ Collapsing duplicated dates: ", sum(duplicated(df$date)))
    sum_or_first <- function(x) if (is.numeric(x)) sum(x, na.rm = TRUE) else dplyr::first(x)
    df <- df %>%
        dplyr::group_by(date) %>%
        dplyr::summarise(dplyr::across(!dplyr::all_of("date"), sum_or_first), .groups = "drop")
}

df <- fill_day(df)

cost_cols <- union(grep("_COST$", names(df), value = TRUE), grep("_COSTS$", names(df), value = TRUE))
df <- safe_parse_numbers(df, cost_cols)

num_cols <- setdiff(names(df), "date")
zero_var <- num_cols[sapply(df[num_cols], function(x) is.numeric(x) && dplyr::n_distinct(x, na.rm = TRUE) <= 1)]
if (length(zero_var)) {
    df <- df[, !(names(df) %in% zero_var), drop = FALSE]
    cat("ℹ️ Dropped zero-variance:", paste(zero_var, collapse = ", "), "\n")
}
if (!"TV_IS_ON" %in% names(df)) df$TV_IS_ON <- 0

## ---------- FEATURE ENGINEERING ----------
df <- df %>% mutate(
    GA_OTHER_COST = rowSums(select(., tidyselect::matches("^GA_.*_COST$") & !any_of(c("GA_SUPPLY_COST", "GA_BRAND_COST", "GA_DEMAND_COST"))), na.rm = TRUE),
    BING_TOTAL_COST = rowSums(select(., tidyselect::matches("^BING_.*_COST$")), na.rm = TRUE),
    META_TOTAL_COST = rowSums(select(., tidyselect::matches("^META_.*_COST$")), na.rm = TRUE),
    ORGANIC_TRAFFIC = rowSums(select(., any_of(c("NL_DAILY_SESSIONS", "SEO_DAILY_SESSIONS", "DIRECT_DAILY_SESSIONS", "TV_DAILY_SESSIONS", "CRM_OTHER_DAILY_SESSIONS", "CRM_DAILY_SESSIONS"))), na.rm = TRUE),
    BRAND_HEALTH = coalesce(DIRECT_DAILY_SESSIONS, 0) + coalesce(SEO_DAILY_SESSIONS, 0),
    ORGxTV = BRAND_HEALTH * coalesce(TV_COST, 0),
    GA_OTHER_IMPRESSIONS = rowSums(select(., tidyselect::matches("^GA_.*_IMPRESSIONS$") & !any_of(c("GA_SUPPLY_IMPRESSIONS", "GA_BRAND_IMPRESSIONS", "GA_DEMAND_IMPRESSIONS"))), na.rm = TRUE),
    BING_TOTAL_IMPRESSIONS = rowSums(select(., tidyselect::matches("^BING_.*_IMPRESSIONS$")), na.rm = TRUE),
    META_TOTAL_IMPRESSIONS = rowSums(select(., tidyselect::matches("^META_.*_IMPRESSIONS$")), na.rm = TRUE)
)

## ---------- WINDOW / FLAGS ----------
end_data_date <- max(df$date, na.rm = TRUE)
start_data_date <- as.Date("2024-01-01")
df <- df %>% filter(date >= start_data_date, date <= end_data_date)
df$DOW <- wday(df$date, label = TRUE)
df$IS_WEEKEND <- ifelse(df$DOW %in% c("Sat", "Sun"), 1, 0)

## ---------- DRIVERS ----------
paid_media_spends <- intersect(cfg$paid_media_spends, names(df))
paid_media_vars <- intersect(cfg$paid_media_vars, names(df))
stopifnot(length(paid_media_spends) == length(paid_media_vars))

keep_idx <- vapply(seq_along(paid_media_spends), function(i) sum(df[[paid_media_spends[i]]], na.rm = TRUE) > 0, logical(1))
paid_media_spends <- paid_media_spends[keep_idx]
paid_media_vars <- paid_media_vars[keep_idx]

context_vars <- intersect(cfg$context_vars %||% character(0), names(df))
factor_vars <- intersect(cfg$factor_vars %||% character(0), names(df))
org_base <- intersect(cfg$organic_vars %||% "ORGANIC_TRAFFIC", names(df))
organic_vars <- if (should_add_n_searches(df, paid_media_spends) && "N_SEARCHES" %in% names(df)) unique(c(org_base, "N_SEARCHES")) else org_base

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

# First: call robyn_inputs WITHOUT hyperparameters
InputCollect <- tryCatch(
    {
        robyn_inputs(
            dt_input = df,
            date_var = "date",
            dep_var = "UPLOAD_VALUE",
            dep_var_type = "revenue",
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

# Breadcrumb before hyper_vars access (does not change behavior)
message("About to build hyper_vars; InputCollect NULL? ", is.null(InputCollect))
if (!is.null(InputCollect)) {
    message("paid_media_vars (preflight): ", paste(InputCollect$paid_media_vars, collapse = ", "))
    message("organic_vars (preflight): ", paste(InputCollect$organic_vars, collapse = ", "))
}

# Now build hyperparameters based on what Robyn ACTUALLY has
hyper_vars <- c(InputCollect$paid_media_vars, InputCollect$organic_vars)
hyperparameters <- list()


# Now attach to InputCollect
## ---------- BUILD HYPERPARAMETERS FIRST ----------
# BEFORE calling robyn_inputs(), build the hyperparameter list

mk_hp <- function(v) {
    if (v == "ORGANIC_TRAFFIC") {
        list(alphas = c(0.5, 2.0), gammas = c(0.3, 0.7), thetas = c(0.9, 0.99))
    } else if (v == "TV_COST") {
        list(alphas = c(0.8, 2.2), gammas = c(0.6, 0.99), thetas = c(0.7, 0.95))
    } else if (v == "PARTNERSHIP_COSTS") {
        list(alphas = c(0.65, 2.25), gammas = c(0.45, 0.875), thetas = c(0.3, 0.625))
    } else {
        list(alphas = c(1.0, 3.0), gammas = c(0.6, 0.9), thetas = c(0.1, 0.4))
    }
}

# Build hyperparameters from the original paid_media_vars and organic_vars
# (the ones you identified before robyn_inputs, not after)
# hyper_vars <- c(paid_media_vars, organic_vars)
# hyperparameters <- list()

for (v in hyper_vars) {
    spec <- mk_hp(v)
    hyperparameters[[paste0(v, "_alphas")]] <- spec$alphas
    hyperparameters[[paste0(v, "_gammas")]] <- spec$gammas
    hyperparameters[[paste0(v, "_thetas")]] <- spec$thetas
}
hyperparameters[["train_size"]] <- train_size

message("Pre-built hyperparameters: ", length(hyperparameters), " keys")

log_hyperparameters(hyperparameters, dir_path)
flush_and_ship_log("after hyperparameters build")


## ---------- NOW CALL robyn_inputs WITH hyperparameters ----------
InputCollect <- tryCatch(
    {
        robyn_inputs(
            dt_input = df,
            date_var = "date",
            dep_var = "UPLOAD_VALUE",
            dep_var_type = "revenue",
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
        writeLines(c(
            "robyn_run() FAILED",
            paste0("When     : ", as.character(Sys.time())),
            paste0("Elapsed  : ", round(elapsed, 2), " sec"),
            paste0("Message  : ", conditionMessage(e)),
            paste0("Call     : ", paste(deparse(conditionCall(e)), collapse = " ")),
            paste0("Class    : ", paste(class(e), collapse = ", ")),
            "--- Approximate R call stack (inner→outer) ---",
            paste(rev(calls_chr), collapse = "\n")
        ), robyn_err_txt)

        err_payload <- list(
            state = "FAILED", step = "robyn_run",
            timestamp = as.character(Sys.time()),
            training_started_at = as.character(t0),
            elapsed_seconds = elapsed,
            message = conditionMessage(e),
            call = paste(deparse(conditionCall(e)), collapse = " "),
            class = unname(class(e)),
            stack_inner_to_outer = as.list(calls_chr),
            params = list(iterations = iter, trials = trials, cores = max_cores)
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
training_time <- as.numeric(difftime(Sys.time(), t0, units = "mins"))
message("✅ Training completed in ", round(training_time, 2), " minutes")

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
OutputCollect <- robyn_outputs(
    InputCollect, OutputModels,
    pareto_fronts = 2, csv_out = "pareto",
    min_candidates = 5, clusters = FALSE,
    export = TRUE, plot_folder = dir_path,
    plot_pareto = FALSE, cores = NULL
)
flush_and_ship_log("after robyn_outputs")
saveRDS(OutputCollect, file.path(dir_path, "OutputCollect.RDS"))
gcs_put_safe(file.path(dir_path, "OutputCollect.RDS"), file.path(gcs_prefix, "OutputCollect.RDS"))

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
top_models <- OutputCollect$resultHypParam$solID[
    1:min(3, nrow(OutputCollect$resultHypParam))
]
for (m in top_models) {
    tryCatch(
        robyn_onepagers(
            InputCollect,
            OutputCollect,
            select_model = m,
            plot_folder = dir_path,
            export = TRUE
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
            export = TRUE
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

is_brand <- InputCollect$paid_media_spends == "GA_BRAND_COST"
low_bounds <- ifelse(is_brand, 0, 0.3)
up_bounds <- ifelse(is_brand, 0, 4)
AllocatorCollect <- try(
    robyn_allocator(
        InputCollect = InputCollect, OutputCollect = OutputCollect,
        select_model = best_id, date_range = c(alloc_start, alloc_end),
        expected_spend = NULL, scenario = "max_historical_response",
        channel_constr_low = low_bounds, channel_constr_up = up_bounds,
        export = TRUE
    ),
    silent = TRUE
)
flush_and_ship_log("after robyn_allocator")
## ---------- METRICS + PLOT ----------
best_row <- OutputCollect$resultHypParam[OutputCollect$resultHypParam$solID == best_id, ]
alloc_tbl <- if (!inherits(AllocatorCollect, "try-error")) AllocatorCollect$result_allocator else NULL

total_response <- to_scalar(if (!is.null(alloc_tbl)) alloc_tbl$total_response else NA_real_)
total_spend <- to_scalar(if (!is.null(alloc_tbl)) alloc_tbl$total_spend else NA_real_)

metrics_txt <- file.path(dir_path, "allocator_metrics.txt")
metrics_csv <- file.path(dir_path, "allocator_metrics.csv")

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
write.csv(metrics_df, metrics_csv, row.names = FALSE)
gcs_put_safe(metrics_csv, file.path(gcs_prefix, "allocator_metrics.csv"))

# Allocator plot (restored)
alloc_dir <- file.path(dir_path, paste0("allocator_plots_", timestamp))
dir.create(alloc_dir, showWarnings = FALSE)
try(
    {
        png(file.path(alloc_dir, paste0("allocator_", best_id, "_365d.png")), width = 1200, height = 800)
        plot(AllocatorCollect)
        dev.off()
        gcs_put_safe(
            file.path(alloc_dir, paste0("allocator_", best_id, "_365d.png")),
            file.path(gcs_prefix, paste0("allocator_plots_", timestamp, "/allocator_", best_id, "_365d.png"))
        )
    },
    silent = TRUE
)

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
