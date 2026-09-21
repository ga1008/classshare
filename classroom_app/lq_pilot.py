"""Default-off presentation flag for the explicitly selected S3 routes.

This does not grant access: each route retains its existing identity and resource
authorization. Queries, headers and browser preferences never enable the pilot.
"""
from __future__ import annotations

import os

from starlette.requests import Request


LQ_PILOT_PATHS = frozenset({
    "/manage/library/courses",
    "/manage/teaching/classes",
    "/manage/teaching/classroom-hub",
    "/manage/teaching/semesters",
    "/manage/library/textbooks",
    "/manage/library/lesson-plans",
    "/manage/library/materials",
    "/manage/system/users",
    "/report-card",
})


def is_lq_pilot_enabled(request: Request | str) -> bool:
    """Select presentation only for an exact canonical path and server opt-in."""
    path = request if isinstance(request, str) else request.url.path
    return (
        path in LQ_PILOT_PATHS
        and os.environ.get("LANSHARE_LQ_PILOT", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
