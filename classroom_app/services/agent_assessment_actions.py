"""Reviewed assessment mutations through the ordinary domain transactions."""
from __future__ import annotations

from fastapi import HTTPException


ACTION_DEFINITIONS = {
    "create_exam_paper": {
        "label": "创建试卷", "done_label": "已创建试卷", "risk": "medium", "execution_mode": "execute", "roles": ["teacher"],
        "fields": {"title": {"type": "str", "required": True, "max_chars": 300},
                   "description": {"type": "text", "max_chars": 20000, "allow_empty": True},
                   "questions": {"type": "json_object", "required": True, "max_bytes": 180000},
                   "config": {"type": "json_object", "max_bytes": 12000},
                   "scope_level": {"type": "str", "max_chars": 20}},
        "description": "创建本人试卷草稿或新的试卷版本；questions 为原生对象：pages 数组中的 questions 包含 id/type/text/answer/points/grading_guidance/deduction_points，type 为 radio/checkbox/text/textarea，选择题另含 options 数组；根 grading 包含 total_score/description/style，style 为 strict/medium/loose/rescue。保留正常导入格式，布置前必须通过完整评分标准校验。scope_level 为 private/department/school，默认 private；仅创建草稿，不等于布置。",
    },
    "update_unassigned_exam_paper": {
        "label": "修改未布置试卷", "done_label": "已修改试卷草稿", "risk": "medium", "execution_mode": "execute", "roles": ["teacher"],
        "fields": {"paper_id": {"type": "str", "required": True, "max_chars": 80},
                   "expected_paper_revision": {"type": "str", "required": True, "max_chars": 64},
                   "title": {"type": "str", "required": True, "max_chars": 300},
                   "description": {"type": "text", "max_chars": 20000, "allow_empty": True},
                   "questions": {"type": "json_object", "required": True, "max_bytes": 180000},
                   "config": {"type": "json_object", "max_bytes": 12000}},
        "description": "先读 exam.review 的 paper_revision，再保存本人或当前管理员正常有权管理的未布置试卷。title/questions 是完整新内容，未提供 description/config 保留当前值；已布置或有学生草稿/答卷时拒绝，请创建新版本，不能改动学生正在作答的题目。",
    },
    "assign_exam_paper": {
        "label": "布置试卷到课堂", "done_label": "已布置试卷", "risk": "high", "execution_mode": "execute", "roles": ["teacher"],
        "fields": {"paper_id": {"type": "str", "required": True, "max_chars": 80},
                   "expected_paper_revision": {"type": "str", "required": True, "max_chars": 64},
                   "class_offering_id": {"type": "int", "required": True},
                   "assessment_kind": {"type": "str", "required": True, "max_chars": 20},
                   "title": {"type": "str", "max_chars": 300},
                   "status": {"type": "str", "max_chars": 20},
                   "availability_mode": {"type": "str", "max_chars": 20},
                   "due_at": {"type": "str", "max_chars": 40},
                   "duration_minutes": {"type": "int", "maximum": 525600},
                   "auto_close": {"type": "bool"}, "send_email_notification": {"type": "bool"},
                   "allowed_file_types": {"type": "str_list", "max_items": 30, "allow_empty": True},
                   "learning_stage_key": {"type": "str", "max_chars": 80},
                   "late_submission_enabled": {"type": "bool"}, "late_submission_until": {"type": "str", "max_chars": 40},
                   "late_penalty_strategy": {"type": "str", "max_chars": 30},
                   "late_penalty_interval_hours": {"type": "number", "minimum": 0.01, "maximum": 8760},
                   "late_penalty_points": {"type": "number", "minimum": 0, "maximum": 100},
                   "late_penalty_min_score": {"type": "number", "minimum": 0, "maximum": 100},
                   "late_score_cap": {"type": "number", "minimum": 0, "maximum": 100}},
        "description": "将当前教师有权使用的试卷布置给本人负责的课堂；先读 exam.review，指定正式类别 homework/midterm/final。完整标准答案/分值/评分指导/扣分点缺失会拒绝。status 默认为 published，可为 new；availability_mode 为 permanent/deadline/countdown；同卷同课堂不能重复发布。沿用正常 AI 评分模式、分类修订、学生通知、截止提醒与迟交政策。",
    },
    "manual_grade_submission": {
        "label": "保存答卷评分", "done_label": "已保存答卷评分", "risk": "high",
        "execution_mode": "execute", "roles": ["teacher"],
        "fields": {
            "submission_id": {"type": "int", "required": True},
            "expected_review_revision": {"type": "str", "required": True, "max_chars": 64},
            "expected_assignment_revision": {"type": "str", "required": True, "max_chars": 64},
            "score": {"type": "number", "required": True, "minimum": 0, "maximum": 100},
            "feedback_md": {"type": "text", "max_chars": 30000, "allow_empty": True},
        },
        "description": "按当前教师的正常批改权限保存答卷原始分数与评语。先读取 submission.review 并携带 expected_review_revision 和 expected_assignment_revision；平台自动套用迟交政策、替换旧 AI 批改、记录成绩版本并进行小组结算。保存评分不等于公布课程最终成绩。",
    },
    "withdraw_grade_publication": {
        "label": "撤回已公布成绩", "done_label": "已撤回成绩公布", "risk": "high",
        "execution_mode": "execute", "roles": ["teacher"],
        "fields": {"class_offering_id": {"type": "int", "required": True},
                   "publication_id": {"type": "int", "required": True},
                   "reason": {"type": "text", "required": True, "max_chars": 2000}},
        "description": "按当前教师正常权限撤回指定课堂的现行成绩公布版本并记录原因；仅在用户明确要求撤回时执行。先读 classroom.grade_publication 取得 publication_id；过期版本拒绝执行。历史快照保留。",
    },
}


def execute_assessment_action(conn, *, actor, action: str, params: dict) -> dict:
    if actor.role != "teacher" or action not in ACTION_DEFINITIONS:
        raise HTTPException(403, "当前身份不能执行此教师考核操作。")
    if action in {"create_exam_paper", "update_unassigned_exam_paper", "assign_exam_paper"}:
        from .exam_paper_management_service import (
            create_exam_paper_record, update_exam_content_record, assign_exam_paper_record,
            lock_exam_paper, _get_exam_paper_for_teacher,
        )
        import json

        data = dict(params)
        for key, allowed in (("scope_level", {"private", "department", "school"}),
                             ("status", {"new", "published"}),
                             ("availability_mode", {"permanent", "deadline", "countdown"})):
            if key in data and data[key] not in allowed:
                raise HTTPException(400, f"{key} 不在该操作允许的范围内。")
        if action == "create_exam_paper":
            data.setdefault("scope_level", "private")
            result = create_exam_paper_record(conn, teacher_id=actor.id, data=data)
        elif action == "update_unassigned_exam_paper":
            lock_exam_paper(conn, data["paper_id"])
            paper = _get_exam_paper_for_teacher(conn, data["paper_id"], actor.id, manage=True)
            data.setdefault("description", paper.get("description") or "")
            data.setdefault("config", json.loads(paper.get("exam_config_json") or "{}"))
            result = update_exam_content_record(conn, teacher_id=actor.id, paper_id=data["paper_id"], payload=data, unassigned_only=True)
            result["paper_id"] = data["paper_id"]
        else:
            result = assign_exam_paper_record(conn, teacher_id=actor.id, paper_id=data["paper_id"], data=data)
        return {**result, "ref_id": result.get("assignment_id", result.get("paper_id")),
                "label": ACTION_DEFINITIONS[action]["done_label"]}
    if action == "manual_grade_submission":
        from .submission_grading_service import grade_submission_record

        result = grade_submission_record(conn, submission_id=params["submission_id"], teacher_id=actor.id,
                                         data=params, actor_display_name=actor.as_user().get("name") or "")
        return {**result, "ref_id": params["submission_id"], "label": "已保存答卷评分"}
    if action == "withdraw_grade_publication":
        from .grade_publication_service import withdraw_grade_publication

        result = withdraw_grade_publication(conn, teacher_id=actor.id, **params)
        return {**result, "ref_id": params["publication_id"], "label": "已撤回成绩公布",
                "url": f"/classroom/{params['class_offering_id']}"}
    raise HTTPException(409, "考核操作尚未完成适配。")
