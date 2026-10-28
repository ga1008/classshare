"""Startup lock ownership, cleanup and reentry without database connections."""
from unittest.mock import Mock, patch
import unittest

from classroom_app.db import startup_lock as startup


class FakeConnection:
    def __init__(self, fail_on=None):
        self.events = []
        self.fail_on = fail_on
        self.closed = False

    def execute(self, sql, params=None):
        self.events.append(('execute', sql, params))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError('synthetic startup connection failure')
        return self

    def fetchone(self):
        return (True,)

    def commit(self):
        self.events.append(('commit',))

    def rollback(self):
        self.events.append(('rollback',))

    def close(self):
        self.closed = True
        self.events.append(('close',))


class StartupLockTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(startup,'get_configured_db_engine',return_value='postgres'))

    def test_success_keeps_session_lock_across_commit_and_explicitly_releases_it(self):
        conn = FakeConnection()
        with startup.postgres_schema_startup_guard(lambda: conn, timeout_ms=2000) as yielded:
            self.assertIsNone(yielded)
            conn.events.append(('body',))
        events = conn.events
        lock_index = next(i for i,e in enumerate(events) if e[0]=='execute' and 'pg_advisory_lock(' in e[1])
        body_index = events.index(('body',))
        unlock_index = next(i for i,e in enumerate(events) if e[0]=='execute' and 'pg_advisory_unlock(' in e[1])
        self.assertLess(lock_index, body_index)
        self.assertIn(('commit',), events[lock_index+1:body_index])
        self.assertIn(('rollback',), events[body_index+1:unlock_index])
        self.assertEqual(events[unlock_index][2], startup.STARTUP_LOCK_KEY)
        self.assertEqual(events[-2:], [('commit',),('close',)])
        self.assertEqual(events[0][2], ('2000ms','3000ms'))

    def test_body_failure_releases_and_closes_then_next_call_can_initialize(self):
        first = FakeConnection()
        with self.assertRaisesRegex(ValueError,'original initialization error'):
            with startup.postgres_schema_startup_guard(lambda: first):
                raise ValueError('original initialization error')
        self.assertTrue(first.closed)
        self.assertTrue(any(e[0]=='execute' and 'pg_advisory_unlock' in e[1] for e in first.events))
        second = FakeConnection()
        with startup.postgres_schema_startup_guard(lambda: second):
            pass
        self.assertTrue(second.closed)

    def test_acquisition_error_closes_physical_connection_and_does_not_run_body(self):
        conn = FakeConnection(fail_on='pg_advisory_lock(')
        body = Mock()
        with self.assertRaisesRegex(RuntimeError,'synthetic'):
            with startup.postgres_schema_startup_guard(lambda: conn):
                body()
        body.assert_not_called()
        self.assertTrue(conn.closed)
        self.assertFalse(any(e[0]=='execute' and 'pg_advisory_unlock' in e[1] for e in conn.events))

    def test_unlock_failure_still_physically_closes_the_session(self):
        conn = FakeConnection(fail_on='pg_advisory_unlock(')
        with self.assertRaisesRegex(RuntimeError,'synthetic'):
            with startup.postgres_schema_startup_guard(lambda: conn):
                pass
        self.assertTrue(conn.closed)
        self.assertEqual(conn.events[-1],('close',))

    def test_nested_reentry_uses_only_one_session_and_one_lock(self):
        conn = FakeConnection()
        nested_factory = Mock(side_effect=AssertionError('must not connect during reentry'))
        with startup.postgres_schema_startup_guard(lambda: conn):
            with startup.postgres_schema_startup_guard(nested_factory):
                pass
            self.assertFalse(conn.closed)
        nested_factory.assert_not_called()
        self.assertEqual(sum(e[0]=='execute' and 'pg_advisory_lock(' in e[1] for e in conn.events),1)
        self.assertEqual(sum(e[0]=='execute' and 'pg_advisory_unlock(' in e[1] for e in conn.events),1)

    def test_default_factory_explicitly_bypasses_the_business_connection_pool(self):
        conn = FakeConnection()
        with patch.object(startup,'connect_postgres',return_value=conn) as connect:
            with startup.postgres_schema_startup_guard():
                pass
        connect.assert_called_once_with(row_factory=startup.sqlite_compatible_dict_row)

    def test_sqlite_does_not_connect_to_postgres(self):
        with patch.object(startup,'get_configured_db_engine',return_value='sqlite'), patch.object(startup,'connect_postgres') as connect:
            factory = Mock(side_effect=AssertionError('must not use PG for SQLite'))
            with startup.postgres_schema_startup_guard(factory):
                pass
            factory.assert_not_called()
            connect.assert_not_called()

    def test_invalid_timeouts_fail_before_any_connection_is_opened(self):
        for value in (True,False,None,'2000',0,999,300001,1.5):
            with self.subTest(value=value), patch.object(startup,'connect_postgres') as connect:
                with self.assertRaises(ValueError):
                    with startup.postgres_schema_startup_guard(timeout_ms=value):
                        pass
                connect.assert_not_called()


if __name__=='__main__':
    unittest.main()
