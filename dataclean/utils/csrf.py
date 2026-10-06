"""CSRF validation helper — kept separate from app.py to avoid circular imports.

Routes import check_csrf from here; app.py seeds the token via a before_request hook.
"""
import logging
import secrets

from flask import abort, request, session


def check_csrf() -> None:
    """Abort with 403 if the CSRF token in the form does not match the session.

    Call this at the top of every state-mutating POST route handler.
    """
    form_token = request.form.get("csrf_token", "")
    session_token = session.get("csrf_token", "")
    if not form_token or not secrets.compare_digest(form_token, session_token):
        logging.warning("CSRF validation failed for %s", request.path)
        abort(403)