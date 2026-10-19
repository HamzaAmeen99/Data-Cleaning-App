"""Small, shared validation helpers used by the route layer."""

from typing import Any

import pandas as pd
from config import Config


def validate_column(df: pd.DataFrame, column: Any) -> bool:
    """Return True only if `column` exists in the DataFrame."""
    return column in df.columns


def dataset_limit_error(df: pd.DataFrame) -> str:
    """Message when a dataset exceeds a configured size limit, else "".

    The numbers are included so the user knows how far over the file is.
    """
    rows, columns = int(len(df)), int(df.shape[1])
    for value, limit, label in (
        (rows, Config.MAX_ROWS, "rows"),
        (columns, Config.MAX_COLUMNS, "columns"),
        (rows * columns, Config.MAX_CELLS, "cells"),
    ):
        if value > limit:
            return (
                f"This dataset has {value:,} {label}, over the limit of {limit:,}. "
                "Please split or clean the file elsewhere and upload a smaller dataset."
            )
    return ""
