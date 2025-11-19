"""
Data validation utilities.

Provides validation functions for:
- DataFrame schema validation
- Data quality checks
- Input parameter validation
- Date range validation
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


def validate_dataframe_schema(
    df: pd.DataFrame, required_columns: List[str]
) -> Tuple[bool, str]:
    """
    Validate that a DataFrame has all required columns.

    Args:
        df: DataFrame to validate
        required_columns: List of required column names

    Returns:
        Tuple of (is_valid, error_message)
    """
    if df is None or df.empty:
        return False, "DataFrame is empty or None"

    missing = set(required_columns) - set(df.columns)
    if missing:
        return False, f"Missing required columns: {', '.join(sorted(missing))}"

    return True, ""


def validate_date_range(start_date: str, end_date: str) -> Tuple[bool, str]:
    """
    Validate date range format and logic.

    Args:
        start_date: Start date string (YYYY-MM-DD)
        end_date: End date string (YYYY-MM-DD)

    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        if start >= end:
            return False, "Start date must be before end date"

        return True, ""
    except ValueError as e:
        return False, f"Invalid date format: {e}"


def validate_numeric_range(
    value: Any,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
    allow_none: bool = False,
) -> Tuple[bool, str]:
    """
    Validate that a numeric value is within a specified range.

    Args:
        value: Value to validate
        min_value: Minimum allowed value (inclusive)
        max_value: Maximum allowed value (inclusive)
        allow_none: Whether None is a valid value

    Returns:
        Tuple of (is_valid, error_message)
    """
    if value is None:
        if allow_none:
            return True, ""
        return False, "Value cannot be None"

    try:
        num = float(value)
    except (ValueError, TypeError):
        return False, f"Value '{value}' is not a valid number"

    if min_value is not None and num < min_value:
        return False, f"Value {num} is below minimum {min_value}"

    if max_value is not None and num > max_value:
        return False, f"Value {num} exceeds maximum {max_value}"

    return True, ""


def validate_data_completeness(
    df: pd.DataFrame,
    required_columns: List[str],
    max_missing_pct: float = 0.1,
) -> Dict[str, Any]:
    """
    Check data completeness for required columns.

    Args:
        df: DataFrame to validate
        required_columns: Columns to check for missing values
        max_missing_pct: Maximum allowed percentage of missing values (0.0-1.0)

    Returns:
        Dictionary with validation results:
        {
            'is_valid': bool,
            'issues': list of issue descriptions,
            'missing_stats': dict of column -> missing percentage
        }
    """
    issues = []
    missing_stats = {}

    for col in required_columns:
        if col not in df.columns:
            issues.append(f"Column '{col}' not found in DataFrame")
            continue

        missing_count = df[col].isna().sum()
        missing_pct = missing_count / len(df)
        missing_stats[col] = missing_pct

        if missing_pct > max_missing_pct:
            issues.append(
                f"Column '{col}' has {missing_pct:.1%} missing values "
                f"(threshold: {max_missing_pct:.1%})"
            )

    return {
        "is_valid": len(issues) == 0,
        "issues": issues,
        "missing_stats": missing_stats,
    }


def validate_column_types(
    df: pd.DataFrame, type_mapping: Dict[str, str]
) -> Tuple[bool, List[str]]:
    """
    Validate that columns have expected data types.

    Args:
        df: DataFrame to validate
        type_mapping: Dictionary of column_name -> expected_type
            Expected types: 'numeric', 'date', 'string', 'boolean'

    Returns:
        Tuple of (is_valid, list of error messages)
    """
    errors = []

    for col, expected_type in type_mapping.items():
        if col not in df.columns:
            errors.append(f"Column '{col}' not found")
            continue

        col_dtype = df[col].dtype

        if expected_type == "numeric":
            if not pd.api.types.is_numeric_dtype(col_dtype):
                errors.append(
                    f"Column '{col}' expected numeric, got {col_dtype}"
                )

        elif expected_type == "date":
            if not pd.api.types.is_datetime64_any_dtype(col_dtype):
                errors.append(
                    f"Column '{col}' expected datetime, got {col_dtype}"
                )

        elif expected_type == "string":
            if not pd.api.types.is_string_dtype(
                col_dtype
            ) and not pd.api.types.is_object_dtype(col_dtype):
                errors.append(
                    f"Column '{col}' expected string, got {col_dtype}"
                )

        elif expected_type == "boolean":
            if not pd.api.types.is_bool_dtype(col_dtype):
                errors.append(
                    f"Column '{col}' expected boolean, got {col_dtype}"
                )

    return len(errors) == 0, errors


def validate_training_config(config: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Validate training configuration parameters.

    Args:
        config: Training configuration dictionary

    Returns:
        Tuple of (is_valid, error_message)
    """
    required_fields = [
        "country",
        "iterations",
        "trials",
        "dep_var",
        "paid_media_spends",
        "paid_media_vars",
    ]

    # Check required fields
    for field in required_fields:
        if field not in config:
            return False, f"Missing required field: {field}"

    # Validate iterations
    is_valid, msg = validate_numeric_range(
        config.get("iterations"), min_value=1, max_value=10000
    )
    if not is_valid:
        return False, f"Invalid iterations: {msg}"

    # Validate trials
    is_valid, msg = validate_numeric_range(
        config.get("trials"), min_value=1, max_value=20
    )
    if not is_valid:
        return False, f"Invalid trials: {msg}"

    # Validate date range if present
    if "start_date" in config and "end_date" in config:
        is_valid, msg = validate_date_range(
            config["start_date"], config["end_date"]
        )
        if not is_valid:
            return False, f"Invalid date range: {msg}"

    return True, ""
