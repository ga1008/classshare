"""Isolation and exact scheduling regressions for the 90-minute lab client."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tests.test_recorder_capacity_probe import make_owned
from tools.platform_capacity_lab_manifest import LabManifestError, fingerprint, source_hashes
from tools.login_attendance_lab import (FIXTURE_KIND, check_student_dto,
                                        load_attendance_fixture, periodic_offsets)
from tools.login_attendance_lab_facts import add_union


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


if __name__ == "__main__":
    unittest.main()
