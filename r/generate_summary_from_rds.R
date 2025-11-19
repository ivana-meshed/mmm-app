#!/usr/bin/env Rscript

# generate_summary_from_rds.R
# Standalone script to generate model summary from existing RDS files

suppressPackageStartupMessages({
    library(jsonlite)
    library(optparse)
})

# Parse command line arguments
option_list <- list(
    make_option(c("--output-collect"), type = "character", default = NULL,
                help = "Path to OutputCollect.RDS file", metavar = "FILE"),
    make_option(c("--input-collect"), type = "character", default = NULL,
                help = "Path to InputCollect.RDS file (optional)",
                metavar = "FILE"),
    make_option(c("--country"), type = "character", default = NULL,
                help = "Country code", metavar = "STRING"),
    make_option(c("--revision"), type = "character", default = NULL,
                help = "Revision identifier", metavar = "STRING"),
    make_option(c("--timestamp"), type = "character", default = NULL,
                help = "Run timestamp", metavar = "STRING"),
    make_option(c("--output"), type = "character",
                default = "model_summary.json",
                help = "Output JSON file path", metavar = "FILE")
)

opt_parser <- OptionParser(option_list = option_list)
opt <- parse_args(opt_parser)

# Validate required arguments
if (is.null(opt$`output-collect`)) {
    stop("--output-collect is required")
}

if (!file.exists(opt$`output-collect`)) {
    stop("OutputCollect.RDS file not found: ", opt$`output-collect`)
}

# Source the helper functions
# Try multiple methods to find the script directory (for both interactive and subprocess calls)
script_dir <- tryCatch({
    # Method 1: Try sys.frame (works when sourced or run interactively)
    ofile <- tryCatch(sys.frame(1)$ofile, error = function(e) NULL)
    if (!is.null(ofile) && nzchar(ofile)) {
        dirname(normalizePath(ofile, mustWork = FALSE))
    } else {
        stop("sys.frame method not available")
    }
}, error = function(e) {
    # Method 2: Try commandArgs (works when run via Rscript)
    tryCatch({
        args <- commandArgs()
        file_arg <- grep("^--file=", args, value = TRUE)
        if (length(file_arg) > 0) {
            file_path <- sub("^--file=", "", file_arg[1])
            dirname(normalizePath(file_path, mustWork = FALSE))
        } else {
            stop("No --file argument found")
        }
    }, error = function(e2) {
        # Method 3: Try current working directory + r/
        if (file.exists("r/extract_model_summary.R")) {
            "r"
        } else if (file.exists("extract_model_summary.R")) {
            "."
        } else if (file.exists("/app/extract_model_summary.R")) {
            "/app"
        } else {
            stop("Cannot find extract_model_summary.R in any expected location")
        }
    })
})

helper_path <- file.path(script_dir, "extract_model_summary.R")
if (!file.exists(helper_path)) {
    stop("Helper script not found at: ", helper_path)
}
source(helper_path)

# Load RDS files
message("Loading OutputCollect.RDS...")
output_collect <- readRDS(opt$`output-collect`)

input_collect <- NULL
if (!is.null(opt$`input-collect`) && nzchar(opt$`input-collect`)) {
    if (file.exists(opt$`input-collect`)) {
        message("Loading InputCollect.RDS...")
        input_collect <- readRDS(opt$`input-collect`)
    } else {
        message("InputCollect.RDS not found, proceeding without it")
    }
}

# Extract summary
message("Extracting model summary...")
summary <- extract_model_summary(
    output_collect = output_collect,
    input_collect = input_collect,
    country = opt$country,
    revision = opt$revision,
    timestamp = opt$timestamp,
    training_time_mins = NULL  # Not available for historical runs
)

# Save summary
message("Saving summary to: ", opt$output)
save_model_summary(summary, opt$output)

message("✅ Summary generation complete")
