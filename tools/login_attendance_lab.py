"""File-only guards and bounded scheduling for the owned attendance load lab.

No discovery, application imports, database coordinates, or network side effects
occur on import. The existing Linux controller owns PG/image/source validation.
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.platform_capacity_lab_manifest import LabManifestError, _json
from tools.recorder_capacity_probe import load_owned_handoff


FIXTURE_KIND = "login-attendance-capacity-fixture"
NONCE = re.compile(r"[a-f0-9]{32}")
SHA256 = re.compile(r"[a-f0-9]{64}")


def periodic_offsets(duration_ms: int, period_ms: int, offset_ms: int = 0) -> range:
    """Every due tick is enumerated in advance, including the last boundary tick.

    Callers must iterate the entire range even if an earlier HTTP call finishes
    late. Lateness is reported; a while(now < end) cutoff must never omit work.
    """
    if any(type(value) is not int for value in (duration_ms, period_ms, offset_ms)):
        raise ValueError("Integer millisecond schedule required")
    if duration_ms <= 0 or period_ms <= 0 or not 0 <= offset_ms < period_ms:
        raise ValueError("Positive bounded periods and phase offset required")
    return range(offset_ms, duration_ms, period_ms)


def load_attendance_fixture(work, source_root, base, nonce, release, fixture_sha256):
    """Validate the complete existing boundary plus the controller-seeded lesson."""
    handoff = load_owned_handoff(work, source_root, base, nonce, release)
    root = Path(work).resolve(strict=True)
    parent = (root / "client-handoff").resolve(strict=True)
    path = (parent / "attendance-fixture.json").resolve(strict=True)
    if (not path.is_relative_to(parent) or SHA256.fullmatch(fixture_sha256 or "") is None
            or hashlib.sha256(path.read_bytes()).hexdigest() != fixture_sha256):
        raise LabManifestError("Explicit controller fixture fingerprint required")
    fixture = _json(path)
    if (not isinstance(fixture, dict) or fixture.get("kind") != FIXTURE_KIND
            or fixture.get("schema_version") != 1 or fixture.get("synthetic") is not True
            or fixture.get("lab_nonce") != nonce or fixture.get("release_id") != release
            or fixture.get("offering_id") != handoff["offering_id"]
            or fixture.get("students") != handoff["users"]):
        raise LabManifestError("Attendance fixture differs from the owned synthetic cohort")
    allowed = {"kind", "schema_version", "synthetic", "lab_nonce", "release_id", "offering_id",
               "students", "teacher", "sessions", "networks", "private_canary", "created_at_ms", "viewers"}
    if fixture.keys() - allowed:
        raise LabManifestError("Unexpected attendance fixture coordinates")
    teacher = fixture.get("teacher")
    if (not isinstance(teacher, dict) or set(teacher) != {"id", "email"}
            or type(teacher["id"]) is not int or teacher["id"] <= 0
            or not isinstance(teacher["email"], str) or not teacher["email"].endswith("@example.invalid")):
        raise LabManifestError("Explicit synthetic owner teacher required")
    sessions = fixture.get("sessions")
    if (not isinstance(sessions, dict) or set(sessions) != {"main", "lifecycle"}
            or any(type(value) is not int or value <= 0 for value in sessions.values())
            or len(set(sessions.values())) != 2):
        raise LabManifestError("Distinct controller-created main/lifecycle lessons required")
    networks = fixture.get("networks")
    if not isinstance(networks, list) or not 1 <= len(networks) <= 4:
        raise LabManifestError("Owned lab ingress networks required")
    for value in networks:
        try:
            network = ipaddress.ip_network(value, strict=True)
        except (TypeError, ValueError):
            raise LabManifestError("Invalid lab ingress network") from None
        if not network.is_private or network.prefixlen != network.max_prefixlen:
            raise LabManifestError("The lab permits exact private ingress addresses only")
    if fixture.get("private_canary") != "ATTENDANCE_PRIVATE_CANARY_" + nonce:
        raise LabManifestError("Nonce-bound privacy canary required")
    viewers = fixture.get("viewers", [])
    if not isinstance(viewers, list) or len(viewers) > 3:
        raise LabManifestError("At most three explicit synthetic history viewers required")
    viewer_ids, viewer_scopes = {teacher["id"]}, {handoff["offering_id"]}
    for viewer in viewers:
        if (not isinstance(viewer, dict) or set(viewer) != {"id", "email", "offering_id", "session_id", "round_id"}
                or any(type(viewer.get(key)) is not int or viewer[key] <= 0 for key in ("id", "offering_id", "session_id", "round_id"))
                or not str(viewer.get("email", "")).endswith("@example.invalid")
                or viewer["id"] in viewer_ids or viewer["offering_id"] in viewer_scopes):
            raise LabManifestError("Viewer must own a distinct synthetic historical classroom")
        viewer_ids.add(viewer["id"])
        viewer_scopes.add(viewer["offering_id"])
    backend = handoff["client_source_sha256"]
    if not {"classroom_app/db/schema_login_attendance.py",
            "classroom_app/services/login_attendance_rules.py"}.issubset(backend):
        raise LabManifestError("Frozen source must contain the new attendance implementation")
    return handoff, fixture


def check_student_dto(value, fixture):
    """Reject sensitive values or fields anywhere, including nested metadata."""
    forbidden = {"ip_address", "networks", "network_source", "session_hash", "watermark_json",
                 "baseline_json", "input_hash", "raw_metrics", "interaction_quality",
                 "interaction_quality_factor", "interaction_quality_reason", "private_notes",
                 "quality", "suggestion", "deviation_ms", "reason", "evidence"}
    def visit(item):
        if isinstance(item, dict):
            if forbidden.intersection(item):
                raise AssertionError("Private attendance/analysis field reached student DTO")
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str) and fixture["private_canary"] in item:
            raise AssertionError("Private attendance canary reached student DTO")
    visit(value)


def coordinate_worker_fault(args):
    """Kill/start only this nonce's real scheduler after its client marker.

    Existing app/PG/collector service stays online. The Linux controller still
    validates physical PG; this control plane never accepts database options.
    """
    import json
    import shlex
    import subprocess
    import time

    load_attendance_fixture(args.work, args.source_root, args.base, args.lab_nonce,
                            args.release_id, args.fixture_sha256)
    work = Path(args.work).resolve(strict=True)
    runtime = args.remote_runtime
    if (not re.fullmatch(r"/[A-Za-z0-9_./-]+", runtime) or ".." in Path(runtime).parts
            or Path(runtime).name != args.lab_nonce):
        raise LabManifestError("Explicit nonce-owned Linux runtime required")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", args.ssh_host):
        raise LabManifestError("Explicit pinned SSH alias required")
    config = Path(args.ssh_config).resolve(strict=True)
    ssh = ["ssh", "-F", str(config), args.ssh_host]
    def command(code):
        return subprocess.check_output([*ssh, code], text=True, timeout=30).strip()
    marker = json.loads(command("cat "+shlex.quote(runtime+"/lab-marker.json")))
    if (marker.get("kind") != "lanshare-capacity-linux-lab" or marker.get("synthetic") is not True
            or marker.get("lab_nonce") != args.lab_nonce or marker.get("release_id") != args.release_id):
        raise LabManifestError("Remote ownership differs from the explicit client boundary")
    build = json.loads(command("cat "+shlex.quote(runtime+"/build-state.json")))
    name = "lanshare-capacity-"+args.lab_nonce+"-scheduler-1"
    old = json.loads(command("docker inspect "+name))[0]
    if (old["Config"]["Labels"].get("io.lanshare.capacity-lab.nonce") != args.lab_nonce
            or old["Image"] != build["image_id"] or not old["State"]["Running"]):
        raise LabManifestError("Scheduler is not the running immutable owned image")
    output = work/"attendance-worker-restarted"
    if output.exists():
        raise LabManifestError("Preserve an existing worker fault run")
    deadline = time.monotonic()+7200
    while time.monotonic() < deadline and not (work/"attendance-worker-restart-ready").exists():
        time.sleep(.2)
    if not (work/"attendance-worker-restart-ready").exists():
        raise RuntimeError("Client did not reach the declared worker fault phase")
    state_path = shlex.quote(runtime+"/artifacts/background-worker-state.json")
    initial = json.loads(command("cat "+state_path))
    began = time.monotonic()
    command("docker kill --signal KILL "+name)
    command("docker start "+name)
    deadline = time.monotonic()+90
    while time.monotonic() < deadline:
        current = json.loads(command("docker inspect "+name))[0]
        try:
            state = json.loads(command("cat "+state_path))
        except (json.JSONDecodeError, subprocess.SubprocessError):
            time.sleep(.2)
            continue
        if (current["State"]["Running"] and state.get("status") == "running"
                and state.get("started_at") != initial.get("started_at")):
            break
        time.sleep(.2)
    else:
        raise RuntimeError("Real scheduler did not restart and publish a new worker state")
    if current["Id"] != old["Id"] or current["Image"] != old["Image"] or current["State"]["Pid"] == old["State"]["Pid"]:
        raise AssertionError("Scheduler restart identity proof failed")
    result = {"status": "restarted", "lab_nonce": args.lab_nonce,
        "same_container_and_image": True, "old_host_pid": old["State"]["Pid"],
        "new_host_pid": current["State"]["Pid"], "client_monotonic_recovery_seconds": time.monotonic()-began,
        "clock_domain": "Client monotonic duration; VM ISO worker states remain separate",
        "worker_started_at_server": state["started_at"], "other_roles_restarted": False,
        "scope": "Process restart proof; scheduled-task lease recovery and final facts are separately required"}
    temporary = output.with_suffix(".new")
    temporary.write_text(json.dumps(result, indent=2))
    temporary.replace(output)
    print(json.dumps(result))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("worker-fault",))
    for argument in ("work", "source-root", "base", "lab-nonce", "release-id", "fixture-sha256",
                     "ssh-config", "ssh-host", "remote-runtime"):
        parser.add_argument("--"+argument, required=True)
    coordinate_worker_fault(parser.parse_args())

