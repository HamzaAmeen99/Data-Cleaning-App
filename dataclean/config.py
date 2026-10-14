# DataClean configuration.
# All tunable settings live here so nothing is hardcoded elsewhere.

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class Config:
    """Base application configuration."""

    # Random per-boot key in dev sessions; set DATACLEAN_SECRET_KEY for a stable key in deployment
    SECRET_KEY = os.environ.get("DATACLEAN_SECRET_KEY") or secrets.token_hex(32)

    # Server-side sessions keep dataset state off the 4 KB cookie (only a SID travels)
    SESSION_TYPE = "filesystem"
    SESSION_FILE_DIR = BASE_DIR / "flask_session"

    # File storage (kept out of /static so uploads are never served directly)
    UPLOAD_FOLDER = BASE_DIR / "uploads"
    PROCESSED_FOLDER = BASE_DIR / "processed"

    # Upload limits
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB

    # Supported file formats
    ALLOWED_EXTENSIONS = {"csv", "xlsx", "xls"}

    # How many rows to show in HTML previews
    PREVIEW_ROWS = 20

    # A column is considered "mostly empty" when more than this share is missing
    MOSTLY_MISSING_THRESHOLD = 0.9
