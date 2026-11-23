"""Cross-process read leases for open game files during physical erasure."""

import ctypes
import hashlib
import os
from contextlib import contextmanager

from fastapi import HTTPException


@contextmanager
def asset_usage(game_id, *, exclusive=False):
    from .minigame_platform import game_root

    stripe = hashlib.sha256(str(game_id).encode()).hexdigest()[:3]
    directory = game_root() / "locks"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ("usage-" + stripe)).open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            from ctypes import wintypes

            class Overlapped(ctypes.Structure):
                _fields_ = [
                    ("Internal", ctypes.c_size_t),
                    ("InternalHigh", ctypes.c_size_t),
                    ("Offset", wintypes.DWORD),
                    ("OffsetHigh", wintypes.DWORD),
                    ("hEvent", wintypes.HANDLE),
                ]

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            fd = wintypes.HANDLE(msvcrt.get_osfhandle(handle.fileno()))
            operation = Overlapped()
            if not kernel.LockFileEx(
                fd, 1 | (2 if exclusive else 0), 0, 1, 0, ctypes.byref(operation)
            ):
                raise HTTPException(503, "游戏文件暂忙", headers={"Retry-After": "1"})
        else:
            import fcntl

            try:
                fcntl.flock(
                    handle,
                    fcntl.LOCK_NB | (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH),
                )
            except BlockingIOError as exc:
                raise HTTPException(
                    503, "游戏文件暂忙", headers={"Retry-After": "1"}
                ) from exc
        # Closing the descriptor releases the advisory lock even after an interrupted response.
        yield
