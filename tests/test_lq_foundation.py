from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app.routers.ui_parts import design_system
from classroom_app.db.schema_user_ui_preferences import ensure_user_ui_preferences_schema
from tools.ui.lq_inventory import build_registry, scan_routes
from tools.ui.lint_lq import audit, violations


class LqPreviewTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("CREATE TABLE teachers(id INTEGER PRIMARY KEY,is_active INTEGER); INSERT INTO teachers VALUES(1,1),(2,0);")
        ensure_user_ui_preferences_schema(self.conn, engine="sqlite")
        self.conn.commit()
        self.addCleanup(self.conn.close)
        app = FastAPI()
        app.include_router(design_system.router)
        self.user = None
        app.dependency_overrides[design_system.get_current_user_optional] = lambda: self.user
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        # This fixture tests route authorization, not the separately built asset graph.
        assets = patch.dict(design_system.templates.env.globals, {"asset_url": lambda path: "/static/" + path})
        assets.start()
        self.addCleanup(assets.stop)
        for target, replacement in (
            ("classroom_app.dependencies.get_db_connection", self.database),
            ("classroom_app.dependencies._identity_cache_is_valid", lambda *_: False),
            ("classroom_app.dependencies._cache_valid_identity", lambda *_: None),
            ("classroom_app.dependencies.invalidate_session_for_user", lambda *_: None),
            ("classroom_app.services.user_ui_preferences_service.get_db_connection", self.database),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    @contextmanager
    def database(self):
        with self.conn:
            yield self.conn

    def test_disabled_route_is_not_exposed_even_to_teacher(self):
        self.user = {"role": "teacher", "id": 1}
        with patch.dict(os.environ, {"LANSHARE_LQ_PREVIEW": "false"}), patch.object(design_system, "validate_authenticated_user_identity") as validate:
            for url in ("/dev/lq", "/dev/lq?layout=editor", "/dev/lq?shell=centered"):
                self.assertEqual(self.client.get(url).status_code, 404)
            validate.assert_not_called()

    def test_enabled_preview_denies_anonymous_and_students(self):
        with patch.dict(os.environ, {"LANSHARE_LQ_PREVIEW": "true"}):
            for user in (None, {"role": "student", "id": 1}):
                self.user = user
                for url in ("/dev/lq", "/dev/lq?layout=editor", "/dev/lq?shell=centered"):
                    self.assertEqual(self.client.get(url).status_code, 403)

    def test_teacher_gets_non_cacheable_preview(self):
        self.user = {"role": "teacher", "id": 1}
        with patch.dict(os.environ, {"LANSHARE_LQ_PREVIEW": "true"}):
            response = self.client.get("/dev/lq")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn('data-lq-preview', response.text)
        self.assertIn('data-ui-palette="teal"', response.text)
        self.assertIn('data-ui-palette-version="0"', response.text)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM user_ui_preferences").fetchone()[0], 0)

    def test_seven_shell_previews_share_identity_gate_and_do_not_write_preferences(self):
        self.user = {"role": "teacher", "id": 1}
        with patch.dict(os.environ, {"LANSHARE_LQ_PREVIEW": "true"}):
            for layout in ("list", "dashboard", "detail", "editor", "take", "immersive", "reading"):
                with self.subTest(layout=layout):
                    response = self.client.get("/dev/lq", params={"layout": layout})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["cache-control"], "no-store")
                    self.assertIn(f'data-lq-shell-preview="{layout}"', response.text)
                    if layout == "list":
                        self.assertIn('data-lq-slot="filter"></div>', response.text)
                        self.assertIn('data-lq-slot="footer"></div>', response.text)
            self.assertEqual(self.client.get("/dev/lq?layout=unknown").status_code, 404)
            self.assertEqual(self.client.get("/dev/lq?layout=").status_code, 404)
            for url in ("/dev/lq?shell=unknown", "/dev/lq?shell=", "/dev/lq?shell=centered&layout=list"):
                self.assertEqual(self.client.get(url).status_code, 404)
            centered = self.client.get("/dev/lq?shell=centered")
            self.assertEqual(centered.status_code, 200)
            self.assertEqual(centered.headers["cache-control"], "no-store")
            self.assertIn('class="centered-page-shell"', centered.text)
            self.assertIn('data-lq-shell-preview="centered"', centered.text)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM user_ui_preferences").fetchone()[0], 0)

    def test_shell_empty_caller_trims_only_fragment_boundaries(self):
        template = design_system.templates.env.from_string("""
        {% from 'macros/lq/shells.html' import lq_page_layout %}
        {% call(slot) lq_page_layout('whitespace-shell','reading') %}
          {% if slot == 'main' %}<pre>\n  retained spaces\n</pre>{% endif %}
        {% endcall %}
        """)
        html = template.render()
        self.assertIn('data-lq-slot="filter"></div>', html)
        self.assertIn('<pre>\n  retained spaces\n</pre>', html)

    def test_enabled_preview_rejects_inactive_and_missing_teachers(self):
        with patch.dict(os.environ, {"LANSHARE_LQ_PREVIEW": "true"}):
            for user_id in (2, 99):
                self.user = {"role": "teacher", "id": user_id}
                with self.subTest(user_id=user_id), patch("classroom_app.services.user_ui_preferences_service.get_db_connection") as preferences:
                    for url in ("/dev/lq", "/dev/lq?layout=editor", "/dev/lq?shell=centered"):
                        self.assertEqual(self.client.get(url).status_code, 403)
                    preferences.assert_not_called()


class LqInventoryTests(unittest.TestCase):
    def test_endpoint_isolation_prefix_and_dynamic_hint(self):
        routes = scan_routes('''
router = APIRouter(prefix="/manage")
@router.get("/one")
def one(user=Depends(get_current_teacher)):
    return templates.TemplateResponse(request, "one.html", {})
@router.get("/two")
def two():
    return templates.TemplateResponse(request, selected_template, {})
''', "routes.py")
        self.assertEqual(routes[0]["routePattern"], "/manage/one")
        self.assertEqual(routes[0]["templates"], ["one.html"])
        self.assertEqual(routes[0]["dependencies"], ["get_current_teacher"])
        self.assertEqual(routes[1]["templates"], [])
        self.assertTrue(routes[1]["dynamicTemplate"])

    def test_transitive_ownership_regeneration_and_retirement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "templates").mkdir()
            (root / "classroom_app/routers").mkdir(parents=True)
            (root / "templates/page.html").write_text('{% extends "base.html" %}', encoding="utf-8")
            (root / "templates/base.html").write_text('<html>Base</html>', encoding="utf-8")
            (root / "classroom_app/routers/pages.py").write_text('@router.get("/page")\ndef page():\n return templates.TemplateResponse(request, "page.html", {})', encoding="utf-8")
            first = build_registry(root)
            base = next(e for e in first["entries"] if e["template"] == "templates/base.html")
            self.assertEqual(base["ownerRoutes"], ["/page"])
            base["status"] = "已盘点"
            base["before"]["screenshots"] = ["before.png"]
            second = build_registry(root, first)
            base2 = next(e for e in second["entries"] if e["id"] == base["id"])
            self.assertEqual(base2["status"], "已盘点")
            self.assertEqual(base2["before"]["screenshots"], ["before.png"])
            (root / "templates/base.html").unlink()
            third = build_registry(root, second)
            self.assertEqual(third["retiredEntries"][0]["id"], base["id"])


class LqGuardTests(unittest.TestCase):
    def test_token_definitions_and_url_fragments_do_not_hide_page_literal_colors(self):
        self.assertEqual(violations("static/css/lq/tokens.css", ":root { --tone-success: #008844; }"), [])
        self.assertEqual(violations("static/css/lq/base.css", '.icon { mask: url("icons.svg#abc"); }'), [])
        self.assertEqual(violations("templates/page.html", '<div style="--progress: 50%"></div>'), [])
        rules = {r["rule"] for r in violations("templates/page.html", '<button class="btn btn-primary" style="color:#fff" onclick="confirm(1)">保存</button>')}
        self.assertTrue({"literal-color", "legacy-class", "inline-style", "native-confirm"}.issubset(rules))

    def test_removing_a_legacy_backdrop_does_not_create_a_blur_host(self):
        path = 'static/css/lq/pilot.css'
        self.assertEqual(violations(path, '.pilot { backdrop-filter:none; -webkit-backdrop-filter: none !important; }'), [])
        self.assertTrue(any(item['rule'] == 'blur-host' for item in violations(path,
            '.pilot { backdrop-filter:none; backdrop-filter:blur(var(--ls-blur-regular)); }')))
        self.assertTrue(any(item['rule'] == 'blur-host' for item in violations(path,
            '.pilot { backdrop-filter:inherit; }')))

    def test_unmigrated_warning_becomes_blocking_without_a_complete_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "page.html").write_text('<button class="btn btn-primary">保存</button>', encoding="utf-8")
            entry = {"id": "page", "template": "page.html", "status": "已盘点"}
            registry = {"entries": [entry]}
            self.assertEqual(audit(root, registry, [])["blocking"], [])
            entry["status"] = "迁移中"
            self.assertEqual(len(audit(root, registry, [])["blocking"]), 1)
            incomplete = {"path": "page.html", "rules": ["legacy-class"], "reason": "shared hook"}
            self.assertEqual(len(audit(root, registry, [incomplete])["blocking"]), 1)
            with self.assertRaises(ValueError):
                audit(root, registry, [], page="unknown")


if __name__ == "__main__":
    unittest.main()
