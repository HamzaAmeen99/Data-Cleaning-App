"""Small, shared validation helpers used by the route layer."""

from typing import Any

import pandas as pd
from flask import flash
from werkzeug.utils import secure_filename


def validate_file(file) -> str | None:
    """Validate an uploaded file object.

    Returns:
        A safe filename when the file is usable, otherwise None
        (and a friendly flash message is queued).
    """
    if file is None or file.filename == "":
        flash("No file selected. Please choose a CSV or Excel file.", "error")
        return None

    filename = secure_filename(file.filename)
    if filename == "" or "." not in filename:
        flash("We couldn't read that filename. Please try another file.", "error")
        return None

    return filename


def validate_column(df: pd.DataFrame, column: Any) -> bool:
    """Return True only if `column` exists in the DataFrame."""
    return column in df.columns
