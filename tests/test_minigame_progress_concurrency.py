"""Two real save transactions addressed to the same actor by solo and team runs."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest import TestCase
from unittest.mock import patch

from classroom_app import config
from classroom_app.db.connection import get_configured_db_engine, get_db_connection
from classroom_app.services import minigame_runtime_store as store


GAME = {"id": "beacon", "writeback": {"series": "coast", "saveVersion": 1}}
ACTOR = "student:17"


def progress_race(first_rank, second_rank, actor, first_run, second_run):
    """Hold the first decision open and detect the second reader's actual PostgreSQL row-lock wait."""
    native = get_configured_db_engine() == "postgres"
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO mg_rt_progress(game,series,save_ver,actor) VALUES(?,?,?,?)", store.pkey(GAME, actor)
        )
    first_decided, second_attempted, second_decided = Event(), Event(), Event()
    release_first, first_committed = Event(), Event()
    pids = {}

    class ObservedConnection:
        def __init__(self, conn, number):
            self.conn = conn
            self.number = number

        def execute(self, sql, args=()):
            statement = " ".join(sql.upper().split())
            if self.number == 2:
                second_attempted.set()
            if statement.startswith("UPDATE MG_RT_PROGRESS SET RANK="):
                if self.number == 1:
                    first_decided.set()
                    if not release_first.wait(8):
                        raise RuntimeError("first decision was never released")
                else:
                    second_decided.set()
                    if not first_committed.wait(8):
                        raise RuntimeError("first transaction did not commit")
            return self.conn.execute(sql, args)

    def write(number, rank, run):
        with get_db_connection() as conn:
            if native:
                pids[number] = conn.execute("SELECT pg_backend_pid()").fetchone()[0]
            store.advance_progress(ObservedConnection(conn, number), GAME, actor, rank, run, number)
            conn.commit()
        if number == 1:
            first_committed.set()

    wait_seen = False
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(write, 1, first_rank, first_run)
        try:
            if not first_decided.wait(5):
                raise RuntimeError("first transaction did not reach its progress decision")
            second = pool.submit(write, 2, second_rank, second_run)
            if not second_attempted.wait(5):
                raise RuntimeError("second transaction did not start")
            if native:
                with get_db_connection() as observer:
                    deadline = time.monotonic() + 4
                    while not second_decided.is_set():
                        wait_seen = observer.execute(
                            "SELECT ?=ANY(pg_blocking_pids(?))", (pids[1], pids[2])
                        ).fetchone()[0]
                        if wait_seen:
                            break
                        if time.monotonic() > deadline:
                            raise RuntimeError("cannot establish second write decision or lock wait")
                        time.sleep(0.005)
        finally:
            release_first.set()
        first.result(timeout=10)
        second.result(timeout=10)
    with get_db_connection() as conn:
        raced = conn.execute("SELECT rank,shared_rev FROM mg_rt_progress WHERE actor=?", (actor,)).fetchone()
        for rank, run in ((second_rank, second_run), (first_rank, first_run)):
            store.advance_progress(conn, GAME, actor, rank, run, 4)
        row = conn.execute("SELECT rank,shared_rev FROM mg_rt_progress WHERE actor=?", (actor,)).fetchone()
        changes = conn.execute(
            "SELECT before_rank,after_rank,revision,run FROM mg_rt_changes WHERE actor=? ORDER BY revision,at_ms",
            (actor,),
        ).fetchall()
    return {
        "beforeRetry": [raced[0], raced[1]],
        "rank": row[0],
        "revision": row[1],
        "changes": [[r[0], r[1], r[2], r[3]] for r in changes],
        "secondBlockedOnFirst": bool(wait_seen),
        "secondReachedUpdateBeforeFirstCommit": second_decided.is_set() and not wait_seen and native,
    }


def exercise():
    with get_db_connection() as conn:
        store.ensure_runtime_schema(conn)
    high_first = progress_race(2, 1, ACTOR, "team-run", "solo-run")
    low_first = progress_race(1, 2, "student:18", "solo-run", "team-run")
    expected_high = [[0, 2, 1, "team-run"]]
    expected_low = [[0, 1, 1, "solo-run"], [1, 2, 2, "team-run"]]
    return {
        "engine": get_configured_db_engine(),
        "teamFirst": high_first,
        "soloFirst": low_first,
        "passed": high_first["beforeRetry"] == [2, 1]
        and high_first["rank"] == 2
        and high_first["revision"] == 1
        and high_first["changes"] == expected_high
        and low_first["beforeRetry"] == [2, 2]
        and low_first["rank"] == 2
        and low_first["revision"] == 2
        and low_first["changes"] == expected_low,
    }


class ProgressConcurrencyTests(TestCase):
    def setUp(self):
        directory = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.object(config, "DB_ENGINE", "sqlite"))
        self.enterContext(patch.object(config, "DB_PATH", directory / "shared.sqlite"))
        self.enterContext(patch.dict(os.environ, {"LANSHARE_DATA_ROOT": str(directory)}))

    def test_ordered_solo_and_team_commits_and_retries(self):
        report = exercise()
        self.assertTrue(report["passed"], report)
