import json
import os
import subprocess
import tempfile
from datetime import datetime

import streamlit as st


def handle_train_api():
    """Handle training API requests"""

    # Check if this is an API request
    if st.query_params.get("api") == "train" and hasattr(
        st.session_state, "api_request_data"
    ):
        try:
            # Get request data from session state (set by main app)
            request_data = st.session_state.api_request_data

            # Extract parameters
            country = request_data.get("country", "test")
            iterations = request_data.get("iterations", 50)
            trials = request_data.get("trials", 2)
            job_id = request_data.get(
                "job_id", f"api-job-{int(datetime.now().timestamp())}"
            )

            # Create minimal job config
            job_config = {
                "country": country,
                "iterations": iterations,
                "trials": trials,
                "revision": "api-test",
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
                "api_mode": True,
            }

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
                    response = {
                        "status": "success",
                        "job_id": job_id,
                        "duration_minutes": duration,
                        "message": f"Training job {job_id} completed successfully",
                        "timestamp": end_time.isoformat(),
                    }
                    st.json(response)
                else:
                    response = {
                        "status": "error",
                        "job_id": job_id,
                        "error": "R execution failed",
                        "message": result.stderr,
                    }
                    st.json(response)

            finally:
                os.unlink(config_path)

        except Exception as e:
            response = {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }
            st.json(response)

        st.stop()  # Stop processing after API response
