"""Bounded read-only observation in the controller-owned synthetic PG lab.

There are no database/host options. Exact historical counts run once per minute;
the one-second path uses the inbox partial index and the small attendance domain.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.platform_capacity_linux_lab import validate_container_boundary, _checked_pg_connection


def observe(seconds):
    if type(seconds) is not int or not 60 <= seconds <= 7200:
        raise ValueError("Observation must be bounded to60..7200seconds")
    identity = validate_container_boundary()
    root = Path(identity["artifact_root"])
    path = root/"attendance-pg-observations.jsonl"
    conn, _ = _checked_pg_connection(identity["marker"])
    conn.execute("SET default_transaction_read_only=on")
    conn.execute("SET application_name='attendance_capacity_observer'")
    conn.execute("SET statement_timeout='2000ms'")
    start, deadline, iteration = time.monotonic(), time.monotonic()+seconds, 0
    try:
        with path.open("x", encoding="utf-8") as output:
            while time.monotonic() < deadline and not (root/"stop-attendance-monitor").exists():
                began = time.perf_counter()
                iteration += 1
                sample = {"iteration":iteration, "server_epoch":time.time(), "elapsed_monotonic":time.monotonic()-start}
                disk = os.statvfs(root)
                sample["disk_available_bytes"] = disk.f_bavail*disk.f_frsize
                try:
                    sample["inbox_unresolved"] = conn.execute("SELECT state,COUNT(*),MIN(received_at) "
                        "FROM behavior_write_inbox WHERE state IN ('pending','quarantined') GROUP BY state").fetchall()
                    sample["waits"] = conn.execute("SELECT application_name,wait_event_type,wait_event,state,COUNT(*) "
                        "FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() "
                        "GROUP BY application_name,wait_event_type,wait_event,state").fetchall()
                    sample["advisory_locks"] = conn.execute("SELECT classid,objid,granted,COUNT(*) FROM pg_locks "
                        "WHERE locktype='advisory' AND classid=%s GROUP BY classid,objid,granted",(0x42485652,)).fetchall()
                    sample["snapshots"] = conn.execute("SELECT COUNT(*),COUNT(*) FILTER(WHERE dirty=1),"
                        "COUNT(*) FILTER(WHERE dirty_version<>calculated_version) FROM learning_progress_snapshots "
                        "WHERE class_offering_id=%s",(identity["fixture"]["classOfferingId"],)).fetchone()
                    sample["rounds"] = [dict(zip(("id","state","revision","evaluated_at_ms","next_evaluation_ms",
                        "ends_at_ms","window_end_ms","settle_at_ms","watermark_json"), row)) for row in conn.execute(
                        "SELECT id,state,revision,evaluated_at_ms,next_evaluation_ms,ends_at_ms,window_end_ms,settle_at_ms,"
                        "watermark_json FROM login_attendance_rounds WHERE class_offering_id=%s ORDER BY id",
                        (identity["fixture"]["classOfferingId"],)).fetchall()]
                    if iteration % 60 == 1:
                        sample["exact_inbox_counts"] = conn.execute("SELECT state,COUNT(*) FROM behavior_write_inbox GROUP BY state").fetchall()
                        sample["exact_access_count"] = conn.execute("SELECT COUNT(*) FROM login_attendance_access").fetchone()[0]
                        sample["table_sizes"] = conn.execute("SELECT relname,n_live_tup,pg_total_relation_size(relid) "
                            "FROM pg_stat_user_tables WHERE relname IN ('behavior_write_inbox','login_attendance_access',"
                            "'login_attendance_evaluations','login_attendance_audit') ORDER BY relname").fetchall()
                    if iteration % 5 == 1:
                        try:
                            health = json.loads(urllib.request.urlopen("http://app:8000/api/internal/health",timeout=3).read())
                            sample["health"] = {k:health.get(k) for k in ("behavior_write_pipeline","db_pool")}
                        except Exception as exc:
                            sample["health"] = {"error_type":type(exc).__name__}
                except Exception as exc:
                    sample["observer_error_type"] = type(exc).__name__
                sample["observer_ms"] = (time.perf_counter()-began)*1000
                output.write(json.dumps(sample,default=str)+"\n")
                output.flush()
                time.sleep(max(0,1-(time.perf_counter()-began)))
        result = {"status":"stopped","lab_nonce":identity["lab_nonce"],"samples":iteration,
                  "elapsed_monotonic_seconds":time.monotonic()-start,"path":str(path),"production_accessed":False}
        print(json.dumps(result))
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds",type=int,default=7200)
    observe(parser.parse_args().seconds)
