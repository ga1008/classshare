#!/usr/bin/env python3
"""Explicit DSH deployment phases. Preflight is read-only; activate is a cutover.

The production deployer calls activate only after its quiesced PG migration.
No model credentials are copied to the host service or inspected for evidence.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.request

ROOT = Path('/lanshare')
UNIT = Path('/etc/systemd/system/lanshare-agent-launcher.service')
SERVICE = 'lanshare-agent-launcher.service'
LIBRARY = Path('/usr/local/lib/lanshare-agent')
SOCKET = Path('/run/lanshare-agent/control/launcher.sock')
DOCKER = ['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock']
LEGACY_KEYS = frozenset({
    'AGENT_TASK_RUNTIME_URL', 'AGENT_TASK_RUNTIME_TOKEN', 'AGENT_TASK_RUNTIME_MODEL',
    'AGENT_TASK_RUNTIME_WORKSPACE_PREFIX', 'AGENT_TASK_RUNTIME_POLL_SECONDS', 'AGENT_TASK_RUNTIME_FIRST',
    'AGENT_TASK_DEEPSEEK_AUTO_APPROVE', 'AGENT_TASK_ALLOW_RUNTIME_SHELL',
    'AGENT_BRIDGE_BASE_URL', 'DEEPSEEK_RUNTIME_TOKEN', 'DEEPSEEK_TUI_WORKERS', 'DEEPSEEK_TUI_TAG',
})


def run(argv, *, timeout=30):
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                            env={'PATH': '/usr/bin:/bin', 'HOME': '/root'}, check=False)
    if result.returncode:
        # Never print Docker output that could contain an environment or key.
        raise RuntimeError('DSH deployment command failed: ' + Path(argv[0]).name)
    return result.stdout


def profile_digest(profile):
    digest = hashlib.sha256()
    for name in ('package.json', 'cordis.patch.yml'):
        digest.update(name.encode() + b'\0' + (profile / name).read_bytes().replace(b'\r\n', b'\n') + b'\0')
    return digest.hexdigest()


def manifest_at(root):
    manifest = json.loads((root / 'deployment/dsh/release.json').read_text())
    if (manifest.get('dsh_package_version') != '0.1.5-rc.1'
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', manifest.get('image', ''))
            or not re.fullmatch(r'[0-9a-f]{64}', manifest.get('profile_sha256', ''))):
        raise ValueError('A fixed tested DSH image and profile manifest are required')
    if profile_digest(root / 'deployment/dsh/profile') != manifest['profile_sha256']:
        raise ValueError('DSH release profile differs from the fixed image manifest')
    return manifest


def preflight(root):
    if os.name != 'posix' or os.getuid() != 0 or root != ROOT or root.resolve(strict=True) != ROOT:
        raise ValueError('DSH host installation requires canonical /lanshare and root')
    for relative in ('docker.env', 'tools/agent_dsh_launcher.py', 'deployment/dsh/profile'):
        path = root / relative
        if path.is_symlink() or not path.exists():
            raise ValueError('Missing or redirected DSH deployment input')
    manifest = manifest_at(root)
    inspected = json.loads(run([*DOCKER, 'image', 'inspect', manifest['image']]))[0]
    if inspected['Id'] != manifest['image']:
        raise ValueError('DSH image ID differs from the manifest')
    evidence = json.loads(run([*DOCKER, 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges:true', '--memory', '256m', '--pids-limit', '32', manifest['image'], '--evidence']))
    if any(evidence.get(key) != manifest[key] for key in ('dsh_package_version', 'profile_sha256')):
        raise ValueError('DSH runtime evidence does not match the release')
    # Existing deployments may own the port already, but only the expected
    # Compose app with an exact loopback binding can satisfy that condition.
    occupied = False
    with socket.socket() as probe:
        try: probe.bind(('127.0.0.1', 18000))
        except OSError: occupied = True
    if occupied:
        ids = run([*DOCKER, 'ps', '-q', '--filter', 'label=com.docker.compose.project=lanshare',
                   '--filter', 'label=com.docker.compose.service=app']).decode().split()
        if len(ids) != 1:
            raise ValueError('Loopback port 18000 is occupied by an unexpected service')
        app = json.loads(run([*DOCKER, 'inspect', ids[0]]))[0]
        if app['HostConfig']['PortBindings'].get('8000/tcp') != [{'HostIp': '127.0.0.1', 'HostPort': '18000'}]:
            raise ValueError('Loopback port 18000 ownership could not be verified')
    run(['/usr/bin/systemctl', '--version'])
    return manifest


def environment_text(text):
    values = {}
    for line in text.splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            values[key] = value
    model = values.get('AGENT_MODEL_DEFAULT') or values.get('AGENT_TASK_RUNTIME_MODEL') or 'deepseek-v4-pro'
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', model.strip(' \"\'')):
        raise ValueError('Configured Agent model is invalid')
    required = {'AGENT_TASKS_ENABLED': 'true', 'AGENT_DSH_ENABLED': 'true',
                'AGENT_DSH_LAUNCHER_SOCKET': '/run/lanshare-agent/launcher.sock',
                'AGENT_MODEL_DEFAULT': model.strip(' \"\''),
                'AGENT_TASK_GLOBAL_CONCURRENCY': '1', 'AGENT_TASK_WORKER_CONCURRENCY': '1'}
    defaults = {'AGENT_TASK_WORKER_ID': 'agent-worker-compose', 'AGENT_TASK_WORKER_POLL_SECONDS': '5',
                'AGENT_TASK_MAX_RUNTIME_SECONDS': '1800', 'AGENT_MODEL_SEARCH_MODEL': 'deepseek-flash',
                'AGENT_MODEL_SEARCH_BASE_URL': 'https://api.deepseek.com/anthropic/v1'}
    for key, value in defaults.items():
        required[key] = values.get(key, '').strip() or value
    lines = [line for line in text.splitlines() if line.split('=', 1)[0] not in LEGACY_KEYS | required.keys()]
    return '\n'.join(lines).rstrip() + '\n' + ''.join(f'{key}={value}\n' for key, value in required.items())


def unit_text(root, manifest):
    return f'''[Unit]
Description=LanShare isolated DSH launcher and task gateway
After=docker.service network.target
Requires=docker.service

[Service]
Type=simple
User=root
Group=root
ExecStart=/usr/bin/python3 {LIBRARY}/launcher.py --image {manifest['image']} --profile {root}/deployment/dsh/profile --task-root {root}/data/agent_tasks --state-root {root}/data/agent_dsh_state --socket-root /run/lanshare-agent --upstream-port 18000 --control-gid 0 --max-concurrency 1 --max-runtime-seconds 1800
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=3
TimeoutStopSec=90
KillMode=mixed
UMask=0027
RuntimeDirectory=lanshare-agent
RuntimeDirectoryMode=0751
RuntimeDirectoryPreserve=yes
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths={root}/data/agent_tasks {root}/data/agent_dsh_state /run/lanshare-agent
RestrictAddressFamilies=AF_UNIX AF_INET
CPUQuota=15%
MemoryMax=192M
TasksMax=96

[Install]
WantedBy=multi-user.target
'''


def atomic_write(path, text, mode):
    temporary = path.with_name(path.name + '.pending')
    if path.is_symlink() or temporary.is_symlink(): raise ValueError('Redirected deployment output')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def stage(root, manifest):
    for path in (LIBRARY, root / 'data/agent_tasks', root / 'data/agent_dsh_state',
                 Path('/run/lanshare-agent'), SOCKET.parent):
        if path.is_symlink(): raise ValueError('Redirected launcher directory')
        path.mkdir(parents=True, exist_ok=True)
        os.chown(path, 0, 0)
        os.chmod(path, 0o751 if path == Path('/run/lanshare-agent') else 0o750)
    # Preserve only controlled host code/config; never copy task homes or keys.
    backup = root / 'data/agent_dsh_state/deploy-backups' / str(time.time_ns())
    backup.mkdir(parents=True, mode=0o700)
    for path in (UNIT, LIBRARY / 'launcher.py', root / 'docker.env'):
        if path.is_symlink(): raise ValueError('Redirected existing service configuration')
        if path.is_file():
            shutil.copyfile(path, backup / path.name)
            os.chmod(backup / path.name, 0o600)
    profile = root / 'deployment/dsh/profile'
    for relative in ('node_modules', '.dsh-module-fallback/node_modules'):
        (profile / relative).mkdir(parents=True, exist_ok=True)
    (profile / 'cordis.yml').touch(exist_ok=True)
    # Windows-produced source archives can carry group-writable directory
    # modes. Normalize only the finite, image-owned profile paths before the
    # launcher's strict ownership check; never recursively change user data.
    for path in (profile, profile / 'node_modules', profile / '.dsh-module-fallback',
                 profile / '.dsh-module-fallback/node_modules', profile / 'package.json',
                 profile / 'cordis.patch.yml', profile / 'cordis.yml'):
        if path.is_symlink(): raise ValueError('Redirected image-owned profile path')
        os.chown(path, 0, 0)
        os.chmod(path, 0o755 if path.is_dir() else 0o644)
    atomic_write(LIBRARY / 'launcher.py', (root / 'tools/agent_dsh_launcher.py').read_text(), 0o644)
    atomic_write(UNIT, unit_text(root, manifest), 0o644)
    return backup


def verify(root, manifest):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(10)
        client.connect(str(SOCKET))
        client.sendall(b'{"version":1,"action":"probe"}\n')
        data = bytearray()
        deadline = time.monotonic() + 10
        while b'\n' not in data:
            client.settimeout(max(.01, deadline - time.monotonic()))
            chunk = client.recv(4096)
            if not chunk or len(data) + len(chunk) > 16384 or time.monotonic() >= deadline:
                raise ValueError('Launcher evidence response is invalid')
            data.extend(chunk)
    result = json.loads(bytes(data).split(b'\n', 1)[0])
    if result.get('status') != 'ready' or any(result.get(key) != manifest[key] for key in ('image', 'dsh_package_version', 'profile_sha256')):
        raise ValueError('Running launcher differs from the intended release')
    if result.get('max_concurrency') != 1 or result.get('network') != 'none':
        raise ValueError('Running launcher limits are invalid')
    with urllib.request.urlopen('http://127.0.0.1:18000/api/internal/health', timeout=10) as response:
        if response.status != 200: raise ValueError('Agent loopback application health failed')
    return result


def retire_legacy(root, manifest):
    verify(root, manifest)
    ids = run([*DOCKER, 'ps', '-aq', '--filter', 'label=com.docker.compose.project=lanshare',
               '--filter', 'label=com.docker.compose.service=deepseek-runtime']).decode().split()
    if len(ids) > 1: raise ValueError('Unexpected multiple legacy Agent containers')
    for identifier in ids:
        run([*DOCKER, 'stop', '--time', '30', identifier], timeout=40)
        run([*DOCKER, 'rm', identifier])
    # Legacy state/image remain available for a deliberate pre-migration rollback.
    # No data removal or blanket compose --remove-orphans is performed.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--mode', choices=('preflight', 'stage', 'activate', 'verify', 'retire-legacy'), required=True)
    args = parser.parse_args()
    manifest = preflight(args.root)
    if args.mode == 'stage': stage(args.root, manifest)
    elif args.mode == 'activate':
        stage(args.root, manifest)
        atomic_write(args.root / 'docker.env', environment_text((args.root / 'docker.env').read_text()), 0o600)
        run(['/usr/bin/systemctl', 'daemon-reload'])
        run(['/usr/bin/systemctl', 'enable', SERVICE])
        run(['/usr/bin/systemctl', 'restart', SERVICE], timeout=100)
    elif args.mode == 'verify': verify(args.root, manifest)
    elif args.mode == 'retire-legacy': retire_legacy(args.root, manifest)
    print(json.dumps({'mode': args.mode, 'status': 'passed', **manifest}, sort_keys=True))


if __name__ == '__main__':
    try: main()
    except Exception as error:
        print('DSH_DEPLOY_FAILED: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
