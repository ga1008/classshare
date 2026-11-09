"""Student appeals for selected, matched imported school attendance facts."""
from __future__ import annotations

from .. import smart_attendance_appeal_service as attendance
from ..approval_workflow_service import ApprovalRequestType, ApprovalWorkflowError, PreparedRequest, register_request_type

KEY = attendance.KEY


def before_create(conn, applicant, subject_id, reason, payload):
    if applicant.get("role") != "student":
        raise ApprovalWorkflowError(403, "只能申诉本人的签到")
    attendance.current_fact(conn, subject_id, applicant, lock=True)


def prepare(conn, applicant, subject_id, reason, payload):
    fact = attendance.current_fact(conn, subject_id, applicant)
    if payload.get("source_fingerprint") != fact["source_fingerprint"]:
        raise ApprovalWorkflowError(409, "同步签到已变化，请重新打开详情后申请")
    state = attendance.load_smart_appeal_states(conn, [fact], viewer=applicant)[subject_id]
    if not state["can_appeal"]:
        raise ApprovalWorkflowError(409, "尚未到申诉时间、已过截止时间或已有待处理申请，请查看记录")
    teacher = conn.execute("SELECT t.id,t.name FROM class_offerings o JOIN teachers t ON t.id=o.teacher_id WHERE o.id=?",
                           (fact["class_offering_id"],)).fetchone()
    if not teacher:
        raise ApprovalWorkflowError(409, "课堂已归档")
    snapshot = {key: fact.get(key) for key in attendance.SNAPSHOT_FIELDS}
    return PreparedRequest(title="智慧课堂签到核对申请", subject_id=subject_id,
        reviewers=[{"role": "teacher", "id": teacher["id"], "name": teacher["name"]}],
        dedupe_key=f"smart-attendance:{subject_id}:{applicant['id']}", class_offering_id=fact["class_offering_id"],
        payload={**snapshot, "teacher_id": teacher["id"], "source_snapshot": snapshot,
                 "appeal_deadline": state["appeal_deadline"]})


def authorize(conn, request, reviewer):
    if reviewer.get("role") != "teacher":
        return False
    owner = conn.execute("SELECT teacher_id FROM class_offerings WHERE id=?", (request.get("class_offering_id"),)).fetchone()
    if owner:
        return int(owner["teacher_id"]) == int(reviewer["id"])
    payload = attendance._load(request["payload_json"])
    return (request.get("status") in {"approved", "rejected", "cancelled", "expired"}
            and int(payload.get("teacher_id") or 0) == int(reviewer["id"]))


def before_decision(conn, request, reviewer, payload):
    # Missing/replaced sources must remain rejectable; approval separately
    # requires an exact current fingerprint after locking the source.
    attendance.lock_offering(conn, request.get("class_offering_id"), reviewer)


def approve(conn, request, reviewer, payload, note):
    fact = attendance.current_fact(conn, request["subject_id"], reviewer, lock=True,
                                   expected_offering=request["class_offering_id"])
    original = attendance._load(request["payload_json"])
    if int(fact["student_id"]) != int(request["applicant_user_pk"]) or fact["session_id"] != original.get("session_id"):
        raise ApprovalWorkflowError(409, "同步签到身份或课次已变化，不能将申诉更正用于他人")
    if payload.get("source_fingerprint") != fact["source_fingerprint"]:
        raise ApprovalWorkflowError(409, "同步签到已变化，请打开最新详情再确认")
    status = str(payload.get("status") or "PRESENT")
    if status not in attendance.STATUSES:
        raise ApprovalWorkflowError(422, "签到状态无效")
    return {"status": status, "source_fingerprint": fact["source_fingerprint"], "source_status": fact["source_status"],
        "local_only": True, "remote_write_status": "not_written",
        "notification_suffix": "本平台签到统计已更正；学校原始记录保留，尚未写回智慧课堂。"}


def inbox_link(request, role):
    payload = attendance._load(request["payload_json"])
    if not request.get("class_offering_id") or payload.get("deleted_archive") or payload.get("history_only"):
        return "/profile?section=notifications#profile-message-center"
    return (f"/classroom/{request['class_offering_id']}?attendance_dashboard=1&session_id={payload.get('session_id')}"
            f"&student_id={request['applicant_user_pk']}&smart_appeal_id={request['id']}")


register_request_type(ApprovalRequestType(key=KEY, label="智慧课堂签到申诉", subject_type=attendance.SUBJECT_TYPE,
    applicant_roles=frozenset({"student"}), prepare=prepare, on_approve=approve,
    before_create=before_create, before_decision=before_decision, authorize_reviewer=authorize,
    detail=lambda conn, request, viewer: {"attendance": attendance.request_detail(conn, request, viewer)},
    inbox_link=inbox_link, never_expire=True, reminder_hours=24,
    approve_label="确认并更正本地统计", reject_label="维持并说明",
    description="核对本人的智慧课堂同步签到；更正仅作用于本平台统计，学校原始记录保留。"))
