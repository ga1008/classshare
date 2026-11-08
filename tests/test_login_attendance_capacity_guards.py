"""Isolation and exact scheduling regressions for the 90-minute lab client."""
import hashlib
import asyncio
import contextvars
from collections import Counter, defaultdict
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import time

import httpx

from tests.test_recorder_capacity_probe import make_owned
from tools.platform_capacity_lab_manifest import LabManifestError, fingerprint, source_hashes
from tools.login_attendance_lab import (FIXTURE_KIND, check_student_dto,
                                        load_attendance_fixture, periodic_offsets, acceptable_attempt_status, validate_scan_round,
                                        coordinate_worker_fault)
from tools.login_attendance_lab_facts import add_union
from tools.login_attendance_probe import Probe, gather_owned
from tools.recorder_capacity_fault import main as coordinate_app_fault, restart_identity_report


class AttendanceCapacityGuardsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root, self.work, self.nonce, self.release, handoff = make_owned(Path(temporary.name))
        for name in ("db/schema_login_attendance.py", "services/login_attendance_rules.py"):
            path = self.root / "classroom_app" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# synthetic source guard input\n")
        hashes = source_hashes(self.root)
        handoff.update(client_source_sha256=hashes, server_source_sha256=hashes)
        handoff["expected_identity"]["source_fingerprint"] = fingerprint(hashes)
        (self.work / "client-handoff/handoff.json").write_text(json.dumps(handoff))
        self.fixture = {"kind": FIXTURE_KIND, "schema_version": 1, "synthetic": True,
            "lab_nonce": self.nonce, "release_id": self.release, "offering_id": 1,
            "students": handoff["users"], "teacher": {"id": 2, "email": "teacher@example.invalid"},
            "sessions": {"main": 1, "lifecycle": 2}, "networks": ["172.30.40.1/32"],
            "private_canary": "ATTENDANCE_PRIVATE_CANARY_" + self.nonce, "created_at_ms": 1}
        self.path = self.work / "client-handoff/attendance-fixture.json"

    def write(self):
        self.path.write_text(json.dumps(self.fixture))
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def load(self, digest=None, **overrides):
        arguments = dict(work=self.work, source_root=self.root, base="https://127.0.0.1:28769",
                         nonce=self.nonce, release=self.release, fixture_sha256=digest or self.write())
        arguments.update(overrides)
        return load_attendance_fixture(**arguments)

    def test_owned_new_runtime_fixture(self):
        self.assertEqual(len(self.load()[1]["students"]), 200)

    def test_refuses_public_target_even_with_valid_fixture(self):
        with self.assertRaises(LabManifestError):
            self.load(base="https://school.example.invalid")

    def test_refuses_changed_fixture_after_controller_copy(self):
        digest = self.write()
        self.fixture["sessions"]["main"] = 1234
        self.write()
        with self.assertRaises(LabManifestError):
            self.load(digest)

    def test_refuses_other_nonce_teacher_or_database_coordinates(self):
        mutations = (
            lambda f: f.update(lab_nonce="b"*32),
            lambda f: f.update(teacher={"id": 2, "email": "a-real-account@example.org"}),
            lambda f: f.update(database={"host": "production.example.invalid"}),
            lambda f: f.update(sessions={"main": 1, "lifecycle": 1}),
            lambda f: f.update(networks=["0.0.0.0/0"]),
            lambda f: f.update(networks=["10.0.0.0/8"]),
            lambda f: f.update(networks=["8.8.8.8/32"]),
        )
        original = json.dumps(self.fixture)
        for index, mutation in enumerate(mutations):
            with self.subTest(mutation=index):
                self.fixture = json.loads(original)
                mutation(self.fixture)
                with self.assertRaises(LabManifestError):
                    self.load()

    def test_all_90_minute_boundary_ticks_remain_due(self):
        duration = 90*60*1000
        operations = sum(len(periodic_offsets(duration, 4000, i*4000//80)) for i in range(80))
        heartbeats = sum(len(periodic_offsets(duration, 60000, (i*60000//200+tab*20000)%60000))
                         for i in range(200) for tab in range(3))
        self.assertEqual(operations, 108000)
        self.assertEqual(heartbeats, 54000)
        self.assertEqual(periodic_offsets(duration, 10000, 9950)[-1], 5399950)
        # A delayed last HTTP completion must not determine enumeration.
        delayed_now = duration+500
        self.assertGreater(delayed_now, periodic_offsets(duration, 10000, 9950)[-1])
        self.assertEqual(len(periodic_offsets(duration, 10000, 9950)), 540)

    def test_rejects_ambiguous_schedule_units(self):
        for values in ((0, 1000, 0), (1000, 0, 0), (1000, 1000, -1),
                       (1000, 1000, 1000), (1000., 1000, 0), (True, 1000, 0)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                periodic_offsets(*values)

    def test_student_dto_rejects_nested_private_evidence(self):
        for value in ({"details": {"ip_address": "synthetic"}},
                      {"history": [{"raw_metrics": {}}]},
                      {"message": self.fixture["private_canary"]}):
            with self.subTest(value=value), self.assertRaises(AssertionError):
                check_student_dto(value, self.fixture)
        check_student_dto({"status": "PRESENT", "history": [{"label": "出勤"}]}, self.fixture)

    def test_three_history_viewers_have_explicit_distinct_owned_scopes(self):
        self.fixture["viewers"] = [{"id":i+10,"email":f"viewer{i}@example.invalid",
            "offering_id":i+2,"session_id":i+3,"round_id":i+4} for i in range(3)]
        self.assertEqual(len(self.load()[1]["viewers"]),3)
        self.fixture["viewers"][0]["offering_id"] = 1
        with self.assertRaises(LabManifestError):
            self.load()

    def test_independent_oracle_merges_overlap_retry_tabs_and_gap(self):
        entries = []
        for start,end in ((3000,4000),(0,2000),(1000,3000),(0,2000),(5000,5500),(5500,6000)):
            add_union(entries,start,end)
        self.assertEqual(entries,[[0,4000],[5000,6000]])
        add_union(entries,4500,4500)
        self.assertEqual(sum(end-start for start,end in entries),5000)

    def test_burst_exemption_is_request_specific_and_only429_503(self):
        for status in (429,503,"429","503"):
            self.assertTrue(acceptable_attempt_status(status,declared_burst=True))
            self.assertFalse(acceptable_attempt_status(status))
        for status in (500,502,"ReadTimeout","ConnectError",401,409):
            self.assertFalse(acceptable_attempt_status(status,declared_burst=True))
        self.assertTrue(acceptable_attempt_status(200))
        self.assertFalse(acceptable_attempt_status(503,declared_burst=1))
        self.assertTrue(acceptable_attempt_status(401,expected_statuses=(401,)))

    def test_inflight_fault_only_targets_exact_main_round_and_run(self):
        run = "a"*32
        row = (1,1,run+"-main","running")
        validate_scan_round(self.fixture,row,10,run)
        for bad in ((1,2,run+"-main","running"),(2,1,run+"-main","running"),
                    (1,1,"other-main","running"),(1,1,run+"-main","published"),None):
            with self.subTest(row=bad),self.assertRaises(LabManifestError):
                validate_scan_round(self.fixture,bad,10,run)
        with self.assertRaises(LabManifestError):
            validate_scan_round(self.fixture,row,0,run)

    def test_stop_file_before_execution_never_opens_network_and_cannot_pass(self):
        self.write()
        arguments = SimpleNamespace(work=str(self.work),source_root=str(self.root),
            base="https://127.0.0.1:28769",lab_nonce=self.nonce,release_id=self.release,
            fixture_sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),mode="run",duration=5400)
        (self.work/"stop-attendance-probe").write_text("owned stop request")
        probe = Probe(arguments)
        with patch("tools.login_attendance_probe.ssl.create_default_context",
                   side_effect=AssertionError("A stopped run must not initialize transport")):
            self.assertFalse(asyncio.run(probe.execute()))
        report = json.loads((probe.output/"http-report.json").read_text())
        self.assertEqual(report["client_status"],"cancelled")
        self.assertFalse(report["capacity_result"])
        self.assertEqual(report["stop_reason"],"owned_stop_file_requested")
        self.assertFalse(report["logical_requests"])
        events = [json.loads(line) for line in (probe.output/"request-journal.jsonl").read_text().splitlines()]
        self.assertEqual([item["event"] for item in events],["stop_requested","finished"])

    def test_diagnostic_cannot_bypass_ninety_minute_formal_guard(self):
        async def check():
            probe = Probe.__new__(Probe)
            probe.args = SimpleNamespace(duration=300)
            with self.assertRaises(ValueError):
                await probe.main_run()
            for duration in (299,901):
                probe.args.duration = duration
                with self.assertRaises(ValueError):
                    await probe.diagnostic_run()
            calls=[]
            async def run_load(*,diagnostic):
                calls.append(diagnostic)
            probe._run_load=run_load
            probe.args.duration=300
            await probe.diagnostic_run()
            self.assertEqual(calls,[True])
        asyncio.run(check())

    def test_stop_keeps_full_due_schedule_and_finishes_inflight_operation(self):
        async def check():
            probe = Probe.__new__(Probe)
            probe.args = SimpleNamespace(duration=1)
            probe.due_ticks,probe.sent_ticks = Counter(),Counter()
            probe.schedule_lateness=[]
            probe.last_scheduled_offset_ms=None
            probe.tick_offset=contextvars.ContextVar("test_attendance_offset",default=None)
            probe.stop_event=asyncio.Event()
            probe.started_monotonic=time.perf_counter()
            entered,release=asyncio.Event(),asyncio.Event()
            completed=[]
            async def operation():
                entered.set()
                await release.wait()
                completed.append(probe.tick_offset.get())
            task=asyncio.create_task(probe.periodic("operator",100,0,operation))
            await entered.wait()
            probe.stop_event.set()
            release.set()
            await asyncio.wait_for(task,1)
            self.assertEqual(probe.due_ticks,{"operator":10})
            self.assertEqual(probe.sent_ticks,{"operator":1})
            self.assertEqual(completed,[0])
            self.assertEqual(probe.last_scheduled_offset_ms,0)
        asyncio.run(check())

    def test_queued_attempt_retains_due_offset_and_starting_steady_phase(self):
        async def check():
            probe=Probe.__new__(Probe)
            probe.args=SimpleNamespace(base="https://127.0.0.1:28769")
            probe.handoff={"release_id":self.release}
            probe.intent_offsets={}
            probe.tick_offset=contextvars.ContextVar("test_queue_offset",default=None)
            probe.logical=Counter()
            probe.latency=defaultdict(list)
            probe.statuses,probe.normal_errors=Counter(),Counter()
            probe.in_steady=True
            probe.planned_outage=False
            probe.active_fault_phase=None
            events=[]
            probe.write=events.append
            class Client:
                async def request(inner,*args,**kwargs):
                    probe.in_steady=False
                    return httpx.Response(200,headers={"x-lanshare-release":self.release})
            probe.client=Client()
            intent=probe.prepare_intent("steady_legacy_material","POST","/synthetic",scheduled_ms=4321)
            token=probe.tick_offset.set(9876)
            try:
                await probe.request("steady_legacy_material","POST","/synthetic",intent_id=intent)
            finally:
                probe.tick_offset.reset(token)
            attempt=events[-1]
            self.assertTrue(attempt["steady"])
            self.assertEqual(attempt["phase"],"steady")
            self.assertEqual(attempt["scheduled_offset_ms"],4321)
            self.assertGreaterEqual(attempt["client_epoch_finished"],attempt["client_epoch_started"])
            self.assertNotIn(intent,probe.intent_offsets)
        asyncio.run(check())

    def test_stop_wins_over_ready_marker_before_either_fault_starts(self):
        self.write()
        config=self.work/"synthetic-ssh-config"
        config.write_text("# not used by the mocked transport")
        (self.work/"stop-attendance-probe").write_text("stop")
        (self.work/"fault-ready").write_text("already ready")
        (self.work/"attendance-worker-restart-ready").write_text("already ready")
        marker={"kind":"lanshare-capacity-linux-lab","synthetic":True,
                "lab_nonce":self.nonce,"release_id":self.release,
                "network":{"addresses":{"nginx":"172.30.40.4"}}}
        arguments=SimpleNamespace(work=str(self.work),runtime="/owned/"+self.nonce,
            lab_nonce=self.nonce,ssh_config=str(config),ssh_host="syntheticlab",nginx_ip="172.30.40.4",
            source_root=str(self.root),base="https://127.0.0.1:28769",release_id=self.release,
            fixture_sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),
            remote_runtime="/owned/"+self.nonce)
        with patch("tools.recorder_capacity_fault.subprocess.check_output",return_value=json.dumps(marker)), \
             patch("tools.recorder_capacity_fault.subprocess.Popen",side_effect=AssertionError("Fault must not start")):
            coordinate_app_fault(arguments)
        app=json.loads((self.work/"fault-coordinator-cancelled.json").read_text())
        self.assertEqual(app["status"],"cancelled")
        self.assertFalse(app["lock_held"])
        self.assertFalse(app["app_restarted"])
        responses=[json.dumps(marker),json.dumps({"image_id":"synthetic-image"}),
                   json.dumps([{"Config":{"Labels":{"io.lanshare.capacity-lab.nonce":self.nonce}},
                                "Image":"synthetic-image","State":{"Running":True}}])]
        with patch("subprocess.check_output",side_effect=responses), \
             patch("subprocess.Popen",side_effect=AssertionError("Fault must not start")):
            worker=coordinate_worker_fault(arguments)
        self.assertEqual(worker["status"],"cancelled")
        self.assertFalse(worker["lock_held"])
        self.assertFalse(worker["worker_killed"])

    def test_failed_burst_awaits_all_children_before_returning(self):
        async def check():
            started,finished,accepted=set(),set(),set()
            all_started=asyncio.Event()
            async def request(index):
                started.add(index)
                if len(started)==600:
                    all_started.set()
                try:
                    if index==0:
                        await all_started.wait()
                        raise RuntimeError("synthetic child failure")
                    await all_started.wait()
                    await asyncio.sleep(.001)
                    accepted.add(index)
                finally:
                    finished.add(index)
            with self.assertRaisesRegex(RuntimeError,"synthetic child failure"):
                await gather_owned(*(request(index) for index in range(600)))
            self.assertEqual(started,set(range(600)))
            self.assertEqual(finished,started)
            self.assertEqual(accepted,set(range(1,600)))
        asyncio.run(check())

    def test_stop_created_at_completion_is_checked_without_poll_delay(self):
        self.write()
        arguments=SimpleNamespace(work=str(self.work),source_root=str(self.root),
            base="https://127.0.0.1:28769",lab_nonce=self.nonce,release_id=self.release,
            fixture_sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),mode="diagnostic",duration=300)
        probe=Probe(arguments)
        class Client:
            async def __aenter__(inner):
                return inner
            async def __aexit__(inner,*unused):
                (self.work/"stop-attendance-probe").write_text("stop during transport cleanup")
            async def get(inner,path):
                return httpx.Response(200,headers={"x-lanshare-release":self.release})
        async def noop(*unused):
            return None
        probe.authenticate=probe.lifecycle=probe.diagnostic_run=noop
        with patch("tools.login_attendance_probe.ssl.create_default_context",return_value=object()), \
             patch("tools.login_attendance_probe.httpx.AsyncClient",return_value=Client()):
            self.assertFalse(asyncio.run(probe.execute()))
        report=json.loads((probe.output/"http-report.json").read_text())
        self.assertEqual(report["client_status"],"cancelled")
        self.assertFalse(probe.completed)
        self.assertTrue(report["stop_drain_completed"])
        self.assertFalse(report["capacity_result"])

    def test_raw_restart_identity_refuses_replacement_or_unchanged_process(self):
        before={"Id":"a"*64,"Image":"sha256:"+"b"*64,"State":{"Pid":101}}
        after={**before,"State":{"Pid":102}}
        report=restart_identity_report(before,after)
        self.assertEqual(report["container_id_before"],report["container_id_after"])
        self.assertEqual(report["image_id_before"],report["image_id_after"])
        for changed in ({**after,"Id":"c"*64},{**after,"Image":"sha256:"+"d"*64},before,
                        {**after,"State":{"Pid":0}},{**after,"State":{"Pid":True}}):
            with self.subTest(changed=changed),self.assertRaises(AssertionError):
                restart_identity_report(before,changed)
        with self.assertRaises(AssertionError):
            restart_identity_report({**before,"State":{"Pid":0}},after)

    def test_fault_refuses_missing_client_run_identity_before_injection(self):
        config=self.work/"synthetic-ssh-config"
        config.write_text("# mocked transport")
        (self.work/"fault-ready").write_text("unbound ready marker")
        marker={"kind":"lanshare-capacity-linux-lab","synthetic":True,
            "lab_nonce":self.nonce,"release_id":self.release,"network":{"addresses":{"nginx":"172.30.40.4"}}}
        arguments=SimpleNamespace(work=str(self.work),runtime="/owned/"+self.nonce,
            lab_nonce=self.nonce,ssh_config=str(config),ssh_host="syntheticlab",nginx_ip="172.30.40.4")
        with patch("tools.recorder_capacity_fault.subprocess.check_output",return_value=json.dumps(marker)) as reads, \
             patch("tools.recorder_capacity_fault.subprocess.Popen",side_effect=AssertionError("Unbound fault must not start")):
            with self.assertRaisesRegex(ValueError,"client run identity"):
                coordinate_app_fault(arguments)
        self.assertEqual(reads.call_count,1)  # Read nonce marker only, no inspect/restart/holder.


if __name__ == "__main__":
    unittest.main()
