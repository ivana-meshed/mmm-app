#!/usr/bin/env Rscript

## ---------- ENV ----------
Sys.setenv(
  RETICULATE_PYTHON = "/usr/bin/python3",
  RETICULATE_AUTOCONFIGURE = "0",
  TZ = "Europe/Berlin",
  # Performance settings - use all available cores in Cloud Run Jobs
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
  library(Robyn)
  library(googleCloudStorageR)
  library(mime)
  library(reticulate)
  library(arrow)
  library(future)
  library(future.apply)
  library(parallel)
})

# Configure parallel processing for Cloud Run Jobs
max_cores <- as.numeric(Sys.getenv("R_MAX_CORES", "32"))
plan(multisession, workers = max_cores)

`%||%` <- function(a, b) {
  if (is.null(a) || length(a) == 0) {
    return(b)
  }
  if (all(is.na(a))) {
    return(b)
  }
  if (is.character(a) && length(a) == 1 && !nzchar(a)) {
    return(b)
  }
  a
}

## ---------- HELPERS ----------
should_add_n_searches <- function(dtf, spend_cols, thr = 0.15) {
  if (!"N_SEARCHES" %in% names(dtf) || length(spend_cols) == 0) {
    return(FALSE)
  }
  ts <- rowSums(dtf[, spend_cols, drop = FALSE], na.rm = TRUE)
  cval <- suppressWarnings(abs(cor(dtf$N_SEARCHES, ts, use = "complete.obs")))
  isTRUE(!is.na(cval) && cval < thr)
}

# NEW: GCS download helper for Cloud Run Jobs
gcs_download <- function(gcs_path, local_path) {
  if (!grepl("^gs://", gcs_path)) {
    stop("gcs_path must start with gs://")
  }

  # Parse gs:// path
  path_parts <- sub("^gs://", "", gcs_path)
  bucket_and_object <- strsplit(path_parts, "/", fixed = TRUE)[[1]]
  bucket_name <- bucket_and_object[1]
  object_name <- paste(bucket_and_object[-1], collapse = "/")

  # Download file
  googleCloudStorageR::gcs_get_object(
    object_name = object_name,
    bucket = bucket_name,
    saveToDisk = local_path,
    overwrite = TRUE
  )

  if (!file.exists(local_path)) {
    stop("Failed to download file from GCS: ", gcs_path)
  }

  message("Downloaded: ", gcs_path, " -> ", local_path)
}

gcs_put <- function(local_file, object_path, upload_type = c("simple", "resumable")) {
  upload_type <- match.arg(upload_type)
  lf <- normalizePath(local_file, mustWork = FALSE)
  if (!file.exists(lf)) stop("Local file does not exist: ", lf)
  if (grepl("^gs://", object_path)) {
    stop("object_path must be a key, not 'gs://…'")
  }
  bkt <- gcs_get_global_bucket()
  if (is.null(bkt) || bkt == "") {
    stop("No bucket set: call gcs_global_bucket(...)")
  }
  typ <- mime::guess_type(lf)
  if (is.na(typ) || typ == "") typ <- "application/octet-stream"
  googleCloudStorageR::gcs_upload(
    file = lf,
    name = object_path,
    bucket = bkt,
    type = typ,
    upload_type = upload_type,
    predefinedAcl = "bucketLevel"
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
  all <- tibble(
    date = seq(min(x$date, na.rm = TRUE), max(x$date, na.rm = TRUE), by = "day")
  )
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

# NEW: Get config from environment (Cloud Run Jobs pattern)
get_cfg_from_env <- function() {
  config_gcs_path <- Sys.getenv("JOB_CONFIG_GCS_PATH")
  if (config_gcs_path == "") {
    stop("JOB_CONFIG_GCS_PATH environment variable not set")
  }

  # Download config from GCS
  temp_config <- tempfile(fileext = ".json")
  gcs_download(config_gcs_path, temp_config)

  # Parse config
  cfg <- jsonlite::fromJSON(temp_config)
  unlink(temp_config)

  return(cfg)
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

options(error = function(e) {
  traceback()
  message("FATAL ERROR: ", conditionMessage(e))
  cleanup()
  quit(status = 1)
})
on.exit(cleanup(), add = TRUE)

## ---------- PYTHON SETUP ----------
reticulate::use_python("/usr/bin/python3", required = TRUE)
cat("---- reticulate::py_config() ----\n")
print(reticulate::py_config())
cat("-------------------------------\n")

if (!reticulate::py_module_available("nevergrad")) {
  stop("nevergrad not importable via reticulate.")
}

## ---------- GCS AUTH ----------
options(googleAuthR.scopes.selected = "https://www.googleapis.com/auth/devstorage.read_write")

if (nzchar(Sys.getenv("GOOGLE_APPLICATION_CREDENTIALS")) &&
  file.exists(Sys.getenv("GOOGLE_APPLICATION_CREDENTIALS"))) {
  googleCloudStorageR::gcs_auth(json_file = Sys.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
} else {
  # Cloud Run default credentials
  token <- googleAuthR::gar_gce_auth(
    scopes = "https://www.googleapis.com/auth/devstorage.read_write"
  )
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
  message("→ Downloading data from GCS: ", cfg$data_gcs_path)

  # Download data file
  temp_data <- tempfile(fileext = ".parquet")
  gcs_download(cfg$data_gcs_path, temp_data)

  # Read Parquet (optimized for Cloud Run Jobs)
  df <- arrow::read_parquet(temp_data, as_data_frame = TRUE)
  unlink(temp_data)

  message(sprintf(
    "✅ Data loaded: %s rows, %s columns",
    format(nrow(df), big.mark = ","), ncol(df)
  ))
} else {
  stop("No data_gcs_path provided in configuration")
}

df <- as.data.frame(df)
names(df) <- toupper(names(df))

## ---------- DATE & CLEAN ----------
if ("DATE" %in% names(df)) {
  if (inherits(df$DATE, "POSIXt")) {
    df$date <- as.Date(df$DATE)
  } else {
    df$date <- as.Date(as.character(df$DATE))
  }
  df$DATE <- NULL
} else if ("date" %in% names(df)) {
  df$date <- as.Date(df[["date"]])
  df[["date"]] <- NULL
} else {
  stop("No DATE/date column in data")
}

df <- filter_by_country(df, country)

# Collapse duplicates by date
if (anyDuplicated(df$date)) {
  message("→ Collapsing duplicated dates: ", sum(duplicated(df$date)), " duplicate rows")
  sum_or_first <- function(x) {
    if (is.numeric(x)) sum(x, na.rm = TRUE) else dplyr::first(x)
  }
  df <- df %>%
    dplyr::group_by(date) %>%
    dplyr::summarise(dplyr::across(!dplyr::all_of("date"), sum_or_first), .groups = "drop")
}

df <- fill_day(df)

# Process cost columns
cost_cols <- grep("_COST$", names(df), value = TRUE)
partner_cols <- grep("_COSTS$", names(df), value = TRUE)
cost_cols <- union(cost_cols, partner_cols)
df <- safe_parse_numbers(df, cost_cols)

# Remove zero-variance columns
num_cols <- setdiff(names(df), "date")
zero_var <- num_cols[sapply(df[num_cols], function(x) {
  is.numeric(x) && dplyr::n_distinct(x, na.rm = TRUE) <= 1
})]

if (length(zero_var)) {
  df <- df[, !(names(df) %in% zero_var), drop = FALSE]
  cat("ℹ️ Dropped zero-variance:", paste(zero_var, collapse = ", "), "\n")
}

if (!"TV_IS_ON" %in% names(df)) df$TV_IS_ON <- 0

## ---------- FEATURE ENGINEERING ----------
df <- df %>% mutate(
  GA_OTHER_COST = rowSums(
    select(., tidyselect::matches("^GA_.*_COST$") &
      !any_of(c("GA_SUPPLY_COST", "GA_BRAND_COST", "GA_DEMAND_COST"))),
    na.rm = TRUE
  ),
  BING_TOTAL_COST = rowSums(select(., tidyselect::matches("^BING_.*_COST$")), na.rm = TRUE),
  META_TOTAL_COST = rowSums(select(., tidyselect::matches("^META_.*_COST$")), na.rm = TRUE),
  ORGANIC_TRAFFIC = rowSums(
    select(., any_of(c(
      "NL_DAILY_SESSIONS", "SEO_DAILY_SESSIONS", "DIRECT_DAILY_SESSIONS",
      "TV_DAILY_SESSIONS", "CRM_OTHER_DAILY_SESSIONS", "CRM_DAILY_SESSIONS"
    ))),
    na.rm = TRUE
  ),
  BRAND_HEALTH = coalesce(DIRECT_DAILY_SESSIONS, 0) + coalesce(SEO_DAILY_SESSIONS, 0),
  ORGxTV = BRAND_HEALTH * coalesce(TV_COST, 0),
  GA_OTHER_IMPRESSIONS = rowSums(
    select(., tidyselect::matches("^GA_.*_IMPRESSIONS$") &
      !any_of(c("GA_SUPPLY_IMPRESSIONS", "GA_BRAND_IMPRESSIONS", "GA_DEMAND_IMPRESSIONS"))),
    na.rm = TRUE
  ),
  BING_TOTAL_IMPRESSIONS = rowSums(select(., tidyselect::matches("^BING_.*_IMPRESSIONS$")), na.rm = TRUE),
  META_TOTAL_IMPRESSIONS = rowSums(select(., tidyselect::matches("^META_.*_IMPRESSIONS$")), na.rm = TRUE)
)

## ---------- DATE FILTERING ----------
end_data_date <- max(df$date, na.rm = TRUE)
start_data_date <- as.Date("2024-01-01")
df <- df %>% filter(date >= start_data_date, date <= end_data_date)
df$DOW <- wday(df$date, label = TRUE)
df$IS_WEEKEND <- ifelse(df$DOW %in% c("Sat", "Sun"), 1, 0)

## ---------- DRIVERS ----------
paid_media_spends <- intersect(cfg$paid_media_spends, names(df))
paid_media_vars <- intersect(cfg$paid_media_vars, names(df))
stopifnot(length(paid_media_spends) == length(paid_media_vars))

keep_idx <- vapply(seq_along(paid_media_spends), function(i) {
  sc <- paid_media_spends[i]
  sum(df[[sc]], na.rm = TRUE) > 0
}, logical(1))
paid_media_spends <- paid_media_spends[keep_idx]
paid_media_vars <- paid_media_vars[keep_idx]

context_vars <- intersect(cfg$context_vars %||% character(0), names(df))
factor_vars <- intersect(cfg$factor_vars %||% character(0), names(df))
org_base <- intersect(cfg$organic_vars %||% "ORGANIC_TRAFFIC", names(df))

if (should_add_n_searches(df, paid_media_spends) && "N_SEARCHES" %in% names(df)) {
  organic_vars <- unique(c(org_base, "N_SEARCHES"))
} else {
  organic_vars <- org_base
}

cat(
  "✅ Drivers\n",
  "  paid_media_spends:", paste(paid_media_spends, collapse = ", "), "\n",
  "  paid_media_vars  :", paste(paid_media_vars, collapse = ", "), "\n",
  "  context_vars     :", paste(context_vars, collapse = ", "), "\n",
  "  factor_vars      :", paste(factor_vars, collapse = ", "), "\n",
  "  organic_vars     :", paste(organic_vars, collapse = ", "), "\n"
)

## ---------- ROBYN INPUTS ----------
InputCollect <- robyn_inputs(
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
  adstock = "geometric"
)

alloc_end <- max(InputCollect$dt_input$date)
alloc_start <- alloc_end - 364

## ---------- HYPERPARAMETERS ----------
hyper_vars <- c(paid_media_vars, organic_vars)
hyperparameters <- list()

message("→ Setting up hyperparameters in parallel with ", max_cores, " cores...")

if (length(hyper_vars) > 10) {
  hyperparameters <- future_lapply(hyper_vars, function(v) {
    if (v == "ORGANIC_TRAFFIC") {
      list(alphas = c(0.5, 2.0), gammas = c(0.3, 0.7), thetas = c(0.9, 0.99))
    } else if (v == "TV_COST") {
      list(alphas = c(0.8, 2.2), gammas = c(0.6, 0.99), thetas = c(0.7, 0.95))
    } else if (v == "PARTNERSHIP_COSTS") {
      list(alphas = c(0.65, 2.25), gammas = c(0.45, 0.875), thetas = c(0.3, 0.625))
    } else {
      list(alphas = c(1.0, 3.0), gammas = c(0.6, 0.9), thetas = c(0.1, 0.4))
    }
  }, future.seed = TRUE)

  names(hyperparameters) <- hyper_vars
  hyperparameters_flat <- list()
  for (v in names(hyperparameters)) {
    hyperparameters_flat[[paste0(v, "_alphas")]] <- hyperparameters[[v]]$alphas
    hyperparameters_flat[[paste0(v, "_gammas")]] <- hyperparameters[[v]]$gammas
    hyperparameters_flat[[paste0(v, "_thetas")]] <- hyperparameters[[v]]$thetas
  }
  hyperparameters <- hyperparameters_flat
} else {
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
InputCollect <- robyn_inputs(InputCollect = InputCollect, hyperparameters = hyperparameters)

## ---------- TRAIN ----------
message("→ Starting optimized Robyn training with ", max_cores, " cores on Cloud Run Jobs...")

start_time <- Sys.time()
OutputModels <- robyn_run(
  InputCollect = InputCollect,
  iterations = iter,
  trials = trials,
  ts_validation = TRUE,
  add_penalty_factor = TRUE,
  cores = max_cores
)
training_time <- difftime(Sys.time(), start_time, units = "mins")

message("✅ Training completed in ", round(training_time, 2), " minutes")

# Save and upload models
saveRDS(OutputModels, file.path(dir_path, "OutputModels.RDS"))
saveRDS(InputCollect, file.path(dir_path, "InputCollect.RDS"))
gcs_put_safe(file.path(dir_path, "OutputModels.RDS"), file.path(gcs_prefix, "OutputModels.RDS"))
gcs_put_safe(file.path(dir_path, "InputCollect.RDS"), file.path(gcs_prefix, "InputCollect.RDS"))

## ---------- OUTPUTS & ONEPAGERS ----------
OutputCollect <- robyn_outputs(
  InputCollect, OutputModels,
  pareto_fronts = 2,
  csv_out = "pareto",
  min_candidates = 5,
  clusters = FALSE,
  export = TRUE,
  plot_folder = dir_path,
  plot_pareto = FALSE,
  cores = NULL
)

saveRDS(OutputCollect, file.path(dir_path, "OutputCollect.RDS"))
gcs_put_safe(file.path(dir_path, "OutputCollect.RDS"), file.path(gcs_prefix, "OutputCollect.RDS"))

best_id <- OutputCollect$resultHypParam$solID[1]
writeLines(
  c(
    best_id, paste("Iterations:", iter), paste("Trials:", trials),
    paste("Training time (mins):", round(training_time, 2))
  ),
  con = file.path(dir_path, "best_model_id.txt")
)
gcs_put_safe(file.path(dir_path, "best_model_id.txt"), file.path(gcs_prefix, "best_model_id.txt"))

# Generate onepagers
top_models <- OutputCollect$resultHypParam$solID[1:min(3, nrow(OutputCollect$resultHypParam))]
for (m in top_models) {
  try(robyn_onepagers(InputCollect, OutputCollect, select_model = m, export = TRUE), silent = TRUE)
}

# Upload onepager
all_files <- list.files(dir_path, recursive = TRUE, full.names = TRUE)
escaped_id <- gsub("\\.", "\\\\.", best_id)
png_pat <- paste0("(?i)(onepager).*", escaped_id, ".*\\.png$")
cand_png <- all_files[grepl(png_pat, all_files, perl = TRUE)]

if (length(cand_png)) {
  canonical <- file.path(dir_path, paste0(best_id, ".png"))
  file.copy(cand_png[1], canonical, overwrite = TRUE)
  gcs_put_safe(canonical, file.path(gcs_prefix, paste0(best_id, ".png")))
}

## ---------- ALLOCATOR ----------
is_brand <- InputCollect$paid_media_spends == "GA_BRAND_COST"
low_bounds <- ifelse(is_brand, 0, 0.3)
up_bounds <- ifelse(is_brand, 0, 4)

AllocatorCollect <- try(
  robyn_allocator(
    InputCollect = InputCollect,
    OutputCollect = OutputCollect,
    select_model = best_id,
    date_range = c(alloc_start, alloc_end),
    expected_spend = NULL,
    scenario = "max_historical_response",
    channel_constr_low = low_bounds,
    channel_constr_up = up_bounds,
    export = TRUE
  ),
  silent = TRUE
)

## ---------- METRICS & FINAL UPLOAD ----------
best_row <- OutputCollect$resultHypParam[OutputCollect$resultHypParam$solID == best_id, ]
alloc_tbl <- if (!inherits(AllocatorCollect, "try-error")) AllocatorCollect$result_allocator else NULL

total_response <- to_scalar(if (!is.null(alloc_tbl)) alloc_tbl$total_response else NA_real_)
total_spend <- to_scalar(if (!is.null(alloc_tbl)) alloc_tbl$total_spend else NA_real_)

# Create comprehensive metrics
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

# CSV metrics
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

# Upload all files
for (f in list.files(dir_path, recursive = TRUE, full.names = TRUE)) {
  rel <- sub(paste0("^", normalizePath(dir_path), "/?"), "", normalizePath(f))
  gcs_put_safe(f, file.path(gcs_prefix, rel))
}

cat("✅ Cloud Run Job completed successfully!\n",
  "Outputs in gs://", googleCloudStorageR::gcs_get_global_bucket(), "/",
  gcs_prefix, "/\n",
  "Training time: ", round(training_time, 2), " minutes using ", max_cores, " cores\n",
  sep = ""
)
