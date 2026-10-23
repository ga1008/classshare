"""Learning-document library redesign: kinds, root collapse, activity sort, preview targets,
git progress board and the read-only lesson binding plan."""
import sqlite3
import unittest

from classroom_app.routers.materials_parts.common import (
    _apply_material_kind_filter,
    _attach_root_activity,
    _list_material_rows_for_parent,
    _sort_items_by_activity,
)
from classroom_app.services import materials_git_progress as progress
from classroom_app.services.material_document_kind_service import (
    MATERIAL_KIND_FILTER_GROUPS,
    attach_material_document_kinds,
    build_kind_facets,
    classify_material_kind,
    normalize_kind_filter,
)
from classroom_app.services.material_preview_service import build_material_preview


class MaterialKindTests(unittest.TestCase):
    def test_specific_kinds_win_over_generic_ones(self):
        self.assertEqual(classify_material_kind({"node_type": "folder", "is_git_repository": True, "lessondoc_pack": {"pack_id": 1}}), "git_repository")
        self.assertEqual(classify_material_kind({"node_type": "folder", "lessondoc_pack": {"pack_id": 1}}), "lessondoc_pack")
        self.assertEqual(classify_material_kind({"node_type": "folder", "is_html_package": True, "is_renderable": True, "render_kind": "html"}), "html_package")
        self.assertEqual(classify_material_kind({"node_type": "folder", "is_renderable": True, "render_kind": "html"}), "html_page")
        self.assertEqual(classify_material_kind({"node_type": "folder"}), "folder")

    def test_file_kinds_follow_extension_and_preview_type(self):
        cases = {
            ("readme.md", "markdown"): "markdown",
            ("a.pdf", "pdf"): "pdf",
            ("a.docx", "document"): "office_document",
            ("a.xlsx", "spreadsheet"): "spreadsheet",
            ("a.pptx", "presentation"): "presentation",
            ("a.png", "image"): "image",
            ("index.html", "text"): "html_page",
            ("main.py", "text"): "code",
            ("notes.txt", "text"): "text",
            ("pack.zip", "binary"): "archive",
            ("clip.mp4", "binary"): "media",
            ("blob.bin", "binary"): "file",
        }
        for (name, preview_type), expected in cases.items():
            ext = name.rsplit(".", 1)[-1]
            self.assertEqual(classify_material_kind({"node_type": "file", "name": name, "file_ext": ext, "preview_type": preview_type}), expected, name)

    def test_attach_writes_label_tone_and_preview_mode(self):
        item = attach_material_document_kinds([{"node_type": "file", "name": "a.pdf", "file_ext": "pdf", "preview_type": "pdf"}])[0]
        self.assertEqual(item["document_kind"], "pdf")
        self.assertEqual(item["kind_label"], "PDF")
        self.assertTrue(item["kind_color"].startswith("#"))
        self.assertEqual(item["kind_preview_mode"], "document")

    def test_kind_filter_groups_and_facets(self):
        items = attach_material_document_kinds([
            {"node_type": "folder", "is_git_repository": True},
            {"node_type": "file", "name": "a.md", "file_ext": "md", "preview_type": "markdown"},
            {"node_type": "file", "name": "a.docx", "file_ext": "docx", "preview_type": "document"},
        ])
        facets = build_kind_facets(items)
        self.assertEqual(facets["git_repository"], 1)
        self.assertEqual(facets["markdown"], 1)
        self.assertEqual(facets["document"], 1)
        self.assertEqual(set(facets), {group["key"] for group in MATERIAL_KIND_FILTER_GROUPS})
        self.assertEqual(normalize_kind_filter("DOCUMENT"), "document")
        self.assertEqual(normalize_kind_filter("nope"), "")
        self.assertEqual([item["document_kind"] for item in _apply_material_kind_filter(items, "document")], ["office_document"])
        self.assertEqual(len(_apply_material_kind_filter(items, "")), 3)


class MaterialPreviewTargetTests(unittest.TestCase):
    def test_document_formats_get_page_preview_and_pdf_gets_inline(self):
        pdf = build_material_preview({"id": 7, "node_type": "file", "name": "a.pdf", "file_ext": "pdf", "preview_type": "pdf"})
        self.assertEqual(pdf["preview_mode"], "document")
        self.assertEqual(pdf["preview_url"], "/materials/document-preview/7")
        self.assertEqual(pdf["preview_media_url"], "/materials/inline/7")
        docx = build_material_preview({"id": 8, "node_type": "file", "name": "a.docx", "file_ext": "docx", "preview_type": "document"})
        self.assertEqual(docx["preview_frame_url"], "/materials/document-preview/8")
        self.assertEqual(docx["preview_media_url"], "")
        pptx = build_material_preview({"id": 9, "node_type": "file", "name": "a.pptx", "file_ext": "pptx", "preview_type": "presentation"})
        self.assertEqual(pptx["preview_mode"], "download")

    def test_text_targets_expose_content_endpoint_only_when_editable(self):
        locked = build_material_preview({"id": 3, "node_type": "file", "name": "a.md", "file_ext": "md", "preview_type": "markdown"})
        self.assertEqual(locked["preview_mode"], "markdown")
        self.assertEqual(locked["preview_url"], "/materials/view/3")
        self.assertEqual(locked["preview_content_url"], "")
        editable = build_material_preview({"id": 3, "node_type": "file", "name": "a.md", "file_ext": "md", "preview_type": "markdown", "editable": True})
        self.assertEqual(editable["preview_content_url"], "/api/materials/3/content")

    def test_html_without_render_target_falls_back_to_text(self):
        plain = build_material_preview({"id": 4, "node_type": "file", "name": "x.html", "file_ext": "html", "preview_type": "text"})
        self.assertEqual(plain["preview_mode"], "text")
        rendered = build_material_preview({"id": 4, "node_type": "file", "name": "x.html", "file_ext": "html", "preview_type": "text",
                                           "is_renderable": True, "render_url": "/materials/render/4/", "render_shell_url": "/materials/render-view/4"})
        self.assertEqual(rendered["preview_mode"], "frame")
        self.assertEqual(rendered["preview_url"], "/materials/render-view/4")
        self.assertEqual(rendered["preview_frame_url"], "/materials/render/4/")

    def test_folder_targets(self):
        readme = build_material_preview({"id": 5, "node_type": "folder", "document_readme_id": 51})
        self.assertEqual(readme["preview_mode"], "markdown")
        self.assertEqual(readme["preview_url"], "/materials/view/51")
        plain = build_material_preview({"id": 6, "node_type": "folder"})
        self.assertEqual(plain["preview_mode"], "folder")


def _library_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE teachers (id INTEGER PRIMARY KEY, is_active INTEGER DEFAULT 1, is_super_admin INTEGER DEFAULT 0,
            school_code TEXT, school_name TEXT, college TEXT, department TEXT);
        CREATE TABLE teacher_organization_memberships (id INTEGER PRIMARY KEY, teacher_id INTEGER, school_code TEXT,
            school_name TEXT, college TEXT, department TEXT, is_primary INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1, updated_at TEXT);
        CREATE TABLE classes (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE courses (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE academic_semesters (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE class_offerings (id INTEGER PRIMARY KEY, class_id INTEGER, course_id INTEGER, teacher_id INTEGER,
            semester_id INTEGER, semester TEXT);
        CREATE TABLE course_materials (id INTEGER PRIMARY KEY, teacher_id INTEGER, parent_id INTEGER, root_id INTEGER,
            name TEXT, material_path TEXT, node_type TEXT, preview_type TEXT DEFAULT '', file_ext TEXT DEFAULT '',
            file_size INTEGER DEFAULT 0, scope_level TEXT DEFAULT 'private', owner_role TEXT DEFAULT 'teacher', owner_user_pk INTEGER,
            school_code TEXT, school_name TEXT, college TEXT, department TEXT, created_at TEXT, updated_at TEXT);
        CREATE TABLE course_material_assignments (material_id INTEGER, class_offering_id INTEGER,
            assigned_by_teacher_id INTEGER, created_at TEXT);
        CREATE TABLE material_ai_import_records (id INTEGER PRIMARY KEY, teacher_id INTEGER, package_material_id INTEGER,
            source_material_id INTEGER, parsed_material_id INTEGER, parent_material_id INTEGER, document_type TEXT,
            parse_status TEXT DEFAULT 'completed');
        CREATE TABLE session_material_generation_tasks (id INTEGER PRIMARY KEY, generated_material_id INTEGER);
        """
    )
    conn.execute("INSERT INTO teachers (id) VALUES (1)")
    rows = [
        # repo "python-course": root touched long ago, a deep file updated recently
        (10, 1, None, 10, "python-course", "python-course", "folder", "folder", "", "2026-01-01", "2026-01-01"),
        (11, 1, 10, 10, "lesson_1", "python-course/lesson_1", "folder", "folder", "", "2026-01-02", "2026-01-02"),
        (12, 1, 11, 10, "lesson_1.html", "python-course/lesson_1/lesson_1.html", "file", "text", "html", "2026-10-20", "2026-10-20"),
        (13, 1, 10, 10, "readme.md", "python-course/readme.md", "file", "markdown", "md", "2026-01-03", "2026-01-03"),
        (14, 1, 10, 10, ".git", "python-course/.git", "folder", "folder", "", "2026-01-01", "2026-01-01"),
        (15, 1, 14, 10, "HEAD", "python-course/.git/HEAD", "file", "text", "", "2026-10-22", "2026-10-22"),
        # a standalone doc updated in between
        (20, 1, None, 20, "python-notes.md", "python-notes.md", "file", "markdown", "md", "2026-05-01", "2026-05-01"),
        # another repo with no match
        (30, 1, None, 30, "java-course", "java-course", "folder", "folder", "", "2026-09-01", "2026-09-01"),
        (31, 1, 30, 30, "intro.md", "java-course/intro.md", "file", "markdown", "md", "2026-09-02", "2026-09-02"),
    ]
    conn.executemany(
        "INSERT INTO course_materials (id, teacher_id, parent_id, root_id, name, material_path, node_type, preview_type, file_ext, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return conn


class LibraryRootCollapseTests(unittest.TestCase):
    def setUp(self):
        self.conn = _library_conn()
        self.addCleanup(self.conn.close)

    def test_keyword_search_collapses_nested_hits_to_roots(self):
        flat = _list_material_rows_for_parent(self.conn, 1, None, keyword="python", library_view="learning")
        self.assertEqual(sorted(int(row["id"]) for row in flat), [10, 11, 12, 13, 20])
        collapsed = _list_material_rows_for_parent(self.conn, 1, None, keyword="python", library_view="learning", collapse_to_roots=True)
        self.assertEqual(sorted(int(row["id"]) for row in collapsed), [10, 20])
        repo = next(row for row in collapsed if int(row["id"]) == 10)
        self.assertEqual(repo["search_match_count"], 4)
        self.assertEqual({sample["id"] for sample in repo["search_matches"]}, {11, 12, 13})
        single = next(row for row in collapsed if int(row["id"]) == 20)
        self.assertEqual(single["search_match_count"], 1)
        self.assertEqual(single["search_matches"], [])

    def test_collapse_only_applies_to_root_keyword_searches(self):
        inside = _list_material_rows_for_parent(
            self.conn, 1, dict(self.conn.execute("SELECT * FROM course_materials WHERE id=10").fetchone()),
            keyword="lesson", collapse_to_roots=True,
        )
        self.assertEqual(sorted(int(row["id"]) for row in inside), [11, 12])
        plain = _list_material_rows_for_parent(self.conn, 1, None, collapse_to_roots=True)
        self.assertEqual(sorted(int(row["id"]) for row in plain), [10, 20, 30])

    def test_root_activity_uses_subtree_and_ignores_git_internals(self):
        items = [dict(row) for row in _list_material_rows_for_parent(self.conn, 1, None, sort_by="updated_at", sort_order="desc")]
        items = _attach_root_activity(self.conn, items)
        by_id = {int(item["id"]): item for item in items}
        self.assertEqual(by_id[10]["latest_activity_at"], "2026-10-20")
        self.assertEqual(by_id[10]["subtree_file_count"], 2)
        self.assertEqual(by_id[10]["subtree_folder_count"], 1)
        self.assertEqual(by_id[30]["latest_activity_at"], "2026-09-02")
        self.assertEqual(by_id[20]["latest_activity_at"], "2026-05-01")
        ordered = _sort_items_by_activity(items, "updated_at", "desc")
        self.assertEqual([int(item["id"]) for item in ordered], [10, 30, 20])
        ordered = _sort_items_by_activity(items, "name", "asc")
        self.assertEqual([int(item["id"]) for item in ordered], [int(item["id"]) for item in items])


class GitProgressBoardTests(unittest.TestCase):
    def test_phases_advance_monotonically_and_finish(self):
        progress.start(901, "update")
        snap = progress.snapshot(901)
        self.assertEqual((snap["status"], snap["phase"]), ("running", "queued"))
        progress.advance(901, "pull", "origin/main")
        progress.advance(901, "export")  # earlier phase never regresses
        snap = progress.snapshot(901)
        self.assertEqual(snap["phase"], "pull")
        self.assertEqual(snap["message"], "origin/main")
        self.assertGreater(snap["percent"], 30)
        self.assertTrue(progress.is_running(901))
        progress.finish(901, "success", "仓库更新完成")
        snap = progress.snapshot(901)
        self.assertEqual((snap["status"], snap["phase"], snap["percent"]), ("success", "done", 100))
        self.assertFalse(progress.is_running(901))
        self.assertEqual(progress.snapshot(902)["status"], "idle")

    def test_failure_keeps_last_phase(self):
        progress.start(903, "update")
        progress.advance(903, "pull")
        progress.finish(903, "auth_required", "需要登录")
        snap = progress.snapshot(903)
        self.assertEqual(snap["status"], "auth_required")
        self.assertEqual(snap["phase"], "pull")
        self.assertEqual(snap["label"], "需要登录")


class BindingPlanTests(unittest.TestCase):
    def setUp(self):
        from test_session_learning_materials_service import _make_conn
        from classroom_app.db import schema_session_learning_materials as schema
        schema._SCHEMA_READY = False
        self.conn = _make_conn()
        self.addCleanup(self.conn.close)
        self.conn.execute("CREATE TABLE courses (id INTEGER PRIMARY KEY, name TEXT)")
        self.conn.execute("CREATE TABLE classes (id INTEGER PRIMARY KEY, name TEXT)")
        self.conn.execute("INSERT INTO courses VALUES (1, 'Python')")
        self.conn.execute("INSERT INTO classes VALUES (1, '软工2301')")
        self.conn.execute("ALTER TABLE class_offerings ADD COLUMN semester TEXT")
        for definition in ("session_date TEXT", "weekday INTEGER", "week_index INTEGER", "academic_section_text TEXT",
                           "academic_location TEXT", "slot_section_count INTEGER", "schedule_status TEXT DEFAULT 'scheduled'",
                           "schedule_metadata_json TEXT DEFAULT '{}'"):
            self.conn.execute("ALTER TABLE class_offering_sessions ADD COLUMN " + definition)
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-09-01',academic_section_text='1-2' WHERE id=10")
        for number in range(2, 4):
            self.conn.execute(
                "INSERT INTO class_offering_sessions(id,class_offering_id,order_index,title,session_date,academic_section_text) VALUES (?,5,?,?,?,'1-2')",
                (number * 10, number, f"L{number}", f"2026-09-{number * 7 - 6:02}"))
        self.conn.execute("INSERT INTO course_material_assignments(material_id,class_offering_id) VALUES (200,5)")
        for number in range(1, 5):
            self.conn.execute(
                "INSERT INTO course_materials(id,teacher_id,parent_id,root_id,name,material_path,node_type,preview_type,file_ext)"
                " VALUES (?,1,200,200,?,?,'file','text','html')",
                (300 + number, f"lesson_{number}.html", f"site/lesson_{number}/lesson_{number}.html"))
        self.root = dict(self.conn.execute("SELECT * FROM course_materials WHERE id=200").fetchone())

    def rows(self):
        return [dict(row) for row in self.conn.execute("SELECT * FROM course_materials WHERE root_id=200").fetchall()]

    def test_plan_is_read_only_and_matches_subsequent_sync(self):
        from classroom_app.services import git_learning_bindings_service as service
        before = self.conn.total_changes
        plan = service.plan_repository_learning_bindings(self.conn, self.root, self.rows(), 1)
        self.assertEqual(self.conn.total_changes, before)
        self.assertEqual([entry["lesson_order"] for entry in plan["entries"]], [0, 1, 2, 3, 4])  # site/index.html is the home page
        self.assertEqual(plan["target_classroom_count"], 1)
        offering = plan["offerings"][0]
        self.assertEqual((offering["course_name"], offering["class_name"]), ("Python", "软工2301"))
        statuses = {lesson["lesson_order"]: lesson["status"] for lesson in offering["lessons"]}
        self.assertEqual(statuses, {0: "will_bind", 1: "will_bind", 2: "will_bind", 3: "will_bind", 4: "missing_session"})
        self.assertEqual(offering["lessons"][1]["session"]["session_date"], "2026-09-01")

        result = service.sync_repository_learning_bindings(self.conn, self.root, self.rows(), 1)
        self.assertEqual((result["total_assignments"], result["total_home_assignments"]), (3, 1))
        self.assertEqual(result["offerings"][0]["course_name"], "Python")
        self.assertEqual(result["offerings"][0]["lesson_count"], 3)
        self.assertTrue(all(item["course_name"] == "Python" for item in result["assignments"]))
        self.assertEqual([item["code"] for item in result["warnings"]], ["missing_active_lesson"])

        after = service.plan_repository_learning_bindings(self.conn, self.root, self.rows(), 1)
        statuses = {lesson["lesson_order"]: lesson["status"] for lesson in after["offerings"][0]["lessons"]}
        self.assertEqual(statuses, {0: "bound", 1: "bound", 2: "bound", 3: "bound", 4: "missing_session"})

    def test_plan_reports_suppressed_rules_and_unassigned_repositories(self):
        from classroom_app.services import git_learning_bindings_service as service
        service.sync_repository_learning_bindings(self.conn, self.root, self.rows(), 1)
        self.conn.execute("UPDATE class_offering_git_learning_bindings SET suppressed=1, owns_binding=0 WHERE lesson_order=2")
        plan = service.plan_repository_learning_bindings(self.conn, self.root, self.rows(), 1)
        statuses = {lesson["lesson_order"]: lesson["status"] for lesson in plan["offerings"][0]["lessons"]}
        self.assertEqual(statuses[2], "suppressed")
        self.conn.execute("DELETE FROM course_material_assignments")
        plan = service.plan_repository_learning_bindings(self.conn, self.root, self.rows(), 1)
        self.assertEqual(plan["offerings"], [])
        self.assertIn("尚未分配课堂", plan["message"])


if __name__ == "__main__":
    unittest.main()


class GitCommandEnvironmentTests(unittest.TestCase):
    def test_git_is_pinned_to_the_exported_workspace(self):
        from pathlib import Path
        from classroom_app.services.materials_git_service import _git_command_env
        workspace = Path("C:/tmp/lanshare-repo-1/workspace") if __import__("os").name == "nt" else Path("/tmp/lanshare-repo-1/workspace")
        env = _git_command_env(workspace, {"PATH": "/usr/bin", "GIT_INDEX_FILE": "stale", "GIT_OBJECT_DIRECTORY": "stale"})
        self.assertEqual(env["GIT_DIR"], str(workspace / ".git"))
        self.assertEqual(env["GIT_WORK_TREE"], str(workspace))
        self.assertEqual(env["GIT_CEILING_DIRECTORIES"], str(workspace.parent))
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertNotIn("GIT_INDEX_FILE", env)
        self.assertNotIn("GIT_OBJECT_DIRECTORY", env)


class ImageFormatTests(unittest.TestCase):
    def test_extra_image_formats_are_registered_and_detected_by_extension(self):
        from classroom_app.services.materials_service import infer_material_profile, is_image_material
        for name in ("a.webp", "b.bmp", "c.avif", "d.ico", "e.tiff"):
            profile = infer_material_profile(name)
            self.assertEqual(profile["preview_type"], "image", name)
            self.assertTrue(profile["mime_type"].startswith("image/"), name)
        # Rows imported before the format was registered still count as images.
        legacy = {"preview_type": "binary", "file_ext": "webp", "name": "old.webp"}
        self.assertTrue(is_image_material(legacy))
        self.assertFalse(is_image_material({"preview_type": "binary", "file_ext": "bin", "name": "x.bin"}))
        preview = build_material_preview({"id": 12, "node_type": "file", "name": "old.webp", "file_ext": "webp", "preview_type": "binary"})
        self.assertEqual(preview["preview_mode"], "image")
        self.assertEqual(preview["preview_url"], "/materials/raw/12")
