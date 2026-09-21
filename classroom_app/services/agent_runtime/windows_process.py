"""Windows ACP subprocesses contained before their first instruction runs.

Windows 10 / Server 2016+ is required for PROC_THREAD_ATTRIBUTE_JOB_LIST.
CPython's STARTUPINFO exposes only handle_list, so the small native launch
adapter below supplies both attributes. CPython still owns pipe creation,
IOCP, stream flow control and process waiting. Keep its private integration
isolated here; exercise this module when upgrading the Windows Python runtime.
"""

import asyncio
import ctypes
import logging
import subprocess
import sys
from asyncio import windows_utils
from asyncio.windows_events import _WindowsSubprocessTransport
from ctypes import wintypes


_startup_cleanups: set[asyncio.Task] = set()
_logger = logging.getLogger(__name__)


class _BasicLimits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t),
                ("max_ws", ctypes.c_size_t), ("process_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", _BasicLimits), ("io", _IoCounters),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]


class _StartupInfo(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("reserved", wintypes.LPWSTR),
                ("desktop", wintypes.LPWSTR), ("title", wintypes.LPWSTR),
                ("x", wintypes.DWORD), ("y", wintypes.DWORD),
                ("x_size", wintypes.DWORD), ("y_size", wintypes.DWORD),
                ("x_chars", wintypes.DWORD), ("y_chars", wintypes.DWORD),
                ("fill", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("show", wintypes.WORD), ("reserved_size", wintypes.WORD),
                ("reserved_bytes", ctypes.c_void_p), ("stdin", wintypes.HANDLE),
                ("stdout", wintypes.HANDLE), ("stderr", wintypes.HANDLE)]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("info", _StartupInfo), ("attributes", ctypes.c_void_p)]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [("process", wintypes.HANDLE), ("thread", wintypes.HANDLE),
                ("pid", wintypes.DWORD), ("tid", wintypes.DWORD)]


_api = ctypes.WinDLL("kernel32", use_last_error=True)
for _name, _arguments, _result in (
    ("CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
    ("SetInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
    ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
    ("InitializeProcThreadAttributeList", [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                          ctypes.POINTER(ctypes.c_size_t)], wintypes.BOOL),
    ("UpdateProcThreadAttribute", [ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t,
                                  ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                  ctypes.c_void_p], wintypes.BOOL),
    ("DeleteProcThreadAttributeList", [ctypes.c_void_p], None),
    ("CreateProcessW", [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
                        ctypes.POINTER(_StartupInfoEx), ctypes.POINTER(_ProcessInformation)], wintypes.BOOL),
):
    _function = getattr(_api, _name)
    _function.argtypes = _arguments
    _function.restype = _result


def _error(message):
    return OSError(ctypes.get_last_error(), message)


class WindowsProcessJob:
    """Non-inheritable job handle; its last close kills every member."""

    def __init__(self):
        self._handle = _api.CreateJobObjectW(None, None)
        if not self._handle:
            raise _error("Cannot create ACP process job")
        try:
            limits = _ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not _api.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise _error("Cannot configure ACP process job")
        except BaseException:
            self.close()
            raise

    def close(self):
        if self._handle:
            _api.CloseHandle(self._handle)
            self._handle = None


def _create_process(args, cwd, env, handles, job):
    """Atomically attach the new process to the job and restrict inheritance."""
    if any("\0" in arg for arg in args) or "\0" in cwd:
        raise ValueError("ACP process arguments contain a null character")
    for key, value in env.items():
        if not key or "=" in key[1:] or "\0" in key or "\0" in value:
            raise ValueError("Invalid ACP process environment")
    command = ctypes.create_unicode_buffer(subprocess.list2cmdline(args))
    environment = ctypes.create_unicode_buffer(
        "\0".join(f"{key}={value}" for key, value in sorted(env.items(), key=lambda item: item[0].upper())) + "\0\0")
    size = ctypes.c_size_t()
    _api.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
    if not size.value:
        raise _error("Cannot size ACP process attributes")
    attributes = ctypes.create_string_buffer(size.value)
    if not _api.InitializeProcThreadAttributeList(attributes, 2, 0, ctypes.byref(size)):
        raise _error("Cannot initialize ACP process attributes")
    try:
        # Values must remain alive until DeleteProcThreadAttributeList.
        inherited = (wintypes.HANDLE * 3)(*handles)
        jobs = (wintypes.HANDLE * 1)(job._handle)
        for attribute, value in ((0x00020002, inherited), (0x0002000D, jobs)):
            if not _api.UpdateProcThreadAttribute(attributes, 0, attribute, value,
                                                  ctypes.sizeof(value), None, None):
                raise _error("Cannot configure ACP process creation attributes")
        startup = _StartupInfoEx()
        startup.info.cb = ctypes.sizeof(startup)
        startup.info.flags = subprocess.STARTF_USESTDHANDLES
        startup.info.stdin, startup.info.stdout, startup.info.stderr = handles
        startup.attributes = ctypes.addressof(attributes)
        process = _ProcessInformation()
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                 | 0x00000400 | 0x00080000)  # UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT
        sys.audit("subprocess.Popen", args[0], command.value, cwd, env)
        if not _api.CreateProcessW(None, command, None, None, True, flags,
                                   environment, cwd, ctypes.byref(startup), ctypes.byref(process)):
            raise _error("Cannot create contained ACP process")
        _api.CloseHandle(process.thread)
        return process.process, process.pid
    finally:
        _api.DeleteProcThreadAttributeList(attributes)


class _JobPopen(windows_utils.Popen):
    def __init__(self, *args, job, **kwargs):
        self._job = job
        super().__init__(*args, **kwargs)

    def _execute_child(self, args, executable, preexec_fn, close_fds, pass_fds,
                       cwd, env, startupinfo, creationflags, shell,
                       p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite,
                       unused_restore_signals, unused_gid, unused_gids, unused_uid,
                       unused_umask, unused_start_new_session, unused_process_group):
        # This adapter serves only the fixed ACP stdio launch contract.
        try:
            if shell or executable or startupinfo or creationflags or pass_fds or not close_fds:
                raise ValueError("Unsupported ACP process launch options")
            handle, pid = _create_process(args, cwd, env,
                                         (int(p2cread), int(c2pwrite), int(errwrite)), self._job)
            self._handle = subprocess.Handle(handle)
            self.pid = pid
            self._child_created = True
        finally:
            self._close_pipe_fds(p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite)


class _JobTransport(_WindowsSubprocessTransport):
    _aborted_initialization = False

    def _start(self, args, shell, stdin, stdout, stderr, bufsize, **kwargs):
        self._proc = _JobPopen(args, shell=shell, stdin=stdin, stdout=stdout,
                               stderr=stderr, bufsize=bufsize, **kwargs)
        self._process_reaped = self._loop.create_future()
        self._process_waiter = self._loop._proactor.wait_for_handle(int(self._proc._handle))

        def process_exited(_):
            self._process_exited(self._proc.poll())
            self._process_reaped.set_result(None)

        self._process_waiter.add_done_callback(process_exited)

    def _call(self, callback, *args):
        if not self._aborted_initialization:
            super()._call(callback, *args)

    async def abort_initialization(self):
        # Failed pipe setup never publishes connection_made. CPython 3.11's
        # _wait() also cannot finish with a missing pipe, so wait on the native
        # process separately and discard unpublished protocol notifications.
        self._aborted_initialization = True
        if self._pending_calls is not None:
            self._pending_calls.clear()
        self.close()
        for fd, pipe in enumerate((self._proc.stdin, self._proc.stdout, self._proc.stderr)):
            if self._pipes[fd] is None:
                pipe.close()
        # The native wait can finish before its queued callback has polled the
        # handle. Only that callback's completion allows us to close it.
        await self._process_reaped
        self._proc._handle.Close()


async def _cleanup_start(transport, ready):
    try:
        await ready
    except BaseException:
        await transport.abort_initialization()
    else:
        transport.close()
        await transport._wait()


def _cleanup_finished(task):
    _startup_cleanups.discard(task)
    if not task.cancelled() and (error := task.exception()) is not None:
        _logger.error("ACP startup cleanup failed", exc_info=(type(error), error, error.__traceback__))


async def create_job_subprocess_exec(*args, cwd, env, limit, job):
    """Return the ordinary asyncio Process, keeping ownership during startup."""
    loop = asyncio.get_running_loop()
    if not isinstance(loop, asyncio.ProactorEventLoop):
        raise RuntimeError("Windows ACP requires the asyncio Proactor event loop")
    protocol = asyncio.subprocess.SubprocessStreamProtocol(limit=limit, loop=loop)
    ready = loop.create_future()
    transport = _JobTransport(loop, protocol, args, False, subprocess.PIPE,
                              subprocess.PIPE, subprocess.PIPE, 0, waiter=ready,
                              cwd=cwd, env=env, job=job)
    try:
        await asyncio.shield(ready)
    except BaseException:
        job.close()
        # A cancelled caller must not strand the launch while asyncio is still
        # connecting pipes. Complete ownership transfer before closing them.
        # A second cancellation can release the caller, but cannot cancel this
        # owner task or reopen the synchronously closed job.
        cleanup = asyncio.create_task(_cleanup_start(transport, ready))
        _startup_cleanups.add(cleanup)
        cleanup.add_done_callback(_cleanup_finished)
        await asyncio.shield(cleanup)
        raise
    return asyncio.subprocess.Process(transport, protocol, loop)
