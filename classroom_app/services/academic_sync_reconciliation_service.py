from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import asdict
from datetime import timedelta
from typing import Any

import httpx

from ..database import get_db_connection
from ..db.connection import execute_insert_returning_id, get_configured_db_engine
from .academic_course_sync_service import (
    _course_description,
    _course_group_key,
    _derived_group_total_hours,
    _find_existing_course,
    _parse_total_hours,
    _parse_week_numbers,
    _upsert_courses_and_schedule_items,
    build_schedule_items_from_teaching_class_rosters,
    enrich_rosters_with_authoritative_course_data,
    infer_missing_course_metadata_with_ai,
)
from .academic_integration_service import load_teacher_academic_access_method, open_authenticated_academic_client
from .academic_roster_sync_service import (
    ACADEMIC_ROSTER_SOURCE,
    FOLLOW_UP_ITEMS,
    ZF_TEACHING_CLASS_LIST_PATH,
    AcademicRosterStudent,
    AcademicTeachingClassRoster,
    _class_names_from_composition,
    _fetch_all_rosters,
    _load_current_semester,
    _load_semester_by_id,
    _now_iso,
    _persist_rosters,
)
from .academic_service import china_now
from .department_service import infer_department_from_text, normalize_department
from .organization_scope_service import load_teacher_org_scope
from .semester_identity_service import identity_from_semester_record, zf_term_params_from_semester


SOURCE_SYSTEM = "gxufl_jwxt"
PLAN_TTL_HOURS = 2

COURSE_FIELD_LABELS = {
    "name": "课程名称",
    "academic_course_code": "真实课程编号",
    "department": "系别",
    "credits": "学分",
    "total_hours": "总学时",
    "description": "课程简介",
}
CLASS_FIELD_LABELS = {
    "name": "班级名称",
    "department": "系别",
    "academic_class_code": "行政班编号",
    "academic_class_name": "教务班级名称",
    "academic_college": "学院",
    "academic_grade": "年级",
    "academic_major": "专业",
}


def _text(value: Any) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split()).strip()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _loads(raw: Any, fallback: Any) -> Any:
    if isinstance(raw, type(fallback)):
        return raw
    try:
        value = json.loads(str(raw or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def _ensure_runtime_schema(conn: Any) -> None:
    serial = "SERIAL PRIMARY KEY" if get_configured_db_engine() == "postgres" else "INTEGER PRIMARY KEY AUTOINCREMENT"
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS teacher_academic_entity_bindings (
            id {serial},
            teacher_id INTEGER NOT NULL,
            semester_scope INTEGER NOT NULL DEFAULT 0,
            source_system TEXT NOT NULL DEFAULT '{SOURCE_SYSTEM}',
            entity_type TEXT NOT NULL,
            source_key TEXT NOT NULL,
            local_entity_id INTEGER NOT NULL,
            source_label TEXT NOT NULL DEFAULT '',
            aliases_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{{}}',
            binding_status TEXT NOT NULL DEFAULT 'active',
            confirmed_at TEXT,
            first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (teacher_id, semester_scope, source_system, entity_type, source_key)
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS teacher_academic_sync_plans (
            id {serial},
            teacher_id INTEGER NOT NULL,
            semester_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            source_fingerprint TEXT NOT NULL,
            snapshot_json TEXT NOT NULL DEFAULT '{{}}',
            preview_json TEXT NOT NULL DEFAULT '{{}}',
            resolution_json TEXT NOT NULL DEFAULT '{{}}',
            result_json TEXT NOT NULL DEFAULT '{{}}',
            expires_at TEXT NOT NULL,
            applied_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _source_keys_for_course(items: list[Any]) -> list[str]:
    keys: list[str] = []
    for item in items:
        if _text(item.course_code):
            keys.append(f"official:{_text(item.course_code).casefold()}")
        if _text(item.course_internal_id):
            keys.append(f"internal:{_text(item.course_internal_id).casefold()}")
        identity = item.raw_json.get("course_identity") if isinstance(item.raw_json, dict) else {}
        public_id = _text((identity or {}).get("public_course_record_id"))
        if public_id:
            keys.append(f"public:{public_id.casefold()}")
    return list(dict.fromkeys(keys))


def _roster_course_group_key(roster: AcademicTeachingClassRoster) -> str:
    if _text(roster.course_code):
        return f"code:{_text(roster.course_code).casefold()}"
    if _text(roster.course_internal_id):
        return f"unresolved-internal:{_text(roster.course_internal_id).casefold()}"
    return f"name:{_text(roster.course_name).casefold()}"


def _load_binding_target(
    conn: Any,
    *,
    teacher_id: int,
    semester_scope: int,
    entity_type: str,
    source_keys: list[str],
) -> tuple[int | None, str]:
    for source_key in source_keys:
        row = conn.execute(
            """
            SELECT local_entity_id
            FROM teacher_academic_entity_bindings
            WHERE teacher_id = ? AND semester_scope = ? AND source_system = ?
              AND entity_type = ? AND source_key = ? AND binding_status = 'active'
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(teacher_id), int(semester_scope), SOURCE_SYSTEM, entity_type, source_key),
        ).fetchone()
        if row:
            return int(row["local_entity_id"]), source_key
    return None, ""


def _offering_impacts(
    conn: Any,
    *,
    teacher_id: int,
    semester_id: int | None,
    field: str,
    entity_id: int,
) -> list[dict[str, Any]]:
    if field not in {"course_id", "class_id"}:
        return []
    semester_clause = "AND o.semester_id = ?" if semester_id else ""
    params: tuple[Any, ...] = (int(teacher_id), int(entity_id))
    if semester_id:
        params = (int(teacher_id), int(semester_id), int(entity_id))
    rows = conn.execute(
        f"""
        SELECT o.id, o.semester, o.semester_id, o.textbook_id,
               o.academic_teaching_class_id, o.academic_teaching_class_name,
               c.name AS course_name, cl.name AS class_name, t.title AS textbook_title,
               (SELECT COUNT(*) FROM class_offering_sessions s WHERE s.class_offering_id = o.id) AS session_count
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        LEFT JOIN textbooks t ON t.id = o.textbook_id
        WHERE o.teacher_id = ? {semester_clause} AND o.{field} = ?
        ORDER BY o.id
        """,
        params,
    ).fetchall()
    return [
        {
            "offering_id": int(row["id"]),
            "semester_id": int(row["semester_id"]) if row["semester_id"] else None,
            "semester_name": _text(row["semester"]),
            "course_name": _text(row["course_name"]),
            "class_name": _text(row["class_name"]),
            "textbook_id": int(row["textbook_id"]) if row["textbook_id"] else None,
            "textbook_title": _text(row["textbook_title"]),
            "session_count": int(row["session_count"] or 0),
            "academic_teaching_class_id": _text(row["academic_teaching_class_id"]),
            "academic_teaching_class_name": _text(row["academic_teaching_class_name"]),
        }
        for row in rows
    ]


def _field_diffs(labels: dict[str, str], local: dict[str, Any], remote: dict[str, Any], *, authoritative: bool) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    for field_name, label in labels.items():
        remote_value = remote.get(field_name)
        local_value = local.get(field_name)
        if remote_value in (None, ""):
            continue
        if str(remote_value).strip() == str(local_value if local_value is not None else "").strip():
            continue
        identity_field = field_name in {"name", "academic_course_code", "academic_class_code"}
        preserves_human_text = field_name in {"description", "department"} and local_value not in (None, "")
        diffs.append(
            {
                "name": field_name,
                "label": label,
                "local": local_value if local_value not in (None, "") else "（空）",
                "remote": remote_value,
                "default_remote": bool(
                    not preserves_human_text
                    and (authoritative or not identity_field or local_value in (None, ""))
                ),
                "identity_field": identity_field,
            }
        )
    return diffs


def _course_preview_items(conn: Any, *, teacher_id: int, semester: dict[str, Any], schedule_items: list[Any]) -> list[dict[str, Any]]:
    grouped: "OrderedDict[str, list[Any]]" = OrderedDict()
    for item in schedule_items:
        grouped.setdefault(_course_group_key(item), []).append(item)
    preview_items: list[dict[str, Any]] = []
    for group_key, items in grouped.items():
        first = items[0]
        source_keys = _source_keys_for_course(items)
        bound_id, binding_key = _load_binding_target(
            conn,
            teacher_id=teacher_id,
            semester_scope=0,
            entity_type="course",
            source_keys=source_keys,
        )
        existing = None
        match_mode = "new"
        ambiguous_count = 0
        if bound_id:
            row = conn.execute(
                "SELECT * FROM courses WHERE id = ? AND created_by_teacher_id = ?",
                (int(bound_id), int(teacher_id)),
            ).fetchone()
            if row:
                existing = dict(row)
                match_mode = "stable_binding"
        if existing is None:
            matched, match_mode, ambiguous_count = _find_existing_course(conn, teacher_id, first)
            existing = dict(matched) if matched else None

        candidate_rows: list[dict[str, Any]] = []
        if existing is None and (ambiguous_count or match_mode == "distinct_academic_code"):
            rows = conn.execute(
                "SELECT * FROM courses WHERE created_by_teacher_id = ? AND name = ? COLLATE NOCASE ORDER BY id DESC",
                (int(teacher_id), first.course_name),
            ).fetchall()
            candidate_rows = [dict(row) for row in rows]
            if len(candidate_rows) == 1:
                existing = candidate_rows[0]
                match_mode = "same_name_different_code"

        credits = next((float(item.credits or 0) for item in items if float(item.credits or 0) > 0), 0.0)
        total_hours = max(
            (_parse_total_hours(item.course_total_hours_text or item.total_hours_text or item.course_hour_text) for item in items),
            default=0,
        )
        if total_hours <= 0:
            total_hours = _derived_group_total_hours(items)
        department = normalize_department(
            infer_department_from_text(first.class_composition, first.course_name, first.raw_text)
        )
        remote = {
            "name": _text(first.course_name),
            "academic_course_code": _text(first.course_code),
            "department": department,
            "credits": credits,
            "total_hours": total_hours,
            "description": _course_description(first, len(items)),
        }
        local = existing or {}
        impacts = _offering_impacts(
            conn,
            teacher_id=teacher_id,
            semester_id=None,
            field="course_id",
            entity_id=int(local.get("id") or 0),
        ) if local else []
        authoritative = match_mode in {"academic_code", "stable_binding", "legacy_code_repair"}
        diffs = _field_diffs(COURSE_FIELD_LABELS, local, remote, authoritative=authoritative)
        has_identity_diff = any(field["identity_field"] for field in diffs)
        needs_confirmation = bool(
            existing
            and (
                match_mode in {"same_name_different_code", "ambiguous_name", "distinct_academic_code"}
                or (impacts and has_identity_diff)
            )
        )
        status = "new" if not existing else "conflict" if needs_confirmation else "update" if diffs else "unchanged"
        preview_items.append(
            {
                "key": f"course:{hashlib.sha1(group_key.encode('utf-8')).hexdigest()[:16]}",
                "source_group_key": group_key,
                "course_group_key": group_key,
                "entity_type": "course",
                "entity_label": "课程",
                "status": status,
                "title": remote["name"] or "未命名课程",
                "subtitle": remote["academic_course_code"] or "课程号待核验",
                "match_reason": match_mode,
                "source_keys": source_keys,
                "binding_key": binding_key,
                "local_id": int(local.get("id") or 0) or None,
                "local_label": _text(local.get("name")),
                "candidate_local_ids": [int(row["id"]) for row in candidate_rows],
                "recommended_action": "merge" if existing else "create",
                "allowed_actions": ["merge", "create", "skip"] if existing else ["create", "skip"],
                "fields": diffs,
                "requires_confirmation": needs_confirmation,
                "impacts": impacts,
                "impact_message": (
                    f"关联 {len(impacts)} 个既有课堂；将原地保留课程 ID，教材与课堂归属不会改变。"
                    if impacts else "尚未关联既有课堂。"
                ),
            }
        )
    return preview_items


def _class_groups(rosters: list[AcademicTeachingClassRoster]) -> OrderedDict[str, dict[str, Any]]:
    grouped: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for roster in rosters:
        course_group_key = _roster_course_group_key(roster)
        declared_names = _class_names_from_composition(roster.class_composition)
        for name in declared_names:
            group = grouped.setdefault(
                name,
                {
                    "name": name,
                    "codes": set(),
                    "students": set(),
                    "course_group_keys": set(),
                    "college": roster.college,
                    "grade": "",
                    "major": "",
                },
            )
            group["course_group_keys"].add(course_group_key)
        for student in roster.students:
            group = grouped.setdefault(
                student.class_name,
                {
                    "name": student.class_name,
                    "codes": set(),
                    "students": set(),
                    "course_group_keys": set(),
                    "college": student.college or roster.college,
                    "grade": student.grade,
                    "major": student.major,
                },
            )
            group["course_group_keys"].add(course_group_key)
            if student.class_code:
                group["codes"].add(student.class_code)
            if student.student_number:
                group["students"].add(student.student_number)
            group["college"] = group["college"] or student.college
            group["grade"] = group["grade"] or student.grade
            group["major"] = group["major"] or student.major
    return grouped


def _student_overlap_candidate(conn: Any, *, teacher_id: int, student_numbers: set[str]) -> tuple[dict[str, Any] | None, float]:
    if len(student_numbers) < 3:
        return None, 0.0
    placeholders = ",".join("?" for _ in student_numbers)
    rows = conn.execute(
        f"""
        SELECT c.*, COUNT(DISTINCT s.student_id_number) AS overlap_count
        FROM classes c
        JOIN students s ON s.class_id = c.id
        WHERE c.created_by_teacher_id = ? AND s.student_id_number IN ({placeholders})
        GROUP BY c.id
        ORDER BY overlap_count DESC, c.id DESC
        """,
        (int(teacher_id), *sorted(student_numbers)),
    ).fetchall()
    if not rows:
        return None, 0.0
    best = dict(rows[0])
    ratio = int(best.get("overlap_count") or 0) / max(1, len(student_numbers))
    if ratio < 0.65 or (len(rows) > 1 and int(rows[1]["overlap_count"] or 0) == int(best["overlap_count"] or 0)):
        return None, ratio
    return best, ratio


def _class_preview_items(conn: Any, *, teacher_id: int, semester: dict[str, Any], rosters: list[AcademicTeachingClassRoster]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for class_name, group in _class_groups(rosters).items():
        source_keys = [f"class:{_text(code).casefold()}" for code in sorted(group["codes"]) if _text(code)]
        bound_id, binding_key = _load_binding_target(
            conn,
            teacher_id=teacher_id,
            semester_scope=0,
            entity_type="class",
            source_keys=source_keys,
        )
        existing = None
        match_mode = "new"
        overlap_ratio = 0.0
        if bound_id:
            row = conn.execute("SELECT * FROM classes WHERE id = ?", (int(bound_id),)).fetchone()
            if row:
                existing, match_mode = dict(row), "stable_binding"
        if existing is None and source_keys:
            row = conn.execute(
                """
                SELECT * FROM classes
                WHERE academic_source = ? AND lower(academic_class_code) IN ({})
                ORDER BY CASE WHEN created_by_teacher_id = ? THEN 0 ELSE 1 END, id
                LIMIT 1
                """.format(",".join("?" for _ in source_keys)),
                (ACADEMIC_ROSTER_SOURCE, *[key.split(":", 1)[1] for key in source_keys], int(teacher_id)),
            ).fetchone()
            if row:
                existing, match_mode = dict(row), "academic_class_code"
        if existing is None:
            row = conn.execute(
                "SELECT * FROM classes WHERE lower(TRIM(name)) = lower(TRIM(?)) ORDER BY CASE WHEN created_by_teacher_id = ? THEN 0 ELSE 1 END, id LIMIT 1",
                (class_name, int(teacher_id)),
            ).fetchone()
            if row:
                existing, match_mode = dict(row), "exact_name"
        if existing is None:
            existing, overlap_ratio = _student_overlap_candidate(
                conn,
                teacher_id=teacher_id,
                student_numbers=set(group["students"]),
            )
            if existing:
                match_mode = "student_overlap"

        class_code = next(iter(sorted(group["codes"])), "")
        department = normalize_department(group["college"]) or normalize_department(group["major"])
        remote = {
            "name": class_name,
            "department": department,
            "academic_class_code": class_code,
            "academic_class_name": class_name,
            "academic_college": _text(group["college"]),
            "academic_grade": _text(group["grade"]),
            "academic_major": _text(group["major"]),
        }
        local = existing or {}
        impacts = _offering_impacts(
            conn,
            teacher_id=teacher_id,
            semester_id=None,
            field="class_id",
            entity_id=int(local.get("id") or 0),
        ) if local else []
        authoritative = match_mode in {"stable_binding", "academic_class_code"}
        diffs = _field_diffs(CLASS_FIELD_LABELS, local, remote, authoritative=authoritative)
        has_identity_diff = any(field["identity_field"] for field in diffs)
        needs_confirmation = bool(
            existing and (match_mode == "student_overlap" or (impacts and has_identity_diff))
        )
        status = "new" if not existing else "conflict" if needs_confirmation else "update" if diffs else "unchanged"
        items.append(
            {
                "key": f"class:{hashlib.sha1(class_name.casefold().encode('utf-8')).hexdigest()[:16]}",
                "source_group_key": class_name,
                "course_group_keys": sorted(group["course_group_keys"]),
                "entity_type": "class",
                "entity_label": "班级",
                "status": status,
                "title": class_name,
                "subtitle": class_code or f"{len(group['students'])} 名学生",
                "match_reason": match_mode,
                "source_keys": source_keys,
                "binding_key": binding_key,
                "local_id": int(local.get("id") or 0) or None,
                "local_label": _text(local.get("name")),
                "student_overlap_ratio": round(overlap_ratio, 3),
                "recommended_action": "merge" if existing else "create",
                "allowed_actions": (
                    ["merge", "create", "skip"]
                    if existing and _text(local.get("name")).casefold() != class_name.casefold()
                    else ["merge", "skip"]
                    if existing
                    else ["create", "skip"]
                ),
                "fields": diffs,
                "requires_confirmation": needs_confirmation,
                "impacts": impacts,
                "impact_message": (
                    f"关联 {len(impacts)} 个既有课堂；合并时沿用班级 ID，课程、教材和课堂历史不变。"
                    if impacts else "尚未关联既有课堂。"
                ),
            }
        )
    return items


def _linked_session_count(conn: Any, offering_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM class_offering_sessions WHERE class_offering_id = ? AND learning_material_id IS NOT NULL",
        (int(offering_id),),
    ).fetchone()
    count = int(row["count"] or 0) if row else 0
    for table in ("session_material_generation_tasks", "learning_material_progress", "smart_classroom_checkin_sessions"):
        try:
            linked = conn.execute(
                f"SELECT COUNT(*) AS count FROM {table} x JOIN class_offering_sessions s ON s.id = x.session_id WHERE s.class_offering_id = ?",
                (int(offering_id),),
            ).fetchone()
            count += int(linked["count"] or 0) if linked else 0
        except Exception:  # optional runtime table
            continue
    return count


def _offering_preview_items(conn: Any, *, semester: dict[str, Any], course_items: list[dict[str, Any]], schedule_items: list[Any]) -> list[dict[str, Any]]:
    grouped = {_course_group_key(item): [] for item in schedule_items}
    for item in schedule_items:
        grouped[_course_group_key(item)].append(item)
    results: list[dict[str, Any]] = []
    for course_item in course_items:
        course_id = int(course_item.get("local_id") or 0)
        if not course_id:
            continue
        incoming = grouped.get(course_item["source_group_key"]) or []
        offerings = _offering_impacts(
            conn,
            teacher_id=int(semester.get("teacher_id") or 0),
            semester_id=int(semester["id"]),
            field="course_id",
            entity_id=course_id,
        )
        for offering in offerings:
            preferred_id = _text(offering.get("academic_teaching_class_id"))
            preferred_name = _text(offering.get("academic_teaching_class_name"))
            candidates = [item for item in incoming if preferred_id and _text(item.teaching_class_id) == preferred_id]
            match_reason = "stable_teaching_class_id" if candidates else ""
            if not candidates and preferred_name:
                candidates = [item for item in incoming if _text(item.teaching_class_name) == preferred_name]
                match_reason = "teaching_class_name" if candidates else ""
            if not candidates and len({_text(item.teaching_class_id or item.teaching_class_name) for item in incoming}) == 1:
                candidates = incoming
                match_reason = "single_teaching_class"
            remote_count = sum(len(_parse_week_numbers(item.weeks_text)) for item in candidates)
            remote_teaching_name = _text(candidates[0].teaching_class_name) if candidates else ""
            current_count = int(offering.get("session_count") or 0)
            linked_count = _linked_session_count(conn, int(offering["offering_id"]))
            changed = bool(candidates and remote_count != current_count)
            identity_changed = bool(candidates and preferred_name and remote_teaching_name != preferred_name)
            ambiguous = not candidates and bool(incoming)
            if not changed and not identity_changed and not ambiguous and preferred_id:
                continue
            field_diffs = [
                {
                    "name": "schedule",
                    "label": "教务实际排课",
                    "local": f"当前 {current_count} 次课",
                    "remote": f"教务 {remote_count} 次课" if candidates else "无法唯一确定教学班",
                    "default_remote": bool(candidates),
                    "identity_field": False,
                }
            ]
            if identity_changed:
                field_diffs.insert(
                    0,
                    {
                        "name": "teaching_class_name",
                        "label": "教学班代号",
                        "local": preferred_name,
                        "remote": remote_teaching_name,
                        "default_remote": True,
                        "identity_field": True,
                    },
                )
            results.append(
                {
                    "key": f"offering:{int(offering['offering_id'])}",
                    "source_group_key": str(offering["offering_id"]),
                    "course_group_key": course_item["source_group_key"],
                    "entity_type": "offering",
                    "entity_label": "课堂排课",
                    "status": "conflict" if ambiguous or linked_count else "update",
                    "title": f"{offering['course_name']} · {offering['class_name']}",
                    "subtitle": f"课堂 #{offering['offering_id']} · 教材保持不变",
                    "match_reason": match_reason or "ambiguous_teaching_class",
                    "local_id": int(offering["offering_id"]),
                    "local_label": f"{current_count} 次课",
                    "recommended_action": "merge" if candidates else "skip",
                    "allowed_actions": ["merge", "skip"],
                    "fields": field_diffs,
                    "requires_confirmation": bool(ambiguous or linked_count or changed or identity_changed),
                    "impacts": [offering],
                    "impact_message": (
                        f"已有 {linked_count} 条课次材料、生成任务或学习记录；同步会保留原课次 ID，取消的课次仅标记停排。"
                        if linked_count else "课堂、课程、班级和教材 ID 均保持不变，仅对齐排课。"
                    ),
                }
            )
    return results


def build_academic_sync_preview(
    conn: Any,
    *,
    teacher_id: int,
    semester: dict[str, Any],
    rosters: list[AcademicTeachingClassRoster],
) -> dict[str, Any]:
    _ensure_runtime_schema(conn)
    semester = {**semester, "teacher_id": int(teacher_id)}
    schedule_items = build_schedule_items_from_teaching_class_rosters(
        rosters,
        source_url=ZF_TEACHING_CLASS_LIST_PATH,
    )
    course_items = _course_preview_items(
        conn,
        teacher_id=teacher_id,
        semester=semester,
        schedule_items=schedule_items,
    )
    class_items = _class_preview_items(
        conn,
        teacher_id=teacher_id,
        semester=semester,
        rosters=rosters,
    )
    offering_items = _offering_preview_items(
        conn,
        semester=semester,
        course_items=course_items,
        schedule_items=schedule_items,
    )
    items = [*course_items, *class_items, *offering_items]
    conflicts = [item for item in items if item.get("requires_confirmation")]
    return {
        "semester_id": int(semester["id"]),
        "semester_name": _text(semester.get("name")),
        "items": items,
        "summary": {
            "course_count": len(course_items),
            "class_count": len(class_items),
            "offering_count": len(offering_items),
            "new_count": sum(1 for item in items if item["status"] == "new"),
            "update_count": sum(1 for item in items if item["status"] == "update"),
            "conflict_count": len(conflicts),
            "unchanged_count": sum(1 for item in items if item["status"] == "unchanged"),
            "student_count": sum(len(roster.students) for roster in rosters),
        },
        "requires_confirmation": bool(conflicts),
        "safety": {
            "stable_ids": True,
            "textbook_policy": "preserve",
            "student_delete_policy": "mark_stale_only",
            "session_delete_policy": "preserve_linked_as_cancelled",
        },
    }


async def create_teacher_academic_sync_preview(teacher_id: int, semester_id: int) -> dict[str, Any]:
    with get_db_connection() as conn:
        _ensure_runtime_schema(conn)
        access_payload = load_teacher_academic_access_method(conn, teacher_id, school_code="gxufl")
        teacher_scope = load_teacher_org_scope(conn, teacher_id)
        semester = _load_semester_by_id(conn, teacher_id, int(semester_id))
    if not access_payload:
        return {"status": "missing_credential", "message": "请先配置并验证教务系统账号。"}
    if not semester:
        return {"status": "invalid_semester", "message": "所选学年学期不存在或不属于当前学校。"}

    try:
        async with open_authenticated_academic_client(access_payload) as (client, _profile, _login_result):
            rosters, source_summary = await _fetch_all_rosters(client, semester)
            identity_warnings: list[str] = []
            if rosters:
                sources, identity_warnings = await enrich_rosters_with_authoritative_course_data(
                    client,
                    semester,
                    rosters,
                    teacher_department=_text(teacher_scope.get("department")),
                )
                source_summary.extend(sources)
    except (ValueError, httpx.HTTPError) as exc:
        return {"status": "academic_login_failed", "message": f"教务系统登录或查询失败：{str(exc)[:180]}"}
    if not rosters:
        return {
            "status": "no_rosters",
            "message": "已登录教务系统，但所选学期没有查询到教学班与学生名单。",
            "semester_id": int(semester["id"]),
        }

    snapshot = {
        "semester": dict(semester),
        "rosters": [asdict(roster) for roster in rosters],
        "source_summary": source_summary,
        "identity_warnings": identity_warnings,
    }
    fingerprint = hashlib.sha256(_json(snapshot).encode("utf-8")).hexdigest()
    expires_at = (china_now().replace(tzinfo=None) + timedelta(hours=PLAN_TTL_HOURS)).isoformat(timespec="seconds")
    with get_db_connection() as conn:
        _ensure_runtime_schema(conn)
        preview = build_academic_sync_preview(
            conn,
            teacher_id=teacher_id,
            semester=dict(semester),
            rosters=rosters,
        )
        now = _now_iso()
        conn.execute(
            """
            UPDATE teacher_academic_sync_plans
            SET status = 'expired', snapshot_json = '{}', updated_at = ?
            WHERE teacher_id = ? AND status = 'pending' AND expires_at < ?
            """,
            (now, int(teacher_id), now),
        )
        conn.execute(
            """
            UPDATE teacher_academic_sync_plans
            SET status = 'superseded', snapshot_json = '{}', updated_at = ?
            WHERE teacher_id = ? AND semester_id = ? AND status = 'pending'
            """,
            (now, int(teacher_id), int(semester["id"])),
        )
        plan_id = execute_insert_returning_id(
            conn,
            """
            INSERT INTO teacher_academic_sync_plans (
                teacher_id, semester_id, status, source_fingerprint,
                snapshot_json, preview_json, expires_at, updated_at
            ) VALUES (?, ?, 'pending', ?, ?, ?, ?, ?)
            """,
            (
                int(teacher_id),
                int(semester["id"]),
                fingerprint,
                _json(snapshot),
                _json(preview),
                expires_at,
                now,
            ),
        )
        conn.commit()
    return {
        "status": "review_required" if preview["requires_confirmation"] else "ready",
        "message": "已完成教务数据与本地课堂链路对比，请确认本次合并方案。",
        "plan_id": plan_id,
        "expires_at": expires_at,
        "source_fingerprint": fingerprint,
        **preview,
        "warnings": identity_warnings,
    }


def _deserialize_rosters(raw_items: list[dict[str, Any]]) -> list[AcademicTeachingClassRoster]:
    rosters: list[AcademicTeachingClassRoster] = []
    for raw in raw_items:
        payload = dict(raw)
        students = [AcademicRosterStudent(**dict(item)) for item in (payload.pop("students", []) or [])]
        rosters.append(AcademicTeachingClassRoster(**payload, students=students))
    return rosters


def _resolved_item(item: dict[str, Any], overrides: dict[str, dict[str, Any]]) -> dict[str, Any]:
    override = overrides.get(str(item["key"])) or {}
    allowed_actions = set(item.get("allowed_actions") or [])
    action = str(override.get("action") or item.get("recommended_action") or "skip")
    if action not in allowed_actions:
        action = str(item.get("recommended_action") or "skip")
    available_fields = {str(field["name"]): field for field in item.get("fields") or []}
    field_choices = override.get("field_choices")
    if isinstance(field_choices, dict):
        remote_fields = [
            name
            for name in available_fields
            if str(field_choices.get(name) or "") == "remote"
        ]
    elif "remote_fields" in override:
        remote_fields = [str(name) for name in (override.get("remote_fields") or []) if str(name) in available_fields]
    else:
        remote_fields = [name for name, field in available_fields.items() if field.get("default_remote")]
    target_id = None if action == "create" else int(override.get("target_id") or item.get("local_id") or 0) or None
    return {"action": action, "target_id": target_id, "remote_fields": remote_fields}


def _resolution_errors(
    preview: dict[str, Any],
    overrides: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for item in preview.get("items") or []:
        if not item.get("requires_confirmation"):
            continue
        key = str(item.get("key") or "")
        override = overrides.get(key)
        allowed_actions = {str(action) for action in (item.get("allowed_actions") or [])}
        action = str((override or {}).get("action") or "")
        missing_fields: list[str] = []
        if not override or action not in allowed_actions:
            errors.append({"key": key, "title": item.get("title"), "missing_fields": []})
            continue
        if action == "merge":
            fields = [str(field.get("name") or "") for field in (item.get("fields") or [])]
            field_choices = override.get("field_choices")
            if isinstance(field_choices, dict):
                missing_fields = [
                    name for name in fields
                    if str(field_choices.get(name) or "") not in {"local", "remote"}
                ]
            elif "remote_fields" not in override:
                # Older clients express explicit local choices by omitting the
                # field from remote_fields. Presence of the list therefore
                # remains a compatible, explicit confirmation signal.
                missing_fields = fields
        if missing_fields:
            errors.append(
                {
                    "key": key,
                    "title": item.get("title"),
                    "missing_fields": missing_fields,
                }
            )
    return errors


def _local_preview_drift(conn: Any, preview: dict[str, Any]) -> list[str]:
    """Detect local edits made after the diff was generated.

    The apply step must never overwrite a teacher's newer edit using an older
    preview.  Only fields shown in the diff are compared because other fields
    are not candidates for replacement.
    """
    drift: list[str] = []
    entity_config = {
        "course": ("courses", set(COURSE_FIELD_LABELS)),
        "class": ("classes", set(CLASS_FIELD_LABELS)),
    }
    for item in preview.get("items") or []:
        entity_type = str(item.get("entity_type") or "")
        local_id = int(item.get("local_id") or 0)
        config = entity_config.get(entity_type)
        if not config or not local_id:
            continue
        table, allowed_fields = config
        row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (local_id,)).fetchone()
        if not row:
            drift.append(f"{item.get('entity_label') or entity_type}“{item.get('local_label') or local_id}”已被删除")
            continue
        current = dict(row)
        for field in item.get("fields") or []:
            field_name = str(field.get("name") or "")
            if field_name not in allowed_fields:
                continue
            expected = field.get("local")
            expected_text = "" if expected in (None, "", "（空）") else str(expected).strip()
            current_value = current.get(field_name)
            current_text = "" if current_value in (None, "") else str(current_value).strip()
            if current_text != expected_text:
                drift.append(
                    f"{item.get('entity_label') or entity_type}“{item.get('local_label') or local_id}”的"
                    f"{field.get('label') or field_name}已在预览后修改"
                )
    return drift


def _upsert_binding(
    conn: Any,
    *,
    teacher_id: int,
    semester_scope: int,
    entity_type: str,
    source_key: str,
    local_entity_id: int,
    source_label: str,
    confirmed: bool,
) -> None:
    if not source_key or not local_entity_id:
        return
    now = _now_iso()
    confirmed_at = now if confirmed else None
    conn.execute(
        """
        INSERT INTO teacher_academic_entity_bindings (
            teacher_id, semester_scope, source_system, entity_type, source_key,
            local_entity_id, source_label, binding_status, confirmed_at,
            first_seen_at, last_seen_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
        ON CONFLICT (teacher_id, semester_scope, source_system, entity_type, source_key)
        DO UPDATE SET local_entity_id = excluded.local_entity_id,
                      source_label = excluded.source_label,
                      binding_status = 'active',
                      confirmed_at = COALESCE(excluded.confirmed_at, teacher_academic_entity_bindings.confirmed_at),
                      last_seen_at = excluded.last_seen_at,
                      updated_at = excluded.updated_at
        """,
        (
            int(teacher_id), int(semester_scope), SOURCE_SYSTEM, entity_type, source_key,
            int(local_entity_id), source_label, confirmed_at, now, now, now,
        ),
    )


async def apply_teacher_academic_sync_plan(
    teacher_id: int,
    plan_id: int,
    resolution_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with get_db_connection() as conn:
        _ensure_runtime_schema(conn)
        row = conn.execute(
            "SELECT * FROM teacher_academic_sync_plans WHERE id = ? AND teacher_id = ?",
            (int(plan_id), int(teacher_id)),
        ).fetchone()
        if not row:
            return {"status": "invalid_plan", "message": "同步方案不存在或无权访问。"}
        plan_row = dict(row)
        now = _now_iso()
        if plan_row.get("status") != "pending":
            return {"status": "invalid_plan", "message": "该同步方案已处理，请重新生成差异预览。"}
        if _text(plan_row.get("expires_at")) < now:
            conn.execute(
                "UPDATE teacher_academic_sync_plans SET status = 'expired', snapshot_json = '{}', updated_at = ? WHERE id = ?",
                (now, int(plan_id)),
            )
            conn.commit()
            return {"status": "expired_plan", "message": "同步方案已过期，请重新读取教务数据。"}
        snapshot = _loads(plan_row.get("snapshot_json"), {})
        preview = _loads(plan_row.get("preview_json"), {})
        local_drift = _local_preview_drift(conn, preview)
        if local_drift:
            return {
                "status": "stale_plan",
                "message": "本地课程或班级在差异预览后已发生变化，请重新读取并比较，避免覆盖较新的人工修改。",
                "changes": local_drift[:8],
            }

    if not snapshot or not preview:
        return {"status": "invalid_plan", "message": "同步方案数据不完整，请重新生成。"}
    overrides = {
        str(item.get("key")): dict(item)
        for item in ((resolution_payload or {}).get("items") or [])
        if isinstance(item, dict) and item.get("key")
    }
    resolution_errors = _resolution_errors(preview, overrides)
    if resolution_errors:
        return {
            "status": "resolution_required",
            "message": "仍有同步差异尚未逐项确认，请选择保留本地值或采用教务值后再合并。",
            "unresolved_items": resolution_errors,
        }
    rosters = _deserialize_rosters(snapshot.get("rosters") or [])
    semester = dict(snapshot.get("semester") or {})
    schedule_items = build_schedule_items_from_teaching_class_rosters(
        rosters,
        source_url=ZF_TEACHING_CLASS_LIST_PATH,
    )
    ai_enrichment, ai_summary = await infer_missing_course_metadata_with_ai(schedule_items)
    course_decisions: dict[str, dict[str, Any]] = {}
    class_decisions: dict[str, dict[str, Any]] = {}
    skip_offering_ids: list[int] = []
    preserve_teaching_class_name_ids: list[int] = []
    resolved_items: list[dict[str, Any]] = []
    preview_by_key = {str(item["key"]): item for item in preview.get("items") or []}
    for item in preview_by_key.values():
        decision = _resolved_item(item, overrides)
        resolved_items.append({"key": item["key"], **decision})
        if item["entity_type"] == "course":
            course_decisions[item["source_group_key"]] = decision
        elif item["entity_type"] == "class":
            class_decisions[item["source_group_key"]] = decision
        elif item["entity_type"] == "offering":
            if decision["action"] == "skip" or "schedule" not in decision["remote_fields"]:
                skip_offering_ids.append(int(item["local_id"]))
            if any(field.get("name") == "teaching_class_name" for field in item.get("fields") or []) \
                    and "teaching_class_name" not in decision["remote_fields"]:
                preserve_teaching_class_name_ids.append(int(item["local_id"]))

    reconciliation = {
        "course_decisions": course_decisions,
        "class_decisions": class_decisions,
        "skip_offering_ids": skip_offering_ids,
        "preserve_teaching_class_name_ids": preserve_teaching_class_name_ids,
    }
    synced_at = _now_iso()
    with get_db_connection() as conn:
        _ensure_runtime_schema(conn)
        local_drift = _local_preview_drift(conn, preview)
        if local_drift:
            return {
                "status": "stale_plan",
                "message": "本地课程或班级在差异预览后已发生变化，请重新读取并比较，避免覆盖较新的人工修改。",
                "changes": local_drift[:8],
            }
        claim = conn.execute(
            """
            UPDATE teacher_academic_sync_plans
            SET status = 'applying', updated_at = ?
            WHERE id = ? AND teacher_id = ? AND status = 'pending' AND expires_at >= ?
            """,
            (synced_at, int(plan_id), int(teacher_id), synced_at),
        )
        if int(claim.rowcount or 0) != 1:
            conn.rollback()
            return {"status": "invalid_plan", "message": "该同步方案已由其他操作处理，请重新生成差异预览。"}
        conn.commit()
        try:
            course_result = _upsert_courses_and_schedule_items(
                conn,
                teacher_id=teacher_id,
                semester=semester,
                items=schedule_items,
                source_summary=snapshot.get("source_summary") or [],
                ai_enrichment=ai_enrichment,
                ai_enrichment_summary=ai_summary,
                reconciliation=reconciliation,
            )
            course_identity_map: dict[str, int] = {}
            course_result_by_group = {
                str(item.get("group_key")): item
                for item in course_result.get("courses") or []
                if item.get("course_id")
            }
            for group_key, result_item in course_result_by_group.items():
                course_id = int(result_item["course_id"])
                course_identity_map[f"code:{_text(result_item.get('course_code')).casefold()}"] = course_id
                course_identity_map[f"name:{_text(result_item.get('course_name')).casefold()}"] = course_id
                for internal_id in result_item.get("course_internal_ids") or []:
                    course_identity_map[f"internal:{_text(internal_id).casefold()}"] = course_id
                preview_item = next(
                    (item for item in preview.get("items") or [] if item.get("source_group_key") == group_key and item.get("entity_type") == "course"),
                    {},
                )
                decision = course_decisions.get(group_key) or {}
                for source_key in preview_item.get("source_keys") or []:
                    _upsert_binding(
                        conn,
                        teacher_id=teacher_id,
                        semester_scope=0,
                        entity_type="course",
                        source_key=source_key,
                        local_entity_id=course_id,
                        source_label=_text(result_item.get("course_name")),
                        confirmed=bool(preview_item.get("requires_confirmation") and decision.get("action") == "merge"),
                    )

            roster_result = _persist_rosters(
                conn,
                teacher_id=teacher_id,
                semester=semester,
                rosters=rosters,
                source_summary=snapshot.get("source_summary") or [],
                synced_at=synced_at,
                reconciliation=reconciliation,
                course_identity_map=course_identity_map,
            )
            for preview_item in preview.get("items") or []:
                if preview_item.get("entity_type") != "class":
                    continue
                class_id = int((roster_result.get("class_ids_by_name") or {}).get(preview_item.get("source_group_key")) or 0)
                if not class_id:
                    continue
                decision = class_decisions.get(preview_item["source_group_key"]) or {}
                for source_key in preview_item.get("source_keys") or []:
                    _upsert_binding(
                        conn,
                        teacher_id=teacher_id,
                        semester_scope=0,
                        entity_type="class",
                        source_key=source_key,
                        local_entity_id=class_id,
                        source_label=preview_item["title"],
                        confirmed=bool(preview_item.get("requires_confirmation") and decision.get("action") == "merge"),
                    )

            warnings = list(
                dict.fromkeys(
                    [
                        *(snapshot.get("identity_warnings") or []),
                        *(course_result.get("warnings") or []),
                        *(roster_result.get("warnings") or []),
                    ]
                )
            )
            result = {
                "status": "success",
                "message": (
                    f"已按确认方案同步 {course_result['course_count']} 门课程、"
                    f"{roster_result['touched_class_count']} 个班级和 {roster_result['roster_student_count']} 条学生关系；"
                    "既有课堂、课程、班级与教材主键均保持稳定。"
                ),
                "semester_id": int(semester["id"]),
                "semester_name": _text(semester.get("name")),
                "synced_at": synced_at,
                "created_count": course_result["created_count"],
                "updated_count": course_result["updated_count"],
                "courses_created": course_result["created_count"],
                "courses_updated": course_result["updated_count"],
                "course_count": course_result["course_count"],
                "schedule_item_count": course_result["schedule_item_count"],
                "occurrence_count": course_result.get("occurrence_count") or 0,
                "offering_update_count": course_result.get("offering_update_count") or 0,
                "courses": course_result.get("courses") or [],
                "unresolved_course_fields": course_result.get("unresolved_course_fields") or [],
                "ai_enrichment": course_result.get("ai_enrichment") or ai_summary,
                "classes_created": roster_result["classes_created"],
                "classes_updated": roster_result["classes_updated"],
                "classes_reused": roster_result.get("classes_reused") or 0,
                "students_created": roster_result["students_created"],
                "students_updated": roster_result["students_updated"],
                "students_reused": roster_result.get("students_reused") or 0,
                "students_moved": roster_result["students_moved"],
                "memberships_upserted": roster_result["memberships_upserted"],
                "teaching_class_count": roster_result["teaching_class_count"],
                "roster_student_count": roster_result["roster_student_count"],
                "touched_class_count": roster_result["touched_class_count"],
                "empty_teaching_class_count": roster_result.get("empty_teaching_class_count") or 0,
                "empty_teaching_classes": roster_result.get("empty_teaching_classes") or [],
                "class_mapping_count": roster_result["class_mapping_count"],
                "class_conflicts": roster_result["class_conflicts"],
                "student_conflicts": roster_result["student_conflicts"],
                "contact_conflicts": roster_result["contact_conflicts"],
                "stale_students": roster_result["stale_students"],
                "rosters": roster_result["rosters"],
                "warnings": warnings,
                "follow_up_items": [*warnings[:3], *FOLLOW_UP_ITEMS],
                "remaining_setup": ["选择教材", "复核未采用的本地/教务差异字段"],
                "integrity": {
                    "stable_course_ids": True,
                    "stable_class_ids": True,
                    "stable_offering_ids": True,
                    "textbooks_preserved": True,
                    "skipped_offering_ids": skip_offering_ids,
                },
            }
            conn.execute(
                """
                UPDATE teacher_academic_sync_plans
                SET status = 'applied', resolution_json = ?, result_json = ?,
                    snapshot_json = '{}', applied_at = ?, updated_at = ?
                WHERE id = ? AND teacher_id = ? AND status = 'applying'
                """,
                (_json({"items": resolved_items}), _json(result), synced_at, synced_at, int(plan_id), int(teacher_id)),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            conn.execute(
                """
                UPDATE teacher_academic_sync_plans
                SET status = 'pending', updated_at = ?
                WHERE id = ? AND teacher_id = ? AND status = 'applying'
                """,
                (_now_iso(), int(plan_id), int(teacher_id)),
            )
            conn.commit()
            raise
    return result


async def run_protected_teacher_academic_sync(teacher_id: int, semester_id: int) -> dict[str, Any]:
    preview = await create_teacher_academic_sync_preview(teacher_id, semester_id)
    if preview.get("status") not in {"ready", "review_required"}:
        return preview
    if preview.get("requires_confirmation"):
        return {
            **preview,
            "status": "conflict_required",
            "message": "检测到会影响既有课堂的课程、班级或排课差异，已暂停写入并等待教师确认。",
        }
    return await apply_teacher_academic_sync_plan(teacher_id, int(preview["plan_id"]), {})
