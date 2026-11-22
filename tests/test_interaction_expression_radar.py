import json
import sqlite3
import unittest
from contextlib import closing

from classroom_app.db.schema_interaction_expression import EXPRESSION_MODEL_VERSION, ensure_interaction_expression_schema
from classroom_app.services.ability_radar_service import build_ability_radar, clear_peer_cache


class PublishedInteractionRadarTests(unittest.TestCase):
    def setUp(self):
        self.conn = self.enterContext(closing(sqlite3.connect(":memory:")))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE class_offerings(id INTEGER PRIMARY KEY,class_id INTEGER);
            CREATE TABLE class_offering_class_links(id INTEGER PRIMARY KEY,offering_id INTEGER,class_id INTEGER,is_primary INTEGER);
            CREATE TABLE students(id INTEGER PRIMARY KEY,class_id INTEGER,enrollment_status TEXT);
            CREATE TABLE learning_progress_snapshots(class_offering_id INTEGER,student_id INTEGER,score REAL,metrics_json TEXT);
            INSERT INTO class_offerings VALUES (7,1),(8,1);
            INSERT INTO students VALUES (1,1,'active'),(2,1,'active'),(3,1,'active'),(4,1,'active');
        """)
        self.conn.executemany("INSERT INTO learning_progress_snapshots VALUES(7,?,?,?)",
                              [(sid, score, '{"interactions":{"interaction_ratio":0.9,"consistency_ratio":0}}')
                               for sid, score in [(1, 30), (2, 60), (3, 20), (4, 50)]])
        ensure_interaction_expression_schema(self.conn)
        clear_peer_cache()
        self.addCleanup(clear_peer_cache)

    def publish(self, offering, own, teacher=None, *, offered=0, participated=0, expression_offered=0):
        def payload(facts):
            return json.dumps({"facts": facts, "opportunities": {
                "offered": offered, "participated": participated, "expression_offered": expression_offered,
                "coverage_status": "complete",
            }})
        self.conn.execute("INSERT INTO interaction_expression_snapshots VALUES(?,1,?,?,?,?)",
                          (offering, EXPRESSION_MODEL_VERSION, payload(own), payload(own if teacher is None else teacher),
                           "2026-11-22T11:00:00"))

    def radar(self, viewer="self", courses=(7,)):
        return build_ability_radar(self.conn, student_id=1, offering_ids=courses, evidence_audience=viewer)

    def test_pending_history_then_lecture_and_discrete_participation(self):
        self.assertEqual("课堂记录正在整理", self.radar()["interaction_detail"]["status_label"])
        self.publish(7, {"participation_count": 0})
        self.assertEqual("暂无可评机会", self.radar()["interaction_detail"]["status_label"])
        self.publish(8, {"participation_count": 0}, offered=3, participated=3)
        detail = self.radar(courses=(7, 8))["interaction_detail"]
        self.assertIn("已记录课堂参与", detail["status_label"])
        self.assertIn("3/3", detail["participation_label"])
        self.assertEqual(0, detail["expression_count"])

    def test_author_and_teacher_get_only_their_published_slice_public_gets_none(self):
        shared = {"participation_count": 5, "expression_count": 3, "ai_message_count": 4,
                  "expression_source_counts": {"legacy_ai": 1, "group_exchange": 1, "discussion_reply": 1}}
        self.publish(7, {**shared, "expression_count": 4,
                         "expression_source_counts": {**shared["expression_source_counts"], "qna": 1}}, shared)
        self.conn.commit()
        snapshot = list(self.conn.execute("SELECT * FROM learning_progress_snapshots ORDER BY student_id"))
        trace = []
        self.conn.set_trace_callback(trace.append)
        self.conn.execute("PRAGMA query_only=ON")
        own = self.radar()
        self.assertEqual(4, own["interaction_detail"]["expression_count"])
        self.assertIn("课堂 AI 学生消息 4 条", own["interaction_detail"]["participation_label"])
        self.assertIn("AI 提问 1 件", own["interaction_detail"]["expression_label"])
        self.assertIn("问答墙 1 件", own["interaction_detail"]["expression_label"])
        trace.clear()
        teacher = self.radar("teacher")
        self.assertEqual(3, teacher["interaction_detail"]["expression_count"])
        self.assertNotIn("问答墙", teacher["interaction_detail"]["expression_label"])
        self.assertFalse(any("self_json" in sql for sql in trace))
        trace.clear()
        public = self.radar(None)
        self.assertNotIn("interaction_detail", public)
        self.assertFalse(any("interaction_expression_snapshots" in sql for sql in trace))
        for radar in (own, teacher, public):
            axis = next(a for a in radar["axes"] if a["key"] == "interaction")
            self.assertIsNone(axis["score"])
            self.assertIsNone(axis["average"])
            self.assertEqual(30, next(a["score"] for a in radar["axes"] if a["key"] == "cultivation"))
        self.assertEqual(snapshot, list(self.conn.execute("SELECT * FROM learning_progress_snapshots ORDER BY student_id")))

    def test_valid_sources_are_visible_while_one_course_is_pending(self):
        self.publish(8, {"participation_count": 1, "expression_count": 1, "ai_message_count": 2})
        detail = self.radar(courses=(7, 8))["interaction_detail"]
        self.assertEqual("已记录互动表达", detail["status_label"])
        self.assertIn("1 门课堂等待整理", detail["coverage_label"])
        self.assertIsNone(detail["ai_message_count"])

    def test_generated_upload_prompt_contributes_only_to_participation_label(self):
        self.publish(7, {"participation_count": 1, "expression_count": 0, "ai_message_count": 1,
                         "source_counts": {"workspace_ai": 1}, "expression_source_counts": {}})
        detail = self.radar()["interaction_detail"]
        self.assertIn("已记录课堂参与", detail["status_label"])
        self.assertIn("课堂 AI 学生消息 1 条", detail["participation_label"])
        self.assertEqual("尚无可核实的表达记录", detail["expression_label"])
