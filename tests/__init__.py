"""Test package bootstrap.

The application now runs exclusively on PostgreSQL (``DB_ENGINE`` defaults to
``postgres`` and ``.env`` points at the local PostgreSQL instance). The unit
test suite, however, deliberately uses in-memory SQLite for fast, isolated,
side-effect-free tests — this is test scaffolding, not an application backend.

Forcing SQLite here (before any ``classroom_app`` import, so it wins over both
the config default and ``.env`` which is loaded with ``override=False``) keeps
package-qualified test imports isolated. Plain discovery with ``-s tests``
can import modules before this package, so the full unit suite must be run
through ``python tools/test_backend.py``. That entry point also disables
dotenv, uses a temporary data root, and forbids real PostgreSQL connections.
"""

import os

# Must run before classroom_app.config is imported by any test module.
os.environ["DB_ENGINE"] = "sqlite"
os.environ.pop("DATABASE_URL", None)
os.environ["POSTGRES_BACKEND_READY"] = "false"
