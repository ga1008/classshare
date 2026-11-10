"""Focused release checks against owned SQLite fixtures, never the live database.

Run inside the final application image with --network none and no data mounts:
  docker run --rm --network none --entrypoint python IMAGE tools/wanderer_release_verify.py
The existing service/ASGI tests perform all business actions; model decisions are
synthetic. No real provider, human account or production conversation is touched.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.isolated_environment import (
    guard_dotenv_loading, guard_postgres_connections, isolate_sqlite_environment,
)

TESTS = (
    "tests.test_behavior_inbox",
    "tests.test_runtime_health_snapshots",
    "tests.test_scheduled_task_service",
    "tests.test_scheduled_task_cancellation",
    "tests.test_wanderer_reflection_schedule",
    "tests.test_wanderer_reflection_snapshot",
    "tests.test_wanderer_communication_recovery",
    "tests.test_wanderer_communication_snapshot",
    "tests.test_wanderer_release_snapshot.ReadOnlySnapshotTest",
    "tests.test_wanderer_account_control.WandererAccountControlTest",
    "tests.test_auth_form_fallback.AuthFormFallbackTests.test_student_html_and_json_share_one_authentication_and_one_login_write",
    "tests.test_auth_form_fallback.AuthFormFallbackTests.test_teacher_success_retains_native_redirect_and_role_scoped_student_session",
    "tests.test_wanderer_platform_execution.WandererPlatformExecutionTest",
    "tests.test_wanderer_capability_catalog",
    "tests.test_agent_capability_catalog_search",
    "tests.test_wanderer_social_v2",
    "tests.test_wanderer_legacy_delivery.WandererLegacyDeliveryTest",
    "tests.test_wanderer_delivery_guard",
    "tests.test_wanderer_daily_reflection_v3",
    "tests.test_wanderer_bond_daily_affinity",
    "tests.test_wanderer_memory_corrections",
    "tests.test_wanderer_admin_api.WandererAdminApiTest.test_message_cursor_is_stable_when_new_messages_arrive",
    "tests.test_wanderer_admin_api.WandererAdminApiTest.test_memory_corrections_require_super_admin_and_preserve_audit",
    "tests.test_wanderer_runtime_v2.RuntimeV2Tests.test_real_callback_reads_then_self_writes_memory",
    "tests.test_wanderer_runtime_v2.RuntimeV2Tests.test_last_credit_executes_commands_but_retains_unpaid_callback",
    "tests.test_wanderer_runtime_v2.RuntimeV2Tests.test_duplicate_source_event_never_calls_again",
    "tests.test_wanderer_runtime_v2.RuntimeV2Tests.test_revocation_while_model_waits_blocks_writes",
    "tests.test_wanderer_runtime_v2.RuntimeV2Tests.test_stale_executing_turn_resumes_receipts_without_model",
    "tests.test_wanderer_runtime_v2.RuntimeV2Tests.test_missing_fixed_version_is_accepted_only_for_complete_current_shape",
    "tests.test_wanderer_runtime_v2.RuntimeV2Tests.test_protocol_repair_is_paid_once_and_replay_never_dispatches_again",
    "tests.test_wanderer_runtime_v2.RuntimeV2Tests.test_second_invalid_protocol_stops_without_business_or_more_repairs",
    "tests.test_wanderer_runtime_v2.RuntimeV2Tests.test_protocol_repair_waits_for_next_day_budget_and_original_event_replays",
    "tests.test_wanderer_runtime_v2.RuntimeV2Tests.test_accepted_business_failure_is_not_a_protocol_repair",
    "tests.test_wanderer_runtime_v2.RuntimeV2Tests.test_historical_protocol_recovery_is_bounded_guarded_and_idempotent",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="List reused suites without application imports")
    args = parser.parse_args()
    if args.list:
        print(json.dumps({"tests": TESTS}, indent=2))
        return 0
    started = time.monotonic()
    log = io.StringIO()
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def external_address(sock, address):
        if sock.family not in (socket.AF_INET, socket.AF_INET6):
            return False
        # Windows implements asyncio's self-pipe with a loopback socketpair.
        # Literal loopback only; DNS names and all external addresses are denied.
        try:
            return not ipaddress.ip_address(address[0]).is_loopback
        except ValueError:
            return True

    def offline_connect(sock, address):
        if external_address(sock, address):
            raise RuntimeError("Release fixture forbids network connections")
        return original_connect(sock, address)

    def offline_connect_ex(sock, address):
        if external_address(sock, address):
            raise RuntimeError("Release fixture forbids network connections")
        return original_connect_ex(sock, address)

    os.chdir(REPO)
    with tempfile.TemporaryDirectory(prefix="lanshare-release-", ignore_cleanup_errors=True) as folder, ExitStack() as guards:
        runtime = Path(folder).resolve()
        (runtime / "db").mkdir()
        isolate_sqlite_environment(runtime)
        guard_dotenv_loading(guards)
        guard_postgres_connections(guards)
        guards.enter_context(patch.object(socket.socket, "connect", offline_connect))
        guards.enter_context(patch.object(socket.socket, "connect_ex", offline_connect_ex))
        with redirect_stdout(log), redirect_stderr(log):
            from classroom_app import config
            if (config.DB_ENGINE != "sqlite" or config.DATABASE_URL
                    or Path(config.DB_PATH).resolve() != runtime / "db" / "classroom.db"):
                raise RuntimeError("Release fixture isolation failed before test import")
            suite = unittest.defaultTestLoader.loadTestsFromNames(TESTS)
            result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
        report = {
            "ok": result.wasSuccessful() and not result.skipped,
            "tests_run": result.testsRun,
            "failures": [test.id() for test, _ in result.failures],
            "errors": [test.id() for test, _ in result.errors],
            "skipped": [{"test": test.id(), "reason": reason} for test, reason in result.skipped],
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "database": "owned_temporary_sqlite",
            "dotenv": "blocked_before_application_import",
            "postgres_connections": "forbidden",
            "network_connections": "external_forbidden_literal_loopback_only",
            "model": "synthetic_decisions_real_services_and_asgi",
        }
        print(json.dumps(report, ensure_ascii=True, indent=2))
        if not report["ok"]:
            print(log.getvalue(), file=sys.stderr)
        return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
