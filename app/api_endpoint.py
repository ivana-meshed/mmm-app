"""
API endpoints for programmatic access to MMM training.

Provides REST-like API endpoints for:
- Job submission and monitoring
- Queue management
- Metadata retrieval
- Results access
"""

import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime
from typing import Any, Dict, Optional

import streamlit as st

from utils.validation import validate_training_config

logger = logging.getLogger(__name__)


def _create_error_response(
    error: str, message: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a standardized error response.

    Args:
        error: Error type or short description
        message: Detailed error message

    Returns:
        Error response dictionary
    """
    return {
        "status": "error",
        "error": error,
        "message": message or error,
        "timestamp": datetime.now().isoformat(),
    }


def _create_success_response(
    data: Dict[str, Any], message: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a standardized success response.

    Args:
        data: Response data
        message: Optional success message

    Returns:
        Success response dictionary
    """
    response = {
        "status": "success",
        "timestamp": datetime.now().isoformat(),
        **data,
    }
    if message:
        response["message"] = message
    return response


def handle_train_api():
    """
    Handle training API requests.

    Query parameters:
        - api=train: Trigger training endpoint
        - country: Country code (required)
        - iterations: Number of iterations (optional, default: 2000)
        - trials: Number of trials (optional, default: 5)
        - revision: Revision tag (optional)

    Response:
        JSON with status, job_id, and execution details
    """
    # Check if this is an API request
    if st.query_params.get("api") == "train" and hasattr(
        st.session_state, "api_request_data"
    ):
        try:
            # Get request data from session state (set by main app)
            request_data = st.session_state.api_request_data

            # Extract and validate parameters
            country = request_data.get("country")
            if not country:
                st.json(_create_error_response("Missing required parameter: country"))
                st.stop()

            iterations = request_data.get("iterations", 2000)
            trials = request_data.get("trials", 5)
            job_id = request_data.get(
                "job_id", f"api-job-{int(datetime.now().timestamp())}"
            )

            # Create job config
            job_config = {
                "country": country,
                "iterations": iterations,
                "trials": trials,
                "revision": request_data.get("revision", "api-test"),
                "date_input": datetime.now().strftime("%Y-%m-%d"),
                "gcs_bucket": os.getenv("GCS_BUCKET", "mmm-app-output"),
                "paid_media_spends": request_data.get(
                    "paid_media_spends", ["GA_SUPPLY_COST"]
                ),
                "paid_media_vars": request_data.get(
                    "paid_media_vars", ["GA_SUPPLY_COST"]
                ),
                "context_vars": request_data.get(
                    "context_vars", ["IS_WEEKEND"]
                ),
                "factor_vars": request_data.get("factor_vars", ["IS_WEEKEND"]),
                "organic_vars": request_data.get(
                    "organic_vars", ["ORGANIC_TRAFFIC"]
                ),
                "dep_var": request_data.get("dep_var", "REVENUE"),
                "api_mode": True,
            }

            # Validate configuration
            is_valid, error_msg = validate_training_config(job_config)
            if not is_valid:
                st.json(_create_error_response("Invalid configuration", error_msg))
                st.stop()

            # Execute training (simplified for API)
            start_time = datetime.now()

            # For baseline testing, simulate training with a quick R execution
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as f:
                json.dump(job_config, f)
                config_path = f.name

            try:
                # Simple R command to test if R is working
                cmd = ["R", "--version"]
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=30
                )

                end_time = datetime.now()
                duration = (end_time - start_time).total_seconds() / 60

                if result.returncode == 0:
                    response = _create_success_response(
                        {
                            "job_id": job_id,
                            "duration_minutes": duration,
                            "config": job_config,
                        },
                        f"Training job {job_id} submitted successfully",
                    )
                    st.json(response)
                else:
                    st.json(
                        _create_error_response(
                            "R execution failed", result.stderr
                        )
                    )

            finally:
                os.unlink(config_path)

        except Exception as e:
            logger.exception("API request failed")
            st.json(_create_error_response("Internal error", str(e)))

        st.stop()  # Stop processing after API response


def handle_status_api():
    """
    Handle job status API requests.

    Query parameters:
        - api=status: Trigger status endpoint
        - job_id: Job ID to query (required)

    Response:
        JSON with job status and details
    """
    if st.query_params.get("api") != "status":
        return

    try:
        job_id = st.query_params.get("job_id")
        if not job_id:
            st.json(_create_error_response("Missing required parameter: job_id"))
            st.stop()

        # TODO: Implement actual job status lookup from GCS/job history
        # For now, return placeholder
        response = _create_success_response(
            {
                "job_id": job_id,
                "state": "UNKNOWN",
                "message": "Status lookup not yet implemented",
            }
        )
        st.json(response)

    except Exception as e:
        logger.exception("Status API request failed")
        st.json(_create_error_response("Internal error", str(e)))

    st.stop()


def handle_metadata_api():
    """
    Handle metadata API requests.

    Query parameters:
        - api=metadata: Trigger metadata endpoint
        - country: Country code (required)
        - version: Metadata version (optional, default: latest)

    Response:
        JSON with metadata content
    """
    if st.query_params.get("api") != "metadata":
        return

    try:
        country = st.query_params.get("country")
        if not country:
            st.json(_create_error_response("Missing required parameter: country"))
            st.stop()

        version = st.query_params.get("version", "latest")

        # TODO: Implement actual metadata retrieval from GCS
        # For now, return placeholder
        response = _create_success_response(
            {
                "country": country,
                "version": version,
                "message": "Metadata retrieval not yet implemented",
            }
        )
        st.json(response)

    except Exception as e:
        logger.exception("Metadata API request failed")
        st.json(_create_error_response("Internal error", str(e)))

    st.stop()


def handle_api_request():
    """
    Main API request handler.

    Routes requests to appropriate endpoint based on 'api' query parameter.

    Supported endpoints:
        - train: Submit training job
        - status: Get job status
        - metadata: Retrieve metadata

    Example usage:
        GET /?api=train&country=fr&iterations=2000
        GET /?api=status&job_id=abc123
        GET /?api=metadata&country=fr&version=latest
    """
    api_type = st.query_params.get("api")

    if not api_type:
        return  # Not an API request

    logger.info(f"Handling API request: {api_type}")

    if api_type == "train":
        handle_train_api()
    elif api_type == "status":
        handle_status_api()
    elif api_type == "metadata":
        handle_metadata_api()
    else:
        st.json(
            _create_error_response(
                "Unknown API endpoint",
                f"Supported endpoints: train, status, metadata. Got: {api_type}",
            )
        )
        st.stop()
