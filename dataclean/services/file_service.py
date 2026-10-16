"""File storage and dataset loading/export helpers.

Import fidelity: CSVs are read twice — once with inferred types and once as
raw text — so identifier columns like ``00123`` keep their leading zeros and
integer columns wider than float precision come back exact (Int64).
Intermediate working files use pickle so Int64/boolean/datetime types survive
between cleaning passes without lossy CSV round trips.
"""

from decimal import Decimal
from pathlib import Path
import re
import time
from uuid import uuid4

import pandas as pd
from flask import current_app

from utils.validators import validate_file

_INT_RE = re.compile(r"[+-]?\d+\Z")


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


def list_excel_sheets(filepath: str | Path) -> list[str]:
    """Return worksheet names of an Excel file (empty on any failure)."""
    try:
        with pd.ExcelFile(filepath) as book:
            return [str(name) for name in book.sheet_names]
    except Exception:
        return []


def describe_parse_error(error: Exception) -> str:
    """Turn a parser exception into one actionable sentence for the user."""
    name = type(error).__name__
    text = str(error).strip().splitlines()[0] if str(error).strip() else ""
    if isinstance(error, UnicodeDecodeError):
        return (
            "This file is not valid UTF-8 text. Choose the Latin-1 (Western) "
            "encoding and upload it again."
        )
    if isinstance(error, FileNotFoundError) or "No such file" in text:
        return "The uploaded file is no longer on disk. Please upload it again."
    if "Worksheet" in text or "sheet" in text.lower():
        return f"The worksheet could not be read: {text}"
    if "BadZipFile" in name or "zip" in text.lower() or "excel file format" in text.lower():
        return "This .xlsx file looks damaged. Re-save it as .xlsx or export it as CSV."
    if "ParserError" in name or "delimiter" in text.lower() or "tokenizing" in text.lower():
        return (
            f"The rows do not line up as CSV: {text}. Check the delimiter "
            "(comma, semicolon, tab) and the encoding, then try again."
        )
    if not text:
        return f"We could not read this file ({name})."
    return f"We could not read this file ({name}: {text})."


def load_dataset_with_error(
    filepath: str | Path,
    encoding: str | None = None,
    delimiter: str | None = None,
    sheet: str | None = None,
) -> tuple[pd.DataFrame | None, str]:
    """Like load_dataset but returns (df, "") or (None, actionable reason)."""
    try:
        return _read_dataset(filepath, encoding=encoding, delimiter=delimiter, sheet=sheet), ""
    except Exception as error:
        return None, describe_parse_error(error)


def load_dataset(
    filepath: str | Path,
    encoding: str | None = None,
    delimiter: str | None = None,
    sheet: str | None = None,
) -> pd.DataFrame | None:
    """Read a CSV/XLS/XLSX/PKL file into a DataFrame.

    encoding/delimiter apply to CSVs ("auto" or omitted delimiters are sniffed).
    sheet selects an Excel worksheet by name (blank = first sheet).

    Returns None (instead of raising) when the file cannot be parsed,
    so the route layer can show a friendly error. Use load_dataset_with_error
    when the reason matters.
    """
    try:
        return _read_dataset(filepath, encoding=encoding, delimiter=delimiter, sheet=sheet)
    except Exception:
        return None


def _read_dataset(
    filepath: str | Path,
    encoding: str | None = None,
    delimiter: str | None = None,
    sheet: str | None = None,
) -> pd.DataFrame:
    """Read a dataset, raising on failure so the caller can explain why."""
    filepath = Path(filepath)
    if not filepath.exists() or not filepath.is_file():
        raise FileNotFoundError(str(filepath))

    suffix = filepath.suffix.lower()
    if suffix == ".pkl":
        return pd.read_pickle(filepath)
    if suffix == ".csv":
        kwargs: dict = {}
        if encoding:
            kwargs["encoding"] = encoding
        if delimiter and delimiter != "auto":
            kwargs["delimiter"] = delimiter
        else:
            kwargs["sep"] = None
            kwargs["engine"] = "python"
        try:
            df = pd.read_csv(filepath, **kwargs)
        except UnicodeDecodeError:
            # Only retry latin1 when the user did not explicitly pick an
            # encoding; then strictness was not requested.
            if encoding:
                raise
            fallback = {k: v for k, v in kwargs.items() if k != "encoding"}
            df = pd.read_csv(filepath, encoding="latin1", **fallback)
            kwargs["encoding"] = "latin1"
        # Second, type-free read of the same file to see the raw text.
        raw_kwargs = dict(kwargs)
        raw_kwargs.update(dtype=str, keep_default_na=False)
        df_raw = pd.read_csv(filepath, **raw_kwargs)
        _repair_typed_columns(df, df_raw)
        return df
    if suffix in (".xls", ".xlsx"):
        if sheet:
            return pd.read_excel(filepath, sheet_name=sheet)
        return pd.read_excel(filepath)
    raise ValueError(f"Unsupported file type '{suffix}'.")


def _repair_typed_columns(df: pd.DataFrame, df_raw: pd.DataFrame) -> None:
    """Fix the two ways type inference destroys values: leading-zero IDs and
    oversized integers (a float64 column silently rounds past 2**53).

    Identifier columns become text; big integer columns become exact Int64
    where values fit, otherwise text. Everything else is left to inference.
    """
    for col in df.columns:
        if col not in df_raw.columns:
            continue
        raw = df_raw[col].astype("string").str.strip()
        # Preserve raw digits while keeping values pandas already recognized as
        # missing (for example N/A) out of the identifier-shape decision.
        raw = raw.mask((raw == "") | df[col].isna())
        values = raw.dropna()
        if values.empty or not bool(values.str.fullmatch(_INT_RE).all()):
            continue

        # Column is integer-shaped. Leading zeros mean an identifier (00123).
        if bool(values.str.fullmatch(r"[+-]?0\d+").any()):
            df[col] = raw
            continue

        numbers = [Decimal(value) for value in values]
        if max(abs(number) for number in numbers) <= 2**53:
            continue  # float64 representation is exact here; inference was fine
        if max(abs(number) for number in numbers) <= 2**63 - 1:
            from services.cleaning_service import _parse_integer

            df[col] = pd.array(
                [_parse_integer(value) if pd.notna(value) else pd.NA for value in raw],
                dtype="Int64",
            )
        else:
            df[col] = raw  # wider than int64: keep exact text instead


def sweep_stale_files(max_age_seconds: float, roots=None) -> int:
    """Delete files untouched for longer than max_age_seconds, then empty folders.

    Runs when a new upload starts so abandoned sessions do not pile up.
    Returns the number of files removed.
    """
    folders = roots or (
        current_app.config["UPLOAD_FOLDER"], current_app.config["PROCESSED_FOLDER"]
    )
    cutoff = time.time() - max(0.0, float(max_age_seconds))
    removed = 0
    for folder in folders:
        folder = Path(folder)
        if not folder.is_dir():
            continue
        # Deepest paths first so empty per-session folders can be removed.
        for path in sorted(folder.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if any(part.startswith(".") for part in path.relative_to(folder).parts):
                continue  # never sweep placeholders such as .gitkeep
            try:
                if path.is_file():
                    if path.stat().st_mtime < cutoff:
                        path.unlink(missing_ok=True)
                        removed += 1
                elif not any(path.iterdir()):
                    path.rmdir()
            except OSError:
                continue
    return removed


def export_dataset(
    df: pd.DataFrame,
    base_name: str | Path,
    file_format: str = "csv",
    encoding: str | None = None,
    delimiter: str | None = None,
    sheet: str | None = None,
    namespace: str | None = None,
) -> Path:
    """Save a cleaned DataFrame.

    Args:
        df: The cleaned DataFrame.
        base_name: Base filename for the output file (extension is adjusted).
        file_format: "csv", "excel" or "pickle" (lossless internal format).
        encoding: CSV text encoding (default utf-8).
        delimiter: CSV field delimiter (default comma).
        sheet: Excel worksheet name to write (default first/only sheet).
        namespace: optional sub-folder that isolates this session's outputs.

    Returns:
        The path of the written file.
    """
    processed_dir = Path(current_app.config["PROCESSED_FOLDER"])
    if namespace:
        # A per-session folder keeps one session's outputs out of another's way.
        processed_dir = processed_dir / str(namespace)
    processed_dir.mkdir(parents=True, exist_ok=True)

    base_name = Path(base_name)
    unique_stem = f"{base_name.stem}_{uuid4().hex}"
    if file_format == "excel":
        out = processed_dir / f"{unique_stem}.xlsx"
        df.to_excel(out, index=False, sheet_name=sheet or "Data")
    elif file_format == "pickle":
        out = processed_dir / f"{unique_stem}.pkl"
        df.to_pickle(out)
    else:
        out = processed_dir / f"{unique_stem}.csv"
        csv_kwargs: dict = {"index": False}
        if encoding:
            csv_kwargs["encoding"] = encoding
        if delimiter and delimiter != "auto":
            csv_kwargs["sep"] = delimiter
        df.to_csv(out, **csv_kwargs)
    return out
