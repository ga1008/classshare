"""Serialize complete PostgreSQL startup migrations across worker processes.

Initialization uses multiple transactions and connections. A transaction lock
on its first phase cannot protect later index/backfill phases. This dedicated
unpooled session owns no domain-table locks and retains one advisory lock across
every internal commit; physical close is the final release on any exception.
Ordinary requests and per-account search transactions never acquire this lock.
"""
from contextlib import contextmanager
import threading

from .connection import get_configured_db_engine
from .postgres import connect_postgres, sqlite_compatible_dict_row

STARTUP_LOCK_KEY = (0x4C534442, 1)  # LSDB; separate from every domain/migration key.
_thread_lock = threading.RLock()
_local = threading.local()


@contextmanager
def postgres_schema_startup_guard(connection_factory=None, timeout_ms=180000):
    if get_configured_db_engine() != 'postgres':
        yield
        return
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 1000 <= timeout_ms <= 300000:
        raise ValueError('Startup lock wait must be between 1000 and 300000 milliseconds')
    with _thread_lock:
        if getattr(_local, 'active', False):
            yield
            return
        # An explicit row factory selects the existing unpooled connection path;
        # a waiter must never occupy the only slot in a size-one business pool.
        conn = connection_factory() if connection_factory else connect_postgres(row_factory=sqlite_compatible_dict_row)
        acquired = False
        try:
            conn.execute("SELECT set_config('lock_timeout', ?, false), set_config('statement_timeout', ?, false)",
                         (f'{timeout_ms}ms', f'{timeout_ms + 1000}ms')).fetchone()
            conn.commit()
            conn.execute('SELECT pg_advisory_lock(?, ?)', STARTUP_LOCK_KEY).fetchone()
            acquired = True
            conn.commit()  # Do not retain a transaction while other connections initialize.
            _local.active = True
            yield
        finally:
            _local.active = False
            try:
                if acquired:
                    conn.rollback()
                    conn.execute('SELECT pg_advisory_unlock(?, ?)', STARTUP_LOCK_KEY).fetchone()
                    conn.commit()
            finally:
                conn.close()  # Always physical: never return a session lock to a pool.
