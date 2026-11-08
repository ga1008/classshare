"""Streaming, read-only PG conservation proof inside an owned synthetic lab.

No DSN/host/database options exist. The controller must validate nonce, physical
PG identity, source and the 200 synthetic identities before the first query.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import ipaddress
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.platform_capacity_linux_lab import validate_container_boundary, _checked_pg_connection
from tools.recorder_capacity_facts import ms, merged_ms


def add_union(ranges, start, end):
    if start >= end:
        return
    output = []
    for left, right in ranges:
        if right < start:
            output.append([left, right])
        elif end < left:
            output.append([start, end])
            start, end = left, right
        else:
            start, end = min(start, left), max(end, right)
    output.append([start, end])
    ranges[:] = output


def read_lines(path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if len(line) > 1024*1024:
                raise ValueError("Oversized client journal line")
            yield json.loads(line)


def receipt_matches(row, batch, actor, offering, payload_hash):
    return bool(row and row[0] == batch and row[1] == actor and row[2] == offering
                and row[3] == "applied" and row[6] == payload_hash)


def valid_client_completion(report):
    mode, duration = report.get("mode"), report.get("duration_requested_seconds", 0)
    return (report.get("client_status") == "passed"
            and ((mode == "run" and duration >= 5400) or (mode == "diagnostic" and 300 <= duration <= 900))
            and bool(report.get("due_ticks")) and report.get("due_ticks") == report.get("sent_ticks")
            and not report.get("normal_errors") and not report.get("legacy_unknown_outcomes")
            and report.get("websocket_checks_passed") is True)


def prove(journal_path, report_path):
    identity = validate_container_boundary()
    artifacts = Path(identity["artifact_root"]).resolve()
    journal_path, report_path = Path(journal_path).resolve(strict=True), Path(report_path).resolve(strict=True)
    if any(not path.is_relative_to(artifacts) for path in (journal_path, report_path)):
        raise ValueError("Facts inputs must remain inside this nonce's artifacts")
    report = json.loads(report_path.read_text())
    if report.get("mode") not in {"run", "diagnostic"}:
        raise ValueError("Only a completed formal or explicitly separate diagnostic run has load facts")
    formal = report["mode"] == "run"
    if report.get("lab_nonce") != identity["lab_nonce"] or report.get("source_release") != identity["marker"]["release_id"]:
        raise ValueError("Client run belongs to another release or nonce")
    fixture = json.loads((artifacts/"attendance-fixture.json").read_text())
    students = {row["student_id"] for row in identity["fixture"]["users"]}
    offering = identity["fixture"]["classOfferingId"]
    if fixture["lab_nonce"] != identity["lab_nonce"] or fixture["offering_id"] != offering:
        raise ValueError("Attendance seed belongs to another physical fixture")
    logins, ack_ids, fingerprints, buffer = {}, set(), {}, []
    rejected_controls, rejected_control_attempts = set(), set()
    legacy_planned, legacy_acked, expected_ops = Counter(), Counter(), Counter()
    legacy_duration, legacy_active = Counter(), Counter()
    learning, elapsed_material, active_material = defaultdict(list), defaultdict(list), defaultdict(list)
    phases = defaultdict(lambda: {"count":0, "min_received_ms":None, "max_received_ms":None,
                                   "min_applied_ms":None, "max_applied_ms":None})
    problems = []
    actual_applied_batches = 0
    conn, _ = _checked_pg_connection(identity["marker"])
    conn.execute("SET default_transaction_read_only=on")
    def problem(code, **details):
        if len(problems) < 40:
            problems.append({"code":code, **details})
    def flush():
        nonlocal actual_applied_batches
        if not buffer:
            return
        keys = [hashlib.sha256(json.dumps(["student", actor, offering, body["batch_id"]]).encode()).hexdigest()
                for actor,body,kind,flow in buffer]
        rows = conn.execute("SELECT batch_id,user_pk,class_offering_id,state,received_at,applied_at,payload_hash "
                            "FROM behavior_write_inbox WHERE batch_key=ANY(%s)", (keys,)).fetchall()
        actual = {row[0]:row for row in rows}
        for actor, body, kind, flow in buffer:
            batch = body["batch_id"]
            row = actual.get(batch)
            if not row or row[1] != actor or row[2] != offering or row[3] != "applied":
                problem("missing_or_non_applied_receipt", batch_id=batch)
                continue
            wire = json.loads(json.dumps(body["events"]))
            if kind == "material_v2":
                context = {key:body.get(key) for key in ("material_id","session_id","scroll_ratio","completed")}
                context["scroll_ratio"] = float(context["scroll_ratio"])
                wire[0]["payload"] = {**wire[0].get("payload",{}), "material_progress_context":context}
            else:
                wire = [{"action_type":event.get("action_type","page_action"),
                         "summary_text":event.get("summary_text",""), "page_key":event.get("page_key"),
                         "payload":event.get("payload",{})} for event in wire]
            expected_hash = hashlib.sha256(json.dumps({"events":wire,"page_key":body.get("page_key")},
                ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
            if expected_hash != row[6]:
                problem("payload_hash_mismatch", batch_id=batch)
                continue
            if not receipt_matches(row, batch, actor, offering, expected_hash):
                problem("receipt_identity_mismatch", batch_id=batch)
                continue
            actual_applied_batches += 1
            received, applied = ms(row[4]), ms(row[5])
            phase = phases[flow]
            phase["count"] += 1
            for key,value,choose in (("min_received_ms",received,min),("max_received_ms",received,max),
                                     ("min_applied_ms",applied,min),("max_applied_ms",applied,max)):
                phase[key] = value if phase[key] is None else choose(phase[key], value)
            for event in body["events"]:
                if event["action_type"] == "page_action":
                    expected_ops[actor] += 1
                payload = event.get("payload",{})
                if payload.get("presence_version") != 2 or "interval_start_ms" not in payload:
                    continue
                if not logins.get(actor):
                    problem("missing_authenticated_login_boundary", student_id=actor)
                    continue
                start = max(payload["interval_start_ms"], ms(logins[actor]))
                end = min(payload["interval_end_ms"], received)
                if start >= end:
                    continue
                if kind == "material_v2":
                    add_union(elapsed_material[actor], start, end)
                active_end = min(end, payload["presence_observed_ms"]-max(0,payload.get("idle_seconds",0)-150)*1000)
                if payload.get("visibility_state") == "visible" and payload.get("focused") is True and start < active_end:
                    add_union(learning[actor], start, active_end)
                    if kind == "material_v2":
                        add_union(active_material[actor], start, active_end)
        buffer.clear()
    try:
        for item in read_lines(journal_path):
            if item.get("run_id") != report["run_id"]:
                raise ValueError("Mixed client journal runs")
            if item["event"] == "authenticated":
                logins[item["actor"]] = item["login_time"]
            if item.get("flow") == "controlled_409":
                if item["event"] == "intent":
                    if item.get("method") != "POST" or item.get("path") != f"/api/classrooms/{offering}/behavior/batch":
                        problem("unexpected_rejected_control_path")
                    rejected_controls.add(item["id"])
                elif item["event"] == "attempt":
                    if item.get("status") != "409":
                        problem("collision_control_was_not_rejected")
                    rejected_control_attempts.add(item["id"])
                continue
            if item["event"] == "batch_result":
                if not item.get("ack"):
                    problem("client_unacknowledged_request", kind=item.get("kind"))
                elif item.get("batch_id"):
                    ack_ids.add(item["batch_id"])
                elif item.get("kind") == "legacy_material":
                    legacy_acked[item["student_id"]] += 1
            if item["event"] != "intent" or item["method"] != "POST":
                continue
            body, actor = item.get("body"), item.get("actor")
            if not isinstance(body, dict) or actor not in students:
                continue
            if item["path"] not in (f"/api/classrooms/{offering}/behavior/batch",
                                   f"/api/classrooms/{offering}/learning/material-progress"):
                continue
            if "batch_id" not in body:
                legacy_planned[actor] += 1
                legacy_duration[actor] += body["duration_seconds"]
                legacy_active[actor] += body["active_seconds"]
                continue
            batch = body["batch_id"]
            digest = hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
            if batch in fingerprints:
                if fingerprints[batch] != digest:
                    problem("client_changed_same_id_body", batch_id=batch)
                continue
            if len(fingerprints) >= 500000:
                raise ValueError("Declared capacity journal batch bound exceeded")
            fingerprints[batch] = digest
            kind = "material_v2" if body.get("progress_version") == 2 else "behavior"
            buffer.append((actor,body,kind,item["flow"]))
            if len(buffer) >= 500:
                flush()
        flush()
        if len(rejected_controls) != (1 if formal else 0) or rejected_controls != rejected_control_attempts:
            problem("collision_control_attempt_missing")
        if set(fingerprints) != ack_ids:
            problem("logical_batch_ack_set_mismatch", planned=len(fingerprints), acked=len(ack_ids))
        if legacy_planned != legacy_acked:
            problem("legacy_planned_ack_mismatch", planned=sum(legacy_planned.values()), acked=sum(legacy_acked.values()))
        actual_ops = dict(conn.execute("SELECT user_pk,COUNT(*) FROM classroom_behavior_events WHERE class_offering_id=%s "
            "AND summary_text=%s GROUP BY user_pk", (offering,"attendance load "+report["run_id"])).fetchall())
        if actual_ops != dict(expected_ops):
            problem("operation_count_mismatch")
        states = conn.execute("SELECT user_pk,learning_online_seconds FROM classroom_behavior_states WHERE class_offering_id=%s "
                              "AND user_role='student' AND user_pk=ANY(%s)", (offering,list(students))).fetchall()
        if len(states) != 200:
            problem("behavior_student_count_mismatch")
        for actor,seconds in states:
            if seconds != merged_ms(learning[actor])//1000:
                problem("learning_union_mismatch", student_id=actor)
        materials = conn.execute("SELECT student_id,accumulated_seconds,active_seconds FROM learning_material_progress "
                                "WHERE class_offering_id=%s AND student_id=ANY(%s)", (offering,list(students))).fetchall()
        if len(materials) != 200:
            problem("material_student_count_mismatch")
        for actor,seconds,active in materials:
            if (seconds != legacy_duration[actor]+merged_ms(elapsed_material[actor])//1000
                    or active != legacy_active[actor]+merged_ms(active_material[actor])//1000):
                problem("material_union_mismatch", student_id=actor)
        pending = conn.execute("SELECT state,COUNT(*) FROM behavior_write_inbox WHERE state IN ('pending','quarantined') GROUP BY state").fetchall()
        if pending:
            problem("unresolved_inbox", states=pending)
        projections = conn.execute("SELECT student_id,dirty,dirty_version,calculated_version FROM learning_progress_snapshots "
            "WHERE class_offering_id=%s AND student_id=ANY(%s)",(offering,list(students))).fetchall()
        if len(projections) != 200 or any(row[1] or row[2] != row[3] for row in projections):
            problem("derived_projection_not_drained")
        global_projections = conn.execute("SELECT COUNT(*) FILTER (WHERE dirty<>0),"
            "COUNT(*) FILTER (WHERE dirty_version IS DISTINCT FROM calculated_version) "
            "FROM learning_progress_snapshots").fetchone()
        pending_counts = dict(pending)
        closed_queues = {"inbox_pending":pending_counts.get("pending",0),
            "inbox_quarantined":pending_counts.get("quarantined",0),
            "dirty_projections":global_projections[0],"projection_version_mismatches":global_projections[1],
            "main_projection_count":len(projections),
            "main_dirty_projections":sum(bool(item[1]) for item in projections),
            "main_projection_version_mismatches":sum(item[2]!=item[3] for item in projections)}
        if global_projections[0] or global_projections[1]:
            problem("global_projection_not_drained")
        round_id = report.get("main_round_id")
        row = conn.execute("SELECT class_offering_id,session_id,state,baseline_json,watermark_json,window_start_ms,window_end_ms,"
                           "starts_at_ms,ends_at_ms,settle_at_ms,evaluated_at_ms "
                           "FROM login_attendance_rounds WHERE id=%s", (round_id,)).fetchone()
        if not row or tuple(row[:2]) != (offering,fixture["sessions"]["main"]):
            problem("main_round_identity_mismatch")
        elif formal and row[2] != "published":
            problem("main_round_not_published")
        records = conn.execute("SELECT student_id,status,manual_confirmed,first_seen_ms,last_seen_ms FROM login_attendance_records WHERE round_id=%s", (round_id,)).fetchall()
        if {r[0] for r in records} != students:
            problem("frozen_roster_mismatch")
        expected_manual = {int(k):v for k,v in report.get("manual_expected",{}).items()}
        ingress = [str(ipaddress.ip_network(net).network_address) for net in fixture["networks"]]
        access = conn.execute("SELECT student_id,MIN(observed_at_ms),MAX(observed_at_ms),"
            "COUNT(*) FILTER (WHERE source='authenticated_request' AND network_source='trusted_proxy' AND session_hash<>'') "
            "FROM login_attendance_access WHERE student_id=ANY(%s) AND observed_at_ms>=%s AND observed_at_ms<%s "
            "AND ip_address=ANY(%s) GROUP BY student_id", (list(students),row[5],row[6],ingress)).fetchall() if row else []
        access_by_actor = {item[0]:item for item in access}
        if set(access_by_actor) != students:
            problem("fresh_ingress_observation_missing")
        for actor,status,manual,first_seen,last_seen in records:
            if formal and (status != expected_manual.get(actor,"PRESENT") or bool(manual) != (actor in expected_manual)):
                problem("attendance_decision_mismatch", student_id=actor)
            raw = access_by_actor.get(actor)
            if raw and (first_seen != raw[1] or last_seen != raw[2] or raw[3] < 1):
                problem("attendance_server_first_last_or_auth_provenance_mismatch", student_id=actor)
        baseline, watermark = (json.loads(row[3]),json.loads(row[4])) if row else ({},{})
        if baseline.get("valid_count") != 200 or baseline.get("main_count") != 200:
            problem("one_person_one_vote_baseline_mismatch", baseline=baseline)
        if formal and (not watermark.get("complete") or watermark.get("pending_batch_count") or watermark.get("quarantined_batch_count")):
            problem("attendance_watermark_incomplete")
        if formal and row and (row[8]-row[7] != report.get("duration_requested_seconds",0)*1000
                or row[6]-row[8] != 600000 or row[9]-row[8] != 720000 or (row[10] or 0) < row[9]
                or watermark.get("observed_through_ms") != row[6] or watermark.get("receipt_cutoff_ms") != row[9]):
            problem("actual_server_window_or_settlement_tail_incomplete")
        burst_phases = {key:value for key,value in phases.items() if key.startswith(("burst600_","headroom1000"))}
        expected_bursts = {"burst600_r1_recorder","burst600_r2_recorder","burst600_r3_recorder","headroom1000_recorder"} if formal else {"burst600_r1_recorder"}
        if set(burst_phases) != expected_bursts or any(value["count"] != 600 or not 0 <= value["max_applied_ms"]-value["max_received_ms"] <= 30000
                                        for value in burst_phases.values()):
            problem("burst_durable_drain_exceeded_or_missing")
        blocked = phases.get("blocked_apply_recorder",{})
        held_path, released_path = artifacts/"apply-held", artifacts/"apply-released"
        blocked_checks = {}
        if held_path.is_file() and released_path.is_file() and blocked:
            held, released = float(held_path.read_text())*1000, float(released_path.read_text())*1000
            blocked_checks = {"all_200_received_after_hold":blocked["count"]==200 and blocked["min_received_ms"]>=held,
                "all_200_applied_after_release":blocked["min_applied_ms"]>=released,
                "hold_seconds_server":(released-held)/1000,"drain_seconds_after_release":(blocked["max_applied_ms"]-released)/1000}
        if (not blocked_checks.get("all_200_received_after_hold") or not blocked_checks.get("all_200_applied_after_release")
                or not 15 <= blocked_checks.get("hold_seconds_server",0) <= 17
                or not 0 <= blocked_checks.get("drain_seconds_after_release",-1) <= 30):
            problem("blocked_apply_server_clock_proof_failed")
        if not valid_client_completion(report):
            problem("client_load_gate_failed")
        server_window = ({"round_id":round_id,"starts_at_ms":row[7],"ends_at_ms":row[8],
            "window_start_ms":row[5],"window_end_ms":row[6],"settle_at_ms":row[9],
            "evaluated_at_ms":row[10],"published_state":row[2],
            "observed_through_ms":watermark.get("observed_through_ms"),
            "receipt_cutoff_ms":watermark.get("receipt_cutoff_ms"),
            "watermark_complete":watermark.get("complete") is True} if row else {})
        result = {"status":"passed" if not problems else "failed", "lab_nonce":identity["lab_nonce"],
            "mode":report["mode"],"capacity_result":False,"actual_applied_batches":actual_applied_batches,
            "run_id":report["run_id"], "source_release":report["source_release"], "logical_batches":len(fingerprints),
            "acked_unique_batches":len(ack_ids), "legacy_planned":sum(legacy_planned.values()), "legacy_acked":sum(legacy_acked.values()),
            "expected_operations":sum(expected_ops.values()), "actual_operations":sum(actual_ops.values()),
            "expected_learning_seconds":sum(merged_ms(r)//1000 for r in learning.values()),
            "actual_learning_seconds":sum(row[1] or 0 for row in states),
            "material_students":len(materials), "attendance_students":len(records),
            "attendance_status_counts":dict(Counter(row[1] for row in records)), "baseline":baseline,
            "watermark":watermark, "receipt_phase_server_times":dict(phases), "blocked_checks":blocked_checks,
            "server_window":server_window,"closed_queues":closed_queues,
            "all_projection_generations_clean_and_current":not any(p["code"]=="derived_projection_not_drained" for p in problems),
            "problems":problems,
            "clock_domain":"Receipt and apply times are VM/server PG timestamps; no client epoch subtraction",
            "production_accessed":False, "oracle":"Raw indexed PG receipts plus independent interval unions; streamed journal in batches of500"}
        output = artifacts/("attendance-facts-"+report["run_id"]+".json")
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in result.items() if k not in ("baseline","receipt_phase_server_times")}))
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal",required=True)
    parser.add_argument("--client-report",required=True)
    args = parser.parse_args()
    prove(args.journal,args.client_report)
