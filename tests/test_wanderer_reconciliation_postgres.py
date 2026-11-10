"""Owned PostgreSQL races for historical command reconciliation, never production.

The platform boundary writes one synthetic native record and then delays its
return. Runtime finalization and administrator reconciliation use real services
and independent PostgreSQL connections throughout.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from fastapi import HTTPException

from classroom_app.services import wanderer_platform_service as platform
from classroom_app.services import wanderer_reconciliation as reconciliation
from classroom_app.services import wanderer_runtime_service as runtime
from classroom_app.services.agent_platform_route_capability import route_key
from classroom_app.time_utils import local_iso
from tests import test_wanderer_world_postgres as fixture


@unittest.skipUnless(os.environ.get('ASSESSMENT_REHEARSAL_TEST_CLUSTER'), 'Requires owned offline PostgreSQL')
class WandererReconciliationPostgresTest(unittest.TestCase):
    setUpClass = classmethod(fixture.WandererWorldPostgresTest.setUpClass.__func__)
    tearDownClass = classmethod(fixture.WandererWorldPostgresTest.tearDownClass.__func__)
    connect = fixture.WandererWorldPostgresTest.connect

    def setUp(self):
        fixture.WandererWorldPostgresTest.setUp(self)
        self.wid = 1
        self.user = {'role': 'teacher', 'id': 1}
        self.command = {'id': 'synthetic_write', 'capability': route_key('POST', '/api/synthetic-reconciliation-write'),
                        'args': {'body': {'name': 'one synthetic write'}}}
        with self.connect() as conn:
            conn.execute('CREATE TABLE teachers(id BIGINT PRIMARY KEY,is_super_admin INTEGER,is_active INTEGER)')
            conn.execute('INSERT INTO teachers VALUES(1,1,1)')
            conn.execute('CREATE TABLE synthetic_reconciliation_writes(operation_id TEXT PRIMARY KEY,created_at TEXT NOT NULL)')
            event = runtime.enqueue_wake_event(conn, self.wid, 'native_reconciliation_fixture', uuid.uuid4().hex)
            self.run = runtime._claim(conn, event['event_id'])
            conn.commit()
        self.operation_id = str(uuid.uuid5(uuid.NAMESPACE_URL,
            f"wanderer:{self.run['turn_id']}:{self.command['id']}"))
        self.observation = {'outcome': 'uncertain', 'reason': 'synthetic_response_interrupted_after_native_commit'}

    def _request(self, conn):
        stamp = int(time.time())
        values = {
            'id': str(uuid.uuid4()), 'operation_id': self.operation_id,
            'fencing_token': self.run['fencing_token'], 'actor_role': 'student', 'actor_id': self.wid,
            'authority_fingerprint': 'synthetic-authority', 'principal_kind': 'wanderer_service',
            'wanderer_id': self.wid, 'turn_id': self.run['turn_id'], 'life_epoch': self.run['life_epoch'],
            'authority_version': self.run['authority_version'], 'capability_key': self.command['capability'],
            'method': 'POST', 'path': '/api/synthetic-reconciliation-write',
            'route_source_sha256': 'synthetic-source', 'request_hash': 'synthetic-request',
            'intent_hash': self.operation_id, 'request_json': '{}', 'settlement_hash': 'synthetic-settlement',
            'mutates': 1, 'execution_host_id': 'owned-native-fixture', 'host_execution_finished_at': stamp,
            'status': 'uncertain', 'result_json': json.dumps(self.observation), 'created_at': stamp, 'updated_at': stamp,
        }
        conn.execute(f"INSERT INTO agent_platform_requests({','.join(values)}) VALUES({','.join('?' for _ in values)})",
                     tuple(values.values()))

    def _historical_command(self, *, completed=False):
        with self.connect() as conn:
            stamp = local_iso()
            packed = runtime.packed(self.command)
            conn.execute('INSERT INTO wanderer_commands(operation_id,turn_id,command_id,capability,args_hash,'
                         "command_json,state,receipt_json,created_at,updated_at) VALUES(?,?,?,?,?,?,'running','{}',?,?)",
                         (self.operation_id, self.run['turn_id'], self.command['id'], self.command['capability'],
                          hashlib.sha256(packed.encode()).hexdigest(), packed, stamp, stamp))
            self._request(conn)
            if completed:
                self.assertTrue(runtime._finish_in_connection(conn, self.run, 'completed'))
            conn.commit()

    def _view(self, conn):
        return reconciliation.get_command_reconciliation(conn, user=self.user, wanderer_id=self.wid,
            operation_id=self.operation_id)['request']

    def _save(self, conn, revision):
        return reconciliation.reconcile_command(conn, user=self.user, wanderer_id=self.wid,
            operation_id=self.operation_id, expected_revision=revision, outcome='occurred',
            reason='Administrator checked the single synthetic native write on an owned test database.')

    def _assert_audits(self, expected):
        with self.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM wanderer_admin_access "
                "WHERE section='command_reconciliation_write' AND selector=?", (self.operation_id,)).fetchone()[0], expected)

    def test_late_runtime_receipt_cannot_overwrite_admin_resolution(self):
        """Two actual connections interleave declaration before the delayed CAS."""
        async def scenario():
            entered, release = asyncio.Event(), asyncio.Event()
            backend_pids = []

            async def delayed_platform(**kwargs):
                self.assertEqual(kwargs['operation_id'], self.operation_id)
                with self.connect() as conn:
                    backend_pids.append(conn.execute('SELECT pg_backend_pid()').fetchone()[0])
                    conn.execute('INSERT INTO synthetic_reconciliation_writes VALUES(?,?)',
                                 (self.operation_id, local_iso()))
                    self._request(conn)
                    conn.commit()
                entered.set()
                await release.wait()
                return {'status': 'succeeded', 'verified_business': True, 'scope': {'kind': 'public'}}

            def declare_after_native_commit():
                with self.connect() as conn:
                    backend_pids.append(conn.execute('SELECT pg_backend_pid()').fetchone()[0])
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM synthetic_reconciliation_writes').fetchone()[0], 1)
                    # A recovered owner has ended the turn while its old caller
                    # still holds a late HTTP result; no command is replayed.
                    self.assertTrue(runtime._finish_in_connection(conn, self.run, 'completed'))
                    conn.commit()
                    view = self._view(conn)
                    self.assertTrue(view['can_reconcile'], view['block_reason'])
                    declared = self._save(conn, view['revision'])
                    conn.commit()
                    return declared

            operation = SimpleNamespace(method='POST', path='/api/synthetic-reconciliation-write', mutates=True)
            with patch.object(platform, '_app', return_value=object()), \
                    patch.object(platform, 'resolve_wanderer_capability', return_value=(operation, None)), \
                    patch.object(platform, 'execute_platform_command', delayed_platform), \
                    patch.object(runtime, '_platform_scope', return_value={'kind': 'public'}):
                running = asyncio.create_task(runtime.execute_command(self.connect, self.run, self.command))
                try:
                    await asyncio.wait_for(entered.wait(), 10)
                    declared = await asyncio.wait_for(asyncio.to_thread(declare_after_native_commit), 10)
                finally:
                    release.set()
                    late = await asyncio.wait_for(running, 10)
            self.assertEqual(len(set(backend_pids)), 2)
            self.assertEqual(declared['request']['state'], 'resolved')
            self.assertEqual(late['status'], 'resolved')
            self.assertFalse(late['verified_business'])
            self.assertFalse(late['automatic_retry_allowed'])
            with self.connect() as conn:
                row = conn.execute('SELECT state,receipt_json FROM wanderer_commands WHERE operation_id=?',
                                   (self.operation_id,)).fetchone()
                self.assertEqual(row['state'], 'resolved')
                self.assertEqual(json.loads(row['receipt_json']), late)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM synthetic_reconciliation_writes').fetchone()[0], 1)
                raw = conn.execute('SELECT status,result_json,settled_at FROM agent_platform_requests WHERE operation_id=?',
                                   (self.operation_id,)).fetchone()
                self.assertEqual(raw['status'], 'uncertain')
                self.assertEqual(json.loads(raw['result_json']), self.observation)
                self.assertIsNone(raw['settled_at'])
            self._assert_audits(1)

        asyncio.run(scenario())

    def test_completion_and_admin_check_do_not_invert_turn_lease_locks(self):
        """Admin waits on a turn held by actual completion, without owning its lease."""
        self._historical_command()
        with self.connect() as conn:
            old_revision = self._view(conn)['revision']
            conn.commit()
        turn_locked, admin_started, finish_allowed = threading.Event(), threading.Event(), threading.Event()
        pids = {}

        def finish():
            with self.connect() as conn:
                conn.execute("SET LOCAL statement_timeout='10s'")
                pids['finish'] = conn.execute('SELECT pg_backend_pid()').fetchone()[0]
                conn.execute('UPDATE wanderer_turns SET state=state WHERE turn_id=?', (self.run['turn_id'],))
                turn_locked.set()
                if not finish_allowed.wait(10):
                    raise AssertionError('owned completion was not released')
                self.assertTrue(runtime._finish_in_connection(conn, self.run, 'completed'))
                conn.commit()
                return 'completed'

        def declare():
            if not turn_locked.wait(10):
                raise AssertionError('completion did not acquire its turn')
            with self.connect() as conn:
                conn.execute("SET LOCAL statement_timeout='10s'")
                pids['admin'] = conn.execute('SELECT pg_backend_pid()').fetchone()[0]
                admin_started.set()
                try:
                    self._save(conn, old_revision)
                except HTTPException as error:
                    conn.rollback()
                    return error.status_code
                raise AssertionError('stale active-turn revision was accepted')

        with ThreadPoolExecutor(max_workers=2) as pool:
            completed = pool.submit(finish)
            declaration = pool.submit(declare)
            try:
                self.assertTrue(admin_started.wait(10))
                deadline = time.monotonic() + 3
                blocked_by_finish = False
                with self.connect() as conn:
                    while time.monotonic() < deadline:
                        blockers = conn.execute('SELECT pg_blocking_pids(?)', (pids['admin'],)).fetchone()[0]
                        if pids['finish'] in blockers:
                            blocked_by_finish = True
                            break
                        time.sleep(0.01)
                self.assertTrue(blocked_by_finish, 'admin must actually contend on the owned turn')
            finally:
                finish_allowed.set()
            self.assertEqual(completed.result(timeout=10), 'completed')
            self.assertEqual(declaration.result(timeout=10), 409)
        self._assert_audits(0)
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT state FROM wanderer_commands WHERE operation_id=?',
                (self.operation_id,)).fetchone()[0], 'running')
            self.assertEqual(conn.execute('SELECT state FROM wanderer_turns WHERE turn_id=?',
                (self.run['turn_id'],)).fetchone()[0], 'completed')

    def test_actual_heartbeat_renewal_blocks_stale_admin_declaration(self):
        self._historical_command(completed=True)
        with self.connect() as conn:
            view = self._view(conn)
            self.assertTrue(view['can_reconcile'])
            conn.commit()

        async def one_real_heartbeat():
            pulses = 0

            async def one_wait(_seconds):
                nonlocal pulses
                pulses += 1
                if pulses > 1:
                    raise asyncio.CancelledError

            # Exercise the actual heartbeat SQL exactly once without a 20s wait.
            runtime_asyncio = SimpleNamespace(**vars(asyncio))
            runtime_asyncio.sleep = one_wait
            with patch.object(runtime, 'asyncio', runtime_asyncio):
                with self.assertRaises(asyncio.CancelledError):
                    await runtime._heartbeat(self.connect, self.run)
            self.assertEqual(pulses, 2)

        asyncio.run(one_real_heartbeat())
        with self.connect() as conn:
            current = self._view(conn)
            self.assertFalse(current['can_reconcile'])
            with self.assertRaises(HTTPException) as refused:
                self._save(conn, view['revision'])
            self.assertEqual(refused.exception.status_code, 409)
            conn.rollback()
        self._assert_audits(0)
