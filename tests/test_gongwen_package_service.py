"""公文打包下载 (gongwen package) — 文件名清洗、zip 组装、可见性与空包保护。"""

import contextlib
import os
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

# 必须在导入 schema 模块前固定引擎，否则宿主机的 DB_ENGINE=postgres 会让
# DDL 走 postgres 方言（GENERATED ... AS IDENTITY）而在 sqlite 上报错。
os.environ["DB_ENGINE"] = "sqlite"
from unittest.mock import AsyncMock, patch

import classroom_app.db.schema_gongwen as schema_gongwen
import classroom_app.services.gongwen_package_service as pkg
from classroom_app.db.schema_gongwen import ensure_gongwen_schema
from classroom_app.services.gongwen_package_service import (
    build_gongwen_package,
    document_base_name,
    readable_file_name,
    safe_download_name,
)

TEACHER_SCOPE = {"school_code": "sch", "school_name": "测试校区", "college": "", "department": ""}


def _run(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class SafeNameTests(unittest.TestCase):
    def test_strips_illegal_characters_and_collapses_spaces(self):
        # Arrange / Act
        name = safe_download_name('关于<开展>2026年:职称/认定 "重新确认"工作的通知?')

        # Assert
        self.assertNotRegex(name, r'[<>:"/\\|?*]')
        self.assertIn("职称", name)
        self.assertNotIn("  ", name)

    def test_caps_length_and_falls_back_when_empty(self):
        self.assertLessEqual(len(safe_download_name("长" * 500)), 120)
        self.assertEqual(safe_download_name("///???"), "公文")

    def test_readable_file_name_uses_sn_title_and_original_ext(self):
        document = {"id": 7, "sn": "职改〔2026〕4号", "title": "关于职称认定的通知"}
        self.assertEqual(
            readable_file_name(document, "primary", "3aa58a8f72bf.pdf"),
            "职改〔2026〕4号 关于职称认定的通知（正文）.pdf",
        )
        self.assertEqual(
            readable_file_name(document, "attachment", "4e6e6fc2.rar"),
            "职改〔2026〕4号 关于职称认定的通知（附件）.rar",
        )

    def test_document_base_name_falls_back_to_id(self):
        self.assertEqual(document_base_name({"id": 12, "sn": "", "title": ""}), "公文12")


class PackageBuildTestBase(unittest.TestCase):
    def setUp(self):
        schema_gongwen._SCHEMA_READY = False
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        ensure_gongwen_schema(self.conn)

        @contextlib.contextmanager
        def _shared_conn():
            yield self.conn

        self._conn_patcher = patch.object(pkg, "get_db_connection", _shared_conn)
        self._conn_patcher.start()
        self.addCleanup(self._conn_patcher.stop)
        self.addCleanup(self.conn.close)

        self.workdir = Path(tempfile.mkdtemp(prefix="gwpkg_test_"))
        import shutil

        self.addCleanup(lambda: shutil.rmtree(self.workdir, ignore_errors=True))

    def _insert_document(self, **overrides) -> int:
        values = {
            "remote_id": "r100",
            "attr_school_code": "sch",
            "attr_level": "school",
            "openness": "school",
            "sn": "职改〔2026〕4号",
            "title": "关于开展2026年职称认定工作的通知",
            "author": "职称改革办公室",
            "file_url": "https://doc.gxufl.com/upload/3aa58a8f.pdf",
            "attachment_url": "",
            "content_html": "<p>正文内容</p>",
            "parsed_status": "done",
        }
        values.update(overrides)
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        cursor = self.conn.execute(
            f"INSERT INTO gongwen_documents ({columns}) VALUES ({marks})", tuple(values.values())
        )
        return int(cursor.lastrowid)


class PackageBuildTests(PackageBuildTestBase):
    def test_packages_primary_and_extracted_attachments_with_readable_names(self):
        # Arrange：本地正文 pdf + 已解压附件目录（真实中文名）。
        doc_id = self._insert_document(attachment_url="https://doc.gxufl.com/upload/4e6e.rar")
        primary = self.workdir / "3aa58a8f.pdf"
        primary.write_bytes(b"%PDF-1.4 test")
        extracted_root = self.workdir / "extracted" / "r100"
        attach_dir = extracted_root / "attachment"
        attach_dir.mkdir(parents=True)
        (attach_dir / "附件10：论文成果统计表.docx").write_bytes(b"docx-bytes")

        ensure_mock = AsyncMock(return_value={"status": "local", "local_path": str(primary)})
        with patch.object(pkg, "ensure_local_attachment", ensure_mock), patch.object(
            pkg, "extracted_root_for", lambda school, remote: extracted_root
        ):
            # Act
            package = _run(build_gongwen_package(TEACHER_SCOPE, doc_id))

        # Assert
        self.addCleanup(lambda: Path(package["zip_path"]).unlink(missing_ok=True))
        self.assertEqual(package["download_name"], "职改〔2026〕4号 关于开展2026年职称认定工作的通知.zip")
        with zipfile.ZipFile(package["zip_path"]) as zf:
            names = set(zf.namelist())
        self.assertIn("职改〔2026〕4号 关于开展2026年职称认定工作的通知（正文）.pdf", names)
        self.assertIn("附件/附件10：论文成果统计表.docx", names)
        self.assertIn("公文信息.txt", names)
        self.assertIn("公文正文.html", names)
        # 附件已有解压文件时不再回源下载原压缩包（只为正文调用了一次）。
        self.assertEqual(ensure_mock.await_count, 1)

    def test_raises_value_error_when_nothing_downloadable(self):
        doc_id = self._insert_document(file_url="", content_html="")
        with patch.object(pkg, "extracted_root_for", lambda school, remote: self.workdir / "none"):
            with self.assertRaises(ValueError):
                _run(build_gongwen_package(TEACHER_SCOPE, doc_id))

    def test_raises_lookup_error_for_invisible_document(self):
        doc_id = self._insert_document()
        other_scope = {"school_code": "other", "school_name": "另一校区", "college": "", "department": ""}
        with self.assertRaises(LookupError):
            _run(build_gongwen_package(other_scope, doc_id))

    def test_raises_lookup_error_for_missing_document(self):
        with self.assertRaises(LookupError):
            _run(build_gongwen_package(TEACHER_SCOPE, 99999))


if __name__ == "__main__":
    unittest.main()
