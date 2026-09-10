"""Durable admission + observed normal HTTP results, with no exactly-once claim.

Normal routers may commit internally. The initial claim therefore commits
before dispatch; a missing response remains uncertain and is never retried
automatically, even under another operation id. A process-private settlement
capability can record the final observation after its task authority expires.
It cannot authorize another request or business mutation.
"""
import asyncio
from dataclasses import dataclass, field
import hashlib
import json
import secrets
import threading
import time
import uuid

from fastapi import HTTPException
import httpx
from starlette.concurrency import run_in_threadpool

from ..database import get_db_connection
from .agent_delegation_service import verify_task_delegation
from .agent_platform_request_context import _RequestIdentity, _request_identity, _SCOPE_KEY
from .agent_platform_request_registry import arguments, matched_route, resolve_capability, platform_request_catalog
from .agent_request_context import _broker_identity

MAX_RESPONSE_BYTES = 128 * 1024
# Waiting threshold, not a hard execution deadline. Native router threads cannot
# be interrupted safely: cancellation drains them and retains admission slots.
WAIT_TIMEOUT_SECONDS = 15
_CAPACITY = threading.BoundedSemaphore(2)
_EXECUTION_HOST_ID = str(uuid.uuid4())


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def _intent_body(operation, operation_id, body):
    field_name = operation.server_operation_id_field
    if field_name is None:
        return body
    payload = json.loads(body)
    if not isinstance(payload, dict) or payload.get(field_name) != operation_id:
        raise HTTPException(400, '下游保存编号必须由平台请求编号派生。')
    # Only the reviewed idempotency metadata is excluded from business intent.
    # Exact body/parameters, including this value, still bind request+nonce.
    del payload[field_name]
    return _canonical(payload).encode()


@dataclass(frozen=True)
class _Settlement:
    request_id: str
    secret: str = field(repr=False)


def _public(row):
    return {'request_id': row['id'], 'operation_id': row['operation_id'], 'capability_key': row['capability_key'],
            'status': row['status'], 'verified_business': False,
            'mutates': bool(row['mutates']), 'host_execution_finished': row['host_execution_finished_at'] is not None,
            'reconciliation_status': row['reconciliation_status'], 'reconciliation_resolution': row['reconciliation_resolution'],
            'result': json.loads(row['result_json']), 'automatic_retry_allowed': False}


def _admit(token, operation, operation_id, path, query, body, normalized):
    try:
        if str(uuid.UUID(operation_id)) != operation_id: raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, '平台请求操作编号必须为 UUID。') from None
    required_scope = 'platform:write' if operation.mutates else 'platform:read'
    now = int(time.time())
    with get_db_connection() as conn:
        grant = verify_task_delegation(conn, token, purpose='tools', required_scope=required_scope, lock_task=True)
        if not grant.delegation.get('source_session_hash'):
            raise HTTPException(403, '该平台请求能力要求当前登录会话授权。')
        # Use the same task -> actor session -> request lock order as manual
        # reconciliation, so an old grant cannot race the intent lock release.
        conn.execute('UPDATE user_sessions SET expires_at=expires_at WHERE session_user_key=?', (grant.actor.key,))
        grant = verify_task_delegation(conn, token, purpose='tools', required_scope=required_scope, lock_task=True)
        request_json = _canonical({'capability_key': operation.key, 'method': operation.method, 'path': path,
                                   'query': query.decode(), 'body_hash': _hash(body), 'parameters': normalized})
        request_hash = _hash(request_json)
        intent_hash = _hash(_canonical([operation.key, operation.method, path, query.decode(), _hash(_intent_body(operation, operation_id, body))]))
        prior = conn.execute('SELECT * FROM agent_platform_requests WHERE actor_role=? AND actor_id=? AND operation_id=?',
                             (grant.actor.role, grant.actor.id, operation_id)).fetchone()
        if prior:
            if (prior['request_hash'] != request_hash or int(prior['task_id']) != int(grant.task['id'])
                    or prior['source_session_hash'] != grant.delegation['source_session_hash']
                    or prior['authority_fingerprint'] != grant.actor.authority_fingerprint
                    or prior['route_source_sha256'] != operation.source_sha256):
                raise HTTPException(409, '该操作编号已绑定其他请求或权限来源。')
            return None, grant, _public(prior)
        # A new task/session/operation id cannot erase an unresolved mutation.
        # After human reconciliation only a future task may make another call.
        unknown = conn.execute("""SELECT id FROM agent_platform_requests WHERE actor_role=? AND actor_id=? AND intent_hash=?
            AND mutates=1 AND ((reconciliation_status='pending' AND status IN ('admitted','executing','submitted','uncertain'))
              OR (task_id=? AND reconciliation_status='cleared'))""",
            (grant.actor.role, grant.actor.id, intent_hash, grant.task['id'])).fetchone() if operation.mutates else None
        if unknown is not None:
            raise HTTPException(409, {'message': '相同意图的请求尚未结清，不能另换编号重试。', 'request_id': unknown['id'], 'requires_reconciliation': True})
        if operation.mutates:
            cleared = conn.execute("""SELECT MAX(reconciled_at) AS latest FROM agent_platform_requests
                WHERE actor_role=? AND actor_id=? AND intent_hash=? AND reconciliation_status='cleared'""",
                (grant.actor.role,grant.actor.id,intent_hash)).fetchone()
            if cleared['latest'] is not None and int(grant.delegation['created_at']) <= int(cleared['latest']):
                raise HTTPException(409, '人工核对后的操作需要新任务和晚于核对时间的新授权。')
        claim = _Settlement(str(uuid.uuid4()), secrets.token_urlsafe(32))
        inserted = conn.execute("""INSERT INTO agent_platform_requests
          (id,operation_id,task_id,attempt_id,fencing_token,delegation_id,actor_role,actor_id,
           source_session_hash,authority_fingerprint,capability_key,method,path,route_source_sha256,
           request_hash,intent_hash,request_json,settlement_hash,mutates,execution_host_id,status,created_at,updated_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'admitted',?,?) ON CONFLICT DO NOTHING""",
          (claim.request_id,operation_id,grant.task['id'],grant.attempt['id'],grant.attempt['fencing_token'],
           grant.delegation['id'],grant.actor.role,grant.actor.id,grant.delegation['source_session_hash'],
           grant.actor.authority_fingerprint,operation.key,operation.method,path,operation.source_sha256,
           request_hash,intent_hash,request_json,_hash(claim.secret),int(operation.mutates),_EXECUTION_HOST_ID,now,now))
        if inserted.rowcount != 1: raise HTTPException(409, '该操作编号或未结清意图已被其他请求占用。')
        conn.commit()  # This durable boundary precedes every original router call.
    return claim, grant, None


def _mark_executing(claim, token, required_scope):
    with get_db_connection() as conn:
        verified = verify_task_delegation(conn, token, purpose='tools', required_scope=required_scope, lock_task=True)
        changed = conn.execute("""UPDATE agent_platform_requests SET status='executing',updated_at=?
            WHERE id=? AND settlement_hash=? AND status='admitted' AND delegation_id=? AND attempt_id=? AND fencing_token=?""",
            (int(time.time()),claim.request_id,_hash(claim.secret),verified.delegation['id'],verified.attempt['id'],verified.attempt['fencing_token']))
        if changed.rowcount != 1: raise HTTPException(409, '该请求不能再次进入执行。')
        conn.commit()


def _uncertain(claim, reason):
    with get_db_connection() as conn:
        conn.execute("""UPDATE agent_platform_requests SET status='uncertain',result_json=?,updated_at=?
            WHERE id=? AND settlement_hash=? AND settled_at IS NULL""",
            (_canonical({'outcome':'uncertain','reason':reason,'follow_up':'reconcile_existing_request_no_automatic_retry'}),
             int(time.time()),claim.request_id,_hash(claim.secret)))
        conn.commit()


def mark_abandoned_platform_requests_uncertain(conn, *, task_id, attempt_id):
    """Trusted lifecycle hook. Does not execute/retry or fabricate a response."""
    return conn.execute("""UPDATE agent_platform_requests SET status='uncertain',updated_at=?,result_json=?
        WHERE task_id=? AND attempt_id=? AND status IN ('admitted','executing') AND settled_at IS NULL""",
        (int(time.time()),_canonical({'outcome':'uncertain','reason':'attempt_ended_before_observation',
          'follow_up':'reconcile_existing_request_no_automatic_retry'}),int(task_id),str(attempt_id))).rowcount


def _settle(claim, status, observation):
    if status not in {'observed_http_result','submitted','uncertain'}: raise ValueError('Invalid observation state')
    encoded = _canonical(observation)
    if len(encoded.encode()) > MAX_RESPONSE_BYTES + 8192: raise ValueError('Observation exceeds receipt bound')
    now = int(time.time())
    with get_db_connection() as conn:
        changed = conn.execute("""UPDATE agent_platform_requests SET status=?,result_json=?,updated_at=?,settled_at=?
            WHERE id=? AND settlement_hash=? AND settled_at IS NULL""",
            (status,encoded,now,now,claim.request_id,_hash(claim.secret)))
        if changed.rowcount != 1: raise HTTPException(409, '平台请求结算能力无效或已经用过。')
        conn.commit()
        row = conn.execute('SELECT * FROM agent_platform_requests WHERE id=?', (claim.request_id,)).fetchone()
        return _public(row)


def _finish_host_execution(claim):
    """Process-owned completion fact; timeouts/reapers cannot synthesize it."""
    with get_db_connection() as conn:
        conn.execute("""UPDATE agent_platform_requests SET host_execution_finished_at=?,updated_at=?
            WHERE id=? AND settlement_hash=? AND execution_host_id=? AND host_execution_finished_at IS NULL""",
            (int(time.time()),int(time.time()),claim.request_id,_hash(claim.secret),_EXECUTION_HOST_ID))
        conn.commit()


def get_platform_request_receipt(token, operation_id):
    with get_db_connection() as conn:
        grant = verify_task_delegation(conn, token, purpose='tools', required_scope='platform:read')
        row = conn.execute('SELECT * FROM agent_platform_requests WHERE actor_role=? AND actor_id=? AND task_id=? AND operation_id=?',
                          (grant.actor.role,grant.actor.id,grant.task['id'],str(operation_id))).fetchone()
        if row is None: raise HTTPException(404, '当前任务中没有该平台请求。')
        if row['source_session_hash'] != grant.delegation['source_session_hash'] or row['authority_fingerprint'] != grant.actor.authority_fingerprint:
            raise HTTPException(401, '当前权限来源不允许重放历史请求结果。')
        return _public(row)


def _authorize_return(token, original, required_scope):
    # A host-owned receipt may settle after revocation, but revoked model
    # callers must not receive private response content from the old request.
    with get_db_connection() as conn:
        current = verify_task_delegation(conn,token,purpose='tools',required_scope=required_scope)
    if (current.actor.key != original.actor.key or current.actor.authority_fingerprint != original.actor.authority_fingerprint
            or current.attempt['id'] != original.attempt['id']
            or current.attempt['fencing_token'] != original.attempt['fencing_token']
            or current.delegation['source_session_hash'] != original.delegation['source_session_hash']):
        raise HTTPException(401,'当前权限来源不允许读取刚完成的请求结果。')


def _observation(response, operation=None):
    result = {'http_status':response.status_code,'body_sha256':_hash(response.content),
              'verified_business':False, 'follow_up':'inspect_observed_result'}
    if not 200 <= response.status_code < 300:
        result['follow_up'] = 'needs_interaction' if 300 <= response.status_code < 400 else 'reconcile_http_error_no_automatic_retry'
        if response.status_code == 413 and operation is not None and operation.key in {'http.materials.content.read', 'http.materials.content.save'}:
            result['follow_up'] = 'use_file_workflow_or_smaller_material_source_no_automatic_retry'
        return 'uncertain', result
    if 'application/json' not in response.headers.get('content-type','').lower():
        result['follow_up'] = 'needs_response_adapter'
        return 'uncertain', result
    try: payload = response.json()
    except ValueError: return 'uncertain', {**result,'follow_up':'needs_response_adapter'}
    if not isinstance(payload,dict): return 'uncertain', {**result,'follow_up':'needs_response_adapter'}
    result['data'] = payload
    if response.status_code == 202:
        return 'submitted', {**result,'follow_up':'needs_job_tracker_not_completed'}
    known_success = payload.get('status') in ('ok','success') and payload.get('success') is not False
    if operation is not None and operation.response_contract == 'assignment_draft':
        from .agent_platform_multipart_service import assignment_draft_response
        known_success = assignment_draft_response(payload)
    if operation is not None and operation.response_contract == 'blog_report_resolution':
        known_success = (set(payload) == {'status', 'id'} and payload.get('status') in ('resolved', 'dismissed')
                         and type(payload.get('id')) is int and payload['id'] > 0)
    if operation is not None and operation.response_contract == 'signature_collection':
        from .agent_platform_request_signatures import collection_response
        known_success = collection_response(payload, operation.key)
    if not known_success:
        return 'uncertain', {**result,'follow_up':'needs_response_adapter'}
    return 'observed_http_result', result


async def _execute(app, token, operation_id, operation, route, path, query, body, normalized, admission, files=None):
    content_type = 'application/json'
    if operation.transport == 'form':
        def prepare_form():
            from .agent_platform_multipart_service import encode_form_upload
            with get_db_connection() as conn:
                grant = verify_task_delegation(conn, token, purpose='tools', required_scope='platform:write')
                return encode_form_upload(conn, grant, operation, normalized, files)
        body, content_type, normalized = await run_in_threadpool(prepare_form)
    if admission.get('stop_requested'):
        raise asyncio.CancelledError
    claim, grant, prior = await run_in_threadpool(_admit,token,operation,operation_id,path,query,body,normalized)
    admission['claim'] = claim
    if prior is not None: return prior
    required_scope = 'platform:write' if operation.mutates else 'platform:read'
    identity = _RequestIdentity(grant.delegation['id'],grant.actor.role,grant.actor.id,int(grant.task['id']),
        grant.attempt['id'],int(grant.attempt['fencing_token']),grant.delegation['source_session_hash'],
        grant.actor.authority_fingerprint,operation_id,operation.method,path,query,_hash(body),route,object(),required_scope)
    marker = _request_identity.set(identity)
    receipt = None
    try:
        if admission.get('stop_requested'):
            raise asyncio.CancelledError
        await run_in_threadpool(_mark_executing,claim,token,required_scope)
        async def bounded_app(scope, receive, send):
            if admission.get('stop_requested'):
                raise asyncio.CancelledError
            if (scope.get('type')!='http' or scope.get('method')!=operation.method or scope.get('path')!=path
                    or scope.get('query_string',b'')!=query or matched_route(app,scope) is not route):
                raise HTTPException(403,'实际路由与持久请求声明不一致。')
            # Consume and verify the exact body before any normal dependencies
            # or handler can run. No body/header fields can mint this nonce.
            received = bytearray()
            while True:
                message = await receive()
                if message['type'] != 'http.request': raise HTTPException(400,'平台请求未完整送达。')
                received.extend(message.get('body',b''))
                if len(received)>len(body): raise HTTPException(403,'平台请求内容发生变化。')
                if not message.get('more_body',False): break
            if bytes(received)!=body: raise HTTPException(403,'平台请求内容发生变化。')
            scope[_SCOPE_KEY] = (identity.nonce,operation_id,identity.body_hash)
            first = True
            total = 0
            async def bound_receive():
                nonlocal first
                if first:
                    first=False
                    return {'type':'http.request','body':body,'more_body':False}
                return await receive()
            async def bound_send(message):
                nonlocal total
                if message['type']=='http.response.body':
                    total += len(message.get('body',b''))
                    if total>MAX_RESPONSE_BYTES: raise ValueError('HTTP observation exceeds bound')
                await send(message)
            if admission.get('stop_requested'):
                raise asyncio.CancelledError
            await app(scope,bound_receive,bound_send)
        transport = httpx.ASGITransport(app=bounded_app,raise_app_exceptions=True)
        async with httpx.AsyncClient(transport=transport,base_url='http://lanshare-agent.internal',follow_redirects=False) as client:
            response = await client.request(operation.method,path + ('?' + query.decode() if query else ''),content=body,
                headers={'accept':'application/json','accept-encoding':'identity',**({'content-type':content_type} if body else {})})
        status,observation = _observation(response, operation)
        receipt = await run_in_threadpool(_settle,claim,status,observation)
        await run_in_threadpool(_authorize_return,token,grant,required_scope)
        return receipt
    except BaseException:
        await run_in_threadpool(_uncertain,claim,'request_did_not_produce_a_settled_observation')
        raise
    finally:
        try:
            await run_in_threadpool(_finish_host_execution,claim)
            if receipt is not None: receipt['host_execution_finished'] = True
        finally:
            identity.active=False
            _request_identity.reset(marker)


async def _drain(work):
    while not work.done():
        try: await asyncio.shield(work)
        except asyncio.CancelledError: continue
        except Exception: break
    if not work.cancelled(): work.exception()


async def dispatch_platform_request(app, token, capability_key, operation_id, *, path_params=None, query_params=None, body=None, files=None):
    """MCP-facing seam; accepts a reviewed key and bounded parameters, no URL/header."""
    if _request_identity.get() is not None or _broker_identity.get() is not None:
        raise HTTPException(403,'不允许嵌套 Agent 平台请求。')
    operation,route = resolve_capability(app,capability_key)
    if files is not None and (operation.transport != 'form' or not operation.allows_files):
        raise HTTPException(400,'该平台能力不支持任务附件。')
    if operation.transport not in {'json', 'form'}:
        raise HTTPException(503,'该平台能力的请求格式尚未接入。')
    path,query,raw,normalized = arguments(operation,path_params=path_params,query_params=query_params,body=body)
    if operation.server_operation_id_field is not None:
        if operation.transport != 'json':
            raise HTTPException(503,'下游保存编号只支持经过审核的 JSON 请求。')
        payload = json.loads(raw) if raw else {}
        payload[operation.server_operation_id_field] = operation_id
        normalized = {**normalized, 'body': payload}
        raw = _canonical(payload).encode()
        if len(raw) > operation.max_body_bytes:
            raise HTTPException(400,'平台请求内容过长。')
    if not _CAPACITY.acquire(blocking=False): raise HTTPException(429,'Agent 平台操作繁忙，请等待现有请求结清。')
    admission = {}
    work = asyncio.create_task(_execute(app,token,operation_id,operation,route,path,query,raw,normalized,admission,files))
    try:
        try: return await asyncio.wait_for(asyncio.shield(work),timeout=WAIT_TIMEOUT_SECONDS)
        except (TimeoutError,asyncio.CancelledError) as error:
            admission['stop_requested'] = True
            claim=admission.get('claim')
            if claim: await run_in_threadpool(_uncertain,claim,'caller_stopped_waiting_execution_may_continue')
            # A canceled HTTP await cannot stop native worker threads. Keep
            # capacity/identity alive until they actually settle and record it.
            await _drain(work)
            if isinstance(error,asyncio.CancelledError): raise
            raise HTTPException(504,'等待已超时，请查询已有操作回执，不能另换编号重试。') from None
    finally:
        # Also drain if recording the timeout failed, or a second cancellation
        # interrupted that recording. Neither can release live execution slots.
        if not work.done(): await _drain(work)
        _CAPACITY.release()
