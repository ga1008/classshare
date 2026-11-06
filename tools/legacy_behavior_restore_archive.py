"""Validate a sealed legacy behavior queue without importing application code."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import BinaryIO


MAX_ARCHIVE_BYTES = 64 << 20
MAX_RECORD_BYTES = 4 << 20
MAX_PROOF_BYTES = 64 << 10
MAX_RECORDS = 20000
HEX_SHA = re.compile(r"[a-f0-9]{64}")
RECORD_FIELDS = frozenset((
    "queue_index", "class_offering_id", "user_pk", "user_role", "display_name", "page_key",
    "events", "session_started_at", "is_barrier", "future_present",
))
CAPTURE_IDENTITY = (
    "container_id", "image_id", "pid", "init_pid", "start_ticks", "cgroup", "source_sha256",
    "layout_sha256", "exe_sha256", "lib_sha256", "build_id", "capture_tool_sha256", "decoder_sha256",
)


class RestoreError(ValueError):
    """A fixed diagnostic code safe to print to the operator console."""


def require(condition, code: str) -> None:
    if not condition:
        raise RestoreError(code)


def _unique_json_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def _finite_json_float(text: str) -> float:
    value = float(text)
    require(math.isfinite(value), "nonfinite_json_value")
    return value


def _parse_json(raw: bytes):
    try:
        return json.loads(raw.decode("ascii"), object_pairs_hook=_unique_json_keys,
                          parse_constant=_finite_json_float, parse_float=_finite_json_float)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise RestoreError("invalid_archive_json") from None


def _validate_identity(proof: dict) -> dict:
    identity = {key: proof.get(key) for key in CAPTURE_IDENTITY}
    for key, value in identity.items():
        if key == "container_id" or key.endswith("_sha256"):
            require(type(value) is str and HEX_SHA.fullmatch(value), "invalid_capture_identity_hash")
    image = identity["image_id"]
    pid, init = identity["pid"], identity["init_pid"]
    ticks, cgroup, build = identity["start_ticks"], identity["cgroup"], identity["build_id"]
    require(type(image) is str and re.fullmatch(r"sha256:[a-f0-9]{64}", image)
            and type(pid) is int and type(init) is int and 1 < init < pid
            and type(ticks) is str and re.fullmatch(r"[1-9][0-9]{0,19}", ticks)
            and type(cgroup) is str and cgroup.startswith("/") and len(cgroup) <= 512
            and type(build) is str and re.fullmatch(r"[a-f0-9]{40}", build), "invalid_capture_identity")
    return identity


@dataclass(frozen=True, slots=True)
class ArchiveRow:
    ordinal: int
    record_sha256: str
    event_count: int
    is_barrier: bool
    raw_record: bytes


@dataclass(frozen=True, slots=True)
class SealedArchive:
    sha256: str
    byte_count: int
    event_count: int
    barrier_count: int
    source_identity_json: str
    proof_sha256: str
    rows: tuple[ArchiveRow, ...]

    def aggregate(self) -> dict:
        return {
            "archive_sha256": self.sha256,
            "records": len(self.rows),
            "business_batches": len(self.rows) - self.barrier_count,
            "barrier_batches": self.barrier_count,
            "archived_events": self.event_count,
            "bytes": self.byte_count,
            "points_credited": 0,
            "seconds_credited": 0,
            "live_behavior_events_created": 0,
        }


def _archive_row(raw: bytes, ordinal: int) -> ArchiveRow:
    row = _parse_json(raw)
    require(type(row) is dict and set(row) == RECORD_FIELDS
            and type(row["queue_index"]) is int and row["queue_index"] == ordinal,
            "archive_fifo_mismatch")
    require(type(row["class_offering_id"]) is int and type(row["user_pk"]) is int
            and type(row["user_role"]) is str and type(row["display_name"]) is str
            and (row["page_key"] is None or type(row["page_key"]) is str)
            and (row["session_started_at"] is None or type(row["session_started_at"]) is str)
            and type(row["is_barrier"]) is bool and type(row["future_present"]) is bool,
            "invalid_archive_record_fields")
    events = row["events"]
    if row["is_barrier"]:
        require(events is None and row["future_present"], "invalid_archive_barrier")
        event_count = 0
    else:
        require(type(events) is list and events and all(type(event) is dict for event in events),
                "invalid_archive_events")
        event_count = len(events)
    return ArchiveRow(ordinal, hashlib.sha256(raw).hexdigest(), event_count, row["is_barrier"], raw)


def validate_archive(stream: BinaryIO, capture: bytes, verification: bytes, *,
                     expected_sha256: str, expected_records: int, expected_events: int) -> SealedArchive:
    require(type(expected_sha256) is str and HEX_SHA.fullmatch(expected_sha256)
            and type(expected_records) is int and 0 < expected_records <= MAX_RECORDS
            and type(expected_events) is int and expected_events >= 0, "invalid_expected_archive")
    require(0 < len(capture) <= MAX_PROOF_BYTES and 0 < len(verification) <= MAX_PROOF_BYTES,
            "invalid_proof_size")
    first = _parse_json(capture)
    final = _parse_json(verification)
    require(type(first) is dict and first.get("status") == "captured" and first.get("paused") is True
            and first.get("proc_mem_opened_readonly") is True and first.get("external_pause_left_untouched") is True
            and first.get("capture_sha256") == expected_sha256, "capture_proof_mismatch")
    require(type(final) is dict and final.get("status") == "passed"
            and final.get("byte_identical_captures") is True and final.get("archive_sha256") == expected_sha256,
            "verification_proof_mismatch")
    identity = _validate_identity(first)

    digest = hashlib.sha256()
    byte_count = event_count = barrier_count = 0
    rows = []
    while raw := stream.readline(MAX_RECORD_BYTES + 1):
        byte_count += len(raw)
        require(len(raw) <= MAX_RECORD_BYTES and byte_count <= MAX_ARCHIVE_BYTES and raw.endswith(b"\n")
                and len(rows) < expected_records, "archive_size_or_count_exceeded")
        row = _archive_row(raw, len(rows))
        rows.append(row)
        event_count += row.event_count
        barrier_count += int(row.is_barrier)
        digest.update(raw)
    require(digest.hexdigest() == expected_sha256 and len(rows) == expected_records
            and event_count == expected_events, "archive_hash_or_totals_mismatch")
    counts = {"queue_depth": len(rows), "events": event_count, "barrier_batches": barrier_count,
              "business_batches": len(rows) - barrier_count}
    require(all(type(first.get(key)) is int and type(final.get(key)) is int and first[key] == final[key] == value
                for key, value in counts.items())
            and first.get("unfinished_tasks") == final.get("records") == len(rows)
            and first.get("capture_bytes") == final.get("bytes") == byte_count,
            "archive_proof_totals_mismatch")
    return SealedArchive(expected_sha256, byte_count, event_count, barrier_count,
                         json.dumps(identity, sort_keys=True, separators=(",", ":")),
                         hashlib.sha256(verification).hexdigest(), tuple(rows))


def _owned_directory(stack: ExitStack, path, *, parent_fd: int | None = None) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(path, flags, dir_fd=parent_fd)
    stack.callback(os.close, fd)
    info = os.fstat(fd)
    require(stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == os.geteuid(), "archive_directory_not_private")
    return fd


@contextmanager
def _owned_file(directory: int, name: str, max_size: int):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    fd = os.open(name, flags, dir_fd=directory)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and stat.S_IMODE(before.st_mode) == 0o600
                and before.st_uid == os.geteuid() and before.st_nlink == 1
                and 0 < before.st_size <= max_size, "archive_file_not_private_or_invalid_size")
        yield stream
        after = os.fstat(stream.fileno())
        require((before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_size, after.st_mtime_ns, after.st_ctime_ns), "archive_file_changed")


def open_sealed_archive(path: Path, **expected) -> SealedArchive:
    require(os.name == "posix" and path.is_absolute() and path.resolve(strict=True) == path
            and path.name == "final-queue", "exact_private_final_archive_required")
    with ExitStack() as stack:
        root = _owned_directory(stack, path)
        first = _owned_directory(stack, "first", parent_fd=root)
        proof = stack.enter_context(_owned_file(root, "verification.json", MAX_PROOF_BYTES))
        capture = stack.enter_context(_owned_file(first, "capture-summary.json", MAX_PROOF_BYTES))
        payload = stack.enter_context(_owned_file(first, "capture.jsonl", MAX_ARCHIVE_BYTES))
        return validate_archive(payload, capture.read(MAX_PROOF_BYTES + 1), proof.read(MAX_PROOF_BYTES + 1), **expected)
