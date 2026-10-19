"""Coordinate validation, parsing and session replacement for an upload."""

from flask import current_app
from utils.validators import dataset_limit_error
from werkzeug.utils import secure_filename
from workspace import dataset_exporter, file_store
from workspace.session import dataset_session

from . import dataset_importer


def process_upload(file, encoding: str, delimiter: str, sheet: str) -> tuple[bool, str]:
    """Process one upload and return whether it was accepted plus its user message."""
    filename, error = _validate_file(file)
    if error:
        return False, error

    if not _allowed_file(filename):
        return False, "Unsupported file type. Please upload a CSV, XLSX or XLS file."

    filepath = file_store.save_uploaded_file(file, filename)
    if filepath is None:
        return False, "We couldn't save that file. Please try again."

    encoding = encoding or None
    if encoding not in (None, "utf-8", "utf-8-sig", "latin1"):
        encoding = None
    delimiter = delimiter or "auto"
    if delimiter not in ("auto", ",", ";", "\t"):
        delimiter = "auto"
    sheet = (sheet or "").strip() or None

    if filepath.suffix.lower() in (".xls", ".xlsx") and sheet:
        sheets = dataset_importer.list_excel_sheets(filepath)
        if sheets and sheet not in sheets:
            file_store.delete_file(filepath)
            return False, f"Worksheet '{sheet}' not found. This workbook has: {', '.join(sheets)}."

    candidate, reason = dataset_importer.load_dataset_with_error(
        filepath,
        encoding=encoding,
        delimiter=delimiter,
        sheet=sheet,
    )
    if candidate is None:
        file_store.delete_file(filepath)
        return False, f"We couldn't read this file. {reason}"
    if candidate.empty:
        file_store.delete_file(filepath)
        return False, "This file contains no data rows. Please choose another dataset."
    limit_error = dataset_limit_error(candidate)
    if limit_error:
        file_store.delete_file(filepath)
        return False, limit_error

    namespace = file_store.new_workspace_namespace()
    try:
        baseline_path = dataset_exporter.export_dataset(
            candidate,
            filename,
            file_format="pickle",
            namespace=namespace,
        )
    except Exception:
        file_store.delete_file(filepath)
        return False, "We couldn't prepare this dataset. Please try again."

    dataset_session.start_new(
        filepath,
        baseline_path,
        filename,
        output_namespace=namespace,
        encoding=encoding,
        delimiter=delimiter,
        sheet=sheet,
    )
    file_store.delete_file(filepath)
    return True, "Dataset uploaded successfully."


def _validate_file(file) -> tuple[str | None, str]:
    if file is None or file.filename == "":
        return None, "No file selected. Please choose a CSV or Excel file."
    filename = secure_filename(file.filename)
    if filename == "" or "." not in filename:
        return None, "We couldn't read that filename. Please try another file."
    return filename, ""


def _allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in current_app.config[
        "ALLOWED_EXTENSIONS"
    ]
