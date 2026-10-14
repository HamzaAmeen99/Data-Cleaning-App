"""Dataset overview and analysis routes."""

import pandas as pd
from config import Config
from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from services import analysis_service, cleaning_service, file_service
from services.dataset_session import dataset_session
from utils.csrf import check_csrf

dataset_bp = Blueprint("dataset", __name__)


def _load_current_dataset() -> pd.DataFrame | None:
    """Load the current working dataset referenced by the session."""
    path = dataset_session.working_path
    if not path:
        return None
    df = file_service.load_dataset(path)
    return dataset_session.apply_column_types(df) if df is not None else None


@dataset_bp.route("/dataset")
def show_dataset():
    """Display filename, shape, preview and per-column information."""
    df = _load_current_dataset()
    if df is None:
        if dataset_session.working_path:
            csrf_token = session.get("csrf_token")
            dataset_session.end()
            if csrf_token:
                session["csrf_token"] = csrf_token
            flash(
                "We couldn't read this file. Please make sure it is a valid CSV or Excel file.",
                "error",
            )
            return redirect(url_for("upload.upload_page"))
        flash("Please upload a dataset first.", "error")
        return redirect(url_for("upload.upload_page"))
    if df.empty:
        csrf_token = session.get("csrf_token")
        dataset_session.end()
        if csrf_token:
            session["csrf_token"] = csrf_token
        flash("This file contains no data rows. Please upload a dataset with data.", "error")
        return redirect(url_for("upload.upload_page"))

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

    is_cleaned = dataset_session.is_cleaned

    return render_template(
        "dataset.html",
        filename=dataset_session.original_filename,
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
    type_labels = {
        "Number": "numeric",
        "Date": "date",
        "Yes/No": "boolean",
        "Text": "text",
    }
    column_types = {
        col: type_labels[_friendly_type(str(df[col].dtype))]
        for col in df.columns
    }
    inconsistent = analysis["inconsistent_text"]

    is_cleaned = dataset_session.is_cleaned

    # Summary counts for the bento-grid stat cards
    total_missing = sum(missing.values())
    duplicates = analysis["duplicates"]
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
        filename=dataset_session.original_filename,
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
    if not dataset_session.upload_path:
        flash("No uploaded dataset found.", "error")
        return redirect(url_for("upload.index"))

    dataset_session.reset()
    flash("Dataset reset back to the original upload.", "success")
    return redirect(url_for("dataset.show_dataset"))


@dataset_bp.route("/dataset/select-columns", methods=["POST"])
def select_columns():
    """Persist the user's column selection and drop unselected columns from the
    working dataset so the cleaning pipeline only operates on chosen columns.
    """
    check_csrf()

    working_path = dataset_session.working_path
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

    selected_before_rename = set(valid_selected)
    type_targets = {
        "Number": "float",
        "Text": "string",
        "Date": "date",
        "Yes/No": "boolean",
    }
    existing_types = dataset_session.column_type_overrides
    requested_types = {}
    for column in all_cols:
        requested = request.form.get(f"type_{column}", "").strip()
        current = _friendly_type(str(df[column].dtype))
        if requested not in type_targets or column not in selected_before_rename:
            continue
        if requested != current:
            requested_types[column] = type_targets[requested]
        elif column in existing_types:
            requested_types[column] = existing_types[column]

    for column, target_type in requested_types.items():
        converted, converted_count = cleaning_service.convert_column_type(
            df, column, target_type
        )
        if converted_count == 0 and df[column].notna().any():
            flash(
                f"Could not convert '{column}' to the selected data type.",
                "error",
            )
            return redirect(url_for("dataset.show_dataset"))
        df = converted

    # Apply any column renames submitted from the edit column modal
    renames = {}
    for c in all_cols:
        new_name = request.form.get(f"rename_{c}", "").strip()
        if new_name and new_name != c:
            renames[c] = new_name

    if renames:
        df = df.rename(columns=renames)
        valid_selected = [renames.get(c, c) for c in valid_selected]
        all_cols = [str(c) for c in df.columns]

    if not renames and not requested_types and set(valid_selected) == set(all_cols):
        # Nothing to drop or rename — just proceed as normal
        session.pop("selected_columns", None)
        flash("All columns kept. Proceeding to cleaning.", "success")
        return redirect(url_for("dataset.dataset_analysis"))

    # Drop unselected columns and save as new working file
    narrowed_df = df[valid_selected]
    original_name = dataset_session.original_filename
    try:
        output_path = file_service.export_dataset(narrowed_df, original_name, file_format="csv")
    except Exception:
        flash("Could not save the column selection. Please try again.", "error")
        return redirect(url_for("dataset.show_dataset"))

    session["current_path"] = str(output_path)
    session["selected_columns"] = valid_selected
    renamed_types = {
        renames.get(column, column): target_type
        for column, target_type in requested_types.items()
    }
    if renamed_types:
        session["column_type_overrides"] = renamed_types
    else:
        session.pop("column_type_overrides", None)
    dropped = [c for c in all_cols if c not in valid_selected]
    summary = f"Kept {len(valid_selected)} column(s)."
    if dropped:
        summary += f" Dropped: {', '.join(dropped)}."
    if renamed_types:
        summary += f" Updated {len(renamed_types)} data type(s)."
    flash(summary, "success")
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
