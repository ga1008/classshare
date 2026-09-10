#!/usr/bin/env python3
"""Real Linux launcher quota/lifecycle checks with synthetic credentials, no model."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import socket
import sys
import threading
import time
from types import SimpleNamespace
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import agent_dsh_launcher as launcher


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    lab = Path('/lanshare/.codex-temp/dsh-migration-20260910')
    if (os.name != 'posix' or os.getuid() != 0 or args.root.absolute() != root or
            root.parent != lab or not re.fullmatch(r'runtime-c[1-9][0-9]*', root.name)):
        parser.error('Requires a new explicitly isolated runtime cohort')
    target = root / 'linux-quota-poc.json'
    if target.exists(): parser.error('Refusing to replace evidence')
    config = SimpleNamespace(image=args.image, profile=root / 'deployment/dsh/profile',
        task_root=root / 'quota-tasks', state_root=root / 'quota-state',
        socket_root=root / 'quota-ipc', max_concurrency=1, max_runtime_seconds=60)
    for directory in (config.task_root, config.state_root, config.socket_root): directory.mkdir()
    gateway = socket.socket(socket.AF_UNIX)
    gateway.bind(str(config.socket_root / 'gateway.sock'))
    gateway.listen()
    os.chown(config.socket_root / 'gateway.sock', 0, 10001)
    os.chmod(config.socket_root / 'gateway.sock', 0o660)
    instance = launcher.Launcher(config)
    report = {'paid_model_requests': 0, 'real_credentials_used': False,
              'kind': 'synthetic_fixture_not_official_DeepSeek', 'checks': {}, 'evidence': instance.evidence}
    def request():
        identifier = 8900000000000000 + (uuid.uuid4().int % 1000000000000)
        workspace = config.task_root / 'tasks' / str(identifier)
        workspace.mkdir(parents=True)
        return {'task_id': identifier, 'attempt_id': str(uuid.uuid4()), 'fencing_token': 1,
            'actor_id': 'teacher:9000000000000001', 'model_token': 'lsagt_' + 'a' * 43,
            'tools_token': 'lsagt_' + 'b' * 43, 'model': 'deepseek-v4-pro', 'search_model': ''}, workspace
    def exceed(path, kind):
        if kind == 'bytes':
            with (path / 'synthetic-sparse.bin').open('wb') as handle:
                handle.truncate(launcher.MAX_WORKSPACE_BYTES + 1)
        else:
            for index in range(launcher.MAX_WORKSPACE_ENTRIES + 1): (path / f'empty-{index}').touch()
    try:
        for kind in ('bytes', 'entries'):
            row, workspace = request()
            exceed(workspace, kind)
            assert launcher.workspace_exceeded(workspace)
            try: instance.run(row, None)
            except ValueError as exc: assert 'quota' in str(exc)
            else: raise AssertionError('Preflight quota accepted')
            assert not (config.state_root / str(row['task_id'])).exists()
            report['checks']['preflight_workspace_' + kind] = True
        for label, kind in (('workspace', 'bytes'), ('home', 'entries')):
            row, workspace = request()
            client, server = socket.socketpair()
            client.settimeout(20)
            errors = []
            def run():
                try: instance.run(row, server)
                except Exception as exc: errors.append(type(exc).__name__)
                finally: server.close()
            worker = threading.Thread(target=run, daemon=True)
            worker.start()
            stream = client.makefile('rb')
            name = launcher.runner_name(row['task_id'], row['attempt_id'])
            try:
                assert json.loads(stream.readline())['status'] == 'ready'
                client.sendall(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                    'params': {'protocolVersion': 1, 'clientCapabilities': {}}}).encode() + b'\n')
                response = json.loads(stream.readline())
                assert response['id'] == 1 and response['result']['protocolVersion'] == 1
                location = workspace if label == 'workspace' else config.state_root / str(row['task_id']) / row['attempt_id']
                exceed(location, kind)
                worker.join(20)
                assert not worker.is_alive(), 'Quota did not terminate the launcher run'
                assert name not in instance.names() and name not in instance.active
                assert not instance.unhealthy.is_set()
                report['checks']['live_' + label + '_' + kind + '_confirmed_removed'] = True
            finally:
                with contextlib.suppress(OSError): client.shutdown(socket.SHUT_RDWR)
                stream.close()
                client.close()
                instance.stop(row)
                worker.join(20)
        report['passed'] = all(report['checks'].values()) and len(report['checks']) == 4
    except Exception as exc:
        report['failure'] = str(exc)
        report['passed'] = False
    finally:
        gateway.close()
        target.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))
    return 0 if report['passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
