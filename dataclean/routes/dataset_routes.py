"""Dataset overview and analysis routes."""

import pandas as pd
from flask import Blueprint, flash, redirect, render_template, session, url_for

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

    # Per-column info table: name, friendly type, missing count.
    column_info = []
    for col, dtype in overview["data_types"].items():
        column_info.append({
            "name": col,
            "type": _friendly_type(dtype),
            "missing": missing.get(col, 0),
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
    upload_path = session.get("upload_path")
    if not upload_path:
        flash("No uploaded dataset found.", "error")
        return redirect(url_for("upload.index"))

    session["current_path"] = upload_path
    session.pop("processed_path", None)
    session["cleaning_history"] = []
    flash("Dataset reset back to the original upload.", "success")
    return redirect(url_for("dataset.show_dataset"))


def _friendly_type(dtype: str) -> str:
    """Map pandas dtypes to simple, non-programmer-friendly labels."""
    if dtype.startswith(("int", "float", "Int", "Float")):
        return "Number"
    if dtype.startswith("datetime"):
        return "Date"
    if dtype.startswith("bool"):
        return "Yes/No"
    return "Text"
