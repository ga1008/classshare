"""Live two-transaction tests against the explicitly selected local rehearsal cluster."""

import json
import os
import unittest
from unittest.mock import patch

from classroom_app.db.schema_project_topic_dispatch import ensure_project_topic_dispatch_schema
from classroom_app.db.schema_project_topic_packages import ensure_project_topic_packages_schema
from classroom_app.services import collaboration_service as groups
from classroom_app.services import project_topic_dispatch_service as dispatch
from tests import test_collaboration_membership_postgres as membership_pg


@unittest.skipUnless(os.environ.get("ASSESSMENT_REHEARSAL_TEST_CLUSTER"), "Requires explicit offline PostgreSQL cluster")
class DispatchPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the real classroom fixture and its pg_blocking_pids lock probe.
        # It creates its own database, on the identity-checked loopback cluster.
        cls.fixture_type = membership_pg.CollaborationMembershipPostgresTests
        cls.fixture_type.setUpClass()
        cls.addClassCleanup(cls.fixture_type.tearDownClass)

    def setUp(self):
        self.fixture = self.fixture_type()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.conn = self.fixture.conn
        self.teacher = self.fixture.teacher()
        for target in ("schema_project_topic_dispatch", "schema_project_topic_packages"):
            self.enterContext(patch(f"classroom_app.db.{target}.get_configured_db_engine", return_value="postgres"))
        ensure_project_topic_packages_schema(self.conn)
        ensure_project_topic_dispatch_schema(self.conn)
        self.conn.execute("""INSERT INTO project_topic_packages
            (id,teacher_id,package_key,title,version,fingerprint,mapping_confirmed,manifest_json,
             common_json,issues_json,common_issues_json,created_at)
            VALUES(1,1,'pg','Topics','1','fixture',1,'{"common":[]}','[]','[]','[]','2026-11-21')""")
        self.conn.executemany("INSERT INTO project_topic_items(package_id,topic_key,ordinal,item_json) VALUES(1,?,?,?)",
                              [(key, n, json.dumps({"key":key,"title":key,"number":str(n),"pool":"regular",
                                                    "readiness":"complete","entry":"","brief":key,"attachments":[]}))
                               for n,key in enumerate(("alpha","beta"), 1)])
        self.fixture.scheme()
        self.fixture.group(group_id=1, scheme=1, members=(7,))
        self.fixture.group(group_id=2, scheme=1, members=(8,))
        self.cfg = {"title":"Topic dispatch", "scheme_id":1, "package_version_id":1}
        self.token = dispatch.preview_dispatch(self.conn,1,self.teacher,self.cfg)["preview_token"]
        self.conn.commit()

    def publish(self, conn, key):
        return dispatch.publish_dispatch(conn, 1, self.teacher, {
            **self.cfg, "preview_token":self.token, "request_id":key,
        })

    def test_replay_waits_and_returns_original_bindings(self):
        a,b = self.fixture.race(lambda c:self.publish(c,"same"),lambda c:self.publish(c,"same"),
                                gate_sql="INSERT INTO project_topic_dispatches")
        self.assertEqual((a[0],b[0]), (200,200))
        self.assertEqual((a[1]["id"],a[1]["groups"]), (b[1]["id"],b[1]["groups"]))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM project_topic_bindings").fetchone()[0],2)

    def test_two_confirmations_of_one_preview_only_publish_once(self):
        a,b = self.fixture.race(lambda c:self.publish(c,"tab-a"),lambda c:self.publish(c,"tab-b"),
                                gate_sql="INSERT INTO project_topic_dispatches")
        self.assertEqual((a[0],b[0]), (200,409))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM project_topic_dispatches").fetchone()[0],1)

    def test_membership_change_invalidates_a_waiting_confirmation(self):
        a,b = self.fixture.race(lambda c:groups.teacher_assign_to_scheme_group(c,2,self.teacher,9),
                                lambda c:self.publish(c,"stale"))
        self.assertEqual((a[0],b[0]), (200,409))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM project_topic_bindings").fetchone()[0],0)

    def test_archive_freezes_membership_before_a_waiting_new_arrival(self):
        activity = self.publish(self.conn,"initial")
        self.conn.commit()
        a,b = self.fixture.race(
            lambda c:dispatch.update_dispatch(c,activity["id"],self.teacher,{"revision":1,"action":"archive"}),
            lambda c:groups.teacher_assign_to_scheme_group(c,2,self.teacher,9),
            gate_sql="UPDATE project_topic_dispatches SET state=",
        )
        self.assertEqual((a[0],b[0]), (200,200))
        own = dispatch.get_dispatch(self.conn,activity["id"],self.fixture.student(9))
        self.assertIsNone(own["my_topic"])
        self.assertIsNotNone(dispatch.get_dispatch(self.conn,activity["id"],self.fixture.student(8))["my_topic"])

    def test_concurrent_first_draft_different_configurations_keeps_first(self):
        def save(conn, title):
            return dispatch.save_dispatch_draft(conn, 1, self.teacher, 1,
                                                {"revision": 0, "payload": {"title": title}})

        a, b = self.fixture.race(lambda c: save(c, "First"), lambda c: save(c, "Second"),
                                 gate_sql="INSERT INTO project_topic_dispatch_drafts")
        self.assertEqual((a[0], b[0]), (200, 409))
        stored = dispatch.get_dispatch_draft(self.conn, 1, self.teacher, 1)
        self.assertEqual((stored["revision"], stored["payload"]["title"]), (1, "First"))

    def test_reader_straddling_archive_and_new_arrival_uses_the_frozen_entitlement(self):
        activity = self.publish(self.conn, "initial")
        self.conn.commit()
        access = groups.ensure_classroom_access

        def archive_and_add_after_initial_classroom_check(conn, offering, user):
            allowed = access(conn, offering, user)
            if conn is self.conn and user["role"] == "student":
                writer = self.fixture.connection()
                try:
                    dispatch.update_dispatch(writer, activity["id"], self.teacher, {"revision": 1, "action": "archive"})
                    groups.teacher_assign_to_scheme_group(writer, 2, self.teacher, 9)
                    writer.commit()
                finally:
                    writer.close()
            return allowed

        with patch.object(groups, "ensure_classroom_access", side_effect=archive_and_add_after_initial_classroom_check):
            view = dispatch.get_dispatch(self.conn, activity["id"], self.fixture.student(9))
        self.assertEqual(view["state"], "archived")
        self.assertIsNone(view["my_topic"])
