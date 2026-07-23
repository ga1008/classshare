import os
import sqlite3
import unittest

os.environ.setdefault("DB_ENGINE", "sqlite")

import classroom_app.db.schema_resume as schema_mod
from classroom_app.db.schema_resume import ensure_resume_schema
from classroom_app.services.resume import resume_application_service as applications
from classroom_app.services.resume import resume_document_service as documents


class ResumeApplicationTests(unittest.TestCase):
    def setUp(self):
        schema_mod._SCHEMA_READY = False
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        ensure_resume_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def _payload(self, **changes):
        payload = {
            "company_name": "示例科技",
            "target_position": "数据分析实习生",
            "status": "wishlist",
            "channel": "学校就业网",
            "next_action": "完善项目说明",
            "next_action_at": "2026-08-01T10:00",
            "note": "仅测试数据",
        }
        payload.update(changes)
        return payload

    def test_create_update_list_delete(self):
        item = applications.create_application(self.conn, 1, self._payload())
        self.assertEqual(item["status_label"], "想投")
        items = applications.list_applications(self.conn, 1)
        self.assertEqual([row["id"] for row in items], [item["id"]])
        updated = applications.update_application(
            self.conn,
            1,
            item["id"],
            self._payload(status="interview", applied_on="2026-07-30"),
        )
        self.assertEqual(updated["status_label"], "面试")
        self.assertTrue(updated["_status_changed"])
        unchanged = applications.update_application(
            self.conn,
            1,
            item["id"],
            self._payload(status="interview", applied_on="2026-07-30", next_action="准备面试"),
        )
        self.assertFalse(unchanged["_status_changed"])
        applications.delete_application(self.conn, 1, item["id"])
        self.assertEqual(applications.list_applications(self.conn, 1), [])

    def test_linked_resume_must_belong_to_student(self):
        resume_id = documents.create_resume(
            self.conn,
            2,
            title="其他学生简历",
            target_position="数据分析实习生",
            template_key="classic",
            layout={"blocks": [{"type": "tech_stack"}]},
        )
        with self.assertRaises(ValueError):
            applications.create_application(self.conn, 1, self._payload(resume_id=resume_id))

    def test_invalid_status_and_dates_are_rejected(self):
        with self.assertRaises(ValueError):
            applications.create_application(self.conn, 1, self._payload(status="hired_secret"))
        with self.assertRaises(ValueError):
            applications.create_application(self.conn, 1, self._payload(applied_on="07/30/2026"))

    def test_other_student_cannot_update_or_delete(self):
        item = applications.create_application(self.conn, 1, self._payload())
        with self.assertRaises(LookupError):
            applications.update_application(self.conn, 2, item["id"], self._payload())
        with self.assertRaises(LookupError):
            applications.delete_application(self.conn, 2, item["id"])


if __name__ == "__main__":
    unittest.main()
