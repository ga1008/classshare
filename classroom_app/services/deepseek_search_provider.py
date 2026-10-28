"""Bounded raw Search API transport. No actor selection, model calls or retries of ambiguous sends."""
from __future__ import annotations

import asyncio
import json

import httpx

from .ai_search_transport import RESULT_COUNT, normalize_query, sanitize_results

SEARCH_URL = 'https://api.deepseek.com/v1/tools/search'
MAX_RESPONSE_BYTES = 262144


async def search_deepseek(query: str, api_key: str, *, client=None) -> dict:
    """A successful nonempty upstream response is billed even if unsafe URLs are discarded."""
    query = normalize_query(query)
    if not api_key:
        return {'outcome': 'failed', 'error': 'provider_unavailable', 'results': []}
    if client is None:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12, connect=3, pool=2), follow_redirects=False,
                                     limits=httpx.Limits(max_connections=4, max_keepalive_connections=2)) as own:
            return await search_deepseek(query, api_key, client=own)
    for attempt in range(2):
        try:
            async with client.stream('POST', SEARCH_URL, headers={'Authorization': f'Bearer {api_key}'},
                                     json={'query': query, 'count': RESULT_COUNT, 'need_content': True}) as response:
                code = response.status_code
                if code != 200:
                    if code in {408, 429, 500, 502, 503, 504} and not attempt:
                        # Only a definite unbilled HTTP response permits another request.
                        delay = response.headers.get('Retry-After', '0.25')
                        try:
                            delay = min(1.0, max(0.0, float(delay)))
                        except ValueError:
                            delay = .25
                        await asyncio.sleep(delay)
                        continue
                    return {'outcome': 'failed', 'error': f'provider_http_{code}', 'results': []}
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        return {'outcome': 'unknown', 'error': 'response_too_large', 'results': []}
                    chunks.append(chunk)
                try:
                    payload = json.loads(b''.join(chunks))
                    results = sanitize_results(payload)
                except (ValueError, UnicodeError):
                    return {'outcome': 'unknown', 'error': 'invalid_provider_response', 'results': []}
                if not payload['results']:
                    return {'outcome': 'empty', 'error': '', 'results': []}
                if not results:
                    return {'outcome': 'succeeded', 'error': 'no_safe_results', 'results': []}
                return {'outcome': 'succeeded', 'error': '', 'results': results}
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            return {'outcome': 'failed', 'error': 'provider_connection_failed', 'results': []}
        except (httpx.TimeoutException, httpx.TransportError):
            # A lost reply cannot prove the provider did not finish. Do not retry.
            return {'outcome': 'unknown', 'error': 'provider_result_uncertain', 'results': []}
    raise AssertionError('bounded search attempts exhausted')
