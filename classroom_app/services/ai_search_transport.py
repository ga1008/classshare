"""Purpose-bound authentication and bounded data for DeepSeek search dispatch.

The model never supplies actors, budgets, dispatch tokens or provider URLs.
Only a persisted reservation can be dispatched by the private AI endpoint.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import time
from urllib.parse import quote, urlsplit, urlunsplit

from fastapi import HTTPException

TIMESTAMP_HEADER = 'X-LanShare-Search-Timestamp'
SIGNATURE_HEADER = 'X-LanShare-Search-Signature'
RESULT_COUNT = 5
RESULT_BYTES = 24000

WEB_SEARCH_TOOL = {
    'type': 'function',
    'function': {
        'name': 'web_search',
        'description': '搜索最新公开网页。每次消耗一次当日搜索额度；已知信息足够时不使用。仅提交概括后的公开主题，禁止发送姓名、学号、联系方式、私聊全文或其他隐私。',
        'parameters': {
            'type': 'object', 'properties': {
                'query': {'type': 'string', 'description': '1至128个字符的精炼公开搜索词，可用site:、after:等语法。'},
            }, 'required': ['query'], 'additionalProperties': False,
        },
    },
}


def normalize_query(value: object) -> str:
    if not isinstance(value, str):
        raise HTTPException(422, '搜索词应为文本')
    text = ' '.join(value.split()).strip()
    if not 1 <= len(text) <= 128 or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in text):
        raise HTTPException(422, '搜索词需要1至128个字符')
    # Reject instead of silently changing the intent. No account/contact data
    # belongs in public search; ordinary years and domain/IP teaching examples do.
    if (re.search(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', text)
            or re.search(r'(?<!\d)1[3-9]\d{9}(?!\d)', text)
            or re.search(r'(?:学号|身份证|密码|密钥|api[_ -]?key|bearer)\s*[:：=]?\s*[A-Za-z0-9_-]{5,}', text, re.I)
            or re.search(r'https?://[^\s/]*@', text, re.I)):
        raise HTTPException(422, '请用不含身份、联系方式或凭据的公开主题搜索')
    return text


def search_fingerprint(query: str) -> str:
    payload = {'query': normalize_query(query), 'count': RESULT_COUNT, 'need_content': True, 'version': 1}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _signature(body: bytes, timestamp: str) -> str:
    secret = (os.getenv('AI_SEARCH_SECRET') or os.getenv('SECRET_KEY') or 'DEFAULT_WEAK_SECRET_KEY_REPLACE_ME').encode()
    key = hmac.new(secret, b'lanshare-ai-search-dispatch-v1', hashlib.sha256).digest()
    return hmac.new(key, timestamp.encode('ascii') + b'.' + body, hashlib.sha256).hexdigest()


def signed_search_request(payload: dict, *, now: float | None = None) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
    stamp = str(int(time.time() if now is None else now))
    return body, {'Content-Type': 'application/json', TIMESTAMP_HEADER: stamp, SIGNATURE_HEADER: _signature(body, stamp)}


def verify_search_request(body: bytes, timestamp: str | None, signature: str | None, *, now: float | None = None) -> None:
    stamp = str(timestamp or '')
    if (len(body) > 4096 or not 9 <= len(stamp) <= 12 or not stamp.isascii() or not stamp.isdigit()
            or abs((time.time() if now is None else now) - int(stamp)) > 60
            or not hmac.compare_digest(_signature(body, stamp), str(signature or ''))):
        raise HTTPException(401, 'search dispatch signature invalid or expired')


def safe_result_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) <= 32 or ord(c) == 127 for c in value) or '\\' in value:
        return None
    try:
        u = urlsplit(value)
        host = (u.hostname or '').rstrip('.').lower()
        if u.scheme not in {'http', 'https'} or not host or u.username or u.password or u.port not in {None, 80, 443}:
            return None
        if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')) or '.' not in host:
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            if host.replace('.', '').isdigit() or host.startswith('0x'):
                return None
        return urlunsplit((u.scheme, u.netloc, quote(u.path, safe='/%:@!$&*+,;=-._~'),
                           quote(u.query, safe='=&?/%:@!$*+,;~-._'), ''))
    except ValueError:
        return None


def sanitize_results(payload: object) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get('results'), list):
        raise ValueError('invalid_search_schema')
    output: list[dict] = []
    sizes = {'title': 160, 'snippet': 400, 'summary': 1300, 'content': 1800, 'site_name': 80, 'date': 40}
    for item in payload['results'][:RESULT_COUNT]:
        if not isinstance(item, dict) or not (url := safe_result_url(item.get('url'))):
            continue
        row = {'url': url}
        row.update({key: str(item.get(key) or '')[:size] for key, size in sizes.items()})
        if not row['title']:
            row['title'] = urlsplit(url).hostname or '来源'
        if len(json.dumps([*output, row], ensure_ascii=False).encode()) > RESULT_BYTES:
            break
        output.append(row)
    return output
