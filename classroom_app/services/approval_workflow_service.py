"""Generic approval workflow (通用审批流模板).

A *request type* registers its business hooks once; the tables, state machine,
permissions, audit trail, notifications (message center + e-mail) and reminder
sweep are shared. Adding a new workflow means implementing one
:class:`ApprovalRequestType` (see ``approval_request_types/submission_withdraw.py``)
and, on the front end, optionally a detail renderer for ``approval_workflow.js``.

State machine: ``pending`` → ``approved`` | ``rejected`` (by any listed reviewer or a
super admin) | ``cancelled`` (by the applicant, or automatically when the business
action happened another way) | ``expired`` (reminder sweep after ``expire_days``).
All decisions use a guarded ``UPDATE … WHERE status = 'pending'`` so concurrent
reviewers cannot both win.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Optional

from ..db.connection import begin_immediate_transaction, execute_insert_returning_id
from .approval_workflow_schema import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_CANCELLED,
    APPROVAL_STATUS_EXPIRED,
    APPROVAL_STATUS_PENDING,
    APPROVAL_STATUS_REJECTED,
    ensure_approval_workflow_schema,
)
from . import message_center_service

MESSAGE_CATEGORY_APPROVAL = "approval_workflow"
TASK_KIND_APPROVAL_REMINDER = "approval_request_reminder"
REMINDER_INTERVAL_SECONDS = 6 * 3600
REASON_MAX_CHARS = 1000
NOTE_MAX_CHARS = 1000

STATUS_LABELS = {
    APPROVAL_STATUS_PENDING: "待审批",
    APPROVAL_STATUS_APPROVED: "已通过",
    APPROVAL_STATUS_REJECTED: "已拒绝",
    APPROVAL_STATUS_CANCELLED: "已取消",
    APPROVAL_STATUS_EXPIRED: "已过期",
}


class ApprovalWorkflowError(Exception):
    """Business error carrying an HTTP-ish status code for the router layer."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = int(status_code)
        self.message = message


@dataclass(frozen=True)
class PreparedRequest:
    title: str
    reviewers: list[dict[str, Any]]
    dedupe_key: str
    subject_id: str
    assignment_id: str | None = None
    class_offering_id: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    expires_at: str | None = None


@dataclass(frozen=True)
class ApprovalRequestType:
    key: str
    label: str
    subject_type: str
    applicant_roles: frozenset[str]
    # prepare(conn, applicant, subject_id, reason, payload) -> PreparedRequest ; raises ApprovalWorkflowError
    prepare: Callable[..., PreparedRequest]
    # on_approve(conn, request, reviewer, decision_payload, note) -> dict stored as decision_payload
    on_approve: Callable[..., dict[str, Any]]
    on_reject: Optional[Callable[..., None]] = None
    on_cancel: Optional[Callable[..., None]] = None
    # detail(conn, request, viewer) -> dict merged into the API detail payload
    detail: Optional[Callable[..., dict[str, Any]]] = None
    # inbox_link(request, role) -> deep link used by notifications
    inbox_link: Optional[Callable[..., str]] = None
    reminder_hours: int = 48
    expire_days: int = 7
    approve_label: str = "同意"
    reject_label: str = "拒绝"
    description: str = ""


_REGISTRY: dict[str, ApprovalRequestType] = {}


def register_request_type(request_type: ApprovalRequestType) -> ApprovalRequestType:
    _REGISTRY[request_type.key] = request_type
    return request_type


def get_request_type(key: str) -> ApprovalRequestType:
    request_type = _REGISTRY.get(str(key or "").strip())
    if request_type is None:
        raise ApprovalWorkflowError(404, "未知的申请类型")
    return request_type


def list_request_types() -> list[dict[str, Any]]:
    return [
        {"key": item.key, "label": item.label, "subject_type": item.subject_type,
         "applicant_roles": sorted(item.applicant_roles), "approve_label": item.approve_label,
         "reject_label": item.reject_label, "description": item.description}
        for item in _REGISTRY.values()
    ]


# --------------------------------------------------------------------------- helpers
def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _identity(user: Mapping[str, Any] | None) -> tuple[str, int]:
    user = user or {}
    try:
        user_pk = int(user.get("id") or 0)
    except (TypeError, ValueError):
        user_pk = 0
    return str(user.get("role") or "").strip().lower(), user_pk


def _user_name(user: Mapping[str, Any] | None) -> str:
    user = user or {}
    return str(user.get("name") or user.get("username") or user.get("display_name") or "").strip()


def _json_loads(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any, *, limit: int, field_name: str, required: bool = False) -> str:
    text = " ".join(str(value or "").split()).strip()
    if required and not text:
        raise ApprovalWorkflowError(400, f"请填写{field_name}")
    if len(text) > limit:
        raise ApprovalWorkflowError(400, f"{field_name}不能超过 {limit} 字")
    return text


def _is_super_admin(conn, user: Mapping[str, Any]) -> bool:
    role, user_pk = _identity(user)
    return role == "teacher" and message_center_service.is_super_admin_teacher(conn, user_pk)


def _load_request_row(conn, request_id: int | str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM approval_requests WHERE id = ? LIMIT 1", (int(request_id),)).fetchone()
    if not row:
        raise ApprovalWorkflowError(404, "申请不存在")
    return dict(row)


def _load_reviewers(conn, request_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT reviewer_role, reviewer_user_pk, reviewer_name, notified_at, reminded_at "
        "FROM approval_request_reviewers WHERE request_id = ? ORDER BY id",
        (int(request_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _is_reviewer(reviewers: list[dict[str, Any]], user: Mapping[str, Any]) -> bool:
    role, user_pk = _identity(user)
    return any(str(item["reviewer_role"]) == role and int(item["reviewer_user_pk"]) == user_pk for item in reviewers)


def _is_applicant(request: Mapping[str, Any], user: Mapping[str, Any]) -> bool:
    role, user_pk = _identity(user)
    return str(request.get("applicant_role")) == role and int(request.get("applicant_user_pk") or 0) == user_pk


def _record_event(conn, request_id: int, event_type: str, *, actor: Mapping[str, Any] | None = None,
                  note: str = "", payload: dict[str, Any] | None = None) -> None:
    role, user_pk = _identity(actor)
    conn.execute(
        "INSERT INTO approval_request_events (request_id, event_type, actor_role, actor_user_pk, actor_name, note, payload_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (int(request_id), event_type, role, user_pk or None, _user_name(actor), note or "",
         json.dumps(payload or {}, ensure_ascii=False), _now_iso()),
    )


def serialize_request(row: Mapping[str, Any], *, reviewers: list[dict[str, Any]] | None = None,
                      viewer: Mapping[str, Any] | None = None) -> dict[str, Any]:
    request_type = _REGISTRY.get(str(row.get("request_type") or ""))
    status = str(row.get("status") or "")
    item = {
        "id": int(row["id"]),
        "request_type": row.get("request_type"),
        "request_type_label": request_type.label if request_type else row.get("request_type"),
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "applicant_role": row.get("applicant_role"),
        "applicant_user_pk": row.get("applicant_user_pk"),
        "applicant_name": row.get("applicant_name") or "",
        "subject_type": row.get("subject_type"),
        "subject_id": row.get("subject_id"),
        "assignment_id": row.get("assignment_id"),
        "class_offering_id": row.get("class_offering_id"),
        "title": row.get("title") or "",
        "reason": row.get("reason") or "",
        "payload": _json_loads(row.get("payload_json")),
        "decision_note": row.get("decision_note") or "",
        "decision_payload": _json_loads(row.get("decision_payload_json")),
        "decided_by_role": row.get("decided_by_role") or "",
        "decided_by_name": row.get("decided_by_name") or "",
        "decided_at": row.get("decided_at"),
        "expires_at": row.get("expires_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "approve_label": request_type.approve_label if request_type else "同意",
        "reject_label": request_type.reject_label if request_type else "拒绝",
    }
    if reviewers is not None:
        item["reviewers"] = [
            {"role": r["reviewer_role"], "id": r["reviewer_user_pk"], "name": r.get("reviewer_name") or ""}
            for r in reviewers
        ]
    if viewer is not None:
        item["can_decide"] = status == APPROVAL_STATUS_PENDING and bool(reviewers) and (
            _is_reviewer(reviewers, viewer) or bool(viewer.get("_is_super_admin"))
        )
        item["can_cancel"] = status == APPROVAL_STATUS_PENDING and _is_applicant(row, viewer)
    return item


# --------------------------------------------------------------------------- notifications
def _notify(conn, *, recipients: list[dict[str, Any]], actor: Mapping[str, Any] | None, title: str, body: str,
            request: Mapping[str, Any], ref_suffix: str, allow_duplicates: bool = False) -> int:
    request_type = _REGISTRY.get(str(request.get("request_type") or ""))
    actor_role, actor_pk = _identity(actor)
    seen: set[tuple[str, int]] = set()
    count = 0
    for recipient in recipients:
        role = str(recipient.get("role") or "").strip().lower()
        try:
            user_pk = int(recipient.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if role not in {"teacher", "student"} or user_pk <= 0 or (role, user_pk) in seen:
            continue
        if (role, user_pk) == (actor_role, actor_pk):
            continue
        seen.add((role, user_pk))
        link = request_type.inbox_link(request, role) if request_type and request_type.inbox_link else ""
        payload = message_center_service._build_notification_payload(
            recipient_role=role,
            recipient_user_pk=user_pk,
            category=MESSAGE_CATEGORY_APPROVAL,
            title=title,
            body_preview=message_center_service._truncate_text(body, 180),
            actor_role=actor_role if actor_role in {"teacher", "student"} else "",
            actor_user_pk=actor_pk or None,
            actor_display_name=_user_name(actor),
            link_url=link,
            class_offering_id=message_center_service._safe_int(request.get("class_offering_id")),
            ref_type=MESSAGE_CATEGORY_APPROVAL,
            ref_id=f"{request.get('request_type')}:{request.get('id')}:{ref_suffix}",
            metadata={"request_id": int(request["id"]), "request_type": request.get("request_type"),
                      "status": request.get("status"), "assignment_id": request.get("assignment_id")},
        )
        count += 1 if message_center_service._insert_notification_if_allowed(conn, payload, allow_duplicates=allow_duplicates) else 0
    return count


# --------------------------------------------------------------------------- operations
def create_request(conn, applicant: Mapping[str, Any], *, request_type: str, subject_id: Any,
                   reason: Any, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_approval_workflow_schema(conn)
    spec = get_request_type(request_type)
    role, user_pk = _identity(applicant)
    if role not in spec.applicant_roles or user_pk <= 0:
        raise ApprovalWorkflowError(403, "当前身份不能发起该申请")
    reason_text = _clean_text(reason, limit=REASON_MAX_CHARS, field_name="申请理由", required=True)
    prepared = spec.prepare(conn, applicant, str(subject_id or "").strip(), reason_text, dict(payload or {}))
    if not prepared.reviewers:
        raise ApprovalWorkflowError(422, "找不到可以审批该申请的教师")
    begin_immediate_transaction(conn)
    existing = conn.execute(
        "SELECT id FROM approval_requests WHERE dedupe_key = ? AND status = 'pending' LIMIT 1",
        (prepared.dedupe_key,),
    ).fetchone()
    if existing:
        raise ApprovalWorkflowError(409, "已有一条待审批的申请，请等待教师处理")
    now = _now_iso()
    request_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO approval_requests (
            request_type, status, applicant_role, applicant_user_pk, applicant_name,
            subject_type, subject_id, assignment_id, class_offering_id, title, reason,
            payload_json, dedupe_key, expires_at, created_at, updated_at
        ) VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (spec.key, role, user_pk, _user_name(applicant), spec.subject_type, prepared.subject_id,
         prepared.assignment_id, prepared.class_offering_id, prepared.title, reason_text,
         json.dumps(prepared.payload, ensure_ascii=False), prepared.dedupe_key, prepared.expires_at, now, now),
    )
    for reviewer in prepared.reviewers:
        reviewer_role, reviewer_pk = _identity(reviewer)
        if reviewer_pk <= 0:
            continue
        conn.execute(
            "INSERT INTO approval_request_reviewers (request_id, reviewer_role, reviewer_user_pk, reviewer_name, notified_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (request_id, reviewer_role, reviewer_user_pk) DO NOTHING",
            (request_id, reviewer_role, reviewer_pk, _user_name(reviewer), now, now),
        )
    _record_event(conn, request_id, "created", actor=applicant, note=reason_text, payload=prepared.payload)
    request = _load_request_row(conn, request_id)
    reviewers = _load_reviewers(conn, request_id)
    _notify(
        conn,
        recipients=[{"role": r["reviewer_role"], "id": r["reviewer_user_pk"]} for r in reviewers],
        actor=applicant,
        title=f"待审批：{prepared.title}",
        body=f"{_user_name(applicant) or '申请人'}：{reason_text}",
        request=request,
        ref_suffix="created",
    )
    return serialize_request(request, reviewers=reviewers, viewer=applicant)


def list_requests(conn, user: Mapping[str, Any], *, scope: str = "incoming", status: str = "",
                  assignment_id: Any = None, request_type: str = "", limit: int = 50) -> list[dict[str, Any]]:
    ensure_approval_workflow_schema(conn)
    role, user_pk = _identity(user)
    if user_pk <= 0:
        return []
    limit = max(1, min(int(limit or 50), 200))
    where: list[str] = []
    params: list[Any] = []
    scope = str(scope or "incoming").strip().lower()
    if scope == "mine":
        where.append("r.applicant_role = ? AND r.applicant_user_pk = ?")
        params.extend([role, user_pk])
    elif scope == "all" and _is_super_admin(conn, user):
        pass
    else:
        where.append("EXISTS (SELECT 1 FROM approval_request_reviewers v WHERE v.request_id = r.id "
                     "AND v.reviewer_role = ? AND v.reviewer_user_pk = ?)")
        params.extend([role, user_pk])
    if status:
        where.append("r.status = ?")
        params.append(str(status).strip().lower())
    if assignment_id not in (None, ""):
        where.append("r.assignment_id = ?")
        params.append(str(assignment_id))
    if request_type:
        where.append("r.request_type = ?")
        params.append(str(request_type))
    sql = "SELECT r.* FROM approval_requests r"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CASE WHEN r.status = 'pending' THEN 0 ELSE 1 END, r.created_at DESC, r.id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, tuple(params)).fetchall()
    viewer = {**dict(user), "_is_super_admin": _is_super_admin(conn, user)}
    items = []
    for row in rows:
        row = dict(row)
        items.append(serialize_request(row, reviewers=_load_reviewers(conn, int(row["id"])), viewer=viewer))
    return items


def count_pending_requests(conn, user: Mapping[str, Any], *, assignment_id: Any = None) -> int:
    ensure_approval_workflow_schema(conn)
    role, user_pk = _identity(user)
    if user_pk <= 0:
        return 0
    params: list[Any] = [role, user_pk]
    sql = ("SELECT COUNT(*) AS n FROM approval_requests r WHERE r.status = 'pending' AND EXISTS ("
           "SELECT 1 FROM approval_request_reviewers v WHERE v.request_id = r.id AND v.reviewer_role = ? AND v.reviewer_user_pk = ?)")
    if assignment_id not in (None, ""):
        sql += " AND r.assignment_id = ?"
        params.append(str(assignment_id))
    row = conn.execute(sql, tuple(params)).fetchone()
    return int((row["n"] if hasattr(row, "keys") else row[0]) or 0)


def get_request(conn, user: Mapping[str, Any], request_id: int | str) -> dict[str, Any]:
    ensure_approval_workflow_schema(conn)
    request = _load_request_row(conn, request_id)
    reviewers = _load_reviewers(conn, int(request["id"]))
    viewer = {**dict(user), "_is_super_admin": _is_super_admin(conn, user)}
    if not (_is_applicant(request, user) or _is_reviewer(reviewers, user) or viewer["_is_super_admin"]):
        raise ApprovalWorkflowError(403, "无权查看该申请")
    item = serialize_request(request, reviewers=reviewers, viewer=viewer)
    spec = _REGISTRY.get(str(request.get("request_type") or ""))
    if spec and spec.detail:
        item["detail"] = spec.detail(conn, request, user)
    events = conn.execute(
        "SELECT event_type, actor_role, actor_name, note, payload_json, created_at FROM approval_request_events "
        "WHERE request_id = ? ORDER BY id",
        (int(request["id"]),),
    ).fetchall()
    item["events"] = [
        {"event_type": e["event_type"], "actor_role": e["actor_role"], "actor_name": e["actor_name"],
         "note": e["note"], "payload": _json_loads(e["payload_json"]), "created_at": e["created_at"]}
        for e in events
    ]
    return item


def latest_request_for_subject(conn, *, request_type: str, subject_id: Any,
                               applicant: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    ensure_approval_workflow_schema(conn)
    params: list[Any] = [str(request_type), str(subject_id)]
    sql = "SELECT * FROM approval_requests WHERE request_type = ? AND subject_id = ?"
    if applicant:
        role, user_pk = _identity(applicant)
        sql += " AND applicant_role = ? AND applicant_user_pk = ?"
        params.extend([role, user_pk])
    sql += " ORDER BY CASE WHEN status = 'pending' THEN 0 ELSE 1 END, id DESC LIMIT 1"
    row = conn.execute(sql, tuple(params)).fetchone()
    return serialize_request(dict(row), viewer=applicant) if row else None


def decide_request(conn, reviewer: Mapping[str, Any], request_id: int | str, *, decision: str,
                   note: Any = "", decision_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_approval_workflow_schema(conn)
    decision = str(decision or "").strip().lower()
    if decision not in {"approve", "reject"}:
        raise ApprovalWorkflowError(400, "决定只能是通过或拒绝")
    request = _load_request_row(conn, request_id)
    spec = get_request_type(request["request_type"])
    reviewers = _load_reviewers(conn, int(request["id"]))
    if not (_is_reviewer(reviewers, reviewer) or _is_super_admin(conn, reviewer)):
        raise ApprovalWorkflowError(403, "你不是该申请的审批人")
    if request["status"] != APPROVAL_STATUS_PENDING:
        raise ApprovalWorkflowError(409, f"该申请已{STATUS_LABELS.get(request['status'], '处理')}，不能重复处理")
    note_text = _clean_text(note, limit=NOTE_MAX_CHARS, field_name="审批意见", required=(decision == "reject"))
    role, user_pk = _identity(reviewer)
    new_status = APPROVAL_STATUS_APPROVED if decision == "approve" else APPROVAL_STATUS_REJECTED
    now = _now_iso()
    begin_immediate_transaction(conn)
    cursor = conn.execute(
        "UPDATE approval_requests SET status = ?, decision_note = ?, decided_by_role = ?, decided_by_user_pk = ?, "
        "decided_by_name = ?, decided_at = ?, updated_at = ? WHERE id = ? AND status = 'pending'",
        (new_status, note_text, role, user_pk, _user_name(reviewer), now, now, int(request["id"])),
    )
    if int(cursor.rowcount or 0) != 1:
        raise ApprovalWorkflowError(409, "该申请刚刚已被处理，请刷新")
    request = _load_request_row(conn, request["id"])
    stored_payload: dict[str, Any] = {}
    if decision == "approve":
        stored_payload = dict(spec.on_approve(conn, request, reviewer, dict(decision_payload or {}), note_text) or {})
        conn.execute("UPDATE approval_requests SET decision_payload_json = ? WHERE id = ?",
                     (json.dumps(stored_payload, ensure_ascii=False), int(request["id"])))
    elif spec.on_reject:
        spec.on_reject(conn, request, reviewer, note_text)
    _record_event(conn, int(request["id"]), new_status, actor=reviewer, note=note_text, payload=stored_payload)
    request = _load_request_row(conn, request["id"])
    verb = "已通过" if decision == "approve" else "已拒绝"
    body = f"{_user_name(reviewer) or '教师'}{verb}你的申请。" + (f"意见：{note_text}" if note_text else "")
    extra = stored_payload.get("notification_suffix")
    if extra:
        body += f" {extra}"
    _notify(conn, recipients=[{"role": request["applicant_role"], "id": request["applicant_user_pk"]}],
            actor=reviewer, title=f"{verb}：{request['title']}", body=body, request=request, ref_suffix=new_status)
    # Other reviewers learn the item is closed so it disappears from their inbox with context.
    _notify(conn, recipients=[{"role": r["reviewer_role"], "id": r["reviewer_user_pk"]} for r in reviewers],
            actor=reviewer, title=f"申请{verb}（同事已处理）：{request['title']}",
            body=body, request=request, ref_suffix=f"{new_status}:peer")
    return serialize_request(request, reviewers=reviewers, viewer=reviewer)


def cancel_request(conn, applicant: Mapping[str, Any], request_id: int | str, *, note: Any = "") -> dict[str, Any]:
    ensure_approval_workflow_schema(conn)
    request = _load_request_row(conn, request_id)
    if not _is_applicant(request, applicant):
        raise ApprovalWorkflowError(403, "只有申请人可以撤销申请")
    if request["status"] != APPROVAL_STATUS_PENDING:
        raise ApprovalWorkflowError(409, "申请已处理，不能撤销")
    note_text = _clean_text(note, limit=NOTE_MAX_CHARS, field_name="备注")
    now = _now_iso()
    begin_immediate_transaction(conn)
    cursor = conn.execute(
        "UPDATE approval_requests SET status = 'cancelled', decision_note = ?, decided_at = ?, updated_at = ? "
        "WHERE id = ? AND status = 'pending'",
        (note_text, now, now, int(request["id"])),
    )
    if int(cursor.rowcount or 0) != 1:
        raise ApprovalWorkflowError(409, "该申请刚刚已被处理，请刷新")
    spec = _REGISTRY.get(str(request.get("request_type") or ""))
    request = _load_request_row(conn, request["id"])
    if spec and spec.on_cancel:
        spec.on_cancel(conn, request, applicant, note_text)
    _record_event(conn, int(request["id"]), "cancelled", actor=applicant, note=note_text)
    reviewers = _load_reviewers(conn, int(request["id"]))
    _notify(conn, recipients=[{"role": r["reviewer_role"], "id": r["reviewer_user_pk"]} for r in reviewers],
            actor=applicant, title=f"申请已撤销：{request['title']}", body=note_text or "申请人已撤销该申请。",
            request=request, ref_suffix="cancelled")
    return serialize_request(request, reviewers=reviewers, viewer=applicant)


def auto_cancel_requests(conn, *, request_type: str, subject_ids: list[str], note: str,
                         actor: Mapping[str, Any] | None = None) -> int:
    """Close pending requests whose business outcome was reached another way (no transaction of its own)."""
    if not subject_ids:
        return 0
    ensure_approval_workflow_schema(conn)
    placeholders = ",".join("?" for _ in subject_ids)
    select_sql = f"SELECT * FROM approval_requests WHERE request_type = ? AND status = 'pending' AND subject_id IN ({placeholders})"
    params = (str(request_type), *[str(item) for item in subject_ids])
    try:
        rows = conn.execute(select_sql, params).fetchall()
    except Exception as exc:  # noqa: BLE001
        # A connection to a database created after the process-wide "schema
        # ready" flag was set (fresh test fixtures, a rebuilt runtime) has no
        # approval tables yet; ensure them once more before giving up.
        if "approval_requests" not in str(exc):
            raise
        import classroom_app.services.approval_workflow_schema as _schema

        _schema._SCHEMA_READY = False
        ensure_approval_workflow_schema(conn)
        rows = conn.execute(select_sql, params).fetchall()
    now = _now_iso()
    closed = 0
    actor_role, actor_pk = _identity(actor)
    for row in rows:
        request = dict(row)
        cursor = conn.execute(
            "UPDATE approval_requests SET status = 'cancelled', decision_note = ?, decided_by_role = ?, decided_by_user_pk = ?, "
            "decided_by_name = ?, decided_at = ?, updated_at = ? WHERE id = ? AND status = 'pending'",
            (note, actor_role, actor_pk or None, _user_name(actor), now, now, int(request["id"])),
        )
        if int(cursor.rowcount or 0) != 1:
            continue
        closed += 1
        _record_event(conn, int(request["id"]), "auto_cancelled", actor=actor, note=note)
        request = _load_request_row(conn, request["id"])
        _notify(conn, recipients=[{"role": request["applicant_role"], "id": request["applicant_user_pk"]}],
                actor=actor, title=f"申请已结束：{request['title']}", body=note, request=request, ref_suffix="auto_cancelled")
    return closed


def remind_stale_requests(conn, *, now: datetime | None = None) -> dict[str, int]:
    """Nudge reviewers once after ``reminder_hours``; expire after ``expire_days``."""
    ensure_approval_workflow_schema(conn)
    now_dt = (now or datetime.now()).replace(microsecond=0)
    rows = conn.execute("SELECT * FROM approval_requests WHERE status = 'pending' ORDER BY id").fetchall()
    reminded = expired = 0
    for row in rows:
        request = dict(row)
        spec = _REGISTRY.get(str(request.get("request_type") or ""))
        if spec is None:
            continue
        try:
            created = datetime.fromisoformat(str(request.get("created_at") or "").replace(" ", "T").replace("Z", ""))
        except ValueError:
            continue
        created = created.replace(tzinfo=None)
        age = now_dt - created
        if age >= timedelta(days=spec.expire_days):
            stamp = now_dt.isoformat()
            cursor = conn.execute(
                "UPDATE approval_requests SET status = 'expired', decision_note = ?, decided_at = ?, updated_at = ? "
                "WHERE id = ? AND status = 'pending'",
                ("超过处理期限未审批，申请自动过期，可重新发起。", stamp, stamp, int(request["id"])),
            )
            if int(cursor.rowcount or 0) == 1:
                expired += 1
                _record_event(conn, int(request["id"]), "expired", note="reminder sweep")
                request = _load_request_row(conn, request["id"])
                _notify(conn, recipients=[{"role": request["applicant_role"], "id": request["applicant_user_pk"]}],
                        actor=None, title=f"申请已过期：{request['title']}",
                        body="教师超过处理期限未审批，申请已自动过期，如仍需要可重新发起。", request=request, ref_suffix="expired")
            continue
        if age >= timedelta(hours=spec.reminder_hours):
            reviewers = [r for r in _load_reviewers(conn, int(request["id"])) if not r.get("reminded_at")]
            if not reviewers:
                continue
            reminded += _notify(
                conn, recipients=[{"role": r["reviewer_role"], "id": r["reviewer_user_pk"]} for r in reviewers],
                actor=None, title=f"待处理提醒：{request['title']}",
                body=f"{request.get('applicant_name') or '申请人'}的申请已等待超过 {spec.reminder_hours} 小时，请尽快处理。",
                request=request, ref_suffix="reminder",
            )
            conn.execute("UPDATE approval_request_reviewers SET reminded_at = ? WHERE request_id = ? AND reminded_at IS NULL",
                         (now_dt.isoformat(), int(request["id"])))
            _record_event(conn, int(request["id"]), "reminded", note="reminder sweep")
    return {"reminded": reminded, "expired": expired}


def ensure_approval_reminder_task(conn) -> int:
    from .scheduled_task_service import schedule_task

    return schedule_task(
        conn,
        task_kind=TASK_KIND_APPROVAL_REMINDER,
        run_at=datetime.now() + timedelta(seconds=900),
        payload={},
        dedupe_key="approval:request-reminder",
        recurrence_seconds=REMINDER_INTERVAL_SECONDS,
        title="Approval request reminder sweep",
        priority=70,
        max_attempts=3,
        replace=False,
    )
