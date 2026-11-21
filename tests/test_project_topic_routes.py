from io import BytesIO
import json
import unittest
from unittest.mock import patch

from openpyxl import load_workbook

from classroom_app.routers import project_topics
from tests.test_architecture_route_snapshot import ROUTE_SNAPSHOT_PATH, _current_route_snapshot


class ProjectTopicRouteContractTests(unittest.TestCase):
    def test_registered_grouping_and_dispatch_endpoints_match_snapshot(self):
        def related(routes):
            return [r for r in routes if "/project-topics/" in r["path"] or r["path"].startswith("/api/collaboration/")]

        expected = related(json.loads(ROUTE_SNAPSHOT_PATH.read_text(encoding="utf-8")))
        self.assertEqual(related(_current_route_snapshot()), expected)

    def test_roster_exports_keep_identifiers_and_names_as_text(self):
        row = dict.fromkeys(project_topics._EXPORT_COLUMNS, "")
        row.update(student_number="0000123", topic_number="01", student_name="=QA", membership="已分组")
        teacher = {"id": 1, "role": "teacher"}
        with patch.object(project_topics, "_export_rows", return_value=[row]):
            csv = project_topics.export_topic_dispatch(1, 1, teacher).body.decode("utf-8-sig")
            response = project_topics.export_topic_workbook(1, 1, teacher)
        self.assertIn("0000123,'=QA", csv)
        book = load_workbook(BytesIO(response.body))
        self.addCleanup(book.close)
        sheet = book.active
        self.assertEqual((sheet["G2"].value, sheet["I2"].value), ("01", "0000123"))
        self.assertEqual((sheet["J2"].value, sheet["J2"].data_type), ("=QA", "s"))
