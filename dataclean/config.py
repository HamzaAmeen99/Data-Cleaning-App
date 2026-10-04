# DataClean configuration.
# All tunable settings live here so nothing is hardcoded elsewhere.

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class Config:
    """Base application configuration."""

    SECRET_KEY = os.environ.get("DATACLEAN_SECRET_KEY", "dev-key-change-me")

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
