"""Single owner of the dataset-related keys kept in the Flask session."""

from flask import session


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
        session["cleaning_history"] = []


dataset_session = DatasetSession()
