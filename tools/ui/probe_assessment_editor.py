"""Run assessment browser acceptance with an owned, synthetic, disposable runtime.

Logs, screenshots, fixture and lifecycle records remain in evidence. Only this
invocation's marked TemporaryDirectory can be removed, after all tracked child
processes exit. PermissionError cleanup retries are bounded to ten seconds;
unconfirmed process exits and permanent cleanup failures preserve exact paths
and produce a nonzero exit. Historical scratch directories are never adopted.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.temp_path_safety import resolve_temp_child

OWNER_FILE = '.assessment-editor-owner.json'


def _new_directory(scratch: Path) -> tempfile.TemporaryDirectory:
    resolve_temp_child(scratch / 'allocation-check', repo_root=ROOT,
                       temp_root=scratch, default=scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    if sys.version_info >= (3, 12):
        return tempfile.TemporaryDirectory(prefix='browser-', dir=scratch, delete=False)
    directory = tempfile.TemporaryDirectory(prefix='browser-', dir=scratch)
    # Python 3.11 lacks delete=False. Prevent implicit deletion when process
    # ownership/exit is uncertain; cleanup() remains our explicit final step.
    directory._finalizer.detach()
    return directory


def _cleanup_owned(directory, scratch: Path, owner: dict, *, processes_exited: bool) -> dict:
    record = {'status': 'preserved', 'attempts': 0, 'error': None}
    if not processes_exited:
        record['error'] = 'Child process exit was not confirmed; owned directory preserved'
        return record
    started = time.monotonic()
    deadline = started + 10.0
    try:
        target = resolve_temp_child(directory.name, repo_root=ROOT,
                                    temp_root=scratch, default=scratch)
        if target.parent != scratch.resolve() or str(target) != owner['owned_directory']:
            raise ValueError('Owned directory path changed; refusing cleanup')
        marker = target / OWNER_FILE
        if marker.is_symlink() or json.loads(marker.read_text(encoding='utf-8')) != owner:
            raise ValueError('Owned directory marker changed; refusing cleanup')
        identity = (target.stat().st_dev, target.stat().st_ino)
        delay = .1
        while True:
            # cleanup() may remove the owner marker before reaching a locked
            # file. On retries verify the original directory identity instead.
            checked = resolve_temp_child(directory.name, repo_root=ROOT,
                                         temp_root=scratch, default=scratch)
            if checked != target:
                raise ValueError('Owned directory path changed during cleanup')
            if target.exists() and (target.stat().st_dev, target.stat().st_ino) != identity:
                raise ValueError('Owned directory was replaced during cleanup')
            record['attempts'] += 1
            try:
                directory.cleanup()
                if target.exists():
                    raise RuntimeError('cleanup() returned with the owned directory still present')
                record['status'] = 'removed'
                break
            except PermissionError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, 1.0)
    except Exception as error:
        record['status'] = 'failed'
        record['error'] = f'{type(error).__name__}: {error}'
    record['elapsed_seconds'] = round(time.monotonic() - started, 3)
    return record


def _stop_process(process: subprocess.Popen, *, require_running: bool = False) -> dict:
    record = {'pid': process.pid, 'returncode': None, 'exit_confirmed': False, 'errors': []}
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                record['errors'].append('Process did not exit within 15 seconds after terminate')
                process.kill()
                process.wait(timeout=15)
        elif require_running:
            record['errors'].append('Synthetic server exited before the runner requested shutdown')
        record['returncode'] = process.poll()
        record['exit_confirmed'] = record['returncode'] is not None
    except Exception as error:
        record['errors'].append(f'{type(error).__name__}: {error}')
        # Even a terminate/exit race must be reported, but an observed exit can
        # still make this run's directory safe to clean.
        try:
            record['returncode'] = process.poll()
            record['exit_confirmed'] = record['returncode'] is not None
        except Exception as poll_error:
            record['errors'].append(f'{type(poll_error).__name__}: {poll_error}')
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, default=ROOT / '.codex-temp/evidence/assessment-editor-20261112')
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    if evidence.is_relative_to((ROOT / '.codex-temp/scratch').resolve()):
        parser.error('Evidence must be outside disposable scratch directories')
    evidence.mkdir(parents=True, exist_ok=True)
    scratch = ROOT / '.codex-temp/scratch/assessment-editor'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    result = 1
    directory = None
    owner = {}
    runtime = None
    server = None
    processes = []
    interrupted_child = False
    report = {'synthetic': True, 'run_id': uuid.uuid4().hex, 'port': port,
              'test_exit_code': None, 'layout_exit_codes': {}, 'errors': [],
              'server_pid': None, 'server_returncode': None, 'server_exit_confirmed': None}

    def run(command, *, name, env, log):
        nonlocal interrupted_child
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        processes.append((name, process))
        try:
            return process.wait()
        except BaseException:
            # A killed test runner could leave browser descendants. Its own
            # eventual exit alone cannot establish that these have exited.
            interrupted_child = True
            raise

    try:
        directory = _new_directory(scratch)
        owned = Path(directory.name).resolve()
        runtime = owned / 'runtime'
        report.update(owned_directory=str(owned), runtime=str(runtime))
        owner = {'run_id': report['run_id'], 'creator_pid': os.getpid(),
                 'owned_directory': str(owned), 'runtime': str(runtime), 'evidence': str(evidence)}
        (owned / OWNER_FILE).write_text(json.dumps(owner, indent=2), encoding='utf-8')
        (evidence / 'runtime-owner.json').write_text(json.dumps(owner, indent=2), encoding='utf-8')
        cache = owned / 'tmp'
        cache.mkdir()
        env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHON_DOTENV_DISABLED='1',
                   P03_PYTHON_EXECUTABLE=sys.executable,
                   P03_RUNTIME_ROOT=str(runtime), LQ_S3_PORT=str(port), ASSESSMENT_EVIDENCE=str(evidence),
                   TEMP=str(cache), TMP=str(cache), TMPDIR=str(cache))
        with (evidence / 'seed.log').open('w', encoding='utf-8') as log:
            for command in (
                [sys.executable, 'tests/e2e/scripts/prepare_ui_v3_runtime.py', '--runtime-root', str(runtime)],
                [sys.executable, 'tools/ui/prepare_lq_s3.py', str(runtime)],
            ):
                code = run(command, name='seed', env=env, log=log)
                if code != 0:
                    raise subprocess.CalledProcessError(code, command)
        shutil.copyfile(runtime / 'fixture.json', evidence / 'fixture.json')
        with (evidence / 'server.log').open('w', encoding='utf-8') as log:
            server = subprocess.Popen([sys.executable, 'tests/e2e/scripts/serve_ui_v3.py',
                '--runtime-root', str(runtime), '--port', str(port)], cwd=ROOT, env=env,
                stdout=log, stderr=subprocess.STDOUT)
            processes.append(('server', server))
            report['server_pid'] = server.pid
            try:
                ready = False
                for _ in range(120):
                    if server.poll() is not None:
                        raise RuntimeError('Synthetic server exited; inspect server.log')
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/internal/health', timeout=1) as response:
                            health = json.load(response)
                        if Path(health['database_path']).resolve() != runtime / 'db/classroom.db':
                            raise RuntimeError('Health reported a database outside the owned runtime')
                        ready = True
                        break
                    except (OSError, KeyError):
                        time.sleep(.5)
                if not ready:
                    raise RuntimeError('Synthetic server did not become ready')
                with (evidence / 'playwright.log').open('w', encoding='utf-8') as browser_log:
                    result = run(['node', 'node_modules/@playwright/test/cli.js', 'test',
                        '--config', 'tests/e2e/assessment-editor.playwright.config.ts'], env=env,
                        name='playwright', log=browser_log)
                    report['test_exit_code'] = result
                if result == 0:
                    fixture = json.loads((runtime / 'fixture.json').read_text(encoding='utf-8'))
                    for role, routes in (
                        ('teacher', '/exam/lq-s3-examFailure/edit,/exam/lq-s3-authoring/edit'),
                        ('student', f"/assignment/{fixture['s3']['draftAssignmentId']},/exam/take/{fixture['s3']['examDraftAssignmentId']}"),
                    ):
                        with (evidence / f'layout-{role}.log').open('w', encoding='utf-8') as probe_log:
                            layout = run(['node', 'tools/ui/probe_layout_readability.cjs',
                                '--base', f'http://127.0.0.1:{port}', '--fixture', str(runtime / 'fixture.json'),
                                '--role', role, '--routes', routes,
                                '--output', str(evidence / f'layout-{role}.json'),
                                '--shots', str(evidence / f'layout-{role}')],
                                env=env, name=f'layout-{role}', log=probe_log)
                            report['layout_exit_codes'][role] = layout
                            if layout != 0:
                                result = result or layout
            finally:
                # Keep the server log open while stopping its writer.
                state = _stop_process(server, require_running=True)
                report.update(server_returncode=state['returncode'], server_exit_confirmed=state['exit_confirmed'])
                report['errors'].extend(state['errors'])
    except BaseException as error:
        result = 130 if isinstance(error, KeyboardInterrupt) else 1
        report['errors'].append(f'{type(error).__name__}: {error}')
    finally:
        report['processes'] = []
        for name, process in reversed(processes):
            state = _stop_process(process)
            report['processes'].append({'name': name, **state})
            report['errors'].extend(state['errors'])
            if process is server:
                report.update(server_returncode=state['returncode'], server_exit_confirmed=state['exit_confirmed'])
        confirmed = not interrupted_child and all(item['exit_confirmed'] for item in report['processes'])
        report['processes_exited'] = confirmed
        if directory is not None:
            report['cleanup'] = _cleanup_owned(directory, scratch, owner, processes_exited=confirmed)
            if report['cleanup']['status'] != 'removed':
                report['errors'].append(report['cleanup']['error'])
        report['runtime_removed'] = runtime is not None and not runtime.exists()
        report['owned_directory_removed'] = directory is not None and not Path(directory.name).exists()
        if report['errors']:
            result = result or 1
        report['exit_code'] = result
        (evidence / 'runtime-lifecycle.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'exit_code': result, 'evidence': str(evidence), 'runtime_removed': report['runtime_removed']}))
    return result


if __name__ == '__main__':
    raise SystemExit(main())
