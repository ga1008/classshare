"""Real startup session locking against an explicitly owned offline PG cluster."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from classroom_app import config
from classroom_app.db import postgres, startup_lock as startup
from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
from tools.assessment_postgres_rehearsal import connect_offline


@unittest.skipUnless(os.environ.get('ASSESSMENT_REHEARSAL_TEST_CLUSTER'), 'Requires explicitly owned offline PostgreSQL')
class StartupLockPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cluster = Path(os.environ['ASSESSMENT_REHEARSAL_TEST_CLUSTER'])
        cls.port = int(os.environ['ASSESSMENT_REHEARSAL_TEST_PORT'])
        cls.name = 'lanshare_assessment_rehearsal_startup_' + uuid.uuid4().hex[:10]
        cls.admin = connect_offline(cluster_dir=cls.cluster,port=cls.port,database='lanshare_assessment_rehearsal')
        cls.admin.autocommit = True
        cls.admin.execute(f'CREATE DATABASE "{cls.name}" TEMPLATE template0')

    @classmethod
    def tearDownClass(cls):
        try:
            cls.admin.execute(f'DROP DATABASE "{cls.name}"')
        finally:
            cls.admin.close()

    def setUp(self):
        self.enterContext(patch.object(startup,'get_configured_db_engine',return_value='postgres'))

    def connect(self):
        raw = connect_offline(cluster_dir=self.cluster,port=self.port,database=self.name)
        raw.row_factory = sqlite_compatible_dict_row
        return LanSharePostgresConnection(raw)

    def try_lock(self):
        with self.connect() as conn:
            got = bool(conn.execute('SELECT pg_try_advisory_lock(?,?) AS locked',startup.STARTUP_LOCK_KEY).fetchone()['locked'])
            if got:
                conn.execute('SELECT pg_advisory_unlock(?,?)',startup.STARTUP_LOCK_KEY)
            return got

    def test_session_lock_survives_internal_business_commits_then_releases(self):
        with startup.postgres_schema_startup_guard(self.connect):
            self.assertFalse(self.try_lock())
            with self.connect() as business:
                business.execute('CREATE TABLE IF NOT EXISTS startup_synthetic_probe(value INTEGER)')
                business.commit()
            self.assertFalse(self.try_lock())
        self.assertTrue(self.try_lock())

    def test_failure_releases_the_real_session_lock(self):
        with self.assertRaisesRegex(RuntimeError,'synthetic body failure'):
            with startup.postgres_schema_startup_guard(self.connect):
                self.assertFalse(self.try_lock())
                raise RuntimeError('synthetic body failure')
        self.assertTrue(self.try_lock())

    def test_independent_session_blocks_at_advisory_lock_before_domain_work(self):
        ready, entered = threading.Event(), threading.Event()
        def initialize():
            ready.set()
            with startup.postgres_schema_startup_guard(self.connect,timeout_ms=5000):
                entered.set()
        with self.connect() as holder:
            holder.execute('SELECT pg_advisory_lock(?,?)',startup.STARTUP_LOCK_KEY)
            holder.commit()
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(initialize)
                try:
                    self.assertTrue(ready.wait(2))
                    waiting = False
                    deadline = time.monotonic()+3
                    while time.monotonic()<deadline:
                        with self.connect() as observer:
                            row = observer.execute("SELECT COUNT(*) AS n FROM pg_locks WHERE locktype='advisory' "
                                "AND classid=? AND objid=? AND objsubid=2 AND NOT granted "
                                "AND database=(SELECT oid FROM pg_database WHERE datname=current_database())",startup.STARTUP_LOCK_KEY).fetchone()
                            waiting = row['n']>0
                        if waiting:
                            break
                        time.sleep(.025)
                    self.assertTrue(waiting,'Initializer must wait on the DB guard, not touch domain tables')
                    self.assertFalse(entered.is_set())
                finally:
                    holder.execute('SELECT pg_advisory_unlock(?,?)',startup.STARTUP_LOCK_KEY)
                    holder.commit()
                pending.result(timeout=5)
        self.assertTrue(entered.is_set())
        self.assertTrue(self.try_lock())

    def test_timeout_does_not_leave_a_session_or_local_reentry_flag_behind(self):
        with self.connect() as holder:
            holder.execute('SELECT pg_advisory_lock(?,?)',startup.STARTUP_LOCK_KEY)
            holder.commit()
            with self.assertRaises(Exception) as caught:
                with startup.postgres_schema_startup_guard(self.connect,timeout_ms=1000):
                    self.fail('Should not enter while another session owns startup')
            self.assertEqual(getattr(caught.exception,'sqlstate',None),'55P03')
            holder.execute('SELECT pg_advisory_unlock(?,?)',startup.STARTUP_LOCK_KEY)
            holder.commit()
        with startup.postgres_schema_startup_guard(self.connect):
            self.assertFalse(self.try_lock())
        self.assertTrue(self.try_lock())

    def test_unpooled_guard_leaves_a_size_one_business_pool_available(self):
        # The target was validated through connect_offline; the application DSN
        # is replaced explicitly and never discovered from environment secrets.
        settings = {'DATABASE_URL':f'postgresql://rehearsal_admin@127.0.0.1:{self.port}/{self.name}',
            'POSTGRES_POOL_ENABLED':True,'POSTGRES_POOL_MIN':1,'POSTGRES_POOL_MAX':1,
            'POSTGRES_POOL_CHECKOUT_TIMEOUT':2,'DB_ENGINE':'postgres','POSTGRES_BACKEND_READY':True}
        with patch.multiple(config,**settings), patch.object(postgres,'_connection_pool',None):
            try:
                with startup.postgres_schema_startup_guard(timeout_ms=5000):
                    with postgres.connect_postgres() as business:
                        self.assertEqual(business.execute('SELECT 42 AS value').fetchone()['value'],42)
                    self.assertEqual(postgres.get_pool_stats()['max'],1)
                    self.assertFalse(self.try_lock())
            finally:
                postgres.close_connection_pool()
        self.assertTrue(self.try_lock())


if __name__=='__main__':
    unittest.main()
