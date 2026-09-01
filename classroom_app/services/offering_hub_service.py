"""课堂运行总台（offering hub）数据聚合。

「课堂」管理页的唯一数据源：在 ``_load_teacher_offering_rows`` 基础行上补齐
运营维度（学生数、课次进度、下次课、AI 配置、活动资产），并产出页面级
统计（信息归集卡片）、待办清单、本周课次日程与一键开课摘要。

学生/班级解析一律走 offering_membership_service 的 SQL 片段，禁止绕过
membership link 的裸主班级等值 join（守卫单测会扫源码）。
"""

from datetime import date, timedelta
from typing import Any

from .academic_service import china_today
from .offering_bootstrap_service import build_offering_bootstrap_candidates
from .offering_membership_service import offering_student_where

WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

RUN_STATUS_LABELS = {
    "unscheduled": "未排课",
    "upcoming": "未开始",
    "active": "进行中",
    "finished": "课次已结束",
}
RUN_STATUS_TONES = {
    "unscheduled": "warning",
    "upcoming": "info",
    "active": "success",
    "finished": "muted",
}

_ACTIVE_SESSION_CLAUSE = "lower(TRIM(COALESCE(schedule_status, 'scheduled'))) NOT IN ('cancelled', 'canceled')"


def _placeholders(items: list[int]) -> str:
    return ",".join("?" for _ in items)


def _safe_date(value: Any) -> date | None:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _relative_day_label(target: date, today: date) -> str:
    delta = (target - today).days
    if delta == 0:
        return "今天"
    if delta == 1:
        return "明天"
    if delta == 2:
        return "后天"
    if delta < 0:
        return f"{-delta} 天前"
    return f"{delta} 天后"


def _weekday_label(value: date) -> str:
    return WEEKDAY_LABELS[value.isoweekday() - 1]


def _student_counts(conn: Any, teacher_id: int) -> dict[int, int]:
    rows = conn.execute(
        f"""
        SELECT o.id AS offering_id, COUNT(DISTINCT s.id) AS n
        FROM class_offerings o
        JOIN students s
          ON {offering_student_where(offering_alias="o", student_alias="s")}
         AND COALESCE(s.enrollment_status, 'active') = 'active'
        WHERE o.teacher_id = ?
        GROUP BY o.id
        """,
        (int(teacher_id),),
    ).fetchall()
    return {int(row["offering_id"]): int(row["n"] or 0) for row in rows}


def _session_progress(conn: Any, offering_ids: list[int], today_iso: str) -> dict[int, dict[str, int]]:
    if not offering_ids:
        return {}
    rows = conn.execute(
        f"""
        SELECT class_offering_id,
               SUM(CASE WHEN {_ACTIVE_SESSION_CLAUSE} THEN 1 ELSE 0 END) AS total,
               SUM(CASE WHEN {_ACTIVE_SESSION_CLAUSE} AND session_date < ? THEN 1 ELSE 0 END) AS done
        FROM class_offering_sessions
        WHERE class_offering_id IN ({_placeholders(offering_ids)})
        GROUP BY class_offering_id
        """,
        (today_iso, *offering_ids),
    ).fetchall()
    return {
        int(row["class_offering_id"]): {
            "total": int(row["total"] or 0),
            "done": int(row["done"] or 0),
        }
        for row in rows
    }


def _next_sessions(conn: Any, offering_ids: list[int], today_iso: str) -> dict[int, dict[str, Any]]:
    if not offering_ids:
        return {}
    rows = conn.execute(
        f"""
        SELECT class_offering_id, session_date, title, order_index,
               academic_section_text, academic_location
        FROM class_offering_sessions
        WHERE class_offering_id IN ({_placeholders(offering_ids)})
          AND session_date >= ?
          AND {_ACTIVE_SESSION_CLAUSE}
        ORDER BY session_date, order_index
        """,
        (*offering_ids, today_iso),
    ).fetchall()
    next_map: dict[int, dict[str, Any]] = {}
    today = date.fromisoformat(today_iso)
    for row in rows:
        offering_id = int(row["class_offering_id"])
        if offering_id in next_map:
            continue
        session_date = _safe_date(row["session_date"])
        if session_date is None:
            continue
        next_map[offering_id] = {
            "date": session_date.isoformat(),
            "weekday_label": _weekday_label(session_date),
            "relative_label": _relative_day_label(session_date, today),
            "title": str(row["title"] or ""),
            "order_index": int(row["order_index"] or 0),
            "section_text": str(row["academic_section_text"] or ""),
            "location": str(row["academic_location"] or ""),
        }
    return next_map


def _ai_configured_ids(conn: Any, offering_ids: list[int]) -> set[int]:
    if not offering_ids:
        return set()
    rows = conn.execute(
        f"""
        SELECT class_offering_id
        FROM ai_class_configs
        WHERE class_offering_id IN ({_placeholders(offering_ids)})
          AND (TRIM(COALESCE(system_prompt, '')) != '' OR TRIM(COALESCE(syllabus, '')) != '')
        """,
        tuple(offering_ids),
    ).fetchall()
    return {int(row["class_offering_id"]) for row in rows}


def _lessondoc_pack_map(conn: Any, offering_ids: list[int]) -> dict[int, dict[str, Any]]:
    """课堂 → 已绑定的 LessonDoc 学习文档包（一次 SQL，避免逐课堂上溯）。

    判定口径与 ``pack_service.find_pack_for_offering`` 一致但走批量 join：
    课堂首页/课次的主材料只要落在某个 active pack 的子树内即算已绑定
    （``course_materials.root_id`` 对包根成立，因为建包时整棵树同根）。
    任何异常都降级为「全部未绑定」——运营总台不能因为附加信息失败而打不开。
    """
    if not offering_ids:
        return {}
    try:
        from ..db.schema_course_doc_packs import ensure_course_doc_pack_schema

        ensure_course_doc_pack_schema(conn)
        rows = conn.execute(
            f"""
            SELECT o.id AS offering_id,
                   p.id AS pack_id,
                   p.root_material_id,
                   p.theme,
                   SUM(CASE WHEN l.gen_status IS NOT NULL AND l.gen_status != 'excluded'
                            THEN 1 ELSE 0 END) AS total_count,
                   SUM(CASE WHEN l.gen_status = 'ready' THEN 1 ELSE 0 END) AS ready_count
            FROM class_offerings o
            JOIN course_materials m
              ON m.id = COALESCE(
                   o.home_learning_material_id,
                   (SELECT s.learning_material_id
                      FROM class_offering_sessions s
                     WHERE s.class_offering_id = o.id
                       AND s.learning_material_id IS NOT NULL
                     ORDER BY s.order_index LIMIT 1)
                 )
            JOIN course_doc_packs p
              ON p.root_material_id = m.root_id AND p.status = 'active'
            LEFT JOIN course_doc_pack_lessons l ON l.pack_id = p.id
            WHERE o.id IN ({_placeholders(offering_ids)})
            GROUP BY o.id, p.id, p.root_material_id, p.theme
            """,
            tuple(offering_ids),
        ).fetchall()
    except Exception:
        return {}
    return {
        int(row["offering_id"]): {
            "pack_id": int(row["pack_id"]),
            "root_material_id": int(row["root_material_id"]),
            "theme": row["theme"],
            "ready_count": int(row["ready_count"] or 0),
            "total_count": int(row["total_count"] or 0),
            "render_shell_url": f"/materials/render-view/{int(row['root_material_id'])}",
        }
        for row in rows
    }


def _activity_counts(conn: Any, offering_ids: list[int]) -> dict[int, dict[str, int]]:
    if not offering_ids:
        return {}
    counts: dict[int, dict[str, int]] = {
        offering_id: {
            "assignment_total": 0,
            "assignment_open": 0,
            "exam_total": 0,
            "poll_active": 0,
            "group_active": 0,
        }
        for offering_id in offering_ids
    }
    placeholders = _placeholders(offering_ids)
    for row in conn.execute(
        f"""
        SELECT class_offering_id,
               COUNT(*) AS total,
               SUM(CASE WHEN status = 'published' THEN 1 ELSE 0 END) AS open_count,
               SUM(CASE WHEN exam_paper_id IS NOT NULL THEN 1 ELSE 0 END) AS exam_count
        FROM assignments
        WHERE class_offering_id IN ({placeholders})
        GROUP BY class_offering_id
        """,
        tuple(offering_ids),
    ).fetchall():
        entry = counts[int(row["class_offering_id"])]
        entry["assignment_total"] = int(row["total"] or 0)
        entry["assignment_open"] = int(row["open_count"] or 0)
        entry["exam_total"] = int(row["exam_count"] or 0)
    # polls / group_schemes 是 runtime-ensured 表，缺表时降级为 0 而非整页失败。
    try:
        for row in conn.execute(
            f"""
            SELECT pa.class_offering_id, COUNT(DISTINCT p.id) AS n
            FROM poll_assignments pa
            JOIN polls p ON p.id = pa.poll_id
            WHERE pa.class_offering_id IN ({placeholders})
              AND p.status IN ('draft', 'active')
            GROUP BY pa.class_offering_id
            """,
            tuple(offering_ids),
        ).fetchall():
            counts[int(row["class_offering_id"])]["poll_active"] = int(row["n"] or 0)
    except Exception:
        pass
    try:
        for row in conn.execute(
            f"""
            SELECT class_offering_id, COUNT(*) AS n
            FROM group_schemes
            WHERE class_offering_id IN ({placeholders})
              AND status = 'active'
            GROUP BY class_offering_id
            """,
            tuple(offering_ids),
        ).fetchall():
            counts[int(row["class_offering_id"])]["group_active"] = int(row["n"] or 0)
    except Exception:
        pass
    return counts


def _class_name_map(conn: Any, class_ids: list[int]) -> dict[int, str]:
    if not class_ids:
        return {}
    rows = conn.execute(
        f"SELECT id, name FROM classes WHERE id IN ({_placeholders(class_ids)})",
        tuple(class_ids),
    ).fetchall()
    return {int(row["id"]): str(row["name"] or "") for row in rows}


def _run_status(progress: dict[str, int]) -> str:
    total = int(progress.get("total") or 0)
    done = int(progress.get("done") or 0)
    if total <= 0:
        return "unscheduled"
    if done <= 0:
        return "upcoming"
    if done >= total:
        return "finished"
    return "active"


def enrich_offerings_for_hub(conn: Any, teacher_id: int, offerings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """在基础课堂行上补齐运营维度；返回新的字典列表（不改入参）。"""
    offering_ids = [int(item["id"]) for item in offerings]
    today = china_today()
    today_iso = today.isoformat()

    student_counts = _student_counts(conn, teacher_id)
    progress_map = _session_progress(conn, offering_ids, today_iso)
    next_map = _next_sessions(conn, offering_ids, today_iso)
    ai_ids = _ai_configured_ids(conn, offering_ids)
    activity_map = _activity_counts(conn, offering_ids)
    doc_pack_map = _lessondoc_pack_map(conn, offering_ids)
    all_class_ids = sorted({cid for item in offerings for cid in (item.get("class_ids") or [])})
    class_names = _class_name_map(conn, all_class_ids)

    enriched: list[dict[str, Any]] = []
    for item in offerings:
        offering_id = int(item["id"])
        progress = progress_map.get(offering_id, {"total": 0, "done": 0})
        status = _run_status(progress)
        total = progress["total"]
        done = progress["done"]
        activity = activity_map.get(
            offering_id,
            {"assignment_total": 0, "assignment_open": 0, "exam_total": 0, "poll_active": 0, "group_active": 0},
        )
        has_textbook = bool(item.get("textbook_id"))
        has_ai_config = offering_id in ai_ids
        missing: list[str] = []
        if not has_textbook:
            missing.append("textbook")
        if not has_ai_config:
            missing.append("ai")
        if total <= 0:
            missing.append("sessions")
        linked_names = [class_names.get(cid, "") for cid in (item.get("class_ids") or []) if class_names.get(cid)]
        enriched.append(
            {
                **item,
                "student_count": student_counts.get(offering_id, 0),
                "class_count": len(item.get("class_ids") or []) or 1,
                "linked_class_names": linked_names,
                "session_total": total,
                "session_done": done,
                "session_percent": round(done / total * 100) if total else 0,
                "next_session": next_map.get(offering_id),
                "has_textbook": has_textbook,
                "has_ai_config": has_ai_config,
                "config_missing": missing,
                "is_config_complete": not missing,
                "run_status": status,
                "run_status_label": RUN_STATUS_LABELS[status],
                "run_status_tone": RUN_STATUS_TONES[status],
                "activity": activity,
                "activity_open_total": activity["assignment_open"] + activity["poll_active"] + activity["group_active"],
                "lessondoc_pack": doc_pack_map.get(offering_id),
            }
        )
    return enriched


def _matches_semester(item: dict[str, Any], semester_id: int | None, semester_name: str) -> bool:
    if semester_id is not None and item.get("semester_id") is not None:
        return int(item["semester_id"]) == int(semester_id)
    if semester_name:
        return str(item.get("semester") or "").strip() == semester_name
    return False


def _week_agenda(conn: Any, teacher_id: int, offerings_by_id: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    today = china_today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    rows = conn.execute(
        f"""
        SELECT os.class_offering_id, os.session_date, os.title, os.order_index,
               os.academic_section_text, os.academic_location
        FROM class_offering_sessions os
        JOIN class_offerings o ON o.id = os.class_offering_id
        WHERE o.teacher_id = ?
          AND os.session_date BETWEEN ? AND ?
          AND {_ACTIVE_SESSION_CLAUSE}
        ORDER BY os.session_date, os.academic_section_text, os.order_index
        """,
        (int(teacher_id), monday.isoformat(), sunday.isoformat()),
    ).fetchall()
    agenda: list[dict[str, Any]] = []
    for row in rows:
        offering = offerings_by_id.get(int(row["class_offering_id"]))
        if offering is None:
            continue
        session_date = _safe_date(row["session_date"])
        if session_date is None:
            continue
        agenda.append(
            {
                "offering_id": int(row["class_offering_id"]),
                "date": session_date.isoformat(),
                "weekday_label": _weekday_label(session_date),
                "is_today": session_date == today,
                "is_past": session_date < today,
                "title": str(row["title"] or ""),
                "section_text": str(row["academic_section_text"] or ""),
                "location": str(row["academic_location"] or ""),
                "course_name": str(offering.get("course_name") or ""),
                "class_name": str(offering.get("class_name") or ""),
            }
        )
    return agenda


def _bootstrap_snapshot(conn: Any, teacher_id: int, semester_id: int | None) -> dict[str, Any] | None:
    if not semester_id:
        return None
    try:
        result = build_offering_bootstrap_candidates(
            conn, teacher_id=int(teacher_id), semester_id=int(semester_id)
        )
    except Exception:
        return None
    candidates = result.get("candidates") or []
    if not candidates:
        return None
    return {
        "summary": result.get("summary") or {},
        "top_candidates": [
            {
                "course_name": str(item.get("course_name") or ""),
                "class_names": list(item.get("class_names") or []),
                "student_count": int(item.get("student_count") or 0),
            }
            for item in candidates[:3]
        ],
    }


def build_offering_hub_context(
    conn: Any,
    teacher_id: int,
    offerings: list[dict[str, Any]],
    semesters: list[dict[str, Any]],
    default_semester_id: int | None,
) -> dict[str, Any]:
    enriched = enrich_offerings_for_hub(conn, teacher_id, offerings)
    offerings_by_id = {int(item["id"]): item for item in enriched}

    default_semester_name = ""
    for semester in semesters:
        if semester.get("id") is not None and default_semester_id is not None:
            if int(semester["id"]) == int(default_semester_id):
                default_semester_name = str(semester.get("name") or "").strip()
                break

    current = [
        item for item in enriched
        if _matches_semester(item, default_semester_id, default_semester_name)
    ]
    if not current:
        current = enriched

    class_id_union = {cid for item in current for cid in (item.get("class_ids") or [])}
    session_total = sum(item["session_total"] for item in current)
    session_done = sum(item["session_done"] for item in current)
    week_agenda = _week_agenda(conn, teacher_id, offerings_by_id)

    course_counter: dict[str, int] = {}
    for item in current:
        name = str(item.get("course_name") or "未命名课程")
        course_counter[name] = course_counter.get(name, 0) + 1
    course_distribution = [
        {"label": name, "value": count}
        for name, count in sorted(course_counter.items(), key=lambda pair: (-pair[1], pair[0]))
    ]

    todo = {
        "missing_textbook": sum(1 for item in current if not item["has_textbook"]),
        "missing_ai": sum(1 for item in current if not item["has_ai_config"]),
        "unscheduled": sum(1 for item in current if item["run_status"] == "unscheduled"),
        "finished": sum(1 for item in current if item["run_status"] == "finished"),
    }

    semester_options: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for item in enriched:
        key = f"{item.get('semester_id') or ''}|{item.get('semester') or ''}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        semester_options.append(
            {
                "value": str(item.get("semester_id") or "") or str(item.get("semester") or ""),
                "label": str(item.get("semester") or "未标注学期"),
                "is_default": _matches_semester(item, default_semester_id, default_semester_name),
            }
        )

    return {
        "hub_offerings": enriched,
        "hub_stats": {
            "current_offering_count": len(current),
            "current_class_count": len(class_id_union),
            "current_student_count": sum(item["student_count"] for item in current),
            "config_complete_count": sum(1 for item in current if item["is_config_complete"]),
            "session_total": session_total,
            "session_done": session_done,
            "week_session_count": len(week_agenda),
            "total_offering_count": len(enriched),
        },
        "hub_course_distribution": course_distribution,
        "hub_todo": todo,
        "hub_week_agenda": week_agenda,
        "hub_bootstrap": _bootstrap_snapshot(conn, teacher_id, default_semester_id),
        "hub_semester_options": semester_options,
        "hub_default_semester_name": default_semester_name,
    }
