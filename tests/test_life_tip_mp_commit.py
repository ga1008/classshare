"""Real MP authentication and SQLite WAL feedback/cache commit interleavings."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from classroom_app import database
from classroom_app.db.connection import execute_insert_returning_id
from classroom_app.routers.mp import life_tips as route
from classroom_app.services import life_tip_service as tips, wechat_mp_service as mp
from tests.sqlite_database_fixture import isolated_sqlite_database


class MiniProgramTipCommitTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(isolated_sqlite_database())
        for name, value in (("_seeded", False), ("_ready_db_key", None), ("_pool_cache", {}),
                            ("_pool_generation", 0), ("_seed_lock", threading.Lock()),
                            ("_pool_fill_lock", threading.Lock())):
            self.stack.enter_context(patch.object(tips, name, value))
        with database.get_db_connection() as conn:
            self.assertEqual(conn.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
            self.teacher = execute_insert_returning_id(conn, "INSERT INTO teachers(name,email,hashed_password) VALUES('Synthetic teacher','mp-tip@example.invalid','unused')", ())
            classroom = execute_insert_returning_id(conn, "INSERT INTO classes(name,created_by_teacher_id) VALUES('Owned MP class',?)", (self.teacher,))
            self.student = execute_insert_returning_id(conn, "INSERT INTO students(student_id_number,name,class_id,school_code,department) VALUES('MP-TIP-SYNTHETIC','Synthetic student',?,'owned','computing')", (classroom,))
            self.tokens = {}
            for role, user in (("student", self.student), ("teacher", self.teacher)):
                openid = "owned-mp-tip-" + role
                mp.create_binding(conn, user_role=role, user_pk=user, openid=openid)
                self.tokens[role] = mp.issue_mp_session(conn, user_role=role, user_pk=user, openid=openid)
            conn.commit()
        tips.warm_life_tip_runtime()
        self.scope = {"school_code": "owned", "department": "computing", "audience_role": "student"}
        self.tip = tips._get_committed_pool(**self.scope)[0]["id"]
        self.app = FastAPI()
        self.app.include_router(route.router, prefix="/api/mp")
        self.assertFalse(self.app.dependency_overrides, "MP authentication must run normally")
        self.client = self.stack.enter_context(TestClient(self.app))

    def post(self, role="student", verdict=-1, *, token=None):
        return self.client.post("/api/mp/life-tips/feedback", headers={"Authorization": "Bearer " + (token or self.tokens[role])},
                                json={"tip_id": self.tip, "verdict": verdict})

    def test_feedback_commit_evicts_old_pool_refilled_between_invalidation_and_commit(self):
        before_commit, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        real = route.get_db_connection
        class GatedCommit:
            def __init__(self): self.raw = real()
            def __getattr__(self, name): return getattr(self.raw, name)
            def __enter__(self): self.raw.__enter__(); return self
            def __exit__(self, *exc): return self.raw.__exit__(*exc)
            def commit(self):
                before_commit.set()
                if not release.wait(5): raise AssertionError("MP commit gate was not released")
                self.raw.commit()
        # Only the feedback transaction's factory is observed. The dependency
        # uses the real MP bearer/session/binding/account checks and real DB.
        with patch.object(route, "get_db_connection", GatedCommit), ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(self.post)
            try:
                self.assertTrue(before_commit.wait(3))
                self.assertEqual(tips._pool_cache, {})
                stale = tips._get_committed_pool(**self.scope)
                self.assertEqual(next(item["weight"] for item in stale if item["id"] == self.tip), 1)
                self.assertTrue(tips._pool_cache, "The old committed snapshot must really refill the shared cache")
                generation = tips._pool_generation
            finally: release.set()
            response = pending.result(timeout=3)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"success": True, "data": {"tip_id": self.tip, "weight": 0, "votes": 1}, "error": None})
        cache_after_commit = dict(tips._pool_cache)
        self.assertNotIn(self.tip, {item["id"] for item in tips._get_committed_pool(**self.scope)})
        self.assertGreater(tips._pool_generation, generation)
        self.assertEqual(cache_after_commit, {}, "A pre-commit reader cannot keep the old pool after MP commit")
        with database.get_db_connection() as conn:
            row = conn.execute("SELECT user_role,user_pk,verdict FROM life_tip_feedback WHERE tip_id=?", (self.tip,)).fetchone()
            self.assertEqual(tuple(row), ("student", self.student, -1))

    def test_real_student_teacher_sessions_verdict_updates_and_revocation_keep_original_json_contract(self):
        for role, verdict, weight, votes in (("student", 0, 2, 1), ("teacher", -99, 1, 2), ("student", -1, 0, 2)):
            response = self.post(role, verdict)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {"success": True, "data": {"tip_id": self.tip, "weight": weight, "votes": votes}, "error": None})
        with database.get_db_connection() as conn:
            rows = conn.execute("SELECT user_role,user_pk,verdict FROM life_tip_feedback ORDER BY user_role").fetchall()
            self.assertEqual([tuple(row) for row in rows], [("student", self.student, -1), ("teacher", self.teacher, -1)])
            self.assertTrue(mp.revoke_mp_session(conn, self.tokens["student"]))
            conn.commit()
        self.assertEqual(self.post().status_code, 401)
        self.assertEqual(self.post(token="not-a-real-mp-session").status_code, 401)
        self.assertEqual(self.client.post("/api/mp/life-tips/feedback", json={"tip_id": self.tip, "verdict": 1}).status_code, 401)
        with database.get_db_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM life_tip_feedback").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT weight FROM life_tips WHERE id=?", (self.tip,)).fetchone()[0], 0)


if __name__ == "__main__": unittest.main()
