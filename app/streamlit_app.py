"""
Robyn MMM Trainer - Main Entry Point

This is the main entry point for the MMM (Marketing Mix Modeling) application.
Uses custom navigation to hide the main page from the sidebar.
"""

# ULTRA-VERBOSE LOGGING: Add at very start to trace execution
import logging
import sys

# Set up logging immediately
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

# Log startup - this should ALWAYS appear if app runs
logger.info("="*80)
logger.info("[APP_STARTUP] Streamlit app starting...")
logger.info(f"[APP_STARTUP] Python version: {sys.version}")
logger.info("="*80)

import streamlit as st

# Log after streamlit import
logger.info("[APP_STARTUP] Streamlit imported successfully")

# Use custom navigation to control sidebar (Streamlit 1.31+)
st.set_page_config(
    page_title="Robyn MMM Trainer",
    page_icon="📊",
    layout="wide",
)

logger.info("[APP_STARTUP] Page config set")

# Log query params immediately (before any processing)
try:
    query_params_obj = st.query_params
    logger.info(f"[APP_STARTUP] st.query_params type: {type(query_params_obj)}")
    logger.info(f"[APP_STARTUP] st.query_params value: {query_params_obj}")
    logger.info(f"[APP_STARTUP] st.query_params bool: {bool(query_params_obj)}")
    if query_params_obj:
        logger.info(f"[APP_STARTUP] st.query_params keys: {list(query_params_obj.keys())}")
except Exception as e:
    logger.error(f"[APP_STARTUP] Error accessing query_params: {e}", exc_info=True)

logger.info("[APP_STARTUP] About to import app_split_helpers...")

from app_split_helpers import *

logger.info("[APP_STARTUP] app_split_helpers imported successfully")

# Handle queue tick endpoint early (before navigation setup)
# This needs to be called explicitly, not at module import time
logger.info("[APP_STARTUP] About to call handle_queue_tick_if_requested()...")
handle_queue_tick_if_requested()
logger.info("[APP_STARTUP] handle_queue_tick_if_requested() returned")

# Define pages for custom navigation
connect_page = st.Page(
    "nav/Connect_Data.py", title="1. Connect Data", icon="🧩"
)
map_page = st.Page("nav/Map_Data.py", title="2. Map Data", icon="🗺️")

prepare_training_data_page = st.Page(
    "nav/Prepare_Training_Data.py",
    title="4. Prepare Training Data",
    icon="🔧",
)

review_page = st.Page(
    "nav/Validate_Mapping.py",
    title="3. Validate Mapping",
    icon="📊",
)

# experiment_page = st.Page("nav/Run_Models.py", title="5. Run Models", icon="🧪")

experiment_page = st.Page(
    "nav/Run_Experiment.py", title="5. Run Models", icon="🧪"
)

results_page = st.Page(
    "nav/View_Results.py", title="6. View Model Results", icon="📈"
)
best_results_page = st.Page(
    "nav/View_Best_Results.py",
    title="7. View Best Models",
    icon="🏆",
)

cache_management_page = st.Page(
    "nav/Cache_Management.py",
    title="Cache Management",
    icon="⚡",
)

model_stability_page = st.Page(
    "nav/Review_Model_Stability.py",
    title="8. View Model Stability",
    icon="🛡️",
)


# Create navigation - this replaces the default sidebar navigation
pg = st.navigation(
    [
        connect_page,
        map_page,
        review_page,
        prepare_training_data_page,
        experiment_page,
        results_page,
        best_results_page,
        model_stability_page,
        # cache_management_page,
    ],
    position="sidebar",
)

# Run the selected page
pg.run()
