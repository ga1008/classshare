"""Exercise the actual middleware definition without importing the live application."""
import ast
from pathlib import Path
import unittest

from starlette.applications import Starlette
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import HTMLResponse, StreamingResponse
from starlette.routing import Route
from starlette.testclient import TestClient


class HttpCompressionBudgetTests(unittest.TestCase):
    def middleware(self):
        tree = ast.parse(Path("classroom_app/app.py").read_text(encoding="utf-8"))
        definition = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "StreamingAwareGZipMiddleware")
        namespace = {"GZipMiddleware": GZipMiddleware}
        exec(compile(ast.Module(body=[definition], type_ignores=[]), "actual-compression-middleware", "exec"), namespace)
        registration = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == "add_middleware"
            and node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == definition.name)
        settings = {kw.arg: kw.value for kw in registration.keywords}
        self.assertEqual("HTTP_GZIP_COMPRESSION_LEVEL", settings["compresslevel"].id)
        self.assertEqual(1024, ast.literal_eval(settings["minimum_size"]))
        return namespace[definition.name]

    def test_compressed_and_identity_html_keep_body_status_and_headers(self):
        body = ("<p>合成课程与材料内容</p>" * 8000).encode()
        async def page(request):
            return HTMLResponse(body, status_code=200, headers={"Cache-Control": "private, no-store", "X-LanShare-Release": "synthetic"})
        app = Starlette(routes=[Route("/classroom/1", page)])
        with TestClient(self.middleware()(app, minimum_size=1024, compresslevel=6)) as client:
            compressed = client.get("/classroom/1", headers={"Accept-Encoding": "gzip"})
            plain = client.get("/classroom/1", headers={"Accept-Encoding": "identity"})
        self.assertEqual(body, compressed.content)
        self.assertEqual(body, plain.content)
        self.assertEqual("gzip", compressed.headers["content-encoding"])
        self.assertNotIn("content-encoding", plain.headers)
        self.assertIn("accept-encoding", compressed.headers["vary"].lower())
        for response in (compressed, plain):
            self.assertEqual(200, response.status_code)
            self.assertEqual("private, no-store", response.headers["cache-control"])
            self.assertEqual("synthetic", response.headers["x-lanshare-release"])

    def test_streaming_ai_remains_uncompressed_and_chunked(self):
        async def chunks():
            yield b"data: " + b"x" * 4096 + b"\n\n"
            yield b"data: [DONE]\n\n"
        async def chat(request):
            return StreamingResponse(chunks(), media_type="text/event-stream")
        app = Starlette(routes=[Route("/api/ai/chat", chat)])
        with TestClient(self.middleware()(app, minimum_size=1024, compresslevel=6)) as client:
            response = client.get("/api/ai/chat", headers={"Accept-Encoding": "gzip"})
        self.assertNotIn("content-encoding", response.headers)
        self.assertIn(b"data: [DONE]", response.content)
