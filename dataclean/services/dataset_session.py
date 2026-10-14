"""Single owner of the dataset-related keys kept in the Flask session."""

from flask import session

from services import file_service
from services.cleaning_service import convert_column_type


class DatasetSession:
    """Reads and writes the dataset session keys so routes never duplicate them."""

    @property
    def upload_path(self) -> str | None:
        return session.get("upload_path")

    @property
    def working_path(self) -> str | None:
        return session.get("current_path") or session.get("upload_path")

    @property
    def original_filename(self) -> str:
        return session.get("original_filename", "dataset")

    @property
    def is_cleaned(self) -> bool:
        return bool(
            session.get("processed_path")
            or (
                session.get("current_path")
                and session.get("current_path") != session.get("upload_path")
            )
        )

    @property
    def column_type_overrides(self) -> dict[str, str]:
        return session.get("column_type_overrides", {})

    def apply_column_types(self, df):
        """Reapply user-selected types after loading an intermediate CSV."""
        converted = df
        for column, target_type in self.column_type_overrides.items():
            converted, _ = convert_column_type(converted, column, target_type)
        return converted

    def start_new(self, path: str, filename: str) -> None:
        """Register a fresh upload and discard any previous dataset's state."""
        session["upload_path"] = str(path)
        session["original_filename"] = filename
        self.reset()

    def reset(self) -> None:
        """Point the working dataset back at the original upload."""
        session["current_path"] = session["upload_path"]
        session.pop("processed_path", None)
        session.pop("selected_columns", None)
        session.pop("column_type_overrides", None)
        session["cleaning_history"] = []

    def end(self) -> None:
        """Delete files owned by the active session and clear its state."""
        paths = {
            session.get("upload_path"),
            session.get("current_path"),
            session.get("processed_path"),
            session.get("excel_path"),
        }
        for path in paths:
            if path:
                file_service.delete_file(path)
        session.clear()


dataset_session = DatasetSession()
