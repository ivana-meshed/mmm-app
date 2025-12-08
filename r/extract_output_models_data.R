#!/usr/bin/env Rscript

# extract_output_models_data.R
# Extract compressed data from OutputCollect.RDS to parquet files
# Extracts: xDecompAgg, resultHypParam, mediaVecCollect, xDecompVecCollect
# Note: OutputCollect is the result from robyn_outputs(), not robyn_run()

# Ensure arrow library is available when sourced
if (!requireNamespace("arrow", quietly = TRUE)) {
    stop("Package 'arrow' is required but not installed.")
}

#' Extract data from OutputCollect.RDS to parquet files
#'
#' @param oc_path Path to OutputCollect.RDS file
#' @param out_dir Output directory for parquet files
extract_output_models_data <- function(oc_path, out_dir) {
    if (!file.exists(oc_path)) {
        stop("OutputCollect.RDS not found at: ", oc_path)
    }
    
    # Ensure directory exists
    dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
    
    message("→ Loading OutputCollect.RDS from: ", oc_path)
    oc <- readRDS(oc_path)
    
    # Track which files were successfully created
    created_files <- character(0)
    
    # Extract xDecompAgg
    tryCatch({
        if (!is.null(oc$xDecompAgg)) {
            out_file <- file.path(out_dir, "xDecompAgg.parquet")
            arrow::write_parquet(as.data.frame(oc$xDecompAgg), out_file)
            message("✅ Exported xDecompAgg to: ", out_file)
            created_files <- c(created_files, out_file)
        } else {
            message("⚠️ xDecompAgg is NULL, skipping")
        }
    }, error = function(e) {
        message("❌ Failed to export xDecompAgg: ", conditionMessage(e))
    })
    
    # Extract resultHypParam
    tryCatch({
        if (!is.null(oc$resultHypParam)) {
            out_file <- file.path(out_dir, "resultHypParam.parquet")
            arrow::write_parquet(as.data.frame(oc$resultHypParam), out_file)
            message("✅ Exported resultHypParam to: ", out_file)
            created_files <- c(created_files, out_file)
        } else {
            message("⚠️ resultHypParam is NULL, skipping")
        }
    }, error = function(e) {
        message("❌ Failed to export resultHypParam: ", conditionMessage(e))
    })
    
    # Extract mediaVecCollect
    tryCatch({
        if (!is.null(oc$mediaVecCollect)) {
            out_file <- file.path(out_dir, "mediaVecCollect.parquet")
            arrow::write_parquet(as.data.frame(oc$mediaVecCollect), out_file)
            message("✅ Exported mediaVecCollect to: ", out_file)
            created_files <- c(created_files, out_file)
        } else {
            message("⚠️ mediaVecCollect is NULL, skipping")
        }
    }, error = function(e) {
        message("❌ Failed to export mediaVecCollect: ", conditionMessage(e))
    })
    
    # Extract xDecompVecCollect
    tryCatch({
        if (!is.null(oc$xDecompVecCollect)) {
            out_file <- file.path(out_dir, "xDecompVecCollect.parquet")
            arrow::write_parquet(as.data.frame(oc$xDecompVecCollect), out_file)
            message("✅ Exported xDecompVecCollect to: ", out_file)
            created_files <- c(created_files, out_file)
        } else {
            message("⚠️ xDecompVecCollect is NULL, skipping")
        }
    }, error = function(e) {
        message("❌ Failed to export xDecompVecCollect: ", conditionMessage(e))
    })
    
    message("→ Extraction complete. Created ", length(created_files), " parquet files in: ", out_dir)
    
    return(invisible(created_files))
}

# If run as a script (not sourced), parse command line arguments
if (!interactive()) {
    # Check if this is being run via Rscript
    args <- commandArgs(trailingOnly = FALSE)
    file_arg <- grep("^--file=", args, value = TRUE)
    
    if (length(file_arg) > 0) {
        # Script is being run directly - load required libraries
        if (!requireNamespace("optparse", quietly = TRUE)) {
            stop("Package 'optparse' is required but not installed.")
        }
        
        suppressPackageStartupMessages({
            library(arrow)
            library(optparse)
        })
        
        option_list <- list(
            make_option(c("--input"), type = "character", default = NULL,
                        help = "Path to OutputCollect.RDS file", metavar = "FILE"),
            make_option(c("--output"), type = "character", default = NULL,
                        help = "Output directory for parquet files", metavar = "DIR")
        )
        
        opt_parser <- OptionParser(option_list = option_list)
        opt <- parse_args(opt_parser)
        
        if (is.null(opt$input)) {
            stop("--input is required")
        }
        
        if (is.null(opt$output)) {
            stop("--output is required")
        }
        
        # Execute extraction
        extract_output_models_data(opt$input, opt$output)
    }
}
