"""
Robyn MMM Trainer - Home Page

This is the main entry point for the MMM (Marketing Mix Modeling) application.
It provides navigation to the main workflows:
1. Connect Data - Set up Snowflake connection
2. Map Data - Map columns and configure metadata
3. Run Experiment - Execute single or batch MMM experiments

The application is built with Streamlit and deployed on Google Cloud Run.
"""

# streamlit_app.py (proposed new Home)
import streamlit as st

st.set_page_config(
    page_title="Robyn MMM Trainer", page_icon="📊", layout="wide"
)
from app_split_helpers import *

st.write(
    """
1. **Connect your Data** – set up your Snowflake connection.
2. **Map Your Data** – map columns and save/load metadata.
3. **Experiment** – run single or queued Robyn experiments.
"""
)

st.divider()
col1, col2, col3 = st.columns(3)
with col1:
    try:
        if st.button("🧩 Connect your Data", use_container_width=True):
            import streamlit as stlib

            stlib.switch_page("pages/0_Connect_Data.py")
    except Exception:
        st.page_link("pages/0_Connect_Data.py", label="🧩 Connect your Data")

with col2:
    try:
        if st.button("🗺️ Map Your Data", use_container_width=True):
            import streamlit as stlib

            stlib.switch_page("pages/1_Map_Data.py")
    except Exception:
        st.page_link("pages/1_Map_Data.py", label="🗺️ Map Your Data")

with col3:
    try:
        if st.button("🧪 Experiment", use_container_width=True):
            import streamlit as stlib

            stlib.switch_page("pages/4_Experiment.py")
    except Exception:
        st.page_link("pages/4_Experiment.py", label="🧪 Experiment")
