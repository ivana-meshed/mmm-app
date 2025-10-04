#!/usr/bin/env Rscript

## =========================================================
## run_all.R — Robyn training + 3-month forecast (resilient)
##  - Sets GCS bucket BEFORE any upload
##  - Synthesizes date if missing (daily index)
##  - Uploads robyn_console.log at checkpoints
##  - Preserved all original features
##  - OPTION A: Manually attach HPs to InputCollect$hyperparameters
##  - Also probes robyn_inputs() to capture exact validation errors
##  - Adds sanitizing + factor diagnostics/fixes for dt_input
## =========================================================

## ---------- ENV ----------
Sys.setenv(
  RETICULATE_PYTHON        = "/usr/bin/python3",
  RETICULATE_AUTOCONFIGURE = "0",
  TZ                       = "Europe/Berlin",
  R_MAX_CORES              = Sys.getenv("R_MAX_CORES", "32"),
  OMP_NUM_THREADS          = Sys.getenv("OMP_NUM_THREADS", "32"),
  OPENBLAS_NUM_THREADS     = Sys.getenv("OPENBLAS_NUM_THREADS", "32")
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
  library(tidyselect)
  library(tibble)
})

HAVE_FORECAST <- requireNamespace("forecast", quietly = TRUE)
max_cores <- as.numeric(Sys.getenv("R_MAX_CORES", "32"))
plan(multisession, workers = max_cores)

`%||%` <- function(a, b) {
  if (is.null(a) || length(a) == 0 || (is.character(a) && length(a) == 1 && !nzchar(a)) || all(is.na(a))) b else a
}
to_scalar <- function(x) {
  x <- suppressWarnings(as.numeric(x))
  if (!length(x)) {
    return(NA_real_)
  }
  if (length(x) > 1) {
    return(sum(x, na.rm = TRUE))
  }
  x
}

## ---------- LOGGING ----------
ts_now <- function() format(Sys.time(), "%Y-%m-%d %H:%M:%S")
logf <- function(..., .sep = "") cat(ts_now(), " | ", paste0(..., collapse = .sep), "\n", sep = "")
log_kv <- function(lst, indent = "  ") for (k in names(lst)) logf(indent, k, ": ", as.character(lst[[k]]))
log_head <- function(df, n = 5) {
  logf("Preview (", n, " rows):")
  utils::capture.output(print(utils::head(df, n))) |>
    paste(collapse = "\n") |>
    logf()
}

## ---------- GCS AUTH & BUCKET (set BEFORE any upload) ----------
options(googleAuthR.scopes.selected = "https://www.googleapis.com/auth/devstorage.read_write")
ensure_gcs_auth <- local({
  authed <- FALSE
  function() {
    if (authed) {
      return(invisible(TRUE))
    }
    creds <- Sys.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if (nzchar(creds) && file.exists(creds)) {
      logf("Auth      | Using JSON key at ", creds)
      googleCloudStorageR::gcs_auth(json_file = creds)
    } else {
      logf("Auth      | Using GCE metadata token")
      googleAuthR::gar_gce_auth(scopes = "https://www.googleapis.com/auth/devstorage.read_write")
      googleCloudStorageR::gcs_auth(token = googleAuthR::gar_token())
    }
    authed <<- TRUE
    invisible(TRUE)
  }
})

ensure_bucket <- function(pref = NULL) {
  ensure_gcs_auth()
  b <- pref %||% Sys.getenv("GCS_BUCKET", "mmm-app-output")
  googleCloudStorageR::gcs_global_bucket(b)
  options(googleCloudStorageR.predefinedAcl = "bucketLevel")
  logf("Bucket    | set to ", b)
  invisible(b)
}

## ---------- Helper: push current log to GCS (best-effort) ----------
push_log <- function() {
  try(
    {
      flush.console()
      if (exists("log_file", inherits = TRUE) &&
        exists("gcs_prefix", inherits = TRUE)) {
        lf <- get("log_file", inherits = TRUE)
        gp <- get("gcs_prefix", inherits = TRUE)
        if (is.character(lf) && nzchar(lf) && file.exists(lf) &&
          is.character(gp) && nzchar(gp)) {
          logf("Log       | uploading robyn_console.log …")
          gcs_put_safe(lf, file.path(gp, "robyn_console.log"))
        } else {
          logf("Log       | skip upload (log_file or gcs_prefix missing/empty)")
        }
      } else {
        logf("Log       | skip upload (symbols not found)")
      }
    },
    silent = TRUE
  )
}

## ---------- GCS helpers ----------
gcs_download <- function(gcs_path, local_path) {
  stopifnot(grepl("^gs://", gcs_path))
  bits <- strsplit(sub("^gs://", "", gcs_path), "/", fixed = TRUE)[[1]]
  bucket <- bits[1]
  object <- paste(bits[-1], collapse = "/")
  logf("GCS GET  | bucket=", bucket, " object=", object)
  googleCloudStorageR::gcs_get_object(object_name = object, bucket = bucket, saveToDisk = local_path, overwrite = TRUE)
  if (!file.exists(local_path)) stop("Failed to download: ", gcs_path)
  logf("GCS GET  | saved to ", local_path, " (", format(file.info(local_path)$size, big.mark = ","), " bytes)")
}
gcs_put <- function(local_file, object_path, upload_type = c("simple", "resumable")) {
  upload_type <- match.arg(upload_type)
  lf <- normalizePath(local_file, mustWork = FALSE)
  if (!file.exists(lf)) stop("Local file does not exist: ", lf)
  if (grepl("^gs://", object_path)) stop("object_path must be a key, not gs://")
  bkt <- googleCloudStorageR::gcs_get_global_bucket()
  if (is.null(bkt) || bkt == "") stop("No bucket set: call gcs_global_bucket(...)")
  typ <- mime::guess_type(lf)
  if (is.na(typ) || typ == "") typ <- "application/octet-stream"
  logf("GCS PUT  | bucket=", bkt, " name=", object_path, " type=", typ, " size=", format(file.info(lf)$size, big.mark = ","))
  googleCloudStorageR::gcs_upload(file = lf, name = object_path, bucket = bkt, type = typ, upload_type = upload_type, predefinedAcl = "bucketLevel")
  invisible(TRUE)
}
gcs_put_safe <- function(...) {
  tryCatch(
    {
      gcs_put(...)
      logf("GCS PUT  | success")
    },
    error = function(e) {
      logf("GCS PUT  | FAILED: ", conditionMessage(e))
    }
  )
}

## ---------- Utility helpers ----------
should_add_n_searches <- function(dtf, spend_cols, thr = 0.15) {
  if (!"N_SEARCHES" %in% names(dtf) || length(spend_cols) == 0) {
    return(FALSE)
  }
  ts <- rowSums(dtf[, spend_cols, drop = FALSE], na.rm = TRUE)
  cval <- suppressWarnings(abs(cor(dtf$N_SEARCHES, ts, use = "complete.obs")))
  isTRUE(!is.na(cval) && cval < thr)
}
fill_day <- function(x) {
  rng <- paste0(as.character(min(x$date, na.rm = TRUE)), " → ", as.character(max(x$date, na.rm = TRUE)))
  all <- tibble(date = seq(min(x$date, na.rm = TRUE), max(x$date, na.rm = TRUE), by = "day"))
  full <- dplyr::left_join(all, x, by = "date")
  num <- names(full)[sapply(full, is.numeric)]
  miss <- sum(is.na(full[num]))
  full[num] <- lapply(full[num], function(v) tidyr::replace_na(v, 0))
  logf("Fill day  | range=", rng, " before=", nrow(x), " after=", nrow(full), " NAs→0=", miss)
  full
}
safe_parse_numbers <- function(df, cols) {
  cols <- intersect(cols, names(df))
  if (length(cols)) logf("ParseNum  | ", paste(cols, collapse = ", "))
  for (cl in cols) {
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
safe_write_csv <- function(df, path) {
  tryCatch(
    {
      readr::write_csv(df, path, na = "")
      logf("WRITE CSV | ", path, " (", format(file.info(path)$size, big.mark = ","), " bytes)")
    },
    error = function(e) logf("WRITE CSV | FAILED ", path, ": ", conditionMessage(e))
  )
}

## ---------- Robust date detection / synthesis ----------
synthesize_date <- function(d, end_date = NULL) {
  n <- nrow(d)
  if (!n) stop("Empty dataset; cannot synthesize dates.")
  end_date <- as.Date(end_date %||% Sys.Date())
  d$date <- seq(end_date - (n - 1), end_date, by = "day")
  logf("Date      | synthesized daily index ending ", as.character(end_date), " (n=", n, ")")
  d
}
choose_or_make_date <- function(d, preferred, end_date_hint) {
  upref <- toupper(preferred)
  if (upref %in% names(d)) {
    if (inherits(d[[upref]], "POSIXt")) d$date <- as.Date(d[[upref]]) else d$date <- as.Date(as.character(d[[upref]]))
    d[[upref]] <- NULL
    return(d)
  }
  is_dateish <- vapply(d, function(x) inherits(x, "Date") || inherits(x, "POSIXt"), logical(1))
  cand <- names(d)[is_dateish]
  if (length(cand) == 1L) {
    if (inherits(d[[cand]], "POSIXt")) d$date <- as.Date(d[[cand]]) else d$date <- as.Date(as.character(d[[cand]]))
    d[[cand]] <- NULL
    return(d)
  }
  for (nm in c("DATE", "DT", "DAY", "DS")) {
    if (nm %in% names(d)) {
      if (inherits(d[[nm]], "POSIXt")) d$date <- as.Date(d[[nm]]) else d$date <- as.Date(as.character(d[[nm]]))
      d[[nm]] <- NULL
      return(d)
    }
  }
  logf("Date      | no date-like column found; synthesizing…")
  synthesize_date(d, end_date = end_date_hint)
}

filter_by_country <- function(dx, country) {
  cn <- toupper(country)
  for (col in c("COUNTRY", "COUNTRY_CODE", "MARKET", "COUNTRY_ISO", "LOCALE")) {
    if (col %in% names(dx)) {
      vals <- unique(toupper(dx[[col]]))
      if (cn %in% vals) {
        logf("Filter    | ", col, " == ", cn, " (", sum(toupper(dx[[col]]) == cn), " rows)")
        dx <- dx[toupper(dx[[col]]) == cn, , drop = FALSE]
        break
      }
    }
  }
  dx
}

## ---------- Ledger ----------
get_ledger_object <- function() Sys.getenv("JOBS_LEDGER_OBJECT", "robyn-jobs/ledger.csv")
append_to_ledger <- function(row) {
  ensure_gcs_auth()
  ledger_obj <- get_ledger_object()
  tmp <- file.path(tempdir(), "jobs_ledger.csv")
  ok <- tryCatch(
    {
      googleCloudStorageR::gcs_get_object(
        object_name = ledger_obj, bucket = googleCloudStorageR::gcs_get_global_bucket(),
        saveToDisk = tmp, overwrite = TRUE
      )
      TRUE
    },
    error = function(e) FALSE
  )
  df_old <- if (ok && file.exists(tmp)) try(readr::read_csv(tmp, show_col_types = FALSE), silent = TRUE) else NULL
  if (inherits(df_old, "try-error")) df_old <- NULL
  df_new <- as.data.frame(row, stringsAsFactors = FALSE)
  out <- if (!is.null(df_old) && nrow(df_old)) {
    if ("job_id" %in% names(df_old)) df_old <- df_old[df_old$job_id != row$job_id, , drop = FALSE]
    dplyr::bind_rows(df_old, df_new)
  } else {
    df_new
  }
  if ("start_time" %in% names(out)) out <- out[order(as.POSIXct(out$start_time), decreasing = TRUE), , drop = FALSE]
  readr::write_csv(out, tmp, na = "")
  gcs_put_safe(tmp, ledger_obj)
  logf("Ledger    | upserted job row")
}

## ---------- Forecast helpers ----------
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
      month = floor_date(date, "month"),
      daily_total = rowSums(across(all_of(spend_cols)), na.rm = TRUE)
    ) %>%
    filter(month < floor_date(last_day, "month")) %>%
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
weekday_profile <- function(vals, dates) {
  n <- length(vals)
  k <- min(n, 8 * 7)
  tail_vals <- tail(vals, k)
  tail_dates <- tail(dates, k)
  df <- tibble(dow = wday(tail_dates, label = TRUE, week_start = 1), val = pmax(tail_vals, 0))
  props <- df |>
    group_by(dow) |>
    summarise(s = sum(val, na.rm = TRUE), .groups = "drop")
  if (sum(props$s) <= 0) props$w <- 1 / 7 else props$w <- props$s / sum(props$s)
  structure(props$w, names = as.character(props$dow))
}
build_spend_forecast <- function(dt_input, spend_cols, horizon_months = 3, monthly_targets = NULL) {
  stopifnot("date" %in% names(dt_input))
  hist <- dt_input %>% arrange(date)
  start_next <- max(hist$date, na.rm = TRUE) + 1
  start_month <- floor_date(start_next, "month")
  if (start_month < start_next) start_month <- start_month %m+% months(1)
  end_month <- start_month %m+% months(horizon_months) - days(1)
  future_days <- tibble(
    date = seq(start_month, end_month, by = "day"),
    dow = wday(date, label = TRUE, week_start = 1)
  )

  fc_list <- list()
  for (v in spend_cols) {
    x <- hist[[v]]
    x[is.na(x)] <- 0
    wk <- hist |>
      mutate(year = isoyear(date), week = isoweek(date)) |>
      group_by(year, week) |>
      summarise(val = sum(.data[[v]], na.rm = TRUE), .groups = "drop") |>
      arrange(year, week)
    if (nrow(wk) < 8) {
      weekly_future <- rep(mean(tail(wk$val, min(4, nrow(wk))), na.rm = TRUE), ceiling(horizon_months * 4.5))
    } else {
      if (HAVE_FORECAST) {
        ts_w <- stats::ts(wk$val, frequency = 52)
        fit <- try(suppressWarnings(forecast::auto.arima(ts_w, stepwise = FALSE, approximation = FALSE)), silent = TRUE)
        if (inherits(fit, "try-error")) fit <- forecast::ets(ts_w)
        weekly_future <- as.numeric(forecast::forecast(fit, h = ceiling(horizon_months * 4.5))$mean)
      } else {
        m <- stats::filter(wk$val, rep(1 / 4, 4), sides = 1)
        weekly_future <- rep(tail(na.omit(as.numeric(m)), 1) %||% mean(wk$val, na.rm = TRUE), ceiling(horizon_months * 4.5))
      }
      weekly_future[weekly_future < 0] <- 0
    }
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
  if (!is.null(monthly_targets)) {
    stopifnot(length(monthly_targets) == horizon_months)
    out <- out |> mutate(month = floor_date(date, "month"))
    months_vec <- seq(floor_date(min(out$date), "month"), by = "1 month", length.out = horizon_months)
    for (i in seq_along(months_vec)) {
      m <- months_vec[i]
      idx <- which(out$month == m)
      cur <- sum(as.matrix(out[idx, spend_cols, drop = FALSE]), na.rm = TRUE)
      tgt <- monthly_targets[i]
      if (is.finite(tgt) && tgt > 0 && is.finite(cur) && cur > 0) out[idx, spend_cols] <- out[idx, spend_cols] * (tgt / cur)
    }
    out <- dplyr::select(out, -month)
  }
  out
}
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
    driver_cols <- intersect(c(InputCollect$paid_media_vars, InputCollect$organic_vars, InputCollect$context_vars, InputCollect$factor_vars), nm)
    if (!length(driver_cols)) {
      base_series <- decomp[[dep_col[1]]]
    } else {
      base_series <- decomp[[dep_col[1]]] - rowSums(decomp[, driver_cols, drop = FALSE], na.rm = TRUE)
    }
  }
  mean(tail(base_series, lookback_days), na.rm = TRUE)
}
make_share_bands <- function(shares, tol = 1e-4) {
  shares[is.na(shares)] <- 0
  lo <- pmax(0, shares - tol)
  up <- pmin(1, shares + tol)
  list(low = lo, up = up)
}

## ========== RUN ==========
logf("Stage     | Auth & early bucket")
ensure_gcs_auth()
early_bucket <- ensure_bucket()

revision <- "unknown"
country <- "xx"
timestamp <- format(Sys.time(), "%m%d_%H%M%S")
dir_path <- path.expand(file.path("~/budget/datasets", revision, country, timestamp))
dir.create(dir_path, recursive = TRUE, showWarnings = FALSE)
gcs_prefix <- file.path("robyn", revision, country, timestamp)

## Start log file ASAP
log_file <- file.path(dir_path, "robyn_console.log")
dir.create(dirname(log_file), recursive = TRUE, showWarnings = FALSE)
log_con_out <- file(log_file, open = "wt")
log_con_err <- file(log_file, open = "at")
sink(log_con_out, split = TRUE)
sink(log_con_err, type = "message")

on.exit(
  {
    try(sink(type = "message"), silent = TRUE)
    try(sink(), silent = TRUE)
    try(close(log_con_err), silent = TRUE)
    try(close(log_con_out), silent = TRUE)
    push_log()
  },
  add = TRUE
)

## Error handler
job_started <- Sys.time()
status_json <- file.path(dir_path, "status.json")
options(error = function(e) {
  message("FATAL ERROR: ", conditionMessage(e))
  try(
    {
      writeLines(jsonlite::toJSON(list(
        state = "FAILED", start_time = as.character(job_started),
        end_time = as.character(Sys.time()), error = conditionMessage(e)
      ), auto_unbox = TRUE), status_json)
      if (nzchar(gcs_prefix)) gcs_put_safe(status_json, file.path(gcs_prefix, "status.json"))
      push_log()
    },
    silent = TRUE
  )
  quit(status = 1)
})

## Write RUNNING status
writeLines(jsonlite::toJSON(list(state = "RUNNING", start_time = as.character(job_started)), auto_unbox = TRUE), status_json)
gcs_put_safe(status_json, file.path(gcs_prefix, "status.json"))
push_log()

## ---------- Load cfg ----------
logf("Stage     | Load configuration")
cfg_path <- Sys.getenv("JOB_CONFIG_GCS_PATH", "")
if (!nzchar(cfg_path)) cfg_path <- sprintf("gs://%s/training-configs/latest/job_config.json", googleCloudStorageR::gcs_get_global_bucket())
logf("CFG       | JOB_CONFIG_GCS_PATH=", cfg_path)
tmp_cfg <- tempfile(fileext = ".json")
gcs_download(cfg_path, tmp_cfg)
cfg <- jsonlite::fromJSON(tmp_cfg)

## Update identifiers/paths
country <- cfg$country
revision <- cfg$revision
timestamp <- cfg$timestamp %||% timestamp
dir_path <- path.expand(file.path("~/budget/datasets", revision, country, timestamp))
dir.create(dir_path, recursive = TRUE, showWarnings = FALSE)
gcs_prefix <- file.path("robyn", revision, country, timestamp)

## Switch bucket if cfg says so
if (!is.null(cfg$gcs_bucket) && nzchar(cfg$gcs_bucket) && cfg$gcs_bucket != googleCloudStorageR::gcs_get_global_bucket()) {
  logf("Bucket    | switching to cfg$gcs_bucket=", cfg$gcs_bucket)
  ensure_bucket(cfg$gcs_bucket)
}
push_log()

## ---------- Params ----------
iter <- as.numeric(cfg$iterations)
trials <- as.numeric(cfg$trials)
train_size <- as.numeric(cfg$train_size)
date_input <- cfg$date_input
dep_var <- toupper(cfg$dep_var %||% "UPLOAD_VALUE")
adstock <- tolower(cfg$adstock %||% "geometric")
date_col_in <- toupper(cfg$date_var %||% "DATE")

logf("Params    |")
log_kv(list(
  iter = iter, trials = trials, country = country, revision = revision, date_input = date_input,
  train_size_cfg = paste(train_size, collapse = ","), max_cores = max_cores, dep_var = dep_var,
  adstock = adstock, bucket = googleCloudStorageR::gcs_get_global_bucket(), gcs_prefix = gcs_prefix
))
push_log()

## ---------- Load data ----------
if (is.null(cfg$data_gcs_path) || !nzchar(cfg$data_gcs_path)) stop("No data_gcs_path provided in configuration.")
logf("Stage     | Download data: ", cfg$data_gcs_path)
tmp_data <- tempfile(fileext = ".parquet")
gcs_download(cfg$data_gcs_path, tmp_data)
df <- arrow::read_parquet(tmp_data, as_data_frame = TRUE)
unlink(tmp_data)
df <- as.data.frame(df)
names(df) <- toupper(names(df))
logf("Data      | Loaded rows=", nrow(df), " cols=", ncol(df))
log_head(df, 5)
logf("Columns   | ", paste(names(df), collapse = ", "))
push_log()

## ---------- Date normalize (with synthesis if needed) ----------
end_hint <- as.Date(date_input %||% Sys.Date())
df <- choose_or_make_date(df, preferred = date_col_in, end_date_hint = end_hint)
if (anyNA(df$date)) {
  nbad <- sum(is.na(df$date))
  logf("Date      | removing ", nbad, " rows with NA date after coercion")
  df <- df[!is.na(df$date), , drop = FALSE]
}
logf("Date      | range: ", as.character(min(df$date)), " → ", as.character(max(df$date)), " rows=", nrow(df))
push_log()

## ---------- Country filter / dedup / fill ----------
rows_pre <- nrow(df)
df <- filter_by_country(df, country)
logf("Filter    | country rows: ", rows_pre, " -> ", nrow(df))
if (anyDuplicated(df$date)) {
  dup <- sum(duplicated(df$date))
  logf("Dedup     | collapsing duplicated dates: ", dup)
  sum_or_first <- function(x) if (is.numeric(x)) sum(x, na.rm = TRUE) else dplyr::first(x)
  df <- df %>%
    group_by(date) %>%
    summarise(across(!all_of("date"), sum_or_first), .groups = "drop")
}
df <- df[order(df$date), , drop = FALSE]
df <- fill_day(df)
push_log()

## ---------- Types / zero-var / features ----------
cost_cols <- union(grep("_COST$", names(df), value = TRUE), grep("_COSTS$", names(df), value = TRUE))
df <- safe_parse_numbers(df, cost_cols)
num_cols <- setdiff(names(df), "date")
zero_var <- num_cols[sapply(df[num_cols], function(x) is.numeric(x) && dplyr::n_distinct(x, na.rm = TRUE) <= 1)]
if (length(zero_var)) {
  logf("ZeroVar   | dropping: ", paste(zero_var, collapse = ", "))
  df <- df[, !(names(df) %in% zero_var), drop = FALSE]
}
if (!"TV_IS_ON" %in% names(df)) {
  df$TV_IS_ON <- 0
  logf("Feature   | init TV_IS_ON=0")
}

df <- df %>% mutate(
  GA_OTHER_COST          = rowSums(select(., matches("^GA_.*_COST$") & !any_of(c("GA_SUPPLY_COST", "GA_BRAND_COST", "GA_DEMAND_COST"))), na.rm = TRUE),
  BING_TOTAL_COST        = rowSums(select(., matches("^BING_.*_COST$")), na.rm = TRUE),
  META_TOTAL_COST        = rowSums(select(., matches("^META_.*_COST$")), na.rm = TRUE),
  ORGANIC_TRAFFIC        = rowSums(select(., any_of(c("NL_DAILY_SESSIONS", "SEO_DAILY_SESSIONS", "DIRECT_DAILY_SESSIONS", "TV_DAILY_SESSIONS", "CRM_OTHER_DAILY_SESSIONS", "CRM_DAILY_SESSIONS"))), na.rm = TRUE),
  BRAND_HEALTH           = coalesce(DIRECT_DAILY_SESSIONS, 0) + coalesce(SEO_DAILY_SESSIONS, 0),
  ORGxTV                 = BRAND_HEALTH * coalesce(TV_COST, 0),
  GA_OTHER_IMPRESSIONS   = rowSums(select(., matches("^GA_.*_IMPRESSIONS$") & !any_of(c("GA_SUPPLY_IMPRESSIONS", "GA_BRAND_IMPRESSIONS", "GA_DEMAND_IMPRESSIONS"))), na.rm = TRUE),
  BING_TOTAL_IMPRESSIONS = rowSums(select(., matches("^BING_.*_IMPRESSIONS$")), na.rm = TRUE),
  META_TOTAL_IMPRESSIONS = rowSums(select(., matches("^META_.*_IMPRESSIONS$")), na.rm = TRUE),
  BING_TOTAL_CLICKS      = rowSums(select(., matches("^BING_.*_CLICKS$")), na.rm = TRUE),
  META_TOTAL_CLICKS      = rowSums(select(., matches("^META_.*_CLICKS$")), na.rm = TRUE)
)
logf("Feature   | engineered columns added")
push_log()

## ---------- Window + flags ----------
end_data_date <- max(df$date, na.rm = TRUE)
start_target <- as.Date("2024-01-01")
df_win <- df %>% filter(date >= start_target, date <= end_data_date)
logf("Window    | requested ", as.character(start_target), " → ", as.character(end_data_date), " rows=", nrow(df_win))
if (nrow(df_win) == 0) {
  logf("Window    | fallback to full range due to 0 rows")
  df_win <- df
}
if (nrow(df_win) < 90) stop("Too few rows after windowing (", nrow(df_win), "). Need at least 90 daily rows.")
df <- df_win
df$DOW <- wday(df$date, label = TRUE)
df$IS_WEEKEND <- ifelse(df$DOW %in% c("Sat", "Sun"), 1, 0)
logf("Window    | USED ", as.character(min(df$date)), " → ", as.character(max(df$date)), " rows=", nrow(df))
push_log()

# after creating df$IS_WEEKEND / df$TV_IS_ON
df$IS_WEEKEND <- as.integer(df$IS_WEEKEND)
df$TV_IS_ON <- as.integer(df$TV_IS_ON > 0)

context_vars <- union(
  c("IS_WEEKEND", "TV_IS_ON"),
  intersect(cfg$context_vars %||% character(0), names(df))
)

# keep factor_vars empty for the clean test:
factor_vars <- character(0)

## ---------- Drivers ----------
paid_media_spends <- intersect(cfg$paid_media_spends, names(df))
paid_media_vars <- intersect(cfg$paid_media_vars, names(df))
stopifnot(length(paid_media_spends) == length(paid_media_vars))
keep_idx <- vapply(seq_along(paid_media_spends), function(i) sum(df[[paid_media_spends[i]]], na.rm = TRUE) > 0, logical(1))
if (any(!keep_idx)) logf("Drivers   | dropping zero-spend: ", paste(paid_media_spends[!keep_idx], collapse = ", "))
paid_media_spends <- paid_media_spends[keep_idx]
paid_media_vars <- paid_media_vars[keep_idx]
context_vars <- intersect(cfg$context_vars %||% character(0), names(df))
factor_vars <- intersect(cfg$factor_vars %||% character(0), names(df))
org_base <- intersect(cfg$organic_vars %||% "ORGANIC_TRAFFIC", names(df))
organic_vars <- if (should_add_n_searches(df, paid_media_spends) && "N_SEARCHES" %in% names(df)) unique(c(org_base, "N_SEARCHES")) else org_base
logf("Drivers   |")
log_kv(list(
  paid_media_spends = paste(paid_media_spends, collapse = ", "),
  paid_media_vars   = paste(paid_media_vars, collapse = ", "),
  context_vars      = paste(context_vars, collapse = ", "),
  factor_vars       = paste(factor_vars, collapse = ", "),
  organic_vars      = paste(organic_vars, collapse = ", ")
))
push_log()

## === SANITIZE/FACTOR DIAGNOSTICS ===
# Helpers to detect & fix invalid factor codes (0, <0, >nlevels)
check_factor <- function(x) {
  if (!is.factor(x)) {
    return(list(is_bad = FALSE, reason = NA_character_))
  }
  ix <- suppressWarnings(as.integer(x))
  bad_codes <- ix[!is.na(ix) & (ix <= 0L | ix > nlevels(x))]
  if (length(bad_codes)) {
    list(
      is_bad = TRUE,
      reason = sprintf(
        "codes outside [1,%d], e.g. %s",
        nlevels(x),
        paste(utils::head(unique(bad_codes), 5), collapse = ",")
      )
    )
  } else {
    list(is_bad = FALSE, reason = NA_character_)
  }
}
scan_invalid_factors <- function(df) {
  res <- lapply(df, check_factor)
  bad <- names(df)[vapply(res, `[[`, logical(1), "is_bad")]
  list(bad_cols = bad, meta = res)
}
relevel_safe <- function(x) {
  if (!is.factor(x)) {
    return(x)
  }
  factor(as.character(x), levels = sort(unique(as.character(x))), ordered = is.ordered(x))
}
fix_invalid_factors <- function(df, keep_as_factor = character(0)) {
  # Rebuild declared factor_vars as factors; turn all other factors into character (Robyn OHEs internally)
  df[] <- lapply(seq_along(df), function(i) {
    nm <- names(df)[i]
    col <- df[[i]]
    if (is.factor(col)) {
      if (nm %in% keep_as_factor) {
        return(relevel_safe(col))
      }
      return(as.character(col))
    }
    col
  })
  df
}
sanitize_for_robyn <- function(df_full, dep_var, paid_media_spends, paid_media_vars, context_vars, factor_vars, organic_vars) {
  vars_keep <- unique(c(
    "date", dep_var,
    paid_media_spends, paid_media_vars,
    context_vars, factor_vars,
    organic_vars
  ))
  vars_keep <- intersect(vars_keep, names(df_full))
  d <- df_full[, vars_keep, drop = FALSE]

  # Prefer character over factor (except declared factor_vars, which we rebuild)
  d <- fix_invalid_factors(d, keep_as_factor = factor_vars)

  # One extra pass: check bad factors remain?
  sc <- scan_invalid_factors(d)
  if (length(sc$bad_cols)) {
    for (bc in sc$bad_cols) logf("Integrity | BAD factor '", bc, "' → ", sc$meta[[bc]]$reason)
    stop("Invalid factor codes remain after sanitization in columns: ", paste(sc$bad_cols, collapse = ", "))
  }
  d
}

## ---------- Inputs (BASE, no HPs) ----------
if (!(dep_var %in% names(df))) stop("Dependent variable '", dep_var, "' not found in data.")
prophet_vars_used <- c("trend", "season", "holiday", "weekday")
if (!requireNamespace("prophet", quietly = TRUE)) {
  logf("Prophet    | package not available → disabling prophet_vars")
  prophet_vars_used <- NULL
}

prophet_vars_used <- NULL # TEMP: avoid prophet merge branch while we debug

# SANITIZE before robyn_inputs() and keep all declared features
df_for_robyn <- sanitize_for_robyn(
  df_full = df,
  dep_var = dep_var,
  paid_media_spends = paid_media_spends,
  paid_media_vars = paid_media_vars,
  context_vars = context_vars,
  factor_vars = factor_vars,
  organic_vars = organic_vars
)
logf("Integrity | dt_input columns: ", paste(names(df_for_robyn), collapse = ", "))

InputCollect_base <- robyn_inputs(
  dt_input = df_for_robyn,
  date_var = "date",
  dep_var = dep_var,
  dep_var_type = "revenue",
  adstock = adstock,
  prophet_vars = prophet_vars_used,
  prophet_country = toupper(country),
  paid_media_spends = paid_media_spends,
  paid_media_vars = paid_media_vars,
  context_vars = context_vars,
  factor_vars = factor_vars,
  organic_vars = organic_vars,
  window_start = min(df_for_robyn$date),
  window_end = max(df_for_robyn$date)
)
# Pick a safe single train_size that guarantees >= 28 validation days
ser_len <- nrow(InputCollect_base$dt_input)
safe_train <- min(0.90, (ser_len - 28) / ser_len - 1e-6)
safe_train <- max(0.70, safe_train) # clamp to [0.70, 0.90]
InputCollect_base$hyperparameters$train_size <- c(safe_train, safe_train)

# Post-creation integrity check on InputCollect$dt_input
logf("Integrity | scanning InputCollect$dt_input for invalid factors")
ic_scan <- scan_invalid_factors(InputCollect_base$dt_input)
if (length(ic_scan$bad_cols)) {
  for (bc in ic_scan$bad_cols) {
    logf("Integrity | BAD factor inside InputCollect$dt_input '", bc, "' → ", ic_scan$meta[[bc]]$reason)
  }
  # Hard fail with clear info
  ex <- utils::capture.output(str(InputCollect_base$dt_input[ic_scan$bad_cols], give.attr = FALSE, strict.width = "cut"))
  stop(paste0(
    "Invalid factor codes detected in InputCollect$dt_input: ",
    paste(ic_scan$bad_cols, collapse = ", "),
    "\nThese columns have factor codes outside their levels. They were sanitized prior to robyn_inputs(), ",
    "so this likely comes from upstream corruption. Inspect the printed structure below:\n",
    paste(ex, collapse = "\n")
  ))
}

logf("Inputs    | dep_var=", InputCollect_base$dep_var, " adstock=", InputCollect_base$adstock, " rows=", nrow(InputCollect_base$dt_input))
logf("Inputs    | window ", as.character(InputCollect_base$window_start), " → ", as.character(InputCollect_base$window_end))
alloc_end <- max(InputCollect_base$dt_input$date)
alloc_start <- alloc_end - 364
logf("AllocWin  | ", as.character(alloc_start), " → ", as.character(alloc_end))
push_log()

## ---------- Hyperparameters (build) ----------
hyper_vars <- c(paid_media_vars, organic_vars)
hyperparameters <- list()
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
for (v in hyper_vars) {
  spec <- mk_hp(v)
  hyperparameters[[paste0(v, "_alphas")]] <- spec$alphas
  hyperparameters[[paste0(v, "_gammas")]] <- spec$gammas
  hyperparameters[[paste0(v, "_thetas")]] <- spec$thetas
}
ser_len <- nrow(InputCollect_base$dt_input)
safe_train <- min(0.90, (ser_len - 28) / ser_len - 1e-6)
safe_train <- max(0.80, safe_train) # a bit tighter than 0.70–0.90 while debugging

# When you construct `hyperparameters`, do THIS:
hyperparameters[["train_size"]] <- c(safe_train, safe_train) # <- overwrite cfg range

expect_keys <- c(
  as.vector(outer(c(paid_media_vars, organic_vars), c("_alphas", "_gammas", "_thetas"), paste0)),
  "train_size"
)
missing <- setdiff(expect_keys, names(hyperparameters))
extra <- setdiff(names(hyperparameters), expect_keys)
logf("HP        | vars=", length(hyper_vars), " keys=", length(names(hyperparameters)), " missing=", length(missing), " extra=", length(extra))
if (length(missing)) stop("Missing HP keys: ", paste(missing, collapse = ", "))
if (length(extra)) stop("Extra HP keys (remove them): ", paste(extra, collapse = ", "))

## ---------- HP diagnostics + robyn_inputs() PROBE (to catch real error) ----------
hp_diag_path <- file.path(dir_path, "robyn_hp_diagnostics.txt")
capture_msgs <- character()
capture_warn <- character()
append_msg <- function(x) capture_msgs <<- c(capture_msgs, x)
append_warn <- function(x) capture_warn <<- c(capture_warn, x)
write_diag <- function(extra = NULL) {
  lines <- c(
    "=== ROBYN HP DIAGNOSTICS ===",
    paste0("Time: ", as.character(Sys.time())), "",
    "-- Your hyperparameters (names):",
    paste0("  ", paste(sort(names(hyperparameters)), collapse = ", ")), "",
    "-- Paid media vars: ",
    paste0("  ", paste(paid_media_vars, collapse = ", ")),
    "-- Organic vars: ",
    paste0("  ", paste(organic_vars, collapse = ", ")), "",
    "-- Messages from robyn_inputs():",
    if (length(capture_msgs)) paste0("  ", capture_msgs) else "  <none>", "",
    "-- Warnings from robyn_inputs():",
    if (length(capture_warn)) paste0("  ", capture_warn) else "  <none>", "",
    extra %||% character(0)
  )
  writeLines(lines, hp_diag_path)
  gcs_put_safe(hp_diag_path, file.path(gcs_prefix, "robyn_hp_diagnostics.txt"))
  push_log()
}

hp_template <- try(robyn_hyper_params(InputCollect_base), silent = TRUE)
templ_names <- if (!inherits(hp_template, "try-error")) names(hp_template) else character(0)
your_names <- names(hyperparameters)

missing_in_yours <- setdiff(templ_names, your_names)
extra_in_yours <- setdiff(your_names, templ_names)

bad_shapes <- c()
bad_ranges <- c()
check_pair <- function(k, v) {
  if (!is.numeric(v) || length(v) != 2L || any(is.na(v))) {
    bad_shapes <<- c(bad_shapes, sprintf("%s (need numeric length-2)", k))
    return()
  }
  if (grepl("_alphas$", k) && any(v <= 0)) bad_ranges <<- c(bad_ranges, sprintf("%s <= 0", k))
  if (grepl("_gammas$|_thetas$", k) && any(v < 0 | v > 1)) bad_ranges <<- c(bad_ranges, sprintf("%s out of [0,1]", k))
  if (v[1] > v[2]) bad_ranges <<- c(bad_ranges, sprintf("%s min>max (%.3f>%.3f)", k, v[1], v[2]))
}
invisible(lapply(names(hyperparameters), function(k) check_pair(k, hyperparameters[[k]])))


write_diag(c(
  "== Preflight diff vs. Robyn template ==",
  paste0("Missing in your HPs: ", if (length(missing_in_yours)) paste(missing_in_yours, collapse = ", ") else "<none>"),
  paste0("Extra keys you provided: ", if (length(extra_in_yours)) paste(extra_in_yours, collapse = ", ") else "<none>"),
  paste0("Bad shapes: ", if (length(bad_shapes)) paste(bad_shapes, collapse = "; ") else "<none>"),
  paste0("Bad ranges/order: ", if (length(bad_ranges)) paste(bad_ranges, collapse = "; ") else "<none>")
))

invisible(withCallingHandlers(
  tryCatch(
    robyn_inputs(InputCollect = InputCollect_base, hyperparameters = hyperparameters),
    error = function(e) {
      write_diag(c(
        "== ERROR ==",
        paste0("Condition: ", conditionMessage(e)), "",
        "Traceback:",
        utils::capture.output(traceback(max.lines = 50L))
      ))
      stop(e)
    }
  ),
  message = function(m) append_msg(conditionMessage(m)),
  warning = function(w) {
    append_warn(conditionMessage(w))
    invokeRestart("muffleWarning")
  }
))

write_diag(c(
  "== SUCCESS ==",
  "robyn_inputs() accepted the provided hyperparameters (probe).",
  "Proceeding with OPTION A (manual attach to InputCollect$hyperparameters)."
))
logf("HP        | diagnostics written to ", hp_diag_path)

## ---------- OPTION A: Manually attach HPs to InputCollect and proceed ----------
InputCollect <- InputCollect_base
stopifnot(is.list(hyperparameters), length(hyperparameters) > 0)
InputCollect$hyperparameters <- hyperparameters

## Train-size sanity
ts_used <- InputCollect$hyperparameters$train_size
if (!is.numeric(ts_used) || length(ts_used) != 2L || any(is.na(ts_used))) {
  stop("train_size malformed inside hyperparameters; need numeric length-2.")
}
logf("HP        | train_size used: ", paste(ts_used, collapse = ","))

## Nonzero spends
nz_spend <- sapply(paid_media_spends, function(c) sum(InputCollect$dt_input[[c]], na.rm = TRUE))
logf("HP        | nonzero spend totals: ", paste(sprintf("%s=%.2f", names(nz_spend), nz_spend), collapse = "; "))
push_log()

## ---------- Train ----------
reticulate::use_python("/usr/bin/python3", required = TRUE)
cat("---- reticulate::py_config() ----\n")
print(reticulate::py_config())
cat("-------------------------------\n")
if (!reticulate::py_module_available("nevergrad")) stop("nevergrad not importable via reticulate.")
logf("Train     | start cores=", max_cores, " iter=", iter, " trials=", trials)
prev_plan <- future::plan()
on.exit(future::plan(prev_plan), add = TRUE)
future::plan(sequential)

# DV stats
dv <- df[[dep_var]]
logf(
  "DV Stats  | dep_var=", dep_var,
  " n=", length(dv),
  " mean=", round(mean(dv, na.rm = TRUE), 4),
  " sd=", round(sd(dv, na.rm = TRUE), 4),
  " min=", round(min(dv, na.rm = TRUE), 4),
  " max=", round(max(dv, na.rm = TRUE), 4)
)
if (all(is.na(dv)) || sd(dv, na.rm = TRUE) == 0) {
  stop("Dependent variable has zero variance or all NA after windowing; cannot train.")
}


# ==== EXTRA VALIDATION & SNAPSHOT BEFORE robyn_run ====
snap_path <- file.path(dir_path, "pre_run_snapshot.rds")
saveRDS(list(
  InputCollect = InputCollect,
  dep_var = dep_var,
  paid_media_spends = InputCollect$paid_media_spends,
  paid_media_vars = InputCollect$paid_media_vars,
  organic_vars = InputCollect$organic_vars,
  context_vars = InputCollect$context_vars,
  factor_vars = InputCollect$factor_vars,
  window = c(start = min(InputCollect$dt_input$date), end = max(InputCollect$dt_input$date))
), snap_path)
gcs_put_safe(snap_path, file.path(gcs_prefix, "pre_run_snapshot.rds"))

# Columns Robyn will try to select during run
sel_cols <- unique(na.omit(c(
  "date", dep_var,
  InputCollect$paid_media_vars,
  InputCollect$context_vars,
  InputCollect$factor_vars,
  InputCollect$organic_vars
)))
missing_cols <- setdiff(sel_cols, names(InputCollect$dt_input))
if (length(missing_cols)) {
  stop(
    "Pre-flight: columns missing from InputCollect$dt_input that robyn_run will select: ",
    paste(missing_cols, collapse = ", ")
  )
}

# Make sure each set is length > 0 (some Robyn paths assume non-empty)
if (!length(InputCollect$paid_media_vars)) stop("Pre-flight: paid_media_vars is empty")
if (!length(InputCollect$paid_media_spends)) stop("Pre-flight: paid_media_spends is empty")
# organic/context/factor can be empty, but log it
logf(
  "Preflight  | media_vars=", paste(InputCollect$paid_media_vars, collapse = ", "),
  " | org_vars=", paste(InputCollect$organic_vars %||% character(0), collapse = ", "),
  " | ctx_vars=", paste(InputCollect$context_vars %||% character(0), collapse = ", "),
  " | fac_vars=", paste(InputCollect$factor_vars %||% character(0), collapse = ", ")
)

# Train-size sanity vs. window (guards rolling windows)
ser_len <- nrow(InputCollect$dt_input)
tsz <- InputCollect$hyperparameters$train_size
if (any(tsz <= 0 | tsz >= 1)) stop("Pre-flight: train_size elements must be in (0,1), got: ", paste(tsz, collapse = ","))
if (ser_len < 180 && diff(range(tsz)) > 0.25) {
  logf(
    "⚠ Preflight | Very short series (", ser_len, " days) w/ wide train_size range (",
    paste(tsz, collapse = ","), ") may break rolling windows."
  )
}

# Pre-flight window
win_days <- as.integer(max(df$date) - min(df$date) + 1)
if (win_days < 90) stop("Training window too short: ", win_days, " days.")
if (!is.numeric(InputCollect$hyperparameters$train_size) || any(is.na(InputCollect$hyperparameters$train_size))) {
  stop("train_size is missing/NA inside hyperparameters. Aborting to avoid empty models.")
}

if (!is.null(InputCollect$prophet_vars) && is.null(InputCollect$dt_prophet)) {
  stop(
    "Prophet vars requested (", paste(InputCollect$prophet_vars, collapse = ","),
    ") but InputCollect$dt_prophet is NULL. Install 'prophet' or set prophet_vars=NULL."
  )
}

logf("Train     | start cores=", max_cores, " iter=", iter, " trials=", trials)


# ==== ROBYN RUN WITH TARGETED DIAGNOSTICS ====
OutputModels <- NULL # ensure symbol exists no matter what

run_once <- function(do_validation = TRUE) {
  withCallingHandlers(
    tryCatch(
      robyn_run(
        InputCollect       = InputCollect,
        iterations         = iter,
        trials             = trials,
        ts_validation      = FALSE, # do_validation,
        add_penalty_factor = TRUE,
        cores              = max_cores
      ),
      error = function(e) {
        attr(e, "backtrace") <- tryCatch(rlang::trace_back(), error = function(.) NULL)
        stop(e)
      }
    ),
    warning = function(w) invokeRestart("muffleWarning")
  )
}

run_err <- NULL
run_tb <- NULL
t0 <- Sys.time()
OutputModels <- tryCatch(
  run_once(do_validation = TRUE),
  error = function(e) {
    run_err <<- conditionMessage(e)
    bt <- tryCatch(capture.output(print(attr(e, "backtrace"))), error = function(.) NULL)
    run_tb <<- bt %||% utils::capture.output(traceback(max.lines = 50L))

    # Write diag then retry without validation
    diag_txt <- file.path(dir_path, "robyn_train_diagnostics.txt")
    writeLines(c(
      "robyn_run failed on validation path with a 'select(NULL, ...)' error or similar.",
      paste0("Condition: ", run_err),
      "---- rlang::trace_back() ----",
      run_tb %||% "<none>"
    ), diag_txt)
    gcs_put_safe(diag_txt, file.path(gcs_prefix, "robyn_train_diagnostics.txt"))
    logf("Train     | retrying with ts_validation=FALSE (diagnostic fallback)")

    # Retry without validation
    tryCatch(run_once(do_validation = FALSE), error = function(e2) {
      run_err <<- paste0(run_err, " | retry error: ", conditionMessage(e2))
      NULL
    })
  }
)
training_time <- as.numeric(difftime(Sys.time(), t0, units = "mins"))

if (is.null(OutputModels) || is.null(OutputModels$resultHypParam) || !NROW(OutputModels$resultHypParam)) {
  diag_txt <- file.path(dir_path, "robyn_train_diagnostics.txt")
  writeLines(c(
    "robyn_run produced no models.",
    paste0("robyn_run error: ", run_err %||% "<none>"),
    "Traceback:",
    run_tb %||% "<none>",
    paste("DV sd:", round(sd(df[[dep_var]], na.rm = TRUE), 6)),
    paste("Train window days:", as.integer(max(df$date) - min(df$date) + 1)),
    paste("train_size:", paste(InputCollect$hyperparameters$train_size, collapse = ",")),
    paste("Nonzero spend totals:", paste(sprintf("%s=%.2f", names(nz_spend), nz_spend), collapse = "; ")),
    paste("Paid media spends kept:", paste(paid_media_spends, collapse = ", ")),
    paste("Hyperparameter keys:", paste(names(hyperparameters), collapse = ", "))
  ), diag_txt)
  gcs_put_safe(diag_txt, file.path(gcs_prefix, "robyn_train_diagnostics.txt"))
  stop("robyn_run returned empty results (see robyn_train_diagnostics.txt).")
}

training_time <- as.numeric(difftime(Sys.time(), t0, units = "mins"))
logf("Train     | completed in ", round(training_time, 2), " minutes")

ok_models <- !is.null(OutputModels) &&
  is.list(OutputModels) &&
  !is.null(OutputModels$resultHypParam) &&
  NROW(OutputModels$resultHypParam) > 0

if (!ok_models) {
  diag_txt <- file.path(dir_path, "robyn_train_diagnostics.txt")
  lines <- c(
    "robyn_run produced no models.",
    if (!is.null(run_err)) paste0("robyn_run error: ", run_err) else "robyn_run error: <none>",
    paste("DV sd:", round(sd(df[[dep_var]], na.rm = TRUE), 6)),
    paste("Train window days:", win_days),
    paste("train_size:", paste(InputCollect$hyperparameters$train_size, collapse = ",")),
    paste("Nonzero spend totals:", paste(sprintf("%s=%.2f", names(nz_spend), nz_spend), collapse = "; ")),
    paste("Paid media spends kept:", paste(paid_media_spends, collapse = ", ")),
    paste("Hyperparameter keys:", paste(names(hyperparameters), collapse = ", "))
  )
  writeLines(lines, diag_txt)
  gcs_put_safe(diag_txt, file.path(gcs_prefix, "robyn_train_diagnostics.txt"))
  push_log()
  stop("robyn_run returned empty results (see robyn_train_diagnostics.txt).")
}

training_time <- as.numeric(difftime(Sys.time(), t0, units = "mins"))
logf("Train     | completed in ", round(training_time, 2), " minutes")
push_log()

## ---------- timings.csv ----------
timings_obj <- file.path(gcs_prefix, "timings.csv")
timings_local <- file.path(tempdir(), "timings.csv")
r_row <- data.frame(Step = "R training (robyn_run)", `Time (s)` = round(training_time * 60, 2), check.names = FALSE)
had_existing <- tryCatch(
  {
    googleCloudStorageR::gcs_get_object(
      object_name = timings_obj, bucket = googleCloudStorageR::gcs_get_global_bucket(),
      saveToDisk = timings_local, overwrite = TRUE
    )
    TRUE
  },
  error = function(e) FALSE
)
out <- if (had_existing && file.exists(timings_local)) {
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
push_log()

## ---------- Save core RDS ----------


om_path <- file.path(dir_path, "OutputModels.RDS")
ic_path <- file.path(dir_path, "InputCollect.RDS")
oc_path <- file.path(dir_path, "OutputCollect.RDS")

ok_models <- !is.null(OutputModels) &&
  is.list(OutputModels) &&
  !is.null(OutputModels$resultHypParam) &&
  NROW(OutputModels$resultHypParam) > 0

if (!ok_models) {
  diag_txt <- file.path(dir_path, "robyn_train_diagnostics.txt")
  add <- c(
    "robyn_run produced no models after retry.",
    paste("DV sd:", round(sd(df[[dep_var]], na.rm = TRUE), 6)),
    paste("Train window days:", as.integer(max(df$date) - min(df$date) + 1)),
    paste("train_size:", paste(InputCollect$hyperparameters$train_size, collapse = ",")),
    paste("Paid media spends kept:", paste(InputCollect$paid_media_spends, collapse = ", "))
  )
  cat(paste(add, collapse = "\n"), file = diag_txt, sep = "\n", append = TRUE)
  gcs_put_safe(diag_txt, file.path(gcs_prefix, "robyn_train_diagnostics.txt"))

  # Mark job as FAILED and stop before outputs/allocator/forecast that need models
  writeLines(jsonlite::toJSON(list(
    state = "FAILED", start_time = as.character(job_started),
    end_time = as.character(Sys.time()),
    error = "robyn_run returned empty results (see robyn_train_diagnostics.txt)"
  ), auto_unbox = TRUE), status_json)
  gcs_put_safe(status_json, file.path(gcs_prefix, "status.json"))
  push_log()
  stop("No models returned; aborted before outputs.")
}


tryCatch(saveRDS(OutputModels, om_path), error = function(e) logf("Save      | FAILED OutputModels.RDS: ", e$message))
tryCatch(saveRDS(InputCollect, ic_path), error = function(e) logf("Save      | FAILED InputCollect.RDS: ", e$message))

if (file.exists(om_path)) gcs_put_safe(om_path, file.path(gcs_prefix, "OutputModels.RDS")) else logf("Save      | MISSING ", om_path)
if (file.exists(ic_path)) gcs_put_safe(ic_path, file.path(gcs_prefix, "InputCollect.RDS")) else logf("Save      | MISSING ", ic_path)
push_log()

## ---------- Outputs & onepagers ----------
logf("Outputs   | robyn_outputs & onepagers")
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
writeLines(c(best_id, paste("Iterations:", iter), paste("Trials:", trials), paste("Training time (mins):", round(training_time, 2))),
  con = file.path(dir_path, "best_model_id.txt")
)
gcs_put_safe(file.path(dir_path, "best_model_id.txt"), file.path(gcs_prefix, "best_model_id.txt"))


top_models <- OutputCollect$resultHypParam$solID[1:min(3, nrow(OutputCollect$resultHypParam))]
for (m in top_models) try(robyn_onepagers(InputCollect, OutputCollect, select_model = m, export = TRUE), silent = TRUE)

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
}
push_log()

## ---------- Allocator overview ----------
is_brand <- InputCollect$paid_media_spends == "GA_BRAND_COST"
low_bounds <- ifelse(is_brand, 0, 0.3)
up_bounds <- ifelse(is_brand, 0, 4)
AllocatorCollect <- try(robyn_allocator(
  InputCollect = InputCollect, OutputCollect = OutputCollect, select_model = best_id,
  date_range = c(alloc_start, alloc_end), expected_spend = NULL, scenario = "max_historical_response",
  channel_constr_low = as.numeric(low_bounds), channel_constr_up = as.numeric(up_bounds), export = TRUE
), silent = TRUE)

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

write.csv(data.frame(
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
  allocator_total_spend    = round(total_spend %||% NA_real_, 2)
), metrics_csv, row.names = FALSE)
gcs_put_safe(metrics_csv, file.path(gcs_prefix, "allocator_metrics.csv"))

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
push_log()

## ---------- SMART 3-month forecast ----------
parse_nums <- function(x) as.numeric(unlist(strsplit(x, "[,;\\s]+")))
spend_cols <- InputCollect$paid_media_spends
monthly_override <- Sys.getenv("FORECAST_MONTHLY_BUDGETS", unset = "")
if (nzchar(monthly_override)) {
  b <- parse_nums(monthly_override)
  monthly_targets <- if (length(b) >= 3) b[1:3] else rep(b[1], 3)
  logf("Forecast  | monthly targets (ENV): ", paste(round(monthly_targets, 0), collapse = ", "))
} else {
  monthly_targets <- compute_monthly_targets_from_history(InputCollect$dt_input, spend_cols, horizon_months = 3)
  logf("Forecast  | monthly targets (HIST): ", paste(round(monthly_targets, 0), collapse = ", "))
}

future_spend <- build_spend_forecast(
  dt_input        = InputCollect$dt_input,
  spend_cols      = spend_cols,
  horizon_months  = 3,
  monthly_targets = monthly_targets
)
plan_path <- file.path(dir_path, "spend_plan_daily_next3m.csv")
safe_write_csv(future_spend, plan_path)
gcs_put_safe(plan_path, file.path(gcs_prefix, "spend_plan_daily_next3m.csv"))

plan_check <- future_spend %>%
  mutate(month = floor_date(date, "month")) %>%
  group_by(month) %>%
  summarise(plan_total = sum(rowSums(across(all_of(spend_cols)), na.rm = TRUE), na.rm = TRUE), .groups = "drop") %>%
  arrange(month)
if (nrow(plan_check)) {
  ratios <- round(plan_check$plan_total / monthly_targets[1:nrow(plan_check)] - 1, 3)
  logf("Forecast  | plan vs targets (ratio-1): ", paste(ratios, collapse = ", "))
  if (any(abs(ratios) > 0.10, na.rm = TRUE)) logf("⚠ Forecast | Plan deviates >10% from targets.")
}

BASE_LOOKBACK <- as.integer(Sys.getenv("FORECAST_BASE_LOOKBACK_DAYS", "28"))
base_daily <- compute_base_daily(OutputCollect, InputCollect, lookback_days = BASE_LOOKBACK)

future_spend <- future_spend %>% mutate(month = floor_date(date, "month"))
months_vec <- sort(unique(future_spend$month))
SHARE_TOL <- as.numeric(Sys.getenv("FORECAST_SHARE_TOL", "1e-4"))
proj_rows <- list()
pred_alloc_dir <- file.path(dir_path, paste0("allocator_pred_plots_", timestamp))
dir.create(pred_alloc_dir, showWarnings = FALSE)
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

  al <- try(robyn_allocator(
    InputCollect = InputCollect,
    OutputCollect = OutputCollect,
    select_model = best_id,
    date_range = c(alloc_start, alloc_end),
    expected_spend = total_budget,
    scenario = "max_historical_response",
    channel_constr_low = as.numeric(bands$low),
    channel_constr_up = as.numeric(bands$up),
    export = TRUE
  ), silent = TRUE)

  al_tbl <- if (!inherits(al, "try-error")) al$result_allocator else NULL
  incr <- get_allocator_total_response(al_tbl)

  if (!inherits(al, "try-error")) {
    pred_fname <- sprintf("allocator_pred_%s.png", format(m, "%Y-%m"))
    pred_local <- file.path(pred_alloc_dir, pred_fname)
    pred_key <- file.path(paste0("allocator_pred_plots_", timestamp), pred_fname)
    try(
      {
        png(pred_local, width = 1200, height = 800)
        plot(al)
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
      incremental = round(incr %||% 0, 2),
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
    incremental = round(incr %||% 0, 2),
    forecast_total = round(base_daily * days_in_m + (incr %||% 0), 2),
    stringsAsFactors = FALSE
  )
}
proj <- if (length(proj_rows)) {
  dplyr::bind_rows(proj_rows)
} else {
  data.frame(
    month = character(), start = as.Date(character()), end = as.Date(character()),
    days = integer(), budget = double(), baseline = double(), incremental = double(), forecast_total = double()
  )
}

if (length(pred_plot_rows)) {
  pred_idx <- dplyr::bind_rows(pred_plot_rows)
  pred_idx_csv <- file.path(dir_path, "forecast_allocator_index.csv")
  safe_write_csv(pred_idx, pred_idx_csv)
  gcs_put_safe(pred_idx_csv, file.path(gcs_prefix, "forecast_allocator_index.csv"))
}
forecast_csv <- file.path(dir_path, "forecast_next3m.csv")
safe_write_csv(proj, forecast_csv)
gcs_put_safe(forecast_csv, file.path(gcs_prefix, "forecast_next3m.csv"))
logf("Forecast  | wrote monthly projections: ", forecast_csv)
push_log()

## ---------- Mirror local dir ----------
for (f in list.files(dir_path, recursive = TRUE, full.names = TRUE)) {
  rel <- sub(paste0("^", normalizePath(dir_path), "/?"), "", normalizePath(f))
  gcs_put_safe(f, file.path(gcs_prefix, rel))
}
push_log()

## ---------- Status SUCCEEDED ----------
job_finished <- Sys.time()
writeLines(jsonlite::toJSON(list(
  state = "SUCCEEDED", start_time = as.character(job_started),
  end_time = as.character(job_finished),
  duration_minutes = round(as.numeric(difftime(job_finished, job_started, units = "mins")), 2)
), auto_unbox = TRUE), status_json)
gcs_put_safe(status_json, file.path(gcs_prefix, "status.json"))
push_log()
