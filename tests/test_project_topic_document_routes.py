"""Exercise Office/PDF delivery through the normal classroom HTTP resource endpoints."""

from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import quote

import fitz
from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app.database import get_db_connection
from classroom_app.dependencies import get_current_user
from classroom_app.routers import project_topics
from classroom_app.services import file_service, libreoffice_service
from classroom_app.services.document_render_service import DocumentRenderService
from tests.sqlite_database_fixture import isolated_sqlite_database


class ProjectTopicDocumentRouteTests(unittest.TestCase):
    def test_group_member_gets_pdf_previews_and_byte_identical_originals(self):
        if not libreoffice_service.soffice_is_runnable():
            self.skipTest("Headless LibreOffice is unavailable")
        self.enterContext(isolated_sqlite_database())
        root = Path(self.enterContext(TemporaryDirectory(prefix="topic-office-http-")))
        self.enterContext(patch.object(file_service, "GLOBAL_FILES_DIR", root / "blobs"))
        self.enterContext(patch.object(file_service, "GLOBAL_FILES_LEGACY_DIRS", ()))
        self.enterContext(patch.object(libreoffice_service, "DATA_ROOT", root))
        self.enterContext(patch.object(project_topics, "document_render_service", DocumentRenderService(root / "render")))
        with get_db_connection() as conn:
            conn.execute("INSERT INTO teachers(id,email,name,hashed_password,is_active) VALUES(741,'file@example.invalid','QA','x',1)")
            conn.execute("INSERT INTO courses(id,name,created_by_teacher_id) VALUES(741,'Files',741)")
            conn.execute("INSERT INTO classes(id,name,created_by_teacher_id) VALUES(741,'File QA',741)")
            conn.execute("INSERT INTO class_offerings(id,course_id,class_id,teacher_id) VALUES(741,741,741,741)")
            conn.executemany("INSERT INTO students(id,name,student_id_number,class_id) VALUES(?,?,?,741)",
                             [(741,"A","001"), (742,"B","002")])
            conn.commit()
        word = Document()
        word.add_paragraph("Topic Word delivery verification")
        with BytesIO() as stream:
            word.save(stream)
            docx = stream.getvalue()
        with fitz.open() as document:
            document.new_page().insert_text((72, 96), "Topic PDF delivery verification")
            pdf = document.tobytes()
        word_path, pdf_path = "课题/任务书.docx", "课题/样例.pdf"
        manifest = {"formatVersion": 1, "packageKey":"files", "version":"1",
                    "topics":[{"key":"docs", "title":"课程文档", "entry":word_path, "attachments":[pdf_path]}]}
        app = FastAPI()
        app.include_router(project_topics.router)
        identity = {"id": 741, "role": "teacher"}
        app.dependency_overrides[get_current_user] = lambda: identity
        client = self.enterContext(TestClient(app))
        base = "/api/classrooms/741/project-topics"
        content = [("project-topics.json", json.dumps(manifest).encode()), (word_path, docx), (pdf_path,pdf)]
        imported = client.post(base + "/packages/import", data={"paths":json.dumps([p for p,b in content])},
                               files=[("files",(p.split("/")[-1],data)) for p,data in content])
        self.assertEqual(imported.status_code, 200, imported.text)
        package = imported.json()["package"]
        config = {"package_version_id":package["id"], "title":"开题",
                  "new_scheme":{"name":"文件验收", "allocation_mode":"all", "group_count":1,
                                "min_members":2, "max_members":2}}
        check = client.post(base + "/preview", json=config)
        self.assertEqual(check.status_code, 200, check.text)
        preview = check.json()["preview"]
        self.assertTrue(preview["ready"], preview)
        created = client.post(base + "/activities", json={**config, "preview_token":preview["preview_token"],
                                                         "request_id":"file-http-1"})
        self.assertEqual(created.status_code, 200, created.text)
        aid = created.json()["activity"]["id"]
        identity.update(role="student", id=742)
        own = client.get(f"{base}/activities/{aid}")
        self.assertEqual(own.status_code, 200, own.text)
        topic = own.json()["activity"]["my_topic"]
        self.assertEqual((topic["entry"],topic["attachments"]), (word_path,[pdf_path]))

        for path, original, text in [(word_path,docx,"Topic Word delivery verification"),
                                     (pdf_path,pdf,"Topic PDF delivery verification")]:
            with self.subTest(path=path):
                url = f"{base}/activities/{aid}/topics/{topic['key']}/files/{quote(path)}"
                preview = client.get(url)
                self.assertEqual(preview.status_code, 200)
                self.assertEqual(preview.headers["content-type"], "application/pdf")
                self.assertTrue(preview.headers["content-disposition"].startswith("inline;"))
                self.assertIn("no-store", preview.headers["cache-control"])
                with fitz.open(stream=preview.content, filetype="pdf") as rendered:
                    self.assertEqual(rendered.page_count, 1)
                    self.assertIn(text, rendered[0].get_text())
                if path == pdf_path:
                    self.assertEqual(preview.content, original)
                downloaded = client.get(url, params={"download": 1})
                self.assertEqual(downloaded.status_code, 200)
                self.assertTrue(downloaded.headers["content-disposition"].startswith("attachment;"))
                self.assertEqual(downloaded.content, original)
