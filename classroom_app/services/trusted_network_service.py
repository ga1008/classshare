"""Canonical client address from explicitly trusted proxies, never header text alone."""
from __future__ import annotations

import ipaddress
import os
import socket
import threading
import time

_lock = threading.Lock()
_host_cache = (0.0, (), frozenset())


def _address(value):
    try:
        ip = ipaddress.ip_address(str(value or "").strip())
        return ip.ipv4_mapped if getattr(ip, "ipv4_mapped", None) else ip
    except ValueError:
        return None


def _trusted_hosts():
    global _host_cache
    names = tuple(x.strip() for x in os.getenv("TRUSTED_PROXY_HOSTS", "").split(",") if x.strip())
    now = time.monotonic()
    with _lock:
        if _host_cache[0] > now and _host_cache[1] == names:
            return _host_cache[2]
        found = set()
        for name in names[:8]:
            try:
                found.update(str(_address(row[4][0])) for row in socket.getaddrinfo(name, None, type=socket.SOCK_STREAM))
            except OSError:
                continue
        _host_cache = (now + 60, names, frozenset(found))
        return _host_cache[2]


def _is_trusted(ip):
    if ip is None:
        return False
    networks = []
    for raw in os.getenv("TRUSTED_PROXY_CIDRS", "127.0.0.1/32,::1/128").split(","):
        try:
            net = ipaddress.ip_network(raw.strip(), strict=False)
            if net.prefixlen:
                networks.append(net)
        except ValueError:
            continue
    return str(ip) in _trusted_hosts() or any(ip in n for n in networks)


class TrustedProxySchemeMiddleware:
    """Honor proxy TLS metadata while retaining the transport peer for IP trust."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in {"http", "websocket"}:
            peer = _address((scope.get("client") or (None,))[0])
            if _is_trusted(peer):
                headers = dict(scope.get("headers") or [])
                proto = headers.get(b"x-forwarded-proto", b"").decode("latin1").lower()
                if proto in {"http", "https"}:
                    scope = dict(scope)
                    scope["scheme"] = ({"http":"ws", "https":"wss"}[proto]
                                       if scope["type"]=="websocket" else proto)
        await self.app(scope, receive, send)


def require_same_origin(request):
    from urllib.parse import urlsplit
    from fastapi import HTTPException
    def identity(value):
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Invalid origin")
        return parsed.scheme.lower(), parsed.hostname.lower(), parsed.port or (443 if parsed.scheme=="https" else 80)
    origin = request.headers.get("origin")
    try:
        bad = request.headers.get("sec-fetch-site")=="cross-site"
        if origin:
            bad = bad or identity(origin) != identity(f"{request.url.scheme}://{request.headers.get('host', '')}")
        if not bad:
            return
    except ValueError:
        pass
    raise HTTPException(403, "此操作必须从当前平台页面发起。")


def client_network(request):
    peer = _address(getattr(getattr(request, "client", None), "host", None))
    if peer is None:
        return {"ip": "", "source": "unknown"}

    if _is_trusted(peer):
        forwarded = str(request.headers.get("x-forwarded-for") or "")
        # Walk right to left. Client-injected leftmost hops never outrank the
        # first non-proxy hop. Nginx overwrites this header with remote_addr.
        if len(forwarded) <= 1024:
            for raw in reversed(forwarded.split(",")):
                hop = _address(raw)
                if hop is not None and not _is_trusted(hop):
                    return {"ip": str(hop), "source": "trusted_proxy"}
        real = _address(request.headers.get("x-real-ip"))
        if real is not None:
            return {"ip": str(real), "source": "trusted_proxy"}
    return {"ip": str(peer), "source": "direct"}
