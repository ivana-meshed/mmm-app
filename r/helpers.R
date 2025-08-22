# helpers.R
suppressPackageStartupMessages({
  library(dplyr)
  library(tidyr)
  library(readr)
  library(lubridate)
  library(stringr)
  library(googleCloudStorageR)
  library(mime)
})

# ---------- Logging ----------
log_section <- function(txt) {
  cat("\n", paste0("==== ", txt, " ====\n"))
}

# ---------- Files / paths ----------
file_exists_or_stop <- function(p) {
  if (!file.exists(p)) stop("File not found: ", p, call. = FALSE)
  p
}

get_timestamp <- function() format(Sys.time(), "%m%d_%H%M%S")

make_output_paths <- function(revision, country, timestamp) {
  dir_path <- path.expand(file.path("~/budget/datasets", revision, country, timestamp))
  dir.create(dir_path, recursive = TRUE, showWarnings = FALSE)

  list(
    dir_path       = dir_path,
    gcs_prefix     = file.path("robyn", revision, country, timestamp),
    out_models     = file.path(dir_path, "OutputModels.RDS"),
    in_collect     = file.path(dir_path, "InputCollect.RDS"),
    out_collect    = file.path(dir_path, "OutputCollect.RDS"),
    best_model_txt = file.path(dir_path, "best_model_id.txt")
  )
}

# ---------- Google Cloud Storage ----------
gcs_init <- function(bucket) {
  if (nzchar(Sys.getenv("GOOGLE_APPLICATION_CREDENTIALS"))) {
    gcs_auth(Sys.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
  }
  gcs_global_bucket(bucket)
  options(googleCloudStorageR.predefinedAcl = "bucketLevel")
}

gcs_put <- function(local_file, object_path, upload_type = c("simple","resumable")) {
  upload_type <- match.arg(upload_type)
  lf <- normalizePath(local_file, mustWork = FALSE)
  if (!file.exists(lf)) stop("Local file does not exist: ", lf)
  if (grepl("^gs://", object_path)) stop("object_path must be a key, not 'gs://…'")

  bkt <- gcs_get_global_bucket()
  if (is.null(bkt) || bkt == "") stop("No bucket set: call gcs_global_bucket('your-bucket')")

  typ <- mime::guess_type(lf); if (is.na(typ) || typ == "") typ <- "application/octet-stream"

  googleCloudStorageR::gcs_upload(
    file          = lf,
    name          = object_path,
    bucket        = bkt,
    type          = typ,
    upload_type   = upload_type,
    predefinedAcl = "bucketLevel"
  )
}

# ---------- Data cleaning ----------
fill_day <- function(x) {
  stopifnot("date" %in% names(x))
  all  <- tibble(date = seq(min(x$date, na.rm = TRUE), max(x$date, na.rm = TRUE), by = "day"))
  full <- left_join(all, x, by = "date")
  num  <- names(full)[vapply(full, is.numeric, logical(1))]
  full[num] <- lapply(full[num], function(v) replace_na(v, 0))
  full
}

# Robust numeric parsing: handles "1,234.56", "1.234,56", factors, etc.
safe_parse_numbers <- function(df, cols) {
  for (cl in intersect(cols, names(df))) {
    x <- df[[cl]]
    if (is.numeric(x)) {
      df[[cl]] <- as.numeric(x)
    } else if (is.factor(x)) {
      df[[cl]] <- suppressWarnings(readr::parse_number(as.character(x)))
    } else if (is.character(x)) {
      df[[cl]] <- suppressWarnings(readr::parse_number(x))
    } else {
      df[[cl]] <- suppressWarnings(readr::parse_number(as.character(x)))
    }
  }
  df
}

drop_zero_variance <- function(df, exclude = c("date")) {
  keep <- names(df)
  keep <- setdiff(keep, exclude)
  zvar <- keep[vapply(df[keep], function(x) is.numeric(x) && dplyr::n_distinct(x, na.rm = TRUE) <= 1, logical(1))]
  if (length(zvar)) {
    cat("ℹ️  Dropped zero-variance columns:", paste(zvar, collapse = ", "), "\n")
    df <- df[, !(names(df) %in% zvar), drop = FALSE]
  }
  df
}

# ---------- Window helpers ----------
stable_window <- function(df, start_date = as.Date("2024-01-01")) {
  end_date <- max(df$date, na.rm = TRUE)
  list(
    df = dplyr::filter(df, date >= start_date, date <= end_date),
    start_date = start_date,
    end_date   = end_date
  )
}

sum_spend_between <- function(df, spend_cols, start_date, end_date) {
  df %>%
    dplyr::filter(date >= start_date, date <= end_date) %>%
    dplyr::select(dplyr::all_of(spend_cols)) %>%
    rowSums(na.rm = TRUE) %>%
    sum()
}

# ---------- Feature engineering ----------
build_feature_aggregates <- function(df) {
  # Must run AFTER zero-variance pruning and numeric parsing
  df %>%
    mutate(
      GA_OTHER_COST = rowSums(select(., tidyselect::matches("^GA_.*_COST$") &
                                       !any_of(c("GA_SUPPLY_COST","GA_BRAND_COST","GA_DEMAND_COST"))), na.rm = TRUE),
      BING_TOTAL_COST = rowSums(select(., tidyselect::matches("^BING_.*_COST$")), na.rm = TRUE),
      META_TOTAL_COST = rowSums(select(., tidyselect::matches("^META_.*_COST$")), na.rm = TRUE)
    ) %>%
    mutate(
      ORGANIC_TRAFFIC = rowSums(select(., any_of(c(
        "NL_DAILY_SESSIONS","SEO_DAILY_SESSIONS","DIRECT_DAILY_SESSIONS",
        "TV_DAILY_SESSIONS","CRM_OTHER_DAILY_SESSIONS","CRM_DAILY_SESSIONS"
      ))), na.rm = TRUE),
      BRAND_HEALTH = coalesce(DIRECT_DAILY_SESSIONS, 0) + coalesce(SEO_DAILY_SESSIONS, 0),
      ORGxTV = BRAND_HEALTH * coalesce(TV_COST, 0)
    ) %>%
    mutate(
      GA_OTHER_IMPRESSIONS   = rowSums(select(., tidyselect::matches("^GA_.*_IMPRESSIONS$") &
                                                !any_of(c("GA_SUPPLY_IMPRESSIONS","GA_BRAND_IMPRESSIONS","GA_DEMAND_IMPRESSIONS"))), na.rm = TRUE),
      BING_TOTAL_IMPRESSIONS = rowSums(select(., tidyselect::matches("^BING_.*_IMPRESSIONS$")), na.rm = TRUE),
      META_TOTAL_IMPRESSIONS = rowSums(select(., tidyselect::matches("^META_.*_IMPRESSIONS$")), na.rm = TRUE)
    )
}

# ---------- Organic-search guard ----------
should_add_n_searches <- function(dtf, spend_cols, thr = 0.15) {
  if (!"N_SEARCHES" %in% names(dtf) || length(spend_cols) == 0) return(FALSE)
  ts <- rowSums(dtf[, spend_cols, drop = FALSE], na.rm = TRUE)
  cor_val <- suppressWarnings(abs(cor(dtf$N_SEARCHES, ts, use = "complete.obs")))
  isTRUE(cor_val < thr)
}

# ---------- Annotations join ----------
# Expects CSV with columns like:
# DATE,DESCRIPTION,ANNOTATION_ID,PROJECT_ID,USER_ID,ANN_CYCLING_EVENT,ANN_PRODUCT_CHANGE,ANN_CRM_ACTIVITY,IS_ANN
# DATE can be "YYYY-MM-DD" or full timestamp with timezone.
join_annotations <- function(df, annotations_csv) {
  if (!file.exists(annotations_csv)) {
    cat("ℹ️  Annotations file not found: ", annotations_csv, " (skipping)\n", sep = "")
    return(df)
  }

  ann <- read.csv(annotations_csv, check.names = FALSE)

  # Normalize column names
  names(ann) <- toupper(names(ann))

  # Parse DATE, strip time & tz to Date
  if ("DATE" %in% names(ann)) {
    # Accept both date-only and datetime/tz formats
    ann$DATE <- suppressWarnings(as.Date(ann$DATE))
    if (any(is.na(ann$DATE))) {
      # Try parsing common datetime formats with lubridate then take date()
      parsed <- suppressWarnings(lubridate::ymd_hms(ann$DATE, quiet = TRUE, tz = "UTC"))
      ann$DATE <- as.Date(parsed)
    }
  } else {
    stop("Annotations CSV missing 'DATE' column.")
  }

  # Fill booleans/flags with 0
  for (cl in c("ANN_CYCLING_EVENT","ANN_PRODUCT_CHANGE","ANN_CRM_ACTIVITY","IS_ANN")) {
    if (cl %in% names(ann)) {
      # Coerce to numeric 0/1 safely
      v <- ann[[cl]]
      if (is.logical(v)) {
        ann[[cl]] <- as.integer(v)
      } else if (is.numeric(v)) {
        ann[[cl]] <- as.integer(replace_na(v, 0))
      } else {
        ann[[cl]] <- as.integer(tolower(as.character(v)) %in% c("true","1","yes","y"))
      }
      ann[[cl]][is.na(ann[[cl]])] <- 0L
    }
  }

  ann <- ann %>%
    distinct(DATE, .keep_all = TRUE)

  # Left join on date
  out <- df %>%
    left_join(ann, by = c("date" = "DATE"))

  # Ensure joined columns exist and fill NAs with 0
  for (cl in c("ANN_CYCLING_EVENT","ANN_PRODUCT_CHANGE","ANN_CRM_ACTIVITY","IS_ANN")) {
    if (!cl %in% names(out)) out[[cl]] <- 0L
    out[[cl]][is.na(out[[cl]])] <- 0L
  }

  out
}

# ---------- Channel maps (optional convenience) ----------
make_channel_maps <- function() {
  channels <- c("GA_SUPPLY","GA_BRAND","GA_DEMAND","GA_OTHER","BING_TOTAL","META_TOTAL","TV","PARTNERSHIP")
  spend_map <- c(
    GA_SUPPLY   = "GA_SUPPLY_COST",
    GA_BRAND    = "GA_BRAND_COST",
    GA_DEMAND   = "GA_DEMAND_COST",
    GA_OTHER    = "GA_OTHER_COST",
    BING_TOTAL  = "BING_TOTAL_COST",
    META_TOTAL  = "META_TOTAL_COST",
    TV          = "TV_COST",
    PARTNERSHIP = "PARTNERSHIP_COSTS"
  )
  var_map <- c(
    GA_SUPPLY   = "GA_SUPPLY_IMPRESSIONS",
    GA_BRAND    = "GA_BRAND_IMPRESSIONS",
    GA_DEMAND   = "GA_DEMAND_IMPRESSIONS",
    GA_OTHER    = "GA_OTHER_IMPRESSIONS",
    BING_TOTAL  = "BING_TOTAL_IMPRESSIONS",
    META_TOTAL  = "META_TOTAL_IMPRESSIONS",
    TV          = "TV_COST",
    PARTNERSHIP = "PARTNERSHIP_COSTS"
  )
  list(channels = channels, spend_map = spend_map, var_map = var_map)
}
