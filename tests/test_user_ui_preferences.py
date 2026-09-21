import colorsys
import sqlite3
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader, select_autoescape

from classroom_app import dependencies
from classroom_app.db.schema_user_ui_preferences import ensure_user_ui_preferences_schema
from classroom_app.dependencies import get_current_user
from classroom_app.routers import user_ui_preferences as router_mod
from classroom_app.services import user_ui_preferences_service as svc


class UIPreferencesTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        ensure_user_ui_preferences_schema(self.conn, engine="sqlite")
        self.conn.commit()
        self.student = {"role": "student", "id": 17}
        self.other = {"role": "student", "id": 18}
        self.teacher = {"role": "teacher", "id": 17}

    def tearDown(self):
        self.conn.close()

    @contextmanager
    def database(self):
        with self.conn:
            yield self.conn

    def save(self, key, version, user=None):
        return svc.update_ui_preferences(self.conn, user or self.student, changes={"palette_key": key}, version=version)

    def test_default_read_is_read_only_and_does_not_create_rows(self):
        self.conn.execute("PRAGMA query_only = ON")
        statements = []
        self.conn.set_trace_callback(statements.append)
        for user, palette in ((self.student, "indigo"), (self.teacher, "teal")):
            preferences = svc.get_ui_preferences(self.conn, user)
            self.assertEqual((preferences["palette_key"], preferences["version"]), (palette, 0))
            self.assertEqual((preferences["appearance"], preferences["glass"]), ("auto", "tinted"))
        self.assertEqual(len(statements), 2)
        self.assertTrue(all(statement.startswith("SELECT ") for statement in statements))
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
        teacher = self.save("teal", 1, self.teacher)
        self.assertEqual((teacher["palette_key"], teacher["version"]), ("teal", 2))
        self.assertNotEqual(first["context_token"], teacher["context_token"])
        self.assertEqual(svc.get_ui_preferences(self.conn, self.student)["palette_key"], "rose")

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

    def test_ssr_all_pages_cache_each_identity_without_cross_account_reuse(self):
        self.save("mint", 0)
        with patch.object(svc, "get_db_connection", side_effect=self.database) as database:
            request = SimpleNamespace(url=SimpleNamespace(path="/dashboard"), state=SimpleNamespace())
            first = svc.resolve_user_ui_preferences(request, self.student)
            self.assertEqual(first["palette_key"], "mint")
            self.assertIs(first, svc.resolve_user_ui_preferences(request, self.student))
            self.assertEqual(database.call_count, 1)
            teacher = svc.resolve_user_ui_preferences(request, self.teacher)
            self.assertEqual(teacher["palette_key"], "teal")
            self.assertNotEqual(teacher["context_token"], first["context_token"])
            self.assertIs(first, svc.resolve_user_ui_preferences(request, self.student))
            for invalid in (None, {"role": "admin", "id": 17}, {"role": "teacher", "id": True},
                            {"role": "student", "id": 17.5}, {"role": "student", "id": 0}):
                self.assertEqual(svc.resolve_user_ui_preferences(request, invalid), {"enabled": False})
            self.assertEqual(database.call_count, 2)
            for path in ("/blog", "/profile", "/manage", "/resume", "/exam/1", "/classroom/3"):
                page_request = SimpleNamespace(url=SimpleNamespace(path=path), state=SimpleNamespace())
                self.assertTrue(svc.resolve_user_ui_preferences(page_request, self.teacher)["enabled"])
            other_request = SimpleNamespace(url=SimpleNamespace(path="/classroom/3"), state=SimpleNamespace())
            self.assertEqual(svc.resolve_user_ui_preferences(other_request, self.other)["palette_key"], "indigo")

    def test_ssr_and_macro_have_correct_first_paint_with_no_client_cache(self):
        self.save("rose", 0)
        svc.update_ui_preferences(self.conn, self.student, changes={"appearance": "dark", "glass": "off"}, version=1)
        env = Environment(loader=FileSystemLoader(Path(__file__).resolve().parents[1] / "templates"), autoescape=select_autoescape())
        env.globals.update(resolve_user_ui_preferences=svc.resolve_user_ui_preferences, asset_url=lambda path: "/static/" + path, static_asset_revision=lambda: "test-release", vite_entry_tags=lambda _: "")
        template = env.from_string("{% extends 'base.html' %}{% from 'macros/user_ui_preferences.html' import user_palette_select %}{% block body %}{{ user_palette_select(ui_palette) }}{% endblock %}")
        request = SimpleNamespace(url=SimpleNamespace(path="/dashboard"), state=SimpleNamespace())
        with patch.object(svc, "get_db_connection", side_effect=self.database):
            html = template.render(request=request, user_info=self.student)
            teacher_html = template.render(request=request, user_info=self.teacher)
        self.assertIn('data-theme="lanshare"', html)
        self.assertIn('data-ui-palette="rose"', html)
        self.assertIn('data-ui-palette-version="2"', html)
        self.assertIn('data-appearance-preference="dark"', html)
        self.assertIn('data-appearance="dark"', html)
        self.assertIn('data-glass-preference="off"', html)
        self.assertIn('data-lq-glass="off"', html)
        self.assertIn('data-lq-tier="C"', html)
        self.assertIn('<option value="rose" selected>', html)
        self.assertEqual(html.count('data-ui-palette-select'), 1)
        self.assertLess(html.index('href="/static/tailwind_app"'), html.index('<body'))
        self.assertLess(html.index('<script>'), html.index('rel="stylesheet"'))
        self.assertIn('data-ui-palette="teal"', teacher_html)
        self.assertIn('data-ui-palette-version="0"', teacher_html)
        self.assertIn('data-glass-preference="tinted"', teacher_html)
        self.assertIn('data-lq-glass="off"', teacher_html)
        self.assertIn(svc.preference_context_token(self.teacher), teacher_html)
        self.assertNotIn(svc.preference_context_token(self.student), teacher_html)

    def test_ssr_database_failure_keeps_page_available_and_marks_recovery(self):
        request = SimpleNamespace(url=SimpleNamespace(path="/dashboard"), state=SimpleNamespace())
        with patch.object(svc, "get_db_connection", side_effect=RuntimeError("test DB unavailable")) as database, self.assertLogs(svc.logger, level="WARNING"):
            preferences = svc.resolve_user_ui_preferences(request, self.student)
            teacher = svc.resolve_user_ui_preferences(request, self.teacher)
            self.assertIs(preferences, svc.resolve_user_ui_preferences(request, self.student))
            self.assertEqual(database.call_count, 2)
        self.assertTrue(preferences["enabled"])
        self.assertFalse(preferences["available"])
        self.assertEqual(preferences["palette_key"], "indigo")
        self.assertEqual((teacher["palette_key"], teacher["appearance"], teacher["glass"]), ("teal", "auto", "tinted"))
        self.assertFalse(teacher["available"])
        self.assertNotEqual(preferences["context_token"], teacher["context_token"])

    def test_all_palettes_keep_text_controls_and_focus_readable(self):
        from tools.ui.export_tokens import PALETTES, resolve_theme

        self.assertEqual(set(PALETTES), svc.PALETTE_KEYS)

        def rgb(value, background=None):
            channels, *opacity = value.split("/")
            hue, saturation, lightness = (float(item.rstrip("%")) for item in channels.split())
            color = colorsys.hls_to_rgb(hue / 360, lightness / 100, saturation / 100)
            if opacity:
                self.assertIsNotNone(background, "Transparent colors require an explicit surface")
                alpha = float(opacity[0])
                return tuple(alpha * channel + (1 - alpha) * base for channel, base in zip(color, background))
            return color

        def luminance(value):
            return sum(weight * (channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4) for weight, channel in zip((.2126, .7152, .0722), value))

        def ratio(first, second):
            a, b = sorted((luminance(first), luminance(second)))
            return (b + .05) / (a + .05)

        for appearance in ("light", "dark"):
            baseline = resolve_theme("indigo", appearance)
            for key in PALETTES:
                palette = resolve_theme(key, appearance)
                with self.subTest(palette=key, appearance=appearance):
                    self.assertGreaterEqual(ratio(rgb(palette["--ls-primary"]), rgb(palette["--ls-on-primary"])), 4.5)
                    self.assertGreaterEqual(ratio(rgb(palette["--ls-accent-foreground"]), rgb(palette["--ls-accent"])), 4.5)
                    for surface in ("--ls-background", "--ls-accent"):
                        self.assertGreaterEqual(ratio(rgb(palette["--ls-muted-foreground"]), rgb(palette[surface])), 4.5)
                        self.assertGreaterEqual(ratio(rgb(palette["--ls-input"]), rgb(palette[surface])), 3)
                        self.assertGreaterEqual(ratio(rgb(palette["--ls-primary"]), rgb(palette[surface])), 3)
                    surface = rgb(palette["--ls-surface-1"])
                    self.assertGreaterEqual(ratio(rgb(palette["--ls-on-primary-soft"]), rgb(palette["--ls-primary-soft"], surface)), 4.5)
                    for state in ("success", "warning", "danger", "info", "neutral"):
                        prefix = f"--ls-tone-{state}"
                        for part in ("base", "fg", "soft", "on-base"):
                            token = f"{prefix}-{part}"
                            self.assertEqual(palette[token], baseline[token], "Account palette must not recolor semantic states")
                        self.assertGreaterEqual(ratio(rgb(palette[f"{prefix}-fg"]), rgb(palette[f"{prefix}-soft"], surface)), 4.5)
                        self.assertGreaterEqual(ratio(rgb(palette[f"{prefix}-on-base"]), rgb(palette[f"{prefix}-base"])), 4.5)

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
            for version in (True, False, -1, "1", 1.0, None, 2147483647):
                self.assertEqual(client.patch("/api/profile/ui-preferences", json={**payload, "version": version}, headers=headers).status_code, 422)
        with patch.object(router_mod, "get_db_connection", side_effect=self.database), self.client(self.other) as client:
            self.assertEqual(client.get("/api/profile/ui-preferences").json()["preferences"]["palette_key"], "indigo")
            stale_account = client.patch("/api/profile/ui-preferences", json=payload, headers=headers)
            self.assertEqual(stale_account.status_code, 409)
            self.assertEqual(stale_account.json()["detail"]["code"], "identity_changed")
            self.assertEqual(svc.get_ui_preferences(self.conn, self.other)["version"], 0)

    def test_api_allows_teacher_and_rejects_unauthenticated_and_unavailable_database(self):
        with patch.object(router_mod, "get_db_connection", side_effect=self.database), self.client(self.teacher) as client:
            response = client.get("/api/profile/ui-preferences")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["preferences"]["palette_key"], "teal")
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

    def test_teacher_first_appearance_write_and_old_palette_client_preserve_other_fields(self):
        headers = {"X-UI-Preferences-Context": svc.preference_context_token(self.teacher)}
        with patch.object(router_mod, "get_db_connection", side_effect=self.database), self.client(self.teacher) as client:
            first = client.patch("/api/profile/ui-preferences", json={"version": 0, "appearance": "dark"}, headers=headers)
            self.assertEqual(first.status_code, 200)
            self.assertEqual({key: first.json()["preferences"][key] for key in ("palette_key", "appearance", "glass", "version")},
                             {"palette_key": "teal", "appearance": "dark", "glass": "tinted", "version": 1})
            stored = self.conn.execute("SELECT palette_key,appearance,glass FROM user_ui_preferences WHERE user_role='teacher'").fetchone()
            self.assertEqual(tuple(stored), ("teal", "dark", None))
            second = client.patch("/api/profile/ui-preferences", json={"version": 1, "appearance": "light", "glass": "off"}, headers=headers)
            self.assertEqual(second.json()["preferences"]["version"], 2)
            legacy = client.patch("/api/profile/ui-preferences", json={"version": 2, "palette_key": "rose"}, headers=headers)
            current = legacy.json()["preferences"]
            self.assertEqual((current["palette_key"], current["appearance"], current["glass"], current["version"]),
                             ("rose", "light", "off", 3))
            self.assertIn("private", legacy.headers["cache-control"])
            self.assertIn("no-store", legacy.headers["cache-control"])
            self.assertEqual(legacy.headers["vary"], "Cookie, Authorization")

    def test_partial_fields_still_share_one_cas_version(self):
        headers = {"X-UI-Preferences-Context": svc.preference_context_token(self.student)}
        with patch.object(router_mod, "get_db_connection", side_effect=self.database), self.client() as client:
            first = client.patch("/api/profile/ui-preferences", json={"version": 0, "appearance": "dark"}, headers=headers)
            self.assertEqual(first.status_code, 200)
            stale = client.patch("/api/profile/ui-preferences", json={"version": 0, "glass": "off"}, headers=headers)
            self.assertEqual(stale.status_code, 409)
            self.assertEqual(stale.json()["code"], "version_conflict")
            self.assertEqual((stale.json()["preferences"]["appearance"], stale.json()["preferences"]["glass"]), ("dark", "tinted"))
            client.patch("/api/profile/ui-preferences", json={"version": 1, "glass": "off"}, headers=headers)
            stale = client.patch("/api/profile/ui-preferences", json={"version": 1, "palette_key": "mint"}, headers=headers)
            self.assertEqual(stale.status_code, 409)
            self.assertEqual((stale.json()["preferences"]["palette_key"], stale.json()["preferences"]["version"]), ("indigo", 2))

    def test_api_rejects_null_empty_unknown_and_invalid_enum_before_database_access(self):
        headers = {"X-UI-Preferences-Context": svc.preference_context_token(self.student)}
        invalid = [{}, {"version": 0}, {"appearance": "dark"}, {"version": 0, "appearance": "auto", "user_role": "teacher"}]
        for field in svc.PREFERENCE_VALUES:
            invalid.extend({"version": 0, field: value} for value in (None, "", "retired", 1, True, [], {}))
        with patch.object(router_mod, "get_db_connection") as database, self.client() as client:
            for payload in invalid:
                with self.subTest(payload=payload):
                    response = client.patch("/api/profile/ui-preferences", json=payload, headers=headers)
                    self.assertEqual(response.status_code, 422)
            database.assert_not_called()

    def test_identity_change_cannot_write_any_preference_field(self):
        self.save("mint", 0)
        headers = {"X-UI-Preferences-Context": svc.preference_context_token(self.student)}
        with patch.object(router_mod, "get_db_connection") as database, self.client(self.teacher) as client:
            response = client.patch("/api/profile/ui-preferences", json={"version": 0, "appearance": "dark", "glass": "off"}, headers=headers)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"]["code"], "identity_changed")
            database.assert_not_called()
        self.assertEqual(svc.get_ui_preferences(self.conn, self.student)["palette_key"], "mint")
        self.assertEqual(svc.get_ui_preferences(self.conn, self.teacher)["version"], 0)

    def test_service_validates_partial_changes_and_falls_back_without_rewriting_stored_values(self):
        for changes in ({}, {"appearance": None}, {"glass": "auto"}, {"palette_key": "teal", "user_pk": 18}, {"appearance": []}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                svc.update_ui_preferences(self.conn, self.student, changes=changes, version=0)
        for version in (True, -1, 1.0, "0", 2147483647):
            with self.subTest(version=version), self.assertRaises(ValueError):
                svc.update_ui_preferences(self.conn, self.student, changes={"appearance": "dark"}, version=version)
        self.conn.execute("INSERT INTO user_ui_preferences(user_role,user_pk,palette_key,appearance,glass,version) VALUES('teacher',17,'retired','unknown',NULL,7)")
        current = svc.get_ui_preferences(self.conn, self.teacher)
        self.assertEqual((current["palette_key"], current["appearance"], current["glass"], current["version"]), ("teal", "auto", "tinted", 7))
        stored = self.conn.execute("SELECT palette_key,appearance,glass,version FROM user_ui_preferences").fetchone()
        self.assertEqual(tuple(stored), ("retired", "unknown", None, 7))

    def test_legacy_schema_migration_is_nullable_idempotent_and_preserves_values(self):
        self.conn.execute("DROP TABLE user_ui_preferences")
        self.conn.execute("""CREATE TABLE user_ui_preferences (
            user_role TEXT NOT NULL, user_pk BIGINT NOT NULL, palette_key TEXT NOT NULL DEFAULT 'indigo',
            version INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(user_role,user_pk))""")
        self.conn.execute("INSERT INTO user_ui_preferences VALUES('teacher',17,'sky',8,'2026-09-20 00:00:00')")
        for _ in range(2):
            ensure_user_ui_preferences_schema(self.conn, engine="sqlite")
        row = self.conn.execute("SELECT * FROM user_ui_preferences").fetchone()
        self.assertEqual((row["palette_key"], row["version"], row["updated_at"], row["appearance"], row["glass"]),
                         ("sky", 8, "2026-09-20 00:00:00", None, None))
        columns = {row["name"]: row for row in self.conn.execute("PRAGMA table_info(user_ui_preferences)")}
        self.assertEqual((columns["appearance"]["notnull"], columns["glass"]["notnull"]), (0, 0))

    def test_commit_failure_rolls_back_and_returns_unavailable_not_success(self):
        @contextmanager
        def failed_commit():
            try:
                yield self.conn
                raise RuntimeError("synthetic commit failure")
            finally:
                self.conn.rollback()

        headers = {"X-UI-Preferences-Context": svc.preference_context_token(self.student)}
        with patch.object(router_mod, "get_db_connection", side_effect=failed_commit), self.client() as client, self.assertLogs(router_mod.logger, level="ERROR"):
            response = client.patch("/api/profile/ui-preferences", json={"version": 0, "appearance": "dark", "glass": "off"}, headers=headers)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("synthetic commit", response.text)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM user_ui_preferences").fetchone()[0], 0)


class UIPreferencesIdentityTests(unittest.TestCase):
    """Exercise real root authentication; only token parsing and DB are replaced."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE teachers(id INTEGER PRIMARY KEY, is_active INTEGER, is_super_admin INTEGER);
            INSERT INTO teachers VALUES(17,1,0),(18,1,1),(19,0,0);
            CREATE TABLE students(id INTEGER PRIMARY KEY, enrollment_status TEXT);
            INSERT INTO students VALUES(17,'active'),(19,'suspended');
        """)
        ensure_user_ui_preferences_schema(self.conn, engine="sqlite")
        self.conn.commit()
        self.addCleanup(self.conn.close)
        self.user = None
        app = FastAPI()
        app.include_router(router_mod.router)
        app.dependency_overrides[dependencies.get_current_user_optional] = lambda: self.user
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        for target, replacement in (
            ("classroom_app.dependencies.get_db_connection", self.database),
            ("classroom_app.dependencies._identity_cache_is_valid", lambda *_: False),
            ("classroom_app.dependencies._cache_valid_identity", lambda *_: None),
            ("classroom_app.dependencies.invalidate_session_for_user", lambda *_: None),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    @contextmanager
    def database(self):
        with self.conn:
            yield self.conn

    def test_student_teacher_and_super_admin_have_own_role_namespaces(self):
        with patch.object(router_mod, "get_db_connection", side_effect=self.database):
            for role, user_id, palette in (("student", 17, "indigo"), ("teacher", 17, "teal"), ("teacher", 18, "teal")):
                with self.subTest(role=role, user_id=user_id):
                    self.user = {"role": role, "id": user_id}
                    response = self.client.get("/api/profile/ui-preferences")
                    self.assertEqual(response.status_code, 200)
                    current = response.json()["preferences"]
                    self.assertEqual((current["palette_key"], current["version"]), (palette, 0))
                    saved = self.client.patch("/api/profile/ui-preferences", json={"version": 0, "appearance": "dark"},
                                              headers={"X-UI-Preferences-Context": current["context_token"]})
                    self.assertEqual(saved.status_code, 200)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM user_ui_preferences").fetchone()[0], 3)

    def test_anonymous_inactive_missing_and_admin_role_never_access_preferences(self):
        for user, status in ((None, 401), ({"role": "admin", "id": 18}, 403),
                             ({"role": "teacher", "id": 19}, 403), ({"role": "student", "id": 19}, 403),
                             ({"role": "teacher", "id": 99}, 403), ({"role": "student", "id": 99}, 403)):
            self.user = user
            with self.subTest(user=user), patch.object(router_mod, "get_db_connection") as database:
                self.assertEqual(self.client.get("/api/profile/ui-preferences").status_code, status)
                response = self.client.patch("/api/profile/ui-preferences", json={"version": 0, "appearance": "dark"})
                self.assertEqual(response.status_code, status)
                database.assert_not_called()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM user_ui_preferences").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
