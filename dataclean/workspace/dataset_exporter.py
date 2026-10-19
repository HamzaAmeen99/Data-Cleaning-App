"""Write datasets to download and lossless workspace formats."""

from pathlib import Path

import pandas as pd

from workspace import file_store


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
            df.to_excel(output, index=False, sheet_name=sheet or "Data")
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
            df.to_csv(output, **csv_options)
    except Exception:
        file_store.delete_file(output)
        raise
    return output
