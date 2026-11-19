import json
import sqlite3
import unittest

from classroom_app.services.cultivation_material_history import legacy_material_credit


class HistoricalMaterialCreditTests(unittest.TestCase):
    def setUp(self):
        db = self.db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.executescript("""
            CREATE TABLE class_offerings (id, home_learning_material_id);
            CREATE TABLE class_offering_sessions (id, class_offering_id, learning_material_id);
            CREATE TABLE class_offering_learning_materials (class_offering_id, material_id, session_id, created_at);
            CREATE TABLE course_material_assignments (class_offering_id, material_id, created_at);
            CREATE TABLE course_materials (id, node_type, created_at);
            CREATE TABLE learning_material_progress (class_offering_id, material_id, first_viewed_at);
            CREATE TABLE cultivation_score_events (id INTEGER PRIMARY KEY, class_offering_id, student_id,
                component, source_ref, delta, metadata_json, created_at);
            CREATE TABLE cultivation_weekly_snapshots (class_offering_id, student_id, components_json, metadata_json);
        """)
        for i in range(1, 22):
            stamp = "2026-11-01" if i <= 17 else "2026-11-15"
            db.execute("INSERT INTO course_materials VALUES (?, 'file', ?)", (i, stamp))
            db.execute("INSERT INTO course_material_assignments VALUES (1, ?, ?)", (i, stamp))
        self.event("2026-11-12", 24, 35.1)
        self.event("2026-11-19", 35.1, 28.4)

    def event(self, when, previous, current, source="snapshot:version:2", version="v1"):
        data = json.dumps(dict(previous=previous, current=current, weight_version=version, weights={"material": 45}))
        self.db.execute("INSERT INTO cultivation_score_events VALUES (NULL,1,9,'material',?,?,?,?)",
                        (source, current - previous, data, when))

    def credit(self):
        return legacy_material_credit(self.db, 1, 9, {"weight_version": "v1"}) * 45

    def test_recovers_documented_loss_and_preserves_later_earnings(self):
        self.assertAlmostEqual(35.1, self.credit())
        self.event("2026-11-20", 28.4, 31.4)
        self.assertAlmostEqual(35.1 + (45 - 35.1) * 3 / (45 - 28.4), self.credit())

    def test_unrelated_recalibration_or_ambiguous_publication_is_not_restored(self):
        self.db.execute("UPDATE course_material_assignments SET created_at='2026-11-21' WHERE material_id=21")
        self.assertEqual(0, self.credit())
        self.db.execute("UPDATE course_materials SET created_at='2026-11-01'")
        self.db.execute("UPDATE course_material_assignments SET created_at='2026-11-01'")
        self.assertEqual(0, self.credit())

    def test_old_formula_and_new_formula_are_not_compared(self):
        self.db.execute("""INSERT INTO cultivation_score_events VALUES
            (NULL,5,99,'task','snapshot:model-upgrade',1,'{}','2026-11-15')""")
        self.assertEqual(0, self.credit())

    def test_explicit_model_or_weight_reset_ends_old_entitlement(self):
        self.event("2026-11-20", 28.4, 25, source="snapshot:model-upgrade")
        self.assertEqual(0, self.credit())
        self.event("2026-11-21", 25, 20, version="teacher-v2")
        self.assertEqual(0, self.credit())

    def test_weekly_calculation_anchors_unchanged_material_after_a_model_change(self):
        data = json.dumps(dict(weight_version="v1", weights={"material": 45}, snapshot_calculated_at="2026-11-13"))
        self.db.execute("INSERT INTO cultivation_score_events VALUES (NULL,1,9,'task','snapshot:model-upgrade',1,?,?)",
                        (data, "2026-11-13"))
        self.db.execute("INSERT INTO cultivation_weekly_snapshots VALUES (1,9,?,?)", (json.dumps({"material": 35.1}), data))
        self.assertAlmostEqual(35.1, self.credit())
