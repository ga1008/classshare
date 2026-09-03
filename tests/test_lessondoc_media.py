import io
import tempfile
from pathlib import Path
from unittest import mock

from PIL import Image

from classroom_app.db import schema_lessondoc_editor
from classroom_app.services import file_service
from classroom_app.services.lessondoc import custom_elements, editor_service as editor, media, pack_service
from tests.test_lessondoc_service import _PackFixture, _manifest


class TestLessonDocMedia(_PackFixture):
    def setUp(self):
        super().setUp()
        schema_lessondoc_editor.reset_schema_ready_for_tests()
        self.addCleanup(schema_lessondoc_editor.reset_schema_ready_for_tests)
        schema_lessondoc_editor.ensure_lessondoc_editor_schema(self.conn)
        self.conn.execute("CREATE TABLE teachers(id INTEGER PRIMARY KEY)")
        self.conn.execute("INSERT INTO teachers VALUES(9)")
        self.directory = tempfile.TemporaryDirectory(prefix="lessondoc-media-")
        self.addCleanup(self.directory.cleanup)
        for patch in (mock.patch.object(file_service, "GLOBAL_FILES_DIR", Path(self.directory.name)),
                      mock.patch.object(file_service, "GLOBAL_FILES_LEGACY_DIRS", ())):
            patch.start()
            self.addCleanup(patch.stop)
        self.pack = self._create_pack()["pack"]
        self.target = pack_service.create_pack_skeleton(self.conn, teacher_id=9, course_id=42, manifest=_manifest(), theme="sky", pack_name="目标课程")["pack"]
        self.conn.commit()

    def upload(self, lesson_no=1):
        buffer = io.BytesIO()
        Image.new("RGB", (2, 3), "red").save(buffer, format="PNG")
        stored = media.verify_and_store(buffer, media.upload_profile("图片.png", "image/png"))
        result = media.attach_upload(self.conn, pack_id=self.pack["id"], teacher_id=9, lesson_no=lesson_no, stored=stored)
        self.conn.commit()
        return result

    def test_upload_deduplicates_and_home_and_lesson_paths_are_distinct(self):
        first, second = self.upload(1), self.upload(0)
        self.assertEqual(first["material_id"], second["material_id"])
        self.assertTrue(first["src"].startswith("../assets/media/"))
        self.assertTrue(second["src"].startswith("assets/media/"))
        self.assertEqual(len(media.list_media(self.conn, pack_id=self.pack["id"], teacher_id=9)["items"]), 1)
        with self.assertRaises(editor.EditorError):
            media.list_media(self.conn, pack_id=self.pack["id"], teacher_id=99)
        self.assertFalse(list(Path(self.directory.name).rglob(".upload-*")))

    def test_content_type_magic_and_image_decode_are_checked(self):
        with self.assertRaises(editor.EditorError):
            media.upload_profile("photo.png", "text/html")
        with self.assertRaises(editor.EditorError):
            media.verify_and_store(io.BytesIO(b"<script>alert(1)</script>"), media.upload_profile("bad.png", "image/png"))
        with self.assertRaises(editor.EditorError):
            media.verify_and_store(io.BytesIO(b"\x89PNG\r\n\x1a\nbroken"), media.upload_profile("bad.png", "image/png"))

    def test_svg_is_sanitized_before_content_addressed_storage(self):
        payload = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" onload="evil()"><script>evil()</script><rect width="10" height="10"/></svg>'
        stored = media.verify_and_store(io.BytesIO(payload), media.upload_profile("图.svg", "image/svg+xml"))
        text = Path(stored["path"]).read_text(encoding="utf-8")
        self.assertNotIn("onload", text)
        self.assertNotIn("<script", text)
        self.assertIn('xmlns="http://www.w3.org/2000/svg"', text)
        self.assertTrue(stored["warnings"])

    def test_template_survives_source_package_removal_and_instances_survive_template_deletion(self):
        uploaded = self.upload()
        template = custom_elements.save_element(self.conn, teacher_id=9, pack_id=self.pack["id"], lesson_no=1, name="图片组件",
                                                element={"id": "photo", "type": "media", "kind": "image", "src": uploaded["src"]})
        self.conn.commit()
        source_path = self.conn.execute("SELECT material_path FROM course_materials WHERE id=?", (self.pack["root_material_id"],)).fetchone()[0]
        self.conn.execute("DELETE FROM course_materials WHERE material_path=? OR material_path LIKE ?", (source_path, source_path + "/%"))
        pack_service.archive_pack_for_material(self.conn, self.pack["root_material_id"])
        self.conn.commit()
        inserted = custom_elements.insert_element(self.conn, teacher_id=9, element_id=template["id"], pack_id=self.target["id"], lesson_no=1)["element"]
        self.assertNotEqual(inserted["id"], "photo")
        row, _ = media.resolve_reference(self.conn, self.target, 1, inserted["src"])
        self.assertEqual(row["file_hash"], uploaded["file_hash"])
        custom_elements.delete_element(self.conn, teacher_id=9, element_id=template["id"])
        self.conn.commit()
        self.assertEqual(media.resolve_reference(self.conn, self.target, 1, inserted["src"])[0]["id"], row["id"])
        self.assertFalse(custom_elements.list_elements(self.conn, teacher_id=9)["items"])

    def test_cross_pack_copy_remaps_actions_and_html_resources_but_preserves_diagram_node_ids(self):
        uploaded = self.upload()
        frame = dict(x=0, y=0, w=400, h=200)
        group = dict(type="group", id="group", frame=dict(x=40, y=40, w=800, h=600), children=[
            dict(type="html", id="markup", frame=frame, body=f'<img src="{uploaded["src"]}">', css="img {width:100px}"),
            dict(type="button", id="btn", frame=frame, label="显示", actions=[dict(do="show", target="markup")]),
            dict(type="diagram", kind="flow", id="flow", frame=frame, nodes=[dict(id="A", label="输入"), dict(id="B", label="输出")], edges=[{"from": "A", "to": "B"}]),
        ])
        copied = custom_elements.copy_element(self.conn, teacher_id=9, source_pack_id=self.pack["id"], source_lesson_no=1,
                                               pack_id=self.target["id"], lesson_no=0, element=group)["element"]
        self.conn.commit()
        children = copied["children"]
        self.assertNotEqual(children[0]["id"], "markup")
        self.assertEqual(children[1]["actions"][0]["target"], children[0]["id"])
        self.assertIn('src="assets/media/', children[0]["body"])
        self.assertEqual(children[2]["nodes"][0]["id"], "A")
        self.assertEqual(children[2]["edges"][0]["from"], "A")
        with self.assertRaises(editor.EditorError):
            custom_elements.copy_element(self.conn, teacher_id=99, source_pack_id=self.pack["id"], source_lesson_no=1,
                                           pack_id=self.target["id"], lesson_no=0, element=group)

    def test_missing_resources_are_reported_before_copy_and_history_restore(self):
        element = dict(type="media", kind="image", src="../assets/media/missing.png")
        with self.assertRaises(editor.EditorError) as error:
            custom_elements.save_element(self.conn, teacher_id=9, pack_id=self.pack["id"], lesson_no=1, name="丢失", element=element)
        self.conn.rollback()
        self.assertEqual(error.exception.code, "MEDIA_MISSING")
        self.assertEqual(media.check_references(self.conn, self.pack, 1, element)[0]["code"], "MEDIA_MISSING")

    def test_multi_element_copy_keeps_cross_root_actions_and_local_diagram_namespace(self):
        frame = dict(x=0, y=0, w=400, h=200)
        elements = [
            dict(type="text", id="shared", md="目标", frame=frame),
            dict(type="button", id="btn", label="显示", frame=frame, actions=[dict(do="show", target="shared")]),
            dict(type="diagram", id="diagram", kind="flow", frame=frame,
                 nodes=[dict(id="shared", label="图示节点"), dict(id="b", label="完成")], edges=[{"from": "shared", "to": "b"}]),
        ]
        copied = custom_elements.copy_elements(self.conn, teacher_id=9, source_pack_id=self.pack["id"], source_lesson_no=1,
                                               pack_id=self.target["id"], lesson_no=1, elements=elements)["elements"]
        self.assertEqual(len(copied), 3)
        self.assertNotEqual(copied[0]["id"], "shared")
        self.assertEqual(copied[1]["actions"][0]["target"], copied[0]["id"])
        self.assertEqual(copied[2]["nodes"][0]["id"], "shared")
        self.assertEqual(copied[2]["edges"][0]["from"], "shared")

    def test_teacher_scoped_template_list_and_mutations(self):
        template = custom_elements.save_element(self.conn, teacher_id=9, pack_id=self.pack["id"], lesson_no=1, name="提示", element=dict(type="text", md="提示"))
        self.conn.commit()
        self.assertEqual(custom_elements.list_elements(self.conn, teacher_id=99)["items"], [])
        for function, extra in ((custom_elements.rename_element, {"name": "越权"}), (custom_elements.delete_element, {})):
            with self.assertRaises(editor.EditorError):
                function(self.conn, teacher_id=99, element_id=template["id"], **extra)
