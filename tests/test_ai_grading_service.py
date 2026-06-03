import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from classroom_app.services.ai_grading_service import expire_stale_ai_grading_submissions


class AIGradingServiceTests(unittest.TestCase):
    def test_expire_stale_grading_can_be_scoped_to_assignment_ids(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            old_time = (datetime.now() - timedelta(hours=8)).isoformat()
            try:
                conn.execute(
                    """
                    CREATE TABLE submissions (
                        id INTEGER PRIMARY KEY,
                        assignment_id TEXT,
                        student_pk_id INTEGER,
                        status TEXT,
                        grading_started_at TEXT,
                        submitted_at TEXT,
                        feedback_md TEXT,
                        grading_attempt_fingerprint TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE learning_stage_exam_attempts (
                        id INTEGER PRIMARY KEY,
                        assignment_id TEXT,
                        student_id INTEGER,
                        status TEXT,
                        class_offering_id INTEGER,
                        stage_key TEXT,
                        ai_error TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE learning_stage_status (
                        id INTEGER PRIMARY KEY,
                        class_offering_id INTEGER,
                        student_id INTEGER,
                        stage_key TEXT,
                        status TEXT,
                        last_calculated_at TEXT
                    )
                    """
                )
                conn.executemany(
                    """
                    INSERT INTO submissions (
                        id, assignment_id, student_pk_id, status, grading_started_at,
                        submitted_at, feedback_md, grading_attempt_fingerprint
                    ) VALUES (?, ?, ?, 'grading', ?, ?, '', 'fp')
                    """,
                    [
                        (1, "assignment-1", 101, old_time, old_time),
                        (2, "assignment-2", 102, old_time, old_time),
                    ],
                )
                conn.commit()

                with patch(
                    "classroom_app.services.message_center_service.create_teacher_grading_issue_notification",
                    lambda *args, **kwargs: None,
                ):
                    expired_count = expire_stale_ai_grading_submissions(
                        conn,
                        stale_minutes=240,
                        assignment_ids=["assignment-1"],
                    )
                conn.commit()

                rows = {
                    row["assignment_id"]: row["status"]
                    for row in conn.execute(
                        "SELECT assignment_id, status FROM submissions ORDER BY assignment_id"
                    ).fetchall()
                }
            finally:
                conn.close()

            self.assertEqual(expired_count, 1)
            self.assertEqual(rows["assignment-1"], "grading_failed")
            self.assertEqual(rows["assignment-2"], "grading")
        finally:
            try:
                os.remove(db_path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
