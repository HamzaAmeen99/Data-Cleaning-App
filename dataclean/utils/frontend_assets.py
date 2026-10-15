"""Minified inline styles and immutable, content-versioned public assets."""

from hashlib import sha256
from pathlib import Path

from flask import abort, request, send_from_directory, url_for
from flask.sessions import SessionInterface
from rcssmin import cssmin


class AssetSessionInterface(SessionInterface):
    """Keep Flask-Session's sliding cookie refresh off shared public responses."""

    def __init__(self, delegate):
        self.delegate = delegate

    def open_session(self, app, request):
        return self.delegate.open_session(app, request)

    def save_session(self, app, session, response):
        if request.endpoint != "frontend_asset":
            self.delegate.save_session(app, session, response)


def register_frontend_assets(app):
    app.session_interface = AssetSessionInterface(app.session_interface)
    root = Path(app.static_folder)
    files = [root / "js/app.js", root / "favicon.svg", *sorted((root / "fonts").glob("*.woff2"))]
    versions = {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()[:12]
        for path in files
    }
    stylesheet = cssmin("\n".join(
        (root / "css" / name).read_text(encoding="utf-8")
        for name in ("fonts.css", "style.css")
    ))

    @app.get("/assets/<version>/<path:filename>")
    def frontend_asset(version, filename):
        # Only bundled frontend files are public; never serve uploads or stale versions.
        if versions.get(filename) != version:
            abort(404)
        response = send_from_directory(root, filename, max_age=31536000)
        response.cache_control.immutable = True
        return response

    def asset_url(filename):
        return url_for("frontend_asset", version=versions[filename], filename=filename)

    @app.context_processor
    def frontend_context():
        css = stylesheet
        for filename in versions:
            if filename.startswith("fonts/"):
                css = css.replace(f"/static/{filename}", asset_url(filename))
        return {"frontend_css": css, "asset_url": asset_url}
