"""Cold-entry and full-quiz admission waves through real ASGI routes/local PG.

No workers are started: this measures command acceptance and ledger integrity,
not generation time. Small smoke by default; formal waves require --formal.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import random
import sys
import time
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
import psycopg
from psycopg_pool import ConnectionPool
from classroom_app import config
from classroom_app.db.postgres import LanSharePostgresConnection, sqlite_compatible_dict_row
from classroom_app.services import career_path_service as career, student_career_job_service as jobs
from tools.career_postgres_workflow_probe import isolated_career_postgres

MAJORS = ("英语", "网络工程", "护理学", "会计学", "汉语言文学", "环境设计", "旅游管理",
          "机械设计制造及其自动化", "国际经济与贸易", "学前教育")


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return {"count": 0}
    return {"count": len(ordered), **{f"p{p}_ms": round(ordered[math.ceil(len(ordered)*p/100)-1], 3) for p in (50, 95, 99)},
            "max_ms": round(ordered[-1], 3)}


def complete_answers(questions, student_id):
    answers = []
    for question in questions:
        if question["kind"] in ("single", "multi"):
            value = question["options"][student_id % len(question["options"])]["value"]
            if question["kind"] == "multi":
                value = [value]
        elif question["kind"] == "scale":
            value = 1 + student_id % 5
        else:
            value = f"合成完整问卷学生{student_id}，希望通过实践核对方向。"
        answers.append({"question_id": question["id"], "value": value})
    return answers


async def open_loop(items, duration, max_inflight, action):
    """Dispatch independently of completion; bounded overload is explicit loss."""
    started = time.perf_counter(); active = set(); skipped = []; lag = []; errors = []
    def done(task):
        active.discard(task)
        error = "CancelledError" if task.cancelled() else task.exception()
        if error is not None:
            errors.append(str(error) if isinstance(error, str) else type(error).__name__)
    for index, item in enumerate(items):
        due = started + index * duration / len(items)
        await asyncio.sleep(max(0, due - time.perf_counter()))
        if time.perf_counter() >= started + duration:
            skipped.extend(items[index:]); break
        lag.append((time.perf_counter() - due) * 1000)
        if len(active) >= max_inflight:
            skipped.append(item); continue
        task = asyncio.create_task(action(item)); active.add(task); task.add_done_callback(done)
    if active:
        await asyncio.gather(*active, return_exceptions=True)
    # Preserve the specified open-loop window even when the last response is fast.
    await asyncio.sleep(max(0, started + duration - time.perf_counter()))
    return {"window_seconds": duration, "elapsed_including_response_drain_seconds": round(time.perf_counter()-started, 3),
            "skipped": skipped, "task_errors": errors, "dispatch_lag": distribution(lag)}


def hashes():
    paths = [Path(__file__), ROOT/"tools/career_postgres_workflow_probe.py", ROOT/"tools/resume_postgres_workflow_probe.py",
             ROOT/"classroom_app/routers/career_path.py", ROOT/"classroom_app/services/student_career_job_service.py",
             ROOT/"classroom_app/services/ai_durable_job_service.py", ROOT/"classroom_app/config.py"]
    paths += list((ROOT/"classroom_app/services").glob("career_*.py"))
    paths += list((ROOT/"classroom_app/db").glob("*.py"))
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


async def exercise(fixture, connect, args, progress):
    responses = progress.setdefault("response_by_student", {}); initial = {}; question_sets = {}; prepared = {}
    controls = {}; shared_by_major = {}; observations = []
    # No seeded software catalogue: all ten professional networks start cold.
    with connect() as conn:
        for index, major in enumerate(MAJORS, 1):
            conn.execute("INSERT INTO classes(id,name,major,program_duration_years) VALUES(?,?,?,4) ON CONFLICT(id) DO UPDATE SET major=EXCLUDED.major",
                         (index, f"进入波峰合成班{index}", major))
        conn.execute("UPDATE students SET class_id=(id-1)%10+1 WHERE id<=?", (args.users+1,))
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM career_major_networks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM career_student_sessions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 0

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fixture["app"], raise_app_exceptions=False),
                                 base_url="http://isolated-admission-probe", timeout=30) as client:
        async def request(sid, path, body=None, method="POST"):
            return await client.request(method, "/api/career-path/" + path, json=body,
                                        headers={"x-probe-student": str(sid)})

        def verify_state(sid, state):
            assert state["ok"] and state["student"]["name"] == f"合成学生{sid}"
            assert state["major"]["name"] == MAJORS[(sid-1) % 10]
            assert state["network"]["nodes"] and state["network_source"] == "baseline"
            network_job = state["tasks"]["network"]
            assert network_job["id"] and network_job["status"] == "queued"
            shared_by_major.setdefault(state["major"]["name"], set()).add(network_job["id"])

        if args.scenario == "quiz":
            # Prerequisite initialization is outside the questionnaire wave.
            semaphore = asyncio.Semaphore(12)
            async def initialize(sid):
                async with semaphore:
                    response = await request(sid, "initialize"); assert response.status_code == 200
                    state = response.json(); verify_state(sid, state); initial[sid] = state
            await asyncio.gather(*(initialize(sid) for sid in range(1,args.users+1)))
            for sid in range(1, 11):
                response = await request(sid, "questions?mode=full", method="GET"); assert response.status_code == 200
                question_sets[(sid-1) % 10] = response.json()
            for sid in range(1,args.users+1):
                questions = question_sets[(sid-1) % 10]
                prepared[sid] = {"answers": complete_answers(questions["questions"], sid), "mode": "full",
                                 "quiz_version": questions["quiz_version"], "revision": initial[sid]["revision"], "enhance": True}
        order = list(range(1,args.users+1)); random.Random(args.seed).shuffle(order)

        async def command(sid):
            started = time.perf_counter()
            response = await request(sid, "initialize" if args.scenario == "entry" else "answers", prepared.get(sid))
            elapsed = (time.perf_counter()-started)*1000
            item = {"student_id": sid, "status": response.status_code, "latency_ms": round(elapsed, 3),
                    "response_bytes": len(response.content)}
            responses[sid] = item
            if response.status_code != 200:
                item["error_code"] = "http_non_success"; return
            raw = response.json(); state = raw if args.scenario == "entry" else raw["state"]
            verify_state(sid, state)
            item.update(revision=state["revision"], network_job_id=state["tasks"]["network"]["id"],
                        network_version=state["network_version"], major=state["major"]["name"])
            if args.scenario == "quiz":
                task = state["tasks"]["personalization"]
                assert state["phase"] == "ready" and state["quiz_mode"] == "full"
                assert task["id"] and task["status"] == "queued"
                item["personal_job_id"] = task["id"]

        wave = await open_loop(order, args.duration, args.max_inflight, command)
        progress.update(wave=wave, response_statuses=dict(Counter(str(value["status"]) for value in responses.values())),
                        command_admission_latency=distribution([value["latency_ms"] for value in responses.values()]),
                        last_stage="wave_dispatched")
        assert not wave["skipped"] and not wave["task_errors"], wave
        assert len(responses) == args.users and all(item["status"] == 200 for item in responses.values())
        assert len(shared_by_major) == 10 and all(len(ids) == 1 for ids in shared_by_major.values())

        with connect() as conn:
            networks = [dict(row) for row in conn.execute("SELECT * FROM career_major_networks ORDER BY id")]
            ledger = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM ai_jobs")}
            sessions = {row["student_id"]: dict(row) for row in conn.execute("SELECT * FROM career_student_sessions")}
            assert len(networks) == 10 and all(row["status"] == "queued" and row["generation"] == 1 for row in networks)
            assert all(json.loads(row["network_json"]) == {} and row["revision"] == 0 for row in networks)
            for row in networks:
                job = ledger[row["job_id"]]; payload = json.loads(job["payload_json"])
                assert job["task_type"] == career.NETWORK_GENERATE_TASK_KIND and job["owner_user_pk"] is None
                assert payload["school_code"] == row["school_code"] and payload["major_key"] == row["major_key"] and payload["generation"] == 1
            for sid, result in responses.items():
                assert result["network_job_id"] in ledger and sessions[sid]["major_name"] == result["major"]
                if args.scenario == "quiz":
                    job = ledger[result["personal_job_id"]]; payload = json.loads(job["payload_json"])
                    assert job["owner_role"] == "student" and job["owner_user_pk"] == sid
                    assert payload["student_id"] == sid and payload["revision"] == sessions[sid]["revision"]
                    assert payload["input_hash"] == sessions[sid]["input_hash"]
                    assert len(json.loads(sessions[sid]["test_answers_json"])) == len(prepared[sid]["answers"])
                    assert sessions[sid]["personal_job_id"] == job["id"] and sessions[sid]["submitted_at"]
            expected = 10 + (args.users if args.scenario == "quiz" else 0)
            assert len(ledger) == expected and all(job["attempt_count"] == 0 and job["status"] == "queued" for job in ledger.values())
            assert conn.execute("SELECT COUNT(*) FROM ai_job_results").fetchone()[0] == 0

        # Duplicate controls are separate from wave timing and request counts.
        sample = list(range(1, min(20,args.users)+1))
        async def duplicate(sid):
            if args.scenario == "entry":
                reply = await request(sid,"initialize"); assert reply.status_code == 200
                assert reply.json()["tasks"]["network"]["id"] == responses[sid]["network_job_id"]
            else:
                reply = await request(sid,"retry",{"target":"personalization", "revision":responses[sid]["revision"], "job_id":responses[sid]["personal_job_id"]})
                assert reply.status_code == 200 and reply.json()["tasks"]["personalization"]["id"] == responses[sid]["personal_job_id"]
        await asyncio.gather(*(duplicate(sid) for sid in sample for _ in range(3)))
        if args.scenario == "quiz":
            stale = await request(1,"answers",prepared[1]); assert stale.status_code == 409
            wrong_owner = await request(2,"retry",{"target":"personalization", "revision":responses[2]["revision"], "job_id":responses[1]["personal_job_id"]})
            assert wrong_owner.status_code == 409
            controls["stale_questionnaire_replay_409"] = True; controls["foreign_job_command_rejected_409"] = True
        with connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == expected
        controls["parallel_duplicate_requests"] = len(sample) * 3
        controls["duplicates_created_zero_new_jobs"] = True
        progress.update(last_stage="wave_and_duplicate_controls_verified",controls_outside_main_wave=controls)

        # Force a test-only capacity boundary after the exact main wave. This
        # does not change production limits or inflate measured wave throughput.
        sid = args.users+1
        init = await request(sid,"initialize"); assert init.status_code == 200
        control_state = init.json()
        questions_reply = await request(sid,"questions?mode=full",method="GET"); questions = questions_reply.json()
        body = {"answers":complete_answers(questions["questions"],sid), "mode":"full", "quiz_version":questions["quiz_version"],
                "revision":control_state["revision"], "enhance":True}
        with connect() as conn:
            active_count = conn.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0]
            before_session = dict(conn.execute("SELECT * FROM career_student_sessions WHERE student_id=?",(sid,)).fetchone())
        with patch.object(jobs,"MAX_PENDING_JOBS",active_count):
            overloaded = await request(sid,"answers",body)
        assert overloaded.status_code == 429 and overloaded.headers.get("Retry-After") == "30"
        with connect() as conn:
            assert dict(conn.execute("SELECT * FROM career_student_sessions WHERE student_id=?",(sid,)).fetchone()) == before_session
            assert conn.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == active_count
        recovered = await request(sid,"answers",body); assert recovered.status_code == 200
        recovered_state = recovered.json()["state"]; recovered_job_id = recovered_state["tasks"]["personalization"]["id"]
        with connect() as conn:
            owner = conn.execute("SELECT owner_user_pk,status FROM ai_jobs WHERE id=?",(recovered_job_id,)).fetchone()
            assert owner["owner_user_pk"] == sid and owner["status"] == "queued"
        controls.update(overload_http_status=429, overload_retry_after_seconds=30, overload_rolled_back_command=True,
                        capacity_restored_same_request_accepted=True, overload_scope="separate post-wave test-only MAX_PENDING_JOBS=current_active_count")

        observations = sorted(responses.values(),key=lambda value:value["student_id"])
        return {"wave": wave, "responses": observations, "response_statuses": dict(Counter(str(value["status"]) for value in observations)),
                "command_admission_latency": distribution([value["latency_ms"] for value in observations]),
                "successful_commands_traced": len(observations), "main_wave_jobs_traced": expected,
                "shared_network_tasks": {major: next(iter(ids)) for major,ids in shared_by_major.items()},
                "full_question_count_by_major": {MAJORS[index]:len(data["questions"]) for index,data in question_sets.items()},
                "controls_outside_main_wave": controls, "generation_attempts": 0, "generated_results": 0}


def validate_args(args):
    if not (10 <= args.users <= 1000 and .1 <= args.duration <= 60 and 1 <= args.max_inflight <= 200):
        raise ValueError("users 10..1000, duration 0.1..60, inflight 1..200 required")
    if (args.users > 100 or args.duration > 10) and not args.formal:
        raise ValueError("Use --formal for the root-scheduled formal wave; defaults are smoke only")


def run(args):
    validate_args(args)
    before = hashes(); output = Path(args.output).resolve()
    if output.exists():
        raise ValueError("Use a new evidence output name")
    output.parent.mkdir(parents=True,exist_ok=True)
    fixture = None; removed = False; left = None; details = {}; progress = {}; failure = None; pool_stats = {}
    try:
        with isolated_career_postgres(students=args.users+1) as fixture, ExitStack() as stack:
            schema = fixture["schema"]
            pool = ConnectionPool(config.DATABASE_URL,min_size=2,max_size=12,timeout=10,
                kwargs={"row_factory":sqlite_compatible_dict_row,"options":f"-c search_path={schema} -c application_name=career-wave-{schema} -c statement_timeout=30000 -c lock_timeout=5000"},open=True)
            pool.wait(timeout=15); stack.callback(pool.close)
            def connect():
                return LanSharePostgresConnection(pool.getconn(timeout=10),pool=pool)
            for name,module in list(sys.modules.items()):
                if name.startswith("classroom_app") and hasattr(module,"get_db_connection"):
                    stack.enter_context(patch.object(module,"get_db_connection",connect))
            details = asyncio.run(exercise(fixture,connect,args,progress))
            pool_stats = pool.get_stats()
    except Exception as exc:
        # Preserve failed-wave evidence as well as finally cleanup. All request
        # rows are synthetic; never include a database URL or exception body.
        failure = {"error_type":type(exc).__name__,"last_stage":progress.get("last_stage","setup_or_request")}
    finally:
        if fixture:
            with psycopg.connect(config.DATABASE_URL,connect_timeout=5,autocommit=True) as admin:
                removed = admin.execute("SELECT COUNT(*) FROM pg_namespace WHERE nspname=%s",(fixture["schema"],)).fetchone()[0] == 0
                left = admin.execute("SELECT COUNT(*) FROM pg_stat_activity WHERE application_name=%s",("career-wave-"+fixture["schema"],)).fetchone()[0]
            assert removed and left == 0
    after = hashes(); changed = sorted(name for name in set(before)|set(after) if before.get(name)!=after.get(name))
    if failure:
        details = {key:value for key,value in progress.items() if key!="response_by_student"}
        details["responses"] = list(progress.get("response_by_student",{}).values())
    report = {"ok":not failure and not changed and removed,"failure":failure,"configuration":vars(args),"synthetic_only":True,"majors":list(MAJORS),
              "measurement_scope":"Real FastAPI ASGI command admission and PostgreSQL transactions, pool max12. No TCP/TLS/session middleware, workers, real AI, Office or email. HTTP 200 is the current career API's accepted-and-persisted contract; task completion is not measured.",
              "dataset_scope":"Ten cold majors, real student/class rows, fixture-only seed resume materials for students1/2; no generated network cache. Quiz initialization and validation/deduplication/overload controls are outside main-wave latency.",
              "pool":pool_stats,"schema_removed":removed,"owned_connections_remaining":left,
              "fixed_code_during_run":not changed,"sources_changed_during_run":changed,"source_hashes_before":before,**details}
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return report


def parser():
    result=argparse.ArgumentParser(description=__doc__)
    result.add_argument("--scenario",choices=("entry","quiz"),default="entry")
    result.add_argument("--users",type=int,default=30)
    result.add_argument("--duration",type=float,default=3)
    result.add_argument("--max-inflight",type=int,default=100)
    result.add_argument("--seed",type=int,default=42)
    result.add_argument("--formal",action="store_true")
    result.add_argument("--output",default=".codex-temp/career-admission-wave-smoke.json")
    return result


if __name__=="__main__":
    args=parser().parse_args(); report=run(args)
    print(json.dumps({key:report.get(key) for key in ("ok","configuration","failure","command_admission_latency","main_wave_jobs_traced",
        "controls_outside_main_wave","schema_removed","owned_connections_remaining","sources_changed_during_run")},ensure_ascii=False,indent=2))
    raise SystemExit(0 if report["ok"] else 1)
