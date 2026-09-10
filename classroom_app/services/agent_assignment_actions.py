"""Reviewed assignment editing and publication through the ordinary service."""
from __future__ import annotations

from fastapi import HTTPException

_IDENTITY = {"assignment_id": {"type": "int", "required": True},
             "expected_revision": {"type": "str", "required": True, "max_chars": 64}}
_SCHEDULE = {"availability_mode": {"type": "str", "max_chars": 20},
             "due_at": {"type": "str", "max_chars": 40},
             "duration_minutes": {"type": "int", "maximum": 525600},
             "auto_close": {"type": "bool"}, "send_email_notification": {"type": "bool"}}
ACTION_DEFINITIONS = {
    "update_assignment_settings": {
        "label": "修改作业", "done_label": "已修改作业", "risk": "medium", "execution_mode": "execute", "roles": ["teacher"],
        "fields": {**_IDENTITY, **_SCHEDULE, "title": {"type": "str", "max_chars": 300},
                   "requirements_md": {"type": "text", "max_chars": 30000, "allow_empty": True},
                   "rubric_md": {"type": "text", "max_chars": 30000, "allow_empty": True},
                   "grading_mode": {"type": "str", "max_chars": 20},
                   "allowed_file_types": {"type": "str_list", "max_items": 30, "allow_empty": True}},
        "description": "修改当前教师有权管理的作业，保留未指定设置；expected_revision 来自 assignment.details。评分输入变化按正常服务使在途批改失效，并保留既有成绩。availability_mode 为 permanent/deadline/countdown，grading_mode 为 manual/ai。",
    },
    "publish_assignment": {
        "label": "发布作业", "done_label": "已发布作业", "risk": "medium", "execution_mode": "execute", "roles": ["teacher"],
        "fields": {**_IDENTITY, **_SCHEDULE},
        "description": "按正常教师权限发布既有作业，触发同样的学生通知和截止提醒；expected_revision 来自 assignment.details。可指定截止或倒计时，已发布作业不会重复发送发布通知。",
    },
}


def execute_assignment_action(conn, *, actor, action: str, params: dict) -> dict:
    from .assignment_management_service import load_assignment_row, update_assignment_record
    from .resource_access_service import teacher_can_manage_assignment

    if actor.role != "teacher" or action not in ACTION_DEFINITIONS:
        raise HTTPException(403, "当前身份不能修改教师作业。")
    row = load_assignment_row(conn, params["assignment_id"])
    if not row:
        raise HTTPException(404, "作业不存在。")
    if not teacher_can_manage_assignment(conn, actor.id, row):
        raise HTTPException(403, "无权修改该作业。")
    for key, allowed in (("availability_mode", {"permanent", "deadline", "countdown"}), ("grading_mode", {"manual", "ai"})):
        if key in params and params[key] not in allowed:
            raise HTTPException(400, f"{key} 必须为 {' / '.join(sorted(allowed))}。")
    data = {key: value for key, value in params.items() if key not in _IDENTITY}
    # The normal form posts this checkbox every time; partial Agent edits must
    # preserve its current value when the field is absent.
    data.setdefault("auto_close", bool(row["auto_close"]))
    if action == "publish_assignment":
        data["status"] = "published"
    result = update_assignment_record(conn, assignment_id=params["assignment_id"], teacher_id=actor.id,
                                      data=data, expected_revision=params["expected_revision"])
    return {**result, "url": f"/assignment/{params['assignment_id']}", "ref_id": params["assignment_id"],
            "label": ACTION_DEFINITIONS[action]["done_label"]}
