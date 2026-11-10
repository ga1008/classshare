"""Response contracts and read-only evidence for ordinary platform commands."""
from __future__ import annotations
import json

from .agent_platform_route_capability import route_key

PENDING = {"queued", "running", "retry_wait", "result_ready", "pending", "processing"}
FAILED = {"failed", "dead_letter", "review_required", "cancelled", "superseded", "expired", "rejected"}


def observe_chat_stream(response, observation):
    answer, completed, error = [], False, ""
    try:
        for line in response.text.splitlines():
            if not line.strip(): continue
            event = json.loads(line)
            if not isinstance(event, dict): raise ValueError("bad event")
            kind = event.get("event")
            if kind == "answer_delta": answer.append(str(event.get("delta") or ""))
            elif kind == "done": completed = True
            elif kind == "error": error = str(event.get("message") or "model_request_failed")[:400]
    except (ValueError, TypeError):
        return "uncertain", {**observation, "follow_up": "invalid_native_stream"}
    if not completed:
        return "uncertain", {**observation, "follow_up": "incomplete_native_stream"}
    return "observed_http_result", {**observation, "data": {"ok": not bool(error), "answer": "".join(answer), "error": error},
                                     "follow_up": "native_stream_completed"}


def _social_response_contract(operation, data):
    if not isinstance(data, dict):
        return False
    path = operation.path
    if path == "/api/social/groups" or path in {"/api/social/groups/{group_id}", "/api/social/groups/{group_id}/transfer"}:
        return type(data.get("id")) is int and data["id"] > 0 and isinstance(data.get("conversation_key"), str)
    if path == "/api/social/groups/{group_id}/members":
        return isinstance(data.get("added"), list) and isinstance(data.get("skipped"), list)
    if path.endswith("/leave"):
        return type(data.get("group_id")) is int and data.get("left") is True
    if path.endswith("/dissolve"):
        return type(data.get("group_id")) is int and data.get("dissolved") is True
    if path == "/api/social/groups/{group_id}/members/{identity}":
        return type(data.get("group_id")) is int and isinstance(data.get("removed"), str)
    if path.startswith("/api/social/friends/requests"):
        return type(data.get("request_id")) is int and data.get("status") in {"pending", "accepted", "declined", "withdrawn"}
    if path == "/api/social/friends/{identity}":
        return data.get("status") == "deleted" and isinstance(data.get("identity"), str)
    if path in {"/api/social/blocks", "/api/social/blocks/{identity}"}:
        return isinstance(data.get("identity"), str) and type(data.get("blocked")) is bool
    if path == "/api/social/messages/{message_id}/recall":
        return type(data.get("id")) is int and data.get("recalled") is True
    if path == "/api/social/conversations/{key}/read":
        return isinstance(data.get("conversation_key"), str) and type(data.get("last_message_id")) is int
    return False


def interpret_platform_job_result(receipt: dict, job_identity: dict) -> dict:
    """Poll results must refer to the exact accepted job, never its replacement."""
    payload = (receipt.get("result") or {}).get("data") or {}
    if job_identity.get("kind") == "career_jobs":
        tasks = payload.get("tasks") or {} if isinstance(payload, dict) else {}
        statuses = []
        for name, identifier in (job_identity.get("jobs") or {}).items():
            job = tasks.get(name) or {}
            if str(job.get("id") or job.get("job_id")) != str(identifier):
                return {**receipt, "status": "uncertain", "error": "accepted_job_identity_changed_or_unavailable"}
            statuses.append(str(job.get("status") or ""))
        status = ("failed" if any(state in FAILED for state in statuses) else "submitted" if any(state in PENDING for state in statuses)
                  else "succeeded" if statuses and all(state in {"succeeded", "ready"} for state in statuses) else "uncertain")
        return {**receipt, "status": status, "verified_business": status == "succeeded", "job_identity": job_identity}
    job = payload.get("job") if isinstance(payload, dict) else None
    if not isinstance(job, dict) or str(job.get("id") or job.get("job_id")) != str(job_identity.get("id")):
        return {**receipt, "status": "uncertain", "error": "accepted_job_identity_changed_or_unavailable"}
    state = str(job.get("status") or "")
    if state == "succeeded":
        return {**receipt, "status": "succeeded", "verified_business": True, "job_identity": job_identity}
    if state in FAILED:
        return {**receipt, "status": "failed", "job_identity": job_identity, "error": job.get("error_code") or state}
    return {**receipt, "status": "submitted" if state in PENDING else "uncertain", "job_identity": job_identity}


def _job_follow_up(operation, args, data):
    if operation.path.startswith("/api/career-path/") and isinstance(data, dict):
        jobs = {name: item.get("id") or item.get("job_id") for name, item in (data.get("tasks") or {}).items()
                if isinstance(item, dict) and (item.get("id") or item.get("job_id")) and item.get("status") in PENDING}
        if jobs:
            return {"follow_up": {"capability": route_key("GET", "/api/career-path/state"), "args": {}},
                    "job_identity": {"kind": "career_jobs", "jobs": jobs}}
    job = data.get("job") if isinstance(data, dict) else None
    if not isinstance(job, dict) or not (job.get("id") or job.get("job_id")):
        return None
    identifier = job.get("id") or job.get("job_id")
    params = args.get("path_params") or {}
    if job.get("task_type") == "resume_suggestion":
        path, values = "/api/resume/suggestions/jobs/{job_id}", {"job_id": identifier}
    elif operation.path.startswith("/api/resume/self-intro"):
        path, values = "/api/resume/self-intro/{intro_id}/job", {"intro_id": params.get("intro_id") or data.get("id")}
    elif operation.path.startswith("/api/resume/resumes") or operation.path == "/api/resume/import":
        path, values = "/api/resume/resumes/{resume_id}/job", {"resume_id": params.get("resume_id") or data.get("id")}
    else:
        return None
    if any(value is None for value in values.values()):
        return None
    return {"follow_up": {"capability": route_key("GET", path), "args": {"path_params": values}},
            "job_identity": {"kind": "ai_job", "id": identifier, "task_type": job.get("task_type")}}


def _verified_message_delivery(conn, source, operation, args, receipt):
    """A lost HTTP reply can be recovered only from committed native evidence."""
    if operation.method != 'POST' or operation.path not in {
            '/api/social/conversations/{key}/messages', '/api/message-center/private/messages'}:
        return None
    from .platform_execution_authority import verify_platform_authority
    from .wanderer_message_delivery_service import verified_social_delivery, verified_private_delivery
    actor = verify_platform_authority(conn, source, required_scope='platform:read').actor
    observed = receipt.get('result') or {}
    data = observed.get('data') or {}
    if not isinstance(data, dict):
        data = {}
    if operation.path == '/api/social/conversations/{key}/messages':
        delivery = verified_social_delivery(conn, source, actor, receipt['operation_id'],
            (args.get('path_params') or {}).get('key'), args.get('body') or {}, data.get('id'))
        if delivery is None:
            return None
        from .social_conversation_service import serialize_messages
        message, metadata = delivery
        replayed = bool((data.get('delivery') or {}).get('reused'))
        data = {**data, **serialize_messages(conn, actor.key, [message])[0], **metadata}
        kind = 'native_message'
    else:
        delivery = verified_private_delivery(conn, source, actor, receipt['operation_id'],
            (args.get('body') or {}).get('contact_identity'), (args.get('body') or {}).get('content'),
            (data.get('sent_message') or {}).get('id'))
        if delivery is None:
            return None
        from .message_center_service import _serialize_private_message, _load_private_message_attachments
        message, metadata = delivery
        replayed = data.get('replayed') is True
        serialized = _serialize_private_message(message, current_identity=actor.key, blocked_identities=set(),
            attachments_by_message=_load_private_message_attachments(conn, [int(message['id'])]))
        data = {**data, 'sent_message': serialized, 'conversation_key': message['conversation_key'], **metadata}
        kind = 'native_private_message'
    if replayed:
        from .wanderer_message_delivery_service import delivery_metadata
        metadata['delivery'].update(delivery_metadata(
            {'operation_id': receipt['operation_id'], 'delivery_kind': 'reused'}, message['id'])['delivery'])
    if receipt.get('status') != 'observed_http_result':
        metadata['delivery']['recovered_from_committed_message'] = True
    return {**receipt, 'execution_status': receipt.get('status'), 'status': 'succeeded',
            'verified_business': True, 'delivery': metadata['delivery'],
            'result': {**observed, 'data': data},
            'completion_evidence': {'kind': kind, 'id': int(message['id']),
                'operation_id': receipt['operation_id'], 'reused': metadata['delivery']['reused']}}


def normalize_platform_result(conn, source, operation, args, receipt):
    result = {**receipt, "execution_status": receipt.get("status")}
    observed = receipt.get("result") or {}
    data = observed.get("data")
    http = observed.get("http_status")
    if observed.get("outcome") == "rejected" or (isinstance(http, int) and 400 <= http < 500):
        return {**result, "status": "failed", "error": observed.get("error_detail") or "platform_request_rejected"}
    delivered = _verified_message_delivery(conn, source, operation, args, receipt)
    if delivered is not None:
        return delivered
    if operation.method == 'POST' and operation.path in {
            '/api/social/conversations/{key}/messages', '/api/message-center/private/messages'}:
        known = conn.execute('SELECT operation_id FROM wanderer_message_deliveries WHERE operation_id=?',
                             (receipt['operation_id'],)).fetchone()
        if known:
            return {**result, 'status': 'uncertain', 'error': 'native_delivery_no_longer_readable_or_matching'}
    tracker = _job_follow_up(operation, args, data)
    if tracker:
        return {**interpret_platform_job_result(result, tracker["job_identity"]), **tracker}
    if isinstance(data, dict) and isinstance(data.get("job"), dict) and data["job"].get("status") in PENDING:
        return {**result, "status": "submitted", "error": "job_tracker_required",
                "job_identity": {"kind": "ai_job", "id": data["job"].get("id") or data["job"].get("job_id"), "task_type": data["job"].get("task_type")}}
    if receipt.get("status") not in {"observed_http_result", "submitted"}:
        return result
    if receipt.get("status") == "submitted":
        return {**result, "status": "submitted", "error": "job_tracker_required"}
    if isinstance(data, dict) and (data.get("ok") is False or data.get("success") is False):
        return {**result, "status": "failed", "error": data.get("error") or data.get("message") or "business_rejected"}
    if operation.method == "POST" and operation.path == "/api/social/conversations/{key}/messages":
        from .platform_execution_authority import verify_platform_authority
        actor = verify_platform_authority(conn, source, required_scope="platform:read").actor
        row = conn.execute("SELECT id FROM social_messages WHERE id=? AND sender_identity=? AND conversation_key=? AND client_nonce=? AND recalled_at IS NULL",
            ((data or {}).get("id"), actor.key, (args.get("path_params") or {}).get("key"), receipt["operation_id"])).fetchone()
        if row:
            return {**result, "status": "succeeded", "verified_business": True,
                    "completion_evidence": {"kind": "native_message", "id": int(row["id"]), "nonce": receipt["operation_id"]}}
        return {**result, "status": "uncertain", "error": "native_message_not_verified"}
    if operation.method == "POST" and operation.path == "/api/message-center/private/messages":
        from .platform_execution_authority import verify_platform_authority
        actor = verify_platform_authority(conn, source, required_scope="platform:read").actor
        sent = (data or {}).get("sent_message") or {}
        row = conn.execute("SELECT m.id FROM wanderer_private_message_operations o "
            "JOIN private_messages m ON m.id=o.message_id WHERE o.operation_id=? "
            "AND o.wanderer_id=? AND m.id=? AND m.sender_identity=? AND m.recipient_identity=?",
            (receipt["operation_id"], source.wanderer_id, sent.get("id"), actor.key,
             (args.get("body") or {}).get("contact_identity"))).fetchone()
        if row:
            return {**result, "status": "succeeded", "verified_business": True,
                    "completion_evidence": {"kind": "native_private_message", "id": int(row["id"]), "operation_id": receipt["operation_id"]}}
        return {**result, "status": "uncertain", "error": "native_private_message_not_verified"}
    # A normal route's explicit success contract is a completed command. Keep
    # independent business verification separate from the HTTP evidence.
    if not operation.mutates or (isinstance(data, dict) and
            (data.get("ok") is True or data.get("success") is True or data.get("status") in {"ok", "success"})) or _social_response_contract(operation, data):
        return {**result, "status": "succeeded", "completion_evidence": "native_business_response"}
    return {**result, "status": "uncertain", "error": "business_result_requires_verification"}
