"""Search budget contracts on independent, synthetic SQLite connections."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from classroom_app.db.schema_ai_search import ensure_ai_search_schema
from classroom_app.services import ai_search_quota_service as quota

NOW = datetime.fromisoformat('2026-10-28T23:59:30+08:00')
FP = hashlib.sha256(b'synthetic public question').hexdigest()


class SyntheticSQLite(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class QuotaContractMixin:
    def transaction(self, fn, *args, **kwargs):
        with self.connect() as conn:
            result = fn(conn, *args, **kwargs)
            conn.commit()
            return result

    def reserve(self, name='first', **kwargs):
        return self.transaction(quota.reserve, actor_kind='student', actor_id=1, channel='chat',
            logical_call_id=name, fingerprint=FP, now=kwargs.pop('now', NOW), **kwargs)

    def send(self, receipt, **kwargs):
        return self.transaction(quota.mark_sent, receipt['call_id'], receipt['dispatch_token'],
                                now=kwargs.pop('now', NOW), **kwargs)

    def settle(self, receipt, outcome='succeeded', **kwargs):
        return self.transaction(quota.settle, receipt['call_id'], receipt['dispatch_token'],
            outcome=outcome, now=kwargs.pop('now', NOW), **kwargs)

    def state(self, **kwargs):
        return self.transaction(quota.snapshot, actor_kind='student', actor_id=1, channel='chat',
                                now=kwargs.pop('now', NOW), **kwargs)

    def race(self, count, fn):
        barrier = threading.Barrier(count)
        def run(index):
            barrier.wait(timeout=20)
            try:
                return fn(index)
            except HTTPException as error:
                return {'http_status': error.status_code}
        with ThreadPoolExecutor(max_workers=count) as pool:
            return list(pool.map(run, range(count)))

    def test_ten_admissions_across_independent_connections(self):
        results = self.race(20, lambda i: self.reserve(f'parallel-{i}'))
        self.assertEqual(sum(item.get('state') == 'reserved' for item in results), 10)
        self.assertEqual(sum(item.get('http_status') == 429 for item in results), 10)
        current = self.state()
        self.assertEqual((current['reserved'], current['remaining'], current['occupied']), (10, 0, 10))

    def test_duplicate_logical_operation_counts_once_and_hides_token_on_replay(self):
        results = self.race(12, lambda _: self.reserve('same-logical-call'))
        self.assertEqual(len({item['call_id'] for item in results}), 1)
        self.assertEqual(sum(not item['replay'] for item in results), 1)
        self.assertEqual(sum('dispatch_token' in item for item in results), 1)
        self.assertEqual(self.state()['reserved'], 1)

    def test_only_one_provider_dispatch_wins_the_cas(self):
        item = self.reserve()
        results = self.race(12, lambda _: self.send(item))
        self.assertEqual(sum(result['won'] for result in results), 1)
        self.assertEqual(self.state()['reserved'], 1)
        self.assertEqual({r['call']['state'] for r in results}, {'sent'})

    def test_fingerprint_conflict_and_wrong_dispatch_token_do_not_mutate(self):
        item = self.reserve()
        with self.assertRaises(HTTPException) as caught:
            self.transaction(quota.reserve, actor_kind='student', actor_id=1, channel='chat',
                logical_call_id='first', fingerprint='f'*64, now=NOW)
        self.assertEqual(caught.exception.status_code, 409)
        with self.assertRaises(HTTPException):
            self.transaction(quota.mark_sent, item['call_id'], 'wrong-token', now=NOW)
        self.assertEqual(self.state()['reserved'], 1)
        self.assertTrue(self.send(item)['won'])

    def test_repeated_settlement_and_conflicting_refund_preserve_success(self):
        item = self.reserve(); self.send(item)
        payload = {'results': [{'title': '合成公开网页', 'url': 'https://example.test/article', 'summary': '测试摘要'}]}
        results = self.race(8, lambda _: self.settle(item, result=payload))
        self.assertEqual(sum(not result['replay'] for result in results), 1)
        self.assertEqual(self.state()['used'], 1)
        self.assertEqual(self.state()['reserved'], 0)
        settled = self.settle(item, 'failed', error='http_500')
        self.assertEqual(settled['state'], 'succeeded')
        self.assertEqual(settled['result'], payload)
        replay = self.reserve()
        self.assertEqual(replay['result'], payload)
        self.assertEqual(replay['quota']['remaining'], 9)

    def test_empty_and_explicit_failure_refund_once_but_never_reexecute_same_id(self):
        for outcome in ('empty', 'failed'):
            item = self.reserve(outcome); self.send(item)
            self.assertEqual(self.settle(item, outcome)['state'], outcome)
            self.settle(item, outcome)
            replay = self.reserve(outcome)
            self.assertTrue(replay['replay'])
            self.assertNotIn('dispatch_token', replay)
        state = self.state()
        self.assertEqual((state['remaining'], state['empty'], state['failed']), (10, 1, 1))

    def test_unsent_release_does_not_cancel_work_already_sent(self):
        unsent = self.reserve('unsent')
        self.transaction(quota.release_unsent, unsent['call_id'], unsent['dispatch_token'], now=NOW)
        self.assertFalse(self.send(unsent)['won'])
        sent = self.reserve('sent'); self.send(sent)
        released = self.transaction(quota.release_unsent, sent['call_id'], sent['dispatch_token'], now=NOW)
        self.assertEqual(released['state'], 'sent')
        self.assertEqual((self.state()['reserved'], self.state()['released']), (1, 1))

    def test_expired_unsent_projection_and_mutation_reclamation_match(self):
        item = self.reserve(lease_seconds=5)
        later = NOW + timedelta(seconds=6)
        self.assertEqual((self.state(now=later)['remaining'], self.state(now=later)['released']), (10, 1))
        # A read projection does not write or pretend the old request can dispatch.
        self.assertEqual(self.transaction(quota.get_call, item['call_id'])['state'], 'reserved')
        fresh = self.reserve('new-after-expiry', now=later)
        self.assertEqual(fresh['quota']['remaining'], 9)
        self.assertFalse(self.send(item, now=later)['won'])
        self.assertEqual(self.transaction(quota.get_call, item['call_id'])['state'], 'released')

    def test_expired_sent_is_unknown_occupancy_and_cannot_be_refunded_or_redispatched(self):
        item = self.reserve(lease_seconds=5); self.send(item, lease_seconds=5)
        later = NOW + timedelta(seconds=6)
        projected = self.state(now=later)
        self.assertEqual((projected['unknown'], projected['reserved'], projected['remaining']), (1, 0, 9))
        self.assertFalse(self.send(item, now=later)['won'])
        self.assertEqual(self.settle(item, 'failed', now=later)['state'], 'unknown')
        self.assertEqual(self.state(now=later)['remaining'], 9)

    def test_midnight_settlement_and_replay_stay_with_original_budget_day(self):
        item = self.reserve(); self.send(item)
        tomorrow = NOW + timedelta(minutes=1)
        self.settle(item, result={'results': []}, now=tomorrow)
        old = self.state(budget_date='2026-10-28', now=tomorrow)
        new = self.state(now=tomorrow)
        self.assertEqual((old['used'], old['remaining'], new['used'], new['remaining']), (1, 9, 0, 10))
        replay = self.reserve(now=tomorrow)
        self.assertEqual(replay['budget_date'], '2026-10-28')
        self.assertTrue(replay['replay'])
        self.assertEqual(self.reserve('new-day', now=tomorrow)['budget_date'], '2026-10-29')

    def test_same_logical_operation_crossing_midnight_still_reserves_once(self):
        results = self.race(12, lambda i: self.reserve('midnight-race', now=NOW + timedelta(minutes=i % 2)))
        self.assertEqual(len({item['call_id'] for item in results}), 1)
        self.assertEqual(sum(not item['replay'] for item in results), 1)
        total = self.state(budget_date='2026-10-28', now=NOW)['occupied'] + self.state(budget_date='2026-10-29', now=NOW)['occupied']
        self.assertEqual(total, 1)

    def test_role_and_channel_scopes_are_independent(self):
        for kind, pk, channel in [('student', 1, 'chat'), ('teacher', 1, 'chat'), ('teacher', 1, 'agent'), ('wanderer', 1, 'wanderer')]:
            self.transaction(quota.reserve, actor_kind=kind, actor_id=pk, channel=channel,
                             logical_call_id='shared-id', fingerprint=FP, now=NOW)
        for kind, channel in [('student','chat'), ('teacher','chat'), ('teacher','agent'), ('wanderer','wanderer')]:
            self.assertEqual(self.transaction(quota.snapshot, actor_kind=kind, actor_id=1, channel=channel, now=NOW)['remaining'], 9)
        for kind, channel in [('student','wanderer'), ('wanderer','chat'), ('wanderer','agent')]:
            with self.assertRaises(HTTPException):
                self.transaction(quota.reserve, actor_kind=kind, actor_id=1, channel=channel,
                                 logical_call_id='bad', fingerprint=FP, now=NOW)

    def test_unknown_network_outcome_uses_quota_and_does_not_leak_error_text(self):
        item = self.reserve(); self.send(item)
        result = self.settle(item, 'unknown', error='Authorization: Bearer private-key; personal prompt')
        self.assertEqual((result['state'], result['error_code']), ('unknown', 'search_failed'))
        self.assertEqual((result['quota']['used'], result['quota']['unknown'], result['quota']['remaining']), (0, 1, 9))
        self.assertNotIn('private-key', json.dumps(self.transaction(quota.get_call, item['call_id'])))

    def test_result_cache_is_bounded_by_utf8_bytes_and_exact_replay_is_preserved(self):
        item = self.reserve(); self.send(item)
        with self.assertRaises(HTTPException):
            self.settle(item, result={'body': '学' * 11000})
        self.assertEqual(self.transaction(quota.get_call, item['call_id'])['state'], 'sent')
        payload = {'results': [{'summary': '公开测试' * 100}]}
        self.assertEqual(self.settle(item, result=payload)['result'], payload)
        self.assertEqual(self.reserve()['result'], payload)

    def test_mutation_transaction_is_owned_by_the_caller(self):
        with self.connect() as conn:
            quota.reserve(conn, actor_kind='student', actor_id=1, channel='chat', logical_call_id='rolled-back', fingerprint=FP, now=NOW)
            conn.rollback()
        self.assertEqual(self.state()['occupied'], 0)
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) AS n FROM ai_web_search_calls').fetchone()['n'], 0)

    def test_expired_cache_is_bounded_without_destroying_quota_or_replay_identity(self):
        old = NOW - timedelta(days=8)
        first = self.reserve('old-1', now=old); self.send(first, now=old)
        self.settle(first, result={'results': [{'title': 'Old public result'}]}, now=old)
        second = self.reserve('old-2', now=old); self.send(second, now=old)
        self.settle(second, result={'results': []}, error='no_safe_results', now=old)
        fresh = self.reserve('fresh'); self.send(fresh)
        self.settle(fresh, result={'results': [{'title': 'Current public result'}]})
        self.assertEqual(self.transaction(quota.purge_cached_results, now=NOW, limit=0), 0)
        self.assertEqual(self.transaction(quota.purge_cached_results, now=NOW, limit=1), 1)
        self.assertEqual(self.transaction(quota.purge_cached_results, now=NOW, limit=1), 1)
        self.assertEqual(self.transaction(quota.purge_cached_results, now=NOW), 0)
        expired = self.reserve('old-1', now=NOW)
        self.assertEqual((expired['state'], expired['result'], expired['error_code']), ('succeeded', None, 'result_expired'))
        self.assertNotIn('dispatch_token', expired)
        self.assertFalse(self.send(first, now=NOW)['won'])
        self.assertEqual(self.transaction(quota.get_call, second['call_id'])['error_code'], 'no_safe_results')
        self.assertEqual(self.transaction(quota.get_call, first['call_id'])['fingerprint'], FP)
        self.assertEqual(self.state(now=NOW,budget_date=old.date().isoformat())['used'], 2)
        self.assertEqual(self.state()['used'], 1)
        self.assertIsNotNone(self.reserve('fresh')['result'])


class SearchQuotaSQLiteTest(QuotaContractMixin, unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory(prefix='lanshare-search-quota-'))
        self.database = Path(folder) / 'synthetic.db'
        with self.connect() as conn:
            ensure_ai_search_schema(conn)
            conn.commit()

    def connect(self):
        conn = sqlite3.connect(self.database, timeout=30, factory=SyntheticSQLite)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout=30000')
        return conn

    def test_snapshot_accepts_read_only_connection_and_services_do_no_runtime_ddl(self):
        self.reserve(lease_seconds=5)
        with self.connect() as conn:
            statements = []
            conn.set_trace_callback(statements.append)
            conn.execute('PRAGMA query_only=ON')
            self.assertEqual(quota.snapshot(conn, actor_kind='student',actor_id=1,channel='chat',now=NOW+timedelta(seconds=6))['remaining'], 10)
            self.assertFalse(any(sql.startswith(('INSERT','UPDATE','DELETE','CREATE','ALTER','COMMIT')) for sql in statements))


class SearchQuotaCleanupTaskTest(unittest.TestCase):
    def test_task_creation_is_idempotent_and_keeps_existing_job_state(self):
        from classroom_app.db import schema_scheduler

        with patch.object(schema_scheduler, '_SCHEMA_READY', False), patch.object(schema_scheduler, 'get_configured_db_engine', return_value='sqlite'):
            with sqlite3.connect(':memory:', factory=SyntheticSQLite) as conn:
                conn.row_factory = sqlite3.Row
                first = quota.ensure_ai_search_cleanup_task(conn)
                original = dict(conn.execute('SELECT * FROM scheduled_tasks WHERE id=?',(first,)).fetchone())
                conn.execute("UPDATE scheduled_tasks SET status='running',last_result='kept' WHERE id=?",(first,))
                self.assertEqual(quota.ensure_ai_search_cleanup_task(conn), first)
                row = dict(conn.execute('SELECT * FROM scheduled_tasks WHERE id=?',(first,)).fetchone())
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM scheduled_tasks').fetchone()[0],1)
                self.assertEqual((row['status'],row['last_result'],row['run_at']),('running','kept',original['run_at']))

    def test_handler_is_registered_with_existing_scheduler(self):
        from classroom_app.services import scheduled_task_handlers  # noqa: F401
        from classroom_app.services.scheduled_task_service import get_registered_task_kinds

        self.assertIn(quota.CACHE_CLEANUP_TASK_KIND,get_registered_task_kinds())

    def test_hourly_task_uses_existing_scheduler_dedupe_contract(self):
        with patch('classroom_app.services.scheduled_task_service.schedule_task', return_value=12) as schedule:
            conn = object()
            self.assertEqual(quota.ensure_ai_search_cleanup_task(conn), 12)
            args, kw = schedule.call_args
            self.assertEqual(args, (conn,))
            self.assertEqual((kw['task_kind'],kw['dedupe_key'],kw['recurrence_seconds']),
                (quota.CACHE_CLEANUP_TASK_KIND,'ai-search:cache-cleanup',3600))
            self.assertFalse(kw['replace'])

    def test_handler_commits_one_bounded_batch_and_propagates_failures_for_scheduler_retry(self):
        conn = MagicMock()
        context = MagicMock()
        context.__enter__.return_value = conn
        with patch('classroom_app.database.get_db_connection', return_value=context), patch.object(quota,'purge_cached_results',return_value=37) as purge:
            self.assertIn('37', quota.handle_ai_search_cache_cleanup({}))
            purge.assert_called_once_with(conn, limit=500)
            conn.commit.assert_called_once()
        conn.reset_mock()
        with patch('classroom_app.database.get_db_connection', return_value=context), patch.object(quota,'purge_cached_results',side_effect=RuntimeError('synthetic DB outage')):
            with self.assertRaises(RuntimeError):
                quota.handle_ai_search_cache_cleanup({})
            conn.commit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
