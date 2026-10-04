"""File storage and dataset loading/export helpers."""

from pathlib import Path

import pandas as pd
from flask import current_app

from utils.validators import validate_file


def allowed_file(filename: str) -> bool:
    """Check whether the file extension is one of the supported formats."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in current_app.config[
        "ALLOWED_EXTENSIONS"
    ]


def save_uploaded_file(file) -> Path | None:
    """Validate and save an uploaded file into the uploads directory.

    Returns:
        The saved file path, or None when the file could not be saved.
    """
    filename = validate_file(file)
    if filename is None:
        return None

    upload_dir = Path(current_app.config["UPLOAD_FOLDER"])
    upload_dir.mkdir(parents=True, exist_ok=True)

    target = upload_dir / filename
    counter = 1
    while target.exists():
        stem, suffix = filename.rsplit(".", 1)
        target = upload_dir / f"{stem}_{counter}.{suffix}"
        counter += 1

    file.save(target)
    return target


def delete_file(filepath: str | Path | None) -> None:
    """Safely remove a file from disk if it exists."""
    if filepath:
        try:
            p = Path(filepath)
            if p.is_file():
                p.unlink(missing_ok=True)
        except OSError:
            pass


def load_dataset(filepath: str | Path) -> pd.DataFrame | None:
    """Read a CSV/XLS/XLSX file into a DataFrame.

    Returns None (instead of raising) when the file cannot be parsed,
    so the route layer can show a friendly error.
    """
    filepath = Path(filepath)
    if not filepath.exists() or not filepath.is_file():
        return None

    suffix = filepath.suffix.lower()
    try:
        if suffix == ".csv":
            try:
                return pd.read_csv(filepath)
            except UnicodeDecodeError:
                return pd.read_csv(filepath, encoding="latin1")
        if suffix in (".xls", ".xlsx"):
            return pd.read_excel(filepath)
    except Exception:
        return None
    return None


def export_dataset(df: pd.DataFrame, filepath: str | Path, file_format: str = "csv") -> Path:
    """Save a cleaned DataFrame to the processed directory.

    Args:
        df: The cleaned DataFrame.
        filepath: Base path for the output file (extension is adjusted).
        file_format: "csv" or "excel".

    Returns:
        The path of the written file.
    """
    processed_dir = Path(current_app.config["PROCESSED_FOLDER"])
    processed_dir.mkdir(parents=True, exist_ok=True)

    filepath = Path(filepath)
    if file_format == "excel":
        out = processed_dir / f"{filepath.stem}.xlsx"
        df.to_excel(out, index=False)
    else:
        out = processed_dir / f"{filepath.stem}.csv"
        df.to_csv(out, index=False)
    return out
