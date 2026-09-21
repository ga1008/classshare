"""Process-local dotenv guard for explicitly isolated test/fixture entrypoints."""
from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
from unittest.mock import patch


def _ignore_dotenv_load(*_args, **_kwargs) -> bool:
    return False


def guard_dotenv_loading(guards: ExitStack | None = None) -> None:
    """Block both loader aliases before application imports, including old dotenv.

    Without a stack, the guard lasts for this dedicated harness process. Explicit
    ``dotenv_values`` parsing remains available and does not mutate environment.
    """
    import dotenv
    import dotenv.main

    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    for module in (dotenv, dotenv.main):
        if guards is None:
            module.load_dotenv = _ignore_dotenv_load
        else:
            guards.enter_context(patch.object(module, "load_dotenv", _ignore_dotenv_load))


def isolate_sqlite_environment(runtime: Path) -> None:
    """Discard inherited database settings before app imports in test processes."""
    for name in list(os.environ):
        if (name.startswith(("PG", "POSTGRES_", "ASSESSMENT_REHEARSAL_TEST_", "MP_PHASE1_", "MAIN_"))
                or name.endswith("_TEST_DATABASE_URL")
                or name.startswith("RUN_LOCAL_PG_")
                or name in {"DATABASE_URL", "LANSHARE_DATA_ROOT"}):
            os.environ.pop(name, None)
    os.environ.update({
        "PYTHON_DOTENV_DISABLED": "1", "DB_ENGINE": "sqlite", "DATABASE_URL": "",
        "POSTGRES_BACKEND_READY": "false", "POSTGRES_POOL_ENABLED": "false",
        "LANSHARE_DATA_ROOT": str(runtime), "MAIN_DATA_DIR": str(runtime),
        "MAIN_DB_PATH": str(runtime / "db" / "classroom.db"), "PYTHONIOENCODING": "utf-8",
    })


def reject_postgres(*_args, **_kwargs):
    raise RuntimeError("Isolated SQLite runtime forbids real PostgreSQL connections")


async def reject_async_postgres(*_args, **_kwargs):
    reject_postgres()


def guard_postgres_connections(guards: ExitStack | None = None) -> None:
    """Block direct, pooled and async psycopg connections before application import."""
    try:
        import psycopg
    except ImportError:
        return
    for owner, replacement in ((psycopg, reject_postgres), (psycopg.Connection, reject_postgres),
                               (psycopg.AsyncConnection, reject_async_postgres)):
        if guards is None:
            owner.connect = replacement
        else:
            guards.enter_context(patch.object(owner, "connect", replacement))
