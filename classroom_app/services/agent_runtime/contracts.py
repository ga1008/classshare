"""Small JSON-RPC contracts, independent of FastAPI, the DB and DSH packages."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class AcpError(Exception):
    """Base transport error; never includes environment or full protocol frames."""


class AcpProtocolError(AcpError):
    pass


class AcpTransportClosed(AcpError):
    pass


class AcpRequestTimeout(AcpError):
    pass


class AcpRemoteError(AcpError):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


@dataclass(frozen=True)
class AcpNotification:
    method: str
    params: Any
    sequence: int = 0


@dataclass(frozen=True)
class AcpClientOptions:
    """Only trusted launcher code supplies argv, cwd and the complete environment.

    No ambient environment is inherited. The client is a process transport, not
    a filesystem/network sandbox; production still requires the runner boundary.
    """

    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str]
    request_timeout: float = 30.0
    write_timeout: float = 10.0
    handler_timeout: float = 60.0
    shutdown_timeout: float = 3.0
    max_frame_bytes: int = 4 * 1024 * 1024
    max_pending_requests: int = 128
    max_inbound_requests: int = 16
    notification_capacity: int = 1024
    stderr_tail_bytes: int = 8192

    def __post_init__(self):
        if not self.argv or any(not isinstance(arg, str) or not arg for arg in self.argv):
            raise ValueError("argv must contain non-empty strings")
        if not self.cwd.is_absolute():
            raise ValueError("ACP cwd must be absolute")
        for name in ("request_timeout", "write_timeout", "handler_timeout", "shutdown_timeout"):
            value = getattr(self, name)
            if not 0 < value < float("inf"):
                raise ValueError(f"{name} must be finite and positive")
        for name in ("max_frame_bytes", "max_pending_requests", "max_inbound_requests",
                     "notification_capacity", "stderr_tail_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
