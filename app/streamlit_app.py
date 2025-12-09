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
)

from app_split_helpers import *

# Define pages for custom navigation
connect_page = st.Page(
    "pages/Connect_Data.py", title="Connect your Data", icon="🧩"
)
map_page = st.Page("pages/Map_Data.py", title="Map your Data", icon="🗺️")

review_page = st.Page(
    "pages/Review_Data.py",
    title="Review Business- & Marketing Data",
    icon="📊",
)

experiment_page = st.Page(
    "pages/Run_Experiment.py", title="Experiment", icon="🧪"
)
results_page = st.Page(
    "pages/View_Results.py", title="Results: Robyn MMM", icon="📈"
)
best_results_page = st.Page(
    "pages/View_Best_Results.py",
    title="Best models per country: Robyn MMM",
    icon="🏆",
)

# Create navigation - this replaces the default sidebar navigation
pg = st.navigation(
    [
        connect_page,
        map_page,
        review_page,
        experiment_page,
        results_page,
        best_results_page,
    ],
    position="sidebar",
)

# Run the selected page
pg.run()
