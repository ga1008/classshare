"""Real material routes after repairing a legacy manual folder, on owned data only."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app.dependencies import get_current_teacher, get_current_user
from classroom_app.routers.materials_parts import library, node_ops
from classroom_app.services.wanderer_manual_library import ensure_manual_materials, manual_documents
from tests.wanderer_test_support import WandererDbTestCase


class ManualRoutesTest(WandererDbTestCase):
    seed_on_setup = False

    def setUp(self):
        super().setUp()
        with self.connect() as conn:
            self.teacher_id = conn.execute("SELECT id FROM teachers ORDER BY id LIMIT 1").fetchone()[0]
            seeded = ensure_manual_materials(conn)
            self.folder_id = seeded["folder_id"]
            conn.execute("UPDATE course_materials SET root_id = NULL WHERE id = ?", (self.folder_id,))
            conn.commit()
            ensure_manual_materials(conn)
            conn.commit()
            self.files = {row["name"]: dict(row) for row in conn.execute(
                "SELECT * FROM course_materials WHERE parent_id = ?", (self.folder_id,),
            ).fetchall()}

        actor = {"id": self.teacher_id, "role": "teacher", "name": "合成教师"}

        async def teacher():
            return actor

        app = FastAPI()
        app.include_router(node_ops.router)
        app.include_router(library.router)
        app.dependency_overrides[get_current_user] = teacher
        app.dependency_overrides[get_current_teacher] = teacher
        self.client = self.enterContext(TestClient(app, raise_server_exceptions=False))

    def test_repaired_folder_detail_shows_both_manuals(self):
        response = self.client.get(f"/api/materials/{self.folder_id}")
        self.assertEqual(response.status_code, 200, response.text)
        material = response.json()["material"]
        self.assertEqual(material["id"], self.folder_id)
        self.assertEqual(material["root_id"], self.folder_id)
        self.assertEqual(material["child_count"], 3)

    def test_repaired_folder_tree_shows_both_manuals(self):
        response = self.client.get(f"/api/materials/{self.folder_id}/tree")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["tree"]["id"], self.folder_id)
        self.assertEqual(payload["stats"]["file_count"], 3)
        self.assertEqual({child["name"] for child in payload["tree"]["children"]}, set(self.files))
        for child in payload["tree"]["children"]:
            self.assertEqual(child["root_id"], self.folder_id)
            self.assertEqual(child["preview_url"], f"/materials/view/{child['id']}")

    def test_repaired_manual_content_endpoints_return_the_full_source(self):
        for document in manual_documents():
            with self.subTest(title=document["title"]):
                row = self.files[f"{document['title']}.md"]
                response = self.client.get(f"/api/materials/{row['id']}/content")
                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                self.assertEqual(payload["content"], document["content"])
                self.assertEqual(payload["material"]["source_revision"], row["file_hash"])
                self.assertEqual(payload["encoding"], "utf-8")


if __name__ == "__main__":
    import unittest

    unittest.main()
