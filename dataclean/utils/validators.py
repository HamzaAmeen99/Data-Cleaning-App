"""Small, shared validation helpers used by the route layer."""

from typing import Any

import pandas as pd
from config import Config


class DatasetLimitExceeded(ValueError):
    """Raised while reading a file when its shape already exceeds a limit."""


def dataset_limit_error_from_shape(rows: int, columns: int) -> str:
    """Return the configured-limit message without requiring a DataFrame."""
    for value, limit, label in (
        (int(rows), Config.MAX_ROWS, "rows"),
        (int(columns), Config.MAX_COLUMNS, "columns"),
        (int(rows) * int(columns), Config.MAX_CELLS, "cells"),
    ):
        if value > limit:
            return (
                f"This dataset has {value:,} {label}, over the limit of {limit:,}. "
                "Please split or clean the file elsewhere and upload a smaller dataset."
            )
    return ""


def validate_column(df: pd.DataFrame, column: Any) -> bool:
    """Return True only if `column` exists in the DataFrame."""
    return column in df.columns


def dataset_limit_error(df: pd.DataFrame) -> str:
    """Message when a dataset exceeds a configured size limit, else "".

    The numbers are included so the user knows how far over the file is.
    """
    return dataset_limit_error_from_shape(len(df), df.shape[1])
