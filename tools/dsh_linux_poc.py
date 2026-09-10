#!/usr/bin/env python3
"""Isolated real-image ACP/MCP/Linux probe. Synthetic tokens/model responses only."""
import argparse
import contextlib
import http.server
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import agent_dsh_launcher as launcher

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, required=True)
parser.add_argument('--image', required=True)
parser.add_argument('--run-id', default='', help='New probe attempt; preserves earlier report and task directories')
args = parser.parse_args()
root = args.root.resolve(strict=True)
lab_root = Path('/lanshare/.codex-temp/dsh-migration-20260910')
if (os.name != 'posix' or args.root.absolute() != root or
        not (root == lab_root or (root.parent == lab_root and re.fullmatch(r'runtime-c[1-9][0-9]*', root.name)))):
    parser.error('This probe requires its explicitly isolated Linux directory')
if args.run_id and not re.fullmatch(r'[a-z][a-z0-9-]{0,11}', args.run_id):
    parser.error('Invalid probe run id')
suffix = '-' + args.run_id if args.run_id else ''
report_path = root / ('linux-poc-result' + suffix + '.json')
if report_path.exists():
    parser.error('Refusing to replace an existing probe report; use a new isolated cohort')
report = {'kind': 'synthetic_fixture_not_official_DeepSeek', 'real_credentials_used': False,
          'paid_model_requests': 0, 'model_requests': [], 'mcp_methods': [], 'events': [], 'checks': {}}
slow_started = threading.Event()
slow_release = threading.Event()
question_started = threading.Event()
question_state = {}
model_token, tools_token = 'lsagt_' + 'a' * 43, 'lsagt_' + 'b' * 43


class Fixture(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def question_reply(self, value):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(value).encode())

    def do_GET(self):
        assert self.headers.get('Authorization') == 'Bearer ' + tools_token
        if self.path == '/api/agent-bridge/mcp':
            self.send_response(405)
            self.end_headers()
            return
        assert self.path == '/api/agent-bridge/questions/' + question_state['id']
        question_state['polls'] += 1
        if question_state['polls'] >= 2 and question_state['scenario'] == 'poc-question':
            question_state.update(status='answered', answers=[{'id': 'fixture_choice', 'selected': [], 'custom': 'Actual synthetic human answer'}])
        self.question_reply({key: value for key, value in question_state.items() if key in {'id', 'status', 'answers'}})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        if self.path.startswith('/api/agent-bridge/questions'):
            assert self.headers.get('Authorization') == 'Bearer ' + tools_token
            if self.path.endswith('/cancel'):
                assert self.path == '/api/agent-bridge/questions/' + question_state['id'] + '/cancel'
                question_state['status'] = 'canceled'
            else:
                assert self.path == '/api/agent-bridge/questions'
                assert body['timeout_seconds'] == 300
                assert str(uuid.UUID(body['request_id'])) == body['request_id']
                question_state.clear()
                question_state.update(id=str(uuid.uuid4()), status='pending', polls=0,
                                      scenario=body['questions'][0]['question'])
                question_started.set()
            self.question_reply({key: value for key, value in question_state.items() if key in {'id', 'status'}})
            return
        if self.path == '/api/agent-bridge/mcp':
            assert self.headers.get('Authorization') == 'Bearer ' + tools_token
            method = body.get('method')
            report['mcp_methods'].append(method)
            if 'id' not in body:
                self.send_response(202)
                self.end_headers()
                return
            if method == 'initialize':
                value = {'protocolVersion': body['params']['protocolVersion'],
                         'serverInfo': {'name': 'lanshare-linux-poc', 'version': '1'},
                         'capabilities': {'tools': {'listChanged': False}}}
            elif method == 'tools/list':
                value = {'tools': [{'name': 'echo', 'description': 'Synthetic fixture echo, no platform data',
                                   'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']}}]}
            elif method == 'tools/call':
                value = {'content': [{'type': 'text', 'text': 'LINUX_MCP_OK:' + body['params']['arguments']['text']}]}
            elif method == 'ping': value = {}
            else: value = {}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'jsonrpc': '2.0', 'id': body['id'], 'result': value}).encode())
            return
        if self.path == '/api/agent-model/messages':
            assert self.headers.get('Authorization') == 'Bearer ' + model_token
            assert self.headers.get('x-api-key') == model_token
            assert self.headers.get('anthropic-version') == '2023-06-01'
            assert body['model'] == 'deepseek-flash'
            assert body['tools'] == [{'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 3}]
            report['checks']['official_search_proxy_request'] = {
                'path': self.path, 'model': body['model'], 'tools': body['tools'],
                'scoped_auth_forwarded': True, 'synthetic_response_only': True}
            self.question_reply({'content': [
                {'type': 'web_search_tool_result', 'content': [{'type': 'web_search_result',
                    'url': 'https://example.invalid/fixture', 'title': 'Synthetic result'}]},
                {'type': 'text', 'text': 'SYNTHETIC_SEARCH_OK', 'citations': [{
                    'url': 'https://example.invalid/fixture', 'cited_text': 'Synthetic fixture only'}]}]})
            return
        assert self.path == '/api/agent-model/chat/completions'
        assert self.headers.get('Authorization') == 'Bearer ' + model_token
        last = body['messages'][-1]
        text = str(last.get('content', ''))
        if last['role'] == 'user':
            # DSH may append a first-turn skill catalog as another user block.
            text = next((str(row.get('content', '')) for row in reversed(body['messages'])
                         if row['role'] == 'user' and 'poc-' in str(row.get('content', ''))), text)
        report['model_requests'].append({'path': self.path, 'model': body['model'],
                                        'synthetic_scenario': text[:300],
                                        'tools': [t['function']['name'] for t in body.get('tools', [])]})
        if 'poc-slow' in text:
            slow_started.set()
            slow_release.wait(5)
        if last['role'] == 'user':
            if 'poc-write' in text:
                name, arguments = 'write', {'file_path': '/workspace/poc-created.txt', 'content': 'Synthetic Linux ACP file.\n'}
            elif 'poc-shell' in text:
                code = 'import os,json; print(json.dumps({"uid":os.getuid(),"cwd":os.getcwd(),"shell":"ok"}))'
                name, arguments = 'bash', {'command': 'python3 -c ' + shlex.quote(code), 'description': 'Check isolated Linux shell identity'}
            elif 'poc-denied-permission' in text:
                name, arguments = 'bash', {'command': 'touch /workspace/must-not-exist.txt',
                    'description': 'Exercise a declined permission request',
                    'sandbox_permissions': 'danger-full-access', 'justification': 'Synthetic permission denial probe'}
            elif 'poc-search' in text:
                name, arguments = 'web_search', {'queries': ['synthetic isolation fixture']}
            elif 'poc-question' in text:
                name, arguments = 'ask_user_question', {'questions': [{'id': 'fixture_choice', 'question': text,
                    'options': [{'label': 'A'}, {'label': 'B'}], 'multi_select': False}]}
            else:
                name, arguments = 'mcp__lanshare__echo', {'text': 'synthetic-only'}
            delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'linux-fixture-' + str(len(report['model_requests'])),
                      'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments)}}]}
            finish = 'tool_calls'
        else:
            # This official provider deliberately returns cited sources, rather
            # than the upstream assistant prose, as the tool result.
            if 'https://example.invalid/fixture' in str(last.get('content', '')) and 'Synthetic fixture only' in str(last.get('content', '')):
                report['checks']['search_response_in_original_tool_result'] = True
            if '\\"uid\\": 10001' in str(last.get('content', '')) or '"uid":10001' in str(last.get('content', '')).replace(' ', ''):
                report['checks']['shell_nonroot_in_original_tool_result'] = True
            if 'Actual synthetic human answer' in str(body['messages']):
                report['checks']['question_answer_in_original_tool_result'] = True
            delta, finish = {'role': 'assistant', 'content': 'Synthetic fixture finished.'}, 'stop'
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for chunk in ({'id': 'linux-poc', 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                          {'id': 'linux-poc', 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}],
                           'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}):
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError): pass


def main():
    config = SimpleNamespace(image=args.image, profile=root / 'deployment/dsh/profile',
                             task_root=root / ('fixture-tasks' + suffix), state_root=root / ('fixture-state' + suffix),
                             socket_root=root / ('fixture-ipc' + suffix), max_concurrency=1, max_runtime_seconds=120)
    for directory in (config.task_root, config.state_root, config.socket_root): directory.mkdir()
    task_id = 9000000000000000 + int(time.time())
    request = {'version': 1, 'action': 'run', 'task_id': task_id, 'attempt_id': str(uuid.uuid4()),
               'fencing_token': 1, 'actor_id': 'teacher:9000000000000001', 'model_token': model_token,
               'tools_token': tools_token, 'model': 'deepseek-v4-pro', 'search_model': 'deepseek-flash'}
    workspace = config.task_root / 'tasks' / str(task_id)
    workspace.mkdir(parents=True)
    instance = launcher.Launcher(config)
    report['evidence'] = instance.evidence
    # Preserve bounded diagnostics solely for this synthetic, secret-free probe.
    original_arguments = launcher.container_arguments
    def diagnostic_arguments(*values):
        command = original_arguments(*values)
        command[command.index('--log-driver') + 1] = 'local'
        command[1:1] = ['--log-opt', 'max-size=1m', '--log-opt', 'max-file=2']
        return command
    launcher.container_arguments = diagnostic_arguments
    original_remove = instance.remove_confirmed
    def capture_then_remove(name):
        with contextlib.suppress(Exception):
            inspected = json.loads(launcher.docker('inspect', '--type', 'container', name))[0]
            report['final_container_state'] = inspected['State']
            logs = subprocess.run([*launcher.DOCKER, 'logs', '--tail', '100', name],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10)
            if logs.returncode == 0:
                report['container_logs'] = logs.stdout.decode(errors='replace')[-16000:]
        return original_remove(name)
    instance.remove_confirmed = capture_then_remove
    fixture = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
    gateway_path = config.socket_root / 'gateway.sock'
    control_path = config.socket_root / 'launcher.sock'
    for path in (gateway_path, control_path):
        if path.exists():
            if not path.is_socket(): raise ValueError('Unexpected fixture IPC file')
            path.unlink()
    gateway = launcher.UnixServer(str(gateway_path), launcher.GatewayHandler)
    gateway.upstream_host, gateway.upstream_port = '127.0.0.1', fixture.server_port
    os.chmod(gateway_path, 0o660)
    os.chown(gateway_path, 0, 10001)
    control = launcher.UnixServer(str(control_path), launcher.ControlHandler)
    control.launcher = instance
    servers = (fixture, gateway, control)
    for server in servers: threading.Thread(target=server.serve_forever, daemon=True).start()
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(35)
    stream = None
    try:
        connection.connect(str(control_path))
        connection.sendall(json.dumps(request).encode() + b'\n')
        stream = connection.makefile('rb')
        report['checks']['launcher_ready'] = json.loads(stream.readline())
        next_id = 0
        def send(method, params, with_id=True):
            nonlocal next_id
            next_id += 1
            row = {'jsonrpc': '2.0', 'method': method, 'params': params}
            if with_id: row['id'] = next_id
            connection.sendall(json.dumps(row).encode() + b'\n')
            return next_id
        def wait(identifier):
            while True:
                line = stream.readline()
                if not line: raise RuntimeError('Official runner closed ACP before response')
                row = json.loads(line)
                if row.get('method') == 'session/request_permission':
                    report['checks']['permission_requests_denied'] = report['checks'].get('permission_requests_denied', 0) + 1
                    connection.sendall(json.dumps({'jsonrpc': '2.0', 'id': row['id'], 'result': {'outcome': {'outcome': 'cancelled'}}}).encode() + b'\n')
                elif row.get('method'): report['events'].append(row)
                elif row.get('id') == identifier:
                    if 'error' in row: raise RuntimeError(str(row['error']))
                    return row.get('result')
        def rpc(method, params): return wait(send(method, params))
        report['checks']['initialize'] = rpc('initialize', {'protocolVersion': 1, 'clientCapabilities': {}, 'clientInfo': {'name': 'linux-fixture', 'version': '1'}})
        scoped_mcp = {'type': 'http', 'name': 'lanshare', 'url': 'http://127.0.0.1:8787/api/agent-bridge/mcp',
                      'headers': [{'name': 'Authorization', 'value': 'Bearer ' + tools_token}]}
        session = rpc('session/new', {'cwd': '/workspace', 'mcpServers': [scoped_mcp]})['sessionId']
        report['checks']['session_new'] = session
        name = launcher.runner_name(task_id, request['attempt_id'])
        inspection = json.loads(launcher.docker('inspect', '--type', 'container', name))[0]
        report['container_limits'] = {k: inspection['HostConfig'][k] for k in ('NetworkMode', 'ReadonlyRootfs', 'CapDrop', 'PidsLimit', 'Memory', 'MemorySwap', 'NanoCpus', 'SecurityOpt')}
        for prompt in ('poc-mcp', 'poc-write', 'poc-shell', 'poc-question', 'poc-search', 'poc-denied-permission'):
            report['checks'][prompt] = rpc('session/prompt', {'sessionId': session, 'prompt': [{'type': 'text', 'text': prompt}]})
            assert report['checks'][prompt]['stopReason'] == 'end_turn', prompt
        report['checks']['file_exists'] = (workspace / 'poc-created.txt').read_text() == 'Synthetic Linux ACP file.\n'
        report['checks']['mcp_name_unique'] = all(row['tools'].count('mcp__lanshare__echo') == 1 for row in report['model_requests'])
        report['checks']['native_question_tool_unique'] = all(row['tools'].count('ask_user_question') == 1 for row in report['model_requests'])
        assert report['checks'].get('question_answer_in_original_tool_result'), 'Original tool never received answer'
        assert report['checks'].get('search_response_in_original_tool_result'), 'Original tool never received search sources'
        assert report['checks'].get('shell_nonroot_in_original_tool_result'), 'Shell did not report nonroot execution'
        assert report['checks'].get('permission_requests_denied', 0) > 0, 'Permission prompt was not observed'
        report['checks']['denied_command_never_executed'] = not (workspace / 'must-not-exist.txt').exists()
        assert report['checks']['denied_command_never_executed']
        assert report['checks']['file_exists'] and report['checks']['mcp_name_unique'] and report['checks']['native_question_tool_unique']
        question_started.clear()
        pending_question = send('session/prompt', {'sessionId': session, 'prompt': [{'type': 'text', 'text': 'poc-question-cancel'}]})
        if not question_started.wait(10): raise RuntimeError('Question fixture did not start')
        # Establish an acknowledged question and actual waiting poll before
        # canceling; an unacknowledged create is closed by the task finalizer.
        for _ in range(80):
            if question_state.get('polls', 0) > 0: break
            time.sleep(.05)
        assert question_state.get('polls', 0) > 0, 'Question wait did not begin'
        send('session/cancel', {'sessionId': session}, with_id=False)
        report['checks']['question_prompt_cancel'] = wait(pending_question)
        for _ in range(40):
            if question_state.get('status') == 'canceled': break
            time.sleep(.05)
        report['checks']['question_canceled_at_platform'] = question_state.get('status') == 'canceled'
        assert report['checks']['question_canceled_at_platform'], 'Cancel did not close pending question'
        verification = """import os,json,socket
from pathlib import Path
r={'uid':os.getuid(),'interfaces':os.listdir('/sys/class/net')}
try: Path('/var/lib/dsh/profiles/lanshare/cordis.patch.yml').write_text('mutate'); r['readonly_profile']=False
except OSError as e: r['readonly_profile']=e.errno==30
try: socket.create_connection(('172.17.0.1',5432),timeout=.5); r['network_blocked']=False
except OSError as e: r['network_blocked']=True; r['network_errno']=e.errno
print(json.dumps(r))
"""
        report['checks']['linux_boundaries'] = json.loads(launcher.docker('exec', name, 'python3', '-c', verification))
        boundary = report['checks']['linux_boundaries']
        assert boundary['uid'] == 10001 and boundary['interfaces'] == ['lo']
        assert boundary['readonly_profile'] and boundary['network_blocked']
        limits = report['container_limits']
        assert limits['NetworkMode'] == 'none' and limits['ReadonlyRootfs'] and limits['CapDrop'] == ['ALL']
        assert 0 < limits['Memory'] <= 1024 * 1024 * 1024 and 0 < limits['NanoCpus'] <= 1000000000
        assert 0 < limits['PidsLimit'] <= 128 and 'no-new-privileges:true' in limits['SecurityOpt']
        artifact_fixture = (root / 'tests/fixtures/dsh_artifact_generation.py').read_text()
        report['checks']['artifact_libraries'] = json.loads(launcher.docker('exec', name, '/opt/lanshare-dsh/python/bin/python3', '-c', artifact_fixture))
        report['artifact_debian_packages'] = launcher.docker('exec', name, 'dpkg-query', '-W', 'python3-docx', 'python3-openpyxl', 'python3-reportlab', 'python3-pil', 'python3-xlsxwriter').decode().splitlines()
        pending = send('session/prompt', {'sessionId': session, 'prompt': [{'type': 'text', 'text': 'poc-slow'}]})
        if not slow_started.wait(10): raise RuntimeError('Slow model fixture did not start')
        send('session/cancel', {'sessionId': session}, with_id=False)
        report['checks']['running_cancel'] = wait(pending)
        assert report['checks']['running_cancel']['stopReason'] == 'cancelled'
        slow_release.set()
        report['checks']['session_close'] = rpc('session/close', {'sessionId': session})
        # The existing container occupies capacity while a new scoped session
        # is admitted. Reject a second attempt before making its home directory.
        other = {**request, 'attempt_id': str(uuid.uuid4())}
        try:
            instance.run(other, connection)
        except ValueError:
            report['checks']['live_capacity_rejects_second_attempt'] = True
        else:
            raise RuntimeError('Launcher admitted another runner beyond capacity')
        fresh = rpc('session/new', {'cwd': '/workspace', 'mcpServers': [scoped_mcp]})['sessionId']
        slow_started.clear()
        slow_release.clear()
        send('session/prompt', {'sessionId': fresh, 'prompt': [{'type': 'text', 'text': 'poc-slow-stop'}]})
        if not slow_started.wait(10): raise RuntimeError('Stop fixture did not start')
        report['checks']['stop_during_model'] = instance.stop(request)
        assert report['checks']['stop_during_model']['status'] == 'stopped'
        slow_release.set()
    finally:
        slow_release.set()
        with contextlib.suppress(OSError): connection.shutdown(socket.SHUT_RDWR)
        if stream: stream.close()
        connection.close()
        try:
            report['checks']['stop_confirmed'] = instance.stop(request)
        except Exception as exc:
            report['stop_error'] = str(exc)
        for server in reversed(servers):
            server.shutdown()
            server.server_close()
        report['checks']['container_absent'] = launcher.runner_name(task_id, request['attempt_id']) not in instance.names()
        if not report['checks']['container_absent']:
            raise RuntimeError('Fixture runner removal was not confirmed')


try:
    main()
except Exception as exc:
    report['failure'] = str(exc)
finally:
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'checks': list(report['checks']), 'failure': report.get('failure'), 'model_requests': len(report['model_requests'])}))
raise SystemExit(1 if report.get('failure') else 0)
