"""CSRF validation helper for the application-level request guard.

The app-level request guard calls this for every matched unsafe request.
"""
import logging
import secrets

from flask import abort, request, session


def check_csrf() -> None:
    """Abort with 403 if the form token does not match the session."""
    form_token = request.form.get("csrf_token", "")
    session_token = session.get("csrf_token", "")
    if not form_token or not secrets.compare_digest(form_token, session_token):
        logging.warning("CSRF validation failed for %s", request.path)
        abort(403)
