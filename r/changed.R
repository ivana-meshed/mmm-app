# When you construct `hyperparameters`, do THIS:
hyperparameters[["train_size"]] <- train_size # <- overwrite cfg range

expect_keys <- c(
    as.vector(outer(c(paid_media_vars, organic_vars), c("_alphas", "_gammas", "_thetas"), paste0)),
    "train_size"
)
missing <- setdiff(expect_keys, names(hyperparameters))
extra <- setdiff(names(hyperparameters), expect_keys)

if (length(missing)) stop("Missing HP keys: ", paste(missing, collapse = ", "))
if (length(extra)) stop("Extra HP keys (remove them): ", paste(extra, collapse = ", "))

# HP coverage & shapes
req <- as.vector(outer(c(paid_media_vars, organic_vars), c("_alphas", "_gammas", "_thetas"), paste0))
missing <- setdiff(req, names(hyperparameters))
badlen <- names(hyperparameters)[vapply(hyperparameters, length, integer(1)) != 2]
if (length(missing)) stop("Missing HP keys: ", paste(missing, collapse = ", "), call. = FALSE)
if (length(badlen)) stop("Non length-2 HP entries: ", paste(badlen, collapse = ", "), call. = FALSE)

## ---------- HP diagnostics + robyn_inputs() PROBE ----------
hp_diag_path <- file.path(dir_path, "robyn_hp_diagnostics.txt")
capture_msgs <- character()
capture_warn <- character()
append_msg <- function(x) capture_msgs <<- c(capture_msgs, x)
append_warn <- function(x) capture_warn <<- c(capture_warn, x)

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

# Always write the diagnostics file once, whether success or failure.
hp_diag_lines <- c(
    "=== ROBYN HP DIAGNOSTICS ===",
    paste0("Time: ", as.character(Sys.time())), "",
    "-- Paid media vars:",
    paste0("  ", paste(paid_media_vars, collapse = ", ")),
    "-- Organic vars:",
    paste0("  ", paste(organic_vars, collapse = ", ")), "",
    "-- Messages from robyn_inputs():",
    if (length(capture_msgs)) paste0("  ", capture_msgs) else "  <none>", "",
    "-- Warnings from robyn_inputs():",
    if (length(capture_warn)) paste0("  ", capture_warn) else "  <none>", "",
    if (!is.null(inp_err)) c("", "== ERROR ==", paste0("  ", inp_err)) else ""
)
writeLines(hp_diag_lines, hp_diag_path)
gcs_put_safe(hp_diag_path, file.path(gcs_prefix, "robyn_hp_diagnostics.txt"))
push_log()
## ---------- robyn_inputs() with hard guard ----------
dir.create(dir_path, recursive = TRUE, showWarnings = FALSE) # ensure path exists

## ---------- TRAIN (with exact error capture) ----------
message("→ Starting Robyn training with ", max_cores, " cores on Cloud Run Jobs...")
t0 <- Sys.time()

# where to save detailed diagnostics
robyn_err_txt <- file.path(dir_path, "robyn_run_error.txt")
robyn_err_json <- file.path(dir_path, "robyn_run_error.json")

# small helper for a readable call stack
.format_calls <- function(cs) {
    # drop very long calls for readability
    vapply(cs, function(z) paste0(deparse(z, nlines = 3L), collapse = " "), character(1))
}

## ---------- ROBYN INPUTS (2-step, strict) ----------
# 1) Build the base inputs (NO hyperparameters here)
