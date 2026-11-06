"""Bound password CPU work independently from the default database threads."""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor


_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()


async def _run_password_work(operation, *args):
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="password-verify")
        executor = _executor
    return await asyncio.get_running_loop().run_in_executor(executor, operation, *args)


async def verify_login_password(password: str, encoded: str) -> bool:
    from ..dependencies import verify_password
    return await _run_password_work(verify_password, password, encoded)


async def hash_setup_password(password: str) -> str:
    from ..dependencies import get_password_hash
    return await _run_password_work(get_password_hash, password)


async def prepare_changed_password(current_password: str, new_password: str, encoded: str) -> str | None:
    """Verify and hash in the same bounded CPU pool, without a DB connection."""
    from ..dependencies import get_password_hash, verify_password

    def prepare():
        return get_password_hash(new_password) if verify_password(current_password, encoded) else None

    return await _run_password_work(prepare)


def stop_password_verifier() -> None:
    global _executor
    with _lock:
        executor, _executor = _executor, None
    if executor is not None:
        executor.shutdown(wait=True)
