"""Cleaning application, results and download routes."""

from pathlib import Path

import pandas as pd
from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from config import Config
from services import cleaning_service, file_service
from utils.validators import validate_column

cleaning_bp = Blueprint("cleaning", __name__)


@cleaning_bp.route("/clean", methods=["POST"])
def clean_dataset():
    """Parse the user's selected operations and apply them via the service."""
    working_path = session.get("current_path") or session.get("upload_path")
    if not working_path:
        flash("Please upload a dataset first.", "error")
        return redirect(url_for("upload.index"))

    df = file_service.load_dataset(working_path)
    if df is None:
        flash("The uploaded dataset is no longer available. Please upload it again.", "error")
        return redirect(url_for("upload.index"))

    operations = _parse_operations(df)
    if not operations:
        flash("No cleaning operations were selected. Choose at least one option.", "error")
        return redirect(url_for("dataset.dataset_analysis"))

    before = _snapshot_stats(df)
    cleaned_df, step_history = cleaning_service.apply_cleaning_operations(df, operations)
    after = _snapshot_stats(cleaned_df)

    original_name = session.get("original_filename", "dataset")
    try:
        output_path = file_service.export_dataset(cleaned_df, original_name, file_format="csv")
    except Exception:
        flash("We couldn't save the cleaned dataset. Please try again.", "error")
        return redirect(url_for("dataset.dataset_analysis"))

    # Track accumulated history across multiple cleaning passes
    existing_history = session.get("cleaning_history", [])
    if step_history != ["No changes were needed."]:
        new_history = existing_history + step_history
    else:
        new_history = existing_history if existing_history else step_history

    session["processed_path"] = str(output_path)
    session["current_path"] = str(output_path)
    session["cleaning_history"] = new_history
    session["step_history"] = step_history
    session["before_stats"] = session.get("initial_stats") or before
    if "initial_stats" not in session:
        session["initial_stats"] = before
    session["after_stats"] = after
    session["cleaned_columns"] = [str(col) for col in cleaned_df.columns]
    return redirect(url_for("cleaning.cleaning_result"))


@cleaning_bp.route("/cleaning/result")
def cleaning_result():
    """Show before/after summary, the cleaning history and a data preview."""
    processed_path = session.get("processed_path")
    if not processed_path:
        flash("No cleaning result found. Please upload and clean a dataset first.", "error")
        return redirect(url_for("upload.index"))

    df = file_service.load_dataset(processed_path)
    if df is None:
        flash("The cleaned dataset could not be opened. Please clean the dataset again.", "error")
        return redirect(url_for("upload.index"))

    preview_columns = [str(col) for col in df.columns]
    preview_values = [
        [None if pd.isna(value) else str(value) for value in row]
        for row in df.head(Config.PREVIEW_ROWS).itertuples(index=False, name=None)
    ]

    return render_template(
        "results.html",
        filename=session.get("original_filename", "dataset"),
        before=session.get("before_stats", {}),
        after=session.get("after_stats", {}),
        history=session.get("cleaning_history", []),
        step_history=session.get("step_history", []),
        preview_columns=preview_columns,
        preview_values=preview_values,
        preview_shown=len(preview_values),
        total_rows=len(df),
    )


@cleaning_bp.route("/download")
def download_dataset():
    """Download the processed dataset as CSV or Excel."""
    processed_path = session.get("processed_path")
    if not processed_path:
        abort(404)

    processed = Path(processed_path)
    if not processed.exists():
        abort(404)

    file_format = request.args.get("format", "csv")
    if file_format not in ("csv", "excel"):
        abort(404)

    base_name = session.get("original_filename", "dataset")
    stem = base_name.rsplit(".", 1)[0]

    if file_format == "csv":
        return send_file(processed, as_attachment=True, download_name=f"{stem}_cleaned.csv")

    df = file_service.load_dataset(processed)
    if df is None:
        abort(404)
    try:
        excel_path = file_service.export_dataset(df, stem, file_format="excel")
    except Exception:
        abort(404)
    return send_file(excel_path, as_attachment=True, download_name=f"{stem}_cleaned.xlsx")


def _parse_operations(df: pd.DataFrame) -> list[dict]:
    """Translate form fields into the operation list the service expects."""
    operations: list[dict] = []
    form = request.form

    # Explicit column drops from issue cards
    dropped_cols = []
    for col in form.getlist("drop_columns"):
        if col and validate_column(df, col):
            dropped_cols.append(col)

    for key, col in form.items():
        if key.startswith("dropcol_") and col and validate_column(df, col):
            if form.get(f"action_{key}") == "drop" and col not in dropped_cols:
                dropped_cols.append(col)

    if dropped_cols:
        operations.append({"type": "drop_columns", "columns": dropped_cols})

    # Global empty columns removal
    if form.get("op_empty"):
        operations.append({"type": "empty_columns"})

    # Whitespace and duplicate removal
    if form.get("op_trim"):
        operations.append({"type": "trim_whitespace"})
    if form.get("op_duplicates"):
        operations.append({"type": "duplicates"})

    # Date cleaning from date issue cards
    for key, column in form.items():
        if not key.startswith("datecol_"):
            continue
        index = key.removeprefix("datecol_")
        action = form.get(f"dateaction_{index}", "")
        if action in ("coerce", "remove") and validate_column(df, column):
            operations.append({"type": "invalid_date", "column": column, "action": action})

    # Missing-value handling, one entry per configured column.
    for key, column in form.items():
        if not key.startswith("misscol_"):
            continue
        index = key.removeprefix("misscol_")
        method = form.get(f"missing_{index}", "")
        if method and validate_column(df, column):
            operations.append({
                "type": "missing",
                "column": column,
                "method": method,
                "value": form.get(f"missingval_{index}") or None,
            })

    # Case standardization and category mapping per text column.
    for key, column in form.items():
        if not key.startswith("textcol_"):
            continue
        index = key.removeprefix("textcol_")
        if not validate_column(df, column):
            continue

        mapping = {}
        for mkey, canonical in form.items():
            if mkey.startswith(f"map_{index}_") and canonical and canonical.strip():
                variant_key = f"mapvar_{mkey.removeprefix('map_')}"
                variant = form.get(variant_key)
                if variant and variant.strip() != canonical.strip():
                    mapping[variant.strip()] = canonical.strip()
        if mapping:
            operations.append({"type": "categories", "column": column, "mapping": mapping})

        case_type = form.get(f"case_{index}", "")
        if case_type in ("lower", "upper", "title"):
            operations.append({"type": "case", "column": column, "case_type": case_type})

    # Column type conversions (supports multiple rows)
    conv_cols = form.getlist("convert_column")
    conv_types = form.getlist("convert_type")
    for ccol, ctype in zip(conv_cols, conv_types):
        if ccol and ctype in ("integer", "float", "string", "date") and validate_column(df, ccol):
            operations.append({
                "type": "convert",
                "column": ccol,
                "target_type": ctype,
            })

    return operations


def _snapshot_stats(df: pd.DataFrame) -> dict:
    """Capture the small set of numbers shown in the before/after summary."""
    return {
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "missing": int(df.isna().sum().sum()),
    }
