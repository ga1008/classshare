"""Attendance appeals reuse the shared durable approval/email machinery."""
from __future__ import annotations

from fastapi import HTTPException
from ...db.connection import get_configured_db_engine
from .. import login_attendance_service as attendance
from ..approval_workflow_service import ApprovalRequestType, ApprovalWorkflowError, PreparedRequest, register_request_type

KEY = "login_attendance_appeal"


def _subject(conn, subject_id):
    try:
        record_id = int(subject_id)
    except (TypeError, ValueError) as exc:
        raise ApprovalWorkflowError(422, "签到记录编号无效") from exc
    row = conn.execute("SELECT * FROM login_attendance_records WHERE id=?", (record_id,)).fetchone()
    if not row:
        raise ApprovalWorkflowError(404, "签到记录不存在")
    return dict(row), attendance.load_round(conn, row["round_id"])


def prepare(conn, applicant, subject_id, reason, payload):
    record, round_row = _subject(conn, subject_id)
    if int(applicant["id"]) != record["student_id"]:
        raise ApprovalWorkflowError(403, "只能申诉自己的签到")
    if not round_row["is_current"] or round_row["state"] in {"running", "scheduled", "void"}:
        raise ApprovalWorkflowError(409, "本轮点名尚未结束或已被替代，暂不能申诉")
    policy = attendance._load(round_row["policy_json"])
    ended = round_row["ended_at_ms"] or round_row["ends_at_ms"]
    if attendance.now_ms() > ended + int(policy.get("appeal_days", 7)) * 86400_000:
        raise ApprovalWorkflowError(409, "本次申诉期限已过，请联系教师更正")
    teacher = conn.execute("SELECT t.id,t.name FROM class_offerings o JOIN teachers t ON t.id=o.teacher_id WHERE o.id=?",
                           (round_row["class_offering_id"],)).fetchone()
    if not teacher:
        raise ApprovalWorkflowError(409, "课程已归档，请联系原任课教师处理")
    return PreparedRequest(title="课堂签到核对申请",reviewers=[{"role":"teacher","id":teacher["id"],"name":teacher["name"]}],
                           dedupe_key=f"attendance:{record['id']}",subject_id=str(record["id"]),
                           class_offering_id=round_row["class_offering_id"],
                           payload={"round_id":round_row["id"],"student_id":record["student_id"],"session_id":round_row["session_id"],
                                    "record_revision":record["revision"],"teacher_id":teacher["id"]})


def before_create(conn,applicant,subject_id,reason,payload):
    record,row=_subject(conn,subject_id)
    if applicant.get("role")!="student" or int(applicant["id"])!=record["student_id"]:
        raise ApprovalWorkflowError(403,"只能申诉自己的签到")
    owner=conn.execute("SELECT teacher_id FROM class_offerings WHERE id=?",(row["class_offering_id"],)).fetchone()
    if not owner:
        raise ApprovalWorkflowError(409,"课程已归档，请联系原任课教师处理")
    try:
        # This server-selected teacher is only a lock/ownership fence. The
        # applicant still has to be this record's student in prepare().
        fresh=attendance.load_round(conn,row["id"],{"role":"teacher","id":owner["teacher_id"]},lock=True)
    except HTTPException as exc:
        raise ApprovalWorkflowError(exc.status_code,str(exc.detail)) from None
    if not fresh["is_current"] or fresh["state"] in {"running","scheduled","void"}:
        raise ApprovalWorkflowError(409,"本轮点名尚未结束或已被替代，暂不能申诉")
    suffix=" FOR UPDATE" if get_configured_db_engine()=="postgres" else ""
    conn.execute("SELECT id FROM login_attendance_records WHERE id=?"+suffix,(record["id"],)).fetchone()
    # prepare() runs next under these locks, so replacement/void/manual edits
    # cannot slip between validation, request insertion and notification.


def authorize(conn, request, reviewer):
    record, row = _subject(conn, request["subject_id"])
    if not row["class_offering_id"]:
        return (reviewer.get("role")=="teacher" and int(reviewer["id"])==row["teacher_id"]
                and request.get("status") in {"approved","rejected","cancelled","expired"})
    try:
        attendance.require_teacher(conn,row["class_offering_id"],reviewer)
        return True
    except HTTPException:
        return False


def before_decision(conn, request, reviewer, payload):
    record, row = _subject(conn, request["subject_id"])
    try:
        row=attendance.load_round(conn,row["id"],reviewer,lock=True)
    except HTTPException as exc:
        raise ApprovalWorkflowError(exc.status_code,str(exc.detail)) from None
    if row["state"]=="void" or not row["is_current"]:
        raise ApprovalWorkflowError(409,"该轮点名已作废或替代，请刷新")
    expected = payload.get("record_revision", attendance._load(request["payload_json"]).get("record_revision"))
    # Re-read after the parent lock: a decision elsewhere must never be lost.
    suffix=" FOR UPDATE" if get_configured_db_engine()=="postgres" else ""
    record=dict(conn.execute("SELECT * FROM login_attendance_records WHERE id=?"+suffix,(record["id"],)).fetchone())
    if expected != record["revision"]:
        raise ApprovalWorkflowError(409,"学生考勤已变化，请打开最新详情后处理")


def approve(conn, request, reviewer, payload, note):
    record, row = _subject(conn,request["subject_id"])
    status = str(payload.get("status") or "PRESENT")
    result = attendance.change_records(conn,row["id"],reviewer,
        {"records":[{"student_id":record["student_id"],"revision":record["revision"],"status":status}],
         "reason":note or "教师核对申诉后确认"},resolve_appeals=False)
    settle_attention(conn,request)
    return {"status":status,"record_revision":record["revision"]+1,"notification_suffix":"本次签到结果已更新。"}


def reject(conn, request, reviewer, note):
    settle_attention(conn,request)


def settle_attention(conn, request, *args):
    conn.execute("UPDATE login_attendance_attention SET resolved_at_ms=? WHERE request_id=?",(attendance.now_ms(),request["id"]))


def after_create(conn, request):
    payload=attendance._load(request["payload_json"])
    # Current state is revalidated in this transaction before notifying.
    record,row=_subject(conn,request["subject_id"])
    if row["state"] in {"void","running","scheduled"} or not row["is_current"]:
        raise ApprovalWorkflowError(409,"点名状态刚刚变化，请刷新")
    conn.execute("INSERT INTO login_attendance_attention(request_id,teacher_id,round_id,student_id,created_at_ms) VALUES(?,?,?,?,?) "
                 "ON CONFLICT(request_id) DO NOTHING",
                 (request["id"],payload["teacher_id"],row["id"],record["student_id"],attendance.now_ms()))


def inbox_link(request, role):
    payload=attendance._load(request["payload_json"])
    if not request.get("class_offering_id") or payload.get("history_only") and role!="teacher":
        return "/profile?section=notifications#profile-message-center"
    if payload.get("history_only"):
        return (f"/classroom/{request['class_offering_id']}?attendance_id={payload.get('round_id')}"
                f"&student_id={payload.get('student_id')}&appeal_id={request['id']}&history_only=1")
    return (f"/classroom/{request['class_offering_id']}?session_id={payload.get('session_id')}"
            f"&attendance_id={payload.get('round_id')}&student_id={payload.get('student_id')}&appeal_id={request['id']}")


def detail(conn, request, viewer):
    record,row=_subject(conn,request["subject_id"])
    if viewer.get("role")=="teacher":
        return {"attendance":attendance.student_detail(conn,row["id"],record["student_id"],viewer)}
    if int(viewer["id"]) != record["student_id"]:
        raise ApprovalWorkflowError(403,"无权读取他人签到记录")
    return {"attendance":attendance._public_record(record)}


register_request_type(ApprovalRequestType(
    key=KEY,label="课堂签到申诉",subject_type="login_attendance_record",applicant_roles=frozenset({"student"}),
    prepare=prepare,on_approve=approve,on_reject=reject,on_cancel=settle_attention,on_auto_cancel=settle_attention,
    after_create=after_create,detail=detail,inbox_link=inbox_link,before_decision=before_decision,
    authorize_reviewer=authorize,never_expire=True,reminder_hours=24,approve_label="确认并更正",reject_label="维持并说明",
    before_create=before_create,
    description="点名结束后申请教师核对本人的课堂签到；已受理案件不会自动过期。"))
