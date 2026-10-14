"""Attachment zip export is written to disk, safe to extract and cleaned up (audit B2)."""
from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from classroom_app.routers.homework_parts import exports


class AttachmentZipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.tmp / "shot.png").write_bytes(b"\x89PNG" + b"x" * 2000)
        (self.tmp / "report.txt").write_text("hello " * 400, encoding="utf-8")

    def _rows(self):
        base = {"student_pk_id": 1, "student_name": "小明", "student_id_number": "S1", "student_class_name": "软件1班"}
        return [
            {**base, "stored_path": str(self.tmp / "shot.png"), "relative_path": "../../shot.png", "original_filename": "shot.png"},
            {**base, "stored_path": str(self.tmp / "report.txt"), "relative_path": "docs/report.txt", "original_filename": "report.txt"},
            {**base, "student_pk_id": 2, "student_name": "小明", "stored_path": str(self.tmp / "report.txt"),
             "relative_path": "report.txt", "original_filename": "report.txt"},
        ]

    def test_zip_paths_are_contained_and_images_are_stored(self):
        target = str(self.tmp / "out.zip")
        with patch.object(exports, "resolve_submission_file_path", side_effect=lambda value: value):
            exports._write_attachment_zip(target, self._rows(), root_folder="软件1班-作业", multi_class=False)
        with zipfile.ZipFile(target) as zf:
            infos = {info.filename: info for info in zf.infolist()}
        self.assertEqual(sorted(infos), [
            "软件1班-作业/小明-S1/docs/report.txt",
            "软件1班-作业/小明-S1/shot.png",
            "软件1班-作业/小明-S1_2/report.txt",
        ])
        self.assertTrue(all(".." not in name for name in infos))
        self.assertEqual(infos["软件1班-作业/小明-S1/shot.png"].compress_type, zipfile.ZIP_STORED)
        self.assertEqual(infos["软件1班-作业/小明-S1/docs/report.txt"].compress_type, zipfile.ZIP_DEFLATED)

    def test_remove_quietly_tolerates_missing_files(self):
        path = self.tmp / "gone.zip"
        path.write_bytes(b"x")
        exports._remove_quietly(str(path))
        exports._remove_quietly(str(path))
        self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
