"""Reviewed HTTP fallback capabilities, not an arbitrary URL proxy.

Each source digest pins the actual normal router function, including its
dependencies/decorator declaration. A changed handler requires another review.
"""
from dataclasses import dataclass, field
import hashlib
import inspect
import json
from pathlib import Path
import re
from urllib.parse import urlencode

from fastapi import HTTPException
from fastapi.routing import APIRoute
from starlette.routing import Match

REPO = Path(__file__).resolve().parents[1]
MAX_BODY_BYTES = 16384


@dataclass(frozen=True)
class RequestCapability:
    key: str
    label: str
    method: str
    path: str
    module: str
    handler: str
    source_sha256: str
    parameters: dict = field(default_factory=dict)
    mutates: bool = True
    transport: str = 'json'
    allows_files: bool = False
    max_body_bytes: int = MAX_BODY_BYTES
    response_contract: str = 'status'
    server_operation_id_field: str | None = None


def _spec(kind, *, required=False, **kw):
    return {'type': kind, 'required': required, **kw}


ID = _spec('integer', required=True, minimum=1, maximum=2**63 - 1)
CONTACT = _spec('string', required=True, maxLength=120, pattern=r'(?:teacher|student):[1-9][0-9]{0,18}')
CAPABILITIES = (
    RequestCapability('http.blog.bookmark.toggle', '切换自己的博客收藏', 'POST', '/api/blog/posts/{post_id}/bookmark',
        'blog', 'api_bookmark_post', 'b61b2468136500d980cf5ac87a1cdbda5b1cc8f5a20efe7ee6698d4627ae021b', {'path': {'post_id': ID}}),
    RequestCapability('http.blog.follow.set', '设置自己的博客关注', 'POST', '/api/blog/follows',
        'blog', 'api_set_blog_follow', '3f42f6880e4fdaa5768b1326f69ea72e3f5c210c5bfc2e026eb2d8ea9df52025', {'body': {
            'target_type': _spec('string', required=True, enum=['section','author','tag'], maxLength=20),
            'target_key': _spec('string', required=True, maxLength=200), 'following': _spec('boolean', required=True)}}),
    RequestCapability('http.blog.follows.list', '读取自己的博客关注', 'GET', '/api/blog/follows',
        'blog', 'api_blog_follows', '34398eea772dc9462689199410722061e9fa87821e1ee87165dbeb8c640ddf73', mutates=False),
    RequestCapability('http.messages.read', '将指定消息标为已读', 'POST', '/api/message-center/read',
        'message_center', 'api_message_center_mark_read', 'c8762c6f587eddb27ecf3bec699e9e951269236fb30a160e6382f81a51b50291', {'body': {
            'notification_ids': _spec('ids', required=True, minItems=1, maxItems=100),
            'include_private': _spec('boolean')}}),
    RequestCapability('http.messages.private.open', '打开私信会话并标为已读', 'GET', '/api/message-center/private/conversation',
        'message_center', 'api_private_message_conversation', '03ba2402ab55530f4907fcdb3eb5b651060cb5eeac3e4a830dfa775e9ba3f020', {'query': {
            'contact': CONTACT, 'scope': _spec('integer', minimum=1, maximum=2**63-1),
            'limit': _spec('integer', minimum=1, maximum=100)}}),
    RequestCapability('http.messages.blocks.list', '读取自己的私信屏蔽', 'GET', '/api/message-center/private/blocks',
        'message_center', 'api_private_message_blocks', 'e3326ef9885211f3cfe50f6d4dd7bab7cc97659d8810149973364aa76b2ba997', mutates=False),
    RequestCapability('http.messages.blocks.add', '屏蔽私信联系人', 'POST', '/api/message-center/private/blocks',
        'message_center', 'api_add_private_message_block', 'd96268afc83976ae99144365d03df89aef576587433f841453348910fae94a11', {'body': {
            'contact_identity': CONTACT, 'class_offering_id': _spec('integer', minimum=1, maximum=2**63-1)}}),
    RequestCapability('http.messages.blocks.remove', '解除私信联系人屏蔽', 'DELETE', '/api/message-center/private/blocks',
        'message_center', 'api_remove_private_message_block', '26ebe0be4b0b0c913baad902aaf876d9d8c4190c95e188af33ee9e90b4d2d66d', {'query': {'contact_identity': CONTACT}}),
    RequestCapability('http.polls.snapshot', '读取可访问课堂的投票', 'GET', '/api/polls/classrooms/{class_offering_id}/snapshot',
        'polls', 'classroom_poll_snapshot', '91399fee806a5d62b323f6914f0907040914cf97d7332469732b3fde5a67a58f', {'path': {'class_offering_id': ID}}, mutates=False),
    RequestCapability('http.polls.detail', '读取可访问的投票详情', 'GET', '/api/polls/{poll_id}',
        'polls', 'poll_detail', '9f43a67a5c50eb17b78beedddb14f8fa087bec3b494c61c8065781afbe560eea', {'path': {'poll_id': ID}}, mutates=False),
    RequestCapability('http.polls.vote', '提交自己的投票选项', 'POST', '/api/polls/{poll_id}/vote',
        'polls', 'poll_vote', '9163294ec2df6c49720e403d3b320acfce0209c309775993ff21af26e1db37a4', {'path': {'poll_id': ID}, 'body': {
            'option_ids': _spec('ids', required=True, minItems=1, maxItems=30)}}),
)


from .agent_platform_request_learning import build_capabilities as _learning_capabilities
from .agent_platform_request_submissions import build_capabilities as _submission_capabilities
from .agent_platform_request_profile import build_capabilities as _profile_capabilities
from .agent_platform_request_polls import build_capabilities as _poll_capabilities
from .agent_platform_request_blog import build_capabilities as _blog_capabilities
from .agent_platform_request_collaboration import build_capabilities as _collaboration_capabilities
from .agent_platform_request_material_content import build_capabilities as _material_content_capabilities

CAPABILITIES += _learning_capabilities(RequestCapability, _spec, ID)
CAPABILITIES += _submission_capabilities(RequestCapability, _spec, ID)
CAPABILITIES += _profile_capabilities(RequestCapability, _spec, ID)
CAPABILITIES += _poll_capabilities(RequestCapability, _spec, ID)
CAPABILITIES += _blog_capabilities(RequestCapability, _spec, ID)
CAPABILITIES += _collaboration_capabilities(RequestCapability, _spec, ID)
CAPABILITIES += _material_content_capabilities(RequestCapability, _spec, ID)


def source_digest(endpoint):
    return hashlib.sha256(inspect.getsource(endpoint).replace('\r\n', '\n').encode()).hexdigest()


def resolve_capability(app, key):
    operation = next((item for item in CAPABILITIES if item.key == key), None)
    if operation is None: raise HTTPException(404, '该平台请求尚未经过接入审核。')
    routes = [route for route in app.router.routes if isinstance(route, APIRoute)
              and route.path == operation.path and operation.method in route.methods]
    if len(routes) != 1: raise HTTPException(503, '平台请求路由缺失或重复。')
    route = routes[0]
    target = route.endpoint
    expected_file = REPO / 'routers' / (operation.module.replace('.', '/') + '.py')
    if (target.__module__ != 'classroom_app.routers.' + operation.module or target.__qualname__ != operation.handler
            or Path(inspect.getsourcefile(target) or '').resolve() != expected_file.resolve()
            or source_digest(target) != operation.source_sha256):
        raise HTTPException(503, '平台请求代码已变化，需重新审核后接入。')
    return operation, route


def arguments(operation, *, path_params=None, query_params=None, body=None):
    values = {'path': {} if path_params is None else path_params, 'query': {} if query_params is None else query_params, 'body': {} if body is None else body}
    normalized = {}
    for location, items in values.items():
        schema = operation.parameters.get(location, {})
        if not isinstance(items, dict) or items.keys() - schema.keys():
            raise HTTPException(400, '平台请求参数包含未注册字段。')
        normalized[location] = {}
        for key, spec in schema.items():
            if key not in items:
                if spec.get('required'): raise HTTPException(400, '平台请求缺少必要参数。')
                continue
            value = items[key]
            if value is None and spec.get('nullable'):
                normalized[location][key] = None
                continue
            kind = spec['type']
            valid = (type(value) is bool if kind == 'boolean' else
                type(value) is int and spec.get('minimum',0) <= value <= spec.get('maximum',2**63-1) if kind == 'integer' else
                isinstance(value,str) and spec.get('minLength',1) <= len(value) <= spec.get('maxLength',200)
                and not any((ord(c)<32 and not (spec.get('allowNewlines') and c in '\n\r\t')) or 0xD800<=ord(c)<=0xDFFF for c in value) if kind == 'string' else
                _valid_json(value, spec) if kind == 'json' else
                isinstance(value,list) and spec['minItems'] <= len(value) <= spec['maxItems']
                and all(isinstance(item,str) and 0 < len(item) <= spec['maxLength']
                        and not any(ord(c)<32 or 0xD800<=ord(c)<=0xDFFF for c in item) for item in value) if kind == 'strings' else
                isinstance(value,list) and spec['minItems'] <= len(value) <= spec['maxItems']
                and all(type(item) is int and 0 < item < 2**63 for item in value) if kind == 'ids' else False)
            if not valid or ('enum' in spec and value not in spec['enum']) or ('pattern' in spec and not re.fullmatch(spec['pattern'],value)):
                raise HTTPException(400, '平台请求参数类型或范围无效。')
            normalized[location][key] = sorted(set(value)) if kind == 'ids' else value
    path = operation.path
    for key,value in normalized['path'].items(): path = path.replace('{' + key + '}',str(value))
    if '{' in path: raise HTTPException(400, '平台请求路径参数不完整。')
    query = urlencode(sorted(normalized['query'].items())).encode('ascii')
    raw = json.dumps(normalized['body'],ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode() if normalized['body'] else b''
    if len(raw)>operation.max_body_bytes: raise HTTPException(400, '平台请求内容过长。')
    return path, query, raw, normalized


def _valid_json(value, spec):
    if not isinstance(value, (dict, list)):
        return False
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        return len(encoded) <= spec.get('maxBytes', 65536)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return False


def matched_route(app, scope):
    # Match the first actual mounted route, as Starlette does. A shadowing
    # route or Mount cannot silently replace the reviewed endpoint.
    for route in app.router.routes:
        match,_ = route.matches(scope)
        if match == Match.FULL: return route
    return None


def platform_request_catalog(app, *, include_blocked=False):
    result = []
    for item in CAPABILITIES:
        try: resolve_capability(app,item.key)
        except HTTPException:
            if include_blocked: result.append({'key':item.key,'label':item.label,'status':'needs_review','executable':False})
            continue
        result.append({'key':item.key,'label':item.label,'parameters':item.parameters,
            'required_scope':'platform:write' if item.mutates else 'platform:read',
            'guarantee':'observed_http_result_not_verified_business','mutates':item.mutates,
            'requires_current_session':True,'status':'reviewed','executable':True,
            'transport':item.transport,'allows_task_files':item.allows_files})
    return result
