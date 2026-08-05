"""Signature identity categories (职务身份) and account<->signature sync.

A signature and the account it is bound to share one identity category
(校长/院长/系主任/教师/教务老师/辅导员...).  Whichever side is edited last
propagates to the other, and binding a signature to an account fills the
missing side automatically.
"""

from __future__ import annotations

from typing import Any


# Ordered: seniority first, then generic roles.  Keys are stored in DB.
IDENTITY_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("principal", "校长"),
    ("vice_principal", "副校长"),
    ("dean", "院长"),
    ("vice_dean", "副院长"),
    ("department_head", "系主任"),
    ("vice_department_head", "副系主任"),
    ("teacher", "教师"),
    ("academic_affairs", "教务老师"),
    ("counselor", "辅导员"),
    ("other", "其他"),
)

IDENTITY_LABELS: dict[str, str] = dict(IDENTITY_CATEGORIES)

# A signature point that needs a 系主任 also accepts 副系主任, etc.
IDENTITY_FILTER_GROUPS: dict[str, tuple[str, ...]] = {
    "principal": ("principal", "vice_principal"),
    "dean": ("dean", "vice_dean"),
    "department_head": ("department_head", "vice_department_head"),
}


def normalize_identity_category(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in IDENTITY_LABELS else ""


def identity_label(value: Any) -> str:
    return IDENTITY_LABELS.get(normalize_identity_category(value), "")


def identity_options() -> list[dict[str, str]]:
    return [{"key": key, "label": label} for key, label in IDENTITY_CATEGORIES]


def expand_identity_filter(value: Any) -> list[str]:
    """Expand one category key into the set accepted when filtering pickers."""
    key = normalize_identity_category(value)
    if not key:
        return []
    return list(IDENTITY_FILTER_GROUPS.get(key, (key,)))


def parse_required_identities(raw: Any) -> list[str]:
    """Parse the comma-separated required_identities column of a function point."""
    result: list[str] = []
    for chunk in str(raw or "").split(","):
        key = normalize_identity_category(chunk)
        if key and key not in result:
            result.append(key)
    return result


def expand_required_identities(raw: Any) -> list[str]:
    expanded: list[str] = []
    for key in parse_required_identities(raw):
        for item in expand_identity_filter(key):
            if item not in expanded:
                expanded.append(item)
    return expanded


def _account_table(role: str) -> str:
    normalized = str(role or "").strip().lower()
    if normalized == "teacher":
        return "teachers"
    if normalized == "student":
        return "students"
    return ""


def get_account_identity(conn: Any, role: str, user_id: Any) -> str:
    table = _account_table(role)
    try:
        normalized_id = int(user_id or 0)
    except (TypeError, ValueError):
        normalized_id = 0
    if not table or normalized_id <= 0:
        return ""
    row = conn.execute(
        f"SELECT identity_category FROM {table} WHERE id = ? LIMIT 1",
        (normalized_id,),
    ).fetchone()
    return normalize_identity_category(row["identity_category"] if row else "")


def set_account_identity(conn: Any, role: str, user_id: Any, identity: str) -> bool:
    table = _account_table(role)
    try:
        normalized_id = int(user_id or 0)
    except (TypeError, ValueError):
        normalized_id = 0
    normalized_identity = normalize_identity_category(identity)
    if not table or normalized_id <= 0:
        return False
    conn.execute(
        f"UPDATE {table} SET identity_category = ? WHERE id = ?",
        (normalized_identity, normalized_id),
    )
    return True


def sync_identity_for_signature(conn: Any, signature_id: int) -> dict[str, str]:
    """After a bind, fill whichever identity side is missing from the other."""
    row = conn.execute(
        """
        SELECT id, subject_role, subject_id, identity_category
        FROM electronic_signatures
        WHERE id = ? LIMIT 1
        """,
        (int(signature_id),),
    ).fetchone()
    if not row:
        return {}
    subject_role = str(row["subject_role"] or "").strip().lower()
    try:
        subject_id = int(row["subject_id"] or 0)
    except (TypeError, ValueError):
        subject_id = 0
    if subject_role not in {"teacher", "student"} or subject_id <= 0:
        return {}
    signature_identity = normalize_identity_category(row["identity_category"])
    account_identity = get_account_identity(conn, subject_role, subject_id)
    if signature_identity and not account_identity:
        set_account_identity(conn, subject_role, subject_id, signature_identity)
        return {"account": signature_identity}
    if account_identity and not signature_identity:
        conn.execute(
            "UPDATE electronic_signatures SET identity_category = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (account_identity, int(signature_id)),
        )
        return {"signature": account_identity}
    return {}


def propagate_account_identity(conn: Any, role: str, user_id: Any, identity: str) -> int:
    """Account identity was edited: push it to every signature bound to it."""
    table_role = str(role or "").strip().lower()
    try:
        normalized_id = int(user_id or 0)
    except (TypeError, ValueError):
        normalized_id = 0
    normalized_identity = normalize_identity_category(identity)
    if table_role not in {"teacher", "student"} or normalized_id <= 0:
        return 0
    cursor = conn.execute(
        """
        UPDATE electronic_signatures
        SET identity_category = ?, updated_at = CURRENT_TIMESTAMP
        WHERE subject_role = ? AND subject_id = ?
          AND status = 'active' AND deleted_at IS NULL
          AND COALESCE(identity_category, '') <> ?
        """,
        (normalized_identity, table_role, normalized_id, normalized_identity),
    )
    return int(cursor.rowcount or 0)
