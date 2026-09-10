"""Bounded public HTTP fetch with DNS pinned to the address that was checked."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from html.parser import HTMLParser
import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

from fastapi import HTTPException

from .agent_bridge_service import MAX_WEB_BYTES


MAX_WEB_SECONDS = 20.0
_DNS_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-public-dns")
_DNS_SLOTS = threading.BoundedSemaphore(4)


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise HTTPException(504, "网页读取超过总时间限制。")
    return remaining


def _resolve_addresses(hostname, port, deadline):
    # libc DNS is not cancellable. Bound both its worker count and queue: timed
    # out lookups retain their slot until the underlying resolver actually ends.
    slots = _DNS_SLOTS
    if not slots.acquire(blocking=False):
        raise HTTPException(503, "网页域名解析繁忙，请稍后重试。")
    try:
        future = _DNS_POOL.submit(socket.getaddrinfo, hostname, port, type=socket.SOCK_STREAM)
    except BaseException:
        slots.release()
        raise
    future.add_done_callback(lambda _: slots.release())
    try:
        return future.result(timeout=_remaining(deadline))
    except FutureTimeout:
        raise HTTPException(504, "网页域名解析超过总时间限制。") from None


class _VisibleHTML(HTMLParser):
    """Linear parser avoids repeated unclosed script/style regex backtracking."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.hidden.append(tag)

    def handle_endtag(self, tag):
        if self.hidden and self.hidden[-1] == tag:
            self.hidden.pop()

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _html_text(content):
    parser = _VisibleHTML()
    parser.feed(content)
    parser.close()
    return " ".join(" ".join(parser.parts).split())


def public_target(raw_url, *, deadline=None):
    deadline = deadline if deadline is not None else time.monotonic() + MAX_WEB_SECONDS
    if not isinstance(raw_url, str) or len(raw_url) > 2000 or any(ord(c) < 33 for c in raw_url):
        raise HTTPException(400, "网页地址无效。")
    try:
        url = urlsplit(raw_url)
        hostname = (url.hostname or "").encode("idna").decode("ascii")
        port = url.port or (443 if url.scheme == "https" else 80)
    except (ValueError, UnicodeError):
        raise HTTPException(400, "网页地址无效。") from None
    if url.scheme not in {"https", "http"} or not hostname or url.username or url.password or url.fragment:
        raise HTTPException(400, "仅支持无登录凭据的 HTTP/HTTPS 网页。")
    try:
        addresses = _resolve_addresses(hostname, port, deadline)
        if not addresses:
            raise ValueError("No addresses")
        for _, _, _, _, address in addresses:
            ip = ipaddress.ip_address(address[0])
            if not ip.is_global or ip.is_multicast or ip.is_unspecified:
                raise HTTPException(400, "不允许访问内网或非公网地址。")
    except HTTPException:
        raise
    except (OSError, ValueError):
        raise HTTPException(502, "网页域名暂时无法解析。") from None
    target = urlunsplit(("", "", url.path or "/", url.query, ""))
    return url, hostname, port, addresses[0], target


class PinnedConnection(http.client.HTTPConnection):
    def __init__(self, hostname, port, address, *, tls, deadline=None):
        self.deadline = deadline if deadline is not None else time.monotonic() + MAX_WEB_SECONDS
        super().__init__(hostname, port, timeout=_remaining(self.deadline))
        self.address = address
        self.tls = tls
        self._transport = None

    def abort(self):
        # HTTPResponse may own the makefile after HTTPConnection.sock becomes
        # None. shutdown the retained transport to interrupt headers/body reads.
        sock = self._transport
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except (OSError, ValueError):
                pass
            try:
                sock.close()
            except OSError:
                pass

    def connect(self):
        family, sock_type, protocol, _, sockaddr = self.address
        sock = socket.socket(family, sock_type, protocol)
        self._transport = self.sock = sock
        try:
            sock.settimeout(_remaining(self.deadline))
            sock.connect(sockaddr)  # No second DNS lookup or environment proxy.
            if self.tls:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host, do_handshake_on_connect=False)
                self._transport = self.sock = sock
                sock.settimeout(_remaining(self.deadline))
                sock.do_handshake()
            self.sock = sock
        except BaseException:
            sock.close()
            raise


def fetch_public_web(raw_url: str, *, mode="text"):
    if mode not in {"text", "raw"}:
        raise HTTPException(422, "不支持的网页读取模式。")
    current = raw_url
    deadline = time.monotonic() + MAX_WEB_SECONDS
    for hop in range(5):
        url, hostname, port, address, target = public_target(current, deadline=deadline)
        connection = PinnedConnection(hostname, port, address, tls=url.scheme == "https", deadline=deadline)
        timer = threading.Timer(_remaining(deadline), connection.abort)
        timer.daemon = True
        timer.start()
        response = None
        try:
            connection.request("GET", target, headers={"User-Agent": "LanShare-Agent/2.0", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            _remaining(deadline)
            if response.status in {301, 302, 303, 307, 308}:
                if hop >= 4 or not response.getheader("Location"):
                    raise HTTPException(502, "网页重定向次数过多或地址无效。")
                current = urljoin(current, response.getheader("Location"))
                continue
            encoding = response.getheader("Content-Encoding", "identity").lower()
            if encoding not in {"identity", ""}:
                raise HTTPException(502, "网页未提供可安全读取的正文编码。")
            body = response.read(MAX_WEB_BYTES + 1)
            _remaining(deadline)
            truncated = len(body) > MAX_WEB_BYTES
            body = body[:MAX_WEB_BYTES]
            content_type = response.getheader("Content-Type", "")[:200]
            charset = re.search(r"charset\s*=\s*[\"']?([\w-]+)", content_type, re.I)
            name = charset.group(1) if charset else "utf-8"
            try:
                text = body.decode(name, errors="replace")
            except (LookupError, UnicodeError, TypeError):
                # Registered binary codecs (e.g. base64_codec) also cannot be
                # used by bytes.decode, even though codecs.lookup accepts them.
                text = body.decode("utf-8", errors="replace")
            if mode == "text" and "html" in content_type.lower():
                text = _html_text(text)
            _remaining(deadline)
            return {"status": "success", "url": current, "status_code": response.status,
                    "content_type": content_type, "truncated": truncated, "content": text}
        except HTTPException:
            raise
        except (OSError, http.client.HTTPException, UnicodeError):
            _remaining(deadline)
            raise HTTPException(502, "网页暂时无法读取。") from None
        finally:
            timer.cancel()
            if response is not None:
                response.close()
            connection.close()
    raise HTTPException(502, "网页重定向未完成。")
