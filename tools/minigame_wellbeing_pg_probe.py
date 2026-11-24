"""Verify overlapping foreground reports and hourly awards on an owned PostgreSQL cluster."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from threading import Barrier
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.native_postgres_temp import OwnedPostgresRuntime


def exercise():
    from classroom_app.db.connection import get_db_connection, get_configured_db_engine
    from classroom_app.db.schema_cultivation_progress import ensure_cultivation_progress_schema
    from classroom_app.services import minigame_wellbeing as play

    assert get_configured_db_engine() == "postgres"
    start = int(datetime(2026, 11, 24, 12, tzinfo=play.LOCAL).timestamp() * 1000)
    group_size = 16
    with get_db_connection() as conn:
        conn.execute("CREATE TABLE students(id INTEGER PRIMARY KEY,class_id INTEGER,enrollment_status TEXT)")
        conn.execute("CREATE TABLE class_offerings(id INTEGER PRIMARY KEY,class_id INTEGER,semester TEXT,semester_id INTEGER)")
        conn.execute("CREATE TABLE class_offering_class_links(offering_id INTEGER,class_id INTEGER)")
        conn.execute("CREATE TABLE academic_semesters(id INTEGER PRIMARY KEY,start_date TEXT,end_date TEXT)")
        conn.execute("INSERT INTO students SELECT i,1,'active' FROM generate_series(1,?) AS i", (group_size,))
        conn.execute("INSERT INTO class_offerings VALUES(1,1,'',NULL),(2,2,'',NULL),(3,1,'2025-2026-2',NULL)")
        conn.execute("INSERT INTO class_offering_class_links VALUES(2,1)")
        ensure_cultivation_progress_schema(conn, engine="postgres")
        play.ensure_schema(conn)
        conn.execute("""INSERT INTO mg_play_totals SELECT id,3570,0 FROM students""")
        conn.execute("""INSERT INTO mg_play_days SELECT id,'2026-11-24',0,3570,0,0,0,'0' FROM students""")

    def contend(ids):
        barrier = Barrier(len(ids))
        def writer(student_id):
            with get_db_connection() as conn:
                barrier.wait(timeout=20)
                before = perf_counter()
                play.record(conn, student_id, start + play.LEASE_MS, {
                    "usageAnchor": start + play.LEASE_MS, "playIntervals": [[0, 60_000]]
                }, start + 60_000)
                conn.commit()
                return (perf_counter() - before) * 1000
        with ThreadPoolExecutor(max_workers=len(ids)) as workers:
            durations = sorted(workers.map(writer, ids))
        return {"workers": len(ids), "p95ms": round(durations[int((len(ids)-1)*0.95)], 1), "maxMs": round(durations[-1], 1)}

    same = contend([1] * group_size)
    independent = contend(range(1, group_size + 1))
    with get_db_connection() as conn:
        # All racing reports on a single student cover the same wall-clock minute.
        for uid in range(1, group_size + 1):
            stats = play.summary(conn, uid, start + 60_000)
            assert (stats["todaySeconds"], stats["totalPoints"], stats["points"]) == (3630, 1, 1), stats
            assert [play.course_points(conn, k, uid) for k in (1, 2, 3)] == [1, 1, 0]
        count = conn.execute("SELECT COUNT(*) AS n FROM cultivation_score_events").fetchone()["n"]
        dirty = conn.execute("SELECT COUNT(*) AS n FROM learning_progress_snapshots WHERE dirty_version=1 AND dirty=1").fetchone()["n"]
        assert count == dirty == group_size * 2
        return {"engine": conn.execute("SHOW server_version").fetchone()[0], "sameStudent": same,
                "independentStudents": independent, "uniqueStudents": group_size,
                "settlementsPerStudent": 1, "currentClassLedgerEntries": count, "dirtySnapshots": dirty}


def main():
    with OwnedPostgresRuntime(ROOT / ".codex-temp/evidence/class-policy", prefix="wellbeing-pg-") as cluster:
        cluster.start()
        with patch.dict(os.environ, {"DB_ENGINE": "postgres", "DATABASE_URL":
                f"postgresql://rehearsal_admin@127.0.0.1:{cluster.port}/postgres",
                "POSTGRES_BACKEND_READY": "true", "POSTGRES_POOL_ENABLED": "false",
                "LANSHARE_DATA_ROOT": str(cluster.runtime), "PYTHON_DOTENV_DISABLED": "1"}), patch("dotenv.load_dotenv", return_value=False):
            report = exercise()
        cluster.write_evidence_json("result.json", report)
        print(cluster.target, json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
