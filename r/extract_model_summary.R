#!/usr/bin/env Rscript

# extract_model_summary.R
# Helper function to extract model summary from OutputCollect.RDS
# This creates a JSON summary with candidate models, Pareto models, and metrics

suppressPackageStartupMessages({
    library(jsonlite)
    library(dplyr)
})

#' Extract model summary from OutputCollect object
#'
#' @param output_collect The OutputCollect object from robyn_outputs()
#' @param input_collect The InputCollect object from robyn_inputs()
#' @param country Country code
#' @param revision Revision identifier
#' @param timestamp Run timestamp
#' @param training_time_mins Training time in minutes
#' @return A list with summary information
extract_model_summary <- function(output_collect, input_collect = NULL,
                                   country = NULL, revision = NULL,
                                   timestamp = NULL,
                                   training_time_mins = NULL) {
    if (is.null(output_collect)) {
        stop("output_collect cannot be NULL")
    }

    # Extract metadata
    summary <- list(
        country = country,
        revision = revision,
        timestamp = timestamp,
        created_at = as.character(Sys.time()),
        training_time_mins = training_time_mins
    )

    # Extract candidate models information
    if (!is.null(output_collect$resultHypParam)) {
        result_hyp <- output_collect$resultHypParam

        # Identify Pareto front models
        # In Robyn, Pareto models are typically marked by pareto_optimal flag
        # or by being in the first N models sorted by some criteria
        has_pareto <- FALSE
        pareto_models <- list()

        if ("pareto_optimal" %in% names(result_hyp)) {
            has_pareto <- any(result_hyp$pareto_optimal == TRUE, na.rm = TRUE)
            if (has_pareto) {
                pareto_df <- result_hyp[result_hyp$pareto_optimal == TRUE, ]
                pareto_models <- lapply(
                    seq_len(min(nrow(pareto_df), 10)),
                    function(i) {
                        row <- pareto_df[i, ]
                        list(
                            model_id = as.character(row$solID),
                            nrmse = as.numeric(row$nrmse %||% NA),
                            decomp_rssd = as.numeric(row$decomp.rssd %||% NA),
                            rsq_train = as.numeric(row$rsq_train %||% NA),
                            nrmse_train = as.numeric(row$nrmse_train %||% NA),
                            rsq_val = as.numeric(row$rsq_val %||% NA),
                            nrmse_val = as.numeric(row$nrmse_val %||% NA),
                            rsq_test = as.numeric(row$rsq_test %||% NA),
                            nrmse_test = as.numeric(row$nrmse_test %||% NA),
                            mape = as.numeric(row$mape %||% NA),
                            robyn_pareto_front = as.integer(
                                row$robyn_pareto_front %||% NA
                            )
                        )
                    }
                )
            }
        } else {
            # Fallback: use pareto_fronts or top models
            # Check if there's a robyn_pareto_front column
            if ("robyn_pareto_front" %in% names(result_hyp)) {
                has_pareto <- any(
                    !is.na(result_hyp$robyn_pareto_front) &
                        result_hyp$robyn_pareto_front > 0
                )
                if (has_pareto) {
                    pareto_df <- result_hyp[
                        !is.na(result_hyp$robyn_pareto_front) &
                            result_hyp$robyn_pareto_front > 0,
                    ]
                    pareto_models <- lapply(
                        seq_len(min(nrow(pareto_df), 10)),
                        function(i) {
                            row <- pareto_df[i, ]
                            list(
                                model_id = as.character(row$solID),
                                nrmse = as.numeric(row$nrmse %||% NA),
                                decomp_rssd = as.numeric(
                                    row$decomp.rssd %||% NA
                                ),
                                rsq_train = as.numeric(row$rsq_train %||% NA),
                                nrmse_train = as.numeric(
                                    row$nrmse_train %||% NA
                                ),
                                rsq_val = as.numeric(row$rsq_val %||% NA),
                                nrmse_val = as.numeric(row$nrmse_val %||% NA),
                                rsq_test = as.numeric(row$rsq_test %||% NA),
                                nrmse_test = as.numeric(
                                    row$nrmse_test %||% NA
                                ),
                                mape = as.numeric(row$mape %||% NA),
                                robyn_pareto_front = as.integer(
                                    row$robyn_pareto_front %||% NA
                                )
                            )
                        }
                    )
                }
            }
        }

        summary$has_pareto_models <- has_pareto
        summary$pareto_model_count <- length(pareto_models)
        summary$pareto_models <- pareto_models

        # Extract all candidate models (limit to reasonable number)
        n_candidates <- min(nrow(result_hyp), 100)
        candidate_models <- lapply(seq_len(n_candidates), function(i) {
            row <- result_hyp[i, ]
            list(
                model_id = as.character(row$solID),
                nrmse = as.numeric(row$nrmse %||% NA),
                decomp_rssd = as.numeric(row$decomp.rssd %||% NA),
                rsq_train = as.numeric(row$rsq_train %||% NA),
                nrmse_train = as.numeric(row$nrmse_train %||% NA),
                rsq_val = as.numeric(row$rsq_val %||% NA),
                nrmse_val = as.numeric(row$nrmse_val %||% NA),
                rsq_test = as.numeric(row$rsq_test %||% NA),
                nrmse_test = as.numeric(row$nrmse_test %||% NA),
                mape = as.numeric(row$mape %||% NA),
                is_pareto = if ("robyn_pareto_front" %in% names(row)) {
                    !is.na(row$robyn_pareto_front) &&
                        row$robyn_pareto_front > 0
                } else {
                    FALSE
                }
            )
        })

        summary$candidate_model_count <- n_candidates
        summary$candidate_models <- candidate_models

        # Extract best model performance
        if (nrow(result_hyp) > 0) {
            best_row <- result_hyp[1, ]
            summary$best_model <- list(
                model_id = as.character(best_row$solID),
                nrmse = as.numeric(best_row$nrmse %||% NA),
                decomp_rssd = as.numeric(best_row$decomp.rssd %||% NA),
                rsq_train = as.numeric(best_row$rsq_train %||% NA),
                nrmse_train = as.numeric(best_row$nrmse_train %||% NA),
                rsq_val = as.numeric(best_row$rsq_val %||% NA),
                nrmse_val = as.numeric(best_row$nrmse_val %||% NA),
                rsq_test = as.numeric(best_row$rsq_test %||% NA),
                nrmse_test = as.numeric(best_row$nrmse_test %||% NA),
                mape = as.numeric(best_row$mape %||% NA)
            )
        }
    }

    # Add input metadata if available
    if (!is.null(input_collect)) {
        summary$input_metadata <- list(
            dep_var = input_collect$dep_var,
            dep_var_type = input_collect$dep_var_type,
            adstock = input_collect$adstock,
            window_start = as.character(input_collect$window_start),
            window_end = as.character(input_collect$window_end),
            paid_media_vars = input_collect$paid_media_vars,
            organic_vars = input_collect$organic_vars,
            context_vars = input_collect$context_vars,
            factor_vars = input_collect$factor_vars
        )
    }

    return(summary)
}

#' Save model summary to JSON file
#'
#' @param summary Summary list from extract_model_summary()
#' @param file_path Path where to save the JSON file
save_model_summary <- function(summary, file_path) {
    dir.create(dirname(file_path), recursive = TRUE, showWarnings = FALSE)
    json_str <- jsonlite::toJSON(
        summary,
        auto_unbox = TRUE,
        pretty = TRUE,
        null = "null",
        na = "null"
    )
    writeLines(json_str, file_path)
    message("Model summary saved to: ", file_path)
}

# Helper operator for NULL coalescing
`%||%` <- function(a, b) {
    if (is.null(a) || length(a) == 0) {
        return(b)
    }
    if (all(is.na(a))) {
        return(b)
    }
    a
}
