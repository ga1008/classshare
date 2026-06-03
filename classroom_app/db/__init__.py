"""Database internals split from the legacy classroom_app.database facade."""

from .connection import get_db_connection
from .repair import repair_user_sessions_storage

__all__ = [
    "get_db_connection",
    "repair_user_sessions_storage",
]
