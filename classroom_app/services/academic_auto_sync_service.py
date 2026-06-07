from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from .academic_service import china_now
from .academic_course_sync_service import sync_current_teacher_courses_from_academic_system
from .academic_classroom_sync_service import sync_teaching_places_from_academic_system
from .academic_course_exam_sync_service import (
    ensure_course_exam_schema,
    sync_current_teacher_course_exams_from_academic_system,
)
from .academic_invigilation_sync_service import sync_current_teacher_invigilations_from_academic_system
from .academic_roster_sync_service import sync_current_teacher_rosters_from_academic_system


SyncCallable = Callable[[int], Awaitable[dict[str, Any]]]


def _int_value(result: dict[str, Any], key: str) -> int:
    try:
        return int(result.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _compact_course_counts(result: dict[str, Any]) -> dict[str, int]:
    return {
        "course_count": _int_value(result, "course_count"),
        "created_count": _int_value(result, "created_count"),
        "updated_count": _int_value(result, "updated_count"),
        "schedule_item_count": _int_value(result, "schedule_item_count"),
        "occurrence_count": _int_value(result, "occurrence_count"),
        "offering_update_count": _int_value(result, "offering_update_count"),
    }


def _compact_roster_counts(result: dict[str, Any]) -> dict[str, int]:
    return {
        "teaching_class_count": _int_value(result, "teaching_class_count"),
        "course_count": _int_value(result, "course_count"),
        "touched_class_count": _int_value(result, "touched_class_count"),
        "classes_created": _int_value(result, "classes_created"),
        "classes_updated": _int_value(result, "classes_updated"),
        "students_created": _int_value(result, "students_created"),
        "students_updated": _int_value(result, "students_updated"),
        "students_moved": _int_value(result, "students_moved"),
        "memberships_upserted": _int_value(result, "memberships_upserted"),
        "roster_student_count": _int_value(result, "roster_student_count"),
        "class_conflicts": _int_value(result, "class_conflicts"),
        "student_conflicts": _int_value(result, "student_conflicts"),
        "contact_conflicts": _int_value(result, "contact_conflicts"),
        "stale_students": _int_value(result, "stale_students"),
    }


def _compact_invigilation_counts(result: dict[str, Any]) -> dict[str, int]:
    return {
        "invigilation_count": _int_value(result, "invigilation_count"),
        "created_count": _int_value(result, "created_count"),
        "updated_count": _int_value(result, "updated_count"),
        "event_created_count": _int_value(result, "event_created_count"),
        "event_updated_count": _int_value(result, "event_updated_count"),
        "notification_count": _int_value(result, "notification_count"),
        "stale_count": _int_value(result, "stale_count"),
    }


def _compact_course_exam_counts(result: dict[str, Any]) -> dict[str, int]:
    return {
        "course_exam_count": _int_value(result, "course_exam_count"),
        "created_count": _int_value(result, "created_count"),
        "updated_count": _int_value(result, "updated_count"),
        "matched_offering_count": _int_value(result, "matched_offering_count"),
        "event_created_count": _int_value(result, "event_created_count"),
        "event_updated_count": _int_value(result, "event_updated_count"),
        "student_notification_count": _int_value(result, "student_notification_count"),
        "stale_count": _int_value(result, "stale_count"),
    }


def _compact_teaching_place_counts(result: dict[str, Any]) -> dict[str, int]:
    return {
        "place_count": _int_value(result, "place_count"),
        "created_count": _int_value(result, "created_count"),
        "updated_count": _int_value(result, "updated_count"),
        "stale_count": _int_value(result, "stale_count"),
    }


def _current_term_params() -> dict[str, str]:
    today = china_now().date()
    year_start = today.year if today.month >= 8 else today.year - 1
    return {
        "xnm": str(year_start),
        "xqm": "12" if 2 <= today.month <= 7 else "3",
    }


def _request_template(
    *,
    url: str,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    referer: str = "",
    body_mode: str = "form",
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json,text/javascript,*/*;q=0.8",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
    }
    if referer:
        headers["Referer"] = referer
    return {
        "provider": "academic",
        "method": "POST",
        "url": url,
        "params": dict(params or {}),
        "headers": headers,
        "body_mode": body_mode,
        "body": dict(body or {}),
    }


def _jqgrid_probe_body(
    *,
    term_params: dict[str, str],
    show_count: int = 10,
    sort_name: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        **term_params,
        **dict(extra or {}),
        "_search": "false",
        "nd": str(int(time.time() * 1000)),
        "queryModel.showCount": str(max(1, int(show_count or 10))),
        "queryModel.currentPage": "1",
        "queryModel.sortName": sort_name,
        "queryModel.sortOrder": "asc",
        "time": "0",
    }


def _sync_stat(conn, table_name: str, teacher_id: int, *, time_column: str = "synced_at") -> dict[str, Any]:
    allowed_tables = {
        "teacher_academic_course_sync_items",
        "teacher_academic_roster_sync_items",
        "teacher_academic_roster_memberships",
        "teacher_academic_invigilation_items",
        "teacher_academic_course_exam_items",
        "teacher_academic_teaching_places",
        "academic_semesters",
    }
    if table_name not in allowed_tables:
        return {"count": 0, "last_synced_at": ""}
    column = time_column if time_column in {"synced_at", "academic_sync_at", "calendar_sync_at"} else "synced_at"
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS count,
               MAX({column}) AS last_synced_at
        FROM {table_name}
        WHERE teacher_id = ?
        """,
        (int(teacher_id),),
    ).fetchone()
    return {
        "count": int((row["count"] if row else 0) or 0),
        "last_synced_at": str((row["last_synced_at"] if row else "") or ""),
    }


def build_academic_sync_capabilities(conn, teacher_id: int) -> list[dict[str, Any]]:
    ensure_course_exam_schema(conn)
    term_params = _current_term_params()
    common_query_params = {"gnmkdm": "N2150"}
    timetable_body: dict[str, Any] = {
        **term_params,
        "kzlx": "ck",
        "djsktkb": "0",
        "xsdm": "",
        "ccdm": "",
        "xsewkbnr": "0",
        "xszd[kch]": "true",
        "xszd[jxbmc]": "true",
        "xszd[jxbzc]": "true",
        "xszd[cd]": "true",
        "xszd[zxs]": "true",
        "xszd[xf]": "true",
        "xszd[xq]": "true",
        "xszd[jxbrs]": "true",
        "xszd[kcxzjc]": "true",
        "xszd[khfs]": "true",
        "xszd[ksfs]": "true",
        "xszd[zhxs]": "true",
    }
    course_stat = _sync_stat(conn, "teacher_academic_course_sync_items", int(teacher_id))
    occurrence_row = conn.execute(
        """
        SELECT COUNT(*) AS count,
               MAX(synced_at) AS last_synced_at
        FROM teacher_academic_course_session_occurrences
        WHERE teacher_id = ?
        """,
        (int(teacher_id),),
    ).fetchone()
    occurrence_count = int((occurrence_row["count"] if occurrence_row else 0) or 0)
    occurrence_synced_at = str((occurrence_row["last_synced_at"] if occurrence_row else "") or "")

    roster_stat = _sync_stat(conn, "teacher_academic_roster_sync_items", int(teacher_id))
    membership_stat = _sync_stat(conn, "teacher_academic_roster_memberships", int(teacher_id))
    invigilation_stat = _sync_stat(conn, "teacher_academic_invigilation_items", int(teacher_id))
    course_exam_stat = _sync_stat(conn, "teacher_academic_course_exam_items", int(teacher_id))
    place_stat = _sync_stat(conn, "teacher_academic_teaching_places", int(teacher_id))
    semester_stat = _sync_stat(conn, "academic_semesters", int(teacher_id), time_column="calendar_sync_at")

    return [
        {
            "key": "courses",
            "label": "课程与课次",
            "description": "同步当前学期教师课表、课程基础信息、真实课次和非周期变动，并对齐本系统课堂。",
            "endpoint": "/api/manage/courses/sync-current-academic",
            "method": "POST",
            "parameters": [
                {"name": "xnm/xqm", "value": "自动识别当前学年学期"},
                {"name": "kzlx", "value": "ck"},
                {"name": "xsxx", "value": "课程字段完整读取"},
            ],
            "last_synced_at": occurrence_synced_at or course_stat["last_synced_at"],
            "has_synced": course_stat["count"] > 0 or occurrence_count > 0,
            "status_text": f"已同步 {course_stat['count']} 条课表、{occurrence_count} 次真实课次",
            "counts": {"course_sync_item_count": course_stat["count"], "occurrence_count": occurrence_count},
            "stats": [
                {"label": "课表记录", "value": course_stat["count"]},
                {"label": "真实课次", "value": occurrence_count},
            ],
            "request_template": _request_template(
                url="https://jwxt.gxufl.com/kbcx/jskbcx_cxJsKb1.html",
                params=common_query_params,
                referer="https://jwxt.gxufl.com/kbcx/jskbcx_cxJskbcxIndex.html?doType=details&gnmkdm=N2150&layout=default",
                body=timetable_body,
            ),
            "safe_note": "只读取教师课表和课程字段，不向教务系统写入信息。",
        },
        {
            "key": "rosters",
            "label": "班级与学生名单",
            "description": "同步教师授课班对应的行政班、学生基础信息和教学班名单关系，保留差异复核信息。",
            "endpoint": "/api/manage/classes/sync-current-academic",
            "method": "POST",
            "parameters": [
                {"name": "xnm/xqm", "value": "自动识别当前学年学期"},
                {"name": "分页", "value": "按教务系统名单接口逐页读取"},
            ],
            "last_synced_at": membership_stat["last_synced_at"] or roster_stat["last_synced_at"],
            "has_synced": roster_stat["count"] > 0 or membership_stat["count"] > 0,
            "status_text": f"已同步 {roster_stat['count']} 个教学班、{membership_stat['count']} 条名单关系",
            "counts": {"teaching_class_count": roster_stat["count"], "membership_count": membership_stat["count"]},
            "stats": [
                {"label": "教学班", "value": roster_stat["count"]},
                {"label": "名单关系", "value": membership_stat["count"]},
            ],
            "request_template": _request_template(
                url="https://jwxt.gxufl.com/xsxkjk/xsxkcx_cxJxbxxList.html",
                params={"doType": "query", "gnmkdm": "N255005"},
                referer="https://jwxt.gxufl.com/xsxkjk/xsxkcx_cxXsxkIndex.html?gnmkdm=N255005&layout=default",
                body=_jqgrid_probe_body(term_params=term_params, show_count=10, sort_name=" "),
            ),
            "safe_note": "按学号和行政班对齐本地数据，冲突不会直接覆盖敏感信息。",
        },
        {
            "key": "semester_calendar",
            "label": "学期校历",
            "description": "识别当前学期，拉取教务系统教学日历并结合节假日/补课日期生成本系统校历。",
            "endpoint": "/api/manage/semesters/calendar/sync-current",
            "method": "POST",
            "parameters": [
                {"name": "当前学期", "value": "优先从教务系统本学期上下文识别"},
                {"name": "节假日", "value": "结合广西适配节假日和补课日期"},
            ],
            "last_synced_at": semester_stat["last_synced_at"],
            "has_synced": bool(semester_stat["last_synced_at"]),
            "status_text": f"已维护 {semester_stat['count']} 个学期",
            "counts": {"semester_count": semester_stat["count"]},
            "stats": [
                {"label": "已维护学期", "value": semester_stat["count"]},
            ],
            "request_template": {
                "provider": "academic",
                "method": "GET",
                "url": "https://jwxt.gxufl.com/xtgl/index_cxAreaSix.html",
                "params": {"localeKey": "zh_CN"},
                "headers": {
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "X-Requested-With": "XMLHttpRequest",
                },
                "body_mode": "form",
                "body": {},
            },
            "safe_note": "生成本地校历，不向教务系统提交修改。",
        },
        {
            "key": "invigilations",
            "label": "监考安排",
            "description": "同步当前学期监考信息，并写入教师日历和待办提醒。",
            "endpoint": "/api/manage/system/academic-invigilations/sync-current",
            "method": "POST",
            "parameters": [
                {"name": "xnm/xqm", "value": "自动识别当前学年学期"},
                {"name": "监考查询", "value": "教师当前账号可见安排"},
            ],
            "last_synced_at": invigilation_stat["last_synced_at"],
            "has_synced": invigilation_stat["count"] > 0,
            "status_text": f"已同步 {invigilation_stat['count']} 条监考安排",
            "counts": {"invigilation_count": invigilation_stat["count"]},
            "stats": [
                {"label": "监考安排", "value": invigilation_stat["count"]},
            ],
            "request_template": _request_template(
                url="https://jwxt.gxufl.com/kwgl/jkcx_cxJsjkxxIndex.html",
                params={"doType": "query", "gnmkdm": "N358125"},
                referer="https://jwxt.gxufl.com/kwgl/jkcx_cxJsjkxxIndex.html?gnmkdm=N358125&layout=default",
                body=_jqgrid_probe_body(
                    term_params=term_params,
                    show_count=10,
                    sort_name="kssj",
                    extra={
                        "ksmcdmb_id": "",
                        "ksrq": "",
                        "sjbh": "",
                        "kc": "",
                        "kch": "",
                    },
                ),
            ),
            "safe_note": "只读取监考安排，日历和待办只写入本系统。",
        },
        {
            "key": "course_exams",
            "label": "任课考试",
            "description": "同步当前教师任课课程的考试安排，匹配本地课堂后写入课堂时间轴、日程和学生重要通知。",
            "endpoint": "/api/manage/system/academic-course-exams/sync-current",
            "method": "POST",
            "parameters": [
                {"name": "xnm/xqm", "value": "自动识别当前学年学期"},
                {"name": "任课教师考试查询", "value": "教师当前账号可见任课考试安排"},
            ],
            "last_synced_at": course_exam_stat["last_synced_at"],
            "has_synced": course_exam_stat["count"] > 0,
            "status_text": f"已同步 {course_exam_stat['count']} 条任课考试安排",
            "counts": {"course_exam_count": course_exam_stat["count"]},
            "stats": [
                {"label": "任课考试", "value": course_exam_stat["count"]},
            ],
            "request_template": _request_template(
                url="https://jwxt.gxufl.com/kwgl/rkjskscx_cxRkjsksIndex.html",
                params={"doType": "query", "gnmkdm": "N358126"},
                referer="https://jwxt.gxufl.com/kwgl/rkjskscx_cxRkjsksIndex.html?gnmkdm=N358126&layout=default",
                body=_jqgrid_probe_body(
                    term_params=term_params,
                    show_count=10,
                    sort_name="kssj ",
                    extra={
                        "ksmcdmb_id": "",
                        "ksrq": "",
                        "sjbh": "",
                        "kc": "",
                        "jkjs": "",
                        "kch": "",
                    },
                ),
            ),
            "safe_note": "只读取教务系统任课考试安排；课堂时间轴、日程和通知只写入本地系统。",
        },
        {
            "key": "teaching_places",
            "label": "教学场地",
            "description": "同步教务系统教学场地，用于本地模糊查询、考试教室选择和空闲教室推荐。",
            "endpoint": "/api/manage/classrooms/sync-academic",
            "method": "POST",
            "parameters": [
                {"name": "场地列表", "value": "按教务系统分页读取"},
                {"name": "场地类别/校区/楼号", "value": "同步为本地筛选字段"},
            ],
            "last_synced_at": place_stat["last_synced_at"],
            "has_synced": place_stat["count"] > 0,
            "status_text": f"已同步 {place_stat['count']} 个教学场地",
            "counts": {"place_count": place_stat["count"]},
            "stats": [
                {"label": "教学场地", "value": place_stat["count"]},
            ],
            "request_template": _request_template(
                url="https://jwxt.gxufl.com/pkgl/jxcdjbxxgl_cxJxcdjbxxIndex.html",
                params={"doType": "query", "gnmkdm": "N211015"},
                referer="https://jwxt.gxufl.com/pkgl/jxcdjbxxgl_cxJxcdjbxxIndex.html?gnmkdm=N211015&layout=default",
                body=_jqgrid_probe_body(term_params={}, show_count=10, sort_name="cdbh"),
            ),
            "safe_note": "空闲教室仍实时查询教务系统，场地列表只作为本地筛选与选择基础。",
        },
    ]


def _stage_payload(
    *,
    key: str,
    label: str,
    result: dict[str, Any],
    counts: dict[str, int],
) -> dict[str, Any]:
    warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
    follow_up_items = result.get("follow_up_items") if isinstance(result.get("follow_up_items"), list) else []
    return {
        "key": key,
        "label": label,
        "status": str(result.get("status") or "unknown"),
        "message": str(result.get("message") or ""),
        "semester_id": result.get("semester_id"),
        "semester_name": str(result.get("semester_name") or ""),
        "counts": counts,
        "warnings": [str(item) for item in warnings[:8]],
        "follow_up_items": [str(item) for item in follow_up_items[:8]],
    }


async def _run_stage(
    *,
    teacher_id: int,
    key: str,
    label: str,
    runner: SyncCallable,
    count_builder: Callable[[dict[str, Any]], dict[str, int]],
) -> dict[str, Any]:
    try:
        result = await runner(int(teacher_id))
    except Exception as exc:
        result = {
            "status": "failed",
            "message": f"{label}自动同步异常：{str(exc)[:180]}",
        }
    return _stage_payload(
        key=key,
        label=label,
        result=result,
        counts=count_builder(result),
    )


def _summarize_auto_sync(stages: list[dict[str, Any]]) -> tuple[str, str]:
    success_count = sum(1 for item in stages if item.get("status") == "success")
    if success_count == len(stages):
        course_counts = next((item.get("counts") or {} for item in stages if item.get("key") == "courses"), {})
        roster_counts = next((item.get("counts") or {} for item in stages if item.get("key") == "rosters"), {})
        invigilation_counts = next((item.get("counts") or {} for item in stages if item.get("key") == "invigilations"), {})
        place_counts = next((item.get("counts") or {} for item in stages if item.get("key") == "teaching_places"), {})
        return (
            "success",
            (
                "教务账号已验证并保存，系统已自动同步"
                f" {course_counts.get('course_count', 0)} 门课程、"
                f"{course_counts.get('occurrence_count', 0)} 次课表课次、"
                f"{roster_counts.get('touched_class_count', 0)} 个班级、"
                f"{roster_counts.get('roster_student_count', 0)} 条教学班名单关系、"
                f"{place_counts.get('place_count', 0)} 个教学场地、"
                f"{invigilation_counts.get('invigilation_count', 0)} 条监考安排。"
            ),
        )
    if success_count:
        return (
            "partial_success",
            "教务账号已验证并保存，部分教务数据已同步；未完成的部分已保留原因，请稍后重试或到课程/班级页面手动同步。",
        )
    return (
        "failed",
        "教务账号已验证并保存，但自动同步课程和班级学生名单都未完成；凭据仍可用于后续手动同步。",
    )


async def sync_teacher_dashboard_reminders(teacher_id: int) -> dict[str, Any]:
    """Refresh the academic data that drives the teacher dashboard reminders.

    Scoped to the two feeds behind the 待办与提醒 widget — invigilation
    assignments and course exams — so the dashboard bell can resync quickly
    without rerunning the full course/roster/place chain.
    """
    stages = [
        await _run_stage(
            teacher_id=teacher_id,
            key="invigilations",
            label="监考安排",
            runner=sync_current_teacher_invigilations_from_academic_system,
            count_builder=_compact_invigilation_counts,
        ),
        await _run_stage(
            teacher_id=teacher_id,
            key="course_exams",
            label="任课考试",
            runner=sync_current_teacher_course_exams_from_academic_system,
            count_builder=_compact_course_exam_counts,
        ),
    ]
    success_count = sum(1 for stage in stages if stage.get("status") == "success")
    invigilation_counts = next((s.get("counts") or {} for s in stages if s.get("key") == "invigilations"), {})
    course_exam_counts = next((s.get("counts") or {} for s in stages if s.get("key") == "course_exams"), {})
    if success_count == len(stages):
        status = "success"
        message = (
            "已刷新教务提醒："
            f"监考 {invigilation_counts.get('invigilation_count', 0)} 条、"
            f"任课考试 {course_exam_counts.get('course_exam_count', 0)} 条。"
        )
    elif success_count:
        status = "partial_success"
        message = "已刷新部分教务提醒，其余未完成的项目可稍后重试。"
    else:
        status = "failed"
        # Surface the first stage message so credential/term issues are actionable.
        first_message = next((s.get("message") for s in stages if s.get("message")), "")
        message = first_message or "教务提醒刷新未完成，请稍后重试或检查教务账号。"
    warnings: list[str] = []
    for stage in stages:
        warnings.extend(stage.get("warnings") or [])
    return {
        "status": status,
        "message": message,
        "stages": stages,
        "warnings": warnings[:12],
    }


async def sync_teacher_academic_data_after_credential_verified(teacher_id: int) -> dict[str, Any]:
    """Run the post-credential sync chain without making credential persistence transactional.

    Course sync runs first so roster memberships can attach to freshly synced
    academic courses when course codes match. Every stage preserves its own
    existing-data alignment rules and can fail independently.
    """
    stages = [
        await _run_stage(
            teacher_id=teacher_id,
            key="courses",
            label="课程课表",
            runner=sync_current_teacher_courses_from_academic_system,
            count_builder=_compact_course_counts,
        ),
        await _run_stage(
            teacher_id=teacher_id,
            key="rosters",
            label="班级学生名单",
            runner=sync_current_teacher_rosters_from_academic_system,
            count_builder=_compact_roster_counts,
        ),
        await _run_stage(
            teacher_id=teacher_id,
            key="invigilations",
            label="监考安排",
            runner=sync_current_teacher_invigilations_from_academic_system,
            count_builder=_compact_invigilation_counts,
        ),
        await _run_stage(
            teacher_id=teacher_id,
            key="course_exams",
            label="任课考试",
            runner=sync_current_teacher_course_exams_from_academic_system,
            count_builder=_compact_course_exam_counts,
        ),
        await _run_stage(
            teacher_id=teacher_id,
            key="teaching_places",
            label="教学场地",
            runner=sync_teaching_places_from_academic_system,
            count_builder=_compact_teaching_place_counts,
        ),
    ]
    status, message = _summarize_auto_sync(stages)
    course_exam_counts = next((item.get("counts") or {} for item in stages if item.get("key") == "course_exams"), {})
    if status == "success":
        message = f"{message} 任课考试 {course_exam_counts.get('course_exam_count', 0)} 条。"
    warnings: list[str] = []
    follow_up_items: list[str] = []
    for stage in stages:
        warnings.extend(stage.get("warnings") or [])
        follow_up_items.extend(stage.get("follow_up_items") or [])
    return {
        "status": status,
        "message": message,
        "stages": stages,
        "warnings": warnings[:12],
        "follow_up_items": follow_up_items[:12],
    }
