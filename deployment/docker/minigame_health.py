"""Read-only health probes against the local game service or its HTTPS proxy."""

import argparse
import http.client
import json
import os
from pathlib import Path
import socket
import ssl
import sys
from urllib.parse import urlsplit


def check(kind, via_nginx):
    api = kind == "api"
    origin = urlsplit(os.environ["MINIGAMES_APP_ORIGIN" if api else "MINIGAMES_CONTENT_ORIGIN"])
    if not origin.hostname:
        raise ValueError("Game origin is missing a hostname")
    if via_nginx:
        if origin.scheme != "https":
            raise ValueError("The production game proxy must use HTTPS")
        # Resolve through the Compose network while validating the public hostname and certificate.
        connection = http.client.HTTPSConnection(origin.hostname, origin.port or 443, timeout=5)
        raw = socket.create_connection(("nginx", 443), timeout=5)
        try:
            connection.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=origin.hostname)
        except BaseException:
            raw.close()
            raise
    else:
        connection = http.client.HTTPConnection("127.0.0.1", 8015 if api else 8014, timeout=5)
    try:
        path = "/api/games/v1/catalog" if api else "/l/" + "A" * 43 + "/main.html"
        connection.request("GET", path, headers={"Host": origin.netloc})
        response = connection.getresponse()
        expected = 401 if api else 404
        if response.status != expected or "no-store" not in response.getheader("Cache-Control", ""):
            raise RuntimeError(f"{kind} probe received HTTP {response.status} or an invalid cache policy")
        if not api and (response.getheader("X-Content-Type-Options") != "nosniff"
                        or response.getheader("Set-Cookie") is not None):
            raise RuntimeError("Content probe received invalid isolation headers")
    finally:
        connection.close()


def check_mounts(root, inspected):
    games = (root / "data" / "games").resolve()
    api, content = [{m["Destination"]: m for m in mounts} for mounts in inspected]
    for mounts, target, path, writable in (
        (api, "/app/data/games", games, True),
        (content, "/app/data/games/versions", games / "versions", False),
        (content, "/app/data/games/locks", games / "locks", True),
    ):
        entry = mounts.get(target, {})
        if (entry.get("Type") != "bind" or Path(entry["Source"]).resolve() != path
                or entry.get("RW") is not writable):
            raise ValueError(f"Unexpected game mount at {target}")
    register = api.get("/app/minigame-register", {})
    if (register.get("Type") != "bind" or not register.get("RW")
            or Path(register["Source"]).resolve().is_relative_to((root / "data").resolve())):
        raise ValueError("The deletion register must be an independent writable bind mount")
    if set(content) != {"/app/data/games/versions", "/app/data/games/locks"}:
        raise ValueError("The game content server has an unexpected mount")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("api", "content", "mounts"))
    parser.add_argument("--via-nginx", action="store_true")
    parser.add_argument("--app-root", type=Path)
    args = parser.parse_args()
    if args.kind == "mounts":
        if not args.app_root:
            parser.error("mounts requires --app-root")
        check_mounts(args.app_root, [json.loads(line) for line in sys.stdin])
        print("MINIGAMES_MOUNTS_VERIFIED")
    else:
        check(args.kind, args.via_nginx)
        print(f"MINIGAMES_READY={args.kind} via={'nginx' if args.via_nginx else 'loopback'}")
