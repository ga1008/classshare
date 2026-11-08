"""Destructive load must refuse unowned targets before a network client exists."""
import hashlib
import json
from pathlib import Path

import unittest
import tempfile
from contextlib import contextmanager


@contextmanager
def raises(exception):
    with unittest.TestCase().assertRaises(exception):
        yield

from tools.platform_capacity_lab_manifest import LabManifestError, fingerprint, source_hashes
from tools.recorder_capacity_probe import load_owned_handoff


def make_owned(tmp_path):
    root = tmp_path / "source"
    (root / "classroom_app").mkdir(parents=True)
    (root / "classroom_app" / "app.py").write_text("# synthetic source\n")
    (root / "scheduled_task_dispatcher.py").write_text("# synthetic worker\n")
    work = root / ".codex-temp" / "client"
    handoff = work / "client-handoff"
    handoff.mkdir(parents=True)
    nonce = "a" * 32
    release = "capacity-linux-" + nonce
    users = [{"identifier": f"CAPACITY-SYNTHETIC-{i:04d}", "student_id": i + 1} for i in range(200)]
    ca = b"a synthetic certificate fingerprint fixture; no TLS or network is used"
    sources = source_hashes(root)
    data = {"kind": "capacity-linux-controller-handoff", "schema_version": 1,
            "synthetic": True, "lab_nonce": nonce, "release_id": release,
            "offering_id": 1, "users": users, "client_source_sha256": sources,
            "server_source_sha256": sources, "ca_sha256": hashlib.sha256(ca).hexdigest(),
            "expected_identity": {"lab_nonce": nonce, "release_id": release,
                "users_sha256": fingerprint(users), "offering_id": 1,
                "source_fingerprint": fingerprint(sources)}}
    (handoff / "capacity-lab.crt").write_bytes(ca)
    (handoff / "handoff.json").write_text(json.dumps(data))
    (handoff / "capacity-reading.json").write_text(json.dumps(
        {"synthetic": True, "class_offering_id": 1, "material_id": 2}))
    (handoff / "capacity-social.json").write_text(json.dumps(
        {"synthetic": True, "student_ids": [r["student_id"] for r in users],
         "identifiers": [r["identifier"] for r in users]}))
    return root, work, nonce, release, data


def check(owned, **overrides):
    root, work, nonce, release, _ = owned
    args = dict(work=work, source_root=root, base="https://127.0.0.1:28769", nonce=nonce, release=release)
    args.update(overrides)
    return load_owned_handoff(**args)


def update(owned, mutate):
    _, work, _, _, data = owned
    mutate(data)
    (work / "client-handoff" / "handoff.json").write_text(json.dumps(data))


def _assert_accepts_owned_pinned_source_matched_fixture(owned):
    assert len(check(owned)["users"]) == 200


def _assert_refuses_production_or_ambiguous_target(owned, base):
    with raises(LabManifestError):
        check(owned, base=base)


def _assert_refuses_changed_ownership_or_fixtures(owned, mutation):
    update(owned, mutation)
    with raises(LabManifestError):
        check(owned)


def _assert_refuses_actual_source_drift(owned):
    root, _, _, _, _ = owned
    (root / "classroom_app" / "app.py").write_text("# changed after export\n")
    with raises(LabManifestError):
        check(owned)


def _assert_refuses_work_outside_source_temporary_root(owned, tmp_path):
    outside = tmp_path / "unowned"
    outside.mkdir()
    with raises(LabManifestError):
        check(owned, work=outside)


def _assert_requires_explicit_nonce(owned, nonce):
    with raises(LabManifestError):
        check(owned, nonce=nonce)


BASES = [
    "https://production.example.invalid:443", "http://127.0.0.1:28769",
    "https://192.0.2.1:28769", "https://localhost:28769", "https://127.0.0.1:443",
    "https://127.0.0.1:28769/api", "https://user:password@127.0.0.1:28769",
    "https://127.0.0.1:28769/?target=production", "https://127.0.0.1:28769/#fragment",
    "https://127.0.0.1:99999",
]
MUTATIONS = [
    lambda d: d.update(synthetic=False),
    lambda d: d.update(lab_nonce="b" * 32),
    lambda d: d.update(release_id="production"),
    lambda d: d.update(database={"host": "production.example.invalid"}),
    lambda d: d.update(ca_sha256="0" * 64),
    lambda d: d["users"][0].update(identifier="a real account"),
    lambda d: d["users"][1].update(student_id=1),
    lambda d: d["client_source_sha256"].clear(),
    lambda d: d["expected_identity"].update(users_sha256="0" * 64),
]
NONCES = ["", "z" * 32, "../escape", "a" * 31]

class RecorderCapacityIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        self.owned = make_owned(self.path)

    def test_owned(self):
        _assert_accepts_owned_pinned_source_matched_fixture(self.owned)

    def test_production_targets(self):
        for base in BASES:
            with self.subTest(base=base):
                _assert_refuses_production_or_ambiguous_target(self.owned, base)

    def test_ownership_mutations(self):
        for index, mutation in enumerate(MUTATIONS):
            with self.subTest(mutation=index):
                with tempfile.TemporaryDirectory() as temporary:
                    _assert_refuses_changed_ownership_or_fixtures(make_owned(Path(temporary)), mutation)

    def test_source_drift(self):
        _assert_refuses_actual_source_drift(self.owned)

    def test_work_scope(self):
        _assert_refuses_work_outside_source_temporary_root(self.owned, self.path)

    def test_nonces(self):
        for nonce in NONCES:
            with self.subTest(nonce=nonce):
                _assert_requires_explicit_nonce(self.owned, nonce)


if __name__ == "__main__":
    unittest.main()
