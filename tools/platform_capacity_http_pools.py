"""Optional bounded HTTP pool shards for the synthetic capacity generator.

This changes client connection allocation, never server load or acceptance gates.
The probe's default one-pool path continues to use its original HTTPX client.
"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import hashlib
from pathlib import Path

import httpx

CONTROL_PATHS = frozenset({"/api/internal/health", "/api/internal/capacity-lab-identity"})
HARNESS_FILES = ("platform_capacity_probe.py", "platform_capacity_http_pools.py",
                 "platform_capacity_ambient.py", "platform_capacity_social.py",
                 "platform_capacity_reading.py", "platform_capacity_socket_workers.py",
                 "platform_capacity_lab_manifest.py")


def pool_budget(shards: int) -> dict:
    if type(shards) is not int or not 1 <= shards <= 16:
        raise ValueError("HTTP pool shard count must be between 1 and 16")
    return {"shards": shards, "aggregate_max_connections": 400,
            "aggregate_max_keepalive_connections": 200, "timeout_seconds": 30, "keepalive_expiry_seconds": 5,
            "pools": [{"shard": index, "max_connections": 400 // shards + (index < 400 % shards),
                       "max_keepalive_connections": 200 // shards + (index < 200 % shards)}
                      for index in range(shards)],
            "routing": "original shared client" if shards == 1 else "verified token ordinal modulo shards; control requests use shard 0",
            "comparison_note": "Default one-pool baseline unchanged; optional sharding changes client connection allocation and queue fairness. It is a new harness variant, not an identical-client performance comparison."}


def harness_hashes(root: Path) -> dict:
    return {"tools/" + name: hashlib.sha256((Path(root) / "tools" / name).read_bytes()).hexdigest()
            for name in HARNESS_FILES}


class _CookieJars:
    def __init__(self, owner): self.owner = owner

    def clear(self):
        for client in self.owner._clients: client.cookies.clear()


class ShardedCapacityClient:
    """A small get/post/request facade; no retry or independent load loop."""
    def __init__(self, *, base_url, verify, shards, client_factory=None):
        self.budget = pool_budget(shards)
        self.base_url = httpx.URL(base_url)
        if (self.base_url.scheme not in {"http", "https"} or self.base_url.host not in {"127.0.0.1", "::1"}
                or self.base_url.port is None or not 1024 <= self.base_url.port <= 65535
                or self.base_url.userinfo or self.base_url.query or self.base_url.fragment or self.base_url.path not in {"", "/"}):
            raise ValueError("HTTP shards require an explicit synthetic loopback origin")
        self._verify, self._factory = verify, client_factory or httpx.AsyncClient
        self._clients, self._token_shards = [], None
        self._stack, self._closing = None, None
        self.is_closed = False
        self.cookies = _CookieJars(self)

    async def __aenter__(self):
        if self._stack is not None or self.is_closed:
            raise RuntimeError("HTTP pool shards cannot be reopened")
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        try:
            for row in self.budget["pools"]:
                client = self._factory(base_url=self.base_url, verify=self._verify, timeout=30, trust_env=False,
                    limits=httpx.Limits(max_connections=row["max_connections"],
                                        max_keepalive_connections=row["max_keepalive_connections"]))
                self._clients.append(await self._stack.enter_async_context(client))
        except BaseException as primary:
            try: await self.aclose()
            except BaseException as cleanup:
                primary.add_note("HTTP pool cleanup also failed: " + type(cleanup).__name__)
            raise
        return self

    async def aclose(self):
        if self._closing is None:
            if self._stack is None:
                self.is_closed = True
                return
            self.is_closed = True
            self._closing = asyncio.create_task(self._stack.aclose())
        interrupted = False
        while not self._closing.done():
            try: await asyncio.shield(self._closing)
            except asyncio.CancelledError: interrupted = True
        # All accepted pools are observed even if cleanup was cancelled twice.
        self._closing.result()
        if interrupted: raise asyncio.CancelledError

    async def __aexit__(self, exc_type, primary, traceback):
        try: await self.aclose()
        except BaseException as cleanup:
            if primary is None: raise
            # Preserve the primary failure while making secondary cleanup
            # failure explicit, without copying arbitrary error text.
            primary.add_note("HTTP pool cleanup also failed: " + type(cleanup).__name__)

    def bind_tokens(self, tokens):
        if self._stack is None or self.is_closed or self._token_shards is not None:
            raise RuntimeError("HTTP identity binding requires one active, unbound pool group")
        if (not tokens or any(not isinstance(token, str) or not token or any(ch in token for ch in "\r\n;") for token in tokens)
                or len(set(tokens)) != len(tokens)):
            raise ValueError("HTTP shards require distinct verified identity tokens")
        self._token_shards = {token: index % self.budget["shards"] for index, token in enumerate(tokens)}

    def _shard(self, method, url, kwargs):
        target = httpx.URL(url)
        if target.is_absolute_url:
            if (target.scheme, target.host, target.port) != (self.base_url.scheme, self.base_url.host, self.base_url.port) or target.userinfo:
                raise ValueError("HTTP shard request differs from the validated origin")
        elif not str(target).startswith("/") or str(target).startswith("//"):
            raise ValueError("HTTP shard request requires an explicit local path")
        headers = httpx.Headers(kwargs.get("headers"))
        cookies = headers.get_list("cookie")
        if "cookies" in kwargs or len(cookies) > 1:
            raise ValueError("HTTP shards require one explicit identity Cookie")
        shard = None
        if cookies:
            cookie = cookies[0]
            prefix = "access_token="
            if not cookie.startswith(prefix) or self._token_shards is None:
                raise ValueError("HTTP request identity is not bound to this run")
            shard = self._token_shards.get(cookie[len(prefix):])
            if shard is None:
                raise ValueError("HTTP request identity is not bound to this run")
        if target.path in CONTROL_PATHS and method.upper() == "GET":
            return 0
        if shard is None:
            raise ValueError("Business HTTP requests require a verified identity Cookie")
        return shard

    async def request(self, method, url, **kwargs):
        if self._stack is None or self.is_closed or len(self._clients) != self.budget["shards"]:
            raise RuntimeError("HTTP pool shards are not active")
        return await self._clients[self._shard(method, url, kwargs)].request(method, url, **kwargs)

    async def get(self, url, **kwargs): return await self.request("GET", url, **kwargs)
    async def post(self, url, **kwargs): return await self.request("POST", url, **kwargs)
