#!/usr/bin/env Rscript

# backfill_summaries.R
# Backfill model summaries for existing runs in GCS
# This script runs the Python aggregation script from within the training container

suppressPackageStartupMessages({
    library(optparse)
})

# Parse command line arguments
option_list <- list(
    make_option(c("--bucket"), type = "character",
                default = Sys.getenv("GCS_BUCKET", "mmm-app-output"),
                help = "GCS bucket name", metavar = "STRING"),
    make_option(c("--project"), type = "character",
                default = Sys.getenv("PROJECT_ID"),
                help = "GCP project ID", metavar = "STRING"),
    make_option(c("--country"), type = "character", default = NULL,
                help = "Filter by country code", metavar = "STRING"),
    make_option(c("--revision"), type = "character", default = NULL,
                help = "Filter by revision", metavar = "STRING")
)

opt_parser <- OptionParser(option_list = option_list)
opt <- parse_args(opt_parser)

message("=== Model Summary Backfill ===")
message(paste("Bucket:", opt$bucket))
message(paste("Project:", opt$project))
if (!is.null(opt$country)) {
    message(paste("Country filter:", opt$country))
}
if (!is.null(opt$revision)) {
    message(paste("Revision filter:", opt$revision))
}

# Build the Python command
python_script <- "/app/scripts/aggregate_model_summaries.py"
if (!file.exists(python_script)) {
    # Try alternate location
    python_script <- "scripts/aggregate_model_summaries.py"
    if (!file.exists(python_script)) {
        stop("Cannot find aggregate_model_summaries.py script")
    }
}

cmd_args <- c(
    python_script,
    "--bucket", opt$bucket,
    "--generate-missing"
)

if (!is.null(opt$project) && nzchar(opt$project)) {
    cmd_args <- c(cmd_args, "--project", opt$project)
}

if (!is.null(opt$country) && nzchar(opt$country)) {
    cmd_args <- c(cmd_args, "--country", opt$country)
}

if (!is.null(opt$revision) && nzchar(opt$revision)) {
    cmd_args <- c(cmd_args, "--revision", opt$revision)
}

message("\nExecuting backfill...")
message(paste("Command: python3", paste(cmd_args, collapse = " ")))

# Execute the Python script
result <- system2(
    "python3",
    args = cmd_args,
    stdout = TRUE,
    stderr = TRUE,
    wait = TRUE
)

# Check result
exit_code <- attr(result, "status")
if (is.null(exit_code)) {
    exit_code <- 0
}

# Print output
cat(result, sep = "\n")

if (exit_code != 0) {
    message("\n❌ Backfill failed with exit code: ", exit_code)
    quit(status = exit_code)
} else {
    message("\n✅ Backfill completed successfully")
    quit(status = 0)
}
