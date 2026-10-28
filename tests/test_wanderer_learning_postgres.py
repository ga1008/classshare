"""Learning races on the explicitly owned native rehearsal cluster, never a configured business DB."""
import os
import unittest
from unittest.mock import patch

from classroom_app.db import schema_wanderers
from classroom_app.services import wanderer_learning_service as learning
from tests.test_wanderer_inbox_postgres import WandererInboxPostgresTest as Fixture


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER"), "Requires owned offline PostgreSQL")
class WandererLearningPostgresTest(unittest.TestCase):
    setUpClass = classmethod(Fixture.setUpClass.__func__)
    tearDownClass = classmethod(Fixture.tearDownClass.__func__)
    connect = Fixture.connect
    race = Fixture.race

    def setUp(self):
        Fixture.setUp(self)
        self.wanderer = {"id": 1, "student_id": 1}
        # Authorization/source withdrawal is exercised end-to-end in SQLite;
        # this minimal native fixture isolates the real transaction/unique races.
        self.enterContext(patch.object(learning, "_access", return_value=True))
        with self.connect() as conn:
            conn.execute("CREATE TABLE social_messages (id INTEGER PRIMARY KEY,body TEXT,sender_identity TEXT,"
                         "recalled_at TEXT,message_type TEXT)")
            conn.execute("INSERT INTO social_messages VALUES (1,'回答简短一点','student:2',NULL,'text'),"
                         "(2,'作业题请说明推导','student:2',NULL,'text'),"
                         "(3,'先用一个具体例子','student:2',NULL,'text')")
            conn.execute("CREATE TABLE blog_posts (id INTEGER PRIMARY KEY,content_md TEXT,author_identity TEXT,"
                         "status TEXT,visibility TEXT,moderation_state TEXT)")
            conn.execute("INSERT INTO blog_posts VALUES (1,'Python 列表有序。','teacher:2','published','public','none')")
            conn.commit()

    def package(self, source_id=1, *, replace=None, content="这位好友希望先给简短结论。", key="social:dm:1|2"):
        with self.connect() as conn:
            row = dict(conn.execute("SELECT * FROM social_messages WHERE id=?", (source_id,)).fetchone())
            source = learning._source(conn, "social_messages", row, self.wanderer)
        return {"snapshot": {"thread_key": key, "evidence": [source], "replace_ids": [replace] if replace else []},
                "candidates": [{"kind": "preference", "topic": "回复习惯", "content": content,
                                "evidence_ids": [source["id"]], "replace_id": replace}]}

    def apply(self, package):
        with self.connect() as conn:
            result = learning.apply_learning(conn, self.wanderer, package)
            conn.commit()
        return result

    def test_parallel_same_evidence_is_saved_once(self):
        package = self.package()
        results = self.race(6, lambda _: self.apply(package))
        self.assertEqual(sum(r["saved"] for r in results), 1)
        self.assertEqual(sum(r["duplicate"] for r in results), 5)

    def test_parallel_corrections_cannot_revive_the_losing_snapshot(self):
        self.apply(self.package())
        with self.connect() as conn:
            old = int(conn.execute("SELECT id FROM wanderer_experience_notes").fetchone()[0])
        packages = [self.package(2, replace=old, content="作业题先说明推导。"),
                    self.package(3, replace=old, content="复杂问题先举例子。")]
        results = self.race(2, lambda i: self.apply(packages[i]))
        self.assertEqual(sum(r["saved"] for r in results), 1)
        self.assertEqual(sum(r["rejected"] for r in results), 1)
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_experience_notes WHERE state='active'").fetchone()[0], 1)

    def test_private_knowledge_stays_scoped_and_never_changes_cultivation(self):
        package = self.package()
        package["candidates"][0].update(kind="knowledge", topic="Python 列表", content="Python 列表保留插入顺序。")
        self.apply(package)
        with self.connect() as conn:
            row = conn.execute("SELECT scope_key FROM wanderer_experience_notes").fetchone()
            self.assertEqual(row["scope_key"], package["snapshot"]["thread_key"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_knowledge_entries").fetchone()[0], 0)
            source = learning._source(conn, "blog_posts", dict(conn.execute("SELECT * FROM blog_posts").fetchone()),
                                      self.wanderer, public=True)
        public = {"snapshot": {"thread_key": "blog_post:1", "evidence": [source], "replace_ids": []},
                  "candidates": [{"kind": "knowledge", "topic": "Python 列表", "content": "Python 列表保留插入顺序。",
                                  "evidence_ids": [source["id"]]}]}
        self.apply(public)
        with self.connect() as conn:
            scopes = {r["scope_key"] for r in conn.execute("SELECT scope_key FROM wanderer_experience_notes")}
        self.assertEqual(scopes, {"social:dm:1|2", "public"})

    def test_old_schema_gains_learning_table_idempotently_without_changing_memory(self):
        with self.connect() as conn:
            conn.execute("INSERT INTO wanderer_memory_threads (wanderer_id,thread_key,summary) VALUES (1,'dm:2','原有记忆')")
            conn.execute("DROP TABLE wanderer_experience_notes")
            conn.commit()
        for _ in range(2):
            with patch.object(schema_wanderers, "_SCHEMA_READY", False), self.connect() as conn:
                schema_wanderers.ensure_wanderer_schema(conn, engine="postgres")
                conn.commit()
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT summary FROM wanderer_memory_threads").fetchone()[0], "原有记忆")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_experience_notes").fetchone()[0], 0)

    def test_failed_audit_sql_does_not_poison_enclosing_transaction(self):
        package = self.package()
        def fail_log(conn, *_args, **_kwargs):
            conn.execute("SELECT * FROM intentionally_missing_learning_audit_table")
        with self.connect() as conn, patch.object(learning, "log_wanderer_action", side_effect=fail_log):
            result = learning.apply_learning_safely(conn, self.wanderer, package)
            self.assertTrue(result["failed"])
            # Real PostgreSQL errors abort the transaction until the savepoint
            # rollback. Simulate subsequent reply persistence in the same tx.
            conn.execute("INSERT INTO social_messages VALUES (4,'正常回复仍提交','student:1',NULL,'text')")
            conn.commit()
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT body FROM social_messages WHERE id=4").fetchone()[0], "正常回复仍提交")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_experience_notes").fetchone()[0], 0)
