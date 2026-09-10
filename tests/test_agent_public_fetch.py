import socket
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

from classroom_app.services import agent_web_fetch_service as service


class PublicFetchTests(unittest.TestCase):
    def addresses(self, ip):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]

    def test_mixed_public_private_dns_is_rejected(self):
        for ip in ("127.0.0.1", "10.1.2.3", "169.254.169.254", "0.0.0.0", "224.0.0.1"):
            with self.subTest(ip=ip), patch.object(service.socket, "getaddrinfo", return_value=self.addresses("8.8.8.8") + self.addresses(ip)):
                with self.assertRaises(HTTPException):
                    service.public_target("https://fixture.example/a")

    def test_connect_uses_validated_socket_address_without_resolving_again(self):
        sock = Mock()
        with patch.object(service.socket, "socket", return_value=sock), patch.object(service.socket, "getaddrinfo") as dns:
            connection = service.PinnedConnection("fixture.example", 80, self.addresses("8.8.8.8")[0], tls=False)
            connection.connect()
            sock.connect.assert_called_once_with(("8.8.8.8", 443))
            dns.assert_not_called()

    def test_response_read_is_bounded_before_decoding(self):
        response = Mock(status=200)
        response.getheader.side_effect = lambda key, default=None: {"Content-Type": "text/plain", "Content-Encoding": "identity"}.get(key, default)
        response.read.return_value = b"a" * (service.MAX_WEB_BYTES + 1)
        connection = Mock()
        connection.getresponse.return_value = response
        with patch.object(service.socket, "getaddrinfo", return_value=self.addresses("8.8.8.8")), patch.object(service, "PinnedConnection", return_value=connection):
            result = service.fetch_public_web("https://fixture.example/a")
        response.read.assert_called_once_with(service.MAX_WEB_BYTES + 1)
        self.assertTrue(result["truncated"])
        self.assertEqual(service.MAX_WEB_BYTES, len(result["content"]))
        connection.close.assert_called_once()

    def test_redirect_is_revalidated_before_opening_connection(self):
        response = Mock(status=302)
        response.getheader.return_value = "http://127.0.0.1/private"
        connection = Mock()
        connection.getresponse.return_value = response
        with patch.object(service.socket, "getaddrinfo", side_effect=[self.addresses("8.8.8.8"), self.addresses("127.0.0.1")]), patch.object(service, "PinnedConnection", return_value=connection) as client:
            with self.assertRaises(HTTPException):
                service.fetch_public_web("https://fixture.example/a")
        self.assertEqual(1, client.call_count)

    def test_userinfo_and_invalid_schemes_never_resolve(self):
        with patch.object(service.socket, "getaddrinfo") as dns:
            for url in ("https://user:secret@fixture.example", "file:///etc/passwd", "http://fixture.example/\r\nX:foo"):
                with self.assertRaises(HTTPException):
                    service.public_target(url)
            dns.assert_not_called()

    def test_unknown_and_binary_charsets_fall_back_to_text(self):
        for charset in ("imaginary-encoding", "base64_codec", "hex_codec"):
            response = Mock(status=200)
            response.getheader.side_effect = lambda key, default=None: {"Content-Type": f"text/plain; charset={charset}", "Content-Encoding": "identity"}.get(key, default)
            response.read.return_value = b"Hello \xff"
            connection = Mock()
            connection.getresponse.return_value = response
            with self.subTest(charset=charset), patch.object(service.socket, "getaddrinfo", return_value=self.addresses("8.8.8.8")), patch.object(service, "PinnedConnection", return_value=connection):
                self.assertEqual("Hello \ufffd", service.fetch_public_web("https://fixture.example/")["content"])
            response.close.assert_called_once()

    def test_unclosed_script_markup_is_bounded_and_hidden(self):
        self.assertEqual("Visible tail", service._html_text("<div>Visible</div><script>bad</script><p>tail</p>"))
        started = time.monotonic()
        self.assertEqual("Visible", service._html_text("Visible" + "<script>unclosed" * 30000))
        self.assertLess(time.monotonic() - started, 2)

    def _dripping_server(self, *, headers):
        """Owned loopback fixture only; public_target is replaced in this test."""
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        stopped = threading.Event()

        def respond():
            try:
                client, _ = server.accept()
                with client:
                    client.settimeout(2)
                    client.recv(4096)
                    if headers:
                        client.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                    else:
                        client.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 100000\r\nConnection: close\r\n\r\n")
                    while not stopped.wait(.015):
                        client.sendall(b"x")
            except OSError:
                pass

        thread = threading.Thread(target=respond, daemon=True)
        thread.start()
        port = server.getsockname()[1]
        target = service.urlsplit(f"http://fixture.example:{port}/")
        address = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))
        started = time.monotonic()
        try:
            with patch.object(service, "MAX_WEB_SECONDS", .15), patch.object(service, "public_target", return_value=(target, "fixture.example", port, address, "/")):
                with self.assertRaises(HTTPException) as caught:
                    service.fetch_public_web(f"http://fixture.example:{port}/")
            self.assertEqual(504, caught.exception.status_code)
            self.assertLess(time.monotonic() - started, 1)
        finally:
            stopped.set()
            server.close()
            thread.join(2)
            self.assertFalse(thread.is_alive())

    def test_total_deadline_interrupts_headers_despite_continuous_bytes(self):
        self._dripping_server(headers=True)

    def test_total_deadline_interrupts_body_after_httpconnection_detaches_socket(self):
        self._dripping_server(headers=False)

    def test_dns_timeout_keeps_its_slot_until_real_lookup_finishes(self):
        release = threading.Event()

        def lookup(*args, **kwargs):
            release.wait(2)
            return self.addresses("8.8.8.8")

        with ThreadPoolExecutor(max_workers=1) as pool:
            with patch.object(service, "_DNS_POOL", pool), patch.object(service, "_DNS_SLOTS", threading.BoundedSemaphore(1)), patch.object(service.socket, "getaddrinfo", side_effect=lookup) as dns:
                try:
                    with self.assertRaises(HTTPException) as first:
                        service.public_target("https://fixture.example/", deadline=time.monotonic() + .04)
                    self.assertEqual(504, first.exception.status_code)
                    with self.assertRaises(HTTPException) as second:
                        service.public_target("https://fixture.example/", deadline=time.monotonic() + .04)
                    self.assertEqual(503, second.exception.status_code)
                    self.assertEqual(1, dns.call_count)
                finally:
                    release.set()

    def test_all_redirects_share_one_absolute_deadline(self):
        response = Mock(status=302)
        response.getheader.return_value = "https://fixture.example/next"
        connection = Mock()
        connection.getresponse.return_value = response
        calls = []

        def target(url, *, deadline):
            calls.append(deadline)
            if len(calls) == 2:
                raise HTTPException(504, "deadline")
            return service.urlsplit(url), "fixture.example", 443, self.addresses("8.8.8.8")[0], "/"

        with patch.object(service, "public_target", side_effect=target), patch.object(service, "PinnedConnection", return_value=connection):
            with self.assertRaises(HTTPException):
                service.fetch_public_web("https://fixture.example/")
        self.assertEqual(2, len(calls))
        self.assertEqual(calls[0], calls[1])
