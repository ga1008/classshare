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

# Only these identities belong to a department; anyone above department
# level (dean, principal, 教务老师, 辅导员…) carries no department affiliation.
DEPARTMENT_SCOPED_IDENTITIES: frozenset[str] = frozenset(
    {"teacher", "department_head", "vice_department_head"}
)


def identity_requires_department(value: Any) -> bool:
    """Empty identity keeps whatever department it has; a set identity only
    keeps a department when it is department-scoped."""
    key = normalize_identity_category(value)
    return not key or key in DEPARTMENT_SCOPED_IDENTITIES


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
        # Self-service fill: identity arrives unverified until an admin confirms.
        conn.execute(
            """
            UPDATE electronic_signatures
            SET identity_category = ?, identity_verified = 0,
                department = CASE WHEN ? = 1 THEN department ELSE '' END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                account_identity,
                1 if identity_requires_department(account_identity) else 0,
                int(signature_id),
            ),
        )
        return {"signature": account_identity}
    return {}


_DATE_RE = None


def _normalize_term_date(value: Any) -> str:
    """Accept '' or 'YYYY-MM-DD'; anything else raises ValueError."""
    import re

    global _DATE_RE
    if _DATE_RE is None:
        _DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    text = str(value or "").strip()
    if not text:
        return ""
    if not _DATE_RE.match(text):
        raise ValueError(f"任期日期格式应为 YYYY-MM-DD：{text}")
    return text


def _today_text(today: Any = None) -> str:
    from datetime import date, datetime

    if isinstance(today, str) and today:
        return today
    if isinstance(today, datetime):
        return today.date().isoformat()
    if isinstance(today, date):
        return today.isoformat()
    return date.today().isoformat()


def _in_term(row: Any, today_text: str) -> bool:
    start = str(row["term_start"] or "")
    end = str(row["term_end"] or "")
    if start and start > today_text:
        return False
    if end and end < today_text:
        return False
    return True


def list_identity_appointments(conn: Any, role: str, user_id: Any) -> list[dict[str, Any]]:
    table_role = str(role or "").strip().lower()
    try:
        holder_id = int(user_id or 0)
    except (TypeError, ValueError):
        holder_id = 0
    if table_role not in {"teacher", "student"} or holder_id <= 0:
        return []
    rows = conn.execute(
        """
        SELECT id, identity_category, term_start, term_end, status
        FROM identity_appointments
        WHERE holder_role = ? AND holder_id = ?
        ORDER BY id
        """,
        (table_role, holder_id),
    ).fetchall()
    today = _today_text()
    ordered = {key: index for index, (key, _label) in enumerate(IDENTITY_CATEGORIES)}
    items = [
        {
            "id": int(row["id"]),
            "identity_category": row["identity_category"],
            "identity_label": identity_label(row["identity_category"]),
            "term_start": row["term_start"] or "",
            "term_end": row["term_end"] or "",
            "status": row["status"] or "active",
            "is_effective": (row["status"] or "active") == "active" and _in_term(row, today),
        }
        for row in rows
    ]
    return sorted(items, key=lambda item: ordered.get(item["identity_category"], 99))


def effective_identity_categories(
    conn: Any,
    role: str,
    user_id: Any,
    *,
    today: Any = None,
) -> list[str]:
    """Active, in-term identity categories; falls back to the legacy column
    when the holder has no appointment rows at all (pre-migration accounts)."""
    table_role = str(role or "").strip().lower()
    try:
        holder_id = int(user_id or 0)
    except (TypeError, ValueError):
        holder_id = 0
    if table_role not in {"teacher", "student"} or holder_id <= 0:
        return []
    rows = conn.execute(
        """
        SELECT identity_category, term_start, term_end, status
        FROM identity_appointments
        WHERE holder_role = ? AND holder_id = ?
        """,
        (table_role, holder_id),
    ).fetchall()
    today_text = _today_text(today)
    if not rows:
        legacy = get_account_identity(conn, table_role, holder_id)
        return [legacy] if legacy else []
    ordered = {key: index for index, (key, _label) in enumerate(IDENTITY_CATEGORIES)}
    effective = {
        normalize_identity_category(row["identity_category"])
        for row in rows
        if (row["status"] or "active") == "active" and _in_term(row, today_text)
    }
    effective.discard("")
    return sorted(effective, key=lambda key: ordered.get(key, 99))


def effective_identities_bulk(
    conn: Any,
    holders: list[tuple[str, int]],
    *,
    today: Any = None,
) -> dict[tuple[str, int], list[str]]:
    """Batch variant for pickers: one query for appointments, legacy fallback
    only for holders without any appointment rows."""
    normalized: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for role, user_id in holders:
        table_role = str(role or "").strip().lower()
        try:
            holder_id = int(user_id or 0)
        except (TypeError, ValueError):
            holder_id = 0
        key = (table_role, holder_id)
        if table_role in {"teacher", "student"} and holder_id > 0 and key not in seen:
            seen.add(key)
            normalized.append(key)
    if not normalized:
        return {}
    placeholders = " OR ".join("(holder_role = ? AND holder_id = ?)" for _ in normalized)
    params: list[Any] = []
    for role, holder_id in normalized:
        params.extend([role, holder_id])
    rows = conn.execute(
        f"""
        SELECT holder_role, holder_id, identity_category, term_start, term_end, status
        FROM identity_appointments
        WHERE {placeholders}
        """,
        tuple(params),
    ).fetchall()
    today_text = _today_text(today)
    has_rows: set[tuple[str, int]] = set()
    result: dict[tuple[str, int], set[str]] = {key: set() for key in normalized}
    for row in rows:
        key = (str(row["holder_role"]), int(row["holder_id"]))
        has_rows.add(key)
        if (row["status"] or "active") == "active" and _in_term(row, today_text):
            category = normalize_identity_category(row["identity_category"])
            if category:
                result.setdefault(key, set()).add(category)
    ordered = {key: index for index, (key, _label) in enumerate(IDENTITY_CATEGORIES)}
    output: dict[tuple[str, int], list[str]] = {}
    for key in normalized:
        if key not in has_rows:
            legacy = get_account_identity(conn, key[0], key[1])
            output[key] = [legacy] if legacy else []
        else:
            output[key] = sorted(result.get(key, set()), key=lambda item: ordered.get(item, 99))
    return output


def recompute_primary_identity(conn: Any, role: str, user_id: Any) -> str:
    """Primary identity = most senior effective appointment; keeps the legacy
    single-value column (and bound signatures) in sync with appointments."""
    effective = effective_identity_categories(conn, role, user_id)
    primary = effective[0] if effective else ""
    current = get_account_identity(conn, role, user_id)
    if primary != current:
        set_account_identity(conn, role, user_id, primary)
        propagate_account_identity(conn, role, user_id, primary)
    return primary


def set_identity_appointments(
    conn: Any,
    role: str,
    user_id: Any,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Replace-all write of a holder's appointments (max 4, deduped by category)."""
    table_role = str(role or "").strip().lower()
    try:
        holder_id = int(user_id or 0)
    except (TypeError, ValueError):
        holder_id = 0
    if table_role not in {"teacher", "student"} or holder_id <= 0:
        raise ValueError("任职身份只能设置在教师或学生账号上。")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        category = normalize_identity_category(item.get("identity_category"))
        if not category:
            raise ValueError("包含无法识别的身份类别。")
        if category in seen:
            raise ValueError(f"身份“{identity_label(category)}”重复出现。")
        seen.add(category)
        term_start = _normalize_term_date(item.get("term_start"))
        term_end = _normalize_term_date(item.get("term_end"))
        if term_start and term_end and term_start > term_end:
            raise ValueError("任期开始日期不能晚于结束日期。")
        normalized.append({"identity_category": category, "term_start": term_start, "term_end": term_end})
    if len(normalized) > 4:
        raise ValueError("一个账号最多登记 4 个任职身份。")
    conn.execute(
        "DELETE FROM identity_appointments WHERE holder_role = ? AND holder_id = ?",
        (table_role, holder_id),
    )
    for item in normalized:
        conn.execute(
            """
            INSERT INTO identity_appointments (
                holder_role, holder_id, identity_category, term_start, term_end, status
            ) VALUES (?, ?, ?, ?, ?, 'active')
            """,
            (table_role, holder_id, item["identity_category"], item["term_start"], item["term_end"]),
        )
    recompute_primary_identity(conn, table_role, holder_id)
    return list_identity_appointments(conn, table_role, holder_id)


def expire_identity_appointments(conn: Any, *, today: Any = None) -> int:
    """Mark overdue appointments expired and demote affected accounts."""
    today_text = _today_text(today)
    rows = conn.execute(
        """
        SELECT DISTINCT holder_role, holder_id FROM identity_appointments
        WHERE status = 'active' AND term_end <> '' AND term_end < ?
        """,
        (today_text,),
    ).fetchall()
    if not rows:
        return 0
    cursor = conn.execute(
        """
        UPDATE identity_appointments
        SET status = 'expired', updated_at = CURRENT_TIMESTAMP
        WHERE status = 'active' AND term_end <> '' AND term_end < ?
        """,
        (today_text,),
    )
    for row in rows:
        recompute_primary_identity(conn, str(row["holder_role"]), int(row["holder_id"]))
    return int(cursor.rowcount or 0)


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
        SET identity_category = ?, identity_verified = 0,
            department = CASE WHEN ? = 1 THEN department ELSE '' END,
            updated_at = CURRENT_TIMESTAMP
        WHERE subject_role = ? AND subject_id = ?
          AND status = 'active' AND deleted_at IS NULL
          AND COALESCE(identity_category, '') <> ?
        """,
        (
            normalized_identity,
            1 if identity_requires_department(normalized_identity) else 0,
            table_role,
            normalized_id,
            normalized_identity,
        ),
    )
    return int(cursor.rowcount or 0)
