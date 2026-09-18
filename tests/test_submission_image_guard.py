"""Per-assignment screenshot hash library: derived claims, conflict rules, image variants."""
from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from classroom_app.services import file_service, submission_image_variants
from classroom_app.services.submission_image_guard_service import (
    HashCandidate,
    HashCheckOptions,
    candidates_from_stored_files,
    duplicate_image_error_detail,
    find_image_hash_conflicts,
    is_hash_guarded_attachment,
    load_assignment_hash_claims,
    load_assignment_question_labels,
    parse_hash_check_items,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64

SCHEMA = """
CREATE TABLE students(id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE exam_papers(id TEXT PRIMARY KEY, questions_json TEXT);
CREATE TABLE submissions(id INTEGER PRIMARY KEY, assignment_id TEXT, student_pk_id INTEGER, student_name TEXT);
CREATE TABLE submission_files(id INTEGER PRIMARY KEY, submission_id INTEGER, original_filename TEXT,
 relative_path TEXT, mime_type TEXT, file_hash TEXT);
CREATE TABLE submission_drafts(id INTEGER PRIMARY KEY, assignment_id TEXT, student_pk_id INTEGER);
CREATE TABLE submission_draft_files(id INTEGER PRIMARY KEY, draft_id INTEGER, question_id TEXT, kind TEXT,
 original_filename TEXT, relative_path TEXT, mime_type TEXT, file_hash TEXT);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO students(id, name) VALUES (1, '张三'), (2, '李四'), (3, '王五')")
    conn.execute(
        "INSERT INTO exam_papers(id, questions_json) VALUES (?, ?)",
        (
            "paper-1",
            json.dumps({"pages": [{"questions": [{"id": "q1", "text": "一"}, {"id": "q2", "text": "二"}]}]}),
        ),
    )
    return conn


def _seed_submission(conn, *, student: int, assignment: str, files: list[tuple[str, str, str]]) -> int:
    submission_id = conn.execute(
        "INSERT INTO submissions(assignment_id, student_pk_id, student_name) VALUES (?, ?, ?)",
        (assignment, student, f"student-{student}"),
    ).lastrowid
    for relative_path, mime, digest in files:
        conn.execute(
            "INSERT INTO submission_files(submission_id, original_filename, relative_path, mime_type, file_hash)"
            " VALUES (?, ?, ?, ?, ?)",
            (submission_id, Path(relative_path).name, relative_path, mime, digest),
        )
    return int(submission_id)


def _seed_draft(conn, *, student: int, assignment: str, files: list[tuple[str, str, str, str]]) -> int:
    draft_id = conn.execute(
        "INSERT INTO submission_drafts(assignment_id, student_pk_id) VALUES (?, ?)",
        (assignment, student),
    ).lastrowid
    for question_id, relative_path, mime, digest in files:
        conn.execute(
            "INSERT INTO submission_draft_files(draft_id, question_id, kind, original_filename, relative_path,"
            " mime_type, file_hash) VALUES (?, ?, 'file', ?, ?, ?, ?)",
            (draft_id, question_id, Path(relative_path).name, relative_path, mime, digest),
        )
    return int(draft_id)


class GuardPredicateTests(unittest.TestCase):
    def test_images_are_guarded_but_drawings_and_code_are_not(self):
        self.assertTrue(is_hash_guarded_attachment("exam_question_files/q1/shot.png", "image/png"))
        self.assertTrue(is_hash_guarded_attachment("shot.JPG", ""))
        self.assertTrue(is_hash_guarded_attachment("pasted", "image/webp"))
        self.assertFalse(is_hash_guarded_attachment("exam_drawings/q1_附图.png", "image/png"))
        self.assertFalse(is_hash_guarded_attachment("exam_question_files/q1/x.png", "image/png", kind="exam_drawing"))
        self.assertFalse(is_hash_guarded_attachment("src/main.py", "text/x-python"))
        self.assertFalse(is_hash_guarded_attachment("report.pdf", "application/pdf"))

    def test_question_labels_follow_paper_order(self):
        conn = _connect()
        labels = load_assignment_question_labels(conn, {"exam_paper_id": "paper-1"})
        self.assertEqual(labels, {"q1": "第1题", "q2": "第2题"})
        self.assertEqual(load_assignment_question_labels(conn, {"exam_paper_id": None}), {})

    def test_parse_hash_check_items_rejects_garbage(self):
        items = parse_hash_check_items([
            {"hash": HASH_A.upper(), "question_id": "q1", "file_name": "a.png"},
            {"hash": "nope"},
            "junk",
            {"file_hash": HASH_B},
        ])
        self.assertEqual([item.file_hash for item in items], [HASH_A, HASH_B])
        self.assertEqual(items[0].question_id, "q1")
        self.assertEqual(items[1].file_name, "附件")


class ConflictRuleTests(unittest.TestCase):
    def setUp(self):
        self.conn = _connect()
        self.labels = load_assignment_question_labels(self.conn, {"exam_paper_id": "paper-1"})

    def test_claims_are_derived_from_submissions_and_drafts_only_for_images(self):
        _seed_submission(self.conn, student=1, assignment="hw", files=[
            ("exam_question_files/q1/shot.png", "image/png", HASH_A),
            ("src/main.py", "text/x-python", HASH_B),
        ])
        _seed_draft(self.conn, student=2, assignment="hw", files=[
            ("q2", "exam_question_files/q2/copy.png", "image/png", HASH_C),
            ("q2", "exam_drawings/q2_附图.png", "image/png", HASH_B),
        ])
        _seed_submission(self.conn, student=3, assignment="other", files=[("x.png", "image/png", HASH_A)])
        claims = load_assignment_hash_claims(self.conn, "hw")
        self.assertEqual(sorted((c.file_hash, c.student_pk_id, c.question_id, c.source) for c in claims), [
            (HASH_A, 1, "q1", "submission"),
            (HASH_C, 2, "q2", "draft"),
        ])
        self.assertEqual(claims[0].student_name, "张三")

    def test_other_students_screenshot_is_refused_with_owner_name_and_question(self):
        _seed_submission(self.conn, student=1, assignment="hw", files=[
            ("exam_question_files/q1/shot.png", "image/png", HASH_A),
        ])
        conflicts = find_image_hash_conflicts(
            self.conn, assignment_id="hw", student_pk_id=2,
            candidates=[HashCandidate(HASH_A, "q2", "exam_question_files/q2/mine.png", "mine.png")],
            question_labels=self.labels,
        )
        self.assertEqual(len(conflicts), 1)
        conflict = conflicts[0]
        self.assertEqual(conflict.kind, "other")
        self.assertEqual(conflict.owner_student_name, "张三")
        self.assertEqual(conflict.owner_question_label, "第1题")
        self.assertIn("张三 同学", conflict.message)
        self.assertIn("第1题", conflict.message)
        self.assertIn("重新选取", conflict.message)

    def test_same_student_other_question_in_draft_is_self_duplicate(self):
        _seed_draft(self.conn, student=1, assignment="hw", files=[
            ("q1", "exam_question_files/q1/shot.png", "image/png", HASH_A),
        ])
        conflicts = find_image_hash_conflicts(
            self.conn, assignment_id="hw", student_pk_id=1,
            candidates=[HashCandidate(HASH_A, "q2", "exam_question_files/q2/again.png", "again.png")],
            question_labels=self.labels,
        )
        self.assertEqual(conflicts[0].kind, "self")
        self.assertIn("已在第1题上传过", conflicts[0].message)

    def test_replacing_the_same_question_or_path_is_not_a_conflict(self):
        _seed_draft(self.conn, student=1, assignment="hw", files=[
            ("q1", "exam_question_files/q1/shot.png", "image/png", HASH_A),
        ])
        by_question = find_image_hash_conflicts(
            self.conn, assignment_id="hw", student_pk_id=1,
            candidates=[HashCandidate(HASH_A, "q1", "exam_question_files/q1/renamed.png", "renamed.png")],
            options=HashCheckOptions(replaced_question_ids={"q1"}),
        )
        self.assertEqual(by_question, [])
        by_path = find_image_hash_conflicts(
            self.conn, assignment_id="hw", student_pk_id=1,
            candidates=[HashCandidate(HASH_A, "q1", "exam_question_files/q1/shot.png", "shot.png")],
        )
        self.assertEqual(by_path, [])

    def test_own_final_submission_never_blocks_a_resubmission(self):
        _seed_submission(self.conn, student=1, assignment="hw", files=[
            ("exam_question_files/q1/shot.png", "image/png", HASH_A),
        ])
        conflicts = find_image_hash_conflicts(
            self.conn, assignment_id="hw", student_pk_id=1,
            candidates=[HashCandidate(HASH_A, "q2", "exam_question_files/q2/shot.png", "shot.png")],
            options=HashCheckOptions(include_own_draft=False),
        )
        self.assertEqual(conflicts, [])

    def test_duplicate_inside_one_batch_is_reported_once(self):
        conflicts = find_image_hash_conflicts(
            self.conn, assignment_id="hw", student_pk_id=1,
            candidates=[
                HashCandidate(HASH_A, "q1", "exam_question_files/q1/a.png", "a.png"),
                HashCandidate(HASH_A, "q2", "exam_question_files/q2/b.png", "b.png"),
                HashCandidate(HASH_B, "", "c.png", "c.png"),
            ],
            question_labels=self.labels,
        )
        self.assertEqual([c.kind for c in conflicts], ["batch"])
        self.assertEqual(conflicts[0].file_name, "b.png")
        self.assertIn("「a.png」", conflicts[0].message)
        self.assertIn("第1题", conflicts[0].message)

    def test_withdraw_frees_the_hash_by_deleting_rows(self):
        submission_id = _seed_submission(self.conn, student=1, assignment="hw", files=[
            ("shot.png", "image/png", HASH_A),
        ])
        candidate = [HashCandidate(HASH_A, "", "shot.png", "shot.png")]
        self.assertEqual(len(find_image_hash_conflicts(self.conn, assignment_id="hw", student_pk_id=2, candidates=candidate)), 1)
        self.conn.execute("DELETE FROM submission_files WHERE submission_id = ?", (submission_id,))
        self.conn.execute("DELETE FROM submissions WHERE id = ?", (submission_id,))
        self.assertEqual(find_image_hash_conflicts(self.conn, assignment_id="hw", student_pk_id=2, candidates=candidate), [])

    def test_whole_assignment_scope_label_and_error_detail(self):
        _seed_submission(self.conn, student=1, assignment="hw", files=[("shot.png", "image/png", HASH_A)])
        conflicts = find_image_hash_conflicts(
            self.conn, assignment_id="hw", student_pk_id=2,
            candidates=[HashCandidate(HASH_A, "", "shot.png", "shot.png")],
        )
        self.assertIn("本次作业", conflicts[0].message)
        detail = duplicate_image_error_detail(conflicts, action_label="提交")
        self.assertEqual(detail["code"], "duplicate_image")
        self.assertEqual(detail["duplicate_file_count"], 1)
        self.assertTrue(detail["message"].startswith("提交失败："))
        self.assertEqual(detail["duplicate_files"][0]["reason"], "duplicate_image")

    def test_candidates_from_stored_files_use_manifest_question_ids(self):
        class Stored:
            def __init__(self, relative_path, mime_type, file_hash):
                self.relative_path = relative_path
                self.mime_type = mime_type
                self.file_hash = file_hash
                self.original_filename = Path(relative_path).name

        candidates = candidates_from_stored_files(
            [Stored("shot.png", "image/png", HASH_A), Stored("notes.txt", "text/plain", HASH_B)],
            {"shot.png": "q2"},
        )
        self.assertEqual([(c.file_hash, c.question_id) for c in candidates], [(HASH_A, "q2")])


class ImageVariantTests(unittest.TestCase):
    def test_variant_is_smaller_jpeg_and_cached_by_source_hash(self):
        with tempfile.TemporaryDirectory(prefix="lanshare-variants-") as tmp:
            root = Path(tmp)
            source = root / "big.png"
            Image.new("RGBA", (2400, 1200), (255, 0, 0, 128)).save(source)
            with patch.object(file_service, "GLOBAL_FILES_DIR", root / "global"), \
                    patch.object(file_service, "GLOBAL_FILES_LEGACY_DIRS", ()):
                first = asyncio.run(submission_image_variants.resolve_submission_image_variant(source, "", "thumb"))
                self.assertIsNotNone(first)
                with Image.open(first) as thumb:
                    self.assertEqual(thumb.format, "JPEG")
                    self.assertLessEqual(max(thumb.size), 360)
                    self.assertEqual(thumb.mode, "RGB")
                self.assertLess(first.stat().st_size, source.stat().st_size)
                second = asyncio.run(submission_image_variants.resolve_submission_image_variant(source, "", "thumb"))
                self.assertEqual(first, second)
                preview = asyncio.run(submission_image_variants.resolve_submission_image_variant(source, "", "preview"))
                with Image.open(preview) as big:
                    self.assertLessEqual(max(big.size), 1600)
                    self.assertGreater(max(big.size), 360)

    def test_corrupt_image_falls_back_to_none(self):
        with tempfile.TemporaryDirectory(prefix="lanshare-variants-") as tmp:
            root = Path(tmp)
            broken = root / "broken.png"
            broken.write_bytes(b"not an image")
            with patch.object(file_service, "GLOBAL_FILES_DIR", root / "global"), \
                    patch.object(file_service, "GLOBAL_FILES_LEGACY_DIRS", ()):
                self.assertIsNone(asyncio.run(submission_image_variants.resolve_submission_image_variant(broken, "", "thumb")))

    def test_unknown_variant_normalizes_to_thumb(self):
        self.assertEqual(submission_image_variants.normalize_variant("huge"), "thumb")
        self.assertEqual(submission_image_variants.normalize_variant("PREVIEW"), "preview")
        binary = io.BytesIO()
        Image.new("RGB", (10, 10)).save(binary, format="PNG")
        self.assertTrue(submission_image_variants.build_variant_bytes_for_tests(binary.getvalue()).startswith(b"\xff\xd8"))


if __name__ == "__main__":
    unittest.main()
