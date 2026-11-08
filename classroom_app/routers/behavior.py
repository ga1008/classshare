from __future__ import annotations

from typing import Any, Optional
from datetime import datetime, timedelta
import ipaddress
import hashlib

from fastapi import APIRouter, Depends, HTTPException, Response, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from ..database import get_db_connection
from ..db.connection import begin_immediate_transaction, get_configured_db_engine
from ..dependencies import get_current_user, get_current_teacher, get_client_ip
from ..services.behavior_tracking_service import record_behavior_batch
from ..services.behavior_inbox_service import BehaviorBatchConflict
from ..services.behavior_event_validation import BehaviorValidationError, BehaviorPayloadTooLarge
from ..services.resource_access_service import ensure_classroom_access, teacher_can_manage_class_offering

class BehaviorRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validated(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                if self.path.endswith(("/behavior/batch", "/learning/material-progress")):
                    return JSONResponse(status_code=422, content={"detail": _collection_error(
                        "batch_payload_invalid", "采集批次格式无效，已停止重放该批次。", quarantine=True)}, headers={"Cache-Control": "no-store"})
                raise
        return validated


router = APIRouter(prefix="/api/classrooms", tags=["behavior"], route_class=BehaviorRoute)


class BehaviorEventItem(BaseModel):
    action_type: str = Field(default="page_action", max_length=64)
    summary_text: str = Field(default="", max_length=300)
    page_key: Optional[str] = Field(default=None, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class BehaviorBatchRequest(BaseModel):
    batch_id: Optional[str] = Field(default=None, min_length=16, max_length=96, pattern=r"^b1_[0-9]{10,16}_[A-Za-z0-9_-]+$")
    user_key: Optional[str] = Field(default=None, max_length=40)
    page_key: Optional[str] = Field(default=None, max_length=64)
    events: list[BehaviorEventItem] = Field(default_factory=list, max_length=20)


def _collection_error(code: str, message: str, *, quarantine=False, current_user_key=None):
    return {"error_code": code, "message": message, "retryable": not quarantine,
            "batch_disposition": "quarantine" if quarantine else "retain",
            **({"current_user_key": current_user_key} if current_user_key else {})}


def _check_identity(user, user_key):
    current = f"{user.get('role')}:{int(user.get('id') or 0)}"
    if user_key and user_key != current:
        raise HTTPException(409, detail=_collection_error("identity_changed", "账号已更换，请重新打开课堂。", current_user_key=current))
    return current


def _ensure_behavior_access(conn, class_offering_id: int, user_pk: int, user_role: str) -> None:
    ensure_classroom_access(conn, class_offering_id, {"id": user_pk, "role": user_role})


@router.post("/{class_offering_id}/behavior/batch")
def ingest_behavior_batch(
    class_offering_id: int,
    body: BehaviorBatchRequest,
    response: Response,
    user: dict = Depends(get_current_user),
):
    user_pk = int(user.get("id") or 0)
    user_role = str(user.get("role") or "")
    if not user_pk or not user_role:
        raise HTTPException(status_code=401, detail=_collection_error("auth_required", "请登录后恢复采集。"))
    current_user_key = _check_identity(user, body.user_key)
    if not body.events:
        return {"status": "success", "accepted_event_count": 0}

    with get_db_connection() as conn:
        _ensure_behavior_access(conn, class_offering_id, user_pk, user_role)

    try:
        snapshot = record_behavior_batch(
            class_offering_id=class_offering_id,
            user_pk=user_pk,
            user_role=user_role,
            display_name=str(user.get("name") or user.get("username") or f"{user_role}:{user_pk}"),
            page_key=body.page_key,
            events=[item.model_dump() for item in body.events],
            session_started_at=str(user.get("login_time") or "").strip() or None,
            wait=False,
            batch_id=body.batch_id,
            require_durable=True,
        )
    except BehaviorPayloadTooLarge as exc:
        raise HTTPException(status_code=413, detail=_collection_error("batch_payload_too_large", str(exc), quarantine=True)) from exc
    except BehaviorValidationError as exc:
        raise HTTPException(status_code=422, detail=_collection_error("batch_payload_invalid", str(exc), quarantine=True)) from exc
    except BehaviorBatchConflict as exc:
        raise HTTPException(status_code=409, detail=_collection_error(exc.error_code, str(exc), quarantine=True)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=_collection_error("batch_payload_too_large", str(exc), quarantine=True)) from exc
    terminal_error = snapshot.get("write_mode") in {"quarantined", "discarded"}
    response.status_code = 409 if terminal_error else (202 if snapshot.get("write_mode") == "durable_queued" else 200)
    response.headers["Cache-Control"] = "no-store"
    return {"status": "error" if terminal_error else "success", **snapshot, "current_user_key": current_user_key,
            **({"collection_error": _collection_error("batch_" + snapshot["write_mode"], str(snapshot.get("message") or "批次已保留待处理。"), quarantine=True)} if terminal_error else {})}


class CollectionDiagnostic(BaseModel):
    diagnostic_id: str = Field(min_length=8, max_length=96, pattern=r"^d1_[A-Za-z0-9_-]+$")
    error_code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    observed_ms: int = Field(gt=0, le=4102444800000)
    affected_batch_id: Optional[str] = Field(default=None, max_length=96, pattern=r"^b1_[0-9]{10,16}_[A-Za-z0-9_-]+$")
    event_count: int = Field(default=0, ge=0, le=512)
    occurrence_count: int = Field(default=1, ge=1, le=1000000)
    affected_batch_count: int = Field(default=0, ge=0, le=1000000)
    interval_start_ms: Optional[int] = Field(default=None, gt=0, le=4102444800000)
    interval_end_ms: Optional[int] = Field(default=None, gt=0, le=4102444800000)


class CollectionDiagnosticsRequest(BaseModel):
    user_key: str = Field(max_length=40)
    diagnostics: list[CollectionDiagnostic] = Field(min_length=1, max_length=20)


@router.post("/{class_offering_id}/behavior/diagnostics")
def ingest_collection_diagnostics(class_offering_id: int, body: CollectionDiagnosticsRequest, request: Request,
                                  response: Response, user: dict = Depends(get_current_user)):
    from ..services.behavior_collection_health_service import record_collection_observations

    current = _check_identity(user, body.user_key)
    now = datetime.now()
    try:
        peer_ip = str(ipaddress.ip_address(get_client_ip(request)))
    except ValueError:
        peer_ip = None
    # Client reports are evidence of a collection failure, never attendance or learning credit.
    floor, ceiling = int((now - timedelta(days=7)).timestamp() * 1000), int(now.timestamp() * 1000) + 5000
    observations = []
    for item in body.diagnostics:
        if (item.interval_start_ms is None) != (item.interval_end_ms is None) or (item.interval_start_ms is not None and
                item.interval_start_ms > item.interval_end_ms):
            raise HTTPException(422, detail=_collection_error("diagnostic_interval_invalid", "诊断区间无效。", quarantine=True))
        reasons = [item.error_code]
        # Clock values remain untrusted claims. Receipt time controls retention,
        # and diagnostic counters never earn attendance or learning credit.
        if not floor <= item.observed_ms <= ceiling:
            reasons.append("client_clock_out_of_range")
        observations.append(dict(class_offering_id=class_offering_id, user_pk=int(user["id"]),
            user_role=str(user["role"]), batch_id=item.diagnostic_id, received_at=now.isoformat(),
            source="client_diagnostic", valid_sample_count=0, rejected_sample_count=0, learning_delta_seconds=0,
            reason_codes=reasons, diagnostics=item.model_dump(exclude_none=True), ip_address=peer_ip))
    with get_db_connection() as conn:
        engine = get_configured_db_engine()
        if engine == "sqlite":
            begin_immediate_transaction(conn)
        else:
            # Match business intake/deletion lock order. Personal diagnostic
            # evidence cannot arrive behind a committed actor/class deletion.
            for table, value, code in (("class_offerings", class_offering_id, "classroom_unavailable"),
                                       ("students" if user["role"] == "student" else "teachers", int(user["id"]), "actor_unavailable")):
                if conn.execute(f"SELECT id FROM {table} WHERE id=? FOR KEY SHARE", (value,)).fetchone() is None:
                    raise HTTPException(409, detail=_collection_error(code, "课堂或账号已变更。", quarantine=True))
            lock_key = int.from_bytes(hashlib.blake2s(f"{current}:{class_offering_id}".encode(), digest_size=4).digest(), "big", signed=True)
            if not conn.execute("SELECT pg_try_advisory_xact_lock(?, ?) AS acquired", (0x42484447, lock_key)).fetchone()["acquired"]:
                raise HTTPException(429, detail=_collection_error("diagnostic_rate_limited", "诊断正在合并，请稍后重试。"), headers={"Retry-After": "2"})
        _ensure_behavior_access(conn, class_offering_id, int(user["id"]), str(user["role"]))
        scope = (str(user["role"]), int(user["id"]), class_offering_id)
        ids = {item.diagnostic_id for item in body.diagnostics}
        existing = conn.execute("SELECT batch_id FROM behavior_collection_observations WHERE user_role=? AND user_pk=? AND class_offering_id=? AND batch_id IN (" + ",".join("?" for _ in ids) + ")", (*scope, *ids)).fetchall()
        new_count = len(ids - {row["batch_id"] for row in existing})
        if new_count:
            recent = conn.execute("SELECT COUNT(*) AS n FROM behavior_collection_observations WHERE user_role=? AND user_pk=? AND class_offering_id=? AND source='client_diagnostic' AND received_at>=?",
                                  (*scope, (now - timedelta(minutes=1)).isoformat())).fetchone()["n"]
            if int(recent) + new_count > 60:
                raise HTTPException(429, detail=_collection_error("diagnostic_rate_limited", "诊断正在合并，请稍后重试。"), headers={"Retry-After": "60"})
        if new_count:
            record_collection_observations(conn, observations)
        conn.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"status": "success", "current_user_key": current,
            "accepted_diagnostic_ids": [item.diagnostic_id for item in body.diagnostics]}


@router.get("/{class_offering_id}/activity-evidence")
def classroom_activity_evidence(class_offering_id: int, window_start: datetime, window_end: datetime,
                               response: Response, include_login_ips: bool = False,
                               user: dict = Depends(get_current_teacher)):
    from ..services.classroom_activity_evidence_service import CLASSROOM_TIMEZONE, build_classroom_activity_evidence

    now = datetime.now(CLASSROOM_TIMEZONE)
    start = window_start.astimezone(now.tzinfo) if window_start.tzinfo else window_start.replace(tzinfo=now.tzinfo)
    end = window_end.astimezone(now.tzinfo) if window_end.tzinfo else window_end.replace(tzinfo=now.tzinfo)
    if not now - timedelta(days=7) <= start < end <= now + timedelta(seconds=5) or end - start > timedelta(days=1):
        raise HTTPException(422, detail="请选择最近7天内、最长24小时的统计区间。")
    with get_db_connection() as conn:
        offering = conn.execute("SELECT id,teacher_id FROM class_offerings WHERE id = ?", (class_offering_id,)).fetchone()
        if not offering or not teacher_can_manage_class_offering(conn, user["id"], offering):
            raise HTTPException(403, detail="仅任课教师可查看该课堂的登录及采集证据。")
        result = build_classroom_activity_evidence(conn, class_offering_id, start, end, include_login_ips=include_login_ips)
    response.headers["Cache-Control"] = "no-store"
    return {"status": "success", **result}
