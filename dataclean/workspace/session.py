"""Single owner of the dataset-related keys kept in the Flask session."""

from datetime import datetime, timezone
from pathlib import Path

from features.cleaning.operations import convert_column_type
from flask import current_app, session

from workspace import file_store


class DatasetSession:
    """Reads and writes the dataset session keys so routes never duplicate them."""

    @property
    def upload_path(self) -> str | None:
        return session.get("upload_path")

    @property
    def baseline_path(self) -> str | None:
        return session.get("baseline_path")

    @property
    def working_path(self) -> str | None:
        return (
            session.get("current_path")
            or session.get("baseline_path")
            or session.get("upload_path")
        )

    @property
    def original_filename(self) -> str:
        return session.get("original_filename", "dataset")

    @property
    def is_cleaned(self) -> bool:
        baseline = session.get("baseline_path") or session.get("upload_path")
        return bool(
            session.get("processed_path")
            or (
                session.get("current_path")
                and session.get("current_path") != baseline
            )
        )

    @property
    def column_type_overrides(self) -> dict[str, str]:
        return session.get("column_type_overrides", {})

    @property
    def output_namespace(self) -> str | None:
        """Sub-folder that isolates this session's generated files."""
        return session.get("output_namespace")

    @property
    def upload_load_params(self) -> dict:
        """CSV encoding / delimiter / Excel sheet the user chose at upload."""
        return {
            "encoding": session.get("upload_encoding"),
            "delimiter": session.get("upload_delimiter"),
            "sheet": session.get("upload_sheet"),
        }

    @property
    def working_load_params(self) -> dict:
        """Use source parsing settings only for a pre-snapshot legacy session."""
        path = self.working_path
        if path and Path(path).suffix.lower() == ".pkl":
            return {}
        return self.upload_load_params

    def apply_column_types(self, df):
        """Reapply user-selected types after loading an intermediate CSV."""
        converted = df
        for column, target_type in self.column_type_overrides.items():
            converted, _ = convert_column_type(converted, column, target_type)
        return converted

    def register_file(self, path: str | Path) -> None:
        """Track every private file created for this dataset session."""
        value = str(path)
        paths = list(session.get("owned_paths", []))
        if value not in paths:
            paths.append(value)
            session["owned_paths"] = paths

    def dataset_fingerprint(self) -> dict | None:
        """Identity used to bind a preview to one immutable working file."""
        path = self.working_path
        if not path:
            return None
        try:
            resolved = Path(path).resolve()
            stat = resolved.stat()
        except OSError:
            return None
        return {
            "path": str(resolved),
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        }

    def _known_paths(self) -> set[str]:
        paths = {str(path) for path in session.get("owned_paths", []) if path}
        for key in (
            "upload_path", "baseline_path", "current_path", "processed_path",
            "excel_path", "csv_path",
        ):
            if session.get(key):
                paths.add(str(session[key]))
        for collection in ("history_stack", "audit_log"):
            for entry in session.get(collection, []):
                if entry.get("file"):
                    paths.add(str(entry["file"]))
        return paths

    @staticmethod
    def _clear_workflow_state() -> None:
        for key in (
            "upload_path", "baseline_path", "current_path", "processed_path",
            "excel_path", "csv_path",
            "original_filename", "upload_encoding", "upload_delimiter", "upload_sheet",
            "selected_columns", "column_type_overrides", "cleaning_history", "step_history",
            "before_stats", "after_stats", "initial_stats", "cleaned_columns", "preview",
            "pending_category_columns", "history_stack", "audit_log", "owned_paths",
            "recipe_preview",
        ):
            session.pop(key, None)

    def _audit_event(self, event: str, **details) -> None:
        entry = {
            "event": event,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "operations": [],
            "step_summary": [],
            **details,
        }
        session.setdefault("audit_log", []).append(entry)

    def push_version(
        self,
        file_path: str,
        *,
        history: list[str],
        overrides: dict,
        rows_before: int,
        rows_after: int,
        operations: list,
        step_summary: list[str] | None = None,
        event: str = "clean",
        source_recipe: list[dict] | None = None,
    ) -> None:
        """Record a committed cleaning/narrowing run as an immutable version.

        The file keeps that run's exact typed values; the entry stores the
        ordered operations + parameters (the recipe), a timestamp, the
        impact counts and the restore state needed for undo.
        """
        entry = {
            "event": event,
            "file": str(file_path),
            "history": list(history),
            "column_type_overrides": dict(overrides),
            "selected_columns": session.get("selected_columns"),
            "rows_before": int(rows_before),
            "rows_after": int(rows_after),
            "operations": [dict(op) for op in operations],
            "step_summary": list(step_summary or []),
            "state": {
                key: session[key]
                for key in (
                    "before_stats", "after_stats", "initial_stats", "step_history",
                    "cleaned_columns", "pending_category_columns",
                )
                if key in session
            },
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if source_recipe is not None:
            entry["source_recipe"] = source_recipe
        session.setdefault("history_stack", []).append(entry)
        # Audit log survives undo/reset: it documents what was done to the data.
        session.setdefault("audit_log", []).append(dict(entry))
        self.register_file(file_path)

    def undo_latest(self) -> dict | None:
        """Restore the working dataset to the state before the latest run.

        Returns the undone entry, or None when there is nothing to undo.
        The discarded version file is deleted because this workflow has no redo.
        """
        stack = session.get("history_stack", [])
        if not stack:
            return None
        popped = stack.pop()
        session["history_stack"] = stack

        # There is no redo path, so an undone snapshot can be removed now.
        popped_path = popped.get("file")
        if popped_path:
            file_store.delete_file(popped_path)
            session["owned_paths"] = [
                path for path in session.get("owned_paths", []) if path != popped_path
            ]

        if stack:
            base = stack[-1]
            session["current_path"] = base["file"]
            session["processed_path"] = base["file"]
            session["column_type_overrides"] = base["column_type_overrides"]
            if base.get("selected_columns"):
                session["selected_columns"] = base["selected_columns"]
            else:
                session.pop("selected_columns", None)
            session["cleaning_history"] = list(base["history"])
            state = base.get("state", {})
            for key in (
                "before_stats", "after_stats", "initial_stats", "step_history",
                "cleaned_columns", "pending_category_columns",
            ):
                if key in state:
                    session[key] = state[key]
                else:
                    session.pop(key, None)
        else:
            self.reset(record_audit=False)
        session.pop("preview", None)
        session.pop("recipe_preview", None)
        self._audit_event(
            "undo",
            undone_event=popped.get("event"),
            undone_timestamp=popped.get("ts"),
        )
        return popped

    def start_new(
        self,
        source_path: str | Path,
        baseline_path: str | Path,
        filename: str,
        output_namespace: str,
        encoding: str | None = None,
        delimiter: str | None = None,
        sheet: str | None = None,
    ) -> None:
        """Register a fresh upload and discard any previous dataset's state."""
        for old_path in self._known_paths():
            file_store.delete_file(old_path)
        self._clear_workflow_state()
        # Sweep files abandoned by earlier sessions before adding new ones.
        protected = (source_path, baseline_path)
        file_store.sweep_stale_files(
            current_app.config["FILE_TTL_SECONDS"],
            protected_paths=protected,
        )
        session["output_namespace"] = output_namespace
        session["upload_path"] = str(source_path)
        session["baseline_path"] = str(baseline_path)
        session["original_filename"] = filename
        session["upload_encoding"] = encoding
        session["upload_delimiter"] = delimiter
        session["upload_sheet"] = sheet
        session["current_path"] = str(baseline_path)
        session["cleaning_history"] = []
        session["history_stack"] = []
        session["audit_log"] = []
        session["owned_paths"] = [str(source_path), str(baseline_path)]

    def reset(self, *, record_audit: bool = True) -> None:
        """Point the working dataset back at its validated baseline snapshot.

        Discards the undo stack (and its now-unreferenced version files);
        the audit log survives so the history of what was done stays readable.
        """
        source_path = session.get("upload_path")
        baseline_path = session.get("baseline_path") or source_path
        if not baseline_path:
            return
        preserved = {str(path) for path in (source_path, baseline_path) if path}
        for path in self._known_paths() - preserved:
            file_store.delete_file(path)
        session["owned_paths"] = list(preserved)
        session["current_path"] = baseline_path
        session.pop("processed_path", None)
        session.pop("excel_path", None)
        session.pop("csv_path", None)
        session.pop("selected_columns", None)
        session.pop("column_type_overrides", None)
        session.pop("step_history", None)
        session.pop("before_stats", None)
        session.pop("after_stats", None)
        session.pop("initial_stats", None)
        session.pop("cleaned_columns", None)
        session.pop("pending_category_columns", None)
        session.pop("preview", None)
        session.pop("recipe_preview", None)
        session["cleaning_history"] = []
        session["history_stack"] = []
        if record_audit:
            self._audit_event("reset")

    def end(self) -> None:
        """Delete files owned by the active session and clear its state."""
        for path in self._known_paths():
            file_store.delete_file(path)
        session.clear()


dataset_session = DatasetSession()
