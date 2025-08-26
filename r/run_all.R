#!/usr/bin/env Rscript

# -------- reticulate config --------
Sys.setenv(
  RETICULATE_PYTHON        = "/usr/bin/python3",
  RETICULATE_AUTOCONFIGURE = "0"
)

suppressPackageStartupMessages({
  library(jsonlite)
  library(dplyr)
  library(tidyr)
  library(lubridate)
  library(readr)
  library(stringr)
  library(Robyn)
  library(googleCloudStorageR) # depends on googleAuthR
  library(mime)
  library(reticulate) # Robyn -> nevergrad via reticulate
})

# -------- safe helpers --------
`%||%` <- function(a, b) {
  if (is.null(a) || length(a) == 0 || all(is.na(a))) {
    return(b)
  }
  if (is.character(a) && length(a) == 1 && !nzchar(a)) {
    return(b)
  }
  a
}

gcs_put <- function(local_file, object_path, upload_type = c("simple", "resumable")) {
  upload_type <- match.arg(upload_type)
  lf <- normalizePath(local_file, mustWork = FALSE)
  if (!file.exists(lf)) stop("Local file does not exist: ", lf)
  if (grepl("^gs://", object_path)) stop("object_path must be a key, not 'gs://…'")
  bkt <- gcs_get_global_bucket()
  if (is.null(bkt) || bkt == "") stop("No bucket set: call gcs_global_bucket(...) first")
  typ <- mime::guess_type(lf)
  if (is.na(typ) || typ == "") typ <- "application/octet-stream"
  googleCloudStorageR::gcs_upload(
    file = lf, name = object_path, bucket = bkt,
    type = typ, upload_type = upload_type,
    predefinedAcl = "bucketLevel"
  )
}
gcs_put_safe <- function(...) {
  tryCatch(gcs_put(...), error = function(e) {
    message("GCS upload failed (non-fatal): ", conditionMessage(e))
  })
}

should_add_n_searches <- function(dtf, spend_cols, thr = 0.15) {
  if (!"N_SEARCHES" %in% names(dtf) || length(spend_cols) == 0) {
    return(FALSE)
  }
  ts <- rowSums(dtf[, spend_cols, drop = FALSE], na.rm = TRUE)
  cval <- suppressWarnings(abs(cor(dtf$N_SEARCHES, ts, use = "complete.obs")))
  isTRUE(!is.na(cval) && cval < thr)
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

get_cfg <- function() {
  args <- commandArgs(trailingOnly = TRUE)
  cfg_path <- sub("^job_cfg=", "", args[grepl("^job_cfg=", args)])
  if (!length(cfg_path) || !file.exists(cfg_path)) stop("Provide job_cfg=/full/path/to/job.json")
  jsonlite::fromJSON(cfg_path)
}

# -------- global run context (for log uploader) --------
.g_bucket <- ""
.g_prefix <- ""

# -------- tee all console to a log file in tempdir(), upload on exit --------
ts <- format(Sys.time(), "%Y%m%d_%H%M%S")
local_log <- file.path(tempdir(), paste0("robyn_console_", ts, ".log"))
log_con_out <- file(local_log, open = "wt")
log_con_err <- file(local_log, open = "at")
sink(log_con_out, type = "output", split = TRUE)
sink(log_con_err, type = "message", append = TRUE)

.on_exit_upload_log <- function() {
  # close sinks
  try(sink(type = "message"), silent = TRUE)
  try(sink(type = "output"), silent = TRUE)
  try(close(log_con_err), silent = TRUE)
  try(close(log_con_out), silent = TRUE)
  # upload if we know where
  if (nzchar(.g_bucket) && nzchar(.g_prefix) && file.exists(local_log)) {
    try(
      {
        gcs_global_bucket(.g_bucket)
        gcs_put(local_log, file.path(.g_prefix, "robyn_console.log"))
        cat("Uploaded console log to gs://", .g_bucket, "/", .g_prefix, "/robyn_console.log\n", sep = "")
      },
      silent = TRUE
    )
  }
}
options(error = function(e) {
  message("FATAL ERROR: ", conditionMessage(e))
  traceback()
  .on_exit_upload_log()
  quit(status = 1)
})
on.exit(.on_exit_upload_log(), add = TRUE)

# -------- bind Python + check nevergrad --------
reticulate::use_python("/usr/bin/python3", required = TRUE)
cat("---- reticulate::py_config() ----\n")
print(reticulate::py_config())
cat("-------------------------------\n")
if (!reticulate::py_module_available("nevergrad")) {
  # last chance: try to load if present
  try(reticulate::py_require("nevergrad"), silent = TRUE)
}
if (!reticulate::py_module_available("nevergrad")) {
  stop("nevergrad not importable via reticulate. Ensure Dockerfile installs numpy/scipy/nevergrad and RETICULATE_PYTHON=/usr/bin/python3.")
}

# -------- config & GCS auth --------
Sys.setenv(TZ = "Europe/Berlin")
cfg <- get_cfg()

# GCS auth: service account on Cloud Run (metadata) or JSON locally
options(googleAuthR.scopes.selected = "https://www.googleapis.com/auth/devstorage.read_write")
if (nzchar(Sys.getenv("GOOGLE_APPLICATION_CREDENTIALS")) &&
  file.exists(Sys.getenv("GOOGLE_APPLICATION_CREDENTIALS"))) {
  googleCloudStorageR::gcs_auth(json_file = Sys.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
} else {
  googleAuthR::gar_gce_auth(scopes = "https://www.googleapis.com/auth/devstorage.read_write")
  googleCloudStorageR::gcs_auth(token = googleAuthR::gar_token())
}

googleCloudStorageR::gcs_global_bucket(cfg$gcs_bucket %||% "mmm-app-output")
options(googleCloudStorageR.predefinedAcl = "bucketLevel")

country <- cfg$country
revision <- cfg$revision
date_input <- cfg$date_input
iter <- as.numeric(cfg$iterations)
trials <- as.numeric(cfg$trials)
train_size <- as.numeric(cfg$train_size)

timestamp <- format(Sys.time(), "%m%d_%H%M%S")
dir_path <- path.expand(file.path("~/budget/datasets", revision, country, timestamp))
dir.create(dir_path, recursive = TRUE, showWarnings = FALSE)
gcs_prefix <- file.path("robyn", revision, country, timestamp)

# expose globals for on.exit uploader + print a discoverable hint for Streamlit
.g_bucket <- gcs_get_global_bucket()
.g_prefix <- gcs_prefix
cat(sprintf("RUN_FOLDER: gs://%s/%s/\n", .g_bucket, .g_prefix))

cat(
  "✅ Parameters\n",
  "  iter       :", iter, "\n",
  "  trials     :", trials, "\n",
  "  country    :", country, "\n",
  "  revision   :", revision, "\n",
  "  date_input :", date_input, "\n",
  "  train_size :", paste(train_size, collapse = ","), "\n"
)

# -------- load data (CSV provided by Streamlit) --------
if (is.null(cfg$csv_path) || !file.exists(cfg$csv_path)) {
  stop("csv_path missing in job.json or file not found. Streamlit must write the input CSV and pass csv_path to R.")
}
message("→ Reading CSV provided by Streamlit: ", cfg$csv_path)
df <- read.csv(cfg$csv_path, check.names = FALSE, stringsAsFactors = FALSE)
names(df) <- toupper(names(df))

# -------- date & clean --------
if ("DATE" %in% names(df)) {
  if (inherits(df$DATE, "POSIXt")) df$date <- as.Date(df$DATE) else df$date <- as.Date(as.character(df$DATE))
  df$DATE <- NULL
} else if ("date" %in% names(df)) {
  df$date <- as.Date(df[["date"]])
  df[["date"]] <- NULL
} else {
  stop("No DATE/date column in data")
}

df <- filter_by_country(df, country)

if (anyDuplicated(df$date)) {
  message("→ Collapsing duplicated dates: ", sum(duplicated(df$date)), " duplicate rows")
  sum_or_first <- function(x) if (is.numeric(x)) sum(x, na.rm = TRUE) else dplyr::first(x)
  df <- df %>%
    dplyr::group_by(date) %>%
    dplyr::summarise(dplyr::across(!dplyr::all_of("date"), sum_or_first), .groups = "drop")
}

df <- fill_day(df)

# numeric coercion for *_COST / *_COSTS
cost_cols <- grep("_COST$", names(df), value = TRUE)
partner_cols <- grep("_COSTS$", names(df), value = TRUE)
cost_cols <- union(cost_cols, partner_cols)
df <- safe_parse_numbers(df, cost_cols)

# drop zero-variance numeric columns
num_cols <- setdiff(names(df), "date")
zero_var <- num_cols[sapply(df[num_cols], function(x) is.numeric(x) && dplyr::n_distinct(x, na.rm = TRUE) <= 1)]
if (length(zero_var)) {
  df <- df[, !(names(df) %in% zero_var), drop = FALSE]
  cat("ℹ️  Dropped zero-variance:", paste(zero_var, collapse = ", "), "\n")
}

if (!"TV_IS_ON" %in% names(df)) df$TV_IS_ON <- 0

# -------- feature engineering --------
df <- df %>% mutate(
  GA_OTHER_COST = rowSums(select(., matches("^GA_.*_COST$"), -any_of(c("GA_SUPPLY_COST", "GA_BRAND_COST", "GA_DEMAND_COST"))), na.rm = TRUE),
  BING_TOTAL_COST = rowSums(select(., matches("^BING_.*_COST$")), na.rm = TRUE),
  META_TOTAL_COST = rowSums(select(., matches("^META_.*_COST$")), na.rm = TRUE),
  ORGANIC_TRAFFIC = rowSums(select(., any_of(c(
    "NL_DAILY_SESSIONS", "SEO_DAILY_SESSIONS", "DIRECT_DAILY_SESSIONS",
    "TV_DAILY_SESSIONS", "CRM_OTHER_DAILY_SESSIONS", "CRM_DAILY_SESSIONS"
  ))), na.rm = TRUE),
  BRAND_HEALTH = coalesce(DIRECT_DAILY_SESSIONS, 0) + coalesce(SEO_DAILY_SESSIONS, 0),
  ORGxTV = BRAND_HEALTH * coalesce(TV_COST, 0),
  GA_OTHER_IMPRESSIONS = rowSums(select(., matches("^GA_.*_IMPRESSIONS$"), -any_of(c("GA_SUPPLY_IMPRESSIONS", "GA_BRAND_IMPRESSIONS", "GA_DEMAND_IMPRESSIONS"))), na.rm = TRUE),
  BING_TOTAL_IMPRESSIONS = rowSums(select(., matches("^BING_.*_IMPRESSIONS$")), na.rm = TRUE),
  META_TOTAL_IMPRESSIONS = rowSums(select(., matches("^META_.*_IMPRESSIONS$")), na.rm = TRUE)
)

# -------- annotations (optional) --------
ann_path <- cfg$annotations_csv %||% file.path(getwd(), "enriched_annotations.csv")
if (file.exists(ann_path)) {
  ann <- read.csv(ann_path, check.names = FALSE, stringsAsFactors = FALSE)
  names(ann) <- toupper(names(ann))
  ann$DATE <- as.Date(ann$DATE)
  df <- df %>% left_join(ann %>% rename(date = DATE), by = "date")
  flag_cols <- intersect(c("ANN_CYCLING_EVENT", "ANN_PRODUCT_CHANGE", "ANN_CRM_ACTIVITY", "IS_ANN"), names(df))
  if (length(flag_cols)) {
    df <- df %>% mutate(across(all_of(flag_cols), ~ {
      v <- dplyr::case_when(
        . %in% c(TRUE, "TRUE", "True", 1, "1") ~ 1L,
        . %in% c(FALSE, "FALSE", "False", 0, "0") ~ 0L,
        TRUE ~ NA_integer_
      )
      tidyr::replace_na(v, 0L)
    }))
  }
}

# -------- window & factors --------
end_data_date <- max(df$date, na.rm = TRUE)
start_data_date <- as.Date("2024-01-01")
df <- df %>% filter(date >= start_data_date, date <= end_data_date)
df$DOW <- wday(df$date, label = TRUE)
df$IS_WEEKEND <- ifelse(df$DOW %in% c("Sat", "Sun"), 1, 0)

# -------- drivers --------
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

# -------- robyn inputs --------
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

# -------- hyperparameters --------
hyper_vars <- c(paid_media_vars, organic_vars)
hyperparameters <- list()
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
hyperparameters[["train_size"]] <- train_size

InputCollect <- robyn_inputs(InputCollect = InputCollect, hyperparameters = hyperparameters)

# Ensure nevergrad again (Robyn calls into it)
if (!reticulate::py_module_available("nevergrad")) reticulate::py_require("nevergrad")

# -------- run robyn --------
OutputModels <- tryCatch(
  {
    robyn_run(
      InputCollect = InputCollect,
      iterations = iter,
      trials = trials,
      ts_validation = TRUE,
      add_penalty_factor = TRUE,
      cores = NULL
    )
  },
  error = function(e) {
    message("robyn_run failed: ", conditionMessage(e))
    stop(e)
  }
)

# Save early artifacts
saveRDS(OutputModels, file.path(dir_path, "OutputModels.RDS"))
saveRDS(InputCollect, file.path(dir_path, "InputCollect.RDS"))
gcs_put_safe(file.path(dir_path, "OutputModels.RDS"), file.path(gcs_prefix, "OutputModels.RDS"))
gcs_put_safe(file.path(dir_path, "InputCollect.RDS"), file.path(gcs_prefix, "InputCollect.RDS"))

# -------- outputs & onepagers --------
OutputCollect <- robyn_outputs(
  InputCollect, OutputModels,
  pareto_fronts = 2, csv_out = "pareto", min_candidates = 5,
  clusters = FALSE, export = TRUE, plot_folder = dir_path, plot_pareto = FALSE, cores = NULL
)
saveRDS(OutputCollect, file.path(dir_path, "OutputCollect.RDS"))
gcs_put_safe(file.path(dir_path, "OutputCollect.RDS"), file.path(gcs_prefix, "OutputCollect.RDS"))

best_id <- OutputCollect$resultHypParam$solID[1]
writeLines(c(best_id, paste("Iterations:", iter), paste("Trials:", trials)),
  con = file.path(dir_path, "best_model_id.txt")
)
gcs_put_safe(file.path(dir_path, "best_model_id.txt"), file.path(gcs_prefix, "best_model_id.txt"))

top_models <- OutputCollect$resultHypParam$solID[1:min(3, nrow(OutputCollect$resultHypParam))]
for (m in top_models) robyn_onepagers(InputCollect, OutputCollect, select_model = m, export = TRUE)

# Try to copy the onepager for best_id to a canonical name {best_id}.png/.pdf
onepager_files <- list.files(dir_path,
  pattern = paste0(best_id, ".*\\.(png|pdf)$"),
  recursive = TRUE, full.names = TRUE
)
if (length(onepager_files)) {
  # prefer PNG
  pngs <- grep("\\.png$", onepager_files, value = TRUE, ignore.case = TRUE)
  pdfs <- grep("\\.pdf$", onepager_files, value = TRUE, ignore.case = TRUE)
  chosen <- if (length(pngs)) pngs[1] else pdfs[1]
  ext <- tools::file_ext(chosen)
  canonical <- file.path(dir_path, paste0(best_id, ".", ext))
  if (!file.exists(canonical)) file.copy(chosen, canonical, overwrite = TRUE)
  gcs_put_safe(canonical, file.path(gcs_prefix, paste0(best_id, ".", ext)))
}

# -------- allocator --------
low_bounds <- ifelse(InputCollect$paid_media_spends == "GA_BRAND_COST", 0, 0.3)
up_bounds <- ifelse(InputCollect$paid_media_spends == "GA_BRAND_COST", 0, 4)

AllocatorCollect <- robyn_allocator(
  InputCollect       = InputCollect,
  OutputCollect      = OutputCollect,
  select_model       = best_id,
  date_range         = c(alloc_start, alloc_end),
  expected_spend     = NULL,
  scenario           = "max_historical_response",
  channel_constr_low = low_bounds,
  channel_constr_up  = up_bounds,
  export             = TRUE
)

# Metrics (txt + csv)
best_row <- OutputCollect$resultHypParam[OutputCollect$resultHypParam$solID == best_id, ]
sum_spend_window <- InputCollect$dt_input %>%
  filter(date >= alloc_start, date <= alloc_end) %>%
  select(all_of(InputCollect$paid_media_spends)) %>%
  rowSums(na.rm = TRUE) %>%
  sum()

alloc_summary <- AllocatorCollect$result_allocator
total_response <- suppressWarnings(as.numeric(alloc_summary$total_response))
total_spend <- suppressWarnings(as.numeric(alloc_summary$total_spend))

metrics_txt <- file.path(dir_path, "allocator_metrics.txt")
metrics_csv <- file.path(dir_path, "allocator_metrics.csv")

lines <- c(
  paste("Model ID:", best_id),
  paste("R2 (train):", round(best_row$rsq_train, 4)),
  paste("NRMSE (train):", round(best_row$nrmse_train, 4)),
  paste("R2 (validation):", round(best_row$rsq_val, 4)),
  paste("NRMSE (validation):", round(best_row$nrmse_val, 4)),
  paste("R2 (test):", round(best_row$rsq_test, 4)),
  paste("NRMSE (test):", round(best_row$nrmse_test, 4)),
  paste("DECOMP.RSSD (train):", round(best_row$decomp.rssd, 4)),
  paste("Allocator Total Response:", ifelse(is.na(total_response), as.character(alloc_summary$total_response), round(total_response, 2))),
  paste("Allocator Total Spend   :", ifelse(is.na(total_spend), as.character(alloc_summary$total_spend), round(total_spend, 2))),
  paste("Recomputed Spend (window):", round(sum_spend_window, 2))
)
writeLines(lines, con = metrics_txt)
gcs_put_safe(metrics_txt, file.path(gcs_prefix, "allocator_metrics.txt"))

metrics_df <- data.frame(
  metric = c(
    "model_id", "r2_train", "nrmse_train", "r2_val", "nrmse_val", "r2_test", "nrmse_test",
    "decomp_rssd_train", "allocator_total_response", "allocator_total_spend", "recomputed_spend_window"
  ),
  value = c(
    best_id,
    round(best_row$rsq_train, 4), round(best_row$nrmse_train, 4),
    round(best_row$rsq_val, 4), round(best_row$nrmse_val, 4),
    round(best_row$rsq_test, 4), round(best_row$nrmse_test, 4),
    round(best_row$decomp.rssd, 4),
    ifelse(is.na(total_response), as.character(alloc_summary$total_response), round(total_response, 2)),
    ifelse(is.na(total_spend), as.character(alloc_summary$total_spend), round(total_spend, 2)),
    round(sum_spend_window, 2)
  ),
  stringsAsFactors = FALSE
)
write.csv(metrics_df, metrics_csv, row.names = FALSE)
gcs_put_safe(metrics_csv, file.path(gcs_prefix, "allocator_metrics.csv"))

# Allocator plot
alloc_dir <- file.path(dir_path, paste0("allocator_plots_", timestamp))
dir.create(alloc_dir, showWarnings = FALSE)
png(file.path(alloc_dir, paste0("allocator_", best_id, "_365d.png")), width = 1200, height = 800)
plot(AllocatorCollect)
dev.off()

# upload everything created under dir_path (non-fatal on errors)
for (f in list.files(dir_path, recursive = TRUE, full.names = TRUE)) {
  rel <- sub(paste0("^", normalizePath(dir_path), "/?"), "", normalizePath(f))
  gcs_put_safe(f, file.path(gcs_prefix, rel))
}

# also copy the local console log into the run folder (besides on.exit upload)
try(
  {
    file.copy(local_log, file.path(dir_path, "robyn_console.log"), overwrite = TRUE)
    gcs_put_safe(file.path(dir_path, "robyn_console.log"), file.path(gcs_prefix, "robyn_console.log"))
  },
  silent = TRUE
)

cat("✅ Done. Outputs in gs://", .g_bucket, "/", gcs_prefix, "/\n", sep = "")
