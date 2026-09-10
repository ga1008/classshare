"""Provider ownership and event receipts against a real ACP fixture process."""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

from classroom_app.services.agent_runtime import AcpClientOptions, AcpProtocolError
from classroom_app.services.agent_runtime.dsh_provider import (
    DshProvider, DshRunIdentity, DshRuntimeEvidence, DshSessionRef,
)


PEER = r'''
import json, sys
pending = None
reasoning='high'
def config(): return [{"id":"reasoning_effort","type":"select","currentValue":reasoning,"options":[{"value":value,"name":value} for value in ("off","low","high","max")]}]
def send(value): print(json.dumps(value), flush=True)
def result(i, value): send({"jsonrpc":"2.0","id":i,"result":value})
def update(value, session="session-one"):
    send({"jsonrpc":"2.0","method":"session/update","params":{"sessionId":session,"update":value}})
for line in sys.stdin:
    row=json.loads(line)
    method, params, i=row.get("method"),row.get("params",{}),row.get("id")
    if method=="initialize":
        result(i,{"protocolVersion":1,"agentInfo":{"name":"fixture","version":"0.0.1"},"agentCapabilities":{"sessionCapabilities":{"resume":{},"close":{},"list":{}},"mcpCapabilities":{"http":True}},"authMethods":[]})
    elif method=="session/new":
        if params.get("cwd")=="/workspace": result(i,{"sessionId":"container-session","configOptions":config()})
        else: result(i,{"sessionId":"session-one","configOptions":config()})
    elif method in ("session/resume","session/close"): result(i,{"configOptions":config()})
    elif method=="session/set_config_option":
        assert params["configId"]=="reasoning_effort"
        reasoning=params["value"]
        result(i,{"configOptions":config()})
    elif method=="session/prompt":
        text=params["prompt"][0]["text"]
        if text=="hold":
            pending=i
            update({"sessionUpdate":"tool_call","toolCallId":"held","status":"in_progress"})
        elif text in ("permission","foreign-permission"):
            pending=i
            send({"jsonrpc":"2.0","id":"permission-one","method":"session/request_permission","params":{"sessionId":"foreign" if text=="foreign-permission" else "session-one","toolCall":{"toolCallId":"write-one"},"options":[{"optionId":"allow-once","kind":"allow_once","name":"Allow"},{"optionId":"reject-once","kind":"reject_once","name":"Reject"}]}})
        else:
            update({"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"FOREIGN_SECRET"}},"someone-else")
            update({"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"Hello "}})
            update({"sessionUpdate":"tool_call","toolCallId":"call-one","title":"Read platform receipt","status":"in_progress"})
            update({"sessionUpdate":"tool_call_update","toolCallId":"call-one","status":"completed","content":[{"type":"content","content":{"type":"text","text":"operation_id=actual-fixture-9"}}]})
            update({"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"world"}})
            update({"sessionUpdate":"usage_update","used":14,"size":1000})
            result(i,{"stopReason":"end_turn"})
    elif method=="session/cancel" and pending is not None:
        result(pending,{"stopReason":"cancelled"})
        pending=None
    elif method=="$/cancel_request": pass
    elif not method:
        update({"sessionUpdate":"tool_call_update","toolCallId":"write-one","status":"failed","rawOutput":row.get("result")})
        result(pending,{"stopReason":"end_turn"})
        pending=None
'''


class AgentDshProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.peer = self.root / "acp_peer.py"
        self.peer.write_text(PEER, encoding="utf-8")
        self.identity = DshRunIdentity("task-1", "teacher:8", "attempt-1", "fence-21")
        self.evidence = DshRuntimeEvidence("0.1.5-rc.1", "a" * 64)

    def tearDown(self):
        self.temp.cleanup()

    def provider(self, **kwargs):
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}}
        options = AcpClientOptions((sys.executable, "-u", str(self.peer)), self.root, env,
                                   request_timeout=2, shutdown_timeout=.3)
        return DshProvider(self.identity, options, runtime_evidence=self.evidence,
                           prompt_timeout=3, cancel_timeout=.3, event_timeout=1, **kwargs)

    async def test_filters_foreign_events_and_drains_callbacks_before_result(self):
        observed = []
        async def consume(event):
            await asyncio.sleep(.01)
            observed.append(event)
        async with self.provider() as provider:
            result = await provider.run([{"type": "text", "text": "hello"}], on_event=consume)
            self.assertTrue(result.completed)
            self.assertEqual(result.final_text, "Hello world")
            self.assertEqual(len(observed), 5)
            self.assertTrue(all(event.identity == self.identity for event in observed))
            self.assertEqual(result.runtime_evidence, self.evidence)
            self.assertEqual(result.tool_receipts[0]["tool_call_id"], "call-one")
            self.assertEqual(result.tool_receipts[0]["source"], "acp")
            self.assertNotIn("committed", result.tool_receipts[0])
            self.assertIn("actual-fixture-9", str(result.tool_receipts[0]["content"]))
            self.assertEqual(result.usage_updates[0]["used"], 14)

    async def test_runner_working_directory_is_separate_from_local_process_cwd(self):
        async with self.provider(runtime_cwd="/workspace") as provider:
            result = await provider.run([{"type": "text", "text": "hello"}])
            self.assertEqual(result.session_ref.runtime_session_id, "container-session")
            self.assertEqual(provider.options.cwd, self.root)

    async def test_reasoning_configuration_is_confirmed_and_reflected_in_result(self):
        async with self.provider(reasoning_effort="off") as provider:
            result = await provider.run([{"type": "text", "text": "hello"}])
            self.assertEqual(result.reasoning_effort, "off")
            options = await provider.configure_reasoning("high")
            self.assertEqual(options[0]["currentValue"], "high")
            result = await provider.run([{"type": "text", "text": "hello"}])
            self.assertEqual(result.reasoning_effort, "high")
        with self.assertRaises(ValueError): self.provider(reasoning_effort="auto")

    async def test_identity_actor_role_attempt_and_fence_block_resume_before_spawn(self):
        for identity in (
            DshRunIdentity("other-task", "teacher:8", "attempt-1", "fence-21"),
            DshRunIdentity("task-1", "student:8", "attempt-1", "fence-21"),
            DshRunIdentity("task-1", "teacher:8", "attempt-2", "fence-21"),
            DshRunIdentity("task-1", "teacher:8", "attempt-1", "fence-20"),
        ):
            provider = self.provider()
            with self.assertRaises(ValueError):
                await provider.run([{"type": "text", "text": "hello"}], session_ref=DshSessionRef(identity, "session-one"))
            self.assertIsNone(provider.client.pid)
            await provider.close()

    async def test_owned_session_ref_resumes_and_retains_launcher_evidence(self):
        async with self.provider() as provider:
            handshake = await provider.start()
            self.assertEqual(handshake["agentInfo"]["version"], "0.0.1")
            ref = DshSessionRef(self.identity, "session-one")
            result = await provider.run([{"type": "text", "text": "hello"}], session_ref=ref)
            self.assertEqual(result.session_ref, ref)
            self.assertEqual(result.runtime_evidence.dsh_package_version, "0.1.5-rc.1")

    async def test_permission_receives_bound_actor_and_requires_offered_one_shot_choice(self):
        identities = []
        async def permit(identity, params):
            identities.append(identity)
            return {"outcome": {"outcome": "selected", "optionId": "invented-admin-grant"}}
        async with self.provider(permission_handler=permit) as provider:
            result = await provider.run([{"type": "text", "text": "permission"}])
            self.assertEqual(identities, [self.identity])
            self.assertEqual(result.tool_receipts[0]["rawOutput"], {"outcome": {"outcome": "cancelled"}})

    async def test_foreign_permission_never_reaches_platform_handler(self):
        async def forbidden(identity, params):
            self.fail("Foreign session reached authorization callback")
        async with self.provider(permission_handler=forbidden) as provider:
            result = await provider.run([{"type": "text", "text": "foreign-permission"}])
            self.assertEqual(result.tool_receipts[0]["rawOutput"]["outcome"]["outcome"], "cancelled")

    async def test_cancel_waits_for_actual_prompt_settlement(self):
        started = asyncio.Event()
        async def consume(event): started.set()
        async with self.provider() as provider:
            running = asyncio.create_task(provider.run([{"type": "text", "text": "hold"}], on_event=consume))
            await asyncio.wait_for(started.wait(), 2)
            await provider.cancel()
            result = await running
            self.assertFalse(result.completed)
            self.assertTrue(result.submitted)
            self.assertEqual(result.stop_reason, "cancelled")

    async def test_callback_failure_and_interval_overflow_stop_runtime(self):
        async def fail(event): raise RuntimeError("ledger sink failed")
        async with self.provider() as provider:
            with self.assertRaisesRegex(RuntimeError, "ledger sink failed"):
                await provider.run([{"type": "text", "text": "hello"}], on_event=fail)
        async with self.provider(max_result_bytes=40) as provider:
            with self.assertRaises(AcpProtocolError):
                await provider.run([{"type": "text", "text": "hello"}])


if __name__ == "__main__":
    unittest.main()
