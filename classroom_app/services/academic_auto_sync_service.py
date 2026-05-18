from __future__ import annotations

from typing import Any, Awaitable, Callable

from .academic_course_sync_service import sync_current_teacher_courses_from_academic_system
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
        return (
            "success",
            (
                "教务账号已验证并保存，系统已自动同步"
                f" {course_counts.get('course_count', 0)} 门课程、"
                f"{course_counts.get('occurrence_count', 0)} 次课表课次、"
                f"{roster_counts.get('touched_class_count', 0)} 个班级、"
                f"{roster_counts.get('roster_student_count', 0)} 条教学班名单关系、"
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
    ]
    status, message = _summarize_auto_sync(stages)
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
