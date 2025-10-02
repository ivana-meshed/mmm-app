#!/usr/bin/env Rscript

## =========================================================
## run_all.R — Train Robyn + smarter 3-month forecast
##  - No robyn_predict; uses allocator to simulate plan
##  - Month-scoped allocator windows
##  - Robust incremental extraction
## =========================================================

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
  library(Robyn)
  library(googleCloudStorageR)
  library(mime)
  library(reticulate)
  library(arrow)
  library(future)
  library(future.apply)
  library(parallel)
  library(tibble)
})

source("helpers.R")

## Optional time-series forecaster
HAVE_FORECAST <- requireNamespace("forecast", quietly = TRUE)

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

# gs://bucket/path -> local file
gcs_download <- function(gcs_path, local_path) {
  stopifnot(grepl("^gs://", gcs_path))
  bits <- strsplit(sub("^gs://", "", gcs_path), "/", fixed = TRUE)[[1]]
  bucket <- bits[1]
  object <- paste(bits[-1], collapse = "/")
  googleCloudStorageR::gcs_get_object(
    object_name = object, bucket = bucket,
    saveToDisk = local_path, overwrite = TRUE
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

safe_write_csv <- function(df, path) {
  tryCatch(readr::write_csv(df, path, na = ""), error = function(e) {
    message("write_csv failed at ", path, ": ", conditionMessage(e))
  })
}

## ---- robust incremental extraction (works across Robyn versions) ----
get_allocator_total_response <- function(al_tbl) {
  if (is.null(al_tbl)) {
    return(NA_real_)
  }
  for (nm in c("total_response", "response_total", "total_response_pred", "response_pred", "response")) {
    if (nm %in% names(al_tbl)) {
      return(to_scalar(al_tbl[[nm]]))
    }
  }
  NA_real_
}
get_allocator_total_spend <- function(al_tbl) {
  if (is.null(al_tbl)) {
    return(NA_real_)
  }
  for (nm in c("total_spend", "spend_total", "expected_spend", "spend")) {
    if (nm %in% names(al_tbl)) {
      return(to_scalar(al_tbl[[nm]]))
    }
  }
  NA_real_
}

## ---- derive monthly targets from history when ENV override not provided ----
compute_monthly_targets_from_history <- function(
    dt_input, spend_cols, horizon_months = 3,
    strategy = Sys.getenv("FORECAST_TARGET_STRATEGY", "mean_last_k"),
    k = as.integer(Sys.getenv("FORECAST_RECENT_MONTHS", "3"))) {
  if (!length(spend_cols)) {
    return(rep(0, horizon_months))
  }
  last_day <- max(dt_input$date, na.rm = TRUE)

  monthly <- dt_input %>%
    mutate(
      month = lubridate::floor_date(date, "month"),
      daily_total = rowSums(across(all_of(spend_cols)), na.rm = TRUE)
    ) %>%
    filter(month < lubridate::floor_date(last_day, "month")) %>%
    group_by(month) %>%
    summarise(total = sum(daily_total, na.rm = TRUE), .groups = "drop") %>%
    arrange(month)

  base_val <- if (nrow(monthly) > 0) {
    if (tolower(strategy) == "last_full") {
      tail(monthly$total, 1)
    } else {
      mean(tail(monthly$total, min(k, nrow(monthly))), na.rm = TRUE)
    }
  } else {
    mean(tail(rowSums(dt_input[, spend_cols, drop = FALSE], na.rm = TRUE), 28), na.rm = TRUE) * 30
  }

  base_val <- ifelse(is.finite(base_val) && base_val > 0, base_val, 0)
  rep(base_val, horizon_months)
}

## ---------- GCS AUTH ----------
options(googleAuthR.scopes.selected = c("https://www.googleapis.com/auth/devstorage.read_write"))
ensure_gcs_auth <- local({
  authed <- FALSE
  function() {
    if (authed) {
      return(invisible(TRUE))
    }
    creds <- Sys.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if (nzchar(creds) && file.exists(creds)) {
      googleCloudStorageR::gcs_auth(json_file = creds)
    } else {
      googleAuthR::gar_gce_auth(scopes = "https://www.googleapis.com/auth/devstorage.read_write")
      googleCloudStorageR::gcs_auth(token = googleAuthR::gar_token())
    }
    authed <<- TRUE
    invisible(TRUE)
  }
})

get_cfg_from_env <- function() {
  cfg_path <- Sys.getenv("JOB_CONFIG_GCS_PATH", unset = "")
  if (cfg_path == "") {
    bucket <- Sys.getenv("GCS_BUCKET", unset = "mmm-app-output")
    cfg_path <- sprintf("gs://%s/training-configs/latest/job_config.json", bucket)
    message("JOB_CONFIG_GCS_PATH not set; falling back to ", cfg_path)
  }
  tmp <- tempfile(fileext = ".json")
  gcs_download(cfg_path, tmp)
  on.exit(unlink(tmp), add = TRUE)
  jsonlite::fromJSON(tmp)
}

## ---------- JOBS JOB_HISTORY (GCS) ----------
get_job_history_object <- function() {
  # object path inside bucket (not gs://)
  obj <- Sys.getenv("JOBS_JOB_HISTORY_OBJECT", unset = "robyn-jobs/job_history.csv")
  if (nzchar(obj)) {
    return(obj)
  } else {
    return("robyn-jobs/job_history.csv")
  }
}

append_to_job_history <- function(row) {
  ensure_gcs_auth()
  job_history_obj <- get_job_history_object()
  tmp_csv <- file.path(tempdir(), "jobs_job_history.csv")
  had <- FALSE
  ok <- tryCatch(
    {
      googleCloudStorageR::gcs_get_object(
        object_name = job_history_obj,
        bucket = googleCloudStorageR::gcs_get_global_bucket(),
        saveToDisk = tmp_csv, overwrite = TRUE
      )
      TRUE
    },
    error = function(e) FALSE
  )
  df_old <- NULL
  if (ok && file.exists(tmp_csv)) {
    had <- TRUE
    df_old <- try(readr::read_csv(tmp_csv, show_col_types = FALSE), silent = TRUE)
    if (inherits(df_old, "try-error")) df_old <- NULL
  }
  df_new <- as.data.frame(row, stringsAsFactors = FALSE)
  if (!is.null(df_old) && nrow(df_old)) {
    if ("job_id" %in% names(df_old)) {
      df_old <- df_old[df_old$job_id != row$job_id, , drop = FALSE]
    }
    out <- dplyr::bind_rows(df_old, df_new)
  } else {
    out <- df_new
  }
  if ("start_time" %in% names(out)) out <- out[order(as.POSIXct(out$start_time), decreasing = TRUE), , drop = FALSE]
  readr::write_csv(out, tmp_csv, na = "")
  gcs_put_safe(tmp_csv, job_history_obj)
  invisible(TRUE)
}


## ---------- SMART SPEND FORECAST (weekly -> daily) ----------
build_spend_forecast <- function(dt_input, spend_cols, horizon_months = 3,
                                 monthly_targets = NULL) {
  stopifnot("date" %in% names(dt_input))
  hist <- dt_input %>% arrange(date)

  start_next <- max(hist$date, na.rm = TRUE) + 1
  start_month <- floor_date(start_next, "month")
  if (start_month < start_next) start_month <- start_month %m+% months(1)
  end_month <- start_month %m+% months(horizon_months) - days(1)

  future_days <- tibble(
    date = seq(start_month, end_month, by = "day"),
    dow  = wday(date, label = TRUE, week_start = 1)
  )

  weekday_profile <- function(vals, dates) {
    n <- length(vals)
    k <- min(n, 8 * 7)
    tail_vals <- tail(vals, k)
    tail_dates <- tail(dates, k)
    df <- tibble(
      dow = wday(tail_dates, label = TRUE, week_start = 1),
      val = pmax(tail_vals, 0)
    )
    props <- df |>
      group_by(dow) |>
      summarise(s = sum(val, na.rm = TRUE), .groups = "drop")
    if (sum(props$s) <= 0) props$w <- 1 / 7 else props$w <- props$s / sum(props$s)
    structure(props$w, names = as.character(props$dow))
  }

  fc_list <- list()
  for (v in spend_cols) {
    x <- hist[[v]]
    x[is.na(x)] <- 0

    # aggregate weekly (ISO)
    wk <- hist |>
      mutate(year = isoyear(date), week = isoweek(date)) |>
      group_by(year, week) |>
      summarise(val = sum(.data[[v]], na.rm = TRUE), .groups = "drop") |>
      arrange(year, week)

    # forecast weekly totals
    if (nrow(wk) < 8) {
      weekly_future <- rep(
        mean(tail(wk$val, min(4, nrow(wk))), na.rm = TRUE),
        ceiling(horizon_months * 4.5)
      )
    } else {
      if (HAVE_FORECAST) {
        ts_w <- stats::ts(wk$val, frequency = 52)
        fit <- try(suppressWarnings(forecast::auto.arima(ts_w, stepwise = FALSE, approximation = FALSE)), silent = TRUE)
        if (inherits(fit, "try-error")) fit <- forecast::ets(ts_w)
        weekly_future <- as.numeric(forecast::forecast(fit, h = ceiling(horizon_months * 4.5))$mean)
      } else {
        m <- stats::filter(wk$val, rep(1 / 4, 4), sides = 1)
        weekly_future <- rep(
          tail(na.omit(as.numeric(m)), 1) %||% mean(wk$val, na.rm = TRUE),
          ceiling(horizon_months * 4.5)
        )
      }
      weekly_future[weekly_future < 0] <- 0
    }

    # disaggregate weeks -> days via weekday profile
    prof <- weekday_profile(x, hist$date)
    weeks_seq <- future_days |>
      mutate(year = isoyear(date), week = isoweek(date)) |>
      distinct(year, week) |>
      mutate(fc = head(weekly_future, n = n()))

    per_day <- future_days |>
      mutate(year = isoyear(date), week = isoweek(date)) |>
      left_join(weeks_seq, by = c("year", "week")) |>
      mutate(
        p = as.numeric(prof[as.character(dow)]),
        !!v := (fc %||% 0) * (p %||% (1 / 7))
      ) |>
      select(date, !!v)

    fc_list[[v]] <- per_day
  }

  out <- Reduce(function(a, b) full_join(a, b, by = "date"), fc_list)
  out[is.na(out)] <- 0

  # optional top-down monthly targets (total budget across channels)
  if (!is.null(monthly_targets)) {
    stopifnot(length(monthly_targets) == horizon_months)
    out <- out |> mutate(month = floor_date(date, "month"))
    months_vec <- seq(start_month, by = "1 month", length.out = horizon_months)
    for (i in seq_along(months_vec)) {
      m <- months_vec[i]
      idx <- which(out$month == m)
      cur <- sum(as.matrix(out[idx, spend_cols, drop = FALSE]), na.rm = TRUE)
      tgt <- monthly_targets[i]
      if (is.finite(tgt) && tgt > 0 && is.finite(cur) && cur > 0) {
        out[idx, spend_cols] <- out[idx, spend_cols] * (tgt / cur)
      }
    }
    out <- dplyr::select(out, -month)
  }

  out
}

## ---------- BASELINE (from decomposition, robust) ----------
compute_base_daily <- function(OutputCollect, InputCollect, lookback_days = 28) {
  decomp <- try(OutputCollect$resultDecomp, silent = TRUE)
  if (inherits(decomp, "try-error") || is.null(decomp)) {
    return(mean(tail(InputCollect$dt_input[[InputCollect$dep_var]], lookback_days), na.rm = TRUE))
  }
  nm <- names(decomp)
  tolower_nms <- tolower(nm)

  base_keys <- c("intercept", "trend", "season", "holiday", "weekday")
  base_cols <- nm[tolower_nms %in% base_keys]

  if (length(base_cols) > 0) {
    base_series <- rowSums(decomp[, base_cols, drop = FALSE], na.rm = TRUE)
  } else {
    dep_candidates <- c("dep_var", "depvar", "y", tolower(InputCollect$dep_var), InputCollect$dep_var)
    dep_col <- nm[match(tolower(dep_candidates), tolower_nms, nomatch = 0)]
    dep_col <- dep_col[dep_col != ""]
    if (!length(dep_col)) {
      return(mean(tail(InputCollect$dt_input[[InputCollect$dep_var]], lookback_days), na.rm = TRUE))
    }
    driver_cols <- intersect(
      c(InputCollect$paid_media_vars, InputCollect$organic_vars, InputCollect$context_vars, InputCollect$factor_vars), nm
    )
    if (!length(driver_cols)) {
      base_series <- decomp[[dep_col[1]]]
    } else {
      base_series <- decomp[[dep_col[1]]] - rowSums(decomp[, driver_cols, drop = FALSE], na.rm = TRUE)
    }
  }
  mean(tail(base_series, lookback_days), na.rm = TRUE)
}

## ---------- TIGHT SHARE CONSTRAINTS ----------
make_share_bands <- function(shares, tol = 1e-4) {
  shares[is.na(shares)] <- 0
  lo <- pmax(0, shares - tol)
  up <- pmin(1, shares + tol)
  list(low = lo, up = up)
}

## ---------- LOGGING / ERROR HANDLING ----------
## ---------- LOGGING / ERROR HANDLING ----------
log_file <- NULL
log_con_out <- NULL
log_con_err <- NULL
.__handling_error <- FALSE

cleanup <- function() {
  # Unwind all sinks
  while (sink.number(type = "message") > 0) sink(type = "message")
  while (sink.number() > 0) sink()
  # Close connections so buffers flush
  for (con in list(log_con_out, log_con_err)) {
    if (!is.null(con)) try(close(con), silent = TRUE)
  }
  # Upload log last
  if (!is.null(log_file) && file.exists(log_file)) {
    try(gcs_put_safe(log_file, file.path(gcs_prefix, "robyn_console.log")), silent = TRUE)
  }
}

options(error = function(e) {
  if (.__handling_error) { # prevent recursive handler bombs
    try(cleanup(), silent = TRUE)
    quit(status = 1)
  }
  .__handling_error <<- TRUE
  traceback()
  message("FATAL ERROR: ", conditionMessage(e))
  # Attempt to write FAILED status + job_history
  try(
    {
      job_finished <- Sys.time()
      status_json2 <- status_json %||% file.path(tempdir(), "status.json")
      writeLines(jsonlite::toJSON(list(
        state = "FAILED",
        start_time = as.character(job_started %||% NA),
        end_time = as.character(job_finished),
        error = conditionMessage(e)
      ), auto_unbox = TRUE), status_json2)
      if (!is.null(gcs_prefix)) gcs_put_safe(status_json2, file.path(gcs_prefix, "status.json"))
      if (!is.null(gcs_prefix)) {
        append_to_job_history(list(
          job_id = gcs_prefix, state = "FAILED",
          country = country %||% NA, revision = revision %||% NA,
          date_input = date_input %||% NA,
          iterations = iter %||% NA, trials = trials %||% NA,
          train_size = paste(train_size, collapse = ",") %||% NA,
          dep_var = dep_var %||% NA, adstock = adstock %||% NA,
          start_time = as.character(job_started %||% NA),
          end_time = as.character(job_finished),
          duration_minutes = NA, gcs_prefix = gcs_prefix %||% NA,
          bucket = googleCloudStorageR::gcs_get_global_bucket()
        ))
      }
    },
    silent = TRUE
  )
  cleanup()
  quit(status = 1)
})


## ---------- LOAD CFG ----------
message("Loading configuration from Cloud Run Jobs environment...")
ensure_gcs_auth()
# set a default bucket early so the error handler can use it
early_bucket <- Sys.getenv("GCS_BUCKET", "mmm-app-output")
if (nzchar(early_bucket)) {
  googleCloudStorageR::gcs_global_bucket(early_bucket)
}

cfg <- get_cfg_from_env()

country <- cfg$country
revision <- cfg$revision
date_input <- cfg$date_input
iter <- as.numeric(cfg$iterations)
trials <- as.numeric(cfg$trials)
train_size <- as.numeric(cfg$train_size)
timestamp <- cfg$timestamp %||% format(Sys.time(), "%m%d_%H%M%S")

log_section("CONFIG")
print(str(cfg, max.level = 1))
cat("country:", country, " revision:", revision, " date_input:", date_input, "\n")
cat("iterations:", iter, " trials:", trials, " train_size:", paste(train_size, collapse = ","), "\n")

## ---------- DYNAMIC VARS FROM CFG ----------
# Make dependent variable, date column, and adstock configurable
dep_var <- toupper(cfg$dep_var %||% "UPLOAD_VALUE")
adstock <- tolower(cfg$adstock %||% "geometric")
date_col_cfg <- cfg$date_var %||% "DATE"
date_col <- toupper(date_col_cfg)

dir_path <- path.expand(file.path("~/budget/datasets", revision, country, timestamp))
dir.create(dir_path, recursive = TRUE, showWarnings = FALSE)
gcs_prefix <- file.path("robyn", revision, country, timestamp)

## ---------- STATUS JSON (RUNNING) ----------
job_started <- Sys.time()
status_json <- file.path(dir_path, "status.json")
writeLines(jsonlite::toJSON(list(state = "RUNNING", start_time = as.character(job_started)), auto_unbox = TRUE), status_json)
gcs_put_safe(status_json, file.path(gcs_prefix, "status.json"))

# status.json (RUNNING) already written above
try(append_to_job_history(list(
  job_id = gcs_prefix,
  state = "RUNNING",
  country = country,
  revision = revision,
  date_input = date_input,
  iterations = iter,
  trials = trials,
  train_size = paste(train_size, collapse = ","),
  dep_var = dep_var,
  adstock = adstock,
  start_time = as.character(job_started),
  end_time = NA,
  duration_minutes = NA,
  gcs_prefix = gcs_prefix,
  bucket = googleCloudStorageR::gcs_get_global_bucket()
)), silent = TRUE)

## ---------- START LOG ----------
log_file <- file.path(dir_path, "robyn_console.log")
dir.create(dirname(log_file), recursive = TRUE, showWarnings = FALSE)
log_con_out <- file(log_file, open = "wt")
log_con_err <- file(log_file, open = "at")
sink(log_con_out, split = TRUE)
sink(log_con_err, type = "message")
on.exit(cleanup(), add = TRUE)


## ---------- PYTHON / NEVERGRAD ----------
reticulate::use_python("/usr/bin/python3", required = TRUE)
cat("---- reticulate::py_config() ----\n")
print(reticulate::py_config())
cat("-------------------------------\n")
if (!reticulate::py_module_available("nevergrad")) stop("nevergrad not importable via reticulate.")

## ---------- GCS AUTH (again) ----------
options(googleAuthR.scopes.selected = "https://www.googleapis.com/auth/devstorage.read_write")
if (nzchar(Sys.getenv("GOOGLE_APPLICATION_CREDENTIALS")) && file.exists(Sys.getenv("GOOGLE_APPLICATION_CREDENTIALS"))) {
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
} else {
  stop("No data_gcs_path provided in configuration.")
}

log_section("DATA LOADED")
cat("Rows x Cols:", nrow(df), "x", ncol(df), "\n")
cat("First 20 columns:", paste(head(names(df), 20), collapse = ", "), "\n")
# Size
cat("Approx df size:", format(object.size(df), units = "MB"), "\n")

# Optional annotations (mirrored near outputs)
if (!is.null(cfg$annotations_gcs_path) && nzchar(cfg$annotations_gcs_path)) {
  ann_local <- file.path(dir_path, "enriched_annotations.csv")
  try(gcs_download(cfg$annotations_gcs_path, ann_local), silent = TRUE)
}

df <- as.data.frame(df)
names(df) <- toupper(names(df))

log_section("KEY COLUMNS CHECK")
names(df) <- toupper(names(df))
if (!dep_var %in% names(df)) stop("dep_var missing: ", dep_var, " in names(df)")
if (!(date_col %in% names(df))) stop("date column missing: ", date_col)
cat("dep_var:", dep_var, "  date_col:", date_col, "\n")

# Quick NA & distribution check
cat("dep_var NA%:", round(mean(is.na(df[[dep_var]])) * 100, 2), "%\n")
s <- summary(df[[dep_var]])
cat("dep_var summary:", paste(capture.output(print(s)), collapse = " "), "\n")

if (!dep_var %in% names(df)) stop("dep_var not in data: ", dep_var)
summary(df[[dep_var]])
length(unique(na.omit(df$UPLOAD_VALUE)))

## ---------- DATE & CLEAN ----------
# Normalize date column from configurable `date_col`
if (!(date_col %in% names(df))) {
  stop("Date column not found in data: ", date_col, " (after uppercasing names)")
}
if (inherits(df[[date_col]], "POSIXt")) {
  df$date <- as.Date(df[[date_col]])
} else {
  df$date <- as.Date(as.character(df[[date_col]]))
}
if (date_col %in% names(df)) df[[date_col]] <- NULL

log_section("DATES & DUPLICATES")
cat("date range (raw):", as.character(min(df$date, na.rm = TRUE)), "→", as.character(max(df$date, na.rm = TRUE)), "\n")
ddup <- sum(duplicated(df$date))
cat("duplicated dates:", ddup, "\n")

df <- filter_by_country(df, country)

if (anyDuplicated(df$date)) {
  message("→ Collapsing duplicated dates: ", sum(duplicated(df$date)))
  sum_or_first <- function(x) if (is.numeric(x)) sum(x, na.rm = TRUE) else dplyr::first(x)
  df <- df %>%
    group_by(date) %>%
    summarise(across(!all_of("date"), sum_or_first), .groups = "drop")
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
  # GA_OTHER_COST
  GA_OTHER_COST = rowSums(
    select(., tidyselect::matches("^GA_.*_COST$"), -any_of(c("GA_SUPPLY_COST", "GA_BRAND_COST", "GA_DEMAND_COST"))),
    na.rm = TRUE
  ),

  # GA_OTHER_IMPRESSIONS
  GA_OTHER_IMPRESSIONS = rowSums(
    select(., tidyselect::matches("^GA_.*_IMPRESSIONS$"), -any_of(c("GA_SUPPLY_IMPRESSIONS", "GA_BRAND_IMPRESSIONS", "GA_DEMAND_IMPRESSIONS"))),
    na.rm = TRUE
  ),
  BING_TOTAL_COST = rowSums(select(., tidyselect::matches("^BING_.*_COST$")), na.rm = TRUE),
  META_TOTAL_COST = rowSums(select(., tidyselect::matches("^META_.*_COST$")), na.rm = TRUE),
  ORGANIC_TRAFFIC = rowSums(select(., any_of(c(
    "NL_DAILY_SESSIONS", "SEO_DAILY_SESSIONS", "DIRECT_DAILY_SESSIONS",
    "TV_DAILY_SESSIONS", "CRM_OTHER_DAILY_SESSIONS", "CRM_DAILY_SESSIONS"
  ))), na.rm = TRUE),
  BRAND_HEALTH = coalesce(DIRECT_DAILY_SESSIONS, 0) + coalesce(SEO_DAILY_SESSIONS, 0),
  TV_ANY = rowSums(select(., any_of(c("TV_COST", "TV_COSTS"))), na.rm = TRUE),
  ORGxTV = BRAND_HEALTH * TV_ANY,
  BING_TOTAL_IMPRESSIONS = rowSums(select(., tidyselect::matches("^BING_.*_IMPRESSIONS$")), na.rm = TRUE),
  META_TOTAL_IMPRESSIONS = rowSums(select(., tidyselect::matches("^META_.*_IMPRESSIONS$")), na.rm = TRUE),
  BING_TOTAL_CLICKS = rowSums(select(., tidyselect::matches("^BING_.*_CLICKS$")), na.rm = TRUE),
  META_TOTAL_CLICKS = rowSums(select(., tidyselect::matches("^META_.*_CLICKS$")), na.rm = TRUE)
)

log_section("POST-CLEAN FEATURES")
cat("Columns after cleaning:", ncol(df), "\n")
have <- function(x) x %in% names(df)
cat("TV_COST present?", have("TV_COST"), "  TV_COSTS present?", have("TV_COSTS"), "\n")
cat("ORGANIC_TRAFFIC present?", have("ORGANIC_TRAFFIC"), "\n")

## ---------- WINDOW / FLAGS ----------
end_data_date <- max(df$date, na.rm = TRUE)
start_data_date <- as.Date("2024-01-01")
df <- df %>% filter(date >= start_data_date, date <= end_data_date)
df$DOW <- wday(df$date, label = TRUE)
df$IS_WEEKEND <- ifelse(df$DOW %in% c("Sat", "Sun"), 1, 0)

log_section("TRAIN WINDOW")
cat(
  "Train window:", as.character(start_data_date), "→", as.character(end_data_date),
  "  rows:", nrow(df), "\n"
)

## ---------- DRIVERS (robust, single source of truth) ----------
cfg_paid_spends <- toupper(cfg$paid_media_spends %||% character(0))
cfg_paid_vars <- toupper(cfg$paid_media_vars %||% character(0))

paid_media_spends <- intersect(cfg_paid_spends, names(df))
paid_media_vars <- intersect(cfg_paid_vars, names(df))
if (!length(paid_media_spends) || length(paid_media_spends) != length(paid_media_vars)) {
  message("⚠️ Config paid_media_* mismatch. Falling back to autodetect *_COST(S).")
  paid_media_spends <- grep("(_COSTS?)$", names(df), value = TRUE)
  paid_media_vars <- paid_media_spends
}
keep_idx <- vapply(paid_media_spends, function(s) sum(df[[s]], na.rm = TRUE) > 0, logical(1))
paid_media_spends <- paid_media_spends[keep_idx]
paid_media_vars <- paid_media_vars[keep_idx]
if (!length(paid_media_spends)) stop("No paid-media channels found with non-zero spend.")

context_vars <- intersect(toupper(cfg$context_vars %||% character(0)), names(df))
factor_vars <- intersect(toupper(cfg$factor_vars %||% character(0)), names(df))
overlap_cf <- intersect(context_vars, factor_vars)
if (length(overlap_cf)) {
  message("⚠️ Dropping overlaps from factor_vars: ", paste(overlap_cf, collapse = ", "))
  factor_vars <- setdiff(factor_vars, overlap_cf)
}

org_base <- intersect(toupper(cfg$organic_vars %||% "ORGANIC_TRAFFIC"), names(df))
organic_vars <- if (should_add_n_searches(df, paid_media_spends) && "N_SEARCHES" %in% names(df)) {
  unique(c(org_base, "N_SEARCHES"))
} else {
  org_base
}

log_section("DRIVERS SELECTED")
cat("paid_media_spends:", paste(paid_media_spends, collapse = ", "), "\n")
cat("paid_media_vars :", paste(paid_media_vars, collapse = ", "), "\n")
cat("context_vars    :", paste(context_vars, collapse = ", "), "\n")
cat("factor_vars     :", paste(factor_vars, collapse = ", "), "\n")
cat("organic_vars    :", paste(organic_vars, collapse = ", "), "\n")

# Totals over the train window and last 90d
if (length(paid_media_spends)) {
  last90 <- df$date >= (max(df$date) - 89)
  tot_train <- sort(colSums(df[, paid_media_spends, drop = FALSE], na.rm = TRUE), decreasing = TRUE)
  tot_90 <- sort(colSums(df[last90, paid_media_spends, drop = FALSE], na.rm = TRUE), decreasing = TRUE)
  cat("Paid totals (train):\n")
  print(round(tot_train, 2))
  cat("Paid totals (last 90d):\n")
  print(round(tot_90, 2))
}


## ---------- ADSTOCK DETECTION ----------
ad_type <- tolower(adstock %||% "geometric")
if (!nzchar(ad_type) || ad_type == "none") {
  message("⚠️ adstock was 'none' or empty. Forcing 'geometric' so HPs make sense.")
  ad_type <- "geometric"
}
use_weibull <- grepl("^weibull", ad_type)

## Handy alias so both TV_COST / TV_COSTS are covered
TV_NAME <- if ("TV_COST" %in% paid_media_vars) "TV_COST" else if ("TV_COSTS" %in% paid_media_vars) "TV_COSTS" else NA

## ---------- HYPERPARAMETERS (build once, attach once) ----------
media_hp_vars <- unique(paid_media_vars)
maybe_org <- intersect(organic_vars, names(df)) # optional
hyper_vars <- unique(c(media_hp_vars, maybe_org))
if (!length(hyper_vars)) stop("No variables available for HP ranges.")

hp_for_var <- function(v) {
  # Base saturation
  sat <- list(alphas = c(1.0, 3.0), gammas = c(0.6, 0.9))
  if (v == "ORGANIC_TRAFFIC") sat <- list(alphas = c(0.5, 2.0), gammas = c(0.3, 0.7))
  if (!is.na(TV_NAME) && v == TV_NAME) sat <- list(alphas = c(0.8, 2.2), gammas = c(0.6, 0.99))
  if (v == "PARTNERSHIP_COSTS") sat <- list(alphas = c(0.65, 2.25), gammas = c(0.45, 0.875))

  if (!use_weibull) {
    ad <- if (!is.na(TV_NAME) && v == TV_NAME) {
      list(thetas = c(0.7, 0.95))
    } else if (v == "PARTNERSHIP_COSTS") {
      list(thetas = c(0.3, 0.625))
    } else if (v == "ORGANIC_TRAFFIC") {
      list(thetas = c(0.9, 0.99))
    } else {
      list(thetas = c(0.1, 0.4))
    }
    c(sat, ad)
  } else {
    ad <- if (!is.na(TV_NAME) && v == TV_NAME) {
      list(shapes = c(0.5, 2.5), scales = c(0.5, 0.99))
    } else if (v == "PARTNERSHIP_COSTS") {
      list(shapes = c(0.3, 2.0), scales = c(0.3, 0.9))
    } else if (v == "ORGANIC_TRAFFIC") {
      list(shapes = c(0.2, 1.5), scales = c(0.8, 0.999))
    } else {
      list(shapes = c(0.2, 2.5), scales = c(0.2, 0.9))
    }
    c(sat, ad)
  }
}

hp_list <- setNames(lapply(hyper_vars, hp_for_var), hyper_vars)

# Build with plural AND singular synonyms for cross-version safety
hyperparameters <- list(train_size = as.numeric(train_size))
for (v in names(hp_list)) {
  h <- hp_list[[v]]

  # saturation
  hyperparameters[[paste0(v, "_alphas")]] <- h$alphas
  hyperparameters[[paste0(v, "_gammas")]] <- h$gammas
  hyperparameters[[paste0(v, "_alpha")]] <- h$alphas
  hyperparameters[[paste0(v, "_gamma")]] <- h$gammas

  if (!use_weibull) {
    hyperparameters[[paste0(v, "_thetas")]] <- h$thetas
    hyperparameters[[paste0(v, "_theta")]] <- h$thetas
  } else {
    hyperparameters[[paste0(v, "_shapes")]] <- h$shapes
    hyperparameters[[paste0(v, "_scales")]] <- h$scales
    hyperparameters[[paste0(v, "_shape")]] <- h$shapes
    hyperparameters[[paste0(v, "_scale")]] <- h$scales
  }
}

# Validate before attach (against paid media only)
core_expected <- unlist(lapply(media_hp_vars, function(v) {
  if (!use_weibull) {
    c(
      paste0(v, "_alphas"), paste0(v, "_gammas"), paste0(v, "_thetas"),
      paste0(v, "_alpha"), paste0(v, "_gamma"), paste0(v, "_theta")
    )
  } else {
    c(
      paste0(v, "_alphas"), paste0(v, "_gammas"), paste0(v, "_shapes"), paste0(v, "_scales"),
      paste0(v, "_alpha"), paste0(v, "_gamma"), paste0(v, "_shape"), paste0(v, "_scale")
    )
  }
}))
if (!length(core_expected)) stop("No paid-media variables to validate HPs against.")

## ---------- ROBYN INPUTS (single call; attach HP here) ----------
InputCollect <- robyn_inputs(
  dt_input          = df,
  date_var          = "date",
  dep_var           = dep_var,
  adstock           = ad_type,
  dep_var_type      = "revenue",
  prophet_vars      = c("trend", "season", "holiday", "weekday"),
  prophet_country   = toupper(country),
  paid_media_spends = paid_media_spends,
  paid_media_vars   = paid_media_vars,
  context_vars      = context_vars,
  factor_vars       = factor_vars,
  organic_vars      = organic_vars,
  window_start      = start_data_date,
  window_end        = end_data_date,
  hyperparameters   = hyperparameters # <-- attach here
)

log_section("ROBYYN INPUTS & HPs")
cat("adstock:", InputCollect$adstock, "\n")
hp_keys <- setdiff(names(InputCollect$hyperparameters %||% list()), "train_size")
cat("HP keys kept (n=", length(hp_keys), "): ", paste(head(hp_keys, 25), collapse = ", "), "\n", sep = "")
if (!length(hp_keys)) {
  cat("!!! No HP keys retained. Check adstock type and var spellings.\n")
}
missing_hp <- setdiff(InputCollect$paid_media_vars, unique(gsub("_(alpha|gamma|theta|shape|scale)s?$", "", hp_keys)))
if (length(missing_hp)) cat("Vars with no HP coverage:", paste(missing_hp, collapse = ", "), "\n")

hp_names <- setdiff(names(InputCollect$hyperparameters %||% list()), "train_size")
cat("adstock detected: ", InputCollect$adstock, "\n", sep = "")
cat("HP keys attached (count=", length(hp_names), "): ",
  paste(head(hp_names, 20), collapse = ", "), "\n",
  sep = ""
)

if (!length(hp_names)) {
  stop(
    "Robyn kept 0 HP keys. Check adstock ('", InputCollect$adstock,
    "'), paid_media_vars (", paste(InputCollect$paid_media_vars, collapse = ", "),
    "), and key spellings (alphas/gammas/thetas vs singular; shapes/scales)."
  )
}

alloc_end <- max(InputCollect$dt_input$date)
alloc_start <- alloc_end - 364


## ---------- TRAIN ----------

log_section("TRAINING START")
t0 <- Sys.time()
cat("iterations:", iter, " trials:", trials, " cores:", max_cores, "\n")

OutputModels <- robyn_run(...)

cat("Training finished in", round(difftime(Sys.time(), t0, units = "mins"), 2), "min\n")

# Selection
log_section("MODEL SELECTION")
sel <- tryCatch(robyn_select(InputCollect, OutputModels), error = function(e) e)
if (inherits(sel, "error")) {
  cat("robyn_select error:", conditionMessage(sel), "\n")
} else {
  # print top candidates briefly
  try(
    {
      rh <- sel$pareto$hyper_parameters %||% sel$resultHypParam %||% NULL
      if (!is.null(rh)) {
        cat("Top candidates:", nrow(rh), "\n")
        print(head(rh[, c("solID", "rsq_train", "nrmse_train", "rsq_val", "nrmse_val")], 5))
      }
    },
    silent = TRUE
  )
}

# Outputs
log_section("ROBYYN OUTPUTS")
OutputCollect <- tryCatch(robyn_outputs(...), error = function(e) e)
if (inherits(OutputCollect, "error")) {
  cat("robyn_outputs error:", conditionMessage(OutputCollect), "\n")
}

message("→ Starting Robyn training with ", max_cores, " cores on Cloud Run Jobs...")
t0 <- Sys.time()
OutputModels <- robyn_run(
  InputCollect = InputCollect,
  iterations = iter,
  trials = trials,
  ts_validation = TRUE,
  add_penalty_factor = TRUE,
  cores = max_cores
)
# --- pick best model robustly ---
sel <- tryCatch(
  robyn_select(InputCollect, OutputModels),
  error = function(e) {
    message("robyn_select failed: ", conditionMessage(e))
    NULL
  }
)

best_id <- NA_character_
if (!is.null(sel) && !is.null(sel$best)) {
  best_id <- tryCatch(
    if (!is.null(sel$best$solID)) sel$best$solID else sel$best$id,
    error = function(e) NA_character_
  )
}

if (is.na(best_id) || best_id == "") {
  message("No best model id available (no valid candidates?).")
}

training_time <- as.numeric(difftime(Sys.time(), t0, units = "mins"))
message("✅ Training completed in ", round(training_time, 2), " minutes")

## ---------- timings.csv APPEND ----------
ensure_gcs_auth()
timings_obj <- file.path(gcs_prefix, "timings.csv")
timings_local <- file.path(tempdir(), "timings.csv")
message("Appending training time to: gs://", googleCloudStorageR::gcs_get_global_bucket(), "/", timings_obj)

r_row <- data.frame(Step = "R training (robyn_run)", `Time (s)` = round(training_time * 60, 2), check.names = FALSE)
had_existing <- FALSE
for (i in 1:5) {
  ok <- tryCatch(
    {
      googleCloudStorageR::gcs_get_object(
        object_name = timings_obj, bucket = googleCloudStorageR::gcs_get_global_bucket(),
        saveToDisk = timings_local, overwrite = TRUE
      )
      TRUE
    },
    error = function(e) FALSE
  )
  if (ok && file.exists(timings_local)) {
    had_existing <- TRUE
    break
  }
  Sys.sleep(2)
}
out <- if (had_existing) {
  old <- try(readr::read_csv(timings_local, show_col_types = FALSE), silent = TRUE)
  if (inherits(old, "try-error")) {
    r_row
  } else {
    if ("Step" %in% names(old)) old <- dplyr::filter(old, Step != "R training (robyn_run)")
    dplyr::bind_rows(old, r_row)
  }
} else {
  r_row
}
readr::write_csv(out, timings_local, na = "")
gcs_put_safe(timings_local, timings_obj)

## ---------- SAVE CORE RDS ----------
saveRDS(OutputModels, file.path(dir_path, "OutputModels.RDS"))
saveRDS(InputCollect, file.path(dir_path, "InputCollect.RDS"))
gcs_put_safe(file.path(dir_path, "OutputModels.RDS"), file.path(gcs_prefix, "OutputModels.RDS"))
gcs_put_safe(file.path(dir_path, "InputCollect.RDS"), file.path(gcs_prefix, "InputCollect.RDS"))

message(sprintf("Local InputCollect HP count: %s", length(InputCollect$hyperparameters)))


## ---------- OUTPUTS & ONEPAGERS ----------
# --- outputs are helpful but optional; don't let them crash the run ---
sel <- tryCatch(robyn_select(InputCollect, OutputModels), error = function(e) {
  message("robyn_select failed: ", conditionMessage(e))
  NULL
})
best_id <- if (!is.null(sel) && !is.null(sel$best)) {
  if (!is.null(sel$best$solID)) sel$best$solID else sel$best$id
} else {
  NA_character_
}

OutputCollect <- tryCatch(
  robyn_outputs(
    InputCollect, OutputModels,
    select_model = if (!is.na(best_id) && nzchar(best_id)) best_id else NULL,
    pareto_fronts = 2, csv_out = "pareto",
    min_candidates = 1, clusters = FALSE,
    export = TRUE, plot_folder = dir_path, plot_pareto = FALSE, cores = NULL
  ),
  error = function(e) {
    message("robyn_outputs failed: ", conditionMessage(e))
    NULL
  }
)

log_section("TRAINING START")
t0 <- Sys.time()
cat("iterations:", iter, " trials:", trials, " cores:", max_cores, "\n")

OutputModels <- robyn_run(...)

cat("Training finished in", round(difftime(Sys.time(), t0, units = "mins"), 2), "min\n")

# Selection
log_section("MODEL SELECTION")
sel <- tryCatch(robyn_select(InputCollect, OutputModels), error = function(e) e)
if (inherits(sel, "error")) {
  cat("robyn_select error:", conditionMessage(sel), "\n")
} else {
  # print top candidates briefly
  try(
    {
      rh <- sel$pareto$hyper_parameters %||% sel$resultHypParam %||% NULL
      if (!is.null(rh)) {
        cat("Top candidates:", nrow(rh), "\n")
        print(head(rh[, c("solID", "rsq_train", "nrmse_train", "rsq_val", "nrmse_val")], 5))
      }
    },
    silent = TRUE
  )
}

# Outputs
log_section("ROBYYN OUTPUTS")
OutputCollect <- tryCatch(robyn_outputs(...), error = function(e) e)
if (inherits(OutputCollect, "error")) {
  cat("robyn_outputs error:", conditionMessage(OutputCollect), "\n")
}

stopifnot(!is.null(OutputCollect$resultHypParam), nrow(OutputCollect$resultHypParam) > 0)
# best_id <- OutputCollect$resultHypParam$solID[[1]]

if (is.null(OutputCollect) || (is.list(OutputCollect) && !length(OutputCollect))) {
  stop("robyn_outputs returned empty — no valid candidates or export/plot error.")
}

# ensure best_id if still missing but outputs succeeded
if ((is.na(best_id) || best_id == "") && !is.null(OutputCollect)) {
  rh <- tryCatch(OutputCollect$resultHypParam, error = function(e) NULL)
  if (!is.null(rh) && nrow(rh)) {
    best_id <- suppressWarnings(na.omit(rh$solID)[1])
  }
}

saveRDS(OutputCollect, file.path(dir_path, "OutputCollect.RDS"))
gcs_put_safe(file.path(dir_path, "OutputCollect.RDS"), file.path(gcs_prefix, "OutputCollect.RDS"))

# best_id <- OutputCollect$resultHypParam$solID[1]
writeLines(
  c(best_id, paste("Iterations:", iter), paste("Trials:", trials), paste("Training time (mins):", round(training_time, 2))),
  con = file.path(dir_path, "best_model_id.txt")
)
gcs_put_safe(file.path(dir_path, "best_model_id.txt"), file.path(gcs_prefix, "best_model_id.txt"))

# onepagers for top models
top_models <- OutputCollect$resultHypParam$solID[1:min(3, nrow(OutputCollect$resultHypParam))]
for (m in top_models) try(robyn_onepagers(InputCollect, OutputCollect, select_model = m, export = TRUE), silent = TRUE)

# canonical onepager (png preferred, pdf fallback)
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
  cand_pdf2 <- all_files[basename(all_files) == paste0(best_id, ".pdf")]
  if (length(cand_pdf2)) {
    canonical <- file.path(dir_path, paste0(best_id, ".pdf"))
    file.copy(cand_pdf2[1], canonical, overwrite = TRUE)
    gcs_put_safe(canonical, file.path(gcs_prefix, paste0(best_id, ".pdf")))
  } else {
    message("No onepager image/pdf found for best_id=", best_id)
  }
}

## ---------- ALLOCATOR (overview) ----------
is_brand <- InputCollect$paid_media_spends == "GA_BRAND_COST"
low_bounds0 <- ifelse(is_brand, 0, 0.3)
up_bounds0 <- ifelse(is_brand, 0, 4)
log_section("ALLOCATOR OVERVIEW")
cat("date_range:", as.character(alloc_start), "→", as.character(alloc_end), "\n")
cat("scenario: max_historical_response\n")
AllocatorCollect <- try(robyn_allocator(...), silent = TRUE)
if (inherits(AllocatorCollect, "try-error")) {
  cat("allocator failed:", conditionMessage(attr(AllocatorCollect, "condition")), "\n")
} else {
  cat("allocator ok: rows=", nrow(AllocatorCollect$result_allocator %||% data.frame()), "\n")
}

AllocatorCollect <- try(
  robyn_allocator(
    InputCollect = InputCollect, OutputCollect = OutputCollect,
    select_model = best_id, date_range = c(alloc_start, alloc_end),
    expected_spend = NULL, scenario = "max_historical_response",
    channel_constr_low = low_bounds0, channel_constr_up = up_bounds0, export = TRUE
  ),
  silent = TRUE
)
log_section("ALLOCATOR OVERVIEW")
cat("date_range:", as.character(alloc_start), "→", as.character(alloc_end), "\n")
cat("scenario: max_historical_response\n")
AllocatorCollect <- try(robyn_allocator(...), silent = TRUE)
if (inherits(AllocatorCollect, "try-error")) {
  cat("allocator failed:", conditionMessage(attr(AllocatorCollect, "condition")), "\n")
} else {
  cat("allocator ok: rows=", nrow(AllocatorCollect$result_allocator %||% data.frame()), "\n")
}


## ---------- METRICS + PLOT ----------
best_row <- OutputCollect$resultHypParam[OutputCollect$resultHypParam$solID == best_id, ]
alloc_tbl <- if (!inherits(AllocatorCollect, "try-error")) AllocatorCollect$result_allocator else NULL

total_response <- get_allocator_total_response(alloc_tbl)
total_spend <- get_allocator_total_spend(alloc_tbl)

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
  paste("Allocator Total Response:", round(total_response %||% NA_real_, 2)),
  paste("Allocator Total Spend:", round(total_spend %||% NA_real_, 2))
), con = metrics_txt)
gcs_put_safe(metrics_txt, file.path(gcs_prefix, "allocator_metrics.txt"))

metrics_df <- data.frame(
  model_id                 = best_id,
  training_time_mins       = round(training_time, 2),
  max_cores_used           = max_cores,
  r2_train                 = round(best_row$rsq_train %||% NA_real_, 4),
  nrmse_train              = round(best_row$nrmse_train %||% NA_real_, 4),
  r2_val                   = round(best_row$rsq_val %||% NA_real_, 4),
  nrmse_val                = round(best_row$nrmse_val %||% NA_real_, 4),
  r2_test                  = round(best_row$rsq_test %||% NA_real_, 4),
  nrmse_test               = round(best_row$nrmse_test %||% NA_real_, 4),
  decomp_rssd_train        = round(best_row$decomp.rssd %||% NA_real_, 4),
  allocator_total_response = round(total_response %||% NA_real_, 2),
  allocator_total_spend    = round(total_spend %||% NA_real_, 2),
  stringsAsFactors         = FALSE
)
write.csv(metrics_df, metrics_csv, row.names = FALSE)
gcs_put_safe(metrics_csv, file.path(gcs_prefix, "allocator_metrics.csv"))

# Allocator plot (365d overview)
alloc_dir <- file.path(dir_path, paste0("allocator_plots_", timestamp))
dir.create(alloc_dir, showWarnings = FALSE)
try(
  {
    png(file.path(alloc_dir, paste0("allocator_", best_id, "_365d.png")), width = 1200, height = 800)
    if (!inherits(AllocatorCollect, "try-error")) plot(AllocatorCollect)
    dev.off()
    gcs_put_safe(
      file.path(alloc_dir, paste0("allocator_", best_id, "_365d.png")),
      file.path(gcs_prefix, paste0("allocator_plots_", timestamp, "/allocator_", best_id, "_365d.png"))
    )
  },
  silent = TRUE
)

# Folder for predicted-month allocator plots
pred_alloc_dir <- file.path(dir_path, paste0("allocator_pred_plots_", timestamp))
dir.create(pred_alloc_dir, showWarnings = FALSE)

## ---------- SMART MONTHLY PROJECTIONS (next 3 months) ----------
# 0) Monthly budget targets: ENV override OR derive from history
parse_nums <- function(x) as.numeric(unlist(strsplit(x, "[,;\\s]+")))
monthly_override <- Sys.getenv("FORECAST_MONTHLY_BUDGETS", unset = "")
spend_cols <- InputCollect$paid_media_spends

if (nzchar(monthly_override)) {
  b <- parse_nums(monthly_override)
  monthly_targets <- if (length(b) >= 3) b[1:3] else rep(b[1], 3)
  message("Forecast monthly targets (ENV): ", paste(round(monthly_targets, 0), collapse = ", "))
} else {
  monthly_targets <- compute_monthly_targets_from_history(InputCollect$dt_input, spend_cols, horizon_months = 3)
  message("Forecast monthly targets (HIST): ", paste(round(monthly_targets, 0), collapse = ", "))
}

# 1) Build daily per-channel plan (weeks -> daily), scaled to targets
future_spend <- build_spend_forecast(
  dt_input        = InputCollect$dt_input,
  spend_cols      = spend_cols,
  horizon_months  = 3,
  monthly_targets = monthly_targets
)

# Debug info about the plan we just built
rng <- try(range(future_spend$date), silent = TRUE)
message(
  "future_spend rows=", nrow(future_spend),
  "; date range=", if (!inherits(rng, "try-error")) paste(rng, collapse = " to ") else "NA",
  "; spend_cols=", paste(spend_cols, collapse = ", ")
)

# Persist the daily plan even if empty (useful for debugging)
plan_path <- file.path(dir_path, "spend_plan_daily_next3m.csv")
safe_write_csv(future_spend, plan_path)
gcs_put_safe(plan_path, file.path(gcs_prefix, "spend_plan_daily_next3m.csv"))

# 2) Baseline/day from decomposition (robust)
BASE_LOOKBACK <- as.integer(Sys.getenv("FORECAST_BASE_LOOKBACK_DAYS", "28"))
base_daily <- compute_base_daily(OutputCollect, InputCollect, lookback_days = BASE_LOOKBACK)

# If the plan is empty, write a baseline-only forecast so downstream doesn’t crash
if (!nrow(future_spend)) {
  start_next <- floor_date(max(InputCollect$dt_input$date, na.rm = TRUE) + 1, "month")
  months_vec_fb <- seq(start_next, by = "1 month", length.out = 3)
  days_vec_fb <- as.integer((months_vec_fb %m+% months(1) - days(1)) - months_vec_fb + 1)

  proj <- data.frame(
    month = format(months_vec_fb, "%Y-%m"),
    start = as.Date(months_vec_fb),
    end = as.Date(months_vec_fb %m+% months(1) - days(1)),
    days = days_vec_fb,
    budget = 0,
    baseline = round(base_daily * days_vec_fb, 2),
    incremental = 0,
    forecast_total = round(base_daily * days_vec_fb, 2),
    stringsAsFactors = FALSE
  )

  forecast_csv <- file.path(dir_path, "forecast_next3m.csv")
  safe_write_csv(proj, forecast_csv)
  gcs_put_safe(forecast_csv, file.path(gcs_prefix, "forecast_next3m.csv"))
  message("⚠️ future_spend was empty; wrote baseline-only forecast.")
  print(proj)
} else {
  # 3) Sanity check: plan totals vs targets
  plan_check <- future_spend %>%
    mutate(month = lubridate::floor_date(date, "month")) %>%
    group_by(month) %>%
    summarise(plan_total = sum(rowSums(across(all_of(spend_cols)), na.rm = TRUE), na.rm = TRUE), .groups = "drop") %>%
    arrange(month)

  if (nrow(plan_check)) {
    ratios <- round(plan_check$plan_total / monthly_targets[1:nrow(plan_check)] - 1, 3)
    message("Plan vs targets (ratio-1): ", paste(ratios, collapse = ", "))
    if (any(abs(ratios) > 0.10, na.rm = TRUE)) {
      message("⚠️ Plan deviates >10% from targets. Check inputs or scaling.")
    }
  }
  log_section("FORECAST PLAN")
  rng <- range(future_spend$date)
  cat("future_spend dates:", as.character(rng[1]), "→", as.character(rng[2]), " rows:", nrow(future_spend), "\n")
  if (exists("plan_check") && nrow(plan_check)) {
    cat("Plan vs targets ratio-1:", paste(round(plan_check$plan_total / monthly_targets[seq_len(nrow(plan_check))] - 1, 3), collapse = ", "), "\n")
  }


  # 4) Aggregate to months & evaluate plan via allocator with tight share bands
  future_spend <- future_spend %>% mutate(month = floor_date(date, "month"))
  months_vec <- sort(unique(future_spend$month))

  SHARE_TOL <- as.numeric(Sys.getenv("FORECAST_SHARE_TOL", "1e-4"))
  proj_rows <- list()
  pred_plot_rows <- list()

  # Ensure folder exists (in case earlier creation was skipped)
  if (!dir.exists(pred_alloc_dir)) dir.create(pred_alloc_dir, showWarnings = FALSE, recursive = TRUE)

  for (i in seq_along(months_vec)) {
    m <- months_vec[i]
    seg <- future_spend %>% filter(month == m)
    days_in_m <- nrow(seg)
    monthly_per_channel <- colSums(seg[, spend_cols, drop = FALSE], na.rm = TRUE)
    total_budget <- sum(monthly_per_channel, na.rm = TRUE)

    if (!is.finite(total_budget) || total_budget <= 0) {
      proj_rows[[i]] <- data.frame(
        month = format(m, "%Y-%m"),
        start = as.Date(m),
        end = as.Date((m %m+% months(1)) - days(1)),
        days = days_in_m,
        budget = 0,
        baseline = round(base_daily * days_in_m, 2),
        incremental = 0,
        forecast_total = round(base_daily * days_in_m, 2),
        stringsAsFactors = FALSE
      )
      next
    }

    shares <- monthly_per_channel / total_budget
    bands <- make_share_bands(shares, tol = SHARE_TOL)


    al <- try(
      robyn_allocator(
        InputCollect = InputCollect,
        OutputCollect = OutputCollect,
        select_model = best_id,
        date_range = c(alloc_start, alloc_end), # use trained window; allocator uses model params
        expected_spend = total_budget,
        scenario = "max_historical_response",
        channel_constr_low = as.numeric(bands$low),
        channel_constr_up = as.numeric(bands$up),
        export = TRUE
      ),
      silent = TRUE
    )

    al_tbl <- if (!inherits(al, "try-error")) al$result_allocator else NULL
    incr <- get_allocator_total_response(al_tbl)

    # Plot & upload allocator for this forecast month
    if (!inherits(al, "try-error")) {
      pred_fname <- sprintf("allocator_pred_%s.png", format(m, "%Y-%m"))
      pred_local <- file.path(pred_alloc_dir, pred_fname)
      pred_key <- file.path(paste0("allocator_pred_plots_", timestamp), pred_fname)

      try(
        {
          png(pred_local, width = 1200, height = 800)
          plot(al) # Robyn's allocator plot
          dev.off()
          gcs_put_safe(pred_local, file.path(gcs_prefix, pred_key))
        },
        silent = TRUE
      )

      pred_plot_rows[[length(pred_plot_rows) + 1]] <- data.frame(
        month = format(m, "%Y-%m"),
        image_key = pred_key,
        image_gs = sprintf("gs://%s/%s/%s", googleCloudStorageR::gcs_get_global_bucket(), gcs_prefix, pred_key),
        budget = round(total_budget, 2),
        baseline = round(base_daily * days_in_m, 2),
        incremental = round(incr, 2),
        forecast_total = round(base_daily * days_in_m + (incr %||% 0), 2),
        stringsAsFactors = FALSE
      )
    }

    proj_rows[[i]] <- data.frame(
      month = format(m, "%Y-%m"),
      start = as.Date(m),
      end = as.Date((m %m+% months(1)) - days(1)),
      days = days_in_m,
      budget = round(total_budget, 2),
      baseline = round(base_daily * days_in_m, 2),
      incremental = round(incr, 2),
      forecast_total = round(base_daily * days_in_m + (incr %||% 0), 2),
      stringsAsFactors = FALSE
    )
  }

  proj <- if (length(proj_rows)) {
    dplyr::bind_rows(proj_rows)
  } else {
    data.frame(
      month = character(), start = as.Date(character()), end = as.Date(character()),
      days = integer(), budget = double(), baseline = double(),
      incremental = double(), forecast_total = double()
    )
  }

  # Index for prediction allocator plots (optional)
  if (length(pred_plot_rows)) {
    pred_idx <- dplyr::bind_rows(pred_plot_rows)
    pred_idx_csv <- file.path(dir_path, "forecast_allocator_index.csv")
    safe_write_csv(pred_idx, pred_idx_csv)
    gcs_put_safe(pred_idx_csv, file.path(gcs_prefix, "forecast_allocator_index.csv"))
  }

  # Persist forecast
  forecast_csv <- file.path(dir_path, "forecast_next3m.csv")
  safe_write_csv(proj, forecast_csv)
  gcs_put_safe(forecast_csv, file.path(gcs_prefix, "forecast_next3m.csv"))
  message("Wrote monthly projections (next 3) to: ", forecast_csv)
  print(proj)
}

log_section("ARTIFACTS")
cat("gcs bucket:", googleCloudStorageR::gcs_get_global_bucket(), "\n")
cat("gcs prefix:", gcs_prefix, "\n")
cat("Output files (local):\n")
print(head(list.files(dir_path, recursive = TRUE), 50))

## ---------- UPLOAD EVERYTHING (mirror local dir) ----------
for (f in list.files(dir_path, recursive = TRUE, full.names = TRUE)) {
  rel <- sub(paste0("^", normalizePath(dir_path), "/?"), "", normalizePath(f))
  gcs_put_safe(f, file.path(gcs_prefix, rel))
}

cat(
  "✅ Cloud Run Job completed successfully!\n",
  "Outputs in gs://", googleCloudStorageR::gcs_get_global_bucket(), "/", gcs_prefix, "/\n",
  "Training time: ", round(training_time, 2), " minutes using ", max_cores, " cores\n",
  sep = ""
)

## ---------- STATUS JSON (SUCCEEDED) ----------
job_finished <- Sys.time()
writeLines(
  jsonlite::toJSON(
    list(
      state = "SUCCEEDED",
      start_time = as.character(job_started),
      end_time = as.character(job_finished),
      duration_minutes = round(as.numeric(difftime(job_finished, job_started, units = "mins")), 2)
    ),
    auto_unbox = TRUE
  ),
  status_json
)
try(append_to_job_history(list(
  job_id = gcs_prefix,
  state = "SUCCEEDED",
  country = country,
  revision = revision,
  date_input = date_input,
  iterations = iter,
  trials = trials,
  train_size = paste(train_size, collapse = ","),
  dep_var = dep_var,
  adstock = adstock,
  start_time = as.character(job_started),
  end_time = as.character(job_finished),
  duration_minutes = round(as.numeric(difftime(job_finished, job_started, units = "mins")), 2),
  gcs_prefix = gcs_prefix,
  bucket = googleCloudStorageR::gcs_get_global_bucket()
)), silent = TRUE)


gcs_put_safe(status_json, file.path(gcs_prefix, "status.json"))
