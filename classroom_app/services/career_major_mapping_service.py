"""School-scoped career aliases, referring to existing academic names.

These are internal career identities, never invented ministry major codes.
Only a controlled administrative import may change mappings. Student GETs read.
"""
from __future__ import annotations

from datetime import datetime, timezone
from .career_seed_data import normalize_major_key
from .career_recommendation_service import payload_hash


def resolve_career_major(conn, school_code: str, academic_name: str) -> dict:
    key = normalize_major_key(academic_name)
    row = conn.execute("SELECT canonical_key,canonical_name FROM career_major_aliases "
                       "WHERE school_code=? AND alias_key=?", (school_code, key)).fetchone()
    canonical = row["canonical_key"] if row else key
    return {"major_key": canonical, "major_name": row["canonical_name"] if row else academic_name,
            "academic_major_name": academic_name,
            "major_id": "career-major-" + payload_hash({"school": school_code, "key": canonical})[:24],
            "major_identity_source": "career_mapping" if row else "academic_name",
            "major_mapping_applied": bool(row)}


def set_career_major_alias(conn, *, school_code: str, alias_name: str, canonical_name: str, reason: str) -> dict:
    for label, value, limit in (("学校", school_code, 80), ("专业别名", alias_name, 160),
                                ("专业名称", canonical_name, 160), ("映射依据", reason, 500)):
        if not isinstance(value, str) or not value.strip() or len(value)>limit:
            raise ValueError(f"{label}不能为空或超长")
    canonical = resolve_career_major(conn, school_code, canonical_name)
    alias_key = normalize_major_key(alias_name)
    if alias_key == "unknown" or canonical["major_key"] == "unknown":
        raise ValueError("缺失的专业不能建立别名")
    # Do not silently abandon an already published network / questionnaire scope.
    existing = conn.execute("SELECT id FROM career_major_networks WHERE school_code=? AND major_key=?",
                            (school_code, alias_key)).fetchone()
    if not existing:
        existing = conn.execute("SELECT student_id FROM career_student_sessions WHERE school_code=? AND major_key=? LIMIT 1",
                                (school_code,alias_key)).fetchone()
    if existing and alias_key != canonical["major_key"]:
        raise ValueError("该别名已有职业网络，请先人工迁移当前版本和反馈，不能直接合并")
    conn.execute("""INSERT INTO career_major_aliases
        (school_code,alias_key,canonical_key,canonical_name,reason,updated_at) VALUES(?,?,?,?,?,?)
        ON CONFLICT(school_code,alias_key) DO UPDATE SET canonical_key=excluded.canonical_key,
        canonical_name=excluded.canonical_name,reason=excluded.reason,updated_at=excluded.updated_at""",
        (school_code,alias_key,canonical["major_key"],canonical["major_name"],reason.strip(),datetime.now(timezone.utc).isoformat()))
    return resolve_career_major(conn, school_code, alias_name)
