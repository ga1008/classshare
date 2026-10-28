"""The same search ledger races on an explicitly owned, empty PostgreSQL DB."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import unittest
import uuid

from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
from classroom_app.db.schema_ai_search import ensure_ai_search_schema
from classroom_app.services import ai_search_quota_service as quota
from tests.test_ai_search_quota import QuotaContractMixin, NOW, FP
from tools.assessment_postgres_rehearsal import connect_offline


@unittest.skipUnless(os.environ.get('ASSESSMENT_REHEARSAL_TEST_CLUSTER'), 'Requires an explicitly owned offline PostgreSQL cluster')
class SearchQuotaPostgresTest(QuotaContractMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Path(os.environ['ASSESSMENT_REHEARSAL_TEST_CLUSTER'])
        cls.port = int(os.environ['ASSESSMENT_REHEARSAL_TEST_PORT'])
        cls.name = 'lanshare_assessment_rehearsal_search_' + uuid.uuid4().hex[:10]
        cls.admin = connect_offline(cluster_dir=cls.cluster, port=cls.port, database='lanshare_assessment_rehearsal')
        cls.admin.autocommit = True
        cls.admin.execute(f'CREATE DATABASE "{cls.name}" TEMPLATE template0')

    @classmethod
    def tearDownClass(cls):
        try:
            cls.admin.execute(f'DROP DATABASE "{cls.name}"')
        finally:
            cls.admin.close()

    def connect(self):
        raw = connect_offline(cluster_dir=self.cluster, port=self.port, database=self.name)
        raw.row_factory = sqlite_compatible_dict_row
        return LanSharePostgresConnection(raw)

    def setUp(self):
        with self.connect() as conn:
            conn.execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public')
            ensure_ai_search_schema(conn)
            conn.commit()

    def test_a_held_actor_bucket_does_not_block_an_unrelated_actor(self):
        other = self.transaction(quota.reserve, actor_kind='student',actor_id=2,channel='chat',
                                 logical_call_id='seed',fingerprint=FP,now=NOW)
        self.transaction(quota.release_unsent, other['call_id'],other['dispatch_token'],now=NOW)
        with self.connect() as held:
            quota.reserve(held,actor_kind='student',actor_id=1,channel='chat',logical_call_id='held',fingerprint=FP,now=NOW)
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(self.transaction, quota.reserve, actor_kind='student',actor_id=2,
                    channel='chat',logical_call_id='independent',fingerprint=FP,now=NOW)
                try:
                    self.assertEqual(pending.result(timeout=5)['state'], 'reserved')
                finally:
                    held.rollback()

    def test_snapshot_is_read_only_on_postgres(self):
        self.reserve(lease_seconds=5)
        with self.connect() as conn:
            conn.execute('SET TRANSACTION READ ONLY')
            self.assertEqual(quota.snapshot(conn, actor_kind='student',actor_id=1,channel='chat',now=NOW)['remaining'],9)
