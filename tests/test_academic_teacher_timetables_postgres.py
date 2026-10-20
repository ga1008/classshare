"""Same cache contracts against an explicitly isolated native PostgreSQL DB."""
import os
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch

from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
from tools.assessment_postgres_rehearsal import connect_offline
from tests import test_academic_teacher_timetables as fixture


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER") and os.environ.get("ASSESSMENT_REHEARSAL_TEST_PORT"),
                     "Explicit isolated loopback PostgreSQL cluster required")
class TeacherTimetablePostgresTests(fixture.TeacherTimetableTests):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Path(os.environ["ASSESSMENT_REHEARSAL_TEST_CLUSTER"])
        cls.port = int(os.environ["ASSESSMENT_REHEARSAL_TEST_PORT"])
        cls.database = "lanshare_assessment_rehearsal_teacher_timetable_" + uuid.uuid4().hex[:10]
        cls.admin = connect_offline(cluster_dir=cls.cluster, port=cls.port, database="lanshare_assessment_rehearsal")
        cls.admin.autocommit = True
        cls.admin.execute(f'CREATE DATABASE "{cls.database}" TEMPLATE template0')
        from classroom_app import config
        dialect = patch.object(config, "DB_ENGINE", "postgres")
        dialect.start()
        cls.addClassCleanup(dialect.stop)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.admin.execute(f'DROP DATABASE "{cls.database}"')
        finally:
            cls.admin.close()

    def open_fixture(self):
        raw = connect_offline(cluster_dir=self.cluster, port=self.port, database=self.database)
        raw.row_factory = sqlite_compatible_dict_row
        conn = LanSharePostgresConnection(raw)
        self.assertEqual(self.database, conn.execute("SELECT current_database()").fetchone()[0])
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        return conn


if __name__ == "__main__":
    unittest.main()
