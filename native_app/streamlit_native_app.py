"""
Streamlit entry point for MMM Trainer Snowflake Native App

This version is adapted to run within Snowflake as a Native App,
using Snowflake's native session instead of external connections.
"""

import streamlit as st
from snowflake.snowpark.context import get_active_session

# Import version info
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
from __version__ import __version__

# Configure page
st.set_page_config(
    page_title="MMM Trainer - Snowflake Native App",
    page_icon="📊",
    layout="wide",
)

# Get Snowflake session (native app context)
try:
    session = get_active_session()
    st.session_state.snowflake_session = session
except Exception as e:
    st.error(f"Failed to get Snowflake session: {e}")
    st.stop()

# Display header
st.title("📊 MMM Trainer")
st.caption(f"Marketing Mix Modeling powered by R/Robyn | Version {__version__}")

# Sidebar
with st.sidebar:
    st.header("Navigation")
    page = st.radio(
        "Select a page:",
        [
            "Home",
            "Configure Data",
            "Map Variables",
            "Run Experiment",
            "View Results",
            "Job History",
        ],
    )
    st.divider()
    st.caption(f"Version {__version__}")
    st.caption("Running in Snowflake Native App")

# Home page
if page == "Home":
    st.header("Welcome to MMM Trainer")
    
    st.markdown("""
    This application helps you run Marketing Mix Modeling experiments using the R/Robyn framework.
    
    ### Getting Started
    
    1. **Configure Data**: Connect to your marketing data table
    2. **Map Variables**: Define spend, revenue, and context variables
    3. **Run Experiment**: Launch a training job with your configuration
    4. **View Results**: Analyze model outputs and insights
    
    ### Quick Info
    """)
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric(
            "Active Jobs",
            session.sql(
                "SELECT COUNT(*) FROM app_schema.active_jobs"
            ).collect()[0][0]
        )
    
    with col2:
        st.metric(
            "Completed Today",
            session.sql(
                """
                SELECT COUNT(*) FROM app_schema.completed_jobs 
                WHERE completed_at::DATE = CURRENT_DATE()
                """
            ).collect()[0][0]
        )
    
    with col3:
        st.metric(
            "Total Experiments",
            session.sql(
                "SELECT COUNT(*) FROM app_schema.job_history"
            ).collect()[0][0]
        )
    
    st.divider()
    
    st.markdown("""
    ### About R/Robyn
    
    Robyn is Meta's open-source Marketing Mix Modeling framework that uses:
    - **Advanced algorithms**: Ridge regression, nevergrad optimization
    - **Media effects**: Adstock and saturation modeling
    - **Budget allocation**: Optimal spend recommendations
    - **Multi-touch attribution**: Understanding channel interactions
    
    ### Need Help?
    
    - Check the [documentation](https://github.com/ivana-meshed/mmm-app)
    - View [R/Robyn documentation](https://github.com/facebookexperimental/Robyn)
    """)

# Configure Data page
elif page == "Configure Data":
    st.header("Configure Data Source")
    
    st.markdown("""
    Select the table containing your marketing data. The data should include:
    - Date column (daily granularity)
    - Dependent variable (revenue, sales, etc.)
    - Media spend columns
    - Optional: context variables, organic variables
    """)
    
    # Data source selection
    data_source_type = st.radio(
        "Data Source Type:",
        ["Select Table", "Custom SQL Query"],
    )
    
    if data_source_type == "Select Table":
        col1, col2, col3 = st.columns(3)
        
        with col1:
            database = st.text_input("Database", value="")
        
        with col2:
            schema = st.text_input("Schema", value="")
        
        with col3:
            table = st.text_input("Table", value="")
        
        if database and schema and table:
            st.info(f"Selected: `{database}.{schema}.{table}`")
            
            if st.button("Preview Data"):
                try:
                    df = session.sql(
                        f"SELECT * FROM {database}.{schema}.{table} LIMIT 100"
                    ).to_pandas()
                    st.dataframe(df)
                    
                    # Store in session state
                    st.session_state.data_source = f"{database}.{schema}.{table}"
                    st.success("✓ Data preview loaded successfully")
                except Exception as e:
                    st.error(f"Error loading data: {e}")
    
    else:  # Custom SQL Query
        query = st.text_area(
            "SQL Query",
            height=150,
            placeholder="SELECT date, revenue, tv_spend, digital_spend FROM ...",
        )
        
        if query:
            if st.button("Preview Query Results"):
                try:
                    df = session.sql(query + " LIMIT 100").to_pandas()
                    st.dataframe(df)
                    
                    # Store in session state
                    st.session_state.data_query = query
                    st.success("✓ Query results loaded successfully")
                except Exception as e:
                    st.error(f"Error executing query: {e}")

# Map Variables page
elif page == "Map Variables":
    st.header("Map Variables")
    
    if "data_source" not in st.session_state and "data_query" not in st.session_state:
        st.warning("Please configure your data source first.")
    else:
        st.markdown("""
        Map your data columns to the required variable types for MMM training.
        """)
        
        # Load data columns
        try:
            if "data_source" in st.session_state:
                df = session.sql(
                    f"SELECT * FROM {st.session_state.data_source} LIMIT 1"
                ).to_pandas()
            else:
                df = session.sql(
                    st.session_state.data_query + " LIMIT 1"
                ).to_pandas()
            
            columns = df.columns.tolist()
            
            st.subheader("Required Variables")
            
            col1, col2 = st.columns(2)
            
            with col1:
                date_col = st.selectbox("Date Column", columns)
            
            with col2:
                dep_var = st.selectbox("Dependent Variable", columns)
            
            st.subheader("Media Spend Variables")
            spend_vars = st.multiselect(
                "Select media spend columns",
                [c for c in columns if c not in [date_col, dep_var]],
            )
            
            st.subheader("Optional Variables")
            
            context_vars = st.multiselect(
                "Context Variables (seasonality, promotions, etc.)",
                [c for c in columns if c not in [date_col, dep_var] + spend_vars],
            )
            
            if st.button("Save Mapping"):
                st.session_state.variable_mapping = {
                    "date_col": date_col,
                    "dep_var": dep_var,
                    "spend_vars": spend_vars,
                    "context_vars": context_vars,
                }
                st.success("✓ Variable mapping saved")
        
        except Exception as e:
            st.error(f"Error loading data columns: {e}")

# Run Experiment page
elif page == "Run Experiment":
    st.header("Run MMM Experiment")
    
    if "variable_mapping" not in st.session_state:
        st.warning("Please complete data configuration and variable mapping first.")
    else:
        st.markdown("Configure and launch your MMM training job.")
        
        # Training configuration
        col1, col2 = st.columns(2)
        
        with col1:
            country = st.text_input("Country/Region", value="US")
            preset = st.selectbox(
                "Training Preset",
                ["Test run", "Production", "Custom"],
            )
        
        with col2:
            iterations = st.number_input("Iterations", value=2000, min_value=100)
            trials = st.number_input("Trials", value=5, min_value=1)
        
        if st.button("Launch Training Job", type="primary"):
            try:
                # Build config JSON
                config = {
                    "country": country,
                    "preset": preset,
                    "iterations": iterations,
                    "trials": trials,
                    "mapping": st.session_state.variable_mapping,
                }
                
                # Call stored procedure to launch job
                result = session.call(
                    "app_schema.launch_training_job",
                    str(config)
                )
                
                st.success(f"✓ Training job launched: {result}")
            except Exception as e:
                st.error(f"Error launching job: {e}")

# View Results page
elif page == "View Results":
    st.header("View Results")
    
    st.markdown("View and analyze completed MMM experiments.")
    
    try:
        df = session.sql(
            """
            SELECT 
                job_id,
                country,
                status,
                created_at,
                completed_at,
                duration_seconds
            FROM app_schema.completed_jobs
            LIMIT 100
            """
        ).to_pandas()
        
        if len(df) > 0:
            st.dataframe(df)
            
            selected_job = st.selectbox("Select job to view details", df["JOB_ID"].tolist())
            
            if selected_job:
                st.info(f"Selected job: {selected_job}")
                # TODO: Load and display results from stage
        else:
            st.info("No completed jobs yet.")
    
    except Exception as e:
        st.error(f"Error loading results: {e}")

# Job History page
elif page == "Job History":
    st.header("Job History")
    
    try:
        # Show active jobs
        st.subheader("Active Jobs")
        active = session.sql(
            "SELECT * FROM app_schema.active_jobs"
        ).to_pandas()
        
        if len(active) > 0:
            st.dataframe(active)
        else:
            st.info("No active jobs.")
        
        st.divider()
        
        # Show all jobs
        st.subheader("All Jobs")
        all_jobs = session.sql(
            """
            SELECT 
                job_id,
                country,
                status,
                created_at,
                completed_at
            FROM app_schema.job_history
            ORDER BY created_at DESC
            LIMIT 100
            """
        ).to_pandas()
        
        st.dataframe(all_jobs)
    
    except Exception as e:
        st.error(f"Error loading job history: {e}")
