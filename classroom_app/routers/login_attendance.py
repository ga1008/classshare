"""Authorized HTTP surface for local classroom attendance."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..database import get_db_connection
from ..dependencies import get_current_teacher, get_current_user
from ..services import login_attendance_service as service
from ..services import login_attendance_settlement_service as settlement
from ..services import attendance_dashboard_service as dashboard_service
from ..services import approval_workflow_service as workflow
from ..services.trusted_network_service import client_network, require_same_origin

async def _private_response(response: Response):
    # These projections include personal attendance and teacher-only IP
    # evidence; shared classroom browsers must fetch for the current identity.
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie, Authorization"


router = APIRouter(prefix="/api/classrooms/{offering_id}/login-attendance", dependencies=[Depends(_private_response)])
attention_router = APIRouter(prefix="/api/login-attendance/attention", dependencies=[Depends(_private_response)])
archive_router = APIRouter(prefix="/api/login-attendance/archive", dependencies=[Depends(_private_response)])


class Revision(BaseModel):
    revision: int = Field(ge=0)


class SettlementRequest(BaseModel):
    model_config = {'extra':'forbid'}
    idempotency_key: str = Field(min_length=8,max_length=100)
    expected_round_revision: int = Field(ge=1,strict=True)
    restart: bool = Field(default=False,strict=True)
    adopt_current_rules: bool = Field(default=False,strict=True)
    expected_task_id: str | None = Field(default=None,min_length=1,max_length=64)
    expected_task_generation: int | None = Field(default=None,ge=1,strict=True)


class CreateRound(BaseModel):
    request_key: str = Field(min_length=8, max_length=100)
    policy_revision: int | None = Field(default=None, ge=0)
    networks: list[str] | None = Field(default=None, max_length=16)
    enforce_network: bool | None = Field(default=None, strict=True)
    duration_minutes: int | None = Field(default=None, ge=1, le=240, strict=True)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    teacher_reference_at: datetime | None = None
    supersedes_id: int | None = None


class VoidRound(Revision):
    reason: str = Field(min_length=1, max_length=1000)


class RecordChange(Revision):
    student_id: int = Field(gt=0)
    status: Literal["PRESENT", "ABSENT", "SICK_LEAVE", "PERSONAL_LEAVE", "LATE", "EARLY"]


class ChangeRecords(BaseModel):
    records: list[RecordChange] = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=1000)


class Appeal(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class Policy(Revision):
    networks: list[str] = Field(default_factory=list, max_length=16)
    enforce_network: bool = Field(default=False, strict=True)
    before_minutes: int = Field(default=10, ge=0, le=20)
    after_minutes: int = Field(default=10, ge=0, le=20)
    appeal_days: int = Field(default=7, ge=1, le=30)


class Claim(BaseModel):
    owner: str = Field(min_length=8, max_length=100)


def _origin(request):
    require_same_origin(request)


def _offering_round(conn, offering_id, round_id):
    row = service.load_round(conn, round_id)
    if row["class_offering_id"] != offering_id:
        raise HTTPException(404, "点名不属于此课堂。")
    return row


@router.get("/history")
def history(offering_id: int,before_id: int|None=None,limit: int=50,user: dict=Depends(get_current_teacher)):
    with get_db_connection() as conn:
        return service.history(conn,offering_id,user,before_id=before_id,limit=limit)


@router.get("/dashboard/visibility")
def dashboard_visibility(offering_id: int, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        return dashboard_service.visibility(conn, offering_id, user)


@router.get("/dashboard")
def dashboard(offering_id: int, session_id: int | None = None, user: dict = Depends(get_current_user)):
    with get_db_connection() as conn:
        return dashboard_service.dashboard(conn, offering_id, user, session_id=session_id)


@router.get("/dashboard/students/{student_id}")
def dashboard_student(offering_id: int, student_id: int, user: dict = Depends(get_current_teacher)):
    with get_db_connection() as conn:
        return dashboard_service.dashboard(conn, offering_id, user, student_id=student_id)


@archive_router.get("/rounds/{round_id}")
def archived_round(round_id: int,user: dict=Depends(get_current_teacher)):
    with get_db_connection() as conn:
        row=service.load_round(conn,round_id,user)
        if row["class_offering_id"] is not None:
            raise HTTPException(409,"该课堂仍存在，请从课堂历史点名查看。")
        return service.round_view(conn,round_id,user)


@archive_router.get("/history")
def archived_history(before_id: int|None=None,limit: int=50,user: dict=Depends(get_current_teacher)):
    with get_db_connection() as conn:
        limit=max(1,min(limit,100))
        condition="class_offering_id IS NULL AND teacher_id=?";args=[user["id"]]
        if before_id is not None:
            condition+=" AND id<?";args.append(before_id)
        rows=conn.execute("SELECT id,state,context_json,created_at_ms FROM login_attendance_rounds WHERE "+condition+
                          " ORDER BY id DESC LIMIT ?",(*args,limit+1)).fetchall()
        return {"items":[{"id":r["id"],"state":r["state"],"context":service._load(r["context_json"]),
                           "created_at_ms":r["created_at_ms"],"can_edit":False} for r in rows[:limit]],
                "next_before_id":rows[limit-1]["id"] if len(rows)>limit else None}


@router.get("/sessions/{session_id}")
def session(offering_id: int, session_id: int, request: Request, user: dict=Depends(get_current_teacher)):
    with get_db_connection() as conn:
        result=service.session_view(conn,offering_id,session_id,user)
        result["teacher_network"]=client_network(request)
        result['teacher_network']['suggested_cidr']=service.rules.suggested_network(result['teacher_network']['ip'])
        return result


@router.post("/sessions/{session_id}/rounds")
def create(offering_id: int, session_id: int, body: CreateRound, request: Request, user: dict=Depends(get_current_teacher)):
    _origin(request)
    with get_db_connection() as conn:
        payload=body.model_dump(mode="json", exclude_none=True)
        policy=service.load_policy(conn,offering_id)
        if payload.get('enforce_network',policy['enforce_network']) and 'networks' not in payload and not policy['networks']:
            suggestion=service.rules.suggested_network(client_network(request)['ip'])
            payload['networks']=[suggestion] if suggestion else []
        result=service.create_round(conn,offering_id,session_id,user,payload)
        conn.commit()
        return result


@router.put("/policy")
def policy(offering_id: int, body: Policy, request: Request, user: dict=Depends(get_current_teacher)):
    _origin(request)
    with get_db_connection() as conn:
        result=service.save_policy(conn,offering_id,user,body.model_dump())
        conn.commit()
        return result


@router.get("/rounds/{round_id}")
def get_round(offering_id: int, round_id: int, user: dict=Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _offering_round(conn,offering_id,round_id)
        return service.round_view(conn,round_id,user)


@router.post("/rounds/{round_id}/end")
def end(offering_id: int, round_id: int, body: Revision, request: Request, user: dict=Depends(get_current_teacher)):
    _origin(request)
    with get_db_connection() as conn:
        _offering_round(conn,offering_id,round_id)
        result=service.end_round(conn,round_id,user,body.model_dump())
        conn.commit()
        return result


@router.post("/rounds/{round_id}/void")
def void(offering_id: int, round_id: int, body: VoidRound, request: Request, user: dict=Depends(get_current_teacher)):
    _origin(request)
    with get_db_connection() as conn:
        _offering_round(conn,offering_id,round_id)
        result=service.void_round(conn,round_id,user,body.model_dump())
        conn.commit()
        return result


@router.patch("/rounds/{round_id}/records")
def change(offering_id: int, round_id: int, body: ChangeRecords, request: Request, user: dict=Depends(get_current_teacher)):
    _origin(request)
    with get_db_connection() as conn:
        _offering_round(conn,offering_id,round_id)
        result=service.change_records(conn,round_id,user,body.model_dump())
        conn.commit()
        return result


@router.post("/rounds/{round_id}/recalculate")
def recalculate(offering_id: int, round_id: int, body: Revision, request: Request, user: dict=Depends(get_current_teacher)):
    _origin(request)
    with get_db_connection() as conn:
        _offering_round(conn,offering_id,round_id)
        row=service.load_round(conn,round_id,user,lock=True)
        service._revision(row,body.model_dump())
        settlement.assert_no_unpublished_settlement(conn,round_id)
        if row["state"]=="void" or not row["is_current"]:
            raise HTTPException(409,"本轮已作废或替代，仅可查看历史记录。")
        if row["state"]=="published" and service.now_ms()>row["window_end_ms"]+7*86400_000:
            raise HTTPException(409,"原始记录回放期已结束，可根据已封存证据更正考勤。")
        if row["evaluated_at_ms"] and service.now_ms()-row["evaluated_at_ms"]<10_000:
            return service.round_view(conn,round_id,user)
        service.evaluate_round(conn,round_id)
        result=service.round_view(conn,round_id,user)
        conn.commit()
        return result


@router.get('/rounds/{round_id}/settlement')
def settlement_status(offering_id:int,round_id:int,user:dict=Depends(get_current_teacher)):
    with get_db_connection() as conn:
        return settlement.status(conn,user,offering_id,round_id)


@router.post('/rounds/{round_id}/settlement',status_code=202)
def request_settlement(offering_id:int,round_id:int,body:SettlementRequest,request:Request,user:dict=Depends(get_current_teacher)):
    _origin(request)
    with get_db_connection() as conn:
        result=settlement.request_settlement(conn,user,offering_id,round_id,body.model_dump(exclude_none=True))
        conn.commit()
        return result


@router.get("/rounds/{round_id}/students/{student_id}")
def detail(offering_id: int, round_id: int, student_id: int, user: dict=Depends(get_current_teacher)):
    with get_db_connection() as conn:
        _offering_round(conn,offering_id,round_id)
        return service.student_detail(conn,round_id,student_id,user)


@router.get("/sessions/{session_id}/me")
def me(offering_id: int, session_id: int, user: dict=Depends(get_current_user)):
    with get_db_connection() as conn:
        return service.student_view(conn,offering_id,session_id,user)


@router.post("/rounds/{round_id}/appeals")
def appeal(offering_id: int, round_id: int, body: Appeal, request: Request, user: dict=Depends(get_current_user)):
    _origin(request)
    with get_db_connection() as conn:
        _offering_round(conn,offering_id,round_id)
        record=conn.execute("SELECT id FROM login_attendance_records WHERE round_id=? AND student_id=?",(round_id,user["id"])).fetchone()
        if user.get("role")!="student" or not record:
            raise HTTPException(403,"只能申诉自己的签到。")
        try:
            result=workflow.create_request(conn,user,request_type="login_attendance_appeal",subject_id=record["id"],reason=body.reason)
        except workflow.ApprovalWorkflowError as exc:
            conn.rollback()
            raise HTTPException(exc.status_code,exc.message) from exc
        conn.commit()
        return {"request":result}


def _attention_row(conn, request_id, user):
    row=conn.execute("SELECT a.*,r.class_offering_id,r.session_id,s.name,s.student_number FROM login_attendance_attention a "
        "JOIN login_attendance_rounds r ON r.id=a.round_id JOIN class_offerings o ON o.id=r.class_offering_id "
        "JOIN login_attendance_records s ON s.round_id=a.round_id AND s.student_id=a.student_id "
        "WHERE a.request_id=? AND o.teacher_id=?",(request_id,user["id"])).fetchone()
    if not row:
        raise HTTPException(404,"提醒不存在或当前无权处理。")
    result=dict(row)
    notice=conn.execute("SELECT email_status,email_queued_at,email_sent_at FROM message_center_notifications "
        "WHERE recipient_role='teacher' AND recipient_user_pk=? AND ref_type='approval_workflow' "
        "AND ref_id LIKE ? ORDER BY id DESC LIMIT 1",(user["id"],f"login_attendance_appeal:{request_id}:%")).fetchone()
    result["email_delivery"] = dict(notice) if notice else {"email_status":"unavailable","email_queued_at":None,"email_sent_at":None}
    return result


@attention_router.get("")
def attention(user: dict=Depends(get_current_teacher)):
    with get_db_connection() as conn:
        rows=conn.execute("SELECT a.request_id FROM login_attendance_attention a JOIN login_attendance_rounds r ON r.id=a.round_id "
            "JOIN class_offerings o ON o.id=r.class_offering_id WHERE o.teacher_id=? AND a.resolved_at_ms IS NULL "
            "AND a.acknowledged_at_ms IS NULL ORDER BY a.created_at_ms LIMIT 20",(user["id"],)).fetchall()
        return {"items":[_attention_row(conn,r["request_id"],user) for r in rows]}


@attention_router.post("/{request_id}/claim")
def claim(request_id: int, body: Claim, request: Request, user: dict=Depends(get_current_teacher)):
    _origin(request)
    with get_db_connection() as conn:
        row=_attention_row(conn,request_id,user)
        stamp=service.now_ms()
        cursor=conn.execute("UPDATE login_attendance_attention SET claim_owner=?,claim_until_ms=? WHERE request_id=? "
            "AND acknowledged_at_ms IS NULL AND resolved_at_ms IS NULL AND (claim_until_ms IS NULL OR claim_until_ms<? OR claim_owner=?)",
            (body.owner,stamp+60_000,request_id,stamp,body.owner))
        conn.commit()
        return {"claimed":cursor.rowcount==1,"item":row}


@attention_router.post("/{request_id}/ack")
def acknowledge(request_id: int, request: Request, user: dict=Depends(get_current_teacher)):
    _origin(request)
    with get_db_connection() as conn:
        _attention_row(conn,request_id,user)
        conn.execute("UPDATE login_attendance_attention SET acknowledged_at_ms=? WHERE request_id=? AND acknowledged_at_ms IS NULL",
                     (service.now_ms(),request_id))
        conn.commit()
        return {"acknowledged":True}
