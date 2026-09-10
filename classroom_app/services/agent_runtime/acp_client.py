"""Bounded ACP JSON-RPC 2.0 stdio client with bidirectional request handling.

No DSH-specific private endpoints or credentials are assumed. A denied or absent
permission handler never grants an action. A returned prompt receipt/outcome is
not evidence that a platform mutation committed: the Broker owns that evidence.
"""

import asyncio
import contextlib
import json
import os
import signal
import subprocess
from typing import Any, Awaitable, Callable

from .contracts import (
    AcpClientOptions, AcpNotification, AcpProtocolError, AcpRemoteError,
    AcpRequestTimeout, AcpTransportClosed,
)

RequestHandler = Callable[[str, Any], Awaitable[Any]]


class _WindowsProcessJob:
    """Kill descendants even when the stdio owner exits before its children."""

    def __init__(self, pid: int):
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t),
                        ("max_ws", ctypes.c_size_t), ("process_limit", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IoCounters),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        api.CreateJobObjectW.restype = wintypes.HANDLE
        api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        api.SetInformationJobObject.restype = wintypes.BOOL
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        api.AssignProcessToJobObject.restype = wintypes.BOOL
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        api.CloseHandle.restype = wintypes.BOOL
        self._api = api
        self._handle = api.CreateJobObjectW(None, None)
        if not self._handle:
            raise OSError(ctypes.get_last_error(), "Cannot create ACP process job")
        process = None
        try:
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not api.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise OSError(ctypes.get_last_error(), "Cannot configure ACP process job")
            process = api.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
            if not process or not api.AssignProcessToJobObject(self._handle, process):
                raise OSError(ctypes.get_last_error(), "Cannot contain ACP process tree")
        except BaseException:
            self.close()
            raise
        finally:
            if process:
                api.CloseHandle(process)

    def close(self):
        if self._handle:
            self._api.CloseHandle(self._handle)
            self._handle = None


class AcpStdioClient:
    def __init__(self, options: AcpClientOptions, *, request_handler: RequestHandler | None = None):
        self.options = options
        self._handler = request_handler
        self._process: asyncio.subprocess.Process | None = None
        self._job: _WindowsProcessJob | None = None
        self._start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._pending: dict[int, asyncio.Future] = {}
        self._incoming: dict[str | int, asyncio.Task] = {}
        self._notifications: asyncio.Queue[AcpNotification] = asyncio.Queue(options.notification_capacity)
        self._closed = asyncio.Event()
        self._error: Exception | None = None
        self._closing = False
        self._next_id = 0
        self._notification_sequence = 0
        self._reader: asyncio.Task | None = None
        self._stderr_reader: asyncio.Task | None = None
        self._close_task: asyncio.Task | None = None
        self._stderr = bytearray()

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    @property
    def returncode(self) -> int | None:
        return self._process.returncode if self._process else None

    @property
    def stderr_tail(self) -> str:
        """Bounded diagnostics; callers must redact before exposing or persisting."""
        return bytes(self._stderr).decode("utf-8", errors="replace")

    @property
    def notification_sequence(self) -> int:
        """Last received notification; a consumer can drain through this boundary."""
        return self._notification_sequence

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def start(self):
        async with self._start_lock:
            self._check_open()
            if self._process is not None:
                return
            spawn_options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
            self._process = await asyncio.create_subprocess_exec(
                *self.options.argv, cwd=str(self.options.cwd), env=dict(self.options.env),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=self.options.max_frame_bytes + 1,
                **spawn_options,
            )
            try:
                if os.name == "nt":
                    self._job = _WindowsProcessJob(self._process.pid)
            except BaseException:
                self._process.kill()
                await self._process.wait()
                self._set_error(AcpTransportClosed("ACP process containment failed"))
                raise
            self._stderr_reader = asyncio.create_task(self._read_stderr())
            self._reader = asyncio.create_task(self._read_frames())

    def _check_open(self):
        if self._closing or self._closed.is_set():
            raise self._error or AcpTransportClosed("ACP transport is closed")

    async def request(self, method: str, params: Any = None, *, timeout: float | None = None) -> Any:
        duration = self.options.request_timeout if timeout is None else timeout
        if not 0 < duration < float("inf"):
            raise ValueError("ACP request timeout must be finite and positive")
        await self.start()
        self._check_open()
        if len(self._pending) >= self.options.max_pending_requests:
            raise AcpProtocolError("ACP pending request limit exceeded")
        self._next_id += 1
        request_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        sent = False
        try:
            # Cancellation during drain may follow a successful write. Sending a
            # cancel for an unobserved id is safe; omitting it can leave work live.
            sent = True
            await self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": {} if params is None else params})
            try:
                return await asyncio.wait_for(asyncio.shield(future), duration)
            except TimeoutError as exc:
                raise AcpRequestTimeout(f"ACP request timed out: {method}") from exc
        except (AcpRequestTimeout, asyncio.CancelledError):
            if sent and not self._closed.is_set() and not self._closing:
                with contextlib.suppress(Exception):
                    await asyncio.shield(self.notify("$/cancel_request", {"requestId": request_id}))
            raise
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # A concurrent transport failure may have settled it.

    async def notify(self, method: str, params: Any = None):
        await self.start()
        await self._send({"jsonrpc": "2.0", "method": method, "params": {} if params is None else params})

    async def cancel_session(self, session_id: str):
        """Cancellation is a notification; await the original prompt settlement."""
        await self.notify("session/cancel", {"sessionId": session_id})

    async def next_notification(self) -> AcpNotification:
        if not self._notifications.empty():
            return self._notifications.get_nowait()
        self._check_open()
        getter = asyncio.create_task(self._notifications.get())
        closer = asyncio.create_task(self._closed.wait())
        try:
            done, _ = await asyncio.wait((getter, closer), return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                return getter.result()
            raise self._error or AcpTransportClosed("ACP transport is closed")
        finally:
            for task in (getter, closer):
                if not task.done():
                    task.cancel()
            await asyncio.gather(getter, closer, return_exceptions=True)

    async def _send(self, frame: dict):
        self._check_open()
        if "method" in frame and (not isinstance(frame["method"], str) or not frame["method"]):
            raise AcpProtocolError("ACP method must be a non-empty string")
        if "params" in frame and not isinstance(frame["params"], (dict, list)):
            raise AcpProtocolError("ACP params must be an object or array")
        try:
            data = json.dumps(frame, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
        except (TypeError, ValueError) as exc:
            raise AcpProtocolError("ACP frame is not JSON serializable") from exc
        if len(data) > self.options.max_frame_bytes:
            raise AcpProtocolError("ACP outgoing frame limit exceeded")
        async with self._write_lock:
            self._check_open()
            try:
                self._process.stdin.write(data)
                await asyncio.wait_for(self._process.stdin.drain(), self.options.write_timeout)
            except (BrokenPipeError, ConnectionResetError, TimeoutError) as exc:
                error = AcpTransportClosed("ACP stdin closed or write timed out")
                self._set_error(error)
                asyncio.create_task(self.close())
                raise error from exc

    def _set_error(self, exc: Exception):
        if self._error is None:
            self._error = exc
        self._closed.set()
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(self._error)

    async def _read_frames(self):
        try:
            while True:
                try:
                    line = await self._process.stdout.readline()
                except ValueError as exc:
                    raise AcpProtocolError("ACP incoming frame limit exceeded") from exc
                if not line:
                    raise AcpTransportClosed("ACP stdout closed")
                if len(line) > self.options.max_frame_bytes or not line.endswith(b"\n"):
                    raise AcpProtocolError("ACP incoming frame is oversized or incomplete")
                try:
                    frame = json.loads(line, parse_constant=self._reject_json_constant)
                except (ValueError, UnicodeError) as exc:
                    raise AcpProtocolError("ACP stdout contains invalid JSON") from exc
                self._dispatch(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._set_error(exc)
            if not self._closing:
                asyncio.create_task(self.close())

    @staticmethod
    def _reject_json_constant(_value):
        raise ValueError("Non-finite JSON number")

    def _dispatch(self, frame: Any):
        if not isinstance(frame, dict) or frame.get("jsonrpc") != "2.0":
            raise AcpProtocolError("ACP invalid JSON-RPC envelope")
        if "method" in frame:
            method = frame["method"]
            if not isinstance(method, str) or not method or "result" in frame or "error" in frame:
                raise AcpProtocolError("ACP invalid method envelope")
            params = frame.get("params", {})
            if "id" not in frame:
                if method == "$/cancel_request":
                    request_id = params.get("requestId") if isinstance(params, dict) else None
                    if isinstance(request_id, (str, int)) and not isinstance(request_id, bool):
                        task = self._incoming.get(request_id)
                        if task:
                            task.cancel()
                    return
                try:
                    self._notification_sequence += 1
                    self._notifications.put_nowait(AcpNotification(method, params, self._notification_sequence))
                except asyncio.QueueFull as exc:
                    raise AcpProtocolError("ACP notification consumer fell behind") from exc
                return
            request_id = frame["id"]
            if not isinstance(request_id, (str, int)) or isinstance(request_id, bool):
                raise AcpProtocolError("ACP invalid request id")
            if request_id in self._incoming or len(self._incoming) >= self.options.max_inbound_requests:
                raise AcpProtocolError("ACP duplicate or excessive server requests")
            task = asyncio.create_task(self._answer(request_id, method, params))
            self._incoming[request_id] = task
            task.add_done_callback(lambda _: self._incoming.pop(request_id, None))
            return
        request_id = frame.get("id")
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
            raise AcpProtocolError("ACP invalid response id")
        if ("result" in frame) == ("error" in frame):
            raise AcpProtocolError("ACP response requires exactly one result or error")
        error = frame.get("error")
        if "error" in frame and (not isinstance(error, dict) or isinstance(error.get("code"), bool)
                                  or not isinstance(error.get("code"), int) or not isinstance(error.get("message"), str)):
            raise AcpProtocolError("ACP invalid error response")
        future = self._pending.get(request_id)
        if future is None or future.done():
            return  # A cancelled/timed-out request may settle late.
        if error is not None:
            future.set_exception(AcpRemoteError(error["code"], error["message"], error.get("data")))
        else:
            future.set_result(frame["result"])

    async def _answer(self, request_id: str | int, method: str, params: Any):
        frame = {"jsonrpc": "2.0", "id": request_id}
        try:
            if self._handler is None:
                if method == "session/request_permission":
                    frame["result"] = {"outcome": {"outcome": "cancelled"}}
                else:
                    frame["error"] = {"code": -32601, "message": "Client method not supported"}
            else:
                frame["result"] = await asyncio.wait_for(self._handler(method, params), self.options.handler_timeout)
        except asyncio.CancelledError:
            frame["error"] = {"code": -32800, "message": "Client request cancelled"}
        except Exception:
            frame["error"] = {"code": -32603, "message": "Client request failed"}
        if not self._closing and not self._closed.is_set():
            try:
                await self._send(frame)
            except Exception as exc:
                self._set_error(exc)
                asyncio.create_task(self.close())

    async def _read_stderr(self):
        while chunk := await self._process.stderr.read(4096):
            self._stderr.extend(chunk)
            del self._stderr[:-self.options.stderr_tail_bytes]

    async def close(self):
        """Idempotent, cancellation-shielded teardown; no invented ACP shutdown RPC."""
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self):
        async with self._start_lock:
            self._closing = True
            self._set_error(AcpTransportClosed("ACP transport closed by client"))
            incoming = tuple(self._incoming.values())
            for task in incoming:
                task.cancel()
            await asyncio.gather(*incoming, return_exceptions=True)
            process = self._process
            if process:
                process.stdin.close()
                try:
                    await asyncio.wait_for(process.wait(), self.options.shutdown_timeout)
                except TimeoutError:
                    if os.name != "nt":
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGTERM)
                    elif self._job:
                        self._job.close()
                    else:
                        process.kill()
                    try:
                        await asyncio.wait_for(process.wait(), self.options.shutdown_timeout)
                    except TimeoutError:
                        process.kill()
                finally:
                    if self._job:
                        self._job.close()
                    elif os.name != "nt":
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
            for task in (self._reader, self._stderr_reader):
                if task and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (self._reader, self._stderr_reader) if task), return_exceptions=True)
