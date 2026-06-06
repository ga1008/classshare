import sqlite3
import unittest

from classroom_app.services import blog_news_crawler_service as crawler


class _FakeCursor:
    def __init__(self, row=None, rowcount=0):
        self._row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self._row


class _FakePostgresCrawlerConnection:
    def __init__(self):
        self.calls = []
        self.commits = 0

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        self.calls.append((normalized, tuple(params)))
        if normalized.startswith("UPDATE blog_news_crawler_runs"):
            return _FakeCursor(
                {
                    "id": 7,
                    "trigger_source": "scheduled",
                    "status": "running",
                    "worker_id": "blog-worker-1",
                    "keywords_json": "[]",
                    "log_json": "[]",
                }
            )
        raise AssertionError(f"Unexpected SQL: {normalized}")

    def commit(self):
        self.commits += 1


class _FakePostgresConfigConnection:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        self.calls.append((normalized, tuple(params)))
        if normalized.startswith("INSERT INTO blog_news_crawler_config"):
            return _FakeCursor(rowcount=1)
        if normalized.startswith("SELECT * FROM blog_news_crawler_config"):
            return _FakeCursor({"id": 1, "enabled": 1, "source_templates_json": "[]"})
        if normalized.startswith("SELECT * FROM blog_news_crawler_runs") and params:
            return _FakeCursor(
                {
                    "id": params[0],
                    "trigger_source": "manual",
                    "status": "pending",
                    "worker_id": "blog-worker-1",
                    "keywords_json": "[]",
                    "log_json": "[]",
                }
            )
        if normalized.startswith("SELECT * FROM blog_news_crawler_runs"):
            return _FakeCursor(None)
        if normalized.startswith("INSERT INTO blog_news_crawler_runs"):
            return _FakeCursor(
                {
                    "id": 12,
                    "trigger_source": "manual",
                    "status": "pending",
                    "worker_id": "blog-worker-1",
                    "keywords_json": "[]",
                    "log_json": "[]",
                }
            )
        raise AssertionError(f"Unexpected SQL: {normalized}")


class BlogNewsCrawlerQueueClaimTests(unittest.TestCase):
    def _sqlite_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE blog_news_crawler_runs (
                id INTEGER PRIMARY KEY,
                trigger_source TEXT DEFAULT 'scheduled',
                status TEXT,
                scheduled_for TEXT,
                worker_id TEXT DEFAULT '',
                started_at TEXT DEFAULT '',
                keywords_json TEXT NOT NULL DEFAULT '[]',
                log_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT,
                updated_at TEXT
            );
            INSERT INTO blog_news_crawler_runs
                (id, trigger_source, status, scheduled_for, created_at, updated_at)
            VALUES
                (1, 'scheduled', 'pending', '2026-01-01T00:00:00', '2026-01-01T00:00:00', '2026-01-01T00:00:00'),
                (2, 'scheduled', 'pending', '2099-01-01T00:00:00', '2026-01-01T00:01:00', '2026-01-01T00:01:00');
            """
        )
        return conn

    def _patch_engine(self, value):
        original = crawler.get_configured_db_engine
        crawler.get_configured_db_engine = lambda: value
        return original

    def test_sqlite_claim_due_blog_run_marks_one_pending_run_running(self):
        conn = self._sqlite_conn()
        try:
            row = crawler._claim_due_blog_news_crawler_run(
                conn,
                worker_id="blog-worker-1",
                now="2026-01-01T00:10:00",
                engine="sqlite",
            )

            self.assertEqual(1, row["id"])
            self.assertEqual("running", row["status"])
            self.assertEqual("blog-worker-1", row["worker_id"])
            rows = conn.execute("SELECT id, status, started_at FROM blog_news_crawler_runs ORDER BY id").fetchall()
            self.assertEqual("running", rows[0]["status"])
            self.assertEqual("2026-01-01T00:10:00", rows[0]["started_at"])
            self.assertEqual("pending", rows[1]["status"])
        finally:
            conn.close()

    def test_postgres_claim_due_blog_run_uses_skip_locked_and_returning(self):
        conn = _FakePostgresCrawlerConnection()

        row = crawler._claim_due_blog_news_crawler_run(
            conn,
            worker_id="blog-worker-1",
            now="2026-01-01T00:10:00",
            engine="postgres",
        )

        self.assertEqual(7, row["id"])
        self.assertEqual(1, conn.commits)
        self.assertEqual(1, len(conn.calls))
        sql, params = conn.calls[0]
        self.assertIn("FOR UPDATE SKIP LOCKED", sql)
        self.assertIn("RETURNING *", sql)
        self.assertEqual(
            ("running", "blog-worker-1", "2026-01-01T00:10:00", "2026-01-01T00:10:00", "pending", "2026-01-01T00:10:00"),
            params,
        )

    def test_postgres_config_and_enqueue_avoid_sqlite_only_sql(self):
        conn = _FakePostgresConfigConnection()
        original = self._patch_engine("postgres")
        try:
            config = crawler.load_blog_news_crawler_config(conn)
            run = crawler.enqueue_blog_news_crawler_run(conn, worker_id="blog-worker-1")
        finally:
            crawler.get_configured_db_engine = original

        self.assertTrue(config["enabled"])
        self.assertEqual(12, run["id"])
        sql_text = "\n".join(call[0] for call in conn.calls)
        self.assertIn("ON CONFLICT (id) DO NOTHING", sql_text)
        self.assertIn("RETURNING id", sql_text)
        self.assertNotIn("INSERT OR IGNORE", sql_text)

    def test_unknown_blog_claim_engine_fails_fast(self):
        conn = self._sqlite_conn()
        try:
            with self.assertRaises(ValueError):
                crawler._claim_due_blog_news_crawler_run(
                    conn,
                    worker_id="blog-worker-1",
                    now="2026-01-01T00:10:00",
                    engine="mysql",
                )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
