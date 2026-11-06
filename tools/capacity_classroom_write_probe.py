"""One real classroom poll: publish, 200 votes, repeat rejection, close and ACL.

Run only after the capacity soak: real logins replace the synthetic users'
sessions. Native execution requires the existing explicit loopback fixture.
The protocol coroutine is also exercised with isolated SQLite/ASGI tests.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
import json
import math
import os
from pathlib import Path
import ssl
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class PollProbeFailure(AssertionError):
    pass


def require(condition, message):
    if not condition:
        raise PollProbeFailure(message)


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {"count": 0}
    return {"count": len(ordered), "p50_ms": round(ordered[math.ceil(len(ordered)*0.5)-1], 2),
            "p95_ms": round(ordered[math.ceil(len(ordered)*0.95)-1], 2), "max_ms": round(ordered[-1], 2)}


def require_finished_soak(runtime: Path, explicit_after_soak: bool) -> None:
    require(explicit_after_soak, "Explicit --after-soak is required because real logins replace existing sessions")
    status_path = runtime / "capacity-report.json"
    require(status_path.is_file(), "The existing synthetic capacity report is required")
    state = json.loads(status_path.read_text(encoding="utf-8"))
    require(state.get("status") in {"passed", "failed", "aborted", "failed_login_burst", "failed_socket_startup"},
            "The capacity soak is still running or has no terminal report; no logins or PG probe may start")


async def exercise_poll(client, *, offering_id, teacher_token, students, reserve_token, outsider_token, report=None):
    """Exercise the actual /api/polls contract; every student has a private cookie."""
    report = report if report is not None else {}
    require(len(students) == 200 and len({student for student, _ in students}) == 200, "Exactly 200 distinct voters are required")
    timings, statuses = defaultdict(list), Counter()
    report.update(run_id=uuid.uuid4().hex, status="running", users=200, errors=[])
    poll_id, closed = None, False
    choices = {}

    async def call(label, method, route, token, expected=200, **kwargs):
        started = time.perf_counter()
        try:
            response = await client.request(method, route, headers={"Cookie": f"access_token={token}"}, **kwargs)
        except Exception as exc:
            report["errors"].append({"phase": label, "exception": type(exc).__name__})
            raise
        timings[label].append((time.perf_counter()-started)*1000)
        statuses[f"{label}:{response.status_code}"] += 1
        if response.status_code != expected:
            report["errors"].append({"phase": label, "status": response.status_code, "expected": expected,
                                     "request_id": response.headers.get("x-request-id")})
            raise PollProbeFailure(f"{label} returned HTTP {response.status_code}, expected {expected}")
        return response

    async def all_calls(operations):
        results = await asyncio.gather(*operations, return_exceptions=True)
        failures = [result for result in results if isinstance(result, BaseException)]
        if failures:
            raise PollProbeFailure(f"{len(failures)} concurrent poll operations failed ({type(failures[0]).__name__})")
        return results

    try:
        title = "合成容量课堂投票 " + report["run_id"]
        body = {"title": title, "description": "owned synthetic capacity poll", "vote_type": "single",
                "options": ["方案甲", "方案乙"], "status": "draft", "allow_change": False,
                "result_visibility": "after_vote"}
        created = (await call("create", "POST", f"/api/polls/classrooms/{offering_id}/polls", teacher_token, json=body)).json()["poll"]
        poll_id = int(created["id"])
        report.update(poll_id=poll_id, title=title)
        require(created["status"] == "draft" and created["total_voters"] == 0, "Poll creation did not preserve its empty draft")
        option_ids = [int(option["id"]) for option in created["options"]]
        require(len(option_ids) == 2 and len(set(option_ids)) == 2, "Actual service did not create two distinct options")
        published = (await call("publish", "POST", f"/api/polls/{poll_id}/status", teacher_token, json={"status": "active"})).json()["poll"]
        require(published["status"] == "active" and published["origin"] == "classroom", "The classroom poll was not published")
        await call("nonmember", "POST", f"/api/polls/{poll_id}/vote", outsider_token, expected=403,
                   json={"option_ids": [option_ids[0]]})

        choices = {int(student): option_ids[index % 2] for index, (student, _) in enumerate(students)}
        async def vote(student, token):
            poll = (await call("vote", "POST", f"/api/polls/{poll_id}/vote", token,
                               json={"option_ids": [choices[student]]})).json()["poll"]
            require(int(poll["id"]) == poll_id and poll["has_voted"] and poll["my_option_ids"] == [choices[student]],
                    "A successful receipt does not match that student's ballot")
            return student
        acknowledgements = await all_calls(vote(student, token) for student, token in students)
        require(set(acknowledgements) == set(choices), "Not all 200 unique students received a matching acknowledgement")
        report["matching_vote_receipts"] = len(acknowledgements)

        # Existing no-change polls reject repeats rather than returning success.
        # Assert the actual contract and then prove the persisted choice did not change.
        duplicates = await all_calls(call("duplicate", "POST", f"/api/polls/{poll_id}/vote", token, expected=400,
                                           json={"option_ids": [choices[student]]}) for student, token in students)
        require(all(response.json().get("detail") == "该投票不允许修改" for response in duplicates), "A repeat was rejected for an unexpected reason")
        report["duplicate_rejections"] = len(duplicates)

        async def verify_member(student, token):
            poll = (await call("member-detail", "GET", f"/api/polls/{poll_id}", token)).json()["poll"]
            require(poll["total_voters"] == 200 and poll["my_option_ids"] == [choices[student]] and poll["change_count"] == 0,
                    "Final member view differs from its original immutable ballot")
        await all_calls(verify_member(student, token) for student, token in students)
        report["matching_member_reads"] = 200
        teacher_result = (await call("teacher-results", "GET", f"/api/polls/{poll_id}", teacher_token)).json()["poll"]
        expected_counts = dict(Counter(choices.values()))
        actual_counts = {int(option["id"]): int(option["count"]) for option in teacher_result["options"]}
        require(teacher_result["show_results"] and teacher_result["total_voters"] == 200 and actual_counts == expected_counts,
                "Teacher totals disagree with the 200 acknowledged ballots")
        report.update(teacher_total_voters=200, expected_option_counts=expected_counts, actual_option_counts=actual_counts)

        reserve = (await call("reserve-before-close", "GET", f"/api/polls/{poll_id}", reserve_token)).json()["poll"]
        require(reserve["can_vote"] and not reserve["has_voted"], "Closure check requires an eligible student who has never voted")
        closure = (await call("close", "POST", f"/api/polls/{poll_id}/status", teacher_token, json={"status": "closed"})).json()["poll"]
        require(closure["status"] == "closed", "Teacher did not close the poll")
        closed = True
        rejected = await call("closed-new-vote", "POST", f"/api/polls/{poll_id}/vote", reserve_token, expected=400,
                              json={"option_ids": [option_ids[0]]})
        require(rejected.json().get("detail") == "该投票已结束或尚未开始", "The new vote was not rejected by the closure rule")
        final = (await call("closed-results", "GET", f"/api/polls/{poll_id}", teacher_token)).json()["poll"]
        require(final["status"] == "closed" and final["total_voters"] == 200, "Closure or rejected writes changed the final total")
        report.update(status="passed", nonmember_denied=True, eligible_new_vote_denied_after_close=True,
                      repeat_semantics="HTTP400 for a no-change poll; original ballots preserved", unexpected_http_errors=0)
        return report, choices
    except BaseException as exc:
        report.update(status="failed", failure=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if poll_id and not closed:
            try:
                await call("cleanup-close", "POST", f"/api/polls/{poll_id}/status", teacher_token, json={"status": "closed"})
            except Exception as exc:
                report["errors"].append({"phase": "cleanup-close", "exception": type(exc).__name__})
        report["http"] = dict(statuses)
        report["latency"] = {label: distribution(values) for label, values in timings.items()}


async def run_native(args, runtime, fixture, report):
    import httpx
    from jose import jwt
    from tools.platform_capacity_background import validate_fixture
    from classroom_app.database import get_db_connection

    _, cohort = validate_fixture(args, runtime, fixture)
    with get_db_connection() as conn:
        rows = conn.execute("SELECT id,student_id_number FROM students WHERE student_id_number LIKE 'CAPACITY-SYNTHETIC-%' ORDER BY student_id_number LIMIT 200").fetchall()
        identifiers = {int(row["id"]): str(row["student_id_number"]) for row in rows}
        require(all(student in identifiers for student in cohort), "Validated capacity cohort differs from its login identities")
        students = [(student, identifiers[student]) for student in cohort]
    require(len(students) == 200, "The existing capacity identities have not been seeded")
    base = f"https://127.0.0.1:{args.proxy_port}" if args.proxy_port else f"http://127.0.0.1:{args.http_port}"
    tls = ssl.create_default_context(cafile=str(ROOT / ".codex-temp/nginx-capacity-native/nginx-1.27.5/capacity-test.crt")) if args.proxy_port else ssl.create_default_context()
    login_times = []
    login_statuses = Counter()
    async def login(role, identifier, expected_id):
        async with httpx.AsyncClient(base_url=base, timeout=60, trust_env=False, verify=tls, follow_redirects=False) as client:
            started = time.perf_counter()
            response = await client.post("/teacher/login" if role == "teacher" else "/api/student/login/password",
                data={"identifier": identifier, "password": fixture["password"], "next": "/dashboard"})
            login_times.append((time.perf_counter()-started)*1000)
            login_statuses[f"{role}:{response.status_code}"] += 1
            require(response.status_code == (303 if role == "teacher" else 200), f"Synthetic {role} login failed: HTTP{response.status_code}")
            token = response.cookies.get("access_token")
            require(bool(token), f"Synthetic {role} login did not issue a session")
            claims = jwt.decode(token, os.environ["SECRET_KEY"], algorithms=["HS256"])
            require(claims.get("role") == role and int(claims.get("id") or 0) == int(expected_id), "Login returned a different synthetic identity")
            return token
    identities = [("teacher", fixture["teacher"]["email"], int(fixture["teacher"]["id"]))]
    identities += [("student", identifier, student) for student, identifier in students]
    identities += [("student", fixture[key]["studentNumber"], int(fixture[key]["id"])) for key in ("student", "otherStudent")]
    sessions = await asyncio.gather(*(login(*identity) for identity in identities), return_exceptions=True)
    report["login_latency"] = distribution(login_times)
    report["login_http"] = dict(login_statuses)
    report["successful_real_logins"] = sum(isinstance(value, str) for value in sessions)
    report["login_errors"] = dict(Counter(type(value).__name__ for value in sessions if isinstance(value, BaseException)))
    require(all(isinstance(value, str) for value in sessions), "One or more real synthetic logins failed")
    async with httpx.AsyncClient(base_url=base, timeout=60, trust_env=False, verify=tls,
            limits=httpx.Limits(max_connections=240, max_keepalive_connections=200)) as client:
        report, choices = await exercise_poll(client, offering_id=fixture["classOfferingId"], teacher_token=sessions[0],
            students=[(student, sessions[index+1]) for index, (student, _) in enumerate(students)],
            reserve_token=sessions[-2], outsider_token=sessions[-1], report=report)
    # Verify rows, not merely API counters. The poll was created through its
    # real classroom HTTP service and is retained closed as synthetic evidence.
    with get_db_connection() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        poll = conn.execute("SELECT title,owner_user_pk,origin_class_offering_id,status FROM polls WHERE id=?", (report["poll_id"],)).fetchone()
        require(poll["title"] == report["title"] and int(poll["owner_user_pk"]) == int(fixture["teacher"]["id"])
                and int(poll["origin_class_offering_id"]) == int(fixture["classOfferingId"]) and poll["status"] == "closed", "Persisted poll ownership mismatch")
        ballots = conn.execute("SELECT voter_id,change_count FROM poll_ballots WHERE poll_id=?", (report["poll_id"],)).fetchall()
        votes = conn.execute("SELECT voter_id,option_id FROM poll_votes WHERE poll_id=?", (report["poll_id"],)).fetchall()
        require(len(ballots) == 200 and {int(row["voter_id"]) for row in ballots} == set(choices)
                and all(int(row["change_count"]) == 0 for row in ballots), "Persisted ballots are not exactly the 200 acknowledged voters")
        require(len(votes) == 200 and {int(row["voter_id"]): int(row["option_id"]) for row in votes} == choices,
                "Persisted votes differ from successful receipts")
    report.update(unique_ballots=200, persisted_votes=200, persisted_choices_match_all_receipts=True,
                  production_data_modified=False, scope="Native synthetic classroom poll only; measures raw HTTP latency without certifying AI or full-platform capacity")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--cluster-dir", type=Path, required=True)
    parser.add_argument("--pg-port", type=int, required=True)
    parser.add_argument("--http-port", type=int, default=18767)
    parser.add_argument("--proxy-port", type=int, default=0)
    parser.add_argument("--after-soak", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.users = 200
    runtime = args.runtime.resolve(strict=True)
    temporary = (ROOT / ".codex-temp").resolve()
    require(runtime != temporary and runtime.is_relative_to(temporary), "Only an owned synthetic .codex-temp runtime is permitted")
    require_finished_soak(runtime, args.after_soak)  # Must precede every PG connection.
    require(args.cluster_dir.resolve(strict=True) == (ROOT / ".codex-temp/platform-capacity-20261105-pg").resolve(), "This task's exact cluster directory is required")
    output = args.output.resolve()
    require(output != temporary and output.is_relative_to(temporary), "Report must stay within .codex-temp")
    from tools.platform_capacity_probe import establish_boundary
    runtime, fixture = establish_boundary(args)
    report = {"status": "starting", "production_data_modified": False}
    try:
        asyncio.run(run_native(args, runtime, fixture, report))
    except BaseException as exc:
        report.update(status="failed", failure=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        from classroom_app.db.postgres import close_connection_pool
        close_connection_pool()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
