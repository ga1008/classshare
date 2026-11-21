import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
import zipfile

from fastapi import HTTPException

from classroom_app.db.schema_project_topic_packages import ensure_project_topic_packages_schema
from classroom_app.services import file_service
from classroom_app.services import project_topic_package_service as packages


class ProjectTopicPackageTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        self.root = root
        self.enterContext(patch.object(file_service, "GLOBAL_FILES_DIR", root))
        self.enterContext(patch.object(file_service, "GLOBAL_FILES_LEGACY_DIRS", ()))
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        ensure_project_topic_packages_schema(self.conn)
        self.manifest = {
            "formatVersion": 1, "packageKey": "lab", "version": "1", "title": "课程演练",
            "common": ["说明.txt"],
            "topics": [
                {"key": "red", "number": "01", "title": "第一题", "entry": "甲/index.html",
                 "attachments": ["甲/data.csv"]},
                {"key": "blue", "number": "B", "title": "第二题", "entry": "乙/brief.md", "pool": "reserve"},
                {"key": "brief", "title": "开题方向", "brief": "从原始资料汇总课程数据。"}
            ]
        }
        self.content = {
            "说明.txt": b"rules",
            "甲/index.html": '<html><head><link rel="stylesheet" href="../style/base.css"></head>'
                              '<body><p>需要提交的成果</p><a href="../乙/brief.md">下一题</a></body></html>'.encode(),
            "甲/data.csv": b"day,value", "乙/brief.md": b"private blue",
            "style/base.css": b'body{background:url(../img/bg.png)}', "img/bg.png": b"image",
            "未分配.txt": b"private", "旧信息.txt": b"private",
        }

    def import_version(self, manifest=None, *, teacher_id=9, archive=False):
        source = dict(self.content)
        if manifest is not None:
            source["project-topics.json"] = json.dumps(manifest, ensure_ascii=False).encode()
        entries = [(f"项目材料/{p}", data) for p, data in source.items()]
        if archive:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as zipped:
                for path, data in entries:
                    zipped.writestr(path, data)
            entries = [("导入.zip", buffer.getvalue())]
        return packages.import_package(self.conn, teacher_id, entries, title="课程演练")

    def scoped(self, version, path, key=None):
        return packages.get_scoped_resource(self.conn, version["id"], path, topic_key=key)[0].read_bytes()

    def test_zip_matches_folder_and_retry_preserves_version(self):
        version = self.import_version(self.manifest)
        self.conn.commit()
        same = self.import_version(self.manifest, archive=True)
        self.assertEqual(same["id"], version["id"])
        self.assertEqual(len(packages.list_teacher_packages(self.conn, 9)), 1)
        self.assertEqual([t["readiness"] for t in version["topics"]], ["complete", "complete", "brief"])
        self.assertEqual(version["topics"][1]["pool"], "reserve")
        self.assertTrue(version["mapping_confirmed"])
        file_hash = self.conn.execute("SELECT file_hash FROM project_topic_files WHERE path='说明.txt'").fetchone()[0]
        self.assertEqual(file_service.count_global_file_references(self.conn, file_hash), 1)
        self.assertEqual(self.scoped(version, "说明.txt"), b"rules")

    def test_topic_resources_include_referenced_images_without_cross_navigation(self):
        version = self.import_version(self.manifest)
        for path in ("甲/index.html", "甲/data.csv", "style/base.css", "img/bg.png"):
            self.assertEqual(self.scoped(version, path, "red"), self.content[path])
        for path, topic in [("乙/brief.md", "red"), ("甲/data.csv", None), ("project-topics.json", "red"),
                            ("未分配.txt", "red"), ("img/bg.png", "blue")]:
            with self.subTest(path=path, topic=topic), self.assertRaises(HTTPException):
                self.scoped(version, path, topic)
        scoped = packages.get_scoped_topic(self.conn, version["id"], "red")
        self.assertNotIn("第二题", str(scoped))
        self.assertNotIn("乙/brief.md", str(scoped))
        self.assertIn("仅教师可见", str(version["issues"]))
        self.assertEqual(packages.get_scoped_topic(self.conn, version["id"], None)["topic"], None)
        self.assertEqual(packages.list_teacher_packages(self.conn, 10), [])
        with self.assertRaises(HTTPException):
            packages.get_teacher_package(self.conn, version["id"], 10)

    def test_brief_draft_folder_missing_catalogue_links_empty_pages_and_confirmation(self):
        self.content = {"group1/group1.html": b"<p>Full task</p>", "main.html": b"".join(
            f'<a href="group{i}/group{i}.html">{i}</a>'.encode() for i in range(1, 15)) + b'<a href="materials.html">data</a>'}
        missing = self.import_version()
        self.assertFalse(missing["mapping_confirmed"])
        self.assertEqual(sum("缺失文件" in x for x in missing["issues"]), 14)
        self.assertEqual(len(missing["manifest"]["topics"]), 1)
        self.content.update({f"group{i}/group{i}.html": b"" for i in range(2, 15)})
        draft = self.import_version()
        self.assertEqual(sum("空文件" in x for x in draft["issues"]), 13)
        self.assertEqual(sum(t["readiness"] == "missing" for t in draft["topics"]), 13)
        saved = packages.remap_package(self.conn, draft["id"], 9, draft["manifest"])
        self.assertTrue(saved["mapping_confirmed"])
        self.assertNotEqual(draft["id"], saved["id"])
        self.assertEqual(packages.remap_package(self.conn, saved["id"], 9, saved["manifest"])["id"], saved["id"])
        self.assertEqual(packages.get_teacher_package(self.conn, draft["id"], 9), draft)

    def test_revision_pins_all_bytes_and_keeps_courses_separate(self):
        old = self.import_version(self.manifest)
        self.content["甲/data.csv"] = b"replacement"
        new = self.import_version(self.manifest)
        self.assertNotEqual(old["id"], new["id"])
        self.assertEqual(self.scoped(old, "甲/data.csv", "red"), b"day,value")
        self.assertEqual(self.scoped(new, "甲/data.csv", "red"), b"replacement")
        other_owner = self.import_version(self.manifest, teacher_id=10)
        self.assertNotEqual(new["id"], other_owner["id"])
        with self.assertRaises(HTTPException):
            packages.remap_package(self.conn, new["id"], 10, self.manifest)

    def test_same_name_and_number_do_not_link_unrelated_legacy_packages(self):
        initial = self.import_version()
        self.content["乙/brief.md"] = b"a different project with the same number"
        changed = self.import_version()
        self.assertNotEqual(initial["package_key"], changed["package_key"])
        self.assertEqual(self.import_version()["package_key"], changed["package_key"])

    def test_common_catalogue_conflict_and_inferred_other_topic_attachment(self):
        self.manifest["common"] = ["all.html"]
        self.content["all.html"] = '<a href="乙/brief.md">other project</a>'.encode()
        self.content["甲/index.html"] = '<img src="../乙/chart.png">'.encode()
        self.content["乙/chart.png"] = b"other chart"
        details = self.import_version(self.manifest)
        self.assertTrue(details["common_issues"])
        self.assertEqual(details["topics"][0]["readiness"], "missing")
        with self.assertRaises(HTTPException):
            self.scoped(details, "乙/chart.png", "red")

    def test_malformed_inputs_do_not_add_versions(self):
        invalid = [("bad.zip", b"bad data")]
        with self.assertRaises(HTTPException):
            packages.import_package(self.conn, 9, invalid)
        for entries in [[("../x", b"no")], [("A.pdf", b"1"), ("a.pdf", b"2")],
                        [("project-topics.json", b"{")]]:
            with self.subTest(files=entries), self.assertRaises(HTTPException):
                packages.import_package(self.conn, 9, entries)
        self.manifest["topics"][0]["key"] = "blue"
        with self.assertRaises(HTTPException):
            self.import_version(self.manifest)
        self.assertEqual(packages.list_teacher_packages(self.conn, 9), [])

    def test_already_published_copies_include_only_this_class_visible_private_bytes(self):
        version = self.import_version(self.manifest)
        self.conn.execute("CREATE TABLE course_material_assignments(material_id INTEGER)")
        self.conn.execute("CREATE TABLE course_materials(root_id INTEGER,material_path TEXT,file_hash TEXT)")
        for package_path, material_path in [("乙/brief.md", "资料/课上/项目.md"),
                                             ("甲/data.csv", "资料/备课/内部.csv"),
                                             ("说明.txt", "资料/课上/要求.txt")]:
            self.conn.execute("""INSERT INTO course_materials(root_id,material_path,file_hash)
                               SELECT 100,?,file_hash FROM project_topic_files WHERE package_id=? AND path=?""",
                              (material_path, version["id"], package_path))
        with patch("classroom_app.services.material_publication_service.get_classroom_publication_context",
                   return_value=([{"root_id": 100, "material_path": "资料/课上"}], {})):
            copies = packages.get_existing_classroom_copies(self.conn, version["id"], 42)
        self.assertEqual(copies, [{"path": "乙/brief.md", "material_path": "资料/课上/项目.md"}])

    def test_parallel_retries_publish_one_complete_package(self):
        database = self.root / "concurrent.sqlite"
        setup = sqlite3.connect(database)
        try:
            ensure_project_topic_packages_schema(setup)
            setup.commit()
        finally:
            setup.close()
        barrier = threading.Barrier(2)
        files = [*self.content.items(), ("project-topics.json", json.dumps(self.manifest).encode())]

        def upload():
            conn = sqlite3.connect(database)
            conn.row_factory = sqlite3.Row
            try:
                barrier.wait(timeout=5)
                result = packages.import_package(conn, 9, files)
                conn.commit()
                return result
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(upload) for _ in range(2)]
            a, b = [future.result() for future in futures]
        self.assertEqual(a, b)
        self.assertEqual(len(a["files"]), len(files))

    def test_original_material_zip_still_wraps_loose_entries_in_archive_folder(self):
        from classroom_app.services.material_zip_service import extract_zip_upload_entries
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as writer:
            writer.writestr("首页.html", b"<h1>One</h1>")
            writer.writestr("lesson_1/lesson_1.html", b"<h1>Two</h1>")
        rows = extract_zip_upload_entries("课程.zip", archive.getvalue())
        self.assertEqual([r["relative_path"] for r in rows], ["课程/首页.html", "课程/lesson_1/lesson_1.html"])
