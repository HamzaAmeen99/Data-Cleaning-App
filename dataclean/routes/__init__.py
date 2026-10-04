"""HTTP route blueprints for DataClean.

Routes only handle HTTP concerns (validating requests, calling services,
flashing messages, choosing responses). All data processing lives in services.
"""

from .upload_routes import upload_bp
from .dataset_routes import dataset_bp
from .cleaning_routes import cleaning_bp

__all__ = ["upload_bp", "dataset_bp", "cleaning_bp"]
