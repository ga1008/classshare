import colorsys
import re
import sqlite3
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader, select_autoescape

from classroom_app.db.schema_user_ui_preferences import ensure_user_ui_preferences_schema
from classroom_app.dependencies import get_current_user
from classroom_app.routers import user_ui_preferences as router_mod
from classroom_app.services import user_ui_preferences_service as svc


class UIPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        ensure_user_ui_preferences_schema(self.conn)
        self.conn.commit()
        self.student = {"role": "student", "id": 17}
        self.other = {"role": "student", "id": 18}

    def tearDown(self):
        self.conn.close()

    @contextmanager
    def database(self):
        with self.conn:
            yield self.conn

    def save(self, key, version, user=None):
        return svc.update_ui_preferences(self.conn, user or self.student, palette_key=key, version=version)

    def test_default_read_is_read_only_and_does_not_create_rows(self):
        self.conn.execute("PRAGMA query_only = ON")
        statements = []
        self.conn.set_trace_callback(statements.append)
        preferences = svc.get_ui_preferences(self.conn, self.student)
        self.assertEqual((preferences["palette_key"], preferences["version"]), ("indigo", 0))
        self.assertEqual(len(statements), 1)
        self.assertTrue(statements[0].startswith("SELECT "))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM user_ui_preferences").fetchone()[0], 0)

    def test_persisted_preferences_are_account_isolated_and_versioned(self):
        first = self.save("mint", 0)
        self.assertEqual(first["version"], 1)
        self.assertEqual(svc.get_ui_preferences(self.conn, self.student), first)
        second = self.save("rose", 1)
        self.assertEqual((second["palette_key"], second["version"]), ("rose", 2))
        self.assertEqual(svc.get_ui_preferences(self.conn, self.other)["palette_key"], "indigo")
        self.assertNotEqual(first["context_token"], svc.preference_context_token(self.other))
        # Same numeric IDs in other role namespaces cannot alter the student row.
        self.conn.execute("INSERT INTO user_ui_preferences(user_role,user_pk,palette_key) VALUES('teacher',17,'sky')")
        self.assertEqual(svc.get_ui_preferences(self.conn, self.student)["palette_key"], "rose")
        with self.assertRaises(ValueError):
            self.save("sky", 0, {"role": "teacher", "id": 17})

    def test_compare_and_swap_rejects_both_insert_and_update_races(self):
        self.save("mint", 0)
        for stale_version in (0, 2):
            with self.subTest(version=stale_version), self.assertRaises(svc.PreferenceConflict) as raised:
                self.save("rose", stale_version)
            self.assertEqual(raised.exception.current["palette_key"], "mint")
            self.assertEqual(raised.exception.current["version"], 1)
        self.save("sky", 1)
        with self.assertRaises(svc.PreferenceConflict):
            self.save("violet", 1)
        self.assertEqual(svc.get_ui_preferences(self.conn, self.student)["palette_key"], "sky")

    def test_invalid_input_and_unknown_stored_key_have_safe_behavior(self):
        for key, version in (("not-a-palette", 0), ("mint", True), ("mint", -1)):
            with self.subTest(key=key, version=version), self.assertRaises(ValueError):
                self.save(key, version)
        self.conn.execute("INSERT INTO user_ui_preferences(user_role,user_pk,palette_key) VALUES('student',17,'retired')")
        current = svc.get_ui_preferences(self.conn, self.student)
        self.assertEqual((current["palette_key"], current["version"]), ("indigo", 1))
        self.assertEqual(self.save("violet", 1)["palette_key"], "violet")

    def test_ssr_is_request_scoped_and_queries_only_adapted_student_pages(self):
        self.save("mint", 0)
        with patch.object(svc, "get_db_connection", side_effect=self.database) as database:
            request = SimpleNamespace(url=SimpleNamespace(path="/dashboard"), state=SimpleNamespace())
            first = svc.resolve_user_ui_preferences(request, self.student)
            self.assertEqual(first["palette_key"], "mint")
            self.assertIs(first, svc.resolve_user_ui_preferences(request, self.student))
            self.assertEqual(database.call_count, 1)
            for path, user in (("/blog", self.student), ("/classroom/3", {"role": "teacher", "id": 17})):
                request = SimpleNamespace(url=SimpleNamespace(path=path), state=SimpleNamespace())
                self.assertFalse(svc.resolve_user_ui_preferences(request, user)["enabled"])
            self.assertEqual(database.call_count, 1)
            other_request = SimpleNamespace(url=SimpleNamespace(path="/classroom/3"), state=SimpleNamespace())
            self.assertEqual(svc.resolve_user_ui_preferences(other_request, self.other)["palette_key"], "indigo")

    def test_ssr_and_macro_have_correct_first_paint_with_no_client_cache(self):
        self.save("rose", 0)
        env = Environment(loader=FileSystemLoader(Path(__file__).resolve().parents[1] / "templates"), autoescape=select_autoescape())
        env.globals.update(resolve_user_ui_preferences=svc.resolve_user_ui_preferences, asset_url=lambda path: "/static/" + path, vite_entry_tags=lambda _: "")
        template = env.from_string("{% extends 'base.html' %}{% from 'macros/user_ui_preferences.html' import user_palette_select %}{% block body %}{{ user_palette_select(ui_palette) }}{% endblock %}")
        request = SimpleNamespace(url=SimpleNamespace(path="/dashboard"), state=SimpleNamespace())
        with patch.object(svc, "get_db_connection", side_effect=self.database):
            html = template.render(request=request, user_info=self.student)
        self.assertIn('data-theme="lanshare"', html)
        self.assertIn('data-ui-palette="rose"', html)
        self.assertIn('data-ui-palette-version="1"', html)
        self.assertIn('<option value="rose" selected>', html)
        self.assertEqual(html.count('data-ui-palette-select'), 1)
        self.assertLess(html.index('css/user_ui_preferences.css'), html.index('<body'))
        teacher_html = template.render(request=request, user_info={"role": "teacher", "id": 17})
        self.assertNotIn('data-ui-palette=', teacher_html)
        self.assertNotIn('user_ui_preferences.css', teacher_html)

    def test_ssr_database_failure_keeps_page_available_and_marks_recovery(self):
        request = SimpleNamespace(url=SimpleNamespace(path="/dashboard"), state=SimpleNamespace())
        with patch.object(svc, "get_db_connection", side_effect=RuntimeError("test DB unavailable")), self.assertLogs(svc.logger, level="WARNING"):
            preferences = svc.resolve_user_ui_preferences(request, self.student)
        self.assertTrue(preferences["enabled"])
        self.assertFalse(preferences["available"])
        self.assertEqual(preferences["palette_key"], "indigo")

    def test_all_palettes_keep_text_controls_and_focus_readable(self):
        css = (Path(__file__).resolve().parents[1] / "static/css/user_ui_preferences.css").read_text(encoding="utf-8")
        def tokens(selector):
            body = re.search(re.escape(selector) + r"\s*\{([^}]+)\}", css).group(1)
            return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", body))
        defaults = tokens("[data-ui-palette]")
        def rgb(value):
            hue, saturation, lightness = (float(item.rstrip("%")) for item in value.split())
            return colorsys.hls_to_rgb(hue / 360, lightness / 100, saturation / 100)
        def luminance(value):
            return sum(weight * (channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4) for weight, channel in zip((.2126, .7152, .0722), value))
        def ratio(first, second):
            a, b = sorted((luminance(first), luminance(second)))
            return (b + .05) / (a + .05)
        for key in svc.PALETTE_KEYS:
            palette = {**defaults, **(tokens(f'[data-ui-palette="{key}"]') if key != "indigo" else {})}
            with self.subTest(palette=key):
                self.assertGreaterEqual(ratio(rgb(palette["--ls-primary"]), (1, 1, 1)), 4.5)
                self.assertGreaterEqual(ratio(rgb(palette["--ls-accent-foreground"]), (1, 1, 1)), 4.5)
                for surface in ("--ls-background", "--ls-accent"):
                    self.assertGreaterEqual(ratio(rgb(palette["--ls-muted-foreground"]), rgb(palette[surface])), 4.5)
                    self.assertGreaterEqual(ratio(rgb(palette["--ls-input"]), rgb(palette[surface])), 3)
                    self.assertGreaterEqual(ratio(rgb(palette["--ls-primary"]), rgb(palette[surface])), 3)
        self.assertNotRegex(css, r"--ls-(?:success|warning|destructive|info)\s*:")

    def client(self, user=None):
        app = FastAPI()
        app.include_router(router_mod.router)
        app.dependency_overrides[get_current_user] = lambda: user or self.student
        return TestClient(app)

    def test_api_is_field_scoped_account_scoped_and_returns_conflict(self):
        headers = {"X-UI-Preferences-Context": svc.preference_context_token(self.student)}
        with patch.object(router_mod, "get_db_connection", side_effect=self.database), self.client() as client:
            initial = client.get("/api/profile/ui-preferences")
            self.assertEqual(initial.status_code, 200)
            self.assertIn("no-store", initial.headers["cache-control"])
            payload = {"palette_key": "sky", "version": 0}
            for extra in ({"user_pk": 18}, {"display_name": "overwrite"}):
                self.assertEqual(client.patch("/api/profile/ui-preferences", json={**payload, **extra}, headers=headers).status_code, 422)
            saved = client.patch("/api/profile/ui-preferences", json=payload, headers=headers)
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(saved.json()["preferences"]["version"], 1)
            conflict = client.patch("/api/profile/ui-preferences", json=payload, headers=headers)
            self.assertEqual(conflict.status_code, 409)
            self.assertEqual(conflict.json()["code"], "version_conflict")
            self.assertEqual(conflict.json()["preferences"]["version"], 1)
            for version in (True, -1, "1"):
                self.assertEqual(client.patch("/api/profile/ui-preferences", json={**payload, "version": version}, headers=headers).status_code, 422)
        with patch.object(router_mod, "get_db_connection", side_effect=self.database), self.client(self.other) as client:
            self.assertEqual(client.get("/api/profile/ui-preferences").json()["preferences"]["palette_key"], "indigo")
            stale_account = client.patch("/api/profile/ui-preferences", json=payload, headers=headers)
            self.assertEqual(stale_account.status_code, 409)
            self.assertEqual(stale_account.json()["detail"]["code"], "identity_changed")
            self.assertEqual(svc.get_ui_preferences(self.conn, self.other)["version"], 0)

    def test_api_rejects_teacher_unauthenticated_and_unavailable_database(self):
        with self.client({"role": "teacher", "id": 17}) as client:
            self.assertEqual(client.get("/api/profile/ui-preferences").status_code, 403)
        app = FastAPI()
        app.include_router(router_mod.router)
        def unauthenticated():
            raise HTTPException(401, "login required")
        app.dependency_overrides[get_current_user] = unauthenticated
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/profile/ui-preferences").status_code, 401)
        with patch.object(router_mod, "get_db_connection", side_effect=RuntimeError("private DB details")), self.client() as client, self.assertLogs(router_mod.logger, level="ERROR"):
            response = client.get("/api/profile/ui-preferences")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private DB details", response.text)


if __name__ == "__main__":
    unittest.main()
