import asyncio
import io
import unittest
from urllib.parse import unquote

import pandas as pd

from classroom_app.routers.homework_parts.exports import build_grades_export_response


async def _collect(response) -> bytes:
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
    return b"".join(chunks)


class GradeExportResponseTests(unittest.TestCase):
    def test_title_with_path_separators_is_sanitized(self):
        frame = pd.DataFrame([{"学号": "2024001", "姓名": "小明", "分数": 90}])
        response = build_grades_export_response(frame, class_label="软件1班", assignment_title="../../etc/作业/第1次")
        disposition = unquote(response.headers["content-disposition"])
        self.assertTrue(disposition.startswith("attachment"))
        filename = disposition.split("''", 1)[1]
        self.assertNotIn("/", filename)
        self.assertNotIn("..", filename.replace(".xlsx", ""))
        self.assertTrue(filename.endswith(".xlsx"))

    def test_workbook_is_streamed_from_memory(self):
        frame = pd.DataFrame([{"学号": "2024001", "姓名": "小明", "分数": 90}])
        response = build_grades_export_response(frame, class_label="软件1班", assignment_title="第1次作业")
        content = asyncio.run(_collect(response))
        restored = pd.read_excel(io.BytesIO(content))
        self.assertEqual(restored.iloc[0]["姓名"], "小明")


if __name__ == "__main__":
    unittest.main()
