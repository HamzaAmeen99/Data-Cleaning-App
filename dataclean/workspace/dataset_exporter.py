"""Write datasets to download and lossless workspace formats."""

from pathlib import Path

import pandas as pd
from pandas.api.types import is_object_dtype, is_string_dtype

from workspace import file_store


_FORMULA_PREFIXES = ("=", "+", "-", "@")
_FORMULA_CONTROL_PREFIXES = ("\t", "\r", "\n")


def _safe_spreadsheet_text(value):
    """Force formula-like text to remain text in spreadsheet applications."""
    if not isinstance(value, str) or not value or value.startswith("'"):
        return value
    if value.startswith(_FORMULA_CONTROL_PREFIXES) or value.lstrip().startswith(_FORMULA_PREFIXES):
        return f"'{value}"
    return value


def _spreadsheet_safe_copy(df: pd.DataFrame) -> pd.DataFrame:
    """Return an export-only copy with formulas neutralized in text fields."""
    safe_df = df.copy(deep=True)
    safe_df.columns = [_safe_spreadsheet_text(column) for column in safe_df.columns]
    for position in range(safe_df.shape[1]):
        series = safe_df.iloc[:, position]
        if (
            is_object_dtype(series.dtype)
            or is_string_dtype(series.dtype)
            or isinstance(series.dtype, pd.CategoricalDtype)
        ):
            safe_df.isetitem(position, series.map(_safe_spreadsheet_text))
    return safe_df


def export_dataset(
    df: pd.DataFrame,
    base_name: str | Path,
    file_format: str = "csv",
    encoding: str | None = None,
    delimiter: str | None = None,
    sheet: str | None = None,
    namespace: str | None = None,
) -> Path:
    """Write a dataset as CSV, Excel, or a lossless internal snapshot."""
    output = None
    try:
        if file_format == "excel":
            output = file_store.new_output_path(base_name, "xlsx", namespace)
            _spreadsheet_safe_copy(df).to_excel(output, index=False, sheet_name=sheet or "Data")
        elif file_format == "pickle":
            output = file_store.new_output_path(base_name, "pkl", namespace)
            df.to_pickle(output)
        else:
            output = file_store.new_output_path(base_name, "csv", namespace)
            csv_options: dict = {"index": False}
            if encoding:
                csv_options["encoding"] = encoding
            if delimiter and delimiter != "auto":
                csv_options["sep"] = delimiter
            _spreadsheet_safe_copy(df).to_csv(output, **csv_options)
    except Exception:
        file_store.delete_file(output)
        raise
    return output
