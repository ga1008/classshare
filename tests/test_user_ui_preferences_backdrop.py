"""S7 B package: page backdrop preference (mode + custom solid colour).

Pure service, schema, HTTP and SSR gates. No application startup, no network,
no PostgreSQL; the sqlite fixture is created in memory by each test.
"""

import json
import re
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader, select_autoescape

from classroom_app.db.postgres_required_columns import REQUIRED_POSTGRES_COLUMNS
from classroom_app.db.schema_user_ui_preferences import ensure_user_ui_preferences_schema
from classroom_app.dependencies import get_current_user
from classroom_app.routers import user_ui_preferences as router_mod
from classroom_app.services import user_ui_preferences_service as svc

ROOT = Path(__file__).resolve().parents[1]
LEGACY_TABLE = """CREATE TABLE user_ui_preferences (
    user_role TEXT NOT NULL, user_pk BIGINT NOT NULL, palette_key TEXT NOT NULL DEFAULT 'indigo',
    appearance TEXT NULL, glass TEXT NULL, version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(user_role,user_pk))"""


class BackdropValueTests(unittest.TestCase):
    """The value space is a whitelist; nothing else may reach storage."""

    def test_modes_cover_scene_off_and_every_registered_category(self):
        self.assertEqual(svc.BACKDROP_MODES,
                         frozenset({"scene", "off", *(f"scene-{key}" for key, _ in svc.BACKDROP_CATEGORIES)}))
        self.assertEqual(len(svc.BACKDROP_CATEGORIES), len({key for key, _ in svc.BACKDROP_CATEGORIES}))
        self.assertEqual(len(svc.BACKDROP_CATEGORIES), len({name for _, name in svc.BACKDROP_CATEGORIES}))
        for key, name in svc.BACKDROP_CATEGORIES:
            self.assertRegex(key, r"^[a-z][a-z-]*[a-z]$")
            self.assertTrue(name.strip())
        self.assertIn(svc.DEFAULT_BACKDROP, svc.BACKDROP_MODES)
        self.assertEqual(svc.DEFAULT_BACKDROP, "scene")

    def test_every_registered_category_exists_in_the_shipped_library(self):
        labels = {name for _, categories in svc.backdrop_library() for name in categories}
        self.assertTrue(labels, "The shipped manifest must provide categories")
        for _, name in svc.BACKDROP_CATEGORIES:
            self.assertIn(name, labels)

    def test_colour_accepts_only_lowercase_six_digit_hex(self):
        for value in ("#ffffff", "#000000", "#0a1b2c", "#abcdef"):
            self.assertIn(value, svc.HEX_COLOR)
        rejected = ("#FFFFFF", "#fff", "#ffffffff", "ffffff", "#gggggg", "white", "red",
                    "rgb(1,2,3)", "url(javascript:alert(1))", "#fff;background:url(x)",
                    "#ffffff ", " #ffffff", "#ffffff;", "var(--x)", "", "#ffff\nff",
                    None, 1, True, [], {}, b"#ffffff")
        for value in rejected:
            with self.subTest(value=value):
                self.assertNotIn(value, svc.HEX_COLOR)
        self.assertIn(svc.DEFAULT_BACKDROP_COLOR, svc.HEX_COLOR)

    def test_validate_rejects_illegal_values_loudly_instead_of_falling_back(self):
        for changes in ({"backdrop": "scene-unknown"}, {"backdrop": "SCENE"}, {"backdrop": ""},
                        {"backdrop": None}, {"backdrop_color": "#FFFFFF"}, {"backdrop_color": "red"},
                        {"backdrop_color": "#fff"}, {"backdrop_color": "#ffffff; background: url(x)"},
                        {"backdrop_color": 16777215}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                svc.validate_preference_changes(changes)
        self.assertEqual(svc.validate_preference_changes({"backdrop": "scene-thesis", "backdrop_color": "#0a1b2c"}),
                         {"backdrop": "scene-thesis", "backdrop_color": "#0a1b2c"})

    def test_client_mirror_of_the_category_registry_cannot_drift(self):
        source = (ROOT / "static/js/user_ui_preferences.js").read_text(encoding="utf-8")
        listed = re.search(r"BACKDROP_CATEGORY_KEYS = Object\.freeze\(\[(.*?)\]\)", source, re.S)
        self.assertIsNotNone(listed)
        self.assertEqual([value for value in re.findall(r"'([^']+)'", listed[1])],
                         [key for key, _ in svc.BACKDROP_CATEGORIES])


class BackdropLibraryTests(unittest.TestCase):
    def test_library_only_exposes_safe_file_names_and_is_deterministic(self):
        library = svc.backdrop_library()
        self.assertTrue(library)
        self.assertEqual(library, tuple(sorted(library)))
        self.assertIs(library, svc.backdrop_library())
        for file, categories in library:
            self.assertRegex(file, r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(?:webp|jpg|jpeg|png)$")
            self.assertTrue((ROOT / "static/img/life_tips" / file).is_file())
            self.assertIsInstance(categories, frozenset)

    def test_hostile_manifest_entries_never_become_urls(self):
        hostile = {"images": [
            {"file": "../../secret.env"}, {"file": "/etc/passwd"}, {"file": "a.webp?x=1"},
            {"file": "a b.webp"}, {"file": 'x");background:url(y'}, {"file": None}, {},
            {"file": "ok-image01.webp", "categories": ["考研"]},
        ]}
        with patch.object(svc.BACKDROP_MANIFEST.__class__, "read_text", lambda *_, **__: json.dumps(hostile)):
            svc.backdrop_library.cache_clear()
            self.addCleanup(svc.backdrop_library.cache_clear)
            self.assertEqual(svc.backdrop_library(), (("ok-image01.webp", frozenset({"考研"})),))
            self.assertEqual(svc.backdrop_image_for("scene", "seed"), "/static/img/life_tips/ok-image01.webp")

    def test_unreadable_manifest_degrades_to_no_image_without_raising(self):
        with patch.object(svc.BACKDROP_MANIFEST.__class__, "read_text", side_effect=OSError("missing")):
            svc.backdrop_library.cache_clear()
            self.addCleanup(svc.backdrop_library.cache_clear)
            with self.assertLogs(svc.logger, level="WARNING"):
                self.assertEqual(svc.backdrop_library(), ())
            self.assertIsNone(svc.backdrop_image_for("scene", "seed"))
            self.assertEqual(svc.resolve_backdrop({"backdrop": "scene"}, seed="seed")["image_css"], "none")

    def test_category_mode_only_picks_images_of_that_category(self):
        for key, name in svc.BACKDROP_CATEGORIES:
            url = svc.backdrop_image_for(f"scene-{key}", "seed-1")
            self.assertTrue(url.startswith(svc.BACKDROP_LIBRARY_BASE))
            file = url[len(svc.BACKDROP_LIBRARY_BASE):]
            self.assertIn(name, dict(svc.backdrop_library())[file], key)

    def test_pick_is_stable_per_account_day_and_differs_across_seeds(self):
        self.assertEqual(svc.backdrop_image_for("scene", "abc"), svc.backdrop_image_for("scene", "abc"))
        self.assertIsNone(svc.backdrop_image_for("off", "abc"))
        self.assertIsNone(svc.backdrop_image_for("nonsense", "abc"))
        seeds = {svc.backdrop_seed("student", pk, date(2026, 9, 22)) for pk in range(1, 40)}
        self.assertEqual(len(seeds), 39)
        self.assertNotEqual(svc.backdrop_seed("student", 1, date(2026, 9, 22)),
                            svc.backdrop_seed("student", 1, date(2026, 9, 23)))
        self.assertNotEqual(svc.backdrop_seed("student", 1, date(2026, 9, 22)),
                            svc.backdrop_seed("teacher", 1, date(2026, 9, 22)))
        self.assertRegex(svc.backdrop_seed("student", 1, date(2026, 9, 22)), r"^[0-9a-f]{16}$")
        self.assertGreater(len({svc.backdrop_image_for("scene", seed) for seed in seeds}), 1,
                           "Different accounts must not all share one image")

    def test_image_css_is_an_unquoted_url_or_none(self):
        resolved = svc.resolve_backdrop({"backdrop": "scene", "backdrop_color": "#0a1b2c"}, seed="seed")
        self.assertEqual(resolved["image_css"], f"url({resolved['image']})")
        self.assertNotIn('"', resolved["image_css"])
        self.assertNotIn("'", resolved["image_css"])
        self.assertEqual(resolved["color"], "#0a1b2c")
        off = svc.resolve_backdrop({"backdrop": "off", "backdrop_color": "#0a1b2c"}, seed="seed")
        self.assertEqual((off["image"], off["image_css"]), (None, "none"))
        # A stored value outside the whitelist resolves to the default; it is
        # never echoed into a style.
        hostile = svc.resolve_backdrop({"backdrop": "javascript:x", "backdrop_color": "#fff;}"}, seed="seed")
        self.assertEqual((hostile["mode"], hostile["color"]), ("scene", "#ffffff"))


class BackdropPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        ensure_user_ui_preferences_schema(self.conn, engine="sqlite")
        self.conn.commit()
        self.addCleanup(self.conn.close)
        self.student = {"role": "student", "id": 17}
        self.teacher = {"role": "teacher", "id": 17}

    @contextmanager
    def database(self):
        with self.conn:
            yield self.conn

    def client(self, user=None):
        app = FastAPI()
        app.include_router(router_mod.router)
        app.dependency_overrides[get_current_user] = lambda: user or self.student
        return TestClient(app)

    def test_defaults_are_scene_and_white_without_creating_a_row(self):
        current = svc.get_ui_preferences(self.conn, self.student)
        self.assertEqual((current["backdrop"], current["backdrop_color"]), ("scene", "#ffffff"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM user_ui_preferences").fetchone()[0], 0)

    def test_first_write_and_later_writes_share_the_existing_cas_version(self):
        first = svc.update_ui_preferences(self.conn, self.student, changes={"backdrop": "off", "backdrop_color": "#123456"}, version=0)
        self.assertEqual((first["backdrop"], first["backdrop_color"], first["version"]), ("off", "#123456", 1))
        self.assertEqual(first["palette_key"], "indigo")
        second = svc.update_ui_preferences(self.conn, self.student, changes={"backdrop": "scene-thesis"}, version=1)
        self.assertEqual((second["backdrop"], second["backdrop_color"], second["version"]), ("scene-thesis", "#123456", 2))
        with self.assertRaises(svc.PreferenceConflict) as raised:
            svc.update_ui_preferences(self.conn, self.student, changes={"backdrop": "off"}, version=1)
        self.assertEqual(raised.exception.current["backdrop"], "scene-thesis")
        self.assertEqual(svc.get_ui_preferences(self.conn, self.teacher)["backdrop"], "scene")

    def test_stored_illegal_values_resolve_to_defaults_without_rewriting_storage(self):
        self.conn.execute("INSERT INTO user_ui_preferences(user_role,user_pk,palette_key,backdrop,backdrop_color,version)"
                          " VALUES('student',17,'indigo','scene-retired','#FFFFFF',4)")
        current = svc.get_ui_preferences(self.conn, self.student)
        self.assertEqual((current["backdrop"], current["backdrop_color"]), ("scene", "#ffffff"))
        stored = self.conn.execute("SELECT backdrop,backdrop_color FROM user_ui_preferences").fetchone()
        self.assertEqual(tuple(stored), ("scene-retired", "#FFFFFF"))

    def test_schema_migration_adds_nullable_columns_and_keeps_old_rows(self):
        self.conn.execute("DROP TABLE user_ui_preferences")
        self.conn.execute(LEGACY_TABLE)
        self.conn.execute("INSERT INTO user_ui_preferences VALUES('student',17,'sky','dark','off',6,'2026-09-20 00:00:00')")
        for _ in range(2):
            ensure_user_ui_preferences_schema(self.conn, engine="sqlite")
        columns = {row["name"]: row for row in self.conn.execute("PRAGMA table_info(user_ui_preferences)")}
        for column in ("backdrop", "backdrop_color"):
            self.assertIn(column, columns)
            self.assertEqual((columns[column]["notnull"], columns[column]["dflt_value"]), (0, None))
        row = self.conn.execute("SELECT * FROM user_ui_preferences").fetchone()
        self.assertEqual((row["palette_key"], row["appearance"], row["glass"], row["version"]), ("sky", "dark", "off", 6))
        self.assertEqual((row["backdrop"], row["backdrop_color"]), (None, None))
        self.assertEqual(set(REQUIRED_POSTGRES_COLUMNS["user_ui_preferences"]), set(columns))

    def test_api_saves_valid_values_and_refuses_invalid_ones_before_touching_the_database(self):
        headers = {"X-UI-Preferences-Context": svc.preference_context_token(self.student)}
        with patch.object(router_mod, "get_db_connection", side_effect=self.database), self.client() as client:
            saved = client.patch("/api/profile/ui-preferences", json={"version": 0, "backdrop": "scene-career", "backdrop_color": "#102030"}, headers=headers)
            self.assertEqual(saved.status_code, 200)
            self.assertEqual(saved.json()["preferences"]["backdrop"], "scene-career")
        with patch.object(router_mod, "get_db_connection") as database, self.client() as client:
            for payload in ({"version": 1, "backdrop": "scene-unknown"}, {"version": 1, "backdrop": "off; drop"},
                            {"version": 1, "backdrop_color": "#FFFFFF"}, {"version": 1, "backdrop_color": "red"},
                            {"version": 1, "backdrop_color": "#ffffff; background:url(//evil)"},
                            {"version": 1, "backdrop_color": "javascript:alert(1)"},
                            {"version": 1, "backdrop": None}, {"version": 1, "backdrop_color": ""},
                            {"version": 1, "backdrop_color": "#" + "f" * 300}):
                with self.subTest(payload=payload):
                    self.assertEqual(client.patch("/api/profile/ui-preferences", json=payload, headers=headers).status_code, 422)
            database.assert_not_called()
        self.assertEqual(svc.get_ui_preferences(self.conn, self.student)["backdrop_color"], "#102030")


class BackdropSSRTests(unittest.TestCase):
    """Real base.html render: signed-in pages get the layer, anonymous do not."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        ensure_user_ui_preferences_schema(self.conn, engine="sqlite")
        self.conn.commit()
        self.addCleanup(self.conn.close)
        self.student = {"role": "student", "id": 17}
        self.env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape())
        self.env.globals.update(resolve_user_ui_preferences=svc.resolve_user_ui_preferences,
                                asset_url=lambda path: "/static/" + path,
                                static_asset_revision=lambda: "test-release", vite_entry_tags=lambda _: "")

    @contextmanager
    def database(self):
        with self.conn:
            yield self.conn

    def render(self, user):
        template = self.env.from_string("{% extends 'base.html' %}"
                                        "{% from 'macros/user_ui_preferences.html' import user_palette_select %}"
                                        "{% block body %}{{ user_palette_select(ui_palette) }}{% endblock %}")
        request = SimpleNamespace(url=SimpleNamespace(path="/dashboard"), state=SimpleNamespace())
        with patch.object(svc, "get_db_connection", side_effect=self.database):
            return template.render(request=request, user_info=user)

    def test_signed_in_page_renders_the_layer_with_only_custom_properties(self):
        html = self.render(self.student)
        self.assertIn('data-lq-page-backdrop', html)
        self.assertIn('data-lq-backdrop-mode="scene"', html)
        self.assertIn('data-lq-backdrop-color="#ffffff"', html)
        style = re.search(r'<div class="lq-page-backdrop"[^>]*style="([^"]*)"', html)[1]
        self.assertTrue(all(re.match(r"--[\w-]+\s*:", part.strip()) for part in style.split(";") if part.strip()), style)
        self.assertRegex(style, r"--lq-backdrop-image: url\(/static/img/life_tips/[A-Za-z0-9._-]+\)")
        self.assertIn('data-ui-preference-select="backdrop"', html)
        self.assertIn('data-ui-preference-input="backdrop_color"', html)
        for _, name in svc.BACKDROP_CATEGORIES:
            self.assertIn(name, html)
        # The layer precedes page content so it can never cover it.
        self.assertLess(html.index("data-lq-page-backdrop"), html.index("data-ui-preference-select"))

    def test_anonymous_pages_render_no_backdrop_layer_and_no_library_reference(self):
        html = self.render(None)
        self.assertNotIn("lq-page-backdrop", html)
        self.assertNotIn("/static/img/life_tips/", html)
        self.assertNotIn("data-lq-backdrop-catalog", html)

    def test_saved_choices_survive_a_reload_and_off_renders_the_custom_colour(self):
        svc.update_ui_preferences(self.conn, self.student, changes={"backdrop": "off", "backdrop_color": "#102030"}, version=0)
        html = self.render(self.student)
        self.assertIn('data-lq-backdrop-mode="off"', html)
        self.assertIn("--lq-backdrop-color: #102030", html)
        self.assertIn("--lq-backdrop-image: none", html)
        self.assertIn('<option value="off" selected>', html)
        self.assertIn('value="#102030"', html)

    def test_catalog_payload_is_json_and_matches_the_registry(self):
        html = self.render(self.student)
        payload = json.loads(re.search(r'data-lq-backdrop-catalog>(.*?)</script>', html, re.S)[1])
        self.assertEqual(payload, [{"key": f"scene-{key}", "name": name} for key, name in svc.BACKDROP_CATEGORIES])

    def test_backdrop_layer_style_sheet_is_not_a_blur_host(self):
        css = (ROOT / "static/css/lq/components/page-backdrop.css").read_text(encoding="utf-8")
        declarations = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        # The backdrop is the surface glass is seen against, never a blur host.
        self.assertNotIn("backdrop-filter", declarations)
        self.assertNotRegex(declarations, r"#[0-9a-fA-F]{3,8}\b")
        self.assertIn("position: fixed", declarations)
        self.assertIn("background-size: contain", declarations)
        self.assertIn('@import "./components/page-backdrop.css";',
                      (ROOT / "static/css/lq/index.css").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


class BackdropShellCoverageTests(unittest.TestCase):
    """Every document root has to include the layer. The manage shell does not
    extend base.html, so it was missed once and the backdrop simply never
    appeared for teachers or on any /manage page."""

    ROOTS = ("templates/base.html", "templates/manage/layout.html")

    def test_every_document_root_includes_the_backdrop_exactly_once(self):
        repo = Path(__file__).resolve().parents[1]
        for name in self.ROOTS:
            with self.subTest(template=name):
                text = (repo / name).read_text(encoding="utf-8")
                self.assertEqual(1, text.count("partials/lq_page_backdrop.html"),
                                 f"{name} must include the backdrop layer exactly once")

    def test_no_other_template_includes_it_so_the_layer_cannot_be_duplicated(self):
        repo = Path(__file__).resolve().parents[1]
        including = sorted(path.relative_to(repo).as_posix()
                           for path in (repo / "templates").rglob("*.html")
                           if "partials/lq_page_backdrop.html" in path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(self.ROOTS), including)
