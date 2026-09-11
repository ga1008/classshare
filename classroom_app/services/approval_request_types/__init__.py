"""Registered approval request types. Import this package once at startup."""

from __future__ import annotations

from . import submission_withdraw  # noqa: F401  (registers on import)

__all__ = ["submission_withdraw"]
