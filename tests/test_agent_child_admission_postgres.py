"""Opt-in task admission races in an exclusively created synthetic PostgreSQL DB."""
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import unittest
import uuid

from fastapi import HTTPException

from classroom_app.db.schema_agent_children import ensure_agent_children_schema
from classroom_app.services import agent_child_admission_service as children
from classroom_app.services.agent_delegation_service import issue_task_delegation
from tests import test_agent_authority_postgres as fixture_module


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER"), "Requires explicit offline native PostgreSQL cluster")
class AgentChildAdmissionPostgresTests(unittest.TestCase):
    setUpClass = classmethod(fixture_module.AgentAuthorityPostgresTests.setUpClass.__func__)
    tearDownClass = classmethod(fixture_module.AgentAuthorityPostgresTests.tearDownClass.__func__)
    connection = fixture_module.AgentAuthorityPostgresTests.connection
    issue = fixture_module.AgentAuthorityPostgresTests.issue

    def setUp(self):
        fixture_module.AgentAuthorityPostgresTests.setUp(self)
        ensure_agent_children_schema(self.conn)
        self.token = issue_task_delegation(self.conn, task_id=10, attempt_id=self.attempt["id"], fencing_token=self.attempt["fencing_token"],
                                           purpose="tools", scopes=["platform:read"], source_session_id="teacher-session")["token"]
        self.conn.commit()
        self.parent = str(uuid.uuid4())

    def payload(self):
        return {"request_id": str(uuid.uuid4()), "parent_session_id": self.parent, "child_session_id": str(uuid.uuid4()), "depth": 1}

    def race(self, payloads):
        barrier = threading.Barrier(len(payloads))
        def call(payload):
            conn = self.connection()
            try:
                barrier.wait(timeout=5)
                result = children.admit_child(conn, self.token, **payload)
                conn.commit()
                return 200, result
            except HTTPException as error:
                conn.rollback()
                return error.status_code, None
            finally:
                conn.close()
        with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
            return list(pool.map(call, payloads))

    def test_native_six_concurrent_admissions_commit_exactly_four(self):
        results = self.race([self.payload() for _ in range(6)])
        self.assertEqual([200] * 4 + [429] * 2, sorted(item[0] for item in results))
        rows = self.conn.execute("SELECT ordinal FROM agent_task_children ORDER BY ordinal").fetchall()
        self.assertEqual([1, 2, 3, 4], [row[0] for row in rows])

    def test_native_same_request_is_one_row_under_concurrent_retry(self):
        payload = self.payload()
        results = self.race([payload] * 6)
        self.assertTrue(all(code == 200 for code, _ in results))
        self.assertEqual(1, len({value["id"] for _, value in results}))
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM agent_task_children").fetchone()[0])

    def test_native_runtime_finish_does_not_release_a_fifth_start(self):
        for _ in range(4):
            item = children.admit_child(self.conn, self.token, **self.payload())
            children.report_child_finish(self.conn, self.token, item["id"], status="completed")
            self.conn.commit()
        results = self.race([self.payload(), self.payload()])
        self.assertEqual([429, 429], [code for code, _ in results])
        before = [tuple(row) for row in self.conn.execute("SELECT * FROM agent_task_children ORDER BY ordinal")]
        ensure_agent_children_schema(self.conn)
        self.conn.commit()
        self.assertEqual(before, [tuple(row) for row in self.conn.execute("SELECT * FROM agent_task_children ORDER BY ordinal")])
