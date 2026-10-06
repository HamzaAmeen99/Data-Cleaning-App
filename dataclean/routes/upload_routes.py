"""Homepage, product intro and file upload routes."""

import shutil
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

from utils.csrf import check_csrf
from services import file_service
from utils.validators import validate_file

upload_bp = Blueprint("upload", __name__)


@upload_bp.route("/")
def index():
    """Show the product intro / landing page."""
    return render_template("landing.html")


@upload_bp.route("/upload", methods=["GET"])
def upload_page():
    """Show the file upload workspace."""
    return render_template("upload.html")


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

    # Verify the file can actually be parsed before showing any pages.
    df = file_service.load_dataset(filepath)
    if df is None:
        file_service.delete_file(filepath)
        flash(
            "We couldn't read this file. Please make sure it is a valid CSV or Excel file.",
            "error",
        )
        return redirect(url_for("upload.upload_page"))
    if df.empty:
        file_service.delete_file(filepath)
        flash("This file contains no data rows. Please upload a dataset with data.", "error")
        return redirect(url_for("upload.upload_page"))

    session["upload_path"] = str(filepath)
    session["current_path"] = str(filepath)
    session["original_filename"] = filename
    session.pop("processed_path", None)
    session["cleaning_history"] = []
    flash("Dataset uploaded successfully.", "success")
    return redirect(url_for("dataset.show_dataset"))


@upload_bp.route("/upload/sample", methods=["GET", "POST"])
def load_sample():
    """Load the built-in sample academic dataset for immediate testing."""
    sample_source = Path(__file__).resolve().parent.parent / "tests" / "sample_messy_data.csv"
    if not sample_source.exists():
        flash("Sample dataset not found.", "error")
        return redirect(url_for("upload.upload_page"))

    upload_dir = Path(current_app.config["UPLOAD_FOLDER"])
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / "sample_messy_data.csv"
    shutil.copy(sample_source, target)

    session["upload_path"] = str(target)
    session["current_path"] = str(target)
    session["original_filename"] = "sample_messy_data.csv"
    session.pop("processed_path", None)
    session["cleaning_history"] = []
    flash("Sample dataset loaded successfully! Review the detected issues below.", "success")
    return redirect(url_for("dataset.show_dataset"))
