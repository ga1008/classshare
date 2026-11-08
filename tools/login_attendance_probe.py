"""Real HTTP lifecycle/capacity client for an explicitly owned synthetic lab.

Only loopback HTTPS plus the controller's pinned CA/source/nonce handoff is
accepted. This client has no database access or production discovery. A smoke
run is deliberately distinct from a 90-minute capacity result.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextvars
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import ssl
import sys
import time
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import websockets

from tools.login_attendance_lab import (check_student_dto, load_attendance_fixture,
                                        periodic_offsets, acceptable_attempt_status)


def distribution(values):
    values = sorted(values)
    return {"count": len(values), **({f"p{p}_ms": round(values[math.ceil(len(values)*p/100)-1], 3)
            for p in (50, 95, 99)} if values else {}), "max_ms": max(values) if values else None}


def round_value(value):
    candidate = value.get("round", value) if isinstance(value, dict) else None
    if (not isinstance(candidate, dict) or type(candidate.get("id")) is not int
            or type(candidate.get("revision")) is not int or not isinstance(candidate.get("state"), str)):
        raise AssertionError("Attendance round response contract unavailable")
    return candidate


class Probe:
    def __init__(self, args):
        self.args = args
        self.handoff, self.fixture = load_attendance_fixture(args.work, args.source_root,
            args.base, args.lab_nonce, args.release_id, args.fixture_sha256)
        self.work = Path(args.work).resolve(strict=True)
        self.run_id = uuid.uuid4().hex
        self.output = self.work / self.run_id
        self.output.mkdir()
        self.journal = (self.output / "request-journal.jsonl").open("x", encoding="utf-8")
        self.latency = defaultdict(list)
        self.statuses = Counter()
        self.logical = Counter()
        self.tokens = {}
        self.login_times = {}
        self.teacher_token = None
        self.viewer_tokens = {}
        self.contracts = []
        self.completed = False
        self.ws = []
        self.ws_tasks = []
        self.ws_counts = Counter()
        self.ws_samples = []
        self.ws_expected = 0
        self.ws_planned = False
        self.ws_stopping = False
        self.phase_results = []
        self.schedule_lateness = []
        self.due_ticks = Counter()
        self.sent_ticks = Counter()
        self.batch_acks = Counter()
        self.started_monotonic = None
        self.in_steady = False
        self.planned_outage = False
        self.normal_errors = Counter()
        self.tick_offset = contextvars.ContextVar("attendance_due_offset", default=None)
        self.legacy_queue = asyncio.Queue(maxsize=256)
        self.legacy_gate = asyncio.Event()
        self.legacy_gate.set()
        self.legacy_inflight = 0
        self.legacy_queue_peak = 0
        self.legacy_delay = []
        self.legacy_failures = []
        self.manual_expected = {}
        self.recorder_successes = 0
        self.discarded_ack_replays = 0
        self.prefix = f"/api/classrooms/{self.fixture['offering_id']}/login-attendance"
        self.client = None

    def write(self, event):
        self.journal.write(json.dumps({"run_id": self.run_id, **event}, ensure_ascii=False)+"\n")
        self.journal.flush()

    def prepare_intent(self, flow, method, path, *, body=None, form=None, actor=None, scheduled_ms=None,
                       declared_burst=False):
        logical_id = uuid.uuid4().hex
        canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")) if body is not None else None
        self.logical[flow] += 1
        self.write({"event": "intent", "id": logical_id, "flow": flow, "method": method,
            "path": path, "actor": actor, "body": body,
            "body_sha256": hashlib.sha256(canonical.encode()).hexdigest() if canonical else None,
            "form_redacted": form is not None, "scheduled_offset_ms": self.tick_offset.get() if scheduled_ms is None else scheduled_ms,
            "declared_burst": declared_burst is True,
            "client_epoch": time.time()})
        return logical_id

    async def request(self, flow, method, path, *, token=None, body=None, form=None,
                      actor=None, retries=0, scheduled_ms=None, intent_id=None, expected_statuses=(200,201,202),
                      declared_burst=False):
        logical_id = intent_id or self.prepare_intent(flow, method, path, body=body, form=form,
                                                       actor=actor, scheduled_ms=scheduled_ms, declared_burst=declared_burst)
        # Reads are safe to retry during a controlled app outage. Every failed
        # attempt is retained; outside that declared window it still fails the
        # normal-error gate even if a later retry succeeds.
        retries = max(retries, 7 if method == "GET" and self.in_steady else 0)
        response = None
        for attempt in range(retries+1):
            began = time.perf_counter()
            planned_at_start = self.planned_outage
            try:
                response = await self.client.request(method, path,
                    headers={"Cookie": f"access_token={token}" if token else "",
                             "Origin": self.args.base, "Referer": self.args.base+"/"},
                    json=body, data=form)
                status = str(response.status_code)
                if response.status_code < 500 and response.headers.get("x-lanshare-release") != self.handoff["release_id"]:
                    raise AssertionError("Successful response came from a different release")
            except httpx.RequestError as exc:
                status = type(exc).__name__
                response = None
            elapsed = (time.perf_counter()-began)*1000
            self.latency[flow].append(elapsed)
            self.statuses[flow, status] += 1
            planned_attempt = planned_at_start or self.planned_outage
            if self.in_steady and not planned_attempt and not acceptable_attempt_status(
                    status, declared_burst=declared_burst, expected_statuses=expected_statuses):
                self.normal_errors[flow, status] += 1
            self.write({"event": "attempt", "id": logical_id, "flow": flow,
                        "attempt": attempt+1, "status": status, "latency_ms": elapsed,
                        "planned_outage": planned_attempt, "declared_burst":declared_burst is True})
            if response is not None and (response.status_code < 500 and response.status_code != 429):
                return response
            if attempt < retries:
                await asyncio.sleep(min(8, 2**attempt))
        return response

    async def overview(self, session="lifecycle"):
        response = await self.request("teacher_overview", "GET",
            self.prefix+f"/sessions/{self.fixture['sessions'][session]}", token=self.teacher_token)
        assert response is not None and response.status_code == 200, "Teacher overview failed"
        value = response.json()
        network = value.get("teacher_network", {})
        owned_ingress = {str(ipaddress.ip_network(net).network_address) for net in self.fixture["networks"]}
        assert network.get("source") == "trusted_proxy" and network.get("ip") in owned_ingress, \
            "Real Nginx ingress attribution differs from the exact controller-owned network"
        return value

    async def authenticate(self, users=None):
        teacher = self.fixture["teacher"]
        response = await self.request("teacher_login", "POST", "/teacher/login", actor=teacher["id"],
            form={"email": teacher["email"], "password": self.handoff["synthetic_password"], "next": "/dashboard"})
        assert response is not None and response.status_code in (200, 303), "Synthetic teacher login failed"
        self.teacher_token = response.cookies.get("access_token")
        assert self.teacher_token, "Teacher cookie missing"
        claim = json.loads(base64.urlsafe_b64decode(self.teacher_token.split(".")[1]+"==="))
        assert claim["role"] == "teacher" and int(claim["id"]) == teacher["id"]
        for viewer in self.fixture.get("viewers", []):
            response = await self.request("viewer_teacher_login", "POST", "/teacher/login", actor=viewer["id"],
                form={"email": viewer["email"], "password": self.handoff["synthetic_password"], "next": "/dashboard"})
            assert response is not None and response.status_code in (200, 303)
            token = response.cookies.get("access_token")
            assert token
            claim = json.loads(base64.urlsafe_b64decode(token.split(".")[1]+"==="))
            assert claim["role"] == "teacher" and int(claim["id"]) == viewer["id"]
            self.viewer_tokens[viewer["id"]] = token
        async def login(user):
            response = await self.request("student_login", "POST", "/api/student/login/password",
                actor=user["student_id"], form={"identifier": user["identifier"],
                    "password": self.handoff["synthetic_password"], "next": "/dashboard"})
            assert response is not None and response.status_code == 200, "Synthetic student login failed"
            token = response.cookies.get("access_token")
            assert token
            claim = json.loads(base64.urlsafe_b64decode(token.split(".")[1]+"==="))
            assert claim["role"] == "student" and int(claim["id"]) == user["student_id"]
            self.tokens[user["student_id"]] = token
            self.login_times[user["student_id"]] = claim.get("login_time")
            self.write({"event": "authenticated", "actor": user["student_id"], "role": "student",
                        "login_time": claim.get("login_time")})
        await asyncio.gather(*(login(user) for user in (users or self.fixture["students"])))
        self.client.cookies.clear()

    async def create_round(self, session="lifecycle", minutes=3):
        # Explicit synthetic times are real wall time, never a fake application
        # clock. Formal runs later use the server's returned round timestamps.
        now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))
        body = {"request_key": self.run_id+"-"+session,
                "networks": self.fixture["networks"],
                "starts_at": (now-timedelta(seconds=30)).isoformat(),
                "ends_at": (now+timedelta(minutes=minutes)).isoformat(),
                "teacher_reference_at": now.isoformat()}
        path = self.prefix+f"/sessions/{self.fixture['sessions'][session]}/rounds"
        response = await self.request("round_create", "POST", path, token=self.teacher_token, body=body)
        assert response is not None and response.status_code in (200, 201, 202), "Round create failed"
        created = round_value(response.json())
        replay = await self.request("round_create_same_key", "POST", path, token=self.teacher_token, body=body)
        assert replay is not None and replay.status_code in (200, 201, 202)
        assert round_value(replay.json())["id"] == created["id"], "Create replay made another round"
        self.contracts.append({"contract": "round_create_idempotent", "passed": True, "round_id": created["id"]})
        return created

    async def student_view(self, student, session="lifecycle"):
        response = await self.request("student_me", "GET",
            self.prefix+f"/sessions/{self.fixture['sessions'][session]}/me",
            token=self.tokens[student], actor=student)
        assert response is not None and response.status_code == 200
        value = response.json()
        check_student_dto(value, self.fixture)
        def check_ids(item):
            if isinstance(item, dict):
                if "student_id" in item:
                    assert item["student_id"] == student, "Another student's attendance reached self DTO"
                for child in item.values(): check_ids(child)
            elif isinstance(item, list):
                for child in item: check_ids(child)
        check_ids(value)
        return value

    async def lifecycle(self):
        created = await self.create_round()
        view = round_value(await self.overview())
        assert len(view["records"]) == 200
        assert {row["student_id"] for row in view["records"]} == {u["student_id"] for u in self.fixture["students"]}
        student = self.fixture["students"][0]["student_id"]
        await self.student_view(student)
        forbidden = await self.request("student_teacher_scope", "GET",
            self.prefix+f"/sessions/{self.fixture['sessions']['lifecycle']}", token=self.tokens[student])
        assert forbidden is not None and forbidden.status_code == 403
        record = next(row for row in view["records"] if row["student_id"] == student)
        change = {"records": [{"student_id": student, "revision": record["revision"], "status": "SICK_LEAVE"}],
                  "reason": self.fixture["private_canary"]}
        path = self.prefix+f"/rounds/{created['id']}/records"
        changed = await self.request("teacher_manual", "PATCH", path, token=self.teacher_token, body=change)
        assert changed is not None and changed.status_code == 200 and changed.json()["results"][0]["result"] == "updated"
        conflict = await self.request("teacher_stale_revision", "PATCH", path, token=self.teacher_token, body=change)
        assert conflict is not None and conflict.status_code == 200 and conflict.json()["results"][0]["result"] == "conflict"
        own = await self.student_view(student)
        assert own["status"] == "SICK_LEAVE" and own["manual_confirmed"]
        self.contracts.append({"contract": "manual_revision_conflict_and_student_private_dto", "passed": True})
        current = round_value(await self.overview())
        ended = await self.request("round_end", "POST", self.prefix+f"/rounds/{created['id']}/end",
            token=self.teacher_token, body={"revision": current["revision"]})
        assert ended is not None and ended.status_code in (200, 202)
        current = round_value(await self.overview())
        assert current["state"] in ("settling", "published")
        applicant = self.fixture["students"][1]["student_id"]
        appeal_path = self.prefix+f"/rounds/{created['id']}/appeals"
        response = await self.request("student_appeal", "POST", appeal_path, actor=applicant,
            token=self.tokens[applicant], body={"reason": "Synthetic attendance review request"})
        assert response is not None and response.status_code in (200, 201)
        request_id = response.json()["request"]["id"]
        duplicate = await self.request("student_appeal_replay", "POST", appeal_path, actor=applicant,
            token=self.tokens[applicant], body={"reason": "Synthetic attendance review request"})
        assert duplicate is not None and duplicate.status_code in (200, 201, 409)
        if duplicate.status_code != 409:
            assert duplicate.json()["request"]["id"] == request_id
        attention = await self.request("teacher_attention", "GET", "/api/login-attendance/attention", token=self.teacher_token)
        assert attention is not None and attention.status_code == 200
        assert request_id in {item["request_id"] for item in attention.json()["items"]}
        async def claim(suffix):
            result = await self.request("teacher_attention_claim", "POST",
                f"/api/login-attendance/attention/{request_id}/claim", token=self.teacher_token,
                body={"owner": self.run_id+suffix})
            assert result is not None and result.status_code == 200
            return result.json()["claimed"]
        claims = await asyncio.gather(claim("-tab-a"), claim("-tab-b"))
        assert sum(claims) == 1, "More than one teacher tab claimed the same durable reminder"
        current = round_value(await self.overview())
        record = next(row for row in current["records"] if row["student_id"] == applicant)
        resolved = await self.request("teacher_resolve_appeal", "PATCH", path, token=self.teacher_token,
            body={"records": [{"student_id": applicant, "revision": record["revision"], "status": "PRESENT"}],
                  "reason": "Synthetic appeal resolved by teacher"})
        assert resolved is not None and resolved.status_code == 200
        attention = await self.request("teacher_attention_after_resolution", "GET",
            "/api/login-attendance/attention", token=self.teacher_token)
        assert attention is not None and attention.status_code == 200
        assert request_id not in {item["request_id"] for item in attention.json()["items"]}
        self.contracts.append({"contract": "appeal_dedupe_single_display_owner_and_manual_resolution", "passed": True,
                               "request_id": request_id, "student_id": applicant})
        current = round_value(await self.overview())
        voided = await self.request("round_void", "POST", self.prefix+f"/rounds/{created['id']}/void",
            token=self.teacher_token, body={"revision": current["revision"], "reason": "Synthetic lifecycle cleanup"})
        assert voided is not None and voided.status_code in (200, 202)
        self.contracts.append({"contract": "owned_200_roster_student_scope_end_void", "passed": True,
                               "round_id": created["id"]})

    def open_ws_count(self):
        return sum(getattr(getattr(conn, "state", None), "name", None) == "OPEN" for conn in self.ws)

    async def open_websockets(self, count=800):
        path = self.work/"client-handoff/recorder-second-offering.json"
        companion = json.loads(path.read_text())
        if (companion.get("synthetic") is not True or companion.get("lab_nonce") != self.args.lab_nonce
                or companion.get("primary_offering_id") != self.fixture["offering_id"]
                or type(companion.get("second_offering_id")) is not int):
            raise AssertionError("Owned companion classroom fixture unavailable")
        paths = [(f"/ws/{self.fixture['offering_id']}", "classroom"), ("/ws/social", "social"),
                 (f"/ws/{companion['second_offering_id']}", "classroom"), ("/ws/social", "social")]
        if count == 200:
            paths = [("/ws/social", "social")]
        elif count != 800:
            raise ValueError("Declared real socket profiles are 800 resident or 200 extra")
        slots = asyncio.Semaphore(16)
        context = ssl.create_default_context(cafile=self.work/"client-handoff/capacity-lab.crt")
        async def connect(user, route, kind):
            async with slots:
                conn = await websockets.connect(self.args.base.replace("https:", "wss:")+route,
                    ssl=context, additional_headers={"Cookie": f"access_token={self.tokens[user['student_id']]}"},
                    open_timeout=30, close_timeout=2, max_queue=32, ping_interval=20, proxy=None)
                self.ws.append(conn)
                ready = asyncio.Event()
                async def consume():
                    try:
                        async for raw in conn:
                            self.ws_counts[kind+"_frames"] += 1
                            message = json.loads(raw)
                            if not ready.is_set() and message.get("type") == ("history" if kind == "classroom" else "hello"):
                                ready.set()
                                self.ws_counts[kind+"_ready"] += 1
                    except Exception as exc:
                        self.ws_counts[("planned_" if self.ws_planned else "unexpected_")+kind+"_"+type(exc).__name__] += 1
                    finally:
                        if not self.ws_stopping:
                            self.ws_counts[("planned_" if self.ws_planned else "unexpected_")+kind+"_closed"] += 1
                self.ws_tasks.append(asyncio.create_task(consume()))
                await asyncio.wait_for(ready.wait(), 30)
        await asyncio.gather(*(connect(user, route, kind) for user in self.fixture["students"] for route,kind in paths))
        self.ws_expected = 1000 if count == 200 else 800
        assert self.open_ws_count() == self.ws_expected
        print(json.dumps({"phase": "websocket_ready", "actual_open": self.open_ws_count()}), flush=True)

    def presence_payload(self, student_index, span_ms):
        observed = int(time.time()*1000)-1000
        payload = {"presence_version": 2, "presence_observed_ms": observed,
                   "visibility_state": "hidden" if 60 <= student_index < 120 else "visible",
                   "focused": not 60 <= student_index < 120,
                   "idle_seconds": 9999 if student_index < 120 else 0, "ai_panel_open": False}
        start = max(observed-span_ms, self.main_started_client_epoch_ms)
        if start < observed:
            payload.update(interval_start_ms=start, interval_end_ms=observed)
        return payload

    async def recorder_batch(self, index, phase, *, heartbeat=False, tab=0, body=None):
        user = self.fixture["students"][index]
        if body is None:
            events = [{"action_type": "presence_heartbeat", "summary_text": "",
                       "payload": self.presence_payload(index, 60000 if heartbeat else 1000)}]
            if not heartbeat and index >= 120:
                events.insert(0, {"action_type": "page_action", "summary_text": "attendance load "+self.run_id,
                                  "payload": {"attendance_run": self.run_id, "tab": tab}})
            body = {"batch_id": f"b1_{int(time.time()*1000)}_{uuid.uuid4()}",
                    "user_key": f"student:{user['student_id']}", "page_key": "acap_"+self.run_id[:8]+"_"+phase,
                    "events": events}
        response = await self.request(phase+"_recorder", "POST",
            f"/api/classrooms/{self.fixture['offering_id']}/behavior/batch",
            token=self.tokens[user["student_id"]], actor=user["student_id"], body=body, retries=7,
            declared_burst=phase in {"burst600_r1","burst600_r2","burst600_r3","headroom1000"})
        valid = response is not None and response.status_code in (200, 202)
        receipt = response.json() if valid else {}
        valid = valid and receipt.get("status") == "success" and receipt.get("batch_id") == body["batch_id"]
        valid = valid and receipt.get("write_mode") in ("durable_queued", "synced")
        if valid:
            self.recorder_successes += 1
            if self.recorder_successes % 100 == 0:
                # Controlled lost-ACK simulation: the first successful wire
                # result is deliberately not accepted by the logical client.
                # Keep its real HTTP status, then resend the immutable body.
                self.write({"event":"controlled_ack_discard", "batch_id":body["batch_id"],"phase":phase})
                replay = await self.request(phase+"_discarded_ack_replay", "POST",
                    f"/api/classrooms/{self.fixture['offering_id']}/behavior/batch",
                    token=self.tokens[user["student_id"]], actor=user["student_id"], body=body, retries=7,
                    declared_burst=phase in {"burst600_r1","burst600_r2","burst600_r3","headroom1000"})
                ack = replay.json() if replay is not None and replay.status_code in (200,202) else {}
                valid = ack.get("status") == "success" and ack.get("batch_id") == body["batch_id"]
                self.discarded_ack_replays += int(valid)
        self.write({"event": "batch_result", "phase": phase, "batch_id": body["batch_id"],
                    "student_id": user["student_id"], "ack": bool(valid), "kind": "behavior"})
        self.batch_acks[phase, bool(valid)] += 1
        assert valid, "A scheduled durable recorder batch failed"
        return body

    async def periodic(self, name, period_ms, offset_ms, operation):
        offsets = periodic_offsets(self.args.duration*1000, period_ms, offset_ms)
        self.due_ticks[name] += len(offsets)
        for offset in offsets:
            target = self.started_monotonic+offset/1000
            await asyncio.sleep(max(0, target-time.perf_counter()))
            self.schedule_lateness.append(max(0, (time.perf_counter()-target)*1000))
            self.sent_ticks[name] += 1
            token = self.tick_offset.set(offset)
            try:
                await operation()
            finally:
                self.tick_offset.reset(token)

    async def main_run(self):
        if self.args.duration < 5400:
            raise ValueError("Formal capacity evidence requires at least 90 minutes")
        now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))
        starts = now+timedelta(seconds=60)
        payload = {"request_key": self.run_id+"-main", "networks": self.fixture["networks"],
                   "starts_at": starts.isoformat(), "ends_at": (starts+timedelta(seconds=self.args.duration)).isoformat()}
        created = await self.request("main_round_create", "POST",
            self.prefix+f"/sessions/{self.fixture['sessions']['main']}/rounds", token=self.teacher_token, body=payload)
        assert created is not None and created.status_code in (200, 201, 202)
        main_round = round_value(created.json())
        self.main_round_id = main_round["id"]
        reading = json.loads((self.work/"client-handoff/capacity-reading.json").read_text())
        async def initial_page(user):
            response = await self.request("classroom_initial_200", "GET",
                f"/classroom/{self.fixture['offering_id']}", token=self.tokens[user["student_id"]])
            assert response is not None and response.status_code == 200 and "data-classroom-page-app" in response.text
        await asyncio.gather(*(initial_page(user) for user in self.fixture["students"]))
        await self.open_websockets()
        await asyncio.sleep(max(0, main_round["starts_at_ms"]/1000-time.time()))
        self.started_monotonic = time.perf_counter()
        self.main_started_client_epoch_ms = int(time.time()*1000)
        self.in_steady = True
        print(json.dumps({"phase": "steady200", "duration_seconds": self.args.duration,
                          "main_round_id": self.main_round_id, "started_client_epoch": time.time()}), flush=True)
        self.sample_stop = False
        async def sample_sockets():
            while not self.sample_stop:
                if self.ws_expected and not self.ws_planned and not self.ws_stopping:
                    self.ws_samples.append({"client_epoch": time.time(), "expected": self.ws_expected,
                                            "actual_open": self.open_ws_count()})
                await asyncio.sleep(.5)
        sampler = asyncio.create_task(sample_sockets())
        material_last = {user["student_id"]: self.main_started_client_epoch_ms for user in self.fixture["students"]}
        async def legacy_consumer():
            while True:
                item = await self.legacy_queue.get()
                if item is None:
                    self.legacy_queue.task_done()
                    return
                try:
                    await self.legacy_gate.wait()
                    self.legacy_delay.append((time.perf_counter()-item["queued_monotonic"])*1000)
                    self.legacy_inflight += 1
                    try:
                        response = await self.request("steady_legacy_material", "POST", item["path"],
                            token=self.tokens[item["student_id"]], actor=item["student_id"],
                            body=item["body"], intent_id=item["intent_id"], retries=0)
                    finally:
                        self.legacy_inflight -= 1
                    try:
                        valid = response is not None and response.status_code == 200 and response.json().get("status") == "success"
                    except (ValueError, TypeError):
                        valid = False
                    self.write({"event": "batch_result", "kind": "legacy_material", "phase": "steady",
                        "intent_id": item["intent_id"], "student_id": item["student_id"], "ack": bool(valid)})
                    self.batch_acks["legacy_material", bool(valid)] += 1
                    if not valid:
                        # A non-idempotent unknown commit is never blindly
                        # replayed. It remains an explicit failed/unknown fact.
                        self.legacy_failures.append(item["intent_id"])
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.legacy_failures.append(item["intent_id"])
                    self.write({"event": "legacy_unknown_outcome", "intent_id": item["intent_id"],
                                "error_type": type(exc).__name__})
                finally:
                    self.legacy_queue.task_done()
        legacy_workers = [asyncio.create_task(legacy_consumer()) for _ in range(4)]
        async def material(index):
            user = self.fixture["students"][index]
            body = {"material_id": reading["material_id"], "session_id": None, "duration_seconds": 30,
                    "active_seconds": 20, "scroll_ratio": .5, "completed": False, "page_key": "material_viewer"}
            kind = "legacy_material"
            if index < 150:
                kind = "v2_material"
                presence = self.presence_payload(index, 90000)
                observed = presence["presence_observed_ms"]
                if material_last[user["student_id"]] < observed:
                    presence.update(interval_start_ms=max(material_last[user["student_id"]], observed-90000), interval_end_ms=observed)
                else:
                    presence.pop("interval_start_ms", None); presence.pop("interval_end_ms", None)
                body.update(progress_version=2, user_key=f"student:{user['student_id']}",
                    batch_id=f"b1_{int(time.time()*1000)}_{uuid.uuid4()}",
                    events=[{"action_type": "presence_heartbeat", "summary_text": "", "payload": presence}])
                material_last[user["student_id"]] = observed
            else:
                path = f"/api/classrooms/{self.fixture['offering_id']}/learning/material-progress"
                intent_id = self.prepare_intent("steady_legacy_material", "POST", path,
                                                actor=user["student_id"], body=body)
                try:
                    self.legacy_queue.put_nowait({"intent_id": intent_id, "path": path,
                        "student_id": user["student_id"], "body": body, "queued_monotonic": time.perf_counter()})
                    self.legacy_queue_peak = max(self.legacy_queue_peak, self.legacy_queue.qsize())
                except asyncio.QueueFull:
                    self.write({"event": "legacy_deferred_overflow", "intent_id": intent_id})
                    raise AssertionError("Bounded legacy client queue exhausted; planned intent preserved")
                return
            response = await self.request("steady_"+kind, "POST",
                f"/api/classrooms/{self.fixture['offering_id']}/learning/material-progress",
                token=self.tokens[user["student_id"]], actor=user["student_id"], body=body,
                retries=7 if index < 150 else 0)
            valid = response is not None and response.status_code in (200, 202) and response.json().get("status") == "success"
            if index < 150:
                valid = valid and response.json().get("batch_id") == body["batch_id"]
            self.write({"event": "batch_result", "kind": kind, "phase": "steady", "student_id": user["student_id"],
                        "batch_id": body.get("batch_id"), "ack": bool(valid)})
            self.batch_acks[kind, bool(valid)] += 1
            assert valid, "A scheduled material request failed"
        async def blog_read():
            response = await self.request("steady_blog_read", "GET", "/api/blog/posts",
                                          token=self.tokens[self.fixture["students"][0]["student_id"]])
            assert response is not None and response.status_code == 200
        async def classroom_read():
            response = await self.request("steady_classroom_read", "GET", f"/classroom/{self.fixture['offering_id']}",
                                          token=self.tokens[self.fixture["students"][0]["student_id"]])
            assert response is not None and response.status_code == 200
        async def teacher_read():
            view = round_value(await self.overview("main"))
            assert len(view["records"]) == 200 and view["summary"]["expected"] == 200
        async def viewer_read(viewer):
            path = f"/api/classrooms/{viewer['offering_id']}/login-attendance/rounds/{viewer['round_id']}"
            response = await self.request("steady_other_teacher_view", "GET", path,
                                          token=self.viewer_tokens[viewer["id"]], actor=viewer["id"])
            assert response is not None and response.status_code == 200
            view = round_value(response.json())
            assert view["id"] == viewer["round_id"] and len(view["records"]) == 200
            assert view["state"] == "published" and view["summary"]["expected"] == 200
        async def at(seconds):
            await asyncio.sleep(max(0, self.started_monotonic+seconds-time.perf_counter()))
        async def burst(seconds, phase):
            await at(seconds)
            began = time.perf_counter()
            await asyncio.gather(*(self.recorder_batch(index, phase, tab=tab)
                                  for index in range(200) for tab in range(3)))
            self.phase_results.append({"phase": phase, "batches": 600,
                "duration_monotonic_seconds": time.perf_counter()-began,
                "latency": distribution(self.latency[phase+"_recorder"])})
            print(json.dumps(self.phase_results[-1]), flush=True)
        async def headroom():
            await at(80*60)
            prior, prior_tasks = len(self.ws), len(self.ws_tasks)
            self.ws_expected = 0
            await self.open_websockets(200)
            began = time.perf_counter()
            await asyncio.gather(*(self.recorder_batch(index, "headroom1000", tab=tab)
                                  for index in range(200) for tab in range(3)), asyncio.sleep(20))
            self.phase_results.append({"phase": "headroom1000", "actual_open": self.open_ws_count(),
                                       "duration_monotonic_seconds": time.perf_counter()-began})
            self.ws_planned = True
            await asyncio.gather(*(conn.close() for conn in self.ws[prior:]), return_exceptions=True)
            await asyncio.gather(*self.ws_tasks[prior_tasks:], return_exceptions=True)
            del self.ws[prior:]; del self.ws_tasks[prior_tasks:]
            self.ws_expected = 800
            self.ws_planned = False
        async def wait_marker(name, seconds=120):
            deadline = time.perf_counter()+seconds
            while time.perf_counter() < deadline and not (self.work/name).exists():
                await asyncio.sleep(.1)
            assert (self.work/name).exists(), "Owned fault coordinator did not acknowledge "+name
        async def application_fault():
            await at(15*60)
            self.legacy_gate.clear()
            paused = time.perf_counter()
            while self.legacy_inflight and time.perf_counter()-paused < 60:
                await asyncio.sleep(.02)
            assert not self.legacy_inflight, "Cannot kill app while a non-idempotent legacy write is unresolved"
            self.write({"event": "legacy_paused_before_fault", "inflight": self.legacy_inflight,
                        "client_monotonic": paused})
            (self.work/"fault-ready").write_text(self.run_id)
            await wait_marker("fault-held")
            self.planned_outage = True
            self.ws_planned = True
            self.ws_expected = 0
            bodies = await asyncio.gather(*(self.recorder_batch(index, "blocked_apply") for index in range(200)))
            (self.work/"fault-acked").write_text(self.run_id)
            await wait_marker("fault-restarted")
            await asyncio.gather(*(self.recorder_batch(index, "blocked_replay", body=body)
                                  for index,body in enumerate(bodies)))
            await asyncio.gather(*(conn.close() for conn in self.ws), return_exceptions=True)
            await asyncio.gather(*self.ws_tasks, return_exceptions=True)
            self.ws.clear(); self.ws_tasks.clear()
            await self.open_websockets()
            self.ws_planned = False
            self.planned_outage = False
            self.legacy_gate.set()
            self.write({"event": "legacy_resumed_after_fault", "queued": self.legacy_queue.qsize(),
                        "pause_monotonic_seconds": time.perf_counter()-paused})
            self.phase_results.append({"phase": "app_and_apply_recovered", "actual_open": self.open_ws_count(),
                                       "same_id_replays": len(bodies), "client_epoch": time.time()})
            print(json.dumps(self.phase_results[-1]), flush=True)
        async def scheduler_fault():
            await at(50*60)
            (self.work/"attendance-worker-restart-ready").write_text(json.dumps({"run_id":self.run_id,"round_id":self.main_round_id}))
            await wait_marker("attendance-worker-restarted",seconds=180)
            self.phase_results.append({"phase": "scheduler_restarted", "client_epoch": time.time()})
        async def teacher_manual_during_load():
            await at(25*60)
            view = round_value(await self.overview("main"))
            student = self.fixture["students"][0]["student_id"]
            record = next(row for row in view["records"] if row["student_id"] == student)
            body = {"records": [{"student_id": student, "revision": record["revision"], "status": "SICK_LEAVE"}],
                    "reason": self.fixture["private_canary"]}
            path = self.prefix+f"/rounds/{self.main_round_id}/records"
            response = await self.request("steady_teacher_manual", "PATCH", path,
                                          token=self.teacher_token, body=body)
            assert response is not None and response.status_code == 200
            assert response.json()["results"][0]["result"] == "updated"
            self.manual_expected[student] = "SICK_LEAVE"
            stale = await self.request("steady_teacher_stale_manual", "PATCH", path,
                                       token=self.teacher_token, body=body)
            assert stale is not None and stale.status_code == 200
            assert stale.json()["results"][0]["result"] == "conflict"
            own = await self.student_view(student, "main")
            assert own["status"] == "SICK_LEAVE" and own["manual_confirmed"]
            view = round_value(await self.overview("main"))
            response = await self.request("steady_teacher_recalculate", "POST",
                self.prefix+f"/rounds/{self.main_round_id}/recalculate", token=self.teacher_token,
                body={"revision": view["revision"]})
            assert response is not None and response.status_code == 200
            current = round_value(response.json())
            assert next(row for row in current["records"] if row["student_id"] == student)["status"] == "SICK_LEAVE"
            self.contracts.append({"contract": "manual_lock_survives_recalculate_under_200_load",
                                   "passed": True, "student_id": student})
        async def client_recovery_contract():
            await at(20*60)
            index = 150
            user = self.fixture["students"][index]
            body = await self.recorder_batch(index,"auth_recovery_seed")
            fresh = json.loads(json.dumps(body))
            fresh["batch_id"] = f"b1_{int(time.time()*1000)}_{uuid.uuid4()}"
            path = f"/api/classrooms/{self.fixture['offering_id']}/behavior/batch"
            denied = await self.request("controlled_401", "POST",path,token="synthetic-invalid-token",
                actor=user["student_id"],body=fresh,expected_statuses=(401,))
            assert denied is not None and denied.status_code == 401
            await self.recorder_batch(index,"after401_same_id",body=fresh)
            collision = json.loads(json.dumps(fresh))
            collision["events"][0]["summary_text"] += " changed"
            denied = await self.request("controlled_409","POST",path,token=self.tokens[user["student_id"]],
                actor=user["student_id"],body=collision,expected_statuses=(409,))
            assert denied is not None and denied.status_code == 409
            await self.recorder_batch(index,"after409_original_id",body=fresh)
            await self.recorder_batch(index,"after409_new_work")
            self.contracts.append({"contract":"401_preserves_same_id_then409_does_not_stop_following_work",
                                   "passed":True,"student_id":user["student_id"]})
        tasks = []
        for index, user in enumerate(self.fixture["students"]):
            if index >= 120:
                tasks.append(self.periodic("operator", 4000, (index-120)*4000//80,
                    lambda index=index: self.recorder_batch(index, "steady")))
            for tab in range(3):
                tasks.append(self.periodic("heartbeat", 60000, (index*60000//200+tab*20000)%60000,
                    lambda index=index,tab=tab: self.recorder_batch(index, "steady", heartbeat=True, tab=tab)))
            tasks.append(self.periodic("material", 30000, index*30000//200, lambda index=index: material(index)))
            tasks.append(self.periodic("student_me", 60000, index*60000//200,
                lambda student=user["student_id"]: self.student_view(student, "main")))
        tasks += [self.periodic("blog_read", 200, 0, blog_read),
                  self.periodic("classroom_read", 10000, 5000, classroom_read),
                  self.periodic("teacher_main", 15000, 0, teacher_read),
                  self.periodic("teacher_second_tab", 15000, 7500, teacher_read),
                  burst(10*60, "burst600_r1"), burst(35*60, "burst600_r2"),
                  burst(70*60, "burst600_r3"), headroom(), application_fault(), scheduler_fault(),
                  teacher_manual_during_load(), client_recovery_contract()]
        for index, viewer in enumerate(self.fixture.get("viewers", [])):
            tasks.append(self.periodic("other_teacher_view", 15000, index*5000+2000,
                                      lambda viewer=viewer: viewer_read(viewer)))
        scheduled_tasks = [asyncio.create_task(task) for task in tasks]
        try:
            await asyncio.gather(*scheduled_tasks)
            await asyncio.wait_for(self.legacy_queue.join(), timeout=120)
            assert not self.legacy_failures, "Legacy transport/commit outcome unknown; no blind replay was performed"
            assert self.due_ticks == self.sent_ticks, "A due tick was silently omitted"
            await asyncio.sleep(max(0, self.started_monotonic+self.args.duration-time.perf_counter()))
            elapsed = time.perf_counter()-self.started_monotonic
            assert elapsed >= self.args.duration, "Formal steady duration was shortened"
            self.in_steady = False
            self.phase_results.append({"phase": "steady200", "duration_monotonic_seconds": elapsed,
                                       "due_ticks": dict(self.due_ticks), "sent_ticks": dict(self.sent_ticks)})
            print(json.dumps(self.phase_results[-1]), flush=True)
        finally:
            for task in scheduled_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*scheduled_tasks, return_exceptions=True)
            for task in legacy_workers:
                task.cancel()
            await asyncio.gather(*legacy_workers, return_exceptions=True)
            self.sample_stop = True
            await sampler
        # Finalizing is a genuine E+10/E+12 wait. A run may not force an early
        # end or change the application clock to make this gate appear passed.
        deadline = time.perf_counter()+15*60
        while time.perf_counter() < deadline:
            view = round_value(await self.overview("main"))
            if view["state"] == "published":
                assert len(view["records"]) == 200
                assert all(row["status"] == self.manual_expected.get(row["student_id"], "PRESENT")
                           for row in view["records"]), "Positive attendance/manual lock did not survive the final round"
                self.phase_results.append({"phase": "final_published", "round_id": view["id"],
                                           "summary": view["summary"], "watermark": view.get("watermark")})
                break
            await asyncio.sleep(10)
        else:
            raise AssertionError("Attendance round did not settle/publish within the real-clock budget")
        self.ws_stopping = True
        await asyncio.gather(*(conn.close() for conn in self.ws), return_exceptions=True)
        await asyncio.gather(*self.ws_tasks, return_exceptions=True)

    async def execute(self):
        context = ssl.create_default_context(cafile=self.work/"client-handoff/capacity-lab.crt")
        try:
            async with httpx.AsyncClient(base_url=self.args.base, verify=context, trust_env=False,
                    timeout=httpx.Timeout(60, pool=30), follow_redirects=False,
                    limits=httpx.Limits(max_connections=350, max_keepalive_connections=250)) as client:
                self.client = client
                live = await client.get("/api/internal/live")
                assert live.status_code == 200 and live.headers.get("x-lanshare-release") == self.handoff["release_id"]
                await self.authenticate()
                await self.lifecycle()
                if self.args.mode == "run":
                    await self.main_run()
            self.write({"event": "finished", "status": "passed", "mode": self.args.mode})
            self.completed = True
            return True
        finally:
            if self.ws:
                self.ws_stopping = True
                await asyncio.gather(*(conn.close() for conn in self.ws), return_exceptions=True)
                await asyncio.gather(*self.ws_tasks, return_exceptions=True)
            report = {"run_id": self.run_id, "mode": self.args.mode, "lab_nonce": self.args.lab_nonce,
                "client_status": "passed" if self.completed else "failed",
                "source_release": self.handoff["release_id"], "production_accessed": False,
                "logical_requests": dict(self.logical), "latency": {k:distribution(v) for k,v in self.latency.items()},
                "statuses": [{"flow":flow,"status":status,"count":count} for (flow,status),count in sorted(self.statuses.items())],
                "contracts": self.contracts, "capacity_result": False}
            report.update(phases=self.phase_results, due_ticks=dict(self.due_ticks), sent_ticks=dict(self.sent_ticks),
                duration_requested_seconds=self.args.duration, main_round_id=getattr(self, "main_round_id", None),
                real_teacher_identities=1+len(self.viewer_tokens), manual_expected=self.manual_expected,
                legacy_queue_peak=self.legacy_queue_peak, legacy_queue_remaining=self.legacy_queue.qsize(),
                legacy_inflight=self.legacy_inflight, legacy_defer_latency=distribution(self.legacy_delay),
                legacy_unknown_outcomes=self.legacy_failures,
                controlled_discarded_ack_replays=self.discarded_ack_replays,
                schedule_lateness=distribution(self.schedule_lateness), websockets=dict(self.ws_counts),
                websocket_samples=self.ws_samples,
                websocket_checks_passed=bool(self.ws_samples) and all(s["expected"] == s["actual_open"] for s in self.ws_samples)
                    and not any(v for k,v in self.ws_counts.items() if k.startswith("unexpected_")),
                batch_acks=[{"kind":kind,"ack":ack,"count":count} for (kind,ack),count in self.batch_acks.items()],
                normal_errors=[{"flow":flow,"status":status,"count":count} for (flow,status),count in self.normal_errors.items()],
                server_facts_required=True)
            (self.output/"http-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            self.journal.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("work", "source-root", "base", "lab-nonce", "release-id", "fixture-sha256"):
        parser.add_argument("--"+name, required=True)
    parser.add_argument("--mode", choices=("smoke", "run"), default="smoke")
    parser.add_argument("--duration", type=int, default=5400)
    args = parser.parse_args()
    asyncio.run(Probe(args).execute())


if __name__ == "__main__":
    main()
