"""Dataset overview and analysis routes."""

import pandas as pd
from config import Config
from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from features.cleaning import operations as cleaning_operations
from features.upload import dataset_importer
from workspace import dataset_exporter
from workspace.session import dataset_session

from features.dataset import analysis as analysis_tools

dataset_bp = Blueprint("dataset", __name__)


def _load_current_dataset() -> pd.DataFrame | None:
    """Load the current working dataset referenced by the session."""
    path = dataset_session.working_path
    if not path:
        return None
    df = dataset_importer.load_dataset(path, **dataset_session.working_load_params)
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
        flash(
            "Your workspace is unavailable or has expired. Upload a dataset to begin again.",
            "error",
        )
        return redirect(url_for("upload.upload_page"))
    if df.empty:
        csrf_token = session.get("csrf_token")
        dataset_session.end()
        if csrf_token:
            session["csrf_token"] = csrf_token
        flash("This file contains no data rows. Please upload a dataset with data.", "error")
        return redirect(url_for("upload.upload_page"))

    overview = analysis_tools.get_dataset_overview(df)
    missing = analysis_tools.get_missing_values(df)
    duplicates = analysis_tools.get_duplicate_count(df)
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
    override_labels = {
        "integer": "Number", "float": "Number", "string": "Text",
        "date": "Date", "boolean": "Yes/No",
    }
    for col, dtype in overview["data_types"].items():
        is_selected = True if not selected_cols else (col in selected_cols)
        column_info.append({
            "name": col,
            "type": override_labels.get(
                dataset_session.column_type_overrides.get(col), _friendly_type(dtype)
            ),
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
        flash(
            "Your workspace is unavailable or has expired. Upload a dataset to begin again.",
            "error",
        )
        return redirect(url_for("upload.upload_page"))

    analysis = analysis_tools.analyze_dataset(
        df, session.get("pending_category_columns", [])
    )

    # Details needed by the operation controls on the cleaning page.
    missing = analysis["missing_values"]
    type_labels = {
        "Number": "numeric",
        "Date": "date",
        "Yes/No": "boolean",
        "Text": "text",
    }
    override_labels = {
        "integer": "Number", "float": "Number", "string": "Text",
        "date": "Date", "boolean": "Yes/No",
    }
    column_types = {
        col: type_labels[
            override_labels.get(
                dataset_session.column_type_overrides.get(str(col)),
                _friendly_type(str(df[col].dtype)),
            )
        ]
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
        preview_result=(session.get("preview") or {}).get("result"),
        preview_token=(session.get("preview") or {}).get("token", ""),
        recipe_result=(session.get("recipe_preview") or {}).get("result"),
        recipe_token=(session.get("recipe_preview") or {}).get("token", ""),
        overview=analysis["overview"],
        suggestions=analysis["suggestions"],
        missing=missing,
        missing_tokens=analysis.get("missing_tokens", {}),
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


@dataset_bp.route("/dataset/preview", methods=["POST"])
def preview_cleaning():
    """Run the selected plan on a copy and show what it would change.

    Stores the preview bound to the dataset version and the operation
    parameters; /clean rejects applications bound to a stale preview.
    """
    working_path = dataset_session.working_path
    if not working_path:
        flash("Please upload a dataset first.", "error")
        return redirect(url_for("upload.index"))

    df = dataset_importer.load_dataset(
        working_path, **dataset_session.working_load_params
    )
    if df is None:
        flash("The uploaded dataset is no longer available. Please upload it again.", "error")
        return redirect(url_for("upload.index"))
    df = dataset_session.apply_column_types(df)

    from features.cleaning.routes import _parse_operations

    operations = _parse_operations(df)
    if not operations:
        flash("No cleaning operations were selected. Choose at least one option.", "error")
        return redirect(url_for("dataset.dataset_analysis"))

    plan_errors = cleaning_operations.validate_plan(df, operations)
    if plan_errors:
        session["preview"] = {"token": "", "dataset": None, "result": {"errors": plan_errors}}
        return redirect(url_for("dataset.dataset_analysis"))

    result = cleaning_operations.preview_plan(df, operations)
    token = cleaning_operations.operation_plan_token(operations)
    session["preview"] = {
        "token": token,
        "dataset": dataset_session.dataset_fingerprint(),
        "operations": operations,
        "result": result,
    }
    flash("Preview updated below. Nothing was applied.", "success")
    return redirect(url_for("dataset.dataset_analysis"))


@dataset_bp.route("/dataset/undo", methods=["POST"])
def undo_dataset():
    """Undo the latest committed cleaning/narrowing run."""
    popped = dataset_session.undo_latest()
    if popped is None:
        flash("No cleaning runs to undo.", "error")
        return redirect(url_for("dataset.dataset_analysis"))
    flash(
        f"Undone: reversed the {popped.get('event', 'run')} run from "
        f"{popped.get('ts', '')}. The dataset is back to the version before it.",
        "success",
    )
    return redirect(url_for("dataset.show_dataset"))


@dataset_bp.route("/dataset/reset", methods=["POST"])
def reset_dataset():
    """Reset the working dataset back to the original uploaded file."""
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
    working_path = dataset_session.working_path
    if not working_path:
        flash("Please upload a dataset first.", "error")
        return redirect(url_for("upload.index"))

    df = dataset_importer.load_dataset(
        working_path, **dataset_session.working_load_params
    )
    if df is None:
        flash("The uploaded dataset is no longer available. Please upload it again.", "error")
        return redirect(url_for("upload.index"))

    df = dataset_session.apply_column_types(df)
    selected = request.form.getlist("selected_columns")
    all_cols = [str(c) for c in df.columns]

    # Validate â€” keep only columns that actually exist in the DataFrame
    valid_selected = [c for c in selected if c in all_cols]

    if not valid_selected:
        flash("Please select at least one column to continue.", "error")
        return redirect(url_for("dataset.show_dataset"))

    selected_recipe = list(valid_selected)
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
        converted, report = cleaning_operations.convert_column_type(
            df, column, target_type
        )
        if not report.applied:
            flash(
                report.describe(column, target_type),
                "error",
            )
            return redirect(url_for("dataset.show_dataset"))
        df = converted

    # Apply any column renames submitted from the edit column modal.
    # Reject empty names and names that duplicate an existing column.
    renames = {}
    target_counts: dict[str, int] = {}
    for c in all_cols:
        raw = request.form.get(f"rename_{c}", "")
        new_name = raw.strip()
        if raw and not new_name:
            flash("Renamed column names cannot be empty. Column not renamed.", "error")
            return redirect(url_for("dataset.show_dataset"))
        if new_name and new_name != c:
            renames[c] = new_name
            target_counts[new_name] = target_counts.get(new_name, 0) + 1
    duplicate_targets = [n for n, count in target_counts.items() if count > 1]
    collisions = [
        n for n in renames.values()
        if n in set(all_cols) and n not in renames
    ]
    if duplicate_targets:
        flash(
            "Renaming rejected: these names are used more than once: "
            + ", ".join(duplicate_targets),
            "error",
        )
        return redirect(url_for("dataset.show_dataset"))
    if collisions:
        flash(
            "Renaming rejected: a column with that name already exists: "
            + ", ".join(collisions),
            "error",
        )
        return redirect(url_for("dataset.show_dataset"))

    if renames:
        df = df.rename(columns=renames)
        valid_selected = [renames.get(c, c) for c in valid_selected]
        all_cols = [str(c) for c in df.columns]

    if not renames and not requested_types and set(valid_selected) == set(all_cols):
        # Nothing to drop or rename â€” just proceed as normal
        session.pop("selected_columns", None)
        session.pop("recipe_preview", None)
        flash("All columns kept. Proceeding to cleaning.", "success")
        return redirect(url_for("dataset.dataset_analysis"))

    # Drop unselected columns and save as new working file
    narrowed_df = df[valid_selected]
    original_name = dataset_session.original_filename
    try:
        # Pickle keeps the selected types intact between cleaning passes.
        output_path = dataset_exporter.export_dataset(
            narrowed_df,
            original_name,
            file_format="pickle",
            namespace=dataset_session.output_namespace,
        )
    except Exception:
        flash("Could not save the column selection. Please try again.", "error")
        return redirect(url_for("dataset.show_dataset"))

    session["current_path"] = str(output_path)
    session["processed_path"] = str(output_path)
    session["selected_columns"] = valid_selected
    renamed_types = {
        renames.get(column, column): target_type
        for column, target_type in requested_types.items()
    }
    if renamed_types:
        session["column_type_overrides"] = renamed_types
    else:
        session.pop("column_type_overrides", None)
    pending_mappings = {
        renames.get(column, column)
        for column in session.get("pending_category_columns", [])
        if renames.get(column, column) in valid_selected
    }
    if pending_mappings:
        session["pending_category_columns"] = sorted(pending_mappings)
    else:
        session.pop("pending_category_columns", None)
    session.pop("preview", None)
    session.pop("recipe_preview", None)
    dropped = [c for c in all_cols if c not in valid_selected]
    summary = f"Kept {len(valid_selected)} column(s)."
    if dropped:
        summary += f" Dropped: {', '.join(dropped)}."
    if renamed_types:
        summary += f" Updated {len(renamed_types)} data type(s)."
    before_stats = {
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "missing": int(df.isna().sum().sum()),
    }
    after_stats = {
        "rows": int(len(narrowed_df)),
        "columns": int(narrowed_df.shape[1]),
        "missing": int(narrowed_df.isna().sum().sum()),
    }
    existing_history = session.get("cleaning_history", [])
    session["cleaning_history"] = existing_history + [summary]
    session["step_history"] = [summary]
    session.setdefault("initial_stats", before_stats)
    session["before_stats"] = session["initial_stats"]
    session["after_stats"] = after_stats
    session["cleaned_columns"] = [str(column) for column in narrowed_df.columns]
    # Narrowing is a committed version too: undo can return to this shape.
    dataset_session.push_version(
        str(output_path),
        history=session["cleaning_history"],
        overrides=session.get("column_type_overrides", {}),
        rows_before=len(df),
        rows_after=len(narrowed_df),
        operations=[{
            "type": "select_columns",
            "columns": selected_recipe,
            "renames": dict(renames),
            "conversions": dict(requested_types),
        }],
        step_summary=[summary],
        event="select_columns",
    )
    flash(summary, "success")
    return redirect(url_for("dataset.dataset_analysis"))


def _friendly_type(dtype: str) -> str:
    """Map pandas dtypes to simple, non-programmer-friendly labels."""
    if dtype.startswith(("int", "float", "Int", "Float")):
        return "Number"
    if dtype.startswith("datetime"):
        return "Date"
    if dtype.startswith(("bool", "boolean")):
        return "Yes/No"
    return "Text"
