#!/usr/bin/env python3
"""Exercise the real Linux host launcher process in its isolated probe root."""
import argparse
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if root != Path('/lanshare/.codex-temp/dsh-migration-20260910') or os.getuid() != 0:
        parser.error('Requires the explicitly isolated Linux fixture root')
    ipc = root / 'service-poc-ipc'
    # Match the installer's finite profile permission normalization after an
    # archive made on Windows. The production launcher rejects writable roots.
    profile = root / 'deployment/dsh/profile'
    if profile.is_symlink(): raise ValueError('Redirected fixture profile')
    os.chmod(profile, 0o755)
    command = [sys.executable, str(root / 'tools/agent_dsh_launcher.py'), '--image', args.image,
        '--profile', str(root / 'deployment/dsh/profile'), '--task-root', str(root / 'service-poc-tasks'),
        '--state-root', str(root / 'service-poc-state'), '--socket-root', str(ipc), '--max-concurrency', '1']
    report = {'paid_model_requests': 0, 'real_credentials_used': False, 'checks': {}}
    wrapped = [sys.executable, '-c', "import faulthandler,runpy,sys; faulthandler.dump_traceback_later(12); sys.argv.pop(0); runpy.run_path(sys.argv[0],run_name='__main__')", *command[1:]]
    started = subprocess.Popen(wrapped, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent'})
    peers = []
    try:
        path = ipc / 'control/launcher.sock'
        deadline = time.monotonic() + 15
        while True:
            if started.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('Real launcher did not start')
            try:
                with socket.socket(socket.AF_UNIX) as client:
                    client.settimeout(3)
                    client.connect(str(path))
                    client.sendall(b'{"version":1,"action":"probe"}\n')
                    with client.makefile('rb') as stream:
                        report['evidence'] = json.loads(stream.readline(16384))
                break
            except (FileNotFoundError, ConnectionRefusedError): time.sleep(.05)
        assert report['evidence']['status'] == 'ready'
        report['checks']['root_control_socket'] = stat.S_IMODE(path.stat().st_mode) == 0o660 and path.stat().st_uid == 0
        report['checks']['separate_gateway_socket'] = (ipc / 'gateway.sock').is_socket() and not (ipc / 'control/gateway.sock').exists()
        second = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5,
                                env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent'})
        report['checks']['singleton_rejects_second_process'] = second.returncode != 0 and started.poll() is None
        time.sleep(.1)
        for _ in range(16):
            client = socket.socket(socket.AF_UNIX)
            client.settimeout(3)
            client.connect(str(path))
            peers.append(client)
            time.sleep(.03)
        time.sleep(.1)
        with socket.socket(socket.AF_UNIX) as overflow:
            overflow.settimeout(3)
            overflow.connect(str(path))
            report['checks']['connection_limit_rejects_seventeenth'] = overflow.recv(1024) == b''
        assert all(report['checks'].values())
    finally:
        for peer in peers: peer.close()
        started.terminate()
        try:
            _stdout, stderr = started.communicate(timeout=15)
            # This launcher has never received a task or credential. Retain
            # only its bounded setup diagnostics for a failed isolated probe.
            if started.returncode: report['startup_diagnostic'] = stderr.decode(errors='replace')[-4000:]
        except subprocess.TimeoutExpired:
            started.kill()
            _stdout, stderr = started.communicate()
            report['startup_diagnostic'] = stderr.decode(errors='replace')[-8000:]
            (root / 'launcher-service-poc.json').write_text(json.dumps(report, indent=2) + '\n')
            raise RuntimeError('Launcher did not terminate promptly')
        report['checks']['sigterm_clean_exit'] = started.returncode == 0
        (root / 'launcher-service-poc.json').write_text(json.dumps(report, indent=2) + '\n')
    assert all(report['checks'].values())
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__': main()
