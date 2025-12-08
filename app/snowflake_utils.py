"""
Snowflake utilities for the MMM application.

This module is maintained for backward compatibility.
New code should use utils.snowflake_connector instead.

Deprecated: This module will be removed in a future version.
"""

from utils.snowflake_connector import get_table_columns, run_query_sample

# Backward compatibility aliases
get_snowflake_columns = get_table_columns
run_sql_sample = run_query_sample
