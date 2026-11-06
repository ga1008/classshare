"""Opt-in real-service poll concurrency checks on a disposable rehearsal schema.

This is a correctness probe, not a hardware capacity certificate. Two hundred
logical contenders share a bounded twelve-connection admission semaphore.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import queue
import socket
import sys
import tempfile
import threading
import time
import uuid
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def _network_guard():
    original_connect, original_connect_ex, original_dns = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def allowed(host):
        return host is None or str(host).lower() in {"localhost", "127.0.0.1", "::1"}

    def connect(sock, address):
        if not allowed(address[0]):
            raise RuntimeError("Poll probe forbids external sockets")
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if not allowed(address[0]):
            raise RuntimeError("Poll probe forbids external sockets")
        return original_connect_ex(sock, address)

    def dns(host, *args, **kwargs):
        if not allowed(host):
            raise RuntimeError("Poll probe forbids external DNS")
        return original_dns(host, *args, **kwargs)

    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, dns


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable-capacity-cluster", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from tools.isolated_environment import guard_dotenv_loading, isolate_sqlite_environment

    guard_dotenv_loading()
    _network_guard()
    temporary_root = REPO / ".codex-temp"
    temporary_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="poll-native-", dir=temporary_root) as temporary:
        isolate_sqlite_environment(Path(temporary))
        os.environ.update(DB_ENGINE="postgres", POSTGRES_BACKEND_READY="true", POSTGRES_POOL_ENABLED="false",
                          DATABASE_URL="postgresql://rehearsal_admin@127.0.0.1:18766/lanshare_assessment_rehearsal_capacity",
                          SECRET_KEY="synthetic-poll-probe-only")
        return _run(args)


def _run(args) -> int:
    from psycopg import connect, sql
    from fastapi import HTTPException
    from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
    from classroom_app.db.schema_polls import ensure_poll_schema
    from classroom_app.services import poll_service as polls

    kwargs = dict(host="127.0.0.1", port=18766, dbname="lanshare_assessment_rehearsal_capacity",
                  user="rehearsal_admin", connect_timeout=3)
    schema = "poll_probe_" + uuid.uuid4().hex
    with connect(**kwargs, autocommit=True) as admin:
        directory, listener, port = admin.execute(
            "SELECT current_setting('data_directory'),current_setting('listen_addresses'),current_setting('port')"
        ).fetchone()
        if (Path(directory).resolve() != (REPO / ".codex-temp/platform-capacity-20261105-pg").resolve()
                or listener != "127.0.0.1" or int(port) != 18766):
            raise RuntimeError("Refusing a cluster outside the dedicated loopback rehearsal boundary")
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    @contextmanager
    def connection():
        raw = connect(**kwargs, row_factory=sqlite_compatible_dict_row)
        try:
            # No public fallback: every unqualified domain table resolves only
            # inside this generated schema, including optional-table lookups.
            raw.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            raw.execute("SET statement_timeout = '10s'")
            raw.execute("SET lock_timeout = '5s'")
            raw.commit()
        except BaseException:
            raw.close()
            raise
        with LanSharePostgresConnection(raw) as conn:
            yield conn

    teacher = {"id": 810001, "role": "teacher", "name": "Synthetic teacher"}
    student = lambda index: {"id": 970000 + index, "role": "student", "name": f"Synthetic {index}"}
    checks, timings, races = {}, {}, {}
    started = time.perf_counter()

    def fields(**changes):
        result = dict(title="Synthetic concurrent poll", description="Owned correctness fixture", vote_type="single",
                      deadline_at=None, allow_change=False, max_changes=0, result_visibility="after_close",
                      options=["A", "B", "C"], class_offering_ids=[910001], status="active")
        result.update(changes)
        return result

    def create(**changes):
        with connection() as conn:
            return polls.create_poll(conn, teacher, fields(**changes), origin=polls.ORIGIN_MANAGEMENT)

    def invoke(operation):
        try:
            with connection() as conn:
                value = operation(conn)
            return {"status": 200, "value": value}
        except HTTPException as exc:
            return {"status": exc.status_code}

    def counts(poll_id):
        with connection() as conn:
            return dict(conn.execute("SELECT COUNT(*) AS ballots,COUNT(DISTINCT voter_id) AS unique_voters "
                                     "FROM poll_ballots WHERE poll_id=?", (poll_id,)).fetchone()) | {
                "votes": conn.execute("SELECT COUNT(*) FROM poll_votes WHERE poll_id=?", (poll_id,)).fetchone()[0]}

    def simultaneous(operations):
        barrier = threading.Barrier(len(operations))

        def work(operation):
            barrier.wait(timeout=5)
            return invoke(operation)

        with ThreadPoolExecutor(max_workers=len(operations)) as executor:
            return list(executor.map(work, operations))

    def race(name, holder_operation, waiter_operation, expected_status):
        """Hold a completed real write uncommitted; observe real PG lock wait."""
        held, release = threading.Event(), threading.Event()
        waiter_pid = queue.Queue()
        holder_result, waiter_result = {}, {}

        def holder():
            try:
                with connection() as conn:
                    holder_result["value"] = holder_operation(conn)
                    held.set()
                    if not release.wait(8):
                        raise TimeoutError("Probe holder release timeout")
                holder_result["status"] = 200
            except BaseException as exc:
                holder_result["error"] = repr(exc)
                held.set()

        def waiter():
            try:
                with connection() as conn:
                    waiter_pid.put(conn.execute("SELECT pg_backend_pid()").fetchone()[0])
                    waiter_result["value"] = waiter_operation(conn)
                waiter_result["status"] = 200
            except HTTPException as exc:
                waiter_result["status"] = exc.status_code
            except BaseException as exc:
                waiter_result["error"] = repr(exc)

        first = threading.Thread(target=holder, daemon=True)
        second = threading.Thread(target=waiter, daemon=True)
        first.start()
        try:
            assert held.wait(5) and "error" not in holder_result, holder_result
            second.start()
            pid = waiter_pid.get(timeout=3)
            locked = False
            deadline = time.monotonic() + 3
            with connection() as observer:
                while time.monotonic() < deadline:
                    observer.execute("SELECT pg_stat_clear_snapshot()")
                    row = observer.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=?", (pid,)).fetchone()
                    if row and row["wait_event_type"] == "Lock":
                        locked = True
                        break
                    if not second.is_alive():
                        break
                    time.sleep(0.01)
            assert locked, {"race": name, "waiter": waiter_result}
        finally:
            release.set()
            first.join(10)
            if second.ident is not None:
                second.join(10)
        assert not first.is_alive() and not second.is_alive()
        assert holder_result.get("status") == 200, holder_result
        assert waiter_result.get("status") == expected_status, waiter_result
        races[name] = {"observed_pg_lock_wait": True, "waiter_status": expected_status}

    report = {"status": "failed", "checks": checks, "races": races, "timings": timings,
              "postgres_port": 18766, "logical_voters": 200, "maximum_vote_connections": 12,
              "isolation": "unique disposable schema; no public table reads or writes; loopback only",
              "capacity_certificate": False}
    try:
        with connection() as conn:
            for ddl in (
                "CREATE TABLE teachers(id INTEGER PRIMARY KEY,name TEXT,is_active INTEGER,is_super_admin INTEGER)",
                "CREATE TABLE classes(id INTEGER PRIMARY KEY,name TEXT)",
                "CREATE TABLE courses(id INTEGER PRIMARY KEY,name TEXT)",
                "CREATE TABLE class_offerings(id INTEGER PRIMARY KEY,class_id INTEGER,course_id INTEGER,teacher_id INTEGER)",
                "CREATE TABLE class_offering_class_links(offering_id INTEGER,class_id INTEGER)",
                "CREATE TABLE students(id INTEGER PRIMARY KEY,class_id INTEGER,enrollment_status TEXT)",
                "CREATE TABLE classroom_live_activities(id INTEGER PRIMARY KEY,kind TEXT)",
                "CREATE TABLE classroom_live_options(activity_id INTEGER)",
                "CREATE TABLE classroom_live_responses(activity_id INTEGER)",
            ):
                conn.execute(ddl)
            conn.execute("INSERT INTO teachers VALUES(810001,'Synthetic',1,0)")
            conn.execute("INSERT INTO classes VALUES(830001,'A'),(830002,'B')")
            conn.execute("INSERT INTO courses VALUES(820001,'Synthetic')")
            conn.execute("INSERT INTO class_offerings VALUES(910001,830001,820001,810001),(910002,830002,820001,810001)")
            for index in range(1, 201):
                conn.execute("INSERT INTO students VALUES(?,830001,'active')", (student(index)["id"],))
            conn.execute("INSERT INTO students VALUES(980001,830002,'active')")
            ensure_poll_schema(conn)
            # Avoid sharing advisory keys with the other synthetic public app.
            conn.execute("ALTER TABLE polls ALTER COLUMN id RESTART WITH 700001")
            conn.execute("ALTER TABLE poll_options ALTER COLUMN id RESTART WITH 610001")

        poll = create()
        poll_id, option = poll["id"], poll["options"][0]["id"]
        barrier, admission = threading.Barrier(201), threading.Semaphore(12)

        def cast(index):
            barrier.wait(timeout=10)
            begin = time.perf_counter()
            with admission:
                result = invoke(lambda conn: polls.vote(conn, poll_id, student(index), [option]))
            return result["status"], time.perf_counter() - begin

        with ThreadPoolExecutor(max_workers=200) as executor:
            pending = [executor.submit(cast, index) for index in range(1, 201)]
            barrier.wait(timeout=10)
            results = [future.result(timeout=30) for future in pending]
        assert all(status == 200 for status, _ in results), results
        assert counts(poll_id) == {"ballots": 200, "unique_voters": 200, "votes": 200}
        assert invoke(lambda conn: polls.vote(conn, poll_id, {"id": 980001, "role": "student"}, [option]))["status"] == 403
        with connection() as conn:
            owner_detail = polls.load_poll_detail(conn, poll_id, teacher)
            assert owner_detail["total_voters"] == 200 and owner_detail["options"][0]["count"] == 200
        durations = sorted(duration for _, duration in results)
        timings["200_vote_p95_including_connection_admission_seconds"] = durations[189]
        timings["200_vote_max_seconds"] = durations[-1]
        checks["200_distinct_voters_unique_and_nonmember_denied"] = True

        parallel = create()
        sharing = threading.Barrier(2)

        def shared(index):
            with connection() as conn:
                polls._load_poll(conn, parallel["id"], lock="vote")
                sharing.wait(timeout=3)
                polls.vote(conn, parallel["id"], student(index), [parallel["options"][0]["id"]])

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(shared, index) for index in (1, 2)]
            for future in futures:
                future.result(timeout=10)
        assert counts(parallel["id"])["ballots"] == 2
        checks["distinct_actors_hold_parent_share_simultaneously"] = True

        first = create()
        outcomes = simultaneous([lambda conn: polls.vote(conn, first["id"], student(1), [first["options"][0]["id"]])] * 2)
        assert sorted(result["status"] for result in outcomes) == [200, 400], outcomes
        assert counts(first["id"]) == {"ballots": 1, "unique_voters": 1, "votes": 1}
        checks["same_actor_concurrent_first_vote_exactly_once"] = True

        changed = create(allow_change=True, max_changes=1)
        assert invoke(lambda conn: polls.vote(conn, changed["id"], student(1), [changed["options"][0]["id"]]))["status"] == 200
        outcomes = simultaneous([lambda conn, option=option: polls.vote(conn, changed["id"], student(1), [option["id"]])
                                 for option in changed["options"][1:]])
        assert sorted(result["status"] for result in outcomes) == [200, 400], outcomes
        with connection() as conn:
            assert conn.execute("SELECT change_count FROM poll_ballots WHERE poll_id=?", (changed["id"],)).fetchone()[0] == 1
        assert counts(changed["id"])["votes"] == 1
        checks["same_actor_concurrent_change_respects_one_change_budget"] = True

        for mutation in ("close", "assign", "options", "delete"):
            for vote_first in (True, False):
                item = create()
                identity, selected = item["id"], item["options"][0]["id"]
                vote_operation = lambda conn, identity=identity, selected=selected: polls.vote(conn, identity, student(1), [selected])
                if mutation == "close":
                    owner_operation = lambda conn: polls.set_poll_status(conn, identity, teacher, "closed")
                    expected = 200 if vote_first else 400
                elif mutation == "assign":
                    owner_operation = lambda conn: polls.set_poll_assignments(conn, identity, teacher, [910002])
                    expected = 200 if vote_first else 403
                elif mutation == "options":
                    owner_operation = lambda conn: polls.update_poll(conn, identity, teacher, fields(options=["New A", "New B"]))
                    expected = 400
                else:
                    owner_operation = lambda conn: polls.delete_poll(conn, identity, teacher)
                    expected = 200 if vote_first else 404
                race(f"{'vote' if vote_first else mutation}_before_{mutation if vote_first else 'vote'}",
                     vote_operation if vote_first else owner_operation,
                     owner_operation if vote_first else vote_operation, expected)
                actual = counts(identity)
                expected_count = int(vote_first and mutation != "delete")
                assert actual == {"ballots": expected_count, "unique_voters": expected_count, "votes": expected_count}, actual
                with connection() as conn:
                    if mutation == "delete":
                        for table in ("polls", "poll_options", "poll_assignments", "poll_participants", "poll_ballots", "poll_votes"):
                            column = "id" if table == "polls" else "poll_id"
                            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (identity,)).fetchone()[0] == 0
                    elif mutation == "close":
                        assert polls._load_poll(conn, identity)["status"] == "closed"
                    elif mutation == "assign":
                        assert polls._assigned_class_ids(conn, identity) == [910002]
                    else:
                        labels = [option["label"] for option in polls._load_options(conn, identity)]
                        assert labels == (["A", "B", "C"] if vote_first else ["New A", "New B"])
        checks["vote_close_assign_option_delete_both_transaction_orders"] = True

        from classroom_app.routers.polls import _notify_if_active
        from classroom_app.services import message_center_service

        def fail_notification(conn, _poll_id):
            conn.execute("SELECT * FROM intentionally_absent_poll_probe_notification")

        active = create()
        with connection() as conn, patch.object(message_center_service, "create_poll_published_notifications", fail_notification):
            conn.execute("UPDATE polls SET title='notification failure survived' WHERE id=?", (active["id"],))
            _notify_if_active(conn, active)
            assert conn.execute("SELECT 1").fetchone()[0] == 1
        with connection() as conn:
            assert polls._load_poll(conn, active["id"])["title"] == "notification failure survived"
        checks["optional_notification_real_sql42_savepoint_preserves_main_commit"] = True
        report["status"] = "passed"
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        with connect(**kwargs, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
            report["schema_removed"] = admin.execute("SELECT to_regnamespace(%s)", (schema,)).fetchone()[0] is None
        report["elapsed_seconds"] = time.perf_counter() - started
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
