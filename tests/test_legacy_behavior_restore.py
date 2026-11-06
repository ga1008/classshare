import contextlib
import hashlib
import io
import json
import unittest
from unittest.mock import Mock, patch

from tools import legacy_behavior_restore as restore
from tools.legacy_behavior_restore_archive import RestoreError, validate_archive


def record(ordinal, *, barrier=False):
    return {
        "queue_index": ordinal, "class_offering_id": 0 if barrier else 71, "user_pk": 0 if barrier else 91,
        "user_role": "" if barrier else "student", "display_name": "" if barrier else "Synthetic pupil",
        "page_key": None if barrier else "dashboard",
        "events": None if barrier else [
            {"action_type": "presence_heartbeat", "payload": {"focused": True, "idle_seconds": 0}},
            {"action_type": "page_action", "payload": {"message": "\u6d4b\u8bd5\U0001f4da"}},
        ],
        "session_started_at": None if barrier else "2026-11-04T08:00:00",
        "is_barrier": barrier, "future_present": barrier,
    }


def capture_fixture(records):
    payload = b"".join(json.dumps(row, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"
                       for row in records)
    digest = hashlib.sha256(payload).hexdigest()
    barriers = sum(row["is_barrier"] for row in records)
    events = sum(len(row["events"] or ()) for row in records)
    totals = dict(queue_depth=len(records), business_batches=len(records) - barriers,
                  barrier_batches=barriers, events=events)
    capture = dict(
        status="captured", paused=True, proc_mem_opened_readonly=True, external_pause_left_untouched=True,
        capture_sha256=digest, capture_bytes=len(payload), unfinished_tasks=len(records),
        container_id="a" * 64, image_id="sha256:" + "b" * 64, pid=101, init_pid=100,
        start_ticks="300", cgroup="/synthetic", source_sha256="c" * 64, layout_sha256="d" * 64,
        exe_sha256="e" * 64, lib_sha256="f" * 64, build_id="1" * 40,
        capture_tool_sha256="2" * 64, decoder_sha256="3" * 64, **totals,
    )
    proof = dict(status="passed", byte_identical_captures=True, archive_sha256=digest,
                 records=len(records), bytes=len(payload), **totals)
    return payload, capture, proof, dict(expected_sha256=digest, expected_records=len(records), expected_events=events)


def parse_fixture(fixture):
    payload, capture, proof, expected = fixture
    return validate_archive(io.BytesIO(payload), json.dumps(capture).encode("ascii"),
                            json.dumps(proof).encode("ascii"), **expected)


class LegacyBehaviorRestoreArchiveTests(unittest.TestCase):
    def test_verified_archive_preserves_exact_batches_barriers_and_duplicates(self):
        fixture = capture_fixture([record(0), record(1), record(2, barrier=True)])
        archive = parse_fixture(fixture)
        self.assertEqual(b"".join(row.raw_record for row in archive.rows), fixture[0])
        self.assertEqual([0, 1, 2], [row.ordinal for row in archive.rows])
        self.assertEqual([2, 2, 0], [row.event_count for row in archive.rows])
        self.assertEqual((2, 1, 4, 0, 0), tuple(archive.aggregate()[name] for name in (
            "business_batches", "barrier_batches", "archived_events", "points_credited", "seconds_credited",
        )))

    def test_tampered_payload_or_seal_refused(self):
        payload, capture, proof, expected = capture_fixture([record(0)])
        with self.assertRaisesRegex(RestoreError, "archive_hash_or_totals_mismatch"):
            parse_fixture((payload.replace(b"dashboard", b"somewhere"), capture, proof, expected))
        with self.assertRaisesRegex(RestoreError, "verification_proof_mismatch"):
            parse_fixture((payload, capture, dict(proof, byte_identical_captures=False), expected))
        with self.assertRaisesRegex(RestoreError, "archive_proof_totals_mismatch"):
            parse_fixture((payload, capture, dict(proof, events=3), expected))

    def test_hash_match_cannot_mask_bad_ordinals_or_partial_rows(self):
        with self.assertRaisesRegex(RestoreError, "archive_fifo_mismatch"):
            parse_fixture(capture_fixture([record(0), record(0)]))
        payload, capture, proof, expected = capture_fixture([record(0)])
        with self.assertRaisesRegex(RestoreError, "archive_size_or_count_exceeded"):
            parse_fixture((payload.rstrip(b"\n"), capture, proof, expected))

    def test_ambiguous_nested_json_rejected(self):
        payload, capture, proof, expected = capture_fixture([record(0)])
        with self.assertRaisesRegex(RestoreError, "duplicate_json_key"):
            parse_fixture((payload.replace(b'"focused":true', b'"focused":true,"focused":false'),
                           capture, proof, expected))
        with self.assertRaisesRegex(RestoreError, "nonfinite_json_value"):
            parse_fixture((payload.replace(b'"idle_seconds":0', b'"idle_seconds":1e999'), capture, proof, expected))

    def test_inspect_never_connects_and_error_console_does_not_print_payload(self):
        fixture = capture_fixture([record(0)])
        archive = parse_fixture(fixture)
        args = ["inspect", "--archive", "/synthetic/final-queue", "--expected-sha256", archive.sha256,
                "--expected-records", "1", "--expected-events", "2"]
        with patch.object(restore, "open_sealed_archive", return_value=archive), \
                patch.object(restore, "_connect_configured_database") as connect, \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(0, restore.main(args))
            connect.assert_not_called()
            self.assertEqual("verified_offline", json.loads(stdout.getvalue())["status"])
            self.assertNotIn("Synthetic pupil", stdout.getvalue())
        with patch.object(restore, "open_sealed_archive", side_effect=OSError("secret source payload")), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(1, restore.main(args))
            self.assertEqual({"status": "failed", "diagnostic": "OSError"}, json.loads(stdout.getvalue()))


class LegacyBehaviorRestoreDatabaseGateTests(unittest.TestCase):
    def setUp(self):
        self.archive = parse_fixture(capture_fixture([record(0)]))
        self.identity = restore.DatabaseIdentity("12345", "synthetic", "owner", "127.0.0.1")
        self.conn = Mock(autocommit=True)
        self.conn.info.transaction_status = 0
        self.conn.transaction.return_value = contextlib.nullcontext()

    def test_wrong_physical_identity_refuses_before_advisory_lock_or_schema(self):
        actual = restore.DatabaseIdentity("12346", "synthetic", "owner", "127.0.0.1")
        with patch.object(restore, "_database_identity", return_value=actual), \
                patch.object(restore, "_schema_exists") as schema:
            with self.assertRaisesRegex(RestoreError, "unexpected_database_identity"):
                restore.restore_archive(self.conn, self.archive, self.identity, operator="test", reason="test")
        schema.assert_not_called()
        self.assertEqual(3, self.conn.execute.call_count)

    def test_existing_namespace_never_creates_or_appends(self):
        with patch.object(restore, "_database_identity", return_value=self.identity), \
                patch.object(restore, "_schema_exists", return_value=True), \
                patch.object(restore, "_create_evidence_tables") as create, \
                patch.object(restore, "_append_rows") as append, \
                patch.object(restore, "_verify_private_tables") as private, \
                patch.object(restore, "_verified_rows") as verify:
            result = restore.restore_archive(self.conn, self.archive, self.identity, operator="test", reason="test")
        create.assert_not_called()
        append.assert_not_called()
        private.assert_called_once_with(self.conn)
        verify.assert_called_once_with(self.conn, self.archive)
        self.assertEqual("already_verified", result["status"])
        self.assertEqual(0, result["inserted_batches"])
