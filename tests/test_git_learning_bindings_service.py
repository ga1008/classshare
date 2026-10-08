"""Ordinal projection and manual-binding lifecycle on an isolated database."""
import unittest
from fastapi import HTTPException

from test_session_learning_materials_service import _make_conn
from classroom_app.db import schema_session_learning_materials as schema
from classroom_app.services import git_learning_bindings_service as service
from classroom_app.services import session_learning_materials_service as materials
from classroom_app.services.offering_session_resequence_service import plan_offering_resequence, apply_offering_resequence


class GitLearningBindingsTests(unittest.TestCase):
    def test_late_ai_result_preserves_newly_selected_home(self):
        snapshot = {5: [dict(row) for row in self.conn.execute("SELECT * FROM class_offering_sessions").fetchall()]}
        self.conn.execute("UPDATE class_offerings SET home_learning_material_id=100 WHERE id=5")
        with self.assertRaises(HTTPException) as caught:
            materials.lock_unchanged_material_targets(self.conn, 1, snapshot, home_materials={5: None})
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.conn.execute("SELECT home_learning_material_id FROM class_offerings WHERE id=5").fetchone()[0], 100)

    def setUp(self):
        schema._SCHEMA_READY = False
        self.conn = _make_conn()
        self.addCleanup(self.conn.close)
        self.conn.execute("CREATE TABLE courses (id INTEGER PRIMARY KEY)")
        self.conn.execute("INSERT INTO courses VALUES (1)")
        for definition in ("session_date TEXT", "weekday INTEGER", "week_index INTEGER", "academic_section_text TEXT",
                           "academic_location TEXT", "slot_section_count INTEGER", "schedule_status TEXT DEFAULT 'scheduled'",
                           "schedule_metadata_json TEXT DEFAULT '{}'"):
            self.conn.execute("ALTER TABLE class_offering_sessions ADD COLUMN " + definition)
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-09-01',academic_section_text='1-2' WHERE id=10")
        for number in range(2, 5):
            self.conn.execute("INSERT INTO class_offering_sessions(id,class_offering_id,order_index,title,session_date,academic_section_text) VALUES (?,5,?,?,?,'1-2')",
                              (number * 10, number, f"original {number}", f"2026-09-{number * 7 - 6:02}"))
        self.conn.execute("CREATE UNIQUE INDEX lesson_ord ON class_offering_sessions(class_offering_id,order_index)")
        self.conn.execute("INSERT INTO course_material_assignments(material_id,class_offering_id) VALUES (200,5)")
        for number in range(1, 5):
            self.add_entry(300 + number, f"site/lesson_{number}/lesson_{number}.html")
        self.root = dict(self.conn.execute("SELECT * FROM course_materials WHERE id=200").fetchone())

    def add_entry(self, mid, path):
        self.conn.execute("INSERT INTO course_materials(id,teacher_id,parent_id,root_id,name,material_path,node_type,preview_type,file_ext) VALUES (?,1,200,200,?,?,'file','text','html')",
                          (mid, path.rsplit('/', 1)[-1], path))

    def sync(self):
        rows = [dict(row) for row in self.conn.execute("SELECT * FROM course_materials WHERE root_id=200").fetchall()]
        return service.sync_repository_learning_bindings(self.conn, self.root, rows, 1)

    def mapping(self):
        return [(row['id'], row['order_index'], row['learning_material_id']) for row in self.conn.execute(
            "SELECT * FROM class_offering_sessions ORDER BY order_index").fetchall()]

    def test_unchanged_tree_binds_and_repeated_sync_has_no_duplicates(self):
        result = self.sync()
        self.assertEqual(result['total_assignments'], 4)
        self.assertEqual(self.mapping(), [(10, 1, 301), (20, 2, 302), (30, 3, 303), (40, 4, 304)])
        self.assertEqual(self.sync()['total_assignments'], 4)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM class_offering_learning_materials").fetchone()[0], 5)

    def test_move_third_to_end_preserves_identity_and_moves_complete_teaching_bundle(self):
        self.sync()
        materials.bind_material_in_transaction(self.conn, 5, 30, 100, 1)
        self.conn.execute("UPDATE class_offering_learning_materials SET ai_blurb='manual extra' WHERE session_id=30 AND material_id=100")
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-12-30' WHERE id=30")
        self.sync()
        self.assertEqual(self.mapping(), [(10, 1, 301), (20, 2, 302), (40, 3, 303), (30, 4, 304)])
        self.assertEqual(self.conn.execute("SELECT session_id,ai_blurb FROM class_offering_learning_materials WHERE material_id=100").fetchone()[:], (40, 'manual extra'))
        self.assertEqual(self.conn.execute("SELECT title,session_date FROM class_offering_sessions WHERE id=30").fetchone()[:], ('original 3', '2026-12-30'))
        self.sync()
        self.assertEqual(self.mapping(), [(10, 1, 301), (20, 2, 302), (40, 3, 303), (30, 4, 304)])

    def test_unbind_is_not_resurrected_even_after_resequence(self):
        self.sync()
        materials.unbind_material_in_transaction(self.conn, 5, 30, 303, 1)
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-12-30' WHERE id=30")
        self.sync()
        self.assertEqual(self.mapping()[2], (40, 3, None))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM class_offering_learning_materials WHERE material_id=303").fetchone()[0], 0)

    def test_delete_auto_entry_preserves_manual_extra_and_promotes_it(self):
        self.sync()
        materials.bind_material_in_transaction(self.conn, 5, 30, 100, 1)
        self.conn.execute("DELETE FROM course_materials WHERE id=303")
        self.sync()
        self.assertEqual(self.mapping()[2], (30, 3, 100))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM class_offering_learning_materials WHERE material_id=303").fetchone()[0], 0)

    def test_explicit_manual_primary_is_preserved(self):
        materials.bind_material_in_transaction(self.conn, 5, 30, 100, 1)
        self.sync()
        self.assertEqual(self.mapping()[2], (30, 3, 100))
        self.assertEqual([row['material_id'] for row in materials._fetch_rows(self.conn, 5, 30)], [100, 303])

    def test_duplicate_numbered_folders_are_reported_not_guessed(self):
        self.add_entry(333, 'site/lesson_03/index.html')
        result = self.sync()
        self.assertEqual(self.mapping()[2], (30, 3, None))
        self.assertEqual({item['material_id'] for item in result['unresolved']}, {303, 333})

    def test_scope_does_not_cross_teacher_or_unassigned_offering(self):
        self.conn.execute("INSERT INTO class_offerings(id,teacher_id) VALUES (6,2),(7,1)")
        self.conn.execute("INSERT INTO course_material_assignments(material_id,class_offering_id) VALUES (200,6)")
        self.assertEqual(self.sync()['target_classroom_count'], 1)
        self.assertEqual(self.conn.execute("SELECT DISTINCT class_offering_id FROM class_offering_git_learning_bindings").fetchone()[0], 5)

    def test_bundle_permutation_rolls_back_with_caller_transaction(self):
        self.sync()
        self.conn.commit()
        baseline = self.mapping()
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-12-30' WHERE id=30")
        apply_offering_resequence(self.conn, plan_offering_resequence(self.conn, 5))
        self.conn.rollback()
        self.assertEqual(self.mapping(), baseline)
        self.assertEqual(self.conn.execute("SELECT session_id FROM class_offering_git_learning_bindings WHERE lesson_order=3").fetchone()[0], 30)

    def test_dry_run_schema_can_be_created_again_after_rollback(self):
        self.conn.commit()
        self.conn.execute("BEGIN IMMEDIATE")
        self.sync()
        self.conn.rollback()
        self.assertFalse(materials.has_material_bindings_table(self.conn))
        self.assertEqual(self.sync()['total_assignments'], 4)

    def test_late_ai_result_rejects_renumbering_before_any_binding_write(self):
        snapshot = {5: [dict(row) for row in self.conn.execute("SELECT * FROM class_offering_sessions").fetchall()]}
        materials.lock_unchanged_material_targets(self.conn, 1, snapshot)
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-12-30' WHERE id=30")
        apply_offering_resequence(self.conn, plan_offering_resequence(self.conn, 5))
        with self.assertRaises(HTTPException) as caught:
            materials.lock_unchanged_material_targets(self.conn, 1, snapshot)
        self.assertEqual(caught.exception.status_code, 409)

    def test_wrong_canonical_legacy_primary_is_repaired_without_losing_manual_extra(self):
        self.conn.execute("UPDATE class_offering_sessions SET learning_material_id=302 WHERE id=10")
        materials.bind_material_in_transaction(self.conn, 5, 10, 100, 1)
        self.conn.execute("UPDATE class_offering_learning_materials SET ai_blurb='manual note' WHERE session_id=10 AND material_id=100")
        result = self.sync()
        self.assertEqual(self.mapping(), [(10, 1, 301), (20, 2, 302), (30, 3, 303), (40, 4, 304)])
        self.assertEqual([(301, ''), (100, 'manual note')],
                         [(row['material_id'], row['ai_blurb']) for row in materials._fetch_rows(self.conn, 5, 10)])
        self.assertEqual(1, result['corrected_legacy_count'])
        self.assertEqual(0, self.sync()['corrected_legacy_count'])

    def test_lower_priority_legacy_readme_still_has_a_proven_wrong_ordinal(self):
        self.add_entry(399, 'site/lesson_2/README.md')
        self.conn.execute("UPDATE course_materials SET preview_type='markdown',file_ext='md' WHERE id=399")
        self.conn.execute("UPDATE class_offering_sessions SET learning_material_id=399 WHERE id=10")
        result = self.sync()
        self.assertEqual((10, 1, 301), self.mapping()[0])
        self.assertEqual([301], [row['material_id'] for row in materials._fetch_rows(self.conn, 5, 10)])
        self.assertEqual(1, result['corrected_legacy_count'])

    def test_existing_projection_tables_do_not_execute_runtime_ddl(self):
        self.sync()
        statements = []
        self.conn.set_trace_callback(statements.append)
        try:
            self.sync()
        finally:
            self.conn.set_trace_callback(None)
        self.assertFalse(any(sql.lstrip().upper().startswith(('CREATE ', 'ALTER ', 'DROP ')) for sql in statements))

    def test_cancellation_preserves_original_bundle_and_projects_active_ordinals_once(self):
        self.sync()
        materials.bind_material_in_transaction(self.conn, 5, 20, 100, 1)
        self.conn.execute("UPDATE class_offering_sessions SET schedule_status='cancelled' WHERE id=20")
        result = self.sync()
        self.assertEqual(self.mapping(), [(10, 1, 301), (30, 2, 302), (40, 3, 303), (20, 4, 302)])
        self.assertEqual([302, 100], [row['material_id'] for row in materials._fetch_rows(self.conn, 5, 20)])
        self.assertIn('inactive_binding_retained', {row['code'] for row in result['warnings']})
        self.assertIn('missing_active_lesson', {row['code'] for row in result['warnings']})
        after = self.mapping()
        self.assertEqual(0, self.sync()['removed_count'])
        self.assertEqual(after, self.mapping())

    def test_undated_or_malformed_date_keeps_history_without_auto_projecting_tail(self):
        self.sync()
        self.conn.execute("UPDATE class_offering_sessions SET session_date='' WHERE id=20")
        self.conn.execute("UPDATE class_offering_sessions SET session_date='unknown' WHERE id=30")
        result = self.sync()
        self.assertEqual(2, result['total_assignments'])
        self.assertEqual([302], [row['material_id'] for row in materials._fetch_rows(self.conn, 5, 20)])
        self.assertEqual([303], [row['material_id'] for row in materials._fetch_rows(self.conn, 5, 30)])
        self.assertEqual({20, 30}, {row['session_id'] for row in result['warnings'] if row['code'] == 'inactive_binding_retained'})

    def test_unbinding_legacy_canonical_before_first_projection_creates_tombstone(self):
        self.conn.execute("ALTER TABLE course_materials ADD COLUMN git_repo_status TEXT DEFAULT 'plain'")
        self.conn.execute("UPDATE course_materials SET git_repo_status='repository' WHERE id=200")
        self.conn.execute("UPDATE class_offering_sessions SET learning_material_id=303 WHERE id=30")
        materials.unbind_material_in_transaction(self.conn, 5, 30, 303, 1)
        self.assertEqual(1, self.sync()['suppressed_count'])
        self.assertEqual((30, 3, None), self.mapping()[2])
        self.conn.execute("UPDATE class_offering_sessions SET session_date='2026-12-30' WHERE id=30")
        self.sync()
        self.assertEqual((40, 3, None), self.mapping()[2])

    def test_unbinding_wrong_ordinal_legacy_does_not_suppress_correct_other_lesson(self):
        self.conn.execute("ALTER TABLE course_materials ADD COLUMN git_repo_status TEXT DEFAULT 'plain'")
        self.conn.execute("UPDATE course_materials SET git_repo_status='repository' WHERE id=200")
        self.conn.execute("UPDATE class_offering_sessions SET learning_material_id=302 WHERE id=10")
        materials.unbind_material_in_transaction(self.conn, 5, 10, 302, 1)
        self.assertEqual(0, self.sync()['suppressed_count'])
        self.assertEqual([(10, 1, 301), (20, 2, 302)], self.mapping()[:2])

    def test_ai_fallback_and_deterministic_parser_share_actual_entry_types(self):
        from classroom_app.routers.materials_parts.common import _is_readme_material_row
        for name in ('README.md', 'index.md', 'lesson_3.md', 'lesson_3.html', '第3课.md', 'index.htm'):
            row = {'node_type': 'file', 'name': name, 'material_path': 'site/chapter/' + name,
                   'preview_type': 'markdown' if name.endswith('.md') else 'text'}
            self.assertTrue(service.is_repository_learning_entry(name), name)
            self.assertTrue(_is_readme_material_row(row), name)
            self.assertFalse(_is_readme_material_row({**row, 'node_type': 'folder'}), name)
        self.assertFalse(_is_readme_material_row({'node_type': 'file', 'name': 'lesson_3.md', 'preview_type': 'binary'}))
        for name in ('lesson_3.pdf', 'notes.md', 'photo.png', 'README.exe'):
            self.assertFalse(service.is_repository_learning_entry(name), name)


if __name__ == '__main__':
    unittest.main()
