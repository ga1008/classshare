"""Worker-side stdio adapter for the narrow host launcher Unix socket.

Not imported by the DSH runner. Task credentials come from the explicit child
environment, never command arguments or inherited application configuration.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import socket
import sys
import threading
import time
import re

MAX_FRAME = 16384
DEFAULT_SOCKET = "/run/lanshare-agent/launcher.sock"


def connect_launcher(request: dict, *, socket_path: str = DEFAULT_SOCKET, timeout: float = 45.0):
    if not isinstance(request, dict) or request.get("action") not in {"run", "stop", "probe"}:
        raise ValueError("Invalid launcher action")
    if not math.isfinite(timeout) or timeout <= 0 or not Path(socket_path).is_absolute():
        raise ValueError("Invalid launcher connection settings")
    data = json.dumps({**request, "version": 1}, separators=(",", ":"), allow_nan=False).encode() + b"\n"
    if len(data) > MAX_FRAME:
        raise ValueError("Launcher request exceeds limit")
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(timeout)
    try:
        deadline = time.monotonic() + timeout
        connection.connect(socket_path)
        connection.sendall(data)
        frame = bytearray()
        while not frame.endswith(b"\n"):
            if len(frame) >= MAX_FRAME:
                raise RuntimeError("Launcher response exceeds limit")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Launcher confirmation timed out")
            connection.settimeout(remaining)
            chunk = connection.recv(1)
            if not chunk:
                raise RuntimeError("Launcher closed before confirming startup")
            frame.extend(chunk)
        response = json.loads(frame)
        expected_status = "stopped" if request["action"] == "stop" else "ready"
        if not isinstance(response, dict) or response.get("status") != expected_status:
            raise RuntimeError("Launcher refused request")
        if expected_status == "ready":
            if (response.get("dsh_package_version") != "0.1.5-rc.1"
                    or not re.fullmatch(r"[0-9a-f]{64}", str(response.get("profile_sha256") or ""))):
                raise RuntimeError("Launcher evidence is invalid")
            expected = request.get("expected_evidence")
            if expected is not None and (not isinstance(expected, dict)
                    or any(response.get(key) != expected.get(key) for key in ("dsh_package_version", "profile_sha256", "image"))):
                raise RuntimeError("Runner evidence changed after admission")
        connection.settimeout(timeout)
        return connection, response
    except BaseException:
        connection.close()
        raise


def control(request: dict, *, socket_path: str = DEFAULT_SOCKET) -> dict:
    connection, response = connect_launcher(request, socket_path=socket_path)
    connection.close()
    return response


def main():
    if len(sys.argv) != 1:
        raise ValueError("Launcher client accepts no command arguments")
    request = json.loads(os.environ.pop("LANSHARE_DSH_LAUNCH_REQUEST", "{}"))
    if request.get("action") != "run":
        raise ValueError("Stdio client only runs task attempts")
    socket_path = os.environ.get("LANSHARE_DSH_LAUNCHER_SOCKET", DEFAULT_SOCKET)
    if not Path(socket_path).is_absolute():
        raise ValueError("Launcher socket must be absolute")
    connection, _evidence = connect_launcher(request, socket_path=socket_path)
    connection.settimeout(None)

    def stdin_pump():
        try:
            while chunk := sys.stdin.buffer.read1(65536):
                connection.sendall(chunk)
        except (OSError, BrokenPipeError):
            pass
        finally:
            try:
                connection.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    threading.Thread(target=stdin_pump, daemon=True).start()
    try:
        while chunk := connection.recv(65536):
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("DSH launcher transport failed", file=sys.stderr)
        raise SystemExit(78)
