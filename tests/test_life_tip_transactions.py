"""Real SQLite transaction/cache boundaries; no PostgreSQL or network fixtures."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from classroom_app import config, database
from classroom_app.db import schema_life_tips as schema
from classroom_app.services import life_tip_service as service


class OwnedFactory:
    """Twelve real connection slots, observable without borrowing for waiters."""
    def __init__(self, path, *, fail_commit=False, fail_close=False):
        self.path, self.slots = path, threading.BoundedSemaphore(12)
        self.lock = threading.Lock()
        self.active = self.peak = self.calls = 0
        self.fail_commit, self.fail_close = fail_commit, fail_close
        self.connections = []

    def __call__(self):
        if not self.slots.acquire(timeout=2):
            raise AssertionError("Twelve owned SQLite slots exhausted")
        with self.lock:
            self.calls += 1
            self.active += 1
            self.peak = max(self.peak, self.active)
            fail_commit, self.fail_commit = self.fail_commit, False
            fail_close, self.fail_close = self.fail_close, False
        raw = sqlite3.connect(self.path, timeout=2)
        raw.row_factory = sqlite3.Row
        factory = self

        class Lease:
            closed = False
            def __getattr__(self, name): return getattr(raw, name)
            def commit(self):
                if fail_commit:
                    raise sqlite3.OperationalError("synthetic commit failure before commit")
                raw.commit()
            def close(self):
                if not self.closed:
                    self.closed = True
                    raw.close()
                    with factory.lock: factory.active -= 1
                    factory.slots.release()
                    if fail_close:
                        raise sqlite3.OperationalError("synthetic close failure after close")

        lease = Lease()
        self.connections.append(lease)
        return lease


class ObservedLock:
    def __init__(self, target_attempts):
        self.lock, self.guard = threading.Lock(), threading.Lock()
        self.attempts, self.target = 0, target_attempts
        self.all_attempted = threading.Event()
    def acquire(self, *args, **kwargs):
        with self.guard:
            self.attempts += 1
            if self.attempts == self.target: self.all_attempted.set()
        return self.lock.acquire(*args, **kwargs)
    def release(self): self.lock.release()
    def __enter__(self): self.acquire(); return self
    def __exit__(self, *_): self.release()


class LifeTipTransactionTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="life-tip-transactions-", dir=os.environ.get("TMP"))
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "owned.sqlite"
        for owner, name, value in ((config, "DB_PATH", self.path), (service, "_seeded", False),
                                   (service, "_ready_db_key", None), (service, "_pool_cache", {}),
                                   (service, "_pool_generation", 0), (service, "_seed_lock", threading.Lock()),
                                   (service, "_pool_fill_lock", threading.Lock())):
            substitute = patch.object(owner, name, value)
            substitute.start(); self.addCleanup(substitute.stop)
        conn = self.connect()
        try:
            schema.ensure_life_tip_schema(conn)
            conn.commit()
        finally: conn.close()
        self.expected = len({service.tip_content_hash(text) for _, text in
                             (*service.LIFE_TIP_SEED_PACK, *service.TEACHER_TIP_SEED_PACK)})
        self.scope = dict(school_code="", department="", audience_role="student")

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=2)
        conn.row_factory = sqlite3.Row
        return conn

    def seed_committed(self):
        conn = self.connect()
        try:
            service.ensure_life_tip_runtime(conn)
            conn.commit()
        finally: conn.close()

    def count(self, conn):
        return conn.execute("SELECT COUNT(*) FROM life_tips").fetchone()[0]

    def test_supplied_transaction_does_not_commit_mark_ready_or_publish_pool(self):
        a, b = self.connect(), self.connect()
        try:
            service.ensure_life_tip_runtime(a)
            self.assertTrue(a.in_transaction)
            self.assertEqual(self.count(a), self.expected)
            self.assertEqual(self.count(b), 0)
            self.assertFalse(service._seeded)
            self.assertIsNone(service._ready_db_key)
            self.assertTrue(service._get_pool(a, **self.scope))
            self.assertEqual(service._get_pool(b, **self.scope), [])
            self.assertEqual(service._pool_cache, {})
            a.rollback()
            self.assertEqual(service._get_pool(b, **self.scope), [])
        finally: a.close(); b.close()

    def test_rollback_is_reseeded_by_the_next_real_transaction(self):
        a, b = self.connect(), self.connect()
        try:
            service.ensure_life_tip_runtime(a); a.rollback()
            self.assertEqual(self.count(b), 0)
            service.ensure_life_tip_runtime(b)
            self.assertEqual(self.count(b), self.expected)
            self.assertTrue(b.in_transaction)
            self.assertFalse(service._seeded)
            b.commit()
            self.assertEqual(self.count(a), self.expected)
        finally: a.close(); b.close()

    def test_independent_memory_databases_each_get_their_own_schema_and_rows(self):
        a, b = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
        a.row_factory = b.row_factory = sqlite3.Row
        try:
            for conn in (a, b):
                service.ensure_life_tip_runtime(conn)
                self.assertEqual(self.count(conn), self.expected)
                self.assertTrue(conn.in_transaction)
            a.rollback()
            self.assertEqual(self.count(b), self.expected)
            self.assertFalse(service._seeded)
        finally: a.close(); b.close()

    def test_warm_publishes_only_after_committed_rows_are_visible_and_slot_closed(self):
        factory = OwnedFactory(self.path)
        with patch.object(database, "get_db_connection", factory):
            self.assertTrue(service.warm_life_tip_runtime())
        self.assertEqual(factory.active, 0)
        self.assertTrue(all(conn.closed for conn in factory.connections))
        self.assertTrue(service._seeded)
        self.assertEqual(service._ready_db_key, service._runtime_db_key())
        conn = self.connect()
        try: self.assertEqual(self.count(conn), self.expected)
        finally: conn.close()

    def test_commit_failure_never_publishes_ready_and_next_owned_warm_recovers(self):
        factory = OwnedFactory(self.path, fail_commit=True)
        with patch.object(database, "get_db_connection", factory):
            with self.assertRaises(sqlite3.OperationalError): service.warm_life_tip_runtime()
            self.assertFalse(service._seeded); self.assertIsNone(service._ready_db_key)
            self.assertEqual(factory.active, 0)
            conn = self.connect()
            try: self.assertEqual(self.count(conn), 0)
            finally: conn.close()
            self.assertTrue(service.warm_life_tip_runtime())
        self.assertEqual(factory.active, 0)

    def test_close_failure_keeps_ready_false_even_when_commit_happened(self):
        factory = OwnedFactory(self.path, fail_close=True)
        with patch.object(database, "get_db_connection", factory):
            with self.assertRaises(sqlite3.OperationalError): service.warm_life_tip_runtime()
            self.assertFalse(service._seeded); self.assertIsNone(service._ready_db_key)
            self.assertEqual(factory.active, 0)
            conn = self.connect()
            try: self.assertEqual(self.count(conn), self.expected)
            finally: conn.close()
            self.assertTrue(service.warm_life_tip_runtime())

    def test_unknown_commit_error_identity_survives_both_cleanup_failures(self):
        primary = RuntimeError("opaque-primary-payload")
        calls = []
        class Faults:
            def commit(self):
                calls.append("commit")
                raise primary
            def rollback(self):
                calls.append("rollback")
                raise ValueError("opaque-rollback-payload")
            def close(self):
                calls.append("close")
                raise OSError("opaque-close-payload")
        with self.assertRaises(RuntimeError) as observed:
            service._close_owned_connection(Faults(), lambda conn: "work completed")
        self.assertIs(observed.exception, primary)
        self.assertEqual(calls, ["commit", "rollback", "close"])
        notes = "\n".join(getattr(primary, "__notes__", []))
        self.assertIn("ValueError", notes)
        self.assertIn("OSError", notes)
        for text in ("opaque-primary-payload", "opaque-rollback-payload", "opaque-close-payload"):
            self.assertNotIn(text, notes)
        self.assertFalse(service._seeded)
        self.assertIsNone(service._ready_db_key)

    def test_seed_waiters_do_not_borrow_twelve_slot_pool_before_lock(self):
        factory, lock = OwnedFactory(self.path), ObservedLock(16)
        entered, release = threading.Event(), threading.Event()
        original = service.ensure_life_tip_runtime
        def gated(conn):
            entered.set()
            if not release.wait(4): raise AssertionError("seed gate was not released")
            return original(conn)
        with patch.object(database, "get_db_connection", factory), patch.object(service, "_seed_lock", lock), \
             patch.object(service, "ensure_life_tip_runtime", gated), ThreadPoolExecutor(max_workers=16) as threads:
            work = [threads.submit(service.warm_life_tip_runtime) for _ in range(16)]
            try:
                self.assertTrue(entered.wait(2)); self.assertTrue(lock.all_attempted.wait(2))
                self.assertEqual(factory.calls, 1); self.assertEqual(factory.active, 1)
            finally: release.set()
            self.assertTrue(all(future.result(timeout=3) for future in work))
        self.assertEqual(factory.calls, 1); self.assertEqual(factory.peak, 1); self.assertEqual(factory.active, 0)

    def test_pool_fill_waiters_do_not_borrow_slots_while_first_reader_is_pending(self):
        self.seed_committed()
        factory, lock = OwnedFactory(self.path), ObservedLock(16)
        with patch.object(database, "get_db_connection", factory):
            self.assertTrue(service.warm_life_tip_runtime())
        initial_calls = factory.calls
        entered, release = threading.Event(), threading.Event()
        original = service._load_pool_from_db
        def gated(conn, **scope):
            rows = original(conn, **scope); entered.set()
            if not release.wait(4): raise AssertionError("read gate was not released")
            return rows
        with patch.object(database, "get_db_connection", factory), patch.object(service, "_pool_fill_lock", lock), \
             patch.object(service, "_load_pool_from_db", gated), ThreadPoolExecutor(max_workers=16) as threads:
            work = [threads.submit(service._get_committed_pool, **self.scope) for _ in range(16)]
            try:
                self.assertTrue(entered.wait(2)); self.assertTrue(lock.all_attempted.wait(2))
                self.assertEqual(factory.calls, initial_calls + 1); self.assertEqual(factory.active, 1)
            finally: release.set()
            self.assertTrue(all(future.result(timeout=3) for future in work))
        self.assertEqual(factory.calls, initial_calls + 1); self.assertEqual(factory.peak, 1); self.assertEqual(factory.active, 0)

    def test_writer_invalidation_prevents_an_old_reader_from_refilling_cache(self):
        self.seed_committed()
        factory = OwnedFactory(self.path)
        entered, release = threading.Event(), threading.Event()
        original = service._load_pool_from_db
        stale = []
        def gated(conn, **scope):
            rows = original(conn, **scope); stale.extend(rows); entered.set()
            if not release.wait(4): raise AssertionError("old reader gate was not released")
            return rows
        with patch.object(database, "get_db_connection", factory), \
             patch.object(service, "_load_pool_from_db", gated), ThreadPoolExecutor(max_workers=1) as threads:
            work = threads.submit(service._get_committed_pool, **self.scope)
            try:
                self.assertTrue(entered.wait(2)); self.assertTrue(stale)
                writer = self.connect()
                try:
                    writer.execute("UPDATE life_tips SET status='retired' WHERE id=?", (stale[0]["id"],))
                    writer.commit()
                finally: writer.close()
                service.invalidate_pool_cache()
            finally: release.set()
            self.assertTrue(work.result(timeout=3))
        self.assertEqual(service._pool_cache, {})
        with patch.object(database, "get_db_connection", factory):
            fresh = service._get_committed_pool(**self.scope)
        self.assertNotIn(stale[0]["id"], {row["id"] for row in fresh})
        self.assertEqual(factory.active, 0)

    def test_failed_or_empty_owned_read_does_not_publish_an_empty_cache(self):
        factory = OwnedFactory(self.path)
        with patch.object(database, "get_db_connection", factory):
            self.assertTrue(service.warm_life_tip_runtime())
            writer = self.connect()
            try:
                writer.execute("UPDATE life_tips SET status='retired'")
                writer.commit()
            finally: writer.close()
            self.assertEqual(service._get_committed_pool(**self.scope), [])
            self.assertEqual(service._pool_cache, {})
            with patch.object(service, "_load_pool_from_db", side_effect=sqlite3.OperationalError("synthetic read failure")):
                with self.assertRaises(sqlite3.OperationalError): service._get_committed_pool(**self.scope)
            self.assertEqual(service._pool_cache, {}); self.assertEqual(factory.active, 0)
            writer = self.connect()
            try:
                writer.execute("UPDATE life_tips SET status='active'")
                writer.commit()
            finally: writer.close()
            self.assertTrue(service._get_committed_pool(**self.scope))
        self.assertEqual(factory.active, 0)
