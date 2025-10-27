"""
Container warming script to pre-load dependencies and authenticate services
"""

import logging
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import psutil
import streamlit as st

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class ContainerWarmer:
    def __init__(self):
        self.warming_tasks = []
        self.warming_status = {}

    def warm_python_environment(self):
        """Pre-import critical Python packages"""
        logger.info("🐍 Warming Python environment...")
        start_time = time.time()

        try:
            # Import data processing libraries
            import numpy as np
            import pandas as pd
            import pyarrow as pa
            import pyarrow.parquet as pq

            # Import snowflake
            import snowflake.connector

            # Import streamlit
            import streamlit as st
            from google.auth import default

            # Import cloud libraries
            from google.cloud import secretmanager, storage

            # Pre-create commonly used objects
            _ = storage.Client()  # Initialize GCS client
            _ = pd.DataFrame({"test": [1, 2, 3]})  # Pre-allocate pandas
            _ = np.array([1, 2, 3])  # Pre-allocate numpy

            elapsed = time.time() - start_time
            logger.info(f"✅ Python environment warmed in {elapsed:.2f}s")
            self.warming_status["python"] = True

        except Exception as e:
            logger.error(f"❌ Failed to warm Python environment: {e}")
            self.warming_status["python"] = False

    def warm_r_environment(self):
        """Pre-load R and critical packages"""
        logger.info("📊 Warming R environment...")
        start_time = time.time()

        try:
            # R script to pre-load packages
            r_warmup_script = """
            # Suppress package startup messages
            suppressPackageStartupMessages({
                library(jsonlite)
                library(dplyr)
                library(tidyr)
                library(readr)
                library(arrow)
                library(lubridate)
                library(stringr)
                library(googleCloudStorageR)
                library(reticulate)
                library(parallel)
                library(future)
            })

            # Configure parallel processing
            max_cores <- as.numeric(Sys.getenv("R_MAX_CORES", "8"))
            plan(multisession, workers = max_cores)

            # Configure reticulate
            Sys.setenv(RETICULATE_PYTHON = "/usr/bin/python3")
            Sys.setenv(RETICULATE_AUTOCONFIGURE = "0")

            # Test basic functionality
            df <- data.frame(x = 1:10, y = letters[1:10])
            result <- df %>% filter(x > 5)

            cat("R environment warmed successfully\\n")
            """

            # Execute R warming script
            result = subprocess.run(
                ["R", "--slave", "--vanilla", "-e", r_warmup_script],
                capture_output=True,
                text=True,
                timeout=60,  # 1 minute timeout
            )

            if result.returncode == 0:
                elapsed = time.time() - start_time
                logger.info(f"✅ R environment warmed in {elapsed:.2f}s")
                self.warming_status["r"] = True
            else:
                logger.error(f"❌ R warming failed: {result.stderr}")
                self.warming_status["r"] = False

        except subprocess.TimeoutExpired:
            logger.error("❌ R environment warming timed out")
            self.warming_status["r"] = False
        except Exception as e:
            logger.error(f"❌ Failed to warm R environment: {e}")
            self.warming_status["r"] = False

    def warm_gcs_authentication(self):
        """Pre-authenticate with Google Cloud Services"""
        logger.info("☁️ Warming GCS authentication...")
        start_time = time.time()

        try:
            from google.auth import default
            from google.cloud import storage

            # Get default credentials
            credentials, project = default()

            # Initialize storage client
            storage_client = storage.Client(credentials=credentials)

            # Test bucket access
            bucket_name = os.getenv("GCS_BUCKET", "mmm-app-output")
            bucket = storage_client.bucket(bucket_name)

            # Perform lightweight operation to verify access
            list(storage_client.list_blobs(bucket, max_results=1))

            elapsed = time.time() - start_time
            logger.info(f"✅ GCS authentication warmed in {elapsed:.2f}s")
            self.warming_status["gcs"] = True

        except Exception as e:
            logger.error(f"❌ Failed to warm GCS authentication: {e}")
            self.warming_status["gcs"] = False

    def warm_system_resources(self):
        """Warm system-level resources"""
        logger.info("🖥️ Warming system resources...")
        start_time = time.time()

        try:
            # Get system information
            cpu_count = os.cpu_count()
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage("/")

            logger.info(
                f"System specs: {cpu_count} CPUs, {memory.total//1024**3}GB RAM"
            )

            # Set optimal environment variables
            os.environ["R_MAX_CORES"] = str(cpu_count)
            os.environ["OMP_NUM_THREADS"] = str(cpu_count)
            os.environ["OPENBLAS_NUM_THREADS"] = str(cpu_count)

            # Pre-allocate some memory to reduce fragmentation
            dummy_data = [0] * 1000000  # Allocate ~8MB
            del dummy_data

            elapsed = time.time() - start_time
            logger.info(f"✅ System resources warmed in {elapsed:.2f}s")
            self.warming_status["system"] = True

        except Exception as e:
            logger.error(f"❌ Failed to warm system resources: {e}")
            self.warming_status["system"] = False

    def run_parallel_warming(self):
        """Run all warming tasks in parallel"""
        logger.info("🔥 Starting container warming process...")
        total_start_time = time.time()

        # Define warming tasks
        warming_tasks = [
            ("system", self.warm_system_resources),
            ("python", self.warm_python_environment),
            ("gcs", self.warm_gcs_authentication),
            (
                "r",
                self.warm_r_environment,
            ),  # R last as it's most time-consuming
        ]

        # Execute tasks in parallel
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_task = {
                executor.submit(task_func): task_name
                for task_name, task_func in warming_tasks
            }

            for future in as_completed(future_to_task):
                task_name = future_to_task[future]
                try:
                    future.result()
                    logger.info(f"✅ {task_name} warming completed")
                except Exception as e:
                    logger.error(f"❌ {task_name} warming failed: {e}")

        total_elapsed = time.time() - total_start_time
        successful_tasks = sum(
            1 for status in self.warming_status.values() if status
        )
        total_tasks = len(self.warming_status)

        logger.info(
            f"🎯 Container warming completed: {successful_tasks}/{total_tasks} tasks successful in {total_elapsed:.2f}s"
        )

        # Write warming status to file for health checks
        try:
            import json

            status_file = "/tmp/warming_status.json"
            with open(status_file, "w") as f:
                json.dump(
                    {
                        "warming_status": self.warming_status,
                        "warming_time": total_elapsed,
                        "timestamp": time.time(),
                    },
                    f,
                )
        except Exception as e:
            logger.warning(f"Could not write warming status: {e}")

        return self.warming_status


def main():
    """Main warming function"""
    warmer = ContainerWarmer()
    status = warmer.run_parallel_warming()

    # Exit with error code if critical components failed
    critical_components = ["python", "gcs"]
    if not all(status.get(comp, False) for comp in critical_components):
        logger.error("❌ Critical components failed to warm")
        sys.exit(1)
    else:
        logger.info("✅ Container is warm and ready")
        sys.exit(0)


if __name__ == "__main__":
    main()

    # ===================== BATCH QUEUE (CSV) =====================
    with st.expander(
        "📚 Batch queue (CSV) — queue & run multiple jobs sequentially",
        expanded=False,
    ):
        # Queue name + Load/Save
        cqn1, cqn2, cqn3 = st.columns([2, 1, 1])
        new_qname = cqn1.text_input(
            "Queue name",
            value=st.session_state["queue_name"],
            help="Persists to GCS under robyn-queues/<name>/queue.json",
        )
        if new_qname != st.session_state["queue_name"]:
            st.session_state["queue_name"] = new_qname

        if cqn2.button("⬇️ Load from GCS"):
            st.session_state.job_queue = load_queue_from_gcs(
                st.session_state.queue_name
            )
            st.success(f"Loaded queue '{st.session_state.queue_name}' from GCS")

        if cqn3.button("⬆️ Save to GCS"):
            save_queue_to_gcs(
                st.session_state.queue_name, st.session_state.job_queue
            )
            st.success(f"Saved queue '{st.session_state.queue_name}' to GCS")

        st.markdown(
            """
    Upload a CSV where each row defines a training run. **Supported columns** (all optional except `country`, `revision`, and data source):

    - `country`, `revision`, `date_input`, `iterations`, `trials`, `train_size`
    - `paid_media_spends`, `paid_media_vars`, `context_vars`, `factor_vars`, `organic_vars`
    - `gcs_bucket` (optional override per row)
    - **Data**: one of `query` **or** `table`
    - `annotations_gcs_path` (optional gs:// path)
            """
        )

        # Template & Example CSVs
        template = pd.DataFrame(
            [
                {
                    "country": "fr",
                    "revision": "r100",
                    "date_input": time.strftime("%Y-%m-%d"),
                    "iterations": 200,
                    "trials": 5,
                    "train_size": "0.7,0.9",
                    "paid_media_spends": "GA_SUPPLY_COST, GA_DEMAND_COST, BING_DEMAND_COST, META_DEMAND_COST, TV_COST, PARTNERSHIP_COSTS",
                    "paid_media_vars": "GA_SUPPLY_COST, GA_DEMAND_COST, BING_DEMAND_COST, META_DEMAND_COST, TV_COST, PARTNERSHIP_COSTS",
                    "context_vars": "IS_WEEKEND,TV_IS_ON",
                    "factor_vars": "IS_WEEKEND,TV_IS_ON",
                    "organic_vars": "ORGANIC_TRAFFIC",
                    "gcs_bucket": st.session_state["gcs_bucket"],
                    "table": "",
                    "query": "SELECT * FROM MESHED_BUYCYCLE.GROWTH.SOME_TABLE",
                    "annotations_gcs_path": "",
                }
            ]
        )

        example = pd.DataFrame(
            [
                {
                    "country": "fr",
                    "revision": "r101",
                    "date_input": time.strftime("%Y-%m-%d"),
                    "iterations": 300,
                    "trials": 6,
                    "train_size": "0.7,0.9",
                    "paid_media_spends": "GA_SUPPLY_COST, GA_DEMAND_COST, META_DEMAND_COST, TV_COST",
                    "paid_media_vars": "GA_SUPPLY_COST, GA_DEMAND_COST, META_DEMAND_COST, TV_COST",
                    "context_vars": "IS_WEEKEND,TV_IS_ON",
                    "factor_vars": "IS_WEEKEND,TV_IS_ON",
                    "organic_vars": "ORGANIC_TRAFFIC",
                    "gcs_bucket": st.session_state["gcs_bucket"],
                    "table": "MESHED_BUYCYCLE.GROWTH.TABLE_A",
                    "query": "",  # either table or query
                    "annotations_gcs_path": "",
                },
                {
                    "country": "de",
                    "revision": "r102",
                    "date_input": time.strftime("%Y-%m-%d"),
                    "iterations": 200,
                    "trials": 5,
                    "train_size": "0.75,0.9",
                    "paid_media_spends": "BING_DEMAND_COST, META_DEMAND_COST, TV_COST, PARTNERSHIP_COSTS",
                    "paid_media_vars": "BING_DEMAND_COST, META_DEMAND_COST, TV_COST, PARTNERSHIP_COSTS",
                    "context_vars": "IS_WEEKEND",
                    "factor_vars": "IS_WEEKEND",
                    "organic_vars": "ORGANIC_TRAFFIC",
                    "gcs_bucket": st.session_state["gcs_bucket"],
                    "table": "",
                    "query": "SELECT * FROM MESHED_BUYCYCLE.GROWTH.TABLE_B WHERE COUNTRY='DE'",
                    "annotations_gcs_path": "",
                },
            ]
        )

        col_dl1, col_dl2 = st.columns(2)
        col_dl1.download_button(
            "Download CSV template",
            data=template.to_csv(index=False),
            file_name="robyn_batch_template.csv",
            mime="text/csv",
        )
        col_dl2.download_button(
            "Download example CSV (2 jobs)",
            data=example.to_csv(index=False),
            file_name="robyn_batch_example.csv",
            mime="text/csv",
        )

        up = st.file_uploader("Upload batch CSV", type=["csv"], key="batch_csv")
        parsed_df = None
        if up:
            try:
                parsed_df = pd.read_csv(up)
                st.success(f"Loaded {len(parsed_df)} rows")
                st.dataframe(parsed_df.head(), use_container_width=True)
            except Exception as e:
                st.error(f"Failed to parse CSV: {e}")

        def _normalize_row(row: pd.Series) -> dict:
            def _g(v, default):
                return (
                    row.get(v) if (v in row and pd.notna(row[v])) else default
                )

            return {
                "country": str(_g("country", country)),
                "revision": str(_g("revision", revision)),
                "date_input": str(_g("date_input", date_input)),
                "iterations": int(_g("iterations", iterations)),
                "trials": int(_g("trials", trials)),
                "train_size": str(_g("train_size", train_size)),
                "paid_media_spends": str(
                    _g("paid_media_spends", paid_media_spends)
                ),
                "paid_media_vars": str(_g("paid_media_vars", paid_media_vars)),
                "context_vars": str(_g("context_vars", context_vars)),
                "factor_vars": str(_g("factor_vars", factor_vars)),
                "organic_vars": str(_g("organic_vars", organic_vars)),
                "gcs_bucket": str(
                    _g("gcs_bucket", st.session_state["gcs_bucket"])
                ),
                "table": str(_g("table", table or "")),
                "query": str(_g("query", query or "")),
                "annotations_gcs_path": str(_g("annotations_gcs_path", "")),
            }

        c_left, c_right = st.columns(2)
        if c_left.button("➕ Enqueue all rows", disabled=(parsed_df is None)):
            if parsed_df is not None:
                # next id after current max
                next_id = (
                    max(
                        [e["id"] for e in st.session_state.job_queue], default=0
                    )
                    + 1
                )
                new_entries = []
                for i, row in parsed_df.iterrows():
                    params = _normalize_row(row)
                    if not (params.get("query") or params.get("table")):
                        continue
                    new_entries.append(
                        {
                            "id": next_id + i,
                            "params": params,
                            "status": "PENDING",
                            "timestamp": None,
                            "execution_name": None,
                            "gcs_prefix": None,
                            "message": "",
                        }
                    )
                st.session_state.job_queue.extend(new_entries)
                save_queue_to_gcs(
                    st.session_state.queue_name, st.session_state.job_queue
                )
                st.success(
                    f"Enqueued {len(new_entries)} job(s) and saved to GCS."
                )

        if c_right.button("🧹 Clear queue"):
            st.session_state["job_queue"] = []
            st.session_state["queue_running"] = False
            save_queue_to_gcs(st.session_state.queue_name, [])
            st.success("Queue cleared & saved to GCS.")

        # Queue controls
        st.caption(
            f"Queue status: {'▶️ RUNNING' if st.session_state.queue_running else '⏸️ STOPPED'} · "
            f"{sum(e['status']=='PENDING' for e in st.session_state.job_queue)} pending · "
            f"{sum(e['status']=='RUNNING' for e in st.session_state.job_queue)} running"
        )
        qc1, qc2, qc3, qc4 = st.columns(4)
        if qc1.button(
            "▶️ Start Queue", disabled=(len(st.session_state.job_queue) == 0)
        ):
            st.session_state["queue_running"] = True
            save_queue_to_gcs(
                st.session_state.queue_name, st.session_state.job_queue
            )
        if qc2.button("⏸️ Stop Queue"):
            st.session_state["queue_running"] = False
            save_queue_to_gcs(
                st.session_state.queue_name, st.session_state.job_queue
            )
        if qc3.button("⏭️ Process Next Step"):
            pass  # tick happens below
        if qc4.button("💾 Save now"):
            save_queue_to_gcs(
                st.session_state.queue_name, st.session_state.job_queue
            )
            st.success("Queue saved to GCS.")

        # Queue table
        if st.session_state.job_queue:
            df_queue = pd.DataFrame(
                [
                    {
                        "ID": e["id"],
                        "Status": e["status"],
                        "Country": e["params"]["country"],
                        "Revision": e["params"]["revision"],
                        "Timestamp": e.get("timestamp", ""),
                        "Exec": (e.get("execution_name", "") or "").split("/")[
                            -1
                        ],
                        "Msg": e.get("message", ""),
                    }
                    for e in st.session_state.job_queue
                ]
            )
            st.dataframe(df_queue, use_container_width=True)
