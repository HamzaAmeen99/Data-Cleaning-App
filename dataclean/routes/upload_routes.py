"""Homepage, product intro and file upload routes."""


from flask import Blueprint, flash, redirect, render_template, request, url_for
from services import file_service
from services.dataset_session import dataset_session
from utils.csrf import check_csrf
from utils.validators import dataset_limit_error, validate_file

upload_bp = Blueprint("upload", __name__)


@upload_bp.route("/")
def index():
    """Show the product landing page."""
    return render_template("landing.html")


@upload_bp.route("/upload", methods=["GET"])
def upload_page():
    """Show the file upload workspace."""
    return render_template("upload.html")


@upload_bp.route("/session/end", methods=["POST"])
def end_session():
    """Delete the active session's files and discard all workflow progress."""
    check_csrf()
    is_beacon = request.form.get("beacon") == "1"
    dataset_session.end()
    if is_beacon:
        return "", 204
    flash("Session ended. Your uploaded and processed files were deleted.", "success")
    return redirect(url_for("upload.upload_page"))


@upload_bp.route("/upload/sample", methods=["GET"])
def sample_redirect():
    # The sample-dataset feature was removed; old bookmarks land on the upload page
    return redirect(url_for("upload.upload_page"))


@upload_bp.route("/upload", methods=["POST"])
def upload_dataset():
    """Receive, validate and store the uploaded file, then go to the overview."""
    check_csrf()
    file = request.files.get("file")
    filename = validate_file(file)
    if filename is None:
        return redirect(url_for("upload.upload_page"))

    if not file_service.allowed_file(filename):
        flash("Unsupported file type. Please upload a CSV, XLSX or XLS file.", "error")
        return redirect(url_for("upload.upload_page"))

    filepath = file_service.save_uploaded_file(file)
    if filepath is None:
        flash("We couldn't save that file. Please try again.", "error")
        return redirect(url_for("upload.upload_page"))

    # Optional CSV/Excel controls chosen on the upload form.
    encoding = request.form.get("encoding", "") or None
    if encoding not in (None, "utf-8", "utf-8-sig", "latin1"):
        encoding = None
    delimiter = request.form.get("delimiter", "") or "auto"
    if delimiter not in ("auto", ",", ";", "\t"):
        delimiter = "auto"
    sheet = (request.form.get("sheet") or "").strip() or None

    # Fail fast with a helpful message when the chosen worksheet is missing.
    if filepath.suffix.lower() in (".xls", ".xlsx") and sheet:
        sheets = file_service.list_excel_sheets(filepath)
        if sheets and sheet not in sheets:
            file_service.delete_file(filepath)
            flash(
                f"Worksheet '{sheet}' not found. This workbook has: {', '.join(sheets)}.",
                "error",
            )
            return redirect(url_for("upload.upload_page"))

    # Validate the new file before replacing an existing working session.
    candidate, reason = file_service.load_dataset_with_error(
        filepath,
        encoding=encoding,
        delimiter=delimiter,
        sheet=sheet,
    )
    if candidate is None:
        file_service.delete_file(filepath)
        flash(f"We couldn't read this file. {reason}", "error")
        return redirect(url_for("upload.upload_page"))
    if candidate.empty:
        file_service.delete_file(filepath)
        flash("This file contains no data rows. Please choose another dataset.", "error")
        return redirect(url_for("upload.upload_page"))
    limit_error = dataset_limit_error(candidate)
    if limit_error:
        file_service.delete_file(filepath)
        flash(limit_error, "error")
        return redirect(url_for("upload.upload_page"))

    dataset_session.start_new(
        filepath,
        filename,
        encoding=encoding,
        delimiter=delimiter,
        sheet=sheet,
    )
    flash("Dataset uploaded successfully.", "success")
    return redirect(url_for("dataset.show_dataset"))

