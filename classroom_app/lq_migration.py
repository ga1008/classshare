"""Server-controlled, default-off presentation switches for S4 page families.

These switches select templates only; they do not grant access or change the
independent S3 exact-route pilot. No request or client preference is consulted.
"""
from __future__ import annotations

import os


LQ_MIGRATION_FAMILIES = frozenset({
    "manage-shell", "navbar-shell", "dashboard", "calendar", "profile",
    "messages", "centered", "growth", "manage-pages",
})


def lq_family_enabled(family: str) -> bool:
    """Require an exact known family in the comma-separated server allowlist.

    Unknown values, booleans and wildcards never enable a family. Read on use
    so a process-local flag change can roll presentation back without cached
    template state. A deployed environment change still requires its normal
    process restart.
    """
    return (
        family in LQ_MIGRATION_FAMILIES
        and family in {value.strip() for value in os.environ.get("LANSHARE_LQ_FAMILIES", "").split(",")}
    )
