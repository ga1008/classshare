"""Best-effort side effects inside a business transaction (audit B6).

A notification or learning-state refresh must never sink the submit or grade
that triggered it. Catching the Python exception is not enough on PostgreSQL:
the failed statement leaves the whole transaction aborted and the following
COMMIT silently rolls back. Running the hook inside a SAVEPOINT and rolling
back to it on failure keeps the outer transaction usable on both engines.

Hooks must not COMMIT (that would end the caller's transaction early). If one
does anyway, the savepoint is gone; that is logged instead of failing work that
is already committed.
"""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


def _savepoint_missing(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "no such savepoint" in text or ("savepoint" in text and "does not exist" in text)


@contextmanager
def best_effort(conn: Any, name: str, *, log_prefix: str = "[BEST_EFFORT]") -> Iterator[None]:
    """Run the block as an optional step; on any error undo only its own writes and log."""
    if not _NAME.match(name):
        raise ValueError("savepoint name must be a short lowercase identifier")
    if isinstance(conn, sqlite3.Connection) and not conn.in_transaction:
        # Outermost SAVEPOINT would open a transaction that RELEASE commits;
        # with nothing to protect, a plain guarded call is the correct form.
        try:
            yield
        except Exception as exc:  # noqa: BLE001 - optional step by definition
            print(f"{log_prefix} {name} failed: {exc!r}")
        return
    savepoint = f"lanshare_be_{name}"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        yield
    except Exception as exc:  # noqa: BLE001 - optional step by definition
        try:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception as rollback_exc:  # noqa: BLE001
            if not _savepoint_missing(rollback_exc):
                print(f"{log_prefix} {name}: savepoint rollback failed: {rollback_exc!r}")
                raise
            print(f"{log_prefix} {name}: hook committed before failing; nothing left to undo")
        print(f"{log_prefix} {name} failed: {exc!r}")
        return
    try:
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception as release_exc:  # noqa: BLE001
        if not _savepoint_missing(release_exc):
            raise
        print(f"{log_prefix} {name}: hook committed its own work (savepoint already released)")
