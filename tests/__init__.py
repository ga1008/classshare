"""Test package bootstrap.

The application now runs exclusively on PostgreSQL (``DB_ENGINE`` defaults to
``postgres`` and ``.env`` points at the local PostgreSQL instance). The unit
test suite, however, deliberately uses in-memory SQLite for fast, isolated,
side-effect-free tests — this is test scaffolding, not an application backend.

Forcing SQLite here (before any ``classroom_app`` import, so it wins over both
the config default and ``.env`` which is loaded with ``override=False``) keeps
the whole suite runnable with a plain ``python -m unittest discover -s tests``
— no more scattered ``DB_ENGINE=sqlite`` on every command line.
"""

import os

# Must run before classroom_app.config is imported by any test module.
os.environ["DB_ENGINE"] = "sqlite"
os.environ.pop("DATABASE_URL", None)
os.environ["POSTGRES_BACKEND_READY"] = "false"
