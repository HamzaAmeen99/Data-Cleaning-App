"""Store, name, remove, and expire private workspace files."""

import time
from pathlib import Path
from uuid import uuid4

from flask import current_app


def new_workspace_namespace() -> str:
    """Return an opaque folder name for one dataset workspace."""
    return uuid4().hex


def save_uploaded_file(file, filename: str) -> Path | None:
    """Save an upload under a collision-free name."""
    upload_dir = Path(current_app.config["UPLOAD_FOLDER"])
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = _available_path(upload_dir, filename)
    try:
        file.save(target)
    except OSError:
        return None
    return target


def new_output_path(
    base_name: str | Path,
    extension: str,
    namespace: str | None = None,
) -> Path:
    """Reserve a unique path inside the processed-data workspace."""
    processed_dir = Path(current_app.config["PROCESSED_FOLDER"])
    if namespace:
        processed_dir /= str(namespace)
    processed_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(base_name).stem
    return processed_dir / f"{stem}_{uuid4().hex}.{extension.lstrip('.')}"


def delete_file(filepath: str | Path | None) -> None:
    """Safely remove a file from disk if it exists."""
    if filepath:
        try:
            path = Path(filepath)
            if path.is_file():
                path.unlink(missing_ok=True)
        except OSError:
            pass


def sweep_stale_files(max_age_seconds: float, roots=None, protected_paths=None) -> int:
    """Delete expired private files and their empty directories."""
    folders = roots or (
        current_app.config["UPLOAD_FOLDER"],
        current_app.config["PROCESSED_FOLDER"],
    )
    protected = {
        str(Path(path).resolve()) for path in (protected_paths or ()) if path
    }
    cutoff = time.time() - max(0.0, float(max_age_seconds))
    removed = 0
    for folder in folders:
        folder = Path(folder)
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if any(part.startswith(".") for part in path.relative_to(folder).parts):
                continue
            try:
                if str(path.resolve()) in protected:
                    continue
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
                    removed += 1
                elif path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
            except OSError:
                continue
    return removed


def _available_path(folder: Path, filename: str) -> Path:
    """Return a non-existing path while preserving the original extension."""
    target = folder / filename
    counter = 1
    while target.exists():
        path = Path(filename)
        target = folder / f"{path.stem}_{counter}{path.suffix}"
        counter += 1
    return target
