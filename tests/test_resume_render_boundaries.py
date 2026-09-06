from __future__ import annotations

import multiprocessing
import os
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from classroom_app.services.resume import resume_render_service as render


def _export_process(directory, messages, release, cached=False):
    def convert(html, fmt):
        if cached:
            raise AssertionError("A cached version must not convert again")
        messages.put("entered_conversion")
        if not release.wait(10):
            raise TimeoutError("Test export was not released")
        return b"%PDF-synthetic-cache-check"
    with patch.dict(os.environ, {"RESUME_EXPORT_CACHE_DIR": directory}), patch.object(render, "export_resume_bytes", side_effect=convert):
        try:
            data = render.export_resume_cached("immutable version", "pdf")
            messages.put(("success", data))
        except render.ResumeExportBusy:
            messages.put("busy")


class ResumeRenderBoundaryTests(unittest.TestCase):
    def test_legacy_flat_skills_and_malformed_groups_are_safe(self):
        groups = render._capability_groups(["资料分析", None, 3, {"group": "语言", "items": ["英语", {"unexpected": True}]}])
        self.assertEqual([{"group": "相关技能", "items": ["资料分析"]}, {"group": "语言", "items": ["英语"]}], groups)
        self.assertEqual([], render._capability_groups("corrupt history"))

    def test_sidebar_keeps_certificate_date_and_description(self):
        block = {"type": "skill_cert", "title": "技能与证书", "skills": ["英语"],
                 "certs": [{"name": "测试证书", "period": "2025-06", "desc": "核验说明"}]}
        output = render._render_block_sidebar(block, {"accent": "#0d9488", "soft": "#d9f3ee"})
        for fact in ("英语", "测试证书", "2025-06", "核验说明"):
            self.assertIn(fact, output)

    def test_conversion_limit_and_cache_are_shared_across_processes(self):
        context = multiprocessing.get_context("spawn")
        messages, release = context.Queue(), context.Event()
        processes = []
        with tempfile.TemporaryDirectory(prefix="career-export-process-") as directory:
            try:
                first = context.Process(target=_export_process, args=(directory, messages, release))
                processes.append(first)
                first.start()
                self.assertEqual("entered_conversion", messages.get(timeout=10))
                second = context.Process(target=_export_process, args=(directory, messages, release))
                processes.append(second)
                second.start()
                self.assertEqual("busy", messages.get(timeout=10))
                second.join(10)
                self.assertEqual(0, second.exitcode)
                release.set()
                self.assertEqual(("success", b"%PDF-synthetic-cache-check"), messages.get(timeout=10))
                first.join(10)
                self.assertEqual(0, first.exitcode)
                third = context.Process(target=_export_process, args=(directory, messages, release, True))
                processes.append(third)
                third.start()
                self.assertEqual(("success", b"%PDF-synthetic-cache-check"), messages.get(timeout=10))
                third.join(10)
                self.assertEqual(0, third.exitcode)
            finally:
                release.set()
                for process in processes:
                    if process.is_alive():
                        process.terminate()
                    process.join(10)
                messages.close()

    def test_cache_sweep_is_bounded_and_preserves_current_and_unknown_files(self):
        with tempfile.TemporaryDirectory(prefix="career-export-sweep-") as directory, patch.dict(os.environ, {"RESUME_EXPORT_CACHE_DIR": directory}):
            root = Path(directory)
            old = root / (date.today() - timedelta(days=10)).isoformat()
            current = root / date.today().isoformat()
            old.mkdir()
            current.mkdir()
            for index in range(9):
                path = old / (f"{index:064x}" + ".pdf")
                path.write_bytes(b"old derivative")
                os.utime(path, (time.time() - 10 * 86400,) * 2)
            (old / "unrelated.txt").write_text("keep")
            for index in range(50):
                (current / (f"{index:064x}" + ".pdf")).write_bytes(b"current derivative")
            self.assertEqual(3, render.cleanup_export_cache(limit=3))
            self.assertEqual(6, len(list(old.glob("*.pdf"))))
            self.assertEqual(50, len(list(current.glob("*.pdf"))))
            self.assertTrue((old / "unrelated.txt").is_file())


if __name__ == "__main__":
    unittest.main()
