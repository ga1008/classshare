"""Pinned real ACP + workflow + child Agent/MCP; deterministic local model only."""
import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from classroom_app.services.agent_runtime import AcpClientOptions, AcpStdioClient


async def run(scratch, report_path):
    package = scratch / "node_modules/@deepseek-ai/dsh"
    assert json.loads((package / "package.json").read_text())["version"] == "0.1.5-rc.1"
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "dsh_version": "0.1.5-rc.1",
              "model_source": "deterministic_local_fixture_not_official_DeepSeek", "paid_calls": 0,
              "model_calls": [], "admissions": [], "finish_observations": [], "mcp_calls": [], "checks": {}}
    slow_started, slow_canceled, slow_release = threading.Event(), threading.Event(), threading.Event()
    lock = threading.Lock()
    active = 0
    maximum = 0
    remote_active = 0
    broker_token = "lsagt_" + "fixture_tools_" * 4
    model_token = "lsagt_" + "fixture_model_" * 4

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send_json(self, value, status=200):
            content = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def do_GET(self):
            self.send_json({}, 405)

        def do_DELETE(self):
            self.send_json({})

        def do_POST(self):
            nonlocal active, maximum, remote_active
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            expected = model_token if self.path.endswith("chat/completions") else broker_token
            assert self.headers.get("Authorization") == "Bearer " + expected
            if self.path == "/api/agent-bridge/children/admit":
                with lock:
                    if len(report["admissions"]) >= 4:
                        self.send_json({"error": "exhausted"}, 429)
                        return
                    active += 1
                    maximum = max(maximum, active)
                    item = {**body, "id": str(uuid.uuid4()), "admitted": True, "total_limit": 4}
                    report["admissions"].append(item)
                self.send_json(item)
                return
            if self.path.startswith("/api/agent-bridge/children/") and self.path.endswith("/finish"):
                with lock:
                    active -= 1
                    item = {"id": self.path.split("/")[-2], "runtime_reported_status": body["status"], "host_execution_verified": False}
                    report["finish_observations"].append(item)
                self.send_json(item)
                return
            if self.path == "/api/agent-bridge/mcp":
                method = body.get("method")
                report["mcp_calls"].append({"method": method, "name": body.get("params", {}).get("name")})
                if method == "notifications/cancelled":
                    slow_canceled.set()
                if "id" not in body:
                    self.send_json({}, 202)
                    return
                if method == "initialize":
                    value = {"protocolVersion": body["params"]["protocolVersion"], "capabilities": {"tools": {}}, "serverInfo": {"name": "workflow-fixture", "version": "1"}}
                elif method == "tools/list":
                    value = {"tools": [{"name": "echo", "description": "Echo synthetic text", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}]}
                elif method == "tools/call":
                    text = body["params"]["arguments"]["text"]
                    if text == "SLOW":
                        remote_active += 1
                        slow_started.set()
                        deadline = time.monotonic() + 8
                        while not slow_canceled.is_set() and not slow_release.is_set() and time.monotonic() < deadline:
                            slow_release.wait(.01)
                        remote_active -= 1
                    value = {"content": [{"type": "text", "text": "MCP_ECHO:" + text}]}
                else:
                    value = {}
                self.send_json({"jsonrpc": "2.0", "id": body["id"], "result": value})
                return
            assert self.path == "/api/agent-model/chat/completions"
            messages = body["messages"]
            last = messages[-1]
            user = "\n".join(str(item.get("content", "")) for item in messages if item["role"] == "user")
            child = "CHILD_" in user
            tool_names = [tool["function"]["name"] for tool in body.get("tools", [])]
            report["model_calls"].append({"child": child, "tools": tool_names, "model": body.get("model")})
            name, arguments, content = None, None, None
            if last["role"] == "user":
                if child:
                    name, arguments = "mcp__lanshare__echo", {"text": "SLOW" if "CHILD_SLOW" in user else "OK"}
                else:
                    if "cancel" in user:
                        script = 'return await parallel([()=>agent("CHILD_SLOW"),()=>agent("CHILD_QUEUED")]);'
                    else:
                        script = 'return await parallel([()=>agent("CHILD_PLAIN"),()=>agent("CHILD_STRUCTURED", {schema:{type:"object",properties:{n:{type:"integer"}},required:["n"],additionalProperties:false}})]);'
                    name, arguments = "workflow", {"meta": {"name": "local-workflow", "description": "Synthetic workflow"}, "script": script}
            elif child and "structured_output" in tool_names and last["role"] == "tool":
                name, arguments = "structured_output", {"n": 7}
            else:
                content = "CHILD_DONE" if child else "WORKFLOW_DONE"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            delta = {"role": "assistant", "content": content} if content else {"role": "assistant", "tool_calls": [{"index": 0, "id": "call_" + uuid.uuid4().hex,
                     "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}
            data = {"id": "fixture", "object": "chat.completion.chunk", "created": 0, "model": body["model"],
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
            end = {**data, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if name else "stop"}],
                   "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
            try:
                for item in (data, end):
                    self.wfile.write(("data: " + json.dumps(item) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    # Exact production plugin URL, local fixture only; fail if occupied.
    server = ThreadingHTTPServer(("127.0.0.1", 8787), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory(prefix="workflow-acp-", dir=scratch) as temporary:
        temp = Path(temporary)
        for name in ("home", "workspace", "plugins"):
            (temp / name).mkdir()
        shutil.copytree(REPO / "deployment/dsh/plugins/bounded-workflow", temp / "plugins/bounded-workflow")
        plugin_path = (temp / "plugins/bounded-workflow/index.mjs").as_posix()
        # Test-only root restriction, on publication and before any prompt.
        restrict = temp / "root-restriction.mjs"
        restrict.write_text("export const inject=['agents','tools']; export function apply(ctx){ctx.on('agent/created',({agent})=>{if(agent.session.header.origin!=='subagent')agent.ctx.tools.restrict({deny:['write']});});}\n")
        patch = temp / "patch.yml"
        patch.write_text(f"""- id: session-telemetry-otel
  config: {{mode: DISABLED}}
- id: session-log-deepseek
  config: {{enabled: false}}
- id: plugin-package-inventory-deepseek
  config: {{enabled: false}}
- id: llm-deepseek
  config:
    baseURL: http://127.0.0.1:8787/api/agent-model
    apiKeyEnv: DSH_GATEWAY_TOKEN
    maxTokens: 1024
    reasoningEffort: off
- id: acp
  inject: [acpAppStartup, lanshareBoundedWorkflow]
  config: {{provider: deepseek-official, model: deepseek-v4-pro}}
- id: agent-default-model
  config: {{provider: deepseek-official, model: deepseek-v4-pro}}
- id: workflow-worker-thread
  config: {{provider: lanshare-bounded-spawn, maxConcurrentAgents: 1, maxTotalAgents: 4, maxItemsPerCall: 16, syncTimeoutMs: 1000, disposeGraceMs: 5000}}
- id: tool-subagent
  disabled: true
- id: tool-subagent-fork
  disabled: true
- id: subagent-spawn-in-process
  disabled: true
- id: subagent-fork-in-process
  disabled: true
- id: tool-ralph
  disabled: true
- id: tool-subagent-control
  disabled: true
- id: tool-subagent-list-agents
  disabled: true
- insert:
    - id: lanshare-bounded-workflow
      name: {json.dumps(plugin_path)}
    - id: synthetic-root-restriction
      name: {json.dumps(restrict.as_posix())}
""", encoding="utf-8")
        env = {key: value for key, value in os.environ.items() if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}}
        env.update({"DSH_HOME": str(temp / "home"), "HOME": str(temp / "home"), "USERPROFILE": str(temp / "home"), "DSH_TELEMETRY_DISABLED": "1",
                    "DSH_GATEWAY_TOKEN": model_token, "DSH_BROKER_TOKEN": broker_token, "DSH_GATEWAY_MODEL": "deepseek-v4-pro",
                    "DSH_BROKER_MCP_URL": "http://127.0.0.1:8787/api/agent-bridge/mcp"})
        options = AcpClientOptions(argv=(shutil.which("node"), str(package / "lib/bin.js"), "--profile", "acp", "--patch", str(patch)),
                                   cwd=temp / "workspace", env=env, request_timeout=35, shutdown_timeout=6)
        async def permission(method, params):
            return {"outcome": {"outcome": "cancelled"}}
        client = AcpStdioClient(options, request_handler=permission)
        try:
            async with client:
                await client.request("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
                session = await client.request("session/new", {"cwd": str(temp / "workspace"), "mcpServers": [{"type": "http", "name": "lanshare",
                    "url": "http://127.0.0.1:8787/api/agent-bridge/mcp", "headers": [{"name": "Authorization", "value": "Bearer " + broker_token}]}]})
                result = await client.request("session/prompt", {"sessionId": session["sessionId"], "prompt": [{"type": "text", "text": "normal synthetic workflow"}]})
                report["checks"]["normal_prompt"] = result
                assert result["stopReason"] == "end_turn"
                assert len(report["admissions"]) == 2 and len(report["finish_observations"]) == 2
                assert all(row["runtime_reported_status"] == "completed" for row in report["finish_observations"])
                assert all("workflow" not in row["tools"] and "write" not in row["tools"] for row in report["model_calls"] if row["child"])
                pending = asyncio.create_task(client.request("session/prompt", {"sessionId": session["sessionId"], "prompt": [{"type": "text", "text": "cancel synthetic workflow"}]}))
                for _ in range(1000):
                    if slow_started.is_set():
                        break
                    await asyncio.sleep(.01)
                assert slow_started.is_set(), "Actual child MCP was never called"
                await client.cancel_session(session["sessionId"])
                report["checks"]["cancel_prompt"] = await asyncio.wait_for(pending, 12)
                assert report["checks"]["cancel_prompt"]["stopReason"] == "cancelled"
                assert len(report["admissions"]) == 3, "Queued fourth child was unexpectedly admitted"
                # Cancelling a client-side HTTP await is not evidence that a
                # remote business handler stopped. Preserve this actual boundary.
                await asyncio.sleep(.1)
                report["checks"]["remote_mcp_work_at_parent_cancel_return"] = remote_active
                await client.request("session/close", {"sessionId": session["sessionId"]})
            report["checks"].update({"peak_runtime_admissions": maximum, "remaining_runtime_admissions": active,
                                      "mcp_cancel_notification": slow_canceled.is_set(), "process_exit": client.returncode})
            assert maximum == 1 and active == 0
            report["full_activation_acceptance"] = False
            report["remaining_gaps"] = ["Same-container runtime observations cannot prove host-isolated child quiescence; official workflow VM is not a security boundary."]
            if not slow_canceled.is_set():
                report["remaining_gaps"].append("ACP cancellation drained the local child, but its in-flight synthetic MCP HTTP handler received no cancellation notification and remained active; platform operation/budget ledgers must independently settle it.")
            report["passed"] = True
        except BaseException as error:
            report["passed"] = False
            report["failure"] = {"type": type(error).__name__, "message": str(error)[:1000], "stderr": client.stderr_tail[-6000:]}
            raise
        finally:
            slow_release.set()
            server.shutdown()
            server.server_close()
            report["plugin_sha256"] = {str(path.relative_to(REPO)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
                                        for path in sorted((REPO / "deployment/dsh/plugins/bounded-workflow").glob("*.mjs"))}
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, default=REPO / ".codex-temp/dsh-poc")
    parser.add_argument("--report", type=Path, default=REPO / "docs/agent-dsh-bounded-workflow-poc-2026-09-10.json")
    args = parser.parse_args()
    asyncio.run(run(args.scratch.resolve(), args.report.resolve()))
