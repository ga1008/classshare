"""Native PostgreSQL proof that withdrawal shares the classroom merge lock."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
from uuid import uuid4

from fastapi import HTTPException
from classroom_app.services.grade_publication_service import withdraw_grade_publication


@unittest.skipUnless(os.environ.get("MP_PHASE1_POSTGRES_TEACHER_DSN"), "Requires an isolated PostgreSQL DSN")
class GradePublicationMergeLockTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict
        self.dsn = os.environ["MP_PHASE1_POSTGRES_TEACHER_DSN"]
        info = conninfo_to_dict(self.dsn)
        if info.get("host") != "127.0.0.1" or info.get("port") in {None, "5432"} or info.get("dbname") != "lanshare_miniapp_phase1":
            raise ValueError("Only the isolated local phase1 database is allowed")
        self.schema = "publication_merge_lock_" + uuid4().hex[:12]
        with psycopg.connect(self.dsn) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup_schema)
        with self.connection() as conn:
            conn.execute("CREATE TABLE courses(id INTEGER PRIMARY KEY,name TEXT)")
            conn.execute("CREATE TABLE academic_semesters(id INTEGER PRIMARY KEY,name TEXT)")
            conn.execute("CREATE TABLE class_offerings(id INTEGER PRIMARY KEY,course_id INTEGER,teacher_id INTEGER,semester_id INTEGER)")
            conn.execute("CREATE TABLE grade_publications(id INTEGER PRIMARY KEY,class_offering_id INTEGER,teacher_id INTEGER,status TEXT,withdrawn_at TEXT,withdrawn_by_teacher_id INTEGER,withdrawal_reason TEXT)")
            conn.execute("INSERT INTO courses VALUES(1,'Synthetic course')")
            conn.execute("INSERT INTO class_offerings VALUES(1,1,10,NULL),(2,1,10,NULL)")
            conn.execute("INSERT INTO grade_publications(id,class_offering_id,teacher_id,status) VALUES(1,1,10,'active')")

    def cleanup_schema(self):
        import psycopg
        from psycopg import sql
        with psycopg.connect(self.dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    @contextmanager
    def connection(self):
        import psycopg
        from psycopg import sql
        from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
        with psycopg.connect(self.dsn, row_factory=sqlite_compatible_dict_row) as raw:
            raw.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            raw.execute("SET lock_timeout TO '5s'")
            yield LanSharePostgresConnection(raw)

    def check_withdrawal(self, *, repoint):
        entered = Event()
        def withdraw():
            with self.connection() as conn:
                class Probe:
                    def execute(self, statement, params=()):
                        if statement.startswith("UPDATE class_offerings"):
                            entered.set()
                        return conn.execute(statement, params)
                try:
                    result = withdraw_grade_publication(Probe(), class_offering_id=1, teacher_id=10,
                                                        publication_id=1, reason="Synthetic review")
                    return result["status"]
                except HTTPException as error:
                    return error.status_code
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.connection() as merger:
                merger.execute("SELECT id FROM class_offerings WHERE id=1 FOR UPDATE")
                job = pool.submit(withdraw)
                try:
                    self.assertTrue(entered.wait(5))
                    # The withdrawal has reached the lock call but cannot
                    # change the snapshot while the merge holds this row.
                    from concurrent.futures import TimeoutError
                    with self.assertRaises(TimeoutError):
                        job.result(timeout=0.15)
                    self.assertEqual("active", merger.execute("SELECT status FROM grade_publications WHERE id=1").fetchone()[0])
                    if repoint:
                        merger.execute("UPDATE grade_publications SET class_offering_id=2 WHERE id=1")
                finally:
                    merger.commit()
            self.assertEqual(409 if repoint else "withdrawn", job.result(timeout=5))
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM grade_publications WHERE id=1").fetchone()
            self.assertEqual("active" if repoint else "withdrawn", row["status"])
            self.assertEqual(2 if repoint else 1, row["class_offering_id"])

    def test_withdrawal_waits_for_merge_snapshot_lock(self):
        self.check_withdrawal(repoint=False)

    def test_withdrawal_rechecks_scope_after_merge_repoints_publication(self):
        self.check_withdrawal(repoint=True)


if __name__ == "__main__":
    unittest.main()
