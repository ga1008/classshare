"""Compatibility facade for the split LanShare database package.

Keep importing public database helpers from this module while implementation lives in
classroom_app.db.*. P02 refactors should not add new schema or route logic here.
"""

from . import config as _config
from .config import DB_PATH
from .db.connection import get_db_connection as _connection_get_db_connection
from .db.repair import repair_user_sessions_storage
from .db.schema import init_database as _schema_init_database
from .db.seeds import init_default_exam_paper as _init_default_exam_paper
from .db.sessions import (
    delete_user_sessions,
    get_user_session,
    list_user_session_roles,
    list_user_sessions,
    save_user_session,
)


_INITIAL_DB_PATH = DB_PATH


def _sync_legacy_db_path_override() -> None:
    global DB_PATH
    if DB_PATH == _INITIAL_DB_PATH and _config.DB_PATH != DB_PATH:
        DB_PATH = _config.DB_PATH
        return
    if DB_PATH != _config.DB_PATH:
        _config.DB_PATH = DB_PATH


def get_db_connection():
    _sync_legacy_db_path_override()
    return _connection_get_db_connection()


def init_database():
    _sync_legacy_db_path_override()
    return _schema_init_database()


__all__ = [
    "DB_PATH",
    "delete_user_sessions",
    "get_db_connection",
    "get_user_session",
    "init_database",
    "list_user_session_roles",
    "list_user_sessions",
    "repair_user_sessions_storage",
    "save_user_session",
]
