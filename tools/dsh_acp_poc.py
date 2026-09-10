"""Official pinned DSH, local fake model and MCP. No paid/model key requests."""
import asyncio
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from classroom_app.services.agent_runtime import AcpClientOptions, AcpStdioClient
from classroom_app.services.agent_runtime.dsh_provider import DshProvider, DshRunIdentity, DshRuntimeEvidence

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--scratch", type=Path, default=REPO / ".codex-temp/dsh-poc")
parser.add_argument("--report", type=Path)
parser.add_argument("--node", default=shutil.which("node"))
ARGS = parser.parse_args()
ROOT = ARGS.scratch.resolve()
ROOT.mkdir(parents=True, exist_ok=True)
if not ARGS.node:
    parser.error("Node.js 24 is required")
MANIFEST = ROOT / "node_modules/@deepseek-ai/dsh/package.json"
if not MANIFEST.is_file() or json.loads(MANIFEST.read_text(encoding="utf-8"))["version"] != "0.1.5-rc.1":
    parser.error("Install @deepseek-ai/dsh@0.1.5-rc.1 into --scratch with npm first")
WORK = ROOT / "workspace"
HOME = ROOT / "isolated-home"
WORK.mkdir(exist_ok=True)
HOME.mkdir(exist_ok=True)
REPORT = {"checked_at": datetime.now(timezone.utc).isoformat(), "dsh_version": "0.1.5-rc.1",
          "model_mode": "deterministic_local_fixture_not_official_DeepSeek", "real_credentials_used": False, "paid_model_requests": 0,
          "model_fixture_requests": [], "permissions": [], "checks": {}, "events": []}
REPORT["node_version"] = subprocess.check_output([ARGS.node, "--version"], text=True,
    env={key: value for key, value in os.environ.items() if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}}).strip()
REPORT["npm_lock_sha256"] = hashlib.sha256((ROOT / "package-lock.json").read_bytes()).hexdigest()
SLOW_STARTED = threading.Event()
SLOW_RELEASE = threading.Event()

MCP = ROOT / "mcp_fixture.py"
MCP.write_text('''import json, sys
from pathlib import Path
log = Path(__file__).with_name("mcp-events.jsonl")
for line in sys.stdin:
    request = json.loads(line)
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"method":request.get("method"),"params":request.get("params",{})})+"\\n")
    if "id" not in request: continue
    method=request["method"]
    if method=="initialize":
        value={"protocolVersion":request["params"]["protocolVersion"],"serverInfo":{"name":"lanshare-poc-fixture","version":"1"},"capabilities":{"tools":{"listChanged":False}}}
    elif method=="tools/list":
        value={"tools":[{"name":"echo","description":"Echo a fixture string; no platform access","inputSchema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}}]}
    elif method=="tools/call":
        value={"content":[{"type":"text","text":"MCP_OK:"+request["params"]["arguments"]["text"]}]}
    elif method=="ping": value={}
    else:
        print(json.dumps({"jsonrpc":"2.0","id":request["id"],"error":{"code":-32601,"message":"fixture method unknown"}}),flush=True)
        continue
    print(json.dumps({"jsonrpc":"2.0","id":request["id"],"result":value}),flush=True)
''', encoding="utf-8")


class ModelFixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path.startswith('/poc-mcp'):
            if self.path == '/poc-mcp-unavailable':
                self.send_response(503)
                self.end_headers()
                return
            assert self.headers.get('Authorization') == 'Bearer poc-placeholder-no-upstream-key'
            method = body.get('method')
            REPORT.setdefault('http_mcp_methods', []).append(method)
            if 'id' not in body:
                self.send_response(202)
                self.end_headers()
                return
            if method == 'initialize':
                value = {'protocolVersion': body['params']['protocolVersion'], 'serverInfo': {'name': 'local-http-fixture', 'version': '1'}, 'capabilities': {'tools': {'listChanged': False}}}
            elif method == 'tools/list':
                value = {'tools': [{'name': 'echo', 'description': 'Synthetic echo', 'inputSchema': {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']}}]}
            elif method == 'tools/call':
                value = {'content': [{'type': 'text', 'text': 'MCP_OK:' + body['params']['arguments']['text']}]}
            else:
                value = {}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'jsonrpc': '2.0', 'id': body['id'], 'result': value}).encode())
            return
        REPORT["model_fixture_requests"].append({
            "path": self.path, "model": body.get("model"),
            "thinking": body.get("thinking"), "reasoning_effort": body.get("reasoning_effort"),
            "authorization_is_fixture": self.headers.get("Authorization") == "Bearer poc-placeholder-no-upstream-key",
            "tool_names": [tool["function"]["name"] for tool in body.get("tools", [])],
        })
        messages = body["messages"]
        last = messages[-1]
        text = str(last.get("content", ""))
        if "poc slow" in text:
            SLOW_STARTED.set()
            SLOW_RELEASE.wait(5)
        if last["role"] == "user":
            if "permission" in text:
                name = "pwsh" if os.name == "nt" else "bash"
                command = "Write-Output 'PocPermissionMustBeRejected'" if os.name == "nt" else "printf 'PocPermissionMustBeRejected'"
                args = {"command": command, "description": "Test rejected fixture permission", "sandbox_permissions": "danger-full-access", "justification": "Synthetic local PoC; client must reject before execution"}
            else:
                name = next(tool['function']['name'] for tool in body['tools'] if tool['function']['name'].startswith('mcp__') and tool['function']['name'].endswith('__echo'))
                args = {"text": "fixture-only"}
            delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": "poc-tool-"+str(len(REPORT["model_fixture_requests"])), "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
            finish = "tool_calls"
        else:
            delta = {"role": "assistant", "content": "POC complete: local fixture response only."}
            finish = "stop"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for chunk in ({"id": "poc", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                      {"id": "poc", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}}):
            try:
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
        try:
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


async def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelFixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = "http://127.0.0.1:" + str(server.server_port)
    patch = ROOT / "poc.patch.yml"
    patch.write_text(f'''- id: session-telemetry-otel
  config:
    mode: DISABLED
- id: session-log-deepseek
  config:
    enabled: false
- id: plugin-package-inventory-deepseek
  config:
    enabled: false
- id: llm-deepseek
  config:
    baseURL: {endpoint}
    apiKeyEnv: POC_MODEL_TOKEN
    reasoningEffort: off
    maxTokens: 1024
- id: web-search-deepseek
  config:
    baseURL: {endpoint}/anthropic/v1
    apiKeyEnv: POC_MODEL_TOKEN
''', encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}}
    env.update({"DSH_HOME": str(HOME), "HOME": str(HOME), "USERPROFILE": str(HOME),
                "POC_MODEL_TOKEN": "poc-placeholder-no-upstream-key", "DSH_TELEMETRY_DISABLED": "1"})
    command = (str(Path(ARGS.node).resolve()), str(ROOT / "node_modules/@deepseek-ai/dsh/lib/bin.js"), "--profile", "acp", "--patch", str(patch))

    async def permission(method, params):
        REPORT["permissions"].append({"method": method, "params": params})
        if method == "session/request_permission":
            return {"outcome": {"outcome": "selected", "optionId": "reject-once"}}
        raise RuntimeError("unsupported fixture client method")

    client = AcpStdioClient(AcpClientOptions(argv=command, cwd=WORK, env=env, request_timeout=60, shutdown_timeout=5), request_handler=permission)
    event_task = None
    try:
        async with client:
            REPORT["pid"] = client.pid
            async def collect():
                while True:
                    event = await client.next_notification()
                    REPORT["events"].append({"method": event.method, "params": event.params})
            event_task = asyncio.create_task(collect())
            handshake = await client.request("initialize", {"protocolVersion": 1, "clientCapabilities": {}, "clientInfo": {"name": "lanshare-local-poc", "version": "1"}})
            REPORT["checks"]["initialize"] = handshake
            REPORT["checks"]["authenticate"] = await client.request("authenticate", {"methodId": "fixture"})
            session = await client.request("session/new", {"cwd": str(WORK), "mcpServers": [{"name": "poc", "command": sys.executable, "args": ["-u", str(MCP)], "env": [{"name": "PYTHONIOENCODING", "value": "utf-8"}]}]})
            REPORT["checks"]["session_new"] = session
            session_id = session["sessionId"]
            REPORT["checks"]["mcp_prompt"] = await client.request("session/prompt", {"sessionId": session_id, "prompt": [{"type": "text", "text": "poc mcp fixture"}]})
            REPORT["checks"]["permission_prompt"] = await client.request("session/prompt", {"sessionId": session_id, "prompt": [{"type": "text", "text": "poc permission fixture"}]})
            slow = asyncio.create_task(client.request("session/prompt", {"sessionId": session_id, "prompt": [{"type": "text", "text": "poc slow fixture"}]}))
            for _ in range(500):
                if SLOW_STARTED.is_set():
                    break
                await asyncio.sleep(.01)
            assert SLOW_STARTED.is_set(), "slow fixture was never dispatched"
            await client.cancel_session(session_id)
            REPORT["checks"]["running_prompt_cancel"] = await asyncio.wait_for(slow, 5)
            SLOW_RELEASE.set()
            REPORT["checks"]["session_close"] = await client.request("session/close", {"sessionId": session_id})
            REPORT["checks"]["session_list"] = await client.request("session/list", {})
            REPORT["checks"]["session_resume"] = await client.request("session/resume", {"sessionId": session_id, "cwd": str(WORK), "mcpServers": []})
            await client.cancel_session(session_id)
            REPORT["checks"]["resumed_close"] = await client.request("session/close", {"sessionId": session_id})
        REPORT["checks"]["first_process_exit"] = client.returncode
        async with AcpStdioClient(client.options, request_handler=permission) as restarted:
            await restarted.request("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
            REPORT["checks"]["restart_resume"] = await restarted.request("session/resume", {"sessionId": session_id, "cwd": str(WORK), "mcpServers": []})
            await restarted.request("session/close", {"sessionId": session_id})
        REPORT["checks"]["restart_exit"] = restarted.returncode
        assert REPORT["checks"]["running_prompt_cancel"]["stopReason"] == "cancelled"
        assert all(call["authorization_is_fixture"] for call in REPORT["model_fixture_requests"])
        assert any("mcp__poc__echo" in call["tool_names"] for call in REPORT["model_fixture_requests"])
        assert len(REPORT["permissions"]) == 1
        assert any(event["params"].get("update", {}).get("status") == "failed" for event in REPORT["events"])
        digest = hashlib.sha256()
        for path in (HOME / "profiles/acp/package.json", HOME / "profiles/acp/cordis.patch.yml", patch):
            digest.update(path.name.encode() + b"\0" + path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
        REPORT["profile_sha256"] = digest.hexdigest()
        REPORT["profile_kind"] = "official ACP plus isolated local-fixture overlay"
        provider_events = []
        async def on_provider_event(event):
            provider_events.append(event.type)
        async with DshProvider(
            DshRunIdentity("poc-task", "teacher:poc", "poc-attempt", "poc-fence"), client.options,
            runtime_evidence=DshRuntimeEvidence("0.1.5-rc.1", REPORT["profile_sha256"]),
            reasoning_effort="off",
            mcp_servers=[{"name": "poc", "command": sys.executable, "args": ["-u", str(MCP)], "env": [{"name": "PYTHONIOENCODING", "value": "utf-8"}]}],
        ) as provider:
            provider_result = await provider.run([{"type": "text", "text": "poc mcp provider fixture"}], on_event=on_provider_event)
            assert provider_result.completed
            assert provider_result.tool_receipts[0]["status"] == "completed"
            assert REPORT["model_fixture_requests"][-1]["thinking"] == {"type": "disabled"}
            assert REPORT["model_fixture_requests"][-1]["reasoning_effort"] is None
            REPORT["provider_checks"] = {"completed": provider_result.completed, "stop_reason": provider_result.stop_reason,
                "final_text": provider_result.final_text, "tool_receipts": provider_result.tool_receipts,
                "identity": {"task_id": "poc-task", "actor_id": "teacher:poc", "attempt_id": "poc-attempt", "fencing_token": "poc-fence"},
                "event_types": provider_events}
            configured = await provider.configure_reasoning("high")
            deeper = await provider.run([{"type": "text", "text": "poc mcp deeper fixture"}])
            assert deeper.completed and deeper.reasoning_effort == "high"
            assert REPORT["model_fixture_requests"][-1]["thinking"] == {"type": "enabled"}
            assert REPORT["model_fixture_requests"][-1]["reasoning_effort"] == "high"
            REPORT["reasoning_checks"] = {"normal": {"thinking": {"type": "disabled"}, "reasoning_effort": None},
                "deep": {"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
                "config_options": configured, "protocol": "session/set_config_option", "config_id": "reasoning_effort"}
        global_patch = ROOT / 'poc-global-mcp.patch.yml'
        global_patch.write_text(f'''- insert:
    - id: mcp-poc-global
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: pocglobal
        transport: streamable-http
        url: {endpoint}/poc-mcp
        headers:
          Authorization: Bearer poc-placeholder-no-upstream-key
        failOnStartupError: true
''', encoding='utf-8')
        global_options = replace(client.options, argv=(*client.options.argv, '--patch', str(global_patch)))
        scoped = {'type': 'http', 'name': 'pocglobal', 'url': endpoint + '/poc-mcp',
                  'headers': [{'name': 'Authorization', 'value': 'Bearer poc-placeholder-no-upstream-key'}]}
        before = len(REPORT['model_fixture_requests'])
        async with DshProvider(DshRunIdentity('global-scope', 'teacher:poc', 'attempt', 'fence'), global_options,
                runtime_evidence=DshRuntimeEvidence('0.1.5-rc.1', REPORT['profile_sha256']), mcp_servers=[scoped]) as provider:
            result = await provider.run([{'type': 'text', 'text': 'poc global scoped fixture'}])
            assert result.completed and result.tool_receipts[0]['status'] == 'completed'
        assert all(row['tool_names'].count('mcp__pocglobal__echo') == 1 for row in REPORT['model_fixture_requests'][before:])
        REPORT['global_scoped_mcp_checks'] = {'same_server_name': True, 'tool_name_unique': True,
                                            'scoped_tool_receipt_completed': True}
        before = len(REPORT['model_fixture_requests'])
        failure = None
        try:
            async with DshProvider(DshRunIdentity('missing-mcp', 'teacher:poc', 'attempt', 'fence'), client.options,
                    runtime_evidence=DshRuntimeEvidence('0.1.5-rc.1', REPORT['profile_sha256']),
                    mcp_servers=[{**scoped, 'url': endpoint + '/poc-mcp-unavailable'}]) as provider:
                await provider.run([{'type': 'text', 'text': 'must not reach model'}])
        except Exception as exc:
            failure = type(exc).__name__
        assert failure and len(REPORT['model_fixture_requests']) == before
        REPORT['scoped_mcp_admission_failure'] = {'failure_type': failure, 'model_calls_before_tools_ready': 0}
    except Exception as exc:
        REPORT["failure"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        if event_task:
            event_task.cancel()
            await asyncio.gather(event_task, return_exceptions=True)
        await client.close()
        SLOW_RELEASE.set()
        server.shutdown()
        server.server_close()
        REPORT["exit_code"] = client.returncode
        REPORT["stderr_tail"] = client.stderr_tail
        REPORT["passed"] = "failure" not in REPORT
        (ROOT / "poc-result.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
        if ARGS.report:
            ARGS.report.resolve().parent.mkdir(parents=True, exist_ok=True)
            ARGS.report.resolve().write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"checks": list(REPORT["checks"]), "failure": REPORT.get("failure"), "exit_code": client.returncode, "permissions": len(REPORT["permissions"]), "model_fixture_requests": len(REPORT["model_fixture_requests"])}))
    return 0 if REPORT["passed"] else 1

raise SystemExit(asyncio.run(main()))
