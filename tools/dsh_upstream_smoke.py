#!/usr/bin/env python3
"""Opt-in active-system-key probe with three calls and small output requests.

Run in the trusted app environment only. This does not impersonate a user,
create a task, mutate the key store, or establish end-to-end Agent readiness.
Without --execute-paid-probe, it neither opens the DB nor makes a request.
"""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
MAX_RESPONSE = 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def requests_for(model):
    return [
        ('chat_off', '/chat/completions', {'model': model, 'max_tokens': 64, 'stream': False,
          'thinking': {'type': 'disabled'}, 'messages': [{'role': 'user', 'content': 'Reply with only OK.'}]}),
        ('chat_high', '/chat/completions', {'model': model, 'max_tokens': 256, 'stream': False,
          'thinking': {'type': 'enabled'}, 'reasoning_effort': 'high',
          'messages': [{'role': 'user', 'content': 'What is 1+1? Reply briefly.'}]}),
        ('native_search', '/messages', {'model': 'deepseek-flash', 'max_tokens': 512,
          'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 'Perform a web search for the query: DeepSeek official API documentation'}]}],
          'tools': [{'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 1}]}),
    ]


def probe(url, secret, payload, *, search=False):
    headers = {'Authorization': 'Bearer ' + secret, 'Content-Type': 'application/json', 'Accept': 'application/json'}
    if search: headers.update({'x-api-key': secret, 'anthropic-version': '2023-06-01'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method='POST')
    started = time.monotonic()
    try:
        with opener.open(request, timeout=90) as response:
            raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE: return {'ok': False, 'error': 'response_limit'}
            body = json.loads(raw)
            result = {'http_status': response.status, 'elapsed_ms': round((time.monotonic() - started) * 1000),
                      'model': body.get('model'), 'usage': body.get('usage')}
            if search:
                blocks = [block for block in body.get('content', []) if block.get('type') == 'web_search_tool_result']
                sources = [item for block in blocks for item in block.get('content', []) if isinstance(item, dict) and item.get('type') == 'web_search_result' and item.get('url')]
                result.update(ok=bool(sources), native_result_blocks=len(blocks), sources=len(sources), stop_reason=body.get('stop_reason'))
            else:
                choice = next(iter(body.get('choices', [])), {})
                message = choice.get('message', {})
                result.update(ok=bool(message.get('content') or message.get('reasoning_content')),
                              finish_reason=choice.get('finish_reason'),
                              has_answer=bool(message.get('content')), has_reasoning=bool(message.get('reasoning_content')))
            return result
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        return {'ok': False, 'http_status': status, 'error': 'upstream_http_error'}
    except Exception as error:
        # Exception strings or response bodies can repeat credentials. Persist
        # only a stable exception class and no upstream response content.
        return {'ok': False, 'error': type(error).__name__}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-paid-probe', action='store_true')
    parser.add_argument('--expected-key-id', type=int)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if not args.execute_paid_probe:
        print(json.dumps({'status': 'not_executed', 'paid_requests': 0, 'requires': '--execute-paid-probe --expected-key-id <active-key-id>',
                          'maximum_requests': 3, 'requested_output_token_caps_total': 832, 'maximum_search_uses': 1}))
        return
    if not args.expected_key_id or args.expected_key_id < 1:
        parser.error('An explicitly selected active system key id is required')
    # Hide import diagnostics; this tool must never print configuration or keys.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        from classroom_app.db import get_db_connection
        from classroom_app.services.agent_key_service import get_active_agent_api_key
        with get_db_connection() as conn:
            active = get_active_agent_api_key(conn)
    if not active or int(active[0]['id']) != args.expected_key_id:
        raise ValueError('The active system Agent key differs from the explicitly selected key')
    item, secret = active
    base = str(item.get('base_url') or 'https://api.deepseek.com').rstrip('/')
    if base not in {'https://api.deepseek.com', 'https://api.deepseek.com/v1'}:
        raise ValueError('This probe accepts only the official DeepSeek chat service')
    result = {'kind': 'real_official_DeepSeek_service_probe_not_platform_actor_acceptance',
              'checked_at_epoch': int(time.time()), 'key_id': args.expected_key_id, 'paid_requests': 0, 'checks': {}}
    for name, endpoint, payload in requests_for(item.get('model') or 'deepseek-v4-pro'):
        url = ('https://api.deepseek.com/anthropic/v1' if name == 'native_search' else base) + endpoint
        result['paid_requests'] += 1
        result['checks'][name] = probe(url, secret, payload, search=name == 'native_search')
    secret = None
    result['ok'] = all(value['ok'] for value in result['checks'].values())
    encoded = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if args.report:
        descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream: stream.write(encoded)
    print(encoded, end='')
    if not result['ok']: raise SystemExit(1)


if __name__ == '__main__':
    try: main()
    except Exception as error:
        print(json.dumps({'ok': False, 'error': type(error).__name__}), file=sys.stderr)
        raise SystemExit(1)
