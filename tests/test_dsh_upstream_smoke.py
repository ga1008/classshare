import contextlib
import http.server
import io
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest

from tools.dsh_upstream_smoke import probe, requests_for


class ServiceProbeTests(unittest.TestCase):
    def test_default_does_not_need_app_or_secret_and_declares_three_requests(self):
        result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'tools/dsh_upstream_smoke.py')],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['paid_requests'], 0)
        requests = requests_for('deepseek-v4-pro')
        self.assertEqual([item[2]['max_tokens'] for item in requests], [64, 256, 512])
        self.assertEqual(requests[2][2]['tools'][0]['max_uses'], 1)

    def test_real_http_preserves_usage_and_requires_native_search_sources(self):
        records = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                records.append(self.path)
                self.rfile.read(int(self.headers['Content-Length']))
                if self.path == '/redirect':
                    self.send_response(302)
                    self.send_header('Location', '/must-not-follow')
                    self.end_headers()
                    return
                if self.path == '/denied':
                    self.send_response(401)
                    self.end_headers()
                    self.wfile.write(b'synthetic-secret-must-not-be-logged')
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                value = {'model': 'fixture', 'usage': {'output_tokens': 589}, 'content': [
                    {'type': 'web_search_tool_result', 'content': [{'type': 'web_search_result', 'url': 'https://example.com/'}]}
                ] if self.path == '/native' else [{'type': 'text', 'text': 'claims a search but provides no native evidence'}]}
                self.wfile.write(json.dumps(value).encode())
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = 'http://127.0.0.1:' + str(server.server_port)
            result = probe(base + '/native', 'synthetic-key', {}, search=True)
            self.assertTrue(result['ok'])
            self.assertEqual(result['usage']['output_tokens'], 589)
            self.assertEqual(result['sources'], 1)
            self.assertFalse(probe(base + '/prose', 'synthetic-key', {}, search=True)['ok'])
            self.assertEqual(probe(base + '/redirect', 'synthetic-key', {})['http_status'], 302)
            refused = probe(base + '/denied', 'synthetic-key', {})
            self.assertEqual(refused['http_status'], 401)
            self.assertNotIn('synthetic-secret', str(refused))
            self.assertNotIn('/must-not-follow', records)
        finally:
            server.shutdown()
            server.server_close()
