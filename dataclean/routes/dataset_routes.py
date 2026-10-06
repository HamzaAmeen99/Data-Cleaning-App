"""Dataset overview and analysis routes."""

import pandas as pd
from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from utils.csrf import check_csrf
from config import Config
from services import analysis_service, file_service

dataset_bp = Blueprint("dataset", __name__)


def _load_current_dataset() -> pd.DataFrame | None:
    """Load the current working dataset referenced by the session."""
    path = session.get("current_path") or session.get("upload_path")
    if not path:
        return None
    return file_service.load_dataset(path)


@dataset_bp.route("/dataset")
def show_dataset():
    """Display filename, shape, preview and per-column information."""
    df = _load_current_dataset()
    if df is None:
        flash("Please upload a dataset first.", "error")
        return redirect(url_for("upload.index"))

    overview = analysis_service.get_dataset_overview(df)
    missing = analysis_service.get_missing_values(df)
    duplicates = analysis_service.get_duplicate_count(df)
    total_missing = sum(missing.values())

    preview_rows = df.head(Config.PREVIEW_ROWS)
    preview_columns = [str(col) for col in df.columns]
    preview_values = [
        [None if pd.isna(value) else str(value) for value in row]
        for row in preview_rows.itertuples(index=False, name=None)
    ]

    # Per-column info table: name, friendly type, missing count, selection state.
    selected_cols = session.get("selected_columns")
    column_info = []
    for col, dtype in overview["data_types"].items():
        is_selected = True if not selected_cols else (col in selected_cols)
        column_info.append({
            "name": col,
            "type": _friendly_type(dtype),
            "missing": missing.get(col, 0),
            "selected": is_selected,
        })

    is_cleaned = bool(
        session.get("processed_path")
        or (session.get("current_path") and session.get("current_path") != session.get("upload_path"))
    )

    return render_template(
        "dataset.html",
        filename=session.get("original_filename", "dataset"),
        overview=overview,
        total_missing=total_missing,
        duplicates=duplicates,
        preview_columns=preview_columns,
        preview_values=preview_values,
        preview_shown=len(preview_values),
        total_rows=overview["rows"],
        column_info=column_info,
        is_cleaned=is_cleaned,
    )


@dataset_bp.route("/dataset/analysis")
def dataset_analysis():
    """Display detected data-quality problems and cleaning suggestions."""
    df = _load_current_dataset()
    if df is None:
        flash("Please upload a dataset first.", "error")
        return redirect(url_for("upload.index"))

    analysis = analysis_service.analyze_dataset(df)

    # Details needed by the operation controls on the cleaning page.
    missing = analysis["missing_values"]
    column_types = {
        col: ("numeric" if pd.api.types.is_numeric_dtype(df[col]) else "text")
        for col in df.columns
    }
    inconsistent = analysis["inconsistent_text"]

    is_cleaned = bool(
        session.get("processed_path")
        or (session.get("current_path") and session.get("current_path") != session.get("upload_path"))
    )

    # Summary counts for the bento-grid stat cards
    total_missing = sum(missing.values())
    duplicates = analysis_service.get_duplicate_count(df)
    text_issues = sum(
        (f.get("whitespace_cells") or 0) + len(f.get("case_variants") or [])
        for f in inconsistent.values()
    )
    structural_issues = sum(
        1 for s in analysis["suggestions"] if s.get("id", "").startswith("issue_")
    )

    # Dataset health score: percentage of cells that are not missing
    total_cells = df.shape[0] * df.shape[1] if df.shape[1] > 0 else 1
    health_score = round(max(0, (total_cells - total_missing) / total_cells * 100), 1)

    return render_template(
        "cleaning.html",
        filename=session.get("original_filename", "dataset"),
        overview=analysis["overview"],
        suggestions=analysis["suggestions"],
        missing=missing,
        column_types=column_types,
        inconsistent=inconsistent,
        columns=[str(col) for col in df.columns],
        is_cleaned=is_cleaned,
        total_missing=total_missing,
        duplicates=duplicates,
        text_issues=text_issues,
        structural_issues=structural_issues,
        health_score=health_score,
    )


@dataset_bp.route("/dataset/reset", methods=["POST", "GET"])
def reset_dataset():
    """Reset the working dataset back to the original uploaded file."""
    if request.method == "POST":
        check_csrf()
    upload_path = session.get("upload_path")
    if not upload_path:
        flash("No uploaded dataset found.", "error")
        return redirect(url_for("upload.index"))

    session["current_path"] = upload_path
    session.pop("processed_path", None)
    session["cleaning_history"] = []
    session.pop("selected_columns", None)
    flash("Dataset reset back to the original upload.", "success")
    return redirect(url_for("dataset.show_dataset"))


@dataset_bp.route("/dataset/select-columns", methods=["POST"])
def select_columns():
    """Persist the user's column selection and drop unselected columns from the
    working dataset so the cleaning pipeline only operates on chosen columns.
    """
    check_csrf()

    working_path = session.get("current_path") or session.get("upload_path")
    if not working_path:
        flash("Please upload a dataset first.", "error")
        return redirect(url_for("upload.index"))

    df = file_service.load_dataset(working_path)
    if df is None:
        flash("The uploaded dataset is no longer available. Please upload it again.", "error")
        return redirect(url_for("upload.index"))

    selected = request.form.getlist("selected_columns")
    all_cols = [str(c) for c in df.columns]

    # Validate — keep only columns that actually exist in the DataFrame
    valid_selected = [c for c in selected if c in all_cols]

    if not valid_selected:
        flash("Please select at least one column to continue.", "error")
        return redirect(url_for("dataset.show_dataset"))

    if set(valid_selected) == set(all_cols):
        # Nothing to drop — just proceed as normal
        session.pop("selected_columns", None)
        flash("All columns kept. Proceeding to cleaning.", "success")
        return redirect(url_for("dataset.dataset_analysis"))

    # Drop unselected columns and save as new working file
    narrowed_df = df[valid_selected]
    original_name = session.get("original_filename", "dataset")
    try:
        output_path = file_service.export_dataset(narrowed_df, original_name, file_format="csv")
    except Exception:
        flash("Could not save the column selection. Please try again.", "error")
        return redirect(url_for("dataset.show_dataset"))

    session["current_path"] = str(output_path)
    session["selected_columns"] = valid_selected
    dropped = [c for c in all_cols if c not in valid_selected]
    flash(
        f"Kept {len(valid_selected)} column(s). Dropped: {', '.join(dropped)}.",
        "success",
    )
    return redirect(url_for("dataset.dataset_analysis"))


def _friendly_type(dtype: str) -> str:
    """Map pandas dtypes to simple, non-programmer-friendly labels."""
    if dtype.startswith(("int", "float", "Int", "Float")):
        return "Number"
    if dtype.startswith("datetime"):
        return "Date"
    if dtype.startswith("bool"):
        return "Yes/No"
    return "Text"
