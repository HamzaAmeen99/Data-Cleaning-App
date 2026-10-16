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
    SESSION_FILE_DIR = BASE_DIR / "instance" / "flask_session"

    # File storage (kept out of /static so uploads are never served directly)
    UPLOAD_FOLDER = BASE_DIR / "uploads"
    PROCESSED_FOLDER = BASE_DIR / "processed"

    # Upload limits
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB

    # Dataset size limits, enforced at upload and before any cleaning run.
    # Raise these for bigger machines; the values trade memory for throughput.
    MAX_ROWS = 300_000
    MAX_COLUMNS = 500
    MAX_CELLS = 6_000_000

    # Imported recipes are small, reviewable configuration files rather than datasets.
    MAX_RECIPE_BYTES = 1 * 1024 * 1024
    MAX_RECIPE_RUNS = 50
    MAX_RECIPE_OPERATIONS = 200

    # Uploaded/processed files nobody has touched for this long are swept away.
    FILE_TTL_SECONDS = 24 * 60 * 60

    # Supported file formats
    ALLOWED_EXTENSIONS = {"csv", "xlsx", "xls"}

    # How many rows to show in HTML previews
    PREVIEW_ROWS = 20

    # A column is considered "mostly empty" when more than this share is missing
    MOSTLY_MISSING_THRESHOLD = 0.9

    # Text tokens that stand for a missing value, matched case-insensitively.
    # Whitespace-only and empty cells always count as missing; these extras
    # count too once the user opts in per column.
    MISSING_TOKENS = ("n/a", "na", "null", "-", "--")
