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
    public_files = {path.relative_to(root).as_posix(): path for path in files}
    version_cache = {}
    css_files = tuple(root / "css" / name for name in ("fonts.css", "style.css"))
    stylesheet_cache = {"signature": None, "content": ""}

    def asset_version(filename):
        """Hash an asset again only when its size or modification time changes."""
        path = public_files.get(filename)
        if path is None:
            abort(404)
        stat = path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
        cached = version_cache.get(filename)
        if cached is None or cached[0] != signature:
            cached = (signature, sha256(path.read_bytes()).hexdigest()[:12])
            version_cache[filename] = cached
        return cached[1]

    def current_stylesheet():
        """Refresh the inline bundle after a CSS edit without restarting Flask."""
        signature = tuple((path.stat().st_mtime_ns, path.stat().st_size) for path in css_files)
        if stylesheet_cache["signature"] != signature:
            stylesheet_cache["content"] = cssmin("\n".join(
                path.read_text(encoding="utf-8") for path in css_files
            ))
            stylesheet_cache["signature"] = signature
        return stylesheet_cache["content"]

    @app.get("/assets/<version>/<path:filename>")
    def frontend_asset(version, filename):
        # Only bundled frontend files are public; never serve uploads or stale versions.
        if filename not in public_files or asset_version(filename) != version:
            abort(404)
        response = send_from_directory(root, filename, max_age=31536000)
        response.cache_control.immutable = True
        return response

    def asset_url(filename):
        return url_for("frontend_asset", version=asset_version(filename), filename=filename)

    @app.context_processor
    def frontend_context():
        css = current_stylesheet()
        for filename in public_files:
            if filename.startswith("fonts/"):
                css = css.replace(f"/static/{filename}", asset_url(filename))
        return {"frontend_css": css, "asset_url": asset_url}
