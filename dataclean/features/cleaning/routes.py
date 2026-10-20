"""Cleaning application, results and download routes."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from config import Config
from features.dataset import analysis as analysis_tools
from features.upload import dataset_importer
from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from workspace import dataset_exporter
from workspace.session import dataset_session
from utils.validators import dataset_limit_error, validate_column

from features.cleaning import operations as cleaning_operations

cleaning_bp = Blueprint("cleaning", __name__)


@cleaning_bp.route("/clean", methods=["POST"])
def clean_dataset():
    """Parse the user's selected operations and apply them via the service."""
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

    limit_error = dataset_limit_error(df)
    if limit_error:
        flash(limit_error, "error")
        return redirect(url_for("dataset.dataset_analysis"))

    preview_token = request.form.get("preview_token", "")
    preview = session.get("preview") or {}
    if preview_token:
        if (
            request.form.get("preview_dirty") == "1"
            or preview.get("token") != preview_token
            or preview.get("dataset") != dataset_session.dataset_fingerprint()
            or cleaning_operations.operation_plan_token(preview.get("operations", [])) != preview_token
        ):
            flash(
                "The preview is out of date â€” the dataset or the operations changed. "
                "Preview again before applying.",
                "error",
            )
            return redirect(url_for("dataset.dataset_analysis"))
        operations = preview.get("operations", [])
    else:
        operations = _parse_operations(df)
    if not operations:
        flash("No cleaning operations were selected. Choose at least one option.", "error")
        return redirect(url_for("dataset.dataset_analysis"))

    plan_errors = cleaning_operations.validate_plan(df, operations)
    if plan_errors:
        for error in plan_errors:
            flash(error, "error")
        flash("The dataset was not changed. Fix the plan and try again.", "error")
        return redirect(url_for("dataset.dataset_analysis"))

    overrides = dict(dataset_session.column_type_overrides)
    original_name = dataset_session.original_filename

    try:
        computed = _compute_cleaning(
            df,
            operations,
            overrides,
            original_name,
            dataset_session.output_namespace,
            list(session.get("pending_category_columns", [])),
        )
    except Exception:
        current_app.logger.exception("Cleaning run failed")
        flash(
            "We couldn't finish cleaning this dataset, so nothing was changed. "
            "Please try again.",
            "error",
        )
        return redirect(url_for("dataset.dataset_analysis"))
    _commit_cleaning(computed)
    return redirect(url_for("cleaning.cleaning_result"))


def _compute_cleaning(
    df: pd.DataFrame,
    operations: list[dict],
    overrides: dict,
    original_name: str,
    namespace: str | None,
    pending_category_columns: list,
) -> dict:
    """Run a validated plan and write its result file without changing session state."""
    before = _snapshot_stats(df)
    converted = dict(overrides)
    cleaned_df, step_history = cleaning_operations.apply_cleaning_operations(
        df, operations, converted_types=converted
    )
    after = _snapshot_stats(cleaned_df)
    # Lossless pickle keeps Int64/boolean/datetime types for later passes;
    # the downloads re-export CSV/Excel straight from this dataset.
    output_path = dataset_exporter.export_dataset(
        cleaned_df, original_name, file_format="pickle", namespace=namespace
    )
    return {
        "output_path": str(output_path),
        "step_history": step_history,
        "before": before,
        "after": after,
        "operations": operations,
        "converted_types": {
            column: target for column, target in converted.items() if column in cleaned_df.columns
        },
        "cleaned_columns": [str(col) for col in cleaned_df.columns],
        "pending_mappings": sorted(
            _pending_mapping_columns(df, cleaned_df, operations, pending_category_columns)
        ),
    }


def _pending_mapping_columns(df, cleaned_df, operations, pending_category_columns) -> set:
    """Columns whose remaining variants still await an explicit category mapping.

    Case normalization can remove the detector's case warning while values such
    as F/Fem/Female still need to be mapped by hand.
    """
    pending = set(pending_category_columns or ())
    mapped_columns = {op.get("column") for op in operations if op.get("type") == "categories"}
    pending -= mapped_columns
    before_text_findings = analysis_tools.detect_inconsistent_text(df)
    for op in operations:
        column = op.get("column")
        if (
            op.get("type") == "case"
            and column not in mapped_columns
            and column in before_text_findings
            and column in cleaned_df.columns
        ):
            values = cleaned_df[column].dropna().astype(str).str.strip()
            if values.nunique() > 1:
                pending.add(column)
            else:
                pending.discard(column)
    pending &= set(cleaned_df.columns.astype(str))
    return pending


def _commit_cleaning(
    computed: dict, *, event: str = "clean", source_recipe: list[dict] | None = None
) -> None:
    """Write a finished cleaning run into the session and the audit log."""
    pending_mappings = set(computed["pending_mappings"])
    if pending_mappings:
        session["pending_category_columns"] = sorted(pending_mappings)
    else:
        session.pop("pending_category_columns", None)

    step_history = computed["step_history"]
    existing_history = session.get("cleaning_history", [])
    if step_history != ["No changes were needed."]:
        new_history = existing_history + step_history
    else:
        new_history = existing_history if existing_history else step_history

    session["processed_path"] = computed["output_path"]
    session["current_path"] = computed["output_path"]
    session["column_type_overrides"] = computed["converted_types"]
    session["cleaning_history"] = new_history
    session["step_history"] = step_history
    session["before_stats"] = session.get("initial_stats") or computed["before"]
    if "initial_stats" not in session:
        session["initial_stats"] = computed["before"]
    session["after_stats"] = computed["after"]
    session["cleaned_columns"] = computed["cleaned_columns"]
    if "selected_columns" in computed:
        session["selected_columns"] = computed["selected_columns"]
    session.pop("preview", None)  # consumed
    session.pop("recipe_preview", None)
    dataset_session.push_version(
        computed["output_path"],
        history=new_history,
        overrides=computed["converted_types"],
        rows_before=int(computed["before"]["rows"]),
        rows_after=int(computed["after"]["rows"]),
        operations=computed["operations"],
        step_summary=step_history,
        event=event,
        source_recipe=source_recipe,
    )


@cleaning_bp.route("/cleaning/result")
def cleaning_result():
    """Show before/after summary, the cleaning history and a data preview."""
    processed_path = session.get("processed_path")
    if not processed_path:
        flash(
            "Your workspace is unavailable or has expired. Upload a dataset to begin again.",
            "error",
        )
        return redirect(url_for("upload.upload_page"))

    df = dataset_importer.load_dataset(processed_path)
    if df is None:
        flash("The cleaned dataset could not be opened. Please clean the dataset again.", "error")
        return redirect(url_for("upload.index"))
    df = dataset_session.apply_column_types(df)

    preview_columns = [str(col) for col in df.columns]
    preview_values = [
        [None if pd.isna(value) else str(value) for value in row]
        for row in df.head(Config.PREVIEW_ROWS).itertuples(index=False, name=None)
    ]

    return render_template(
        "results.html",
        filename=dataset_session.original_filename,
        before=session.get("before_stats", {}),
        after=session.get("after_stats", {}),
        history=session.get("cleaning_history", []),
        step_history=session.get("step_history", []),
        runs=session.get("audit_log", []),
        can_undo=bool(session.get("history_stack")),
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

    base_name = dataset_session.original_filename
    stem = base_name.rsplit(".", 1)[0]

    df = dataset_importer.load_dataset(processed)
    if df is None:
        abort(404)

    if file_format == "csv":
        try:
            params = dataset_session.upload_load_params
            csv_path = dataset_exporter.export_dataset(
                df,
                stem,
                file_format="csv",
                encoding=params.get("encoding") or "utf-8",
                delimiter=params.get("delimiter") if params.get("delimiter") != "auto" else None,
                namespace=dataset_session.output_namespace,
            )
        except Exception:
            abort(404)
        dataset_session.register_file(csv_path)
        session["csv_path"] = str(csv_path)
        return send_file(csv_path, as_attachment=True, download_name=f"{stem}_cleaned.csv")
    df = dataset_session.apply_column_types(df)
    try:
        excel_path = dataset_exporter.export_dataset(
            df,
            stem,
            file_format="excel",
            sheet=dataset_session.upload_load_params.get("sheet"),
            namespace=dataset_session.output_namespace,
        )
    except Exception:
        abort(404)
    session["excel_path"] = str(excel_path)
    dataset_session.register_file(excel_path)
    return send_file(excel_path, as_attachment=True, download_name=f"{stem}_cleaned.xlsx")


@cleaning_bp.route("/download/audit")
def download_audit():
    """Download the cleaning recipe + audit summary as JSON.

    The recipe (ordered operations with parameters) is what a supported
    replay needs; the audit adds timestamps and impact counts per run.
    """
    stem = dataset_session.original_filename.rsplit(".", 1)[0]
    recipe = []
    for entry in session.get("history_stack", []):
        if entry.get("event") == "recipe_import" and entry.get("source_recipe"):
            recipe.extend(entry["source_recipe"])
        else:
            recipe.append({
                "event": entry.get("event"),
                "operations": entry.get("operations", []),
            })
    payload = {
        "format": cleaning_operations.RECIPE_FORMAT,
        "version": cleaning_operations.RECIPE_VERSION,
        "original_filename": dataset_session.original_filename,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "recipe": recipe,
        "runs": [
            {
                "event": entry.get("event"),
                "timestamp": entry.get("ts"),
                "operations": entry.get("operations", []),
                "rows_before": entry.get("rows_before"),
                "rows_after": entry.get("rows_after"),
                "history": entry.get("step_summary", []),
                "source_recipe": entry.get("source_recipe"),
            }
            for entry in session.get("audit_log", [])
        ],
    }
    body = json.dumps(payload, indent=2, ensure_ascii=False)
    return Response(
        body,
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename={stem}_recipe_audit.json"},
    )


@cleaning_bp.route("/recipe/inspect", methods=["POST"])
def inspect_recipe():
    """Validate and dry-run an uploaded recipe without changing the dataset."""
    recipe_file = request.files.get("recipe_file")
    if not dataset_session.working_path:
        flash("Please upload a dataset first.", "error")
        return redirect(url_for("upload.index"))
    if recipe_file is None or not recipe_file.filename:
        session["recipe_preview"] = {"result": {"errors": ["Choose a JSON recipe file."]}}
        return redirect(url_for("dataset.dataset_analysis"))
    if Path(recipe_file.filename).suffix.lower() != ".json":
        session["recipe_preview"] = {"result": {"errors": ["Recipe files must use .json."]}}
        return redirect(url_for("dataset.dataset_analysis"))

    try:
        raw = recipe_file.stream.read(Config.MAX_RECIPE_BYTES + 1)
        if len(raw) > Config.MAX_RECIPE_BYTES:
            raise ValueError("Recipe files must be 1 MB or smaller.")
        payload = json.loads(raw.decode("utf-8-sig"))
        recipe = cleaning_operations.normalize_recipe_payload(
            payload,
            max_runs=Config.MAX_RECIPE_RUNS,
            max_operations=Config.MAX_RECIPE_OPERATIONS,
        )
        df = dataset_importer.load_dataset(
            dataset_session.working_path, **dataset_session.working_load_params
        )
        if df is None:
            raise ValueError("The current dataset is no longer available.")
        df = dataset_session.apply_column_types(df)
        limit_error = dataset_limit_error(df)
        if limit_error:
            raise ValueError(limit_error)
        _, report, _, _ = cleaning_operations.inspect_recipe(
            df, recipe, converted_types=dataset_session.column_type_overrides
        )
        token = cleaning_operations.operation_plan_token(recipe)
        session["recipe_preview"] = {
            "token": token,
            "dataset": dataset_session.dataset_fingerprint(),
            "recipe": recipe,
            "result": report,
        }
        flash("Recipe checked. Review its impact before applying it.", "success")
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        session["recipe_preview"] = {"result": {"errors": [str(exc)]}}
    return redirect(url_for("dataset.dataset_analysis"))


@cleaning_bp.route("/recipe/apply", methods=["POST"])
def apply_recipe():
    """Re-run and atomically commit a previously reviewed recipe."""
    preview = session.get("recipe_preview") or {}
    recipe = preview.get("recipe")
    token = request.form.get("recipe_token", "")
    if (
        not token
        or preview.get("token") != token
        or preview.get("dataset") != dataset_session.dataset_fingerprint()
        or not isinstance(recipe, list)
        or cleaning_operations.operation_plan_token(recipe) != token
    ):
        flash("The recipe review is out of date. Check the recipe again.", "error")
        return redirect(url_for("dataset.dataset_analysis"))

    try:
        df = dataset_importer.load_dataset(
            dataset_session.working_path, **dataset_session.working_load_params
        )
        if df is None:
            raise ValueError("The current dataset is no longer available.")
        df = dataset_session.apply_column_types(df)
        cleaned, _, converted, history = cleaning_operations.inspect_recipe(
            df, recipe, converted_types=dataset_session.column_type_overrides
        )
        clean_operations = [
            operation
            for run in recipe
            if run["event"] == "clean"
            for operation in run["operations"]
        ]
        pending_mappings = sorted(_pending_mapping_columns(
            df,
            cleaned,
            clean_operations,
            list(session.get("pending_category_columns", [])),
        ))
        output_path = dataset_exporter.export_dataset(
            cleaned,
            dataset_session.original_filename,
            file_format="pickle",
            namespace=dataset_session.output_namespace,
        )
        computed = {
            "output_path": str(output_path),
            "step_history": history or ["Recipe made no changes."],
            "before": _snapshot_stats(df),
            "after": _snapshot_stats(cleaned),
            "operations": [],
            "converted_types": converted,
            "cleaned_columns": [str(column) for column in cleaned.columns],
            "selected_columns": [str(column) for column in cleaned.columns],
            "pending_mappings": pending_mappings,
        }
    except (TypeError, ValueError):
        flash("The recipe could no longer be applied. Check it again.", "error")
        return redirect(url_for("dataset.dataset_analysis"))
    except Exception:
        current_app.logger.exception("Recipe application failed")
        flash("We couldn't apply the recipe, so nothing was changed.", "error")
        return redirect(url_for("dataset.dataset_analysis"))

    _commit_cleaning(computed, event="recipe_import", source_recipe=recipe)
    flash(f"Applied {len(recipe)} reviewed recipe run(s).", "success")
    return redirect(url_for("cleaning.cleaning_result"))


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
        operations.append({"type": "empty_columns", "tokens": list(Config.MISSING_TOKENS)})

    # Whitespace and duplicate removal
    if form.get("op_trim"):
        operations.append({"type": "trim_whitespace"})
    if form.get("op_duplicates"):
        mode = form.get("dedup_mode", "first")
        if mode not in ("first", "last", "all"):
            mode = "first"
        op = {"type": "duplicates", "mode": mode}
        # Checked columns define a duplicate key; unchecked means the entire row.
        subset = [c for c in form.getlist("dedup_columns") if validate_column(df, c)]
        if subset:
            op["subset"] = subset
        operations.append(op)

    # Date cleaning from date issue cards
    global_dayfirst = bool(form.get("date_dayfirst"))
    global_date_format = form.get("date_format", "")
    if global_date_format not in cleaning_operations.DATE_FORMATS:
        global_date_format = ""
    timezone_policy = "utc" if form.get("date_utc") else "preserve"
    for key, column in form.items():
        if not key.startswith("datecol_"):
            continue
        index = key.removeprefix("datecol_")
        action = form.get(f"dateaction_{index}", "")
        if action in ("coerce", "remove") and validate_column(df, column):
            operations.append({
                "type": "invalid_date",
                "column": column,
                "action": action,
                "dayfirst": global_dayfirst,
                "date_format": global_date_format,
            })

    # Missing-value handling, one entry per configured column.
    for key, column in form.items():
        if not key.startswith("misscol_"):
            continue
        index = key.removeprefix("misscol_")
        method = form.get(f"missing_{index}", "")
        if method and validate_column(df, column):
            tokens = list(Config.MISSING_TOKENS) if form.get(f"misstok_{index}") else []
            operations.append({
                "type": "missing",
                "column": column,
                "method": method,
                "value": form.get(f"missingval_{index}") or None,
                "tokens": tokens,
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
    conv_policies = form.getlist("convert_policy")
    conv_formats = form.getlist("convert_format")
    for index, (ccol, ctype) in enumerate(zip(conv_cols, conv_types)):
        if ccol and ctype in ("integer", "float", "string", "date", "boolean") and validate_column(df, ccol):
            policy = conv_policies[index] if index < len(conv_policies) else "reject"
            number_format = conv_formats[index] if index < len(conv_formats) else ""
            if number_format not in ("", "us", "eu"):
                number_format = ""
            operations.append({
                "type": "convert",
                "column": ccol,
                "target_type": ctype,
                "invalid_policy": policy,
                "number_format": number_format if ctype in ("integer", "float") else "",
                "dayfirst": global_dayfirst if ctype == "date" else False,
                "date_format": global_date_format if ctype == "date" else "",
                "timezone_policy": timezone_policy if ctype == "date" else "preserve",
            })

    # Unicode/whitespace normalization across every text column.
    if form.get("op_normalize"):
        operations.append({"type": "normalize_text", "columns": []})

    # One regex replacement rule.
    regex_column = form.get("regex_column", "")
    regex_pattern = (form.get("regex_pattern") or "").strip()
    if regex_column and regex_pattern and validate_column(df, regex_column):
        operations.append({
            "type": "regex_replace",
            "column": regex_column,
            "pattern": regex_pattern,
            "replacement": form.get("regex_replacement", ""),
        })

    # Split one column on a separator into named columns.
    split_source = form.get("split_column", "")
    if split_source and validate_column(df, split_source):
        names = [
            name.strip()
            for name in (form.get("split_new_columns") or "").split(",")
            if name.strip()
        ]
        if names:
            operations.append({
                "type": "split_column",
                "column": split_source,
                "separator": form.get("split_separator", ""),
                "new_columns": names,
            })

    # Merge several columns into one.
    merge_sources = [c for c in form.getlist("merge_columns") if validate_column(df, c)]
    if len(merge_sources) >= 2:
        operations.append({
            "type": "merge_columns",
            "columns": merge_sources,
            "separator": form.get("merge_separator", " "),
            "new_column": (form.get("merge_new_column") or "").strip(),
            "drop_source": form.get("merge_keep") != "1",
        })

    # Group-based filling of one column.
    groupfill_column = form.get("groupfill_column", "")
    groupfill_by = [c for c in form.getlist("groupfill_by") if validate_column(df, c)]
    groupfill_method = form.get("groupfill_method", "")
    if (
        groupfill_column
        and validate_column(df, groupfill_column)
        and groupfill_by
        and groupfill_method
    ):
        operations.append({
            "type": "group_fill",
            "column": groupfill_column,
            "group_by": groupfill_by,
            "method": groupfill_method,
        })

    # Compact rule builder submits one reviewed JSON object per rule.
    submitted_rules = form.getlist("validation_rule_json")
    for raw_rule in submitted_rules:
        try:
            operation = json.loads(raw_rule)
        except (TypeError, json.JSONDecodeError):
            operation = None
        if isinstance(operation, dict) and operation.get("type") == "validate":
            operations.append(operation)
        else:
            operations.append({"type": "invalid_validation_rule"})

    # Keep accepting the previous single-rule fields for old clients/tests.
    validate_target = form.get("validate_column", "")
    validate_rule = form.get("validate_rule", "")
    if not submitted_rules and validate_target and validate_rule and validate_column(df, validate_target):
        op = {
            "type": "validate",
            "column": validate_target,
            "rule": validate_rule,
            "action": form.get("validate_action") or "report",
            "tokens": list(Config.MISSING_TOKENS) if form.get("validate_tokens") else [],
        }
        minimum = (form.get("validate_min") or "").strip()
        maximum = (form.get("validate_max") or "").strip()
        if minimum:
            op["minimum"] = minimum
        if maximum:
            op["maximum"] = maximum
        values = [v.strip() for v in (form.get("validate_values") or "").split(",") if v.strip()]
        if values:
            op["values"] = values
        pattern = (form.get("validate_pattern") or "").strip()
        if pattern:
            op["pattern"] = pattern
        multiplier = (form.get("validate_multiplier") or "").strip()
        if multiplier:
            op["multiplier"] = multiplier
        operations.append(op)

    return operations


def _snapshot_stats(df: pd.DataFrame) -> dict:
    """Capture the small set of numbers shown in the before/after summary."""
    return {
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "missing": int(df.isna().sum().sum()),
    }
