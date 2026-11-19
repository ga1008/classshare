import json
import sqlite3
import unittest
from datetime import datetime
from unittest.mock import patch

from classroom_app.services import learning_progress_service as learning
from classroom_app.services.cultivation_material_service import (
    legacy_scored_material_ids, load_material_scope, material_credit, material_scope_key, session_credit_start,
)


class CultivationEarnedMaterialTests(unittest.TestCase):
    def test_publishing_then_studying_never_requires_catching_up(self):
        first = material_credit({"1": 1.0, "2": 0.5}, {})
        opened = material_credit({"1": 1.0, "2": 0.5, "3": 0.0}, first)
        self.assertEqual(opened["coverage_ratio"], 0.5)
        self.assertEqual(opened["earned_ratio"], first["earned_ratio"])
        work = material_credit({"1": 1.0, "2": 0.5, "3": 0.25}, opened)
        self.assertAlmostEqual(work["earned_ratio"], 0.75 + 0.25 / 6)
        self.assertEqual(work, material_credit({"1": 1.0, "2": 0.5, "3": 0.25}, work))
        removed = material_credit({"1": 1.0, "2": 0.0}, work)
        rebound = material_credit({"1": 1.0, "2": 0.5, "3": 0.25}, removed)
        self.assertEqual(rebound, work)
        self.assertEqual(material_credit({"1": 1, "2": 1, "3": 1}, rebound)["earned_ratio"], 1.0)

    def test_scope_uses_moved_slot_and_excludes_future_even_if_also_on_homepage(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE class_offerings(id, teacher_id, home_learning_material_id);
            INSERT INTO class_offerings VALUES(1,3,2);
            CREATE TABLE class_offering_sessions(id, class_offering_id, order_index, learning_material_id,
                session_date, academic_section_text, academic_time_text, schedule_metadata_json, schedule_status);
            INSERT INTO class_offering_sessions VALUES(11,1,1,1,'2026-11-12','2-3','', '{}','scheduled'),
                (12,1,2,2,'2026-11-20','8-9','08:30-10:00','{"section_text":"2-3"}','scheduled'),
                (13,1,3,3,'2026-11-12','4-5','','{}','cancelled'),
                (14,1,4,4,'','','','{}','scheduled');
            CREATE TABLE class_offering_learning_materials(class_offering_id,session_id,material_id,created_at);
            INSERT INTO class_offering_learning_materials VALUES(1,12,5,'2026-11-09'),
                (1,11,6,'2026-11-09'),(1,0,2,'2026-11-12');
            CREATE TABLE course_materials(id, teacher_id, parent_id, name, root_id, node_type, material_path,
                preview_type, check_questions_json, check_questions_status);
            CREATE TABLE course_material_assignments(class_offering_id,material_id,created_at,assigned_by_teacher_id,publication_mode);
        """)
        for mid in range(1, 9):
            db.execute("INSERT INTO course_materials VALUES(?,3,NULL,?,1,'file',?,'markdown','{}','idle')",
                       (mid, str(mid), f"/{mid}.md"))
        db.executemany("INSERT INTO course_material_assignments VALUES(1,?,'2026-11-01',3,'explicit')",
                       [(mid,) for mid in (2, 3, 4, 5, 7)])
        db.execute("INSERT INTO course_material_assignments VALUES(1,8,'2026-11-01',3,'archive')")
        before, next_start = load_material_scope(db, 1, as_of=datetime.fromisoformat("2026-11-20T16:19:59+08:00"))
        after, another = load_material_scope(db, 1, as_of=datetime.fromisoformat("2026-11-20T16:20:00+08:00"))
        self.assertEqual([r["id"] for r in before], [1, 7])
        self.assertEqual(next_start.isoformat(), "2026-11-20T16:20:00+08:00")
        self.assertEqual([r["id"] for r in after], [1, 2, 5, 7])
        self.assertIsNone(another)
        self.assertEqual(material_scope_key(after), material_scope_key(list(reversed(after)) + [after[0]]))
        db.execute("UPDATE course_material_assignments SET created_at='2026-11-18' WHERE material_id=5")
        self.assertEqual(legacy_scored_material_ids(db, 1, {2, 5, 6, 7, 8}, "2026-11-11"), {2, 7})
        self.assertEqual(session_credit_start({"session_date": "2026-11-20"}).isoformat(), "2026-11-21T00:00:00+08:00")
        explicit = {"session_date": "2026-11-20", "academic_time_text": "09:35-10:15"}
        self.assertEqual(session_credit_start(explicit).isoformat(), "2026-11-20T09:35:00+08:00")

    def test_first_upgrade_also_pays_new_work_over_a_protected_legacy_floor(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE learning_progress_snapshots(class_offering_id, student_id,
            metrics_json, components_json, metadata_json, calculated_at)""")
        old = {"material": {"ratio": 0.8, "required_count": 2,
                            "items": [{"id": 1, "unit_ratio": 0.6}, {"id": 2, "unit_ratio": 1.0}]}}
        db.execute("INSERT INTO learning_progress_snapshots VALUES(1,8,?,'{}','{}','2026-11-19T08:00:00')",
                   (json.dumps(old),))
        units = {"1": 0.6, "2": 1.0, "3": 0.25, "4": 0}
        old_fact = {"completed": True, "updated_at": "2026-11-20T09:00:00"}
        facts = {1: old_fact, 2: old_fact, 3: {**old_fact, "first_viewed_at": "2026-11-20T08:50:00"}}
        got = learning._earned_material_credit(db, 1, 8, units, facts)
        self.assertAlmostEqual(got["earned_ratio"], 0.8 + (1 - 0.8) * 0.25 / 2.4)
        self.assertLess(got["coverage_ratio"], got["legacy_credit_ratio"])

    def test_legacy_credit_read_once_and_previous_future_read_is_not_paid_twice(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE learning_progress_snapshots(class_offering_id, student_id, metrics_json,
            components_json, metadata_json, calculated_at)""")
        db.execute("INSERT INTO learning_progress_snapshots VALUES(1,1,?,'{\"material\":30}','{}','2026-11-10')",
                   (json.dumps({"weights": {"material": 40, "task": 40, "interaction": 15, "consistency": 5},
                                "material": {"required_count": 3, "items": [{"id": 3, "unit_ratio": 1.0}]}}),))
        docs = [{"id": mid, "name": str(mid)} for mid in (1, 2, 3, 4)]
        facts = {1: {"completed": 1, "mastered": 1, "updated_at": "2026-11-08"},
                 3: {"completed": 1, "mastered": 1, "updated_at": "2026-11-09"},
                 4: {"completed": 1, "mastered": 1, "updated_at": "2026-11-07"}}
        scope = self.enterContext(patch.object(learning, "_load_required_materials", return_value=docs[:2]))
        self.enterContext(patch.object(learning, "_load_progress_rows", return_value=facts))
        self.enterContext(patch.object(learning, "_load_assignment_metrics", return_value={"task_ratio": 0.0}))
        self.enterContext(patch.object(learning, "_load_interaction_metrics", return_value={"interaction_ratio": 0, "consistency_ratio": 0}))
        history = self.enterContext(patch.object(learning, "legacy_material_credit", return_value=0.78))
        self.enterContext(patch.object(learning, "legacy_scored_material_ids", return_value=set()))
        config = {"weights": {"material": 45, "task": 35, "interaction": 15, "consistency": 5}}
        initial = learning._build_learning_metrics(db, 1, 1, weight_config=config)
        self.assertEqual(initial["components"]["material"], 35.1)
        self.assertEqual(initial["material"]["legacy_credit_ratio"], 0.78)
        self.assertEqual(initial["material"]["ratio"], 0.5)
        db.execute("UPDATE learning_progress_snapshots SET metrics_json=?", (json.dumps(initial),))
        self.assertNotIn("4", initial["material"]["credit_watermarks"])
        scope.return_value = docs[:3]
        after = learning._build_learning_metrics(db, 1, 1, weight_config=config)
        self.assertEqual(after["components"]["material"], 35.1)
        self.assertEqual(after["material"]["ratio"], 0.6667)
        self.assertEqual(after["material"]["items"][2]["credit_unit_ratio"], 1)
        history.assert_called_once()
        scope.return_value = docs
        newly_required = learning._build_learning_metrics(db, 1, 1, weight_config=config)
        self.assertAlmostEqual(newly_required["material"]["earned_ratio"], 0.89)

        # A historical projection may have lost its detailed material object
        # while retaining its separately stored, rounded component and weights.
        history.return_value = 0
        db.execute("UPDATE learning_progress_snapshots SET metrics_json='{}', components_json=?, metadata_json=?",
                   (json.dumps({"material": 30}), json.dumps({"weights":
                        {"material": 40, "task": 40, "interaction": 15, "consistency": 5}})))
        fallback = learning._build_learning_metrics(db, 1, 1, weight_config=config)
        self.assertEqual(fallback["material"]["legacy_credit_ratio"], 0.75)
        self.assertEqual(fallback["components"]["material"], 33.8)
