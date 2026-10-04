"""DataClean - a small no-code data cleaning utility.

Run from the dataclean/ directory with:
    flask --app app run --debug
"""

from flask import Flask, flash, redirect, render_template, request, url_for

from config import Config
from routes import cleaning_bp, dataset_bp, upload_bp


def create_app(config_class: type[Config] = Config) -> Flask:
    """Application factory: build, configure and wire up the Flask app."""
    app = Flask(__name__)
    app.config.from_object(config_class)

    _ensure_folders(app)

    app.register_blueprint(upload_bp)
    app.register_blueprint(dataset_bp)
    app.register_blueprint(cleaning_bp)

    _register_error_handlers(app)

    @app.context_processor
    def inject_app_name():
        return {"app_name": "DataClean"}

    @app.template_filter("number_format")
    def number_format(value) -> str:
        """Format numbers with thousands separators, e.g. 4821 -> 4,821."""
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return str(value)

    return app


def _ensure_folders(app: Flask) -> None:
    """Make sure the upload and processed directories exist."""
    for folder in (app.config["UPLOAD_FOLDER"], app.config["PROCESSED_FOLDER"]):
        folder.mkdir(parents=True, exist_ok=True)


def _register_error_handlers(app: Flask) -> None:
    """Show friendly error pages instead of Python stack traces."""

    @app.errorhandler(413)
    def too_large(_error):
        flash("That file is too large. Please upload a file smaller than 16 MB.", "error")
        return redirect(url_for("upload.index")), 302

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", message="Page not found."), 404

    @app.errorhandler(500)
    def server_error(_error):
        return (
            render_template(
                "error.html",
                message="Something went wrong while processing your request. Please try again.",
            ),
            500,
        )


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
