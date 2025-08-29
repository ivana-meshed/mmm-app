"""
Container warming script to pre-load dependencies and authenticate services
"""
import os
import sys
import time
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
import psutil

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
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
            import pandas as pd
            import numpy as np
            import pyarrow as pa
            import pyarrow.parquet as pq
            
            # Import cloud libraries
            from google.cloud import storage, secretmanager
            from google.auth import default
            
            # Import streamlit
            import streamlit as st
            
            # Import snowflake
            import snowflake.connector
            
            # Pre-create commonly used objects
            _ = storage.Client()  # Initialize GCS client
            _ = pd.DataFrame({'test': [1, 2, 3]})  # Pre-allocate pandas
            _ = np.array([1, 2, 3])  # Pre-allocate numpy
            
            elapsed = time.time() - start_time
            logger.info(f"✅ Python environment warmed in {elapsed:.2f}s")
            self.warming_status['python'] = True
            
        except Exception as e:
            logger.error(f"❌ Failed to warm Python environment: {e}")
            self.warming_status['python'] = False
    
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
            max_cores <- as.numeric(Sys.getenv("R_MAX_CORES", "4"))
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
                ['R', '--slave', '--vanilla', '-e', r_warmup_script],
                capture_output=True,
                text=True,
                timeout=60  # 1 minute timeout
            )
            
            if result.returncode == 0:
                elapsed = time.time() - start_time
                logger.info(f"✅ R environment warmed in {elapsed:.2f}s")
                self.warming_status['r'] = True
            else:
                logger.error(f"❌ R warming failed: {result.stderr}")
                self.warming_status['r'] = False
                
        except subprocess.TimeoutExpired:
            logger.error("❌ R environment warming timed out")
            self.warming_status['r'] = False
        except Exception as e:
            logger.error(f"❌ Failed to warm R environment: {e}")
            self.warming_status['r'] = False
    
    def warm_gcs_authentication(self):
        """Pre-authenticate with Google Cloud Services"""
        logger.info("☁️ Warming GCS authentication...")
        start_time = time.time()
        
        try:
            from google.cloud import storage
            from google.auth import default
            
            # Get default credentials
            credentials, project = default()
            
            # Initialize storage client
            storage_client = storage.Client(credentials=credentials)
            
            # Test bucket access
            bucket_name = os.getenv('GCS_BUCKET', 'mmm-app-output')
            bucket = storage_client.bucket(bucket_name)
            
            # Perform lightweight operation to verify access
            list(storage_client.list_blobs(bucket, max_results=1))
            
            elapsed = time.time() - start_time
            logger.info(f"✅ GCS authentication warmed in {elapsed:.2f}s")
            self.warming_status['gcs'] = True
            
        except Exception as e:
            logger.error(f"❌ Failed to warm GCS authentication: {e}")
            self.warming_status['gcs'] = False
    
    def warm_system_resources(self):
        """Warm system-level resources"""
        logger.info("🖥️ Warming system resources...")
        start_time = time.time()
        
        try:
            # Get system information
            cpu_count = os.cpu_count()
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            logger.info(f"System specs: {cpu_count} CPUs, {memory.total//1024**3}GB RAM")
            
            # Set optimal environment variables
            os.environ['R_MAX_CORES'] = str(cpu_count)
            os.environ['OMP_NUM_THREADS'] = str(cpu_count) 
            os.environ['OPENBLAS_NUM_THREADS'] = str(cpu_count)
            
            # Pre-allocate some memory to reduce fragmentation
            dummy_data = [0] * 1000000  # Allocate ~8MB
            del dummy_data
            
            elapsed = time.time() - start_time
            logger.info(f"✅ System resources warmed in {elapsed:.2f}s")
            self.warming_status['system'] = True
            
        except Exception as e:
            logger.error(f"❌ Failed to warm system resources: {e}")
            self.warming_status['system'] = False
    
    def run_parallel_warming(self):
        """Run all warming tasks in parallel"""
        logger.info("🔥 Starting container warming process...")
        total_start_time = time.time()
        
        # Define warming tasks
        warming_tasks = [
            ('system', self.warm_system_resources),
            ('python', self.warm_python_environment),
            ('gcs', self.warm_gcs_authentication),
            ('r', self.warm_r_environment)  # R last as it's most time-consuming
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
        successful_tasks = sum(1 for status in self.warming_status.values() if status)
        total_tasks = len(self.warming_status)
        
        logger.info(f"🎯 Container warming completed: {successful_tasks}/{total_tasks} tasks successful in {total_elapsed:.2f}s")
        
        # Write warming status to file for health checks
        try:
            import json
            status_file = '/tmp/warming_status.json'
            with open(status_file, 'w') as f:
                json.dump({
                    'warming_status': self.warming_status,
                    'warming_time': total_elapsed,
                    'timestamp': time.time()
                }, f)
        except Exception as e:
            logger.warning(f"Could not write warming status: {e}")
        
        return self.warming_status

def main():
    """Main warming function"""
    warmer = ContainerWarmer()
    status = warmer.run_parallel_warming()
    
    # Exit with error code if critical components failed
    critical_components = ['python', 'gcs']
    if not all(status.get(comp, False) for comp in critical_components):
        logger.error("❌ Critical components failed to warm")
        sys.exit(1)
    else:
        logger.info("✅ Container is warm and ready")
        sys.exit(0)

if __name__ == "__main__":
    main()