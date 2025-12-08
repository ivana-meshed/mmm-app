"""
Cache Management Page

Provides interface for viewing and managing Snowflake query cache.
Helps users understand cache performance and manually clear cache when needed.
"""

import streamlit as st
from app_shared import require_login_and_domain
from utils.snowflake_cache import clear_snowflake_cache, get_cache_stats

# Authentication
require_login_and_domain()
ensure_session_defaults()

st.title("⚡ Cache Management")

st.markdown(
    """
This page helps you manage the Snowflake query cache, which reduces costs by avoiding 
repeated execution of identical queries.

**Expected Savings:** ~70% reduction in Snowflake compute costs with typical usage patterns.
"""
)

# Get cache statistics
stats = get_cache_stats()

# Display cache stats
st.subheader("📊 Cache Statistics")

col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "In-Memory Cache",
        f"{stats['in_memory_count']} queries",
        help=f"TTL: {stats['in_memory_ttl_seconds'] // 60} minutes",
    )

with col2:
    st.metric(
        "GCS Cache",
        f"{stats['gcs_count']} queries",
        help=f"TTL: {stats['gcs_ttl_seconds'] // 3600} hours",
    )

with col3:
    st.metric(
        "GCS Storage",
        f"{stats['gcs_total_size_mb']} MB",
        help="Total size of cached query results in GCS",
    )

st.divider()

# Cache explanation
with st.expander("ℹ️ How Query Caching Works"):
    st.markdown(
        """
    ### Two-Tier Caching Strategy
    
    1. **In-Memory Cache (Fast)**
       - TTL: 1 hour
       - Immediate access to recently used queries
       - Lost when application restarts
    
    2. **GCS Persistent Cache (Durable)**
       - TTL: 24 hours
       - Survives application restarts
       - Shared across all users
       - Stored as compressed Parquet files
    
    ### How It Saves Money
    
    - Without caching: Every query hits Snowflake warehouse
    - With caching: Only new/expired queries hit Snowflake
    - **Typical savings: 70% reduction in Snowflake compute costs**
    
    ### When Cache is Used
    
    The cache is automatically used for:
    - Data preview queries
    - Metadata lookups
    - Report generation
    - Dashboard queries
    
    The cache is **NOT** used for:
    - Write operations (INSERT, UPDATE, DELETE)
    - CREATE/ALTER/DROP statements
    - Queries explicitly marked as `use_cache=False`
    
    ### Cache Key
    
    Queries are matched based on normalized SQL:
    - Whitespace differences are ignored
    - Case differences are ignored
    - `SELECT * FROM table` and `select * from table` use the same cache
    """
    )

st.divider()

# Cache management actions
st.subheader("🔧 Cache Actions")

col1, col2 = st.columns(2)

with col1:
    if st.button("🗑️ Clear All Cache", type="primary", width='stretch'):
        with st.spinner("Clearing cache..."):
            clear_snowflake_cache()
            st.success("✅ All cache cleared successfully!")
            st.rerun()

with col2:
    if st.button("🔄 Refresh Statistics", width='stretch'):
        st.rerun()

st.divider()

# Cost savings calculator
st.subheader("💰 Cost Savings Calculator")

st.markdown(
    """
Estimate your monthly savings based on cache hit rate and query volume.
"""
)

col1, col2 = st.columns(2)

with col1:
    queries_per_month = st.number_input(
        "Queries per month",
        min_value=10,
        max_value=100000,
        value=500,
        step=100,
        help="Total number of queries executed per month",
    )

with col2:
    cache_hit_rate = st.slider(
        "Cache hit rate (%)",
        min_value=0,
        max_value=100,
        value=70,
        help="Percentage of queries served from cache (typical: 70%)",
    )

# Calculate costs
CREDITS_PER_100_QUERIES = 10  # Assumption from cost estimate
CREDIT_COST = 2.0  # USD per credit

queries_hitting_snowflake = queries_per_month * (1 - cache_hit_rate / 100)
credits_needed = (queries_hitting_snowflake / 100) * CREDITS_PER_100_QUERIES
cost_with_cache = credits_needed * CREDIT_COST

credits_without_cache = (queries_per_month / 100) * CREDITS_PER_100_QUERIES
cost_without_cache = credits_without_cache * CREDIT_COST

savings = cost_without_cache - cost_with_cache

# Display results
st.markdown("### 📈 Estimated Monthly Costs")

col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "Without Cache",
        f"${cost_without_cache:.2f}",
        help=f"{credits_without_cache:.1f} Snowflake credits",
    )

with col2:
    st.metric(
        "With Cache",
        f"${cost_with_cache:.2f}",
        delta=f"-${savings:.2f}",
        delta_color="inverse",
        help=f"{credits_needed:.1f} Snowflake credits",
    )

with col3:
    st.metric(
        "Monthly Savings",
        f"${savings:.2f}",
        delta=f"{cache_hit_rate}% reduction",
        help="Estimated monthly cost savings from caching",
    )

st.info(
    f"💡 **Tip:** At {cache_hit_rate}% cache hit rate, you save "
    f"**${savings:.2f}/month** ({cache_hit_rate}% reduction in Snowflake costs)"
)
