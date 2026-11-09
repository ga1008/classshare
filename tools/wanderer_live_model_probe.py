"""Explicit paid-provider replay against an owned synthetic world only.

No dotenv configuration is loaded wholesale, no HTTP platform actions execute,
and every real model invocation passes the ordinary wanderer wake accounting.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


async def probe(runtime_root: Path) -> dict:
    from dotenv import dotenv_values
    from tools.isolated_environment import isolate_sqlite_environment, guard_dotenv_loading, guard_postgres_connections
    provider_values = dotenv_values(REPO / '.env')
    key = provider_values.get('DEEPSEEK_API_KEY') or os.environ.get('DEEPSEEK_API_KEY')
    if not key:
        return {'available': False, 'reason': 'No configured DeepSeek credential', 'model_calls': 0}
    isolate_sqlite_environment(runtime_root)
    guard_dotenv_loading()
    guard_postgres_connections()
    # Only the already configured provider's credential/model settings enter the
    # isolated process. Database URLs, credentials and unrelated config do not.
    for name in ('DEEPSEEK_MODEL_STANDARD', 'DEEPSEEK_MODEL_THINKING', 'DEEPSEEK_MODEL_DEEP_TEXT'):
        if provider_values.get(name):
            os.environ[name] = provider_values[name]
    provider_values.clear()
    os.environ.update(DEEPSEEK_API_KEY=key, DEEPSEEK_ENABLED='true',
                      AI_PROVIDER_HTTP_MAX_ATTEMPTS='1', AI_PROVIDER_TIMEOUT_MAX_ATTEMPTS='1',
                      AI_HTTP_TIMEOUT_DEEP='60', AI_PROVIDER_PROBE_ENABLED='false')
    from classroom_app import config, database
    assert config.DB_ENGINE == 'sqlite' and not config.DATABASE_URL
    assert Path(config.DB_PATH).resolve() == runtime_root / 'db' / 'classroom.db'
    database.init_database()
    from classroom_app.services.wanderer_persona_seeds import seed_wanderers
    from classroom_app.services.wanderer_identity_service import load_wanderer_by_slug
    from classroom_app.services.wanderer_settings_service import wanderer_runtime_setting_set, daily_ai_calls_key
    from classroom_app.services import wanderer_runtime_service as runtime
    from classroom_app.time_utils import local_iso
    import ai_assistant
    import httpx
    for name, settings in ai_assistant.PLATFORMS_CONFIG.items():
        settings['enabled'] = name == 'DeepSeek'
    # Account for provider-specific key capitalization without enabling another.
    deepseek_names = [name for name in ai_assistant.PLATFORMS_CONFIG if name.lower() == 'deepseek']
    assert len(deepseek_names) == 1
    ai_assistant.PLATFORMS_CONFIG[deepseek_names[0]]['enabled'] = True
    report = {'available': True, 'synthetic': True, 'provider': 'DeepSeek',
              'database': str(config.DB_PATH), 'model_calls': 0, 'http_requests': 0,
              'real_business_access': False, 'personas': [], 'calls': []}
    original_async_send, original_sync_send = httpx.AsyncClient.send, httpx.Client.send
    async def guarded_async_send(client, request, *args, **kwargs):
        if request.url.host != 'api.deepseek.com':
            raise RuntimeError('Probe forbids non-provider network access')
        report['http_requests'] += 1
        if report['http_requests'] > 6:
            raise RuntimeError('Probe provider request ceiling reached')
        return await original_async_send(client, request, *args, **kwargs)
    def guarded_sync_send(client, request, *args, **kwargs):
        if request.url.host != 'api.deepseek.com':
            raise RuntimeError('Probe forbids non-provider network access')
        report['http_requests'] += 1
        if report['http_requests'] > 6:
            raise RuntimeError('Probe provider request ceiling reached')
        return original_sync_send(client, request, *args, **kwargs)
    allowed = {'self.status', 'world.chapters', 'world.read', 'memory.write', 'memory.read',
               'knowledge.remember', 'knowledge.read', 'runtime.receipt', 'runtime.context'}
    original_execute = runtime.execute_command
    async def bounded_command(factory, run, command, **kwargs):
        if command['capability'] not in allowed:
            return {'status': 'denied', 'capability': command['capability'],
                    'error': '本次合成研读场景只开放世界阅读、本人知识与记忆操作。'}
        return await original_execute(factory, run, command, **kwargs)
    active_slug = ''
    async def caller(endpoint, payload, **kwargs):
        assert endpoint == '/api/ai/chat'
        report['model_calls'] += 1
        assert report['model_calls'] <= 6
        data = await ai_assistant.ai_chat_task(ai_assistant.AIChatRequest(**payload))
        report['calls'].append({'persona': active_slug,
            'prompt_sha256': hashlib.sha256(payload['system_prompt'].encode()).hexdigest(),
            'response_json': data.get('response_json'), 'response_text': data.get('response_text'),
            'execution_metadata': data.get('execution_metadata')})
        (runtime_root / 'model-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return data
    with database.get_db_connection() as conn:
        conn.execute('INSERT INTO teachers(name,email,hashed_password) VALUES(?,?,?)',
                     ('合成管理员', 'synthetic-live-probe@example.test', 'synthetic-unusable-hash'))
        seed_wanderers(conn)
        wanderer_runtime_setting_set(conn, 'enabled', '1')
        wanderer_runtime_setting_set(conn, 'autonomous_v2_enabled', '1')
        wanderer_runtime_setting_set(conn, daily_ai_calls_key(), '0')
        for slug in ('atai', 'lele'):
            conn.execute("UPDATE wanderer_agents SET status='active',daily_wake_limit=3,born_at=?,lifespan_weeks=10 WHERE slug=?",
                         (local_iso(), slug))
        conn.commit()
    with patch.object(httpx.AsyncClient, 'send', guarded_async_send), patch.object(httpx.Client, 'send', guarded_sync_send), \
            patch.object(runtime, 'execute_command', bounded_command):
        for active_slug in ('atai', 'lele'):
            with database.get_db_connection() as conn:
                person = load_wanderer_by_slug(conn, active_slug)
            try:
                result = await runtime.wake_wanderer(database.get_db_connection, person['id'],
                    source='synthetic_live_reading', event_key=f'live-reading:{active_slug}', force=True,
                    context={'scope': {'kind': 'public'}, 'location': '世界手册书架',
                             'event': '今天你来到书架。请亲自阅读 world 章节，收到执行回执后按自己的性格写下本人记忆；确认保存后结束本次活动。最多三次思考，只有真实读过的内容才能写成见闻。'},
                    ai_caller=caller)
                with database.get_db_connection() as conn:
                    commands = [dict(row) for row in conn.execute('SELECT c.capability,c.state,c.receipt_json FROM wanderer_commands c JOIN wanderer_turns t ON t.turn_id=c.turn_id WHERE t.wanderer_id=? ORDER BY c.created_at,c.operation_id', (person['id'],))]
                    memories = [dict(row) for row in conn.execute('SELECT m.content,s.author_kind,s.evidence_json FROM wanderer_episodic_memories m JOIN wanderer_memory_sources s ON s.memory_id=m.id WHERE m.wanderer_id=?', (person['id'],))]
                    usage = conn.execute("SELECT COALESCE(SUM(used_units),0) AS n FROM wanderer_resource_usage WHERE owner_id=? AND resource='ai_wake'", (person['id'],)).fetchone()['n']
                report['personas'].append({'slug': active_slug, 'name': person['display_name'],
                    'result': result, 'commands': commands, 'self_authored_memories': memories,
                    'billed_wake_units': usage, 'billed_wakes': usage / 1_000_000})
            except Exception as error:
                report['personas'].append({'slug': active_slug, 'error_type': type(error).__name__,
                    'error': str(error).replace(key, '[redacted]')[:600]})
    report['complete'] = True
    report['verified'] = {
        'protocol_valid': len(report['calls']) == 6 and all(
            (call.get('response_json') or {}).get('protocol') == 'wanderer.v1' for call in report['calls']),
        'each_persona_self_authored_memory': len(report['personas']) == 2 and all(
            len(person.get('self_authored_memories', [])) == 1 and person['self_authored_memories'][0]['author_kind'] == 'self'
            for person in report['personas']),
        'each_persona_three_paid_wakes': len(report['personas']) == 2 and all(
            person.get('billed_wakes') == 3 for person in report['personas']),
        'each_persona_stopped_voluntarily': len(report['personas']) == 2 and all(
            not [call for call in report['calls'] if call['persona'] == person['slug']][-1]['response_json']['callback']['requested']
            for person in report['personas'] if any(call['persona'] == person['slug'] for call in report['calls'])),
        'provider_requests_equal_billed_wakes': report['http_requests'] == sum(
            person.get('billed_wakes', 0) for person in report['personas']),
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--allow-paid-provider', action='store_true', required=True)
    args = parser.parse_args()
    runtime_root = args.runtime_root.resolve()
    if not runtime_root.is_relative_to(REPO / '.codex-temp') or runtime_root.exists():
        raise SystemExit('Use a new, owned directory below .codex-temp')
    runtime_root.mkdir(parents=True)
    (runtime_root / 'db').mkdir()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    result = asyncio.run(probe(runtime_root))
    (runtime_root / 'model-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps({key: result.get(key) for key in ('available','model_calls','http_requests','complete')}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
