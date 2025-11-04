"""
Robyn MMM Trainer - Main Entry Point

This is the main entry point for the MMM (Marketing Mix Modeling) application.
Uses custom navigation to hide the main page from the sidebar.
"""

import streamlit as st

# Use custom navigation to control sidebar (Streamlit 1.31+)
st.set_page_config(
    page_title="Robyn MMM Trainer",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",  # Start with sidebar collapsed
)

from app_split_helpers import *

# Define pages for custom navigation
connect_page = st.Page(
    "pages/0_Connect_Data.py", title="Connect Data", icon="🧩"
)
map_page = st.Page("pages/1_Map_Data.py", title="Map Data", icon="🗺️")

review_page = st.Page("pages/2_Review_Data.py", title="Review Data", icon="📊")

prepare_page = st.Page(
    "pages/3_Prepare_Training_Data.py", title="Prepare Data", icon="🛠️"
)
prepare_page2 = st.Page(
    "pages/3_Prepare_Training_Datav2.py", title="Prepare Data 2", icon="🛠️"
)
experiment_page = st.Page(
    "pages/4_Run_Experiment.py", title="Run Experiment", icon="🧪"
)
results_page = st.Page(
    "pages/5_View_Results.py", title="View Results", icon="📈"
)
best_results_page = st.Page(
    "pages/6_View_Best_Results.py", title="Best Results", icon="🏆"
)

# Create navigation - this replaces the default sidebar navigation
pg = st.navigation(
    [
        connect_page,
        map_page,
        review_page,
        prepare_page,
        prepare_page2,
        experiment_page,
        results_page,
        best_results_page,
    ]
)

# Run the selected page
pg.run()
