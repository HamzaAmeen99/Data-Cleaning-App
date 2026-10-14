"""Homepage, product intro and file upload routes."""

from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, url_for

from utils.csrf import check_csrf
from services import file_service
from services.dataset_session import dataset_session
from utils.validators import validate_file

upload_bp = Blueprint("upload", __name__)


@upload_bp.route("/")
def index():
    """Show the product landing page."""
    return render_template("landing.html")


@upload_bp.route("/upload", methods=["GET"])
def upload_page():
    """Show the file upload workspace."""
    return render_template("upload.html")


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

    dataset_session.start_new(filepath, filename)
    flash("Dataset uploaded successfully.", "success")
    return redirect(url_for("dataset.show_dataset"))

