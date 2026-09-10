"""Small isolated routing fixtures: never import LanShare or open a database."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import hashlib
import json
from pathlib import Path
import tempfile
import textwrap
from types import SimpleNamespace
import unittest

from tools.agent_capability_inventory import (
    build_inventory,
    main,
    operation_key,
    render_markdown,
    snapshot_runtime_routes,
)


class AgentCapabilityInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")

    def inventory(self, **kwargs):
        return build_inventory(self.root, routers_dir="sample/routers", entrypoints=["sample/app.py:app"], **kwargs)

    def simple_app(self, routes: str):
        self.write("sample/app.py", """
            from fastapi import FastAPI
            from .routers import items
            app = FastAPI()
            app.include_router(items.router)
        """)
        self.write("sample/routers/items.py", "from fastapi import APIRouter, Depends\nrouter = APIRouter()\n" + textwrap.dedent(routes))

    def test_nested_router_prefixes_dependencies_and_imported_app_are_resolved_without_execution(self):
        marker = self.root / "must-not-exist"
        self.write("sample/core.py", f"""
            from fastapi import FastAPI
            from pathlib import Path
            Path({str(marker)!r}).write_text('executed')
            app = FastAPI()
        """)
        self.write("sample/auth.py", """
            from fastapi import Depends
            def current_user(): pass
            def current_teacher(user=Depends(current_user)): pass
            def organization(): pass
            def authenticated_app(): pass
        """)
        self.write("sample/app.py", """
            from fastapi import Depends
            from .core import app
            from .auth import authenticated_app
            from .routers import manage
            app.include_router(manage.router, prefix='/v2', dependencies=[Depends(authenticated_app)])
        """)
        self.write("sample/routers/manage.py", """
            from fastapi import APIRouter, Depends
            from ..auth import current_teacher
            from . import items as child
            router = APIRouter(prefix='/api/manage', dependencies=[Depends(current_teacher)])
            router.include_router(child.router, prefix='/teaching')
        """)
        self.write("sample/routers/items.py", """
            from fastapi import APIRouter as Router, Depends, Security
            from typing import Annotated
            from ..auth import organization, current_user
            router = Router(prefix='/items')
            @router.get('/{item_id:path}', dependencies=[Depends(organization)])
            async def detail(item_id: str, user: Annotated[dict, Security(current_user, scopes=['read'])]):
                return item_id
        """)
        report = self.inventory()
        self.assertFalse(marker.exists())
        self.assertEqual([], report["graph_issues"])
        self.assertEqual(1, len(report["operations"]))
        row = report["operations"][0]
        self.assertEqual("/v2/api/manage/teaching/items/{item_id:path}", row["path"])
        self.assertTrue(row["mounted"])
        self.assertEqual({"sample.auth.authenticated_app", "sample.auth.current_teacher", "sample.auth.current_user", "sample.auth.organization"}, {item["callable"] for item in row["dependencies"]})
        self.assertEqual(["read"], json.loads(next(item["scopes"].replace("'", '"') for item in row["dependencies"] if item["scopes"])))
        self.assertEqual("not_verified", row["agent_execution_status"])
        self.assertIsNone(row["agent_tool"])

    def test_transitive_dependency_and_star_reexports_are_recorded(self):
        self.write("sample/auth.py", """
            from fastapi import Depends
            def current_user(): pass
            def current_teacher(user=Depends(current_user)): pass
        """)
        self.write("sample/routers/common.py", """
            from fastapi import APIRouter, Depends
            from ..auth import current_teacher
        """)
        self.write("sample/routers/items.py", """
            from .common import *
            router = APIRouter()
            @router.get('/api/items')
            def items(user=Depends(current_teacher)): pass
        """)
        self.write("sample/app.py", """
            from fastapi import FastAPI
            from .routers.items import router as routes
            app = FastAPI()
            app.include_router(routes)
        """)
        row = self.inventory()["operations"][0]
        self.assertEqual("sample.auth.current_teacher", row["dependencies"][0]["callable"])
        self.assertEqual("sample.auth.current_user", row["dependency_closure"][0]["callable"])

    def test_literal_loops_and_multiple_mounts_expand_without_losing_methods(self):
        self.simple_app("""
            def endpoint(): pass
            ROUTES = [{'path': '/alpha'}, {'path': '/beta'}]
            for item in ROUTES:
                router.add_api_route(f"{item['path']}/{{item_id}}", endpoint, methods=['GET', 'POST'])
        """)
        self.write("sample/app.py", """
            from fastapi import FastAPI
            from .routers.items import router
            app = FastAPI()
            app.include_router(router, prefix='/one')
            app.include_router(router, prefix='/two')
        """)
        report = self.inventory()
        self.assertEqual(8, len(report["operations"]))
        self.assertEqual({"/one/alpha/{item_id}", "/one/beta/{item_id}", "/two/alpha/{item_id}", "/two/beta/{item_id}"}, {row["path"] for row in report["operations"]})
        self.assertEqual({"GET", "POST"}, {row["method"] for row in report["operations"]})
        self.assertEqual(0, report["summary"]["unresolved_or_conditional_rows"])

    def test_dynamic_registration_remains_explicit_and_is_not_classified_public(self):
        self.simple_app("""
            def make_routes(): raise RuntimeError('must not run')
            def endpoint(): pass
            for item in make_routes():
                router.add_api_route(item['path'], endpoint, methods=item['methods'])
        """)
        report = self.inventory()
        row = report["operations"][0]
        self.assertIsNone(row["path"])
        self.assertEqual("UNKNOWN", row["method"])
        self.assertEqual("unknown", row["classification"]["category"])
        self.assertEqual("item['path']", row["path_expression"])
        self.assertIn("dynamic_methods", row["review_reasons"])
        self.assertIn("conditional_or_dynamic_registration", row["review_reasons"])
        self.assertEqual(1, report["summary"]["authorization_review_required"])

    def test_unknown_prefix_and_unmounted_router_are_never_dropped(self):
        self.simple_app("""
            @router.get('/api/items')
            def endpoint(): pass
        """)
        self.write("sample/routers/orphan.py", """
            from fastapi import APIRouter
            router = APIRouter(prefix=runtime_prefix())
            @router.get('/lost')
            def endpoint(): pass
        """)
        self.write("sample/app.py", """
            from fastapi import FastAPI
            from .routers.items import router
            app = FastAPI()
            app.include_router(router)
            app.include_router(missing_router)
        """)
        report = self.inventory()
        self.assertEqual(2, len(report["operations"]))
        orphan = next(row for row in report["operations"] if "orphan" in row["handler"])
        self.assertFalse(orphan["mounted"])
        self.assertIn("not_reachable_from_entrypoints", orphan["review_reasons"])
        self.assertIsNone(orphan["path"])
        self.assertIn("unresolved_include", {issue["kind"] for issue in report["graph_issues"]})

    def test_duplicate_routes_remain_visible_and_operation_keys_survive_line_moves(self):
        routes = """
            @router.get('/api/items')
            def first(): pass
            @router.get('/api/items')
            def second(): pass
        """
        self.simple_app(routes)
        initial = self.inventory()
        self.assertEqual(2, len(initial["operations"]))
        self.assertEqual(1, len(initial["duplicate_operations"]))
        self.assertEqual(2, len({row["registration_id"] for row in initial["operations"]}))
        path = self.root / "sample/routers/items.py"
        path.write_text("\n\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
        later = self.inventory()
        self.assertEqual([row["operation_key"] for row in initial["operations"]], [row["operation_key"] for row in later["operations"]])
        self.assertEqual([row["registration_id"] for row in initial["operations"]], [row["registration_id"] for row in later["operations"]])

    def test_parse_failure_is_reported_while_other_routes_are_retained(self):
        self.simple_app("""
            @router.get('/api/items')
            def endpoint(): pass
        """)
        self.write("sample/routers/broken.py", "def broken(:")
        report = self.inventory()
        self.assertEqual(1, len(report["operations"]))
        self.assertEqual("unparsed_file", report["graph_issues"][0]["kind"])

    def test_method_name_in_http_client_body_is_not_misreported_as_route(self):
        self.simple_app("""
            @router.post('/api/items')
            def endpoint():
                http_client.get('https://example.invalid')
        """)
        self.assertEqual(1, len(self.inventory()["operations"]))

    def test_runtime_and_openapi_differences_remain_in_matrix_without_granting_support(self):
        self.simple_app("""
            @router.get('/api/items')
            def items(): pass
            @router.get('/hidden', include_in_schema=False)
            def hidden(): pass
            @router.websocket('/ws')
            def stream(): pass
        """)
        runtime = {"routes": [{"method": "GET", "path": "/api/items"}, {"method": "GET", "path": "/from-factory"}]}
        openapi = {"openapi": "3.1.0", "paths": {"/api/items": {"get": {"operationId": "items"}}, "/from-openapi": {"post": {"operationId": "new"}}}}
        report = self.inventory(runtime=runtime, openapi=openapi)
        self.assertEqual(5, len(report["operations"]))
        item = next(row for row in report["operations"] if row["path"] == "/api/items")
        self.assertEqual(["ast", "runtime", "openapi"], item["evidence"])
        self.assertEqual({"/hidden", "/ws"}, {row["path"] for row in report["comparisons"]["runtime"]["ast_only"]})
        self.assertEqual([], report["comparisons"]["openapi"]["ast_only"])
        self.assertEqual(0, report["summary"]["supported_capabilities"])
        self.assertTrue(all(row["agent_execution_status"] == "not_verified" for row in report["operations"]))

    def test_include_in_schema_false_in_parent_propagates_to_openapi_comparison(self):
        self.simple_app("""
            @router.get('/api/items')
            def items(): pass
        """)
        self.write("sample/app.py", """
            from fastapi import FastAPI
            from .routers.items import router
            app = FastAPI()
            app.include_router(router, include_in_schema=False)
        """)
        report = self.inventory(openapi={"paths": {}})
        self.assertFalse(report["operations"][0]["include_in_schema"])
        self.assertEqual([], report["comparisons"]["openapi"]["ast_only"])

    def test_runtime_snapshot_walks_mounts_without_invoking_handlers(self):
        def endpoint():
            raise AssertionError("must not invoke")

        child = SimpleNamespace(path="/items", methods={"GET", "POST"}, endpoint=endpoint, name="items", include_in_schema=True)
        mount = SimpleNamespace(path="/api", routes=[child])
        snapshot = snapshot_runtime_routes(SimpleNamespace(routes=[mount]))
        self.assertEqual({("GET", "/api/items"), ("POST", "/api/items")}, {(row["method"], row["path"]) for row in snapshot["routes"]})
        json.dumps(snapshot)

    def test_check_mode_detects_new_routes_without_rewriting_generated_files(self):
        self.simple_app("""
            @router.get('/api/items')
            def items(): pass
        """)
        arguments = ["--root", str(self.root), "--routers-dir", "sample/routers", "--entrypoint", "sample/app.py:app"]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, main(arguments))
            self.assertEqual(0, main(arguments + ["--check"]))
        output = self.root / "docs/agent-capability-matrix.json"
        before = output.read_bytes()
        with (self.root / "sample/routers/items.py").open("a", encoding="utf-8") as handle:
            handle.write("\n@router.delete('/api/items')\ndef delete(): pass\n")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(1, main(arguments + ["--check"]))
        self.assertEqual(before, output.read_bytes())

    def test_missing_source_and_unknown_auth_are_reviewable_not_successful_coverage(self):
        missing = self.inventory()
        self.assertIn("empty_router_source", {issue["kind"] for issue in missing["graph_issues"]})
        self.simple_app("""
            @router.get('/calendar/{token}')
            def token_feed(): pass
        """)
        row = self.inventory()["operations"][0]
        self.assertEqual("unknown", row["classification"]["category"])
        self.assertIn("not evidence", row["classification"]["reason"])
        self.assertIn(operation_key("GET", "/calendar/{token}"), render_markdown(self.inventory()))

    def reviewed(self):
        return {"schema_version": 1, "adapters": [{
            "capability_key": "items.list", "kind": "read", "method": "GET", "path": "/api/items",
            "handler": "sample.routers.items.items", "roles": ["teacher"],
            "resource_boundary": "owned resources", "receipt": "JSON response", "verification": ["isolated fixture"],
            "limitations": "reviewed query parameters only; no write coverage",
            "source_files": [{"file": "sample/routers/items.py", "sha256": hashlib.sha256((self.root / "sample/routers/items.py").read_bytes()).hexdigest()}],
        }]}

    def test_explicit_review_marks_only_adapter_subset_and_changed_evidence_reopens_review(self):
        self.simple_app("""
            @router.get('/api/items')
            def items(): pass
            @router.post('/api/items')
            def create(): pass
        """)
        evidence = self.reviewed()
        report = self.inventory(reviewed=evidence)
        self.assertEqual(1, report["summary"]["locally_verified_read_adapters"])
        self.assertEqual(0, report["summary"]["fully_covered_web_routes"])
        self.assertEqual(0, report["summary"]["supported_capabilities"])
        self.assertEqual("not_verified", next(row for row in report["operations"] if row["method"] == "POST")["agent_execution_status"])
        self.assertIn("items.list", render_markdown(report))
        with (self.root / "sample/routers/items.py").open("a") as handle:
            handle.write("\n# changed implementation\n")
        changed = self.inventory(reviewed=evidence)
        self.assertEqual([], changed["reviewed_adapters"])
        self.assertEqual("review_source_changed_or_missing", changed["review_issues"][0]["reason"])

    def test_review_cannot_promote_ambiguous_wrong_handler_or_unmounted_routes(self):
        self.simple_app("""
            @router.get('/api/items')
            def items(): pass
        """)
        evidence = self.reviewed()
        evidence["adapters"][0]["handler"] = "sample.routers.items.other"
        self.assertEqual("route_missing_changed_or_ambiguous", self.inventory(reviewed=evidence)["review_issues"][0]["reason"])
        evidence = self.reviewed()
        self.write("sample/app.py", """
            from fastapi import FastAPI
            from .routers import items
            app = FastAPI()
            app.include_router(items.router)
            app.include_router(items.router)
        """)
        self.assertEqual([], self.inventory(reviewed=evidence)["reviewed_adapters"])

    def test_http_observation_is_separate_from_transaction_and_domain_job_proof(self):
        self.simple_app("""
            @router.get('/api/items')
            def items(): pass
        """)
        evidence = self.reviewed()
        request = {**evidence["adapters"][0], "kind": "request", "capability_key": "http.items.list"}
        evidence["adapters"].append(request)
        self.assertEqual("request_requires_observation_guarantee", self.inventory(reviewed=evidence)["review_issues"][0]["reason"])
        request["guarantee"] = "observed_http_result_not_verified_business"
        report = self.inventory(reviewed=evidence)
        self.assertEqual(1, report["summary"]["locally_verified_read_adapters"])
        self.assertEqual(1, report["summary"]["locally_reviewed_request_adapters"])
        self.assertEqual(0, report["summary"]["locally_verified_write_adapters"])
        self.assertEqual("observed_http_only", report["reviewed_adapters"][1]["business_completion"])
        self.assertEqual(["platform_read", "platform_request"], report["operations"][0]["agent_tools"])
        self.assertIn("B 层受控 HTTP 请求 1", render_markdown(report))

    def test_secure_input_requires_user_confirmation_and_is_not_a_model_write_tool(self):
        self.simple_app("""
            @router.get('/api/items')
            def items(): pass
        """)
        evidence = self.reviewed()
        item = evidence['adapters'][0]
        item.update(kind='secure_input', capability_key='account.password')
        self.assertEqual('secure_input_requires_user_only_confirmation', self.inventory(reviewed=evidence)['review_issues'][0]['reason'])
        item['guarantee'] = 'user_only_authenticated_confirmation'
        report = self.inventory(reviewed=evidence)
        self.assertEqual(1, report['summary']['locally_verified_secure_input_adapters'])
        self.assertEqual(0, report['summary']['locally_verified_write_adapters'])
        self.assertEqual(['authenticated_user_confirmation'], report['operations'][0]['agent_tools'])
        self.assertEqual('authenticated_user_transaction_receipt', report['reviewed_adapters'][0]['business_completion'])

    def test_file_read_snapshot_does_not_claim_binary_delivery_or_inflate_domain_reads(self):
        self.simple_app("""
            @router.get('/api/items')
            def items(): pass
        """)
        evidence = self.reviewed()
        item = evidence['adapters'][0]
        item.update(kind='file_read', capability_key='file.material')
        self.assertEqual('file_read_requires_authorized_snapshot', self.inventory(reviewed=evidence)['review_issues'][0]['reason'])
        item['guarantee'] = 'authorized_file_snapshot'
        report = self.inventory(reviewed=evidence)
        self.assertEqual(1, report['summary']['locally_verified_file_read_adapters'])
        self.assertEqual(0, report['summary']['locally_verified_read_adapters'])
        self.assertEqual(0, report['summary']['fully_covered_web_routes'])
        self.assertEqual(['platform_file'], report['operations'][0]['agent_tools'])
        self.assertEqual('authorized_snapshot_not_binary_delivery', report['reviewed_adapters'][0]['business_completion'])
        self.assertIn('授权文件来源 1', render_markdown(report))


if __name__ == "__main__":
    unittest.main()
