"""Publish diagnostic snapshots without doing SQL or filesystem work in probes.

One collector owns all refreshes. A stuck provider cannot create more threads;
callers see snapshot age and component errors while liveness stays independent.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import threading
import time
from typing import Any, Callable


class RuntimeHealthSnapshots:
    def __init__(self, providers: dict[str, Callable[[], Any]], *, interval: float = 30):
        self.providers = providers
        self.interval = max(1.0, interval)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._data: dict[str, Any] = {}
        self._errors: dict[str, str] = {}
        self._sampled: dict[str, float] = {}
        self._generated_at: str | None = None

    def refresh(self) -> None:
        # Only the collector calls this in production. Publish each provider
        # separately so a slow optional diagnostic does not conceal a DB probe.
        for name, provider in self.providers.items():
            if self._stop.is_set():
                return
            try:
                value = provider()
            except Exception as exc:
                with self._lock:
                    self._errors[name] = type(exc).__name__
            else:
                with self._lock:
                    self._data[name] = value
                    self._sampled[name] = time.monotonic()
                    self._errors.pop(name, None)
                    self._generated_at = datetime.now(timezone.utc).isoformat()

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            ages = {name: round(max(0.0, now - at), 2) for name, at in self._sampled.items()}
            pending = [name for name in self.providers if name not in self._data]
            stale = [name for name, age in ages.items() if age > self.interval * 3]
            return {
                **deepcopy(self._data),
                "diagnostics": {
                    "generated_at": self._generated_at,
                    "age_seconds": ages,
                    "pending": pending,
                    "stale": stale,
                    "errors": dict(self._errors),
                    "collector_alive": bool(self._thread and self._thread.is_alive()),
                },
            }

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="runtime-diagnostics", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.refresh()
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread:
            thread.join(timeout=1.0)
