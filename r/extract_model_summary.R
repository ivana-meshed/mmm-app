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

    # Extract decomposition contributions and channel ROAS from xDecompAgg
    if (!is.null(output_collect$xDecompAgg) && !is.null(summary$best_model)) {
        decomp_contrib <- .extract_decomp_contributions(
            output_collect, input_collect, summary$best_model$model_id
        )
        summary$decomp_contribution <- decomp_contrib
    }

    return(summary)
}

#' Extract decomposition contributions and ROAS from xDecompAgg
#'
#' @param output_collect OutputCollect from robyn_outputs()
#' @param input_collect InputCollect from robyn_inputs() (may be NULL)
#' @param best_model_id The best model's solID
#' @return A list with paid_media_share, baseline_share, organic_share,
#'   context_share, channel_roas, and allocator_stability_roas_cv
.extract_decomp_contributions <- function(output_collect, input_collect,
                                          best_model_id) {
    xda <- output_collect$xDecompAgg

    # Resolve known paid-media, organic, and context variable names
    paid_media_vars <- character(0)
    organic_vars <- character(0)
    context_vars <- character(0)
    factor_vars <- character(0)
    if (!is.null(input_collect)) {
        paid_media_vars <- as.character(input_collect$paid_media_vars %||% character(0))
        organic_vars <- as.character(input_collect$organic_vars %||% character(0))
        context_vars <- as.character(input_collect$context_vars %||% character(0))
    }

    result <- list(
        paid_media_share = NA_real_,
        baseline_share = NA_real_,
        organic_share = NA_real_,
        context_share = NA_real_,
        channel_roas = list(),
        channel_cpa = list(),
        allocator_stability_roas_cv = NA_real_
    )

    # ---- Best-model decomposition shares ----
    tryCatch({
        best_rows <- xda[xda$solID == best_model_id, ]
        if (nrow(best_rows) == 0) {
            return(result)
        }

        # xDecompPerc gives the share of each variable in total response
        perc_col <- if ("xDecompPerc" %in% names(best_rows)) "xDecompPerc" else NULL
        if (is.null(perc_col)) {
            return(result)
        }

        vars <- best_rows$rn

        paid_idx <- vars %in% paid_media_vars
        organic_idx <- vars %in% organic_vars
        context_idx <- vars %in% context_vars
        # Baseline = everything not classified above (intercept, trend, season, holiday, …)
        baseline_idx <- !paid_idx & !organic_idx & !context_idx

        result$paid_media_share <- round(
            sum(best_rows[[perc_col]][paid_idx], na.rm = TRUE), 4
        )
        result$organic_share <- round(
            sum(best_rows[[perc_col]][organic_idx], na.rm = TRUE), 4
        )
        result$context_share <- round(
            sum(best_rows[[perc_col]][context_idx], na.rm = TRUE), 4
        )
        result$baseline_share <- round(
            sum(best_rows[[perc_col]][baseline_idx], na.rm = TRUE), 4
        )

        # ---- Per-channel ROAS for best model ----
        roas_col <- if ("roi_total" %in% names(best_rows)) {
            "roi_total"
        } else if ("roi_mean" %in% names(best_rows)) {
            "roi_mean"
        } else {
            NULL
        }

        cpa_col <- if ("cpa_total" %in% names(best_rows)) {
            "cpa_total"
        } else if ("cpa_mean" %in% names(best_rows)) {
            "cpa_mean"
        } else {
            NULL
        }

        .safe_named_numeric <- function(rows, col) {
            vals <- setNames(
                as.list(round(as.numeric(rows[[col]]), 4)),
                as.character(rows$rn)
            )
            lapply(vals, function(v) {
                if (is.null(v) || length(v) == 0) return(NA_real_)
                v_num <- suppressWarnings(as.numeric(v))
                if (length(v_num) == 1 && !is.na(v_num)) v_num else NA_real_
            })
        }

        if (!is.null(roas_col) && length(paid_media_vars) > 0) {
            media_rows <- best_rows[paid_idx, ]
            result$channel_roas <- .safe_named_numeric(media_rows, roas_col)
        }

        if (!is.null(cpa_col) && length(paid_media_vars) > 0) {
            media_rows <- best_rows[paid_idx, ]
            result$channel_cpa <- .safe_named_numeric(media_rows, cpa_col)
        }
    }, error = function(e) {
        message("⚠️ Failed to extract decomp contributions: ", conditionMessage(e))
    })

    # ---- Allocator stability: ROAS CV across Pareto models ----
    tryCatch({
        roas_col <- if ("roi_total" %in% names(xda)) {
            "roi_total"
        } else if ("roi_mean" %in% names(xda)) {
            "roi_mean"
        } else {
            NULL
        }

        # Identify Pareto model IDs
        pareto_ids <- NULL
        if (!is.null(output_collect$resultHypParam)) {
            rhp <- output_collect$resultHypParam
            if ("robyn_pareto_front" %in% names(rhp)) {
                pareto_ids <- rhp$solID[
                    !is.na(rhp$robyn_pareto_front) & rhp$robyn_pareto_front > 0
                ]
            } else if ("pareto_optimal" %in% names(rhp)) {
                pareto_ids <- rhp$solID[rhp$pareto_optimal == TRUE]
            }
        }

        if (!is.null(roas_col) && length(paid_media_vars) > 0 &&
            !is.null(pareto_ids) && length(pareto_ids) >= 2) {
            pareto_rows <- xda[xda$solID %in% pareto_ids &
                xda$rn %in% paid_media_vars, ]

            # For each channel, compute CV of ROAS across Pareto models
            cvs <- vapply(paid_media_vars, function(ch) {
                ch_roas <- as.numeric(
                    pareto_rows[[roas_col]][pareto_rows$rn == ch]
                )
                ch_roas <- ch_roas[!is.na(ch_roas)]
                if (length(ch_roas) < 2 || mean(ch_roas) == 0) {
                    return(NA_real_)
                }
                sd(ch_roas) / abs(mean(ch_roas))
            }, numeric(1))

            cvs <- cvs[!is.na(cvs)]
            if (length(cvs) > 0) {
                result$allocator_stability_roas_cv <- round(mean(cvs), 4)
            }
        }
    }, error = function(e) {
        message("⚠️ Failed to compute allocator stability: ", conditionMessage(e))
    })

    return(result)
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
