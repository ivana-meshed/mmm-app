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

# STEP 1: Generate missing summaries
message("\n=== Step 1: Generating missing summaries ===")

cmd_args_generate <- c(
    python_script,
    "--bucket", opt$bucket,
    "--generate-missing"
)

if (!is.null(opt$project) && nzchar(opt$project)) {
    cmd_args_generate <- c(cmd_args_generate, "--project", opt$project)
}

if (!is.null(opt$country) && nzchar(opt$country)) {
    cmd_args_generate <- c(cmd_args_generate, "--country", opt$country)
}

if (!is.null(opt$revision) && nzchar(opt$revision)) {
    cmd_args_generate <- c(cmd_args_generate, "--revision", opt$revision)
}

message(paste("Command: python3", paste(cmd_args_generate, collapse = " ")))

# Execute the generation step
result_generate <- system2(
    "python3",
    args = cmd_args_generate,
    stdout = TRUE,
    stderr = TRUE,
    wait = TRUE
)

# Check result
exit_code_generate <- attr(result_generate, "status")
if (is.null(exit_code_generate)) {
    exit_code_generate <- 0
}

# Print output
cat(result_generate, sep = "\n")

if (exit_code_generate != 0) {
    message("\n❌ Summary generation failed with exit code: ", exit_code_generate)
    quit(status = exit_code_generate)
}

message("\n✅ Summary generation completed")

# STEP 2: Aggregate summaries by country
message("\n=== Step 2: Aggregating summaries by country ===")

cmd_args_aggregate <- c(
    python_script,
    "--bucket", opt$bucket,
    "--aggregate"
)

if (!is.null(opt$project) && nzchar(opt$project)) {
    cmd_args_aggregate <- c(cmd_args_aggregate, "--project", opt$project)
}

if (!is.null(opt$country) && nzchar(opt$country)) {
    cmd_args_aggregate <- c(cmd_args_aggregate, "--country", opt$country)
}

if (!is.null(opt$revision) && nzchar(opt$revision)) {
    cmd_args_aggregate <- c(cmd_args_aggregate, "--revision", opt$revision)
}

message(paste("Command: python3", paste(cmd_args_aggregate, collapse = " ")))

# Execute the aggregation step
result_aggregate <- system2(
    "python3",
    args = cmd_args_aggregate,
    stdout = TRUE,
    stderr = TRUE,
    wait = TRUE
)

# Check result
exit_code_aggregate <- attr(result_aggregate, "status")
if (is.null(exit_code_aggregate)) {
    exit_code_aggregate <- 0
}

# Print output
cat(result_aggregate, sep = "\n")

if (exit_code_aggregate != 0) {
    message("\n⚠️ Aggregation failed with exit code: ", exit_code_aggregate)
    message("Note: This is non-critical. Individual summaries were generated successfully.")
    quit(status = 0)  # Don't fail the whole job
}

message("\n✅ Aggregation completed successfully")
message("\n✅ All backfill operations completed successfully")
quit(status = 0)
