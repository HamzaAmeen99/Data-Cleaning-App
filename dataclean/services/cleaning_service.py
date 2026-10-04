"""Deterministic cleaning operations.

Each function takes a DataFrame plus explicit user-approved parameters and
returns the cleaned DataFrame together with a count of what changed.
Nothing here silently guesses on the user's behalf: merging categories,
changing case or dropping columns only happens when explicitly requested.
"""

import pandas as pd

from utils.validators import validate_column


def remove_duplicate_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Remove fully duplicated rows.

    Returns:
        (cleaned_df, number_of_rows_removed)
    """
    removed = int(df.duplicated().sum())
    return df.drop_duplicates(), removed


def trim_whitespace(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Strip leading/trailing whitespace from every text (object) column.

    Returns:
        (cleaned_df, number_of_cells_changed)
    """
    cleaned = df.copy()
    changed = 0
    for col in cleaned.select_dtypes(include="object").columns:
        original = cleaned[col]
        stripped = original.astype(str).str.strip()
        # Only count non-null cells that actually changed
        mask = original.notna() & (original != stripped)
        changed += int(mask.sum())
        cleaned[col] = original.where(original.isna(), stripped)
    return cleaned, changed


def standardize_text_case(
    df: pd.DataFrame, column: str, case_type: str
) -> tuple[pd.DataFrame, int]:
    """Convert a text column to lower, upper or title case.

    Preserves existing whitespace and column dtypes without unintended stripping.

    Returns:
        (cleaned_df, number_of_cells_changed)
    """
    if not validate_column(df, column):
        return df, 0

    cleaners = {
        "lower": str.lower,
        "upper": str.upper,
        "title": str.title,
    }
    cleaner = cleaners.get(case_type)
    if cleaner is None:
        return df, 0

    cleaned = df.copy()
    original = cleaned[column]
    converted = original.map(
        lambda v: cleaner(str(v)) if pd.notna(v) and isinstance(v, (str, bytes)) else v
    )
    # Compare while safely ignoring NaN == NaN
    changed = int(((converted != original) & (original.notna() | converted.notna())).sum())
    cleaned[column] = converted
    return cleaned, changed


def handle_missing_values(
    df: pd.DataFrame, column: str, method: str, custom_value=None
) -> tuple[pd.DataFrame, int]:
    """Fill or remove missing values in one column.

    Methods:
        remove  - drop rows where this column is missing
        mean / median - numeric columns only
        mode    - most frequent value (works for any column)
        custom  - fill with the user-provided value

    Returns:
        (cleaned_df, number_of_rows_or_cells_affected)
    """
    if not validate_column(df, column):
        return df, 0

    missing_count = int(df[column].isna().sum())
    if missing_count == 0:
        return df, 0

    cleaned = df.copy()

    if method == "remove":
        return cleaned.dropna(subset=[column]), missing_count

    if method == "mean":
        if not pd.api.types.is_numeric_dtype(cleaned[column]):
            return df, 0
        cleaned[column] = cleaned[column].fillna(cleaned[column].mean())
        return cleaned, missing_count

    if method == "median":
        if not pd.api.types.is_numeric_dtype(cleaned[column]):
            return df, 0
        cleaned[column] = cleaned[column].fillna(cleaned[column].median())
        return cleaned, missing_count

    if method == "mode":
        mode = cleaned[column].mode(dropna=True)
        if mode.empty:
            return df, 0
        cleaned[column] = cleaned[column].fillna(mode.iloc[0])
        return cleaned, missing_count

    if method == "custom":
        if custom_value is None or str(custom_value).strip() == "":
            return df, 0
        fill_val = custom_value
        # Preserve numeric dtype if possible
        if pd.api.types.is_numeric_dtype(cleaned[column]):
            try:
                if pd.api.types.is_integer_dtype(cleaned[column]):
                    fill_val = int(fill_val)
                else:
                    fill_val = float(fill_val)
            except (ValueError, TypeError):
                pass
        cleaned[column] = cleaned[column].fillna(fill_val)
        return cleaned, missing_count

    return df, 0


def clean_invalid_dates(
    df: pd.DataFrame, column: str, action: str = "coerce"
) -> tuple[pd.DataFrame, int]:
    """Handle unparseable date values in a column.

    Actions:
        coerce - set invalid date values to missing (NaN)
        remove - drop rows containing unparseable dates

    Returns:
        (cleaned_df, number_of_rows_or_cells_affected)
    """
    if not validate_column(df, column):
        return df, 0

    non_null = df[column].dropna()
    if non_null.empty:
        return df, 0

    parsed = pd.to_datetime(non_null.astype(str), errors="coerce")
    bad_mask = parsed.isna()
    bad_count = int(bad_mask.sum())
    if bad_count == 0:
        return df, 0

    bad_indices = non_null[bad_mask].index
    cleaned = df.copy()

    if action == "remove":
        return cleaned.drop(index=bad_indices), bad_count
    elif action == "coerce":
        cleaned.loc[bad_indices, column] = pd.NA
        return cleaned, bad_count

    return df, 0


def convert_column_type(
    df: pd.DataFrame, column: str, target_type: str
) -> tuple[pd.DataFrame, int]:
    """Convert a column to integer, float, string or date.

    Values that cannot be converted are set to missing instead of crashing.

    Returns:
        (cleaned_df, number_of_cells_successfully_converted)
    """
    if not validate_column(df, column):
        return df, 0

    cleaned = df.copy()
    series = cleaned[column]

    try:
        if target_type == "integer":
            result = pd.to_numeric(series, errors="coerce").round().astype("Int64")
        elif target_type == "float":
            result = pd.to_numeric(series, errors="coerce")
        elif target_type == "string":
            result = series.astype("string")
        elif target_type == "date":
            result = pd.to_datetime(series, errors="coerce")
        else:
            return df, 0
    except (ValueError, TypeError):
        return df, 0

    changed = int(result.notna().sum())
    # If nothing converted, keep the original column instead of wiping it.
    if changed == 0:
        return df, 0

    cleaned[column] = result
    return cleaned, changed


def standardize_categories(
    df: pd.DataFrame, column: str, mapping: dict
) -> tuple[pd.DataFrame, int]:
    """Replace values in a column using an explicit user-provided mapping.

    The application never merges categories silently: every replacement in
    `mapping` was chosen by the user.

    Returns:
        (cleaned_df, number_of_cells_changed)
    """
    if not validate_column(df, column) or not mapping:
        return df, 0

    clean_mapping = {
        str(k).strip(): str(v).strip()
        for k, v in mapping.items()
        if str(k).strip() != str(v).strip() and str(v).strip() != ""
    }
    if not clean_mapping:
        return df, 0

    cleaned = df.copy()
    original = cleaned[column]
    
    # Also support matching both raw and stripped string forms
    lookup = dict(clean_mapping)
    for k, v in clean_mapping.items():
        lookup[k] = v

    # Replace on both raw and stripped
    def _replace_val(val):
        if pd.isna(val):
            return val
        sval = str(val)
        stripped = sval.strip()
        if stripped in lookup:
            return lookup[stripped]
        if sval in lookup:
            return lookup[sval]
        return val

    replaced = original.map(_replace_val)
    # Accurately count only actual value changes, ignoring NaN == NaN
    changed = int(((replaced != original) & (original.notna() | replaced.notna())).sum())
    cleaned[column] = replaced
    return cleaned, changed


def drop_columns(df: pd.DataFrame, columns: list[str]) -> tuple[pd.DataFrame, int]:
    """Drop specified columns from the DataFrame.

    Returns:
        (cleaned_df, number_of_columns_removed)
    """
    valid_cols = [col for col in columns if validate_column(df, col)]
    if not valid_cols:
        return df, 0
    return df.drop(columns=valid_cols), len(valid_cols)


def remove_empty_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Drop columns that contain no data at all.

    Returns:
        (cleaned_df, number_of_columns_removed)
    """
    empty_cols = [col for col in df.columns if df[col].dropna().empty]
    if not empty_cols:
        return df, 0
    return df.drop(columns=empty_cols), len(empty_cols)


def _describe_value(value) -> str:
    """Short human-readable rendering of a value for the cleaning history."""
    text = str(value)
    return text if len(text) <= 30 else text[:27] + "..."


def apply_cleaning_operations(
    df: pd.DataFrame, operations: list[dict]
) -> tuple[pd.DataFrame, list[str]]:
    """Apply a list of user-approved operations in a clean, predictable order.

    Returns:
        (cleaned_df, cleaning_history) where cleaning_history is a list of
        human-readable sentences describing exactly what was done.
    """
    cleaned = df.copy()
    history: list[str] = []

    # Sort/group operations logically:
    # 1. Dropping explicit columns
    # 2. Dropping all empty columns
    # 3. Trimming whitespace
    # 4. Removing duplicates
    # 5. Category standardization (explicit mappings run before blanket case changes)
    # 6. Text case standardization
    # 7. Missing value handling
    # 8. Date validity cleaning
    # 9. Type conversion

    for op in operations:
        op_type = op.get("type", "")

        if op_type == "drop_columns":
            cols = op.get("columns", [])
            cleaned, n = drop_columns(cleaned, cols)
            if n:
                history.append(f"Removed column(s): {', '.join(cols)}.")

        elif op_type == "empty_columns":
            cleaned, n = remove_empty_columns(cleaned)
            if n:
                history.append(f"Removed {n} empty column(s).")

        elif op_type == "trim_whitespace":
            cleaned, n = trim_whitespace(cleaned)
            if n:
                history.append(f"Trimmed whitespace in {n} cell(s).")

        elif op_type == "duplicates":
            cleaned, n = remove_duplicate_rows(cleaned)
            if n:
                history.append(f"Removed {n} duplicate row(s).")

        elif op_type == "categories":
            column = op.get("column", "")
            mapping = op.get("mapping", {})
            cleaned, n = standardize_categories(cleaned, column, mapping)
            if n:
                pairs = ", ".join(
                    f"'{_describe_value(k)}' -> '{_describe_value(v)}'"
                    for k, v in list(mapping.items())[:10]
                )
                history.append(f"Standardized {n} value(s) in '{column}': {pairs}.")

        elif op_type == "case":
            column = op.get("column", "")
            case_type = op.get("case_type", "")
            cleaned, n = standardize_text_case(cleaned, column, case_type)
            if n:
                history.append(
                    f"Standardized case of {n} value(s) in '{column}' to {case_type}."
                )

        elif op_type == "missing":
            column = op.get("column", "")
            method = op.get("method", "")
            cleaned, n = handle_missing_values(cleaned, column, method, op.get("value"))
            labels = {
                "remove": f"Removed {n} row(s) where '{column}' was missing.",
                "mean": f"Filled {n} missing value(s) in '{column}' using the mean.",
                "median": f"Filled {n} missing value(s) in '{column}' using the median.",
                "mode": f"Filled {n} missing value(s) in '{column}' using the most frequent value.",
                "custom": f"Filled {n} missing value(s) in '{column}' using a custom value.",
            }
            if n and method in labels:
                history.append(labels[method])

        elif op_type == "invalid_date":
            column = op.get("column", "")
            action = op.get("action", "coerce")
            cleaned, n = clean_invalid_dates(cleaned, column, action)
            if n:
                if action == "remove":
                    history.append(f"Removed {n} row(s) with invalid dates in '{column}'.")
                else:
                    history.append(f"Cleared {n} invalid date value(s) in '{column}'.")

        elif op_type == "convert":
            column = op.get("column", "")
            target_type = op.get("target_type", "")
            cleaned, n = convert_column_type(cleaned, column, target_type)
            if n:
                history.append(
                    f"Converted {n} value(s) in '{column}' to {target_type}."
                )
            else:
                history.append(
                    f"Could not convert any values in '{column}' to {target_type}."
                )

    if not history:
        history.append("No changes were needed.")
    return cleaned, history
