"""Content identity and Git publication regressions on synthetic in-memory data."""
import hashlib
import json
import unittest
from unittest import mock

from fastapi import HTTPException

from classroom_app.db import schema_session_knowledge_trees as schema
from classroom_app.services import git_learning_bindings_service as bindings
from classroom_app.services import session_knowledge_tree_service as kt
from test_session_learning_materials_service import _make_conn
from test_session_knowledge_tree import SAMPLE


class KnowledgeTreeSourceHashTests(unittest.TestCase):
    def setUp(self):
        self.conn = _make_conn()
        self.addCleanup(self.conn.close)
        schema._SCHEMA_READY = False
        schema.ensure_session_knowledge_tree_schema(self.conn)
        self.conn.execute("CREATE TABLE courses (id INTEGER PRIMARY KEY)")
        self.conn.execute("INSERT INTO courses VALUES (1)")
        for definition in ("session_date TEXT", "weekday INTEGER", "week_index INTEGER", "academic_section_text TEXT",
                           "academic_location TEXT", "slot_section_count INTEGER", "schedule_status TEXT DEFAULT 'scheduled'",
                           "schedule_metadata_json TEXT DEFAULT '{}'"):
            self.conn.execute("ALTER TABLE class_offering_sessions ADD COLUMN " + definition)
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-09-01',academic_section_text='1-2' WHERE id=10")
        self.conn.execute("INSERT INTO class_offering_sessions(id,class_offering_id,order_index,title,session_date,academic_section_text) "
                          "VALUES (20,5,2,'L2','2026-09-08','1-2')")
        self.conn.execute("INSERT INTO course_material_assignments(material_id,class_offering_id) VALUES (200,5)")
        self.add_entry(301, "site/lesson_1/lesson_1.html", "lesson 1")
        self.add_entry(302, "site/lesson_2/lesson_2.html", "lesson 2")
        self.root = dict(self.conn.execute("SELECT * FROM course_materials WHERE id=200").fetchone())
        self.sync()

    def add_entry(self, mid, path, content):
        self.conn.execute("""INSERT INTO course_materials
            (id,teacher_id,parent_id,root_id,name,material_path,node_type,preview_type,file_ext,file_hash)
            VALUES (?,1,200,200,?,?,'file','text','html',?)""",
            (mid, path.rsplit('/', 1)[-1], path, hashlib.sha256(content.encode()).hexdigest()))

    def sync(self):
        rows = [dict(row) for row in self.conn.execute("SELECT * FROM course_materials WHERE root_id=200")]
        return bindings.sync_repository_learning_bindings(self.conn, self.root, rows, 1)

    def fingerprint(self, sid):
        session = dict(self.conn.execute("SELECT * FROM class_offering_sessions WHERE id=?", (sid,)).fetchone())
        return kt.source_fingerprints(self.conn, kt.session_material_ids(self.conn, 5, session, 1))

    def ready(self, sid, *, legacy=False):
        row = kt.ensure_row(self.conn, class_offering_id=5, session_id=sid, teacher_id=1)
        token = kt.start_generation(self.conn, row)
        tree = kt.normalize_tree(SAMPLE)
        self.assertTrue(kt.store_generated_tree(self.conn, row["id"], token, tree,
            fingerprint=self.fingerprint(sid)[int(legacy)], source_label="fixture"))
        kt.set_leaf_done(self.conn, sid, next(kt.iter_leaves(tree["nodes"]))["id"], True)
        return kt.load_row(self.conn, sid)

    def state(self, sid):
        current, legacy = self.fingerprint(sid)
        return kt.serialize(kt.load_row(self.conn, sid), can_edit=True, has_material=bool(current),
                            current_fingerprint=current, legacy_fingerprint=legacy or None)

    def test_content_identity_ignores_metadata_aliases_order_and_duplicate_bindings(self):
        self.add_entry(401, "site/copy.html", "lesson 1")
        original = kt.source_fingerprint(self.conn, [301, 302])
        self.assertEqual(original, kt.source_fingerprint(self.conn, [302, 401, 301]))
        self.conn.execute("UPDATE course_materials SET name='new display name', material_path='site/new-path.html' WHERE id=301")
        self.assertEqual(original, kt.source_fingerprint(self.conn, [301, 302]))
        self.conn.execute("UPDATE course_materials SET file_hash='changed' WHERE id=301")
        self.assertNotEqual(original, kt.source_fingerprint(self.conn, [301, 302]))

    def test_unknown_hashes_do_not_prove_two_documents_equal(self):
        self.conn.execute("UPDATE course_materials SET file_hash='' WHERE id IN (301,302)")
        self.assertNotEqual(kt.source_fingerprint(self.conn, [301]), kt.source_fingerprint(self.conn, [302]))

    def test_legacy_read_compatibility_does_not_hide_actual_change(self):
        before = self.ready(10, legacy=True)
        self.assertFalse(self.state(10)["is_stale"])
        self.assertTrue(self.state(10)["current_source_fingerprint"].startswith("content-v1:"))
        self.conn.execute("UPDATE course_materials SET file_hash='changed' WHERE id=301")
        self.assertTrue(self.state(10)["is_stale"])
        self.assertEqual(0, kt.upgrade_repository_source_fingerprints(self.conn, 200, 1))
        self.assertEqual(before, kt.load_row(self.conn, 10))

    def test_git_same_content_replacement_and_one_changed_lesson_preserve_other_progress(self):
        before = {sid: self.ready(sid, legacy=True) for sid in (10, 20)}
        self.assertEqual(2, kt.upgrade_repository_source_fingerprints(self.conn, 200, 1))
        # Git removes and recreates an entry under an equivalent new path/id.
        self.conn.execute("DELETE FROM course_materials WHERE id=301")
        self.add_entry(401, "site/lesson_1/index.html", "lesson 1")
        self.conn.execute("UPDATE course_materials SET file_hash='lesson-2-new-content' WHERE id=302")
        self.sync()
        self.assertEqual(401, self.conn.execute("SELECT learning_material_id FROM class_offering_sessions WHERE id=10").fetchone()[0])
        self.assertFalse(self.state(10)["is_stale"])
        self.assertTrue(self.state(20)["is_stale"])
        for sid in (10, 20):
            current = kt.load_row(self.conn, sid)
            for key in ("tree_json", "completed_json", "progress_percent", "version", "updated_at", "generated_at"):
                self.assertEqual(before[sid][key], current[key], (sid, key))
        self.assertEqual(0, kt.upgrade_repository_source_fingerprints(self.conn, 200, 1))

    def test_upgrade_respects_repository_owner_generation_and_caller_rollback(self):
        before = self.ready(10, legacy=True)
        self.assertEqual(0, kt.upgrade_repository_source_fingerprints(self.conn, 200, 99))
        self.assertEqual(0, kt.upgrade_repository_source_fingerprints(self.conn, 999, 1))
        self.conn.commit()
        self.assertEqual(1, kt.upgrade_repository_source_fingerprints(self.conn, 200, 1))
        self.conn.rollback()
        self.assertEqual(before, kt.load_row(self.conn, 10))
        kt.start_generation(self.conn, before)
        self.assertEqual(0, kt.upgrade_repository_source_fingerprints(self.conn, 200, 1))

    def test_generation_wins_if_it_starts_after_completion_or_order_read(self):
        before = self.ready(10)
        kt.start_generation(self.conn, before)
        self.assertIsNone(kt._write_completion(self.conn, before, []))
        tree = json.loads(before["tree_json"])
        with mock.patch.object(kt, "load_row", return_value=before):
            with self.assertRaises(HTTPException) as caught:
                kt.save_order(self.conn, 10, "", [n["id"] for n in reversed(tree["nodes"])], before["version"])
        self.assertEqual(409, caught.exception.status_code)
        self.assertEqual(before["tree_json"], kt.load_row(self.conn, 10)["tree_json"])
        self.assertEqual(before["completed_json"], kt.load_row(self.conn, 10)["completed_json"])

    def test_batch_marks_removed_sources_stale_without_resetting_tree(self):
        before = self.ready(10)
        self.conn.execute("DELETE FROM class_offering_learning_materials WHERE session_id=10")
        self.conn.execute("UPDATE class_offering_sessions SET learning_material_id=NULL WHERE id=10")
        states = kt.list_offering_trees(self.conn, 5, 1)
        self.assertTrue(next(row for row in states if row["session_id"] == 10)["is_stale"])
        self.assertEqual(before, kt.load_row(self.conn, 10))


if __name__ == "__main__":
    unittest.main()
