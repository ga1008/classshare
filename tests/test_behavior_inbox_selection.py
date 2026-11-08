"""Actor-head selection preserves pending order and transactional visibility."""
from datetime import datetime, timedelta
import sqlite3
import unittest

from classroom_app.db.schema_behavior_inbox import ensure_behavior_inbox_schema
from classroom_app.services.behavior_inbox_service import BehaviorWritePipeline


class BehaviorInboxSelectionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        ensure_behavior_inbox_schema(self.conn, engine="sqlite")
        self.pipeline = BehaviorWritePipeline(lambda *_args, **_kwargs: {})
        self.timestamp = "2026-01-01T00:00:00.000000"

    def insert(self, key, actor, *, role="student", classroom=1, retry=None, state="pending"):
        self.conn.execute("""INSERT INTO behavior_write_inbox
            (batch_key,batch_id,payload_hash,class_offering_id,user_pk,user_role,
             display_name,events_json,received_at,retry_at,state)
            VALUES(?,?,?,?,?,?,'Synthetic','[]',?,?,?)""",
            (key, key, "hash", classroom, actor, role, self.timestamp, retry, state))

    def select(self):
        return self.pipeline._select_next_receipt(self.conn, postgres=False)

    def test_future_head_blocks_same_actor_across_classrooms_but_not_other_role(self):
        future = (datetime.now() + timedelta(days=1)).isoformat()
        self.insert("a", 10, classroom=1, retry=future)
        self.insert("b", 10, classroom=2)
        self.insert("c", 10, role="teacher")
        self.insert("d", 20)
        self.assertEqual("c", self.select()["batch_key"])
        self.conn.execute("UPDATE behavior_write_inbox SET state='applied' WHERE batch_key='c'")
        self.assertEqual("d", self.select()["batch_key"])
        self.conn.execute("UPDATE behavior_write_inbox SET retry_at=NULL WHERE batch_key='a'")
        self.assertEqual("a", self.select()["batch_key"])

    def test_ties_use_batch_key_and_new_head_precedes_later_other_actor(self):
        for key, actor in (("c", 20), ("b", 10), ("a", 10)):
            self.insert(key, actor)
        self.assertEqual("a", self.select()["batch_key"])
        self.conn.execute("UPDATE behavior_write_inbox SET state='applied' WHERE batch_key='a'")
        self.assertEqual("b", self.select()["batch_key"])
        self.conn.execute("UPDATE behavior_write_inbox SET state='applied' WHERE batch_key='b'")
        self.assertEqual("c", self.select()["batch_key"])

    def test_rollback_restores_head_and_quarantine_does_not_block_later_work(self):
        self.insert("a", 10)
        self.insert("b", 10)
        self.conn.commit()
        self.conn.execute("UPDATE behavior_write_inbox SET state='applied' WHERE batch_key='a'")
        self.assertEqual("b", self.select()["batch_key"])
        self.conn.rollback()
        self.assertEqual("a", self.select()["batch_key"])
        self.conn.execute("UPDATE behavior_write_inbox SET state='quarantined' WHERE batch_key='a'")
        self.assertEqual("b", self.select()["batch_key"])


if __name__ == "__main__":
    unittest.main()
