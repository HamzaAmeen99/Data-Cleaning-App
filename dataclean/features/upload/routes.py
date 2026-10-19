"""Homepage, product intro and file upload routes."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from utils.csrf import check_csrf
from workspace.session import dataset_session

from .workflow import process_upload

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
    return redirect(url_for("upload.upload_page"))


@upload_bp.route("/upload", methods=["POST"])
def upload_dataset():
    """Receive, validate and store the uploaded file, then go to the overview."""
    check_csrf()
    accepted, message = process_upload(
        request.files.get("file"),
        request.form.get("encoding", ""),
        request.form.get("delimiter", ""),
        request.form.get("sheet", ""),
    )
    flash(message, "success" if accepted else "error")
    if accepted:
        return redirect(url_for("dataset.show_dataset"))
    return redirect(url_for("upload.upload_page"))
