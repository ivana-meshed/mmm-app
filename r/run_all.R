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
})

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
log_file <- NULL
cleanup <- function() {
  try(sink(type = "message"), silent = TRUE)
  try(sink(), silent = TRUE)
  if (!is.null(log_file)) try(gcs_put_safe(log_file, file.path(gcs_prefix, "robyn_console.log")), silent = TRUE)
}

options(error = function(e) {
  traceback()
  message("FATAL ERROR: ", conditionMessage(e))
  cleanup()
  quit(status = 1)
})

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

## ---------- STATUS JSON (RUNNING) ----------
job_started <- Sys.time()
status_json <- file.path(dir_path, "status.json")
writeLines(jsonlite::toJSON(list(state = "RUNNING", start_time = as.character(job_started)), auto_unbox = TRUE), status_json)
gcs_put_safe(status_json, file.path(gcs_prefix, "status.json"))

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

# Optional annotations (mirrored near outputs)
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
  GA_OTHER_COST = rowSums(select(., tidyselect::matches("^GA_.*_COST$") &
    !any_of(c("GA_SUPPLY_COST", "GA_BRAND_COST", "GA_DEMAND_COST"))), na.rm = TRUE),
  BING_TOTAL_COST = rowSums(select(., tidyselect::matches("^BING_.*_COST$")), na.rm = TRUE),
  META_TOTAL_COST = rowSums(select(., tidyselect::matches("^META_.*_COST$")), na.rm = TRUE),
  ORGANIC_TRAFFIC = rowSums(select(., any_of(c(
    "NL_DAILY_SESSIONS", "SEO_DAILY_SESSIONS", "DIRECT_DAILY_SESSIONS",
    "TV_DAILY_SESSIONS", "CRM_OTHER_DAILY_SESSIONS", "CRM_DAILY_SESSIONS"
  ))), na.rm = TRUE),
  BRAND_HEALTH = coalesce(DIRECT_DAILY_SESSIONS, 0) + coalesce(SEO_DAILY_SESSIONS, 0),
  ORGxTV = BRAND_HEALTH * coalesce(TV_COST, 0),
  GA_OTHER_IMPRESSIONS = rowSums(select(., tidyselect::matches("^GA_.*_IMPRESSIONS$") &
    !any_of(c("GA_SUPPLY_IMPRESSIONS", "GA_BRAND_IMPRESSIONS", "GA_DEMAND_IMPRESSIONS"))), na.rm = TRUE),
  BING_TOTAL_IMPRESSIONS = rowSums(select(., tidyselect::matches("^BING_.*_IMPRESSIONS$")), na.rm = TRUE),
  META_TOTAL_IMPRESSIONS = rowSums(select(., tidyselect::matches("^META_.*_IMPRESSIONS$")), na.rm = TRUE),
  BING_TOTAL_CLICKS = rowSums(select(., tidyselect::matches("^BING_.*_CLICKS$")), na.rm = TRUE),
  META_TOTAL_CLICKS = rowSums(select(., tidyselect::matches("^META_.*_CLICKS$")), na.rm = TRUE)
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

keep_idx <- vapply(
  seq_along(paid_media_spends),
  function(i) sum(df[[paid_media_spends[i]]], na.rm = TRUE) > 0, logical(1)
)
paid_media_spends <- paid_media_spends[keep_idx]
paid_media_vars <- paid_media_vars[keep_idx]

context_vars <- intersect(cfg$context_vars %||% character(0), names(df))
factor_vars <- intersect(cfg$factor_vars %||% character(0), names(df))
org_base <- intersect(cfg$organic_vars %||% "ORGANIC_TRAFFIC", names(df))
organic_vars <- if (should_add_n_searches(df, paid_media_spends) && "N_SEARCHES" %in% names(df)) {
  unique(c(org_base, "N_SEARCHES"))
} else {
  org_base
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
  dt_input          = df,
  date_var          = "date",
  dep_var           = "UPLOAD_VALUE",
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
  adstock           = "geometric"
)

alloc_end <- max(InputCollect$dt_input$date)
alloc_start <- alloc_end - 364

## ---------- HYPERPARAMETERS ----------
hyper_vars <- c(paid_media_vars, organic_vars)
hyperparameters <- list()

message("→ Setting up hyperparameters (", length(hyper_vars), " vars) with ", max_cores, " cores...")
if (length(hyper_vars) > 10) {
  hp <- future_lapply(hyper_vars, function(v) {
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
  names(hp) <- hyper_vars
  for (v in names(hp)) {
    hyperparameters[[paste0(v, "_alphas")]] <- hp[[v]]$alphas
    hyperparameters[[paste0(v, "_gammas")]] <- hp[[v]]$gammas
    hyperparameters[[paste0(v, "_thetas")]] <- hp[[v]]$thetas
  }
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

## ---------- OUTPUTS & ONEPAGERS ----------
OutputCollect <- robyn_outputs(
  InputCollect, OutputModels,
  pareto_fronts = 2, csv_out = "pareto",
  min_candidates = 5, clusters = FALSE,
  export = TRUE, plot_folder = dir_path,
  plot_pareto = FALSE, cores = NULL
)
saveRDS(OutputCollect, file.path(dir_path, "OutputCollect.RDS"))
gcs_put_safe(file.path(dir_path, "OutputCollect.RDS"), file.path(gcs_prefix, "OutputCollect.RDS"))

best_id <- OutputCollect$resultHypParam$solID[1]
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
AllocatorCollect <- try(
  robyn_allocator(
    InputCollect = InputCollect, OutputCollect = OutputCollect,
    select_model = best_id, date_range = c(alloc_start, alloc_end),
    expected_spend = NULL, scenario = "max_historical_response",
    channel_constr_low = low_bounds0, channel_constr_up = up_bounds0, export = TRUE
  ),
  silent = TRUE
)

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

# Sanity check: plan totals vs targets
plan_check <- future_spend %>%
  mutate(month = lubridate::floor_date(date, "month")) %>%
  group_by(month) %>%
  summarise(plan_total = sum(rowSums(across(all_of(spend_cols)), na.rm = TRUE), na.rm = TRUE), .groups = "drop") %>%
  arrange(month)
if (nrow(plan_check)) {
  ratios <- round(plan_check$plan_total / monthly_targets[1:nrow(plan_check)] - 1, 3)
  message("Plan vs targets (ratio-1): ", paste(ratios, collapse = ", "))
  if (any(abs(ratios) > 0.10, na.rm = TRUE)) message("⚠️ Plan deviates >10% from targets. Check inputs or scaling.")
}

plan_path <- file.path(dir_path, "spend_plan_daily_next3m.csv")
safe_write_csv(future_spend, plan_path)
gcs_put_safe(plan_path, file.path(gcs_prefix, "spend_plan_daily_next3m.csv"))

# 2) Baseline/day from decomposition (robust)
BASE_LOOKBACK <- as.integer(Sys.getenv("FORECAST_BASE_LOOKBACK_DAYS", "28"))
base_daily <- compute_base_daily(OutputCollect, InputCollect, lookback_days = BASE_LOOKBACK)

# 3) Aggregate to months & evaluate plan via allocator with tight share bands
future_spend <- future_spend %>% mutate(month = floor_date(date, "month"))
months_vec <- sort(unique(future_spend$month))

SHARE_TOL <- as.numeric(Sys.getenv("FORECAST_SHARE_TOL", "1e-4"))
proj_rows <- list()

pred_plot_rows <- list()

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
  # --- ADD: plot + upload allocator for this forecast month ---
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

    # (optional) stash a small row to build an index CSV later
    if (!exists("pred_plot_rows", inherits = FALSE)) pred_plot_rows <- list()
    pred_plot_rows[[length(pred_plot_rows) + 1]] <- data.frame(
      month = format(m, "%Y-%m"),
      image_key = pred_key,
      image_gs = sprintf(
        "gs://%s/%s/%s",
        googleCloudStorageR::gcs_get_global_bucket(),
        gcs_prefix, pred_key
      ),
      budget = round(total_budget, 2),
      baseline = round(base_daily * days_in_m, 2),
      incremental = round(incr, 2),
      forecast_total = round(base_daily * days_in_m + (incr %||% 0), 2),
      stringsAsFactors = FALSE
    )
  }
  # --- end ADD ---

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

# ADD: write a small index for the prediction allocator plots
if (exists("pred_plot_rows", inherits = FALSE) && length(pred_plot_rows)) {
  pred_idx <- dplyr::bind_rows(pred_plot_rows)
  pred_idx_csv <- file.path(dir_path, "forecast_allocator_index.csv")
  safe_write_csv(pred_idx, pred_idx_csv)
  gcs_put_safe(pred_idx_csv, file.path(gcs_prefix, "forecast_allocator_index.csv"))
}


forecast_csv <- file.path(dir_path, "forecast_next3m.csv")
safe_write_csv(proj, forecast_csv)
gcs_put_safe(forecast_csv, file.path(gcs_prefix, "forecast_next3m.csv"))
message("Wrote monthly projections (next 3) to: ", forecast_csv)
print(proj)

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
gcs_put_safe(status_json, file.path(gcs_prefix, "status.json"))
