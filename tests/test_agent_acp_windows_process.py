"""Real Windows launcher, early descendants and interrupted startup gates."""

import asyncio
import ctypes
import gc
import os
import sys
import tempfile
import unittest
from ctypes import wintypes
from pathlib import Path
from unittest.mock import patch

from classroom_app.services.agent_runtime import AcpClientOptions, AcpStdioClient, AcpTransportClosed


EARLY_PEER = r'''
import json, os, subprocess, sys, time
from pathlib import Path
child = subprocess.Popen([sys.executable, "-u", "-c", "import os, time; from pathlib import Path; Path('child.tmp').write_text(str(os.getpid())); Path('child.tmp').replace('child.pid'); time.sleep(30)"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
deadline = time.monotonic() + 5
while not Path("child.pid").exists() and time.monotonic() < deadline:
    time.sleep(.005)
print(json.dumps({"jsonrpc": "2.0", "method": "ready", "params": {"peer": os.getpid(), "child_launcher": child.pid, "child": int(Path("child.pid").read_text()), "unicode": os.environ.get("ACP_UNICODE"), "ambient": os.environ.get("ACP_AMBIENT")}}), flush=True)
for line in sys.stdin:
    pass
'''


@unittest.skipUnless(os.name == "nt", "Windows creation-time job containment")
class AgentAcpWindowsProcessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from classroom_app.services.agent_runtime import windows_process

        self.windows = windows_process
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "ACP 空间"
        self.root.mkdir()
        self.peer = self.root / "早生后代.py"
        self.peer.write_text(EARLY_PEER, encoding="utf-8")
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, arguments, result in (
            ("OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            ("IsProcessInJob", [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
            ("WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            ("GetCurrentProcess", [], wintypes.HANDLE),
            ("GetProcessHandleCount", [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
        ):
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result
        self.handles = []
        self.clients = []

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        for handle in self.handles:
            self.api.CloseHandle(handle)
        self.temp.cleanup()

    def client(self, *, executable=None, cwd=None):
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}}
        env.update(PYTHONIOENCODING="utf-8", ACP_UNICODE="你好🙂")
        client = AcpStdioClient(AcpClientOptions(
            argv=(executable or sys.executable, "-u", str(self.peer)), cwd=cwd or self.root, env=env,
            request_timeout=3, shutdown_timeout=.3,
        ))
        self.clients.append(client)
        return client

    def open_process(self, pid):
        handle = self.api.OpenProcess(0x100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
        self.assertTrue(handle, f"Cannot open fixture process {pid}")
        self.handles.append(handle)
        return handle

    def assert_exited(self, handles):
        for handle in handles:
            self.assertEqual(self.api.WaitForSingleObject(handle, 2000), 0)

    def handle_count(self):
        count = wintypes.DWORD()
        self.assertTrue(self.api.GetProcessHandleCount(self.api.GetCurrentProcess(), ctypes.byref(count)))
        return count.value

    async def early_processes(self, client):
        event = await asyncio.wait_for(client.next_notification(), 5)
        self.assertEqual(event.method, "ready")
        self.assertEqual(event.params["unicode"], "你好🙂")
        self.assertIsNone(event.params["ambient"])
        return [self.open_process(pid) for pid in
                (client.pid, event.params["peer"], event.params["child_launcher"], event.params["child"])]

    async def test_venv_launcher_and_early_descendants_are_in_exact_job(self):
        client = self.client()
        with patch.dict(os.environ, {"ACP_AMBIENT": "must not leak"}):
            await client.start()
        handles = await self.early_processes(client)
        for handle in handles:
            contained = wintypes.BOOL()
            self.assertTrue(self.api.IsProcessInJob(handle, client._job._handle, ctypes.byref(contained)))
            self.assertTrue(contained.value)
        await asyncio.gather(client.close(), client.close())
        self.assert_exited(handles)

    async def test_job_attribute_failure_never_launches_uncontained_process(self):
        client = self.client()
        update = self.windows._api.UpdateProcThreadAttribute

        def reject_job(attributes, flags, attribute, *args):
            if attribute == 0x0002000D:
                ctypes.set_last_error(87)
                return False
            return update(attributes, flags, attribute, *args)

        with patch.object(self.windows._api, "UpdateProcThreadAttribute", side_effect=reject_job), \
                patch.object(self.windows._api, "CreateProcessW", wraps=self.windows._api.CreateProcessW) as create:
            with self.assertRaises(OSError):
                await client.start()
            create.assert_not_called()
        self.assertIsNone(client.pid)
        self.assertIsNone(client._job._handle)
        with self.assertRaises(AcpTransportClosed):
            await client.start()

    async def test_native_creation_failure_releases_job_and_pipe_handles(self):
        gc.collect()
        baseline = self.handle_count()
        for _ in range(12):
            client = self.client(executable=str(self.root / "missing-python.exe"))
            with self.assertRaises(OSError):
                await client.start()
            self.assertIsNone(client._job._handle)
        gc.collect()
        self.assertLessEqual(self.handle_count(), baseline + 1)

    async def test_pipe_connection_failure_reaps_process_and_releases_handles(self):
        loop = asyncio.get_running_loop()
        connect = loop.connect_read_pipe
        # Fail both before any read pipe and after stdout has been connected.
        for fail_at in (1, 2):
            client = self.client()
            count = 0
            captured = []
            create = self.windows._create_process

            def capture(*args, **kwargs):
                result = create(*args, **kwargs)
                captured.append(self.open_process(result[1]))
                return result

            async def fail_connect(*args, **kwargs):
                nonlocal count
                count += 1
                if count == fail_at:
                    raise OSError("injected ACP pipe connection failure")
                return await connect(*args, **kwargs)

            with self.subTest(fail_at=fail_at), \
                    patch.object(self.windows, "_create_process", side_effect=capture), \
                    patch.object(loop, "connect_read_pipe", side_effect=fail_connect):
                with self.assertRaisesRegex(OSError, "injected ACP pipe"):
                    await asyncio.wait_for(client.start(), 3)
            self.assert_exited(captured)
            self.assertIsNone(client._job._handle)

    async def test_two_start_cancellations_preserve_cleanup_owner(self):
        client = self.client()
        loop = asyncio.get_running_loop()
        connect = loop.connect_read_pipe
        entered, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
        cleanup = self.windows._cleanup_start

        async def gated_connect(*args, **kwargs):
            entered.set()
            await release.wait()
            return await connect(*args, **kwargs)

        async def observed_cleanup(*args):
            try:
                await cleanup(*args)
            finally:
                cleaned.set()

        with patch.object(loop, "connect_read_pipe", side_effect=gated_connect), \
                patch.object(self.windows, "_cleanup_start", side_effect=observed_cleanup):
            task = asyncio.create_task(client.start())
            try:
                await asyncio.wait_for(entered.wait(), 3)
                deadline = loop.time() + 5
                while not (self.root / "child.pid").exists():
                    self.assertLess(loop.time(), deadline)
                    await asyncio.sleep(.01)
                child = self.open_process(int((self.root / "child.pid").read_text()))
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assert_exited([child])
                self.assertIsNone(client._job._handle)
            finally:
                release.set()
            await asyncio.wait_for(cleaned.wait(), 3)
            await asyncio.sleep(0)
            self.assertFalse(self.windows._startup_cleanups)
        await client.close()
        with self.assertRaises(AcpTransportClosed):
            await client.start()

    async def test_cancelling_close_cannot_abandon_early_descendants(self):
        client = self.client()
        await client.start()
        handles = await self.early_processes(client)
        entered, release = asyncio.Event(), asyncio.Event()
        wait = client._process.wait

        async def gated_wait():
            entered.set()
            await release.wait()
            return await wait()

        with patch.object(client._process, "wait", side_effect=gated_wait):
            task = asyncio.create_task(client.close())
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            release.set()
            await client.close()
        self.assert_exited(handles)

    async def test_failed_pipe_setup_waits_for_exit_callback_before_closing_handle(self):
        client = self.client()
        loop = asyncio.get_running_loop()
        wait_for_handle = loop._proactor.wait_for_handle
        native_done = asyncio.Event()
        callbacks = []
        process_handles = []

        class DeferredCallback:
            def __init__(self, future):
                self.future = future

            def __await__(self):
                return self.future.__await__()

            def add_done_callback(self, callback):
                def defer(future):
                    callbacks.append(lambda: callback(future))
                    native_done.set()
                self.future.add_done_callback(defer)

        def defer_wait(handle, *args, **kwargs):
            process_handles.append(handle)
            return DeferredCallback(wait_for_handle(handle, *args, **kwargs))

        with patch.object(loop._proactor, "wait_for_handle", side_effect=defer_wait), \
                patch.object(loop, "connect_read_pipe", side_effect=OSError("injected pipe failure")):
            task = asyncio.create_task(client.start())
            try:
                await asyncio.wait_for(native_done.wait(), 3)
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                self.assertEqual(self.api.WaitForSingleObject(process_handles[0], 0), 0)
            finally:
                for callback in callbacks:
                    callback()
                with self.assertRaisesRegex(OSError, "injected pipe failure"):
                    await asyncio.wait_for(task, 3)

    async def test_concurrent_launches_do_not_accumulate_native_handles(self):
        async def batch(index):
            clients = []
            for number in range(4):
                cwd = self.root / f"batch-{index}-{number}"
                cwd.mkdir()
                clients.append(self.client(cwd=cwd))
            await asyncio.gather(*(client.start() for client in clients))
            groups = await asyncio.gather(*(self.early_processes(client) for client in clients))
            await asyncio.gather(*(client.close() for client in clients))
            for group in groups:
                self.assert_exited(group)
                for handle in group:
                    self.api.CloseHandle(handle)
                    self.handles.remove(handle)
            for client in clients:
                self.clients.remove(client)

        await batch(0)  # Warm IOCP registrations before measuring.
        gc.collect()
        baseline = self.handle_count()
        for index in range(1, 4):
            await batch(index)
            gc.collect()
            self.assertLessEqual(self.handle_count(), baseline + 2)


if __name__ == "__main__":
    unittest.main()
