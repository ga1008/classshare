"""Exercise wire and lifecycle behaviour through a real stdio subprocess."""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from classroom_app.services.agent_runtime import (
    AcpClientOptions, AcpProtocolError, AcpRemoteError, AcpRequestTimeout,
    AcpStdioClient, AcpTransportClosed,
)


PEER = r'''
import json, os, subprocess, sys, threading, time
from pathlib import Path
lock = threading.Lock()
approval_request = None

def send(frame):
    with lock:
        print(json.dumps(frame, ensure_ascii=False), flush=True)

def result(request_id, value):
    send({"jsonrpc": "2.0", "id": request_id, "result": value})

for line in sys.stdin:
    frame = json.loads(line)
    method, params, request_id = frame.get("method"), frame.get("params", {}), frame.get("id")
    if not method:
        result(approval_request, frame.get("result", {"error": frame.get("error")}))
    elif method == "echo":
        threading.Timer(params.get("delay", 0), result, (request_id, params.get("value"))).start()
    elif method == "error":
        send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32001, "message": "fixture refused", "data": {"retryable": False}}})
    elif method == "hold":
        pass
    elif method == "$/cancel_request":
        send({"jsonrpc": "2.0", "method": "cancel-observed", "params": params})
        result(params["requestId"], "late cancelled response")
    elif method == "session/cancel":
        send({"jsonrpc": "2.0", "method": "session-cancel-observed", "params": params})
    elif method == "approval":
        approval_request = request_id
        send({"jsonrpc": "2.0", "id": "approval-1", "method": "session/request_permission", "params": {"sessionId": "own-session", "toolCall": {"toolCallId": "tool-1"}, "options": [{"optionId": "allow-once", "kind": "allow_once", "name": "Allow"}]}})
    elif method == "cancel-handler":
        send({"jsonrpc": "2.0", "method": "$/cancel_request", "params": {"requestId": "approval-1"}})
        result(request_id, "cancel sent")
    elif method == "event":
        send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "own-session", "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "你好"}}}})
        result(request_id, {})
    elif method == "bad":
        print("not JSON", flush=True)
    elif method == "nonfinite":
        print('{"jsonrpc":"2.0","id":1,"result":NaN}', flush=True)
    elif method == "incomplete":
        sys.stdout.write('{"jsonrpc":"2.0"}')
        sys.stdout.flush()
        os._exit(3)
    elif method == "oversized":
        print("x" * 4096, flush=True)
    elif method == "flood":
        for index in range(10):
            send({"jsonrpc": "2.0", "method": "event", "params": {"index": index}})
        result(request_id, {})
    elif method == "stderr":
        sys.stderr.write("z" * 10000)
        sys.stderr.flush()
        result(request_id, {})
    elif method == "spawn":
        child = subprocess.Popen([sys.executable, "-c", "import time; from pathlib import Path; Path('child.started').touch(); time.sleep(1.2); Path('child.survived').touch()"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 2
        while not Path("child.started").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        result(request_id, {"pid": child.pid, "started": Path("child.started").exists()})
    elif method == "exit":
        os._exit(7)
'''


class AgentAcpClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.peer = self.root / "peer.py"
        self.peer.write_text(PEER, encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def client(self, *, request_handler=None, **kwargs):
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}}
        env["PYTHONIOENCODING"] = "utf-8"
        options = AcpClientOptions(
            argv=(sys.executable, "-u", str(self.peer)), cwd=self.root, env=env,
            request_timeout=2, shutdown_timeout=.3, **kwargs,
        )
        return AcpStdioClient(options, request_handler=request_handler)

    async def test_out_of_order_concurrent_requests_and_unicode_events(self):
        async with self.client() as client:
            values = await asyncio.gather(
                client.request("echo", {"value": "slow", "delay": .1}),
                client.request("echo", {"value": "快"}),
            )
            self.assertEqual(values, ["slow", "快"])
            await client.request("event")
            notification = await client.next_notification()
            self.assertEqual(notification.method, "session/update")
            self.assertEqual(notification.params["update"]["content"]["text"], "你好")
        self.assertEqual(client.returncode, 0)

    async def test_remote_error_preserves_structured_code(self):
        async with self.client() as client:
            with self.assertRaises(AcpRemoteError) as raised:
                await client.request("error")
            self.assertEqual(raised.exception.code, -32001)
            self.assertEqual(raised.exception.data, {"retryable": False})
            self.assertEqual(await client.request("echo", {"value": "still open"}), "still open")

    async def test_timeout_sends_cancel_and_late_response_does_not_poison_next_request(self):
        async with self.client() as client:
            with self.assertRaises(AcpRequestTimeout):
                await client.request("hold", timeout=.08)
            event = await client.next_notification()
            self.assertEqual(event.method, "cancel-observed")
            self.assertIsInstance(event.params["requestId"], int)
            self.assertEqual(await client.request("echo", {"value": 42}), 42)

    async def test_caller_cancellation_and_session_cancel_notification(self):
        async with self.client() as client:
            held = asyncio.create_task(client.request("hold"))
            await client.request("echo", {"value": "barrier"})
            held.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await held
            self.assertEqual((await client.next_notification()).method, "cancel-observed")
            await client.cancel_session("own-session")
            self.assertEqual((await client.next_notification()).params["sessionId"], "own-session")

    async def test_missing_permission_handler_fails_closed(self):
        async with self.client() as client:
            self.assertEqual(await client.request("approval"), {"outcome": {"outcome": "cancelled"}})

    async def test_permission_handler_can_make_rpc_without_deadlocking_reader(self):
        async def answer(method, params):
            self.assertEqual(method, "session/request_permission")
            self.assertEqual(params["sessionId"], "own-session")
            self.assertEqual(await client.request("echo", {"value": "checked"}), "checked")
            return {"outcome": {"outcome": "selected", "optionId": "allow-once"}}

        async with self.client(request_handler=answer) as client:
            result = await client.request("approval")
            self.assertEqual(result["outcome"]["optionId"], "allow-once")

    async def test_peer_cancels_pending_handler(self):
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def answer(method, params):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async with self.client(request_handler=answer) as client:
            approval = asyncio.create_task(client.request("approval"))
            await asyncio.wait_for(entered.wait(), 2)
            await client.request("cancel-handler")
            self.assertTrue(cancelled.is_set())
            self.assertEqual((await approval)["error"]["code"], -32800)

    async def test_invalid_and_oversized_stdout_fail_pending_requests(self):
        for mode in ("bad", "nonfinite", "incomplete", "oversized"):
            with self.subTest(mode=mode):
                async with self.client(max_frame_bytes=1024) as client:
                    with self.assertRaises(AcpProtocolError):
                        await client.request(mode)
                self.assertIsNotNone(client.returncode)

    async def test_permission_handler_timeout_does_not_grant_or_block_reader(self):
        async def answer(method, params):
            await asyncio.Event().wait()

        async with self.client(request_handler=answer, handler_timeout=.05) as client:
            self.assertEqual((await client.request("approval"))["error"]["code"], -32603)
            self.assertEqual(await client.request("echo", {"value": "recovered"}), "recovered")

    async def test_outgoing_limit_and_notification_overflow_are_bounded(self):
        async with self.client(max_frame_bytes=1024) as client:
            with self.assertRaises(AcpProtocolError):
                await client.request("echo", {"value": "x" * 2000})
            self.assertEqual(await client.request("echo", {"value": "ok"}), "ok")
        async with self.client(notification_capacity=2) as client:
            with self.assertRaises(AcpProtocolError):
                await client.request("flood")

    async def test_process_exit_wakes_waiters_and_stderr_is_bounded(self):
        async with self.client(stderr_tail_bytes=128) as client:
            await client.request("stderr")
            await asyncio.sleep(.03)
            self.assertEqual(len(client.stderr_tail), 128)
            waiter = asyncio.create_task(client.next_notification())
            with self.assertRaises(AcpTransportClosed):
                await client.request("exit")
            with self.assertRaises(AcpTransportClosed):
                await waiter
        self.assertEqual(client.returncode, 7)

    async def test_close_is_idempotent_and_reaps_descendant_after_parent_eof(self):
        client = self.client()
        await client.start()
        child = await client.request("spawn")
        self.assertTrue(child["started"])
        await asyncio.gather(client.close(), client.close())
        self.assertIsNotNone(client.returncode)
        await asyncio.sleep(1.4)
        self.assertFalse((self.root / "child.survived").exists())
        with self.assertRaises(AcpTransportClosed):
            await client.start()

    async def test_closing_fails_pending_requests(self):
        client = self.client()
        held = asyncio.create_task(client.request("hold"))
        await client.request("echo", {"value": "barrier"})
        await client.close()
        with self.assertRaises(AcpTransportClosed):
            await held


if __name__ == "__main__":
    unittest.main()
