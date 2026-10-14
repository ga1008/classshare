"""Registered approval request types. Import this package once at startup."""

from __future__ import annotations

from . import grading_appeal  # noqa: F401  (registers on import)
from . import late_submission  # noqa: F401  (registers on import)
from . import submission_correction  # noqa: F401  (registers on import)
from . import submission_withdraw  # noqa: F401  (registers on import; legacy, read-only)

__all__ = ["grading_appeal", "late_submission", "submission_correction", "submission_withdraw"]
