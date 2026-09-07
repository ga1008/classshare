"""Read-only deployment gate for an explicit native PostgreSQL rehearsal report."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from tools.signature_visibility_rehearsal import valid_report_proof
REPORT_CONTRACT = "native-pg-rehearsal-v1"
REQUIRED_MIGRATION_FILES = (
    "classroom_app/config.py", "classroom_app/database.py", "classroom_app/storage_paths.py",
    "classroom_app/db/schema.py", "classroom_app/db/postgres_schema.py",
    "classroom_app/db/postgres_required_columns.py", "classroom_app/db/schema_assignments.py",
    "classroom_app/db/schema_ai_jobs.py", "classroom_app/db/schema_grade_publications.py",
    "classroom_app/db/schema_signature_workflow.py",
)


def file_hash(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def migration_source_hashes(repo_root: Path) -> dict[str, str]:
    repo_root = repo_root.resolve(strict=True)
    names = set(REQUIRED_MIGRATION_FILES)
    names.update(path.relative_to(repo_root).as_posix() for path in (repo_root / "classroom_app/db").rglob("*.py"))
    return {name: file_hash(repo_root / name) for name in sorted(names)}


def validate_report(report: dict[str, Any], *, repo_root: Path, backup_file: Path) -> dict[str, Any]:
    blockers = []

    def require(condition, code):
        if not condition:
            blockers.append(code)

    def preserved_phase(phase):
        raw = phase.get("old_differences_per_pass")
        if raw == [[], []] and not phase.get("signature_visibility_migration_per_pass"):
            return True
        proofs = phase.get("signature_visibility_migration_per_pass")
        # A registry created by this migration has no old rows to compare;
        # its sole new marker is still verified in each proof and on restart.
        registry_added = "schema_migrations" in (phase.get("added_tables") or [])
        return (isinstance(proofs, list) and len(proofs) == 2
                and all(valid_report_proof(proof) for proof in proofs)
                and proofs[0] == proofs[1]
                and raw == [[item for item in proof["allowed_differences"]
                             if item != "table:schema_migrations" or not registry_added] for proof in proofs]
                and phase.get("unexpected_old_differences_per_pass") == [[], []])

    require(report.get("report_contract") == REPORT_CONTRACT, "native_report_contract_missing")
    require(report.get("status") == "ok", "native_report_not_passed")
    require(report.get("database_engine") == "postgres", "native_postgres_engine_required")
    source = report.get("source_export") or {}
    require(source.get("remote_local_hash_equal") is True and source.get("unchanged_after_rehearsal") is True,
            "source_backup_preservation_not_verified")
    try:
        with backup_file.open("rb") as stream:
            require(stream.read(5) == b"PGDMP", "postgres_custom_dump_required")
        backup_hash = file_hash(backup_file)
        require(source.get("sha256") == backup_hash, "backup_digest_mismatch")
        require(source.get("bytes") == backup_file.stat().st_size, "backup_size_mismatch")
    except OSError:
        backup_hash = None
        blockers.append("explicit_backup_unavailable")
    restore = report.get("clean_restore") or {}
    require(restore.get("restored_again_from_original_dump") is True
            and restore.get("baseline_equals_initial_unmigrated_restore") is True
            and restore.get("no_timestamp_or_sequence_whitelist") is True, "clean_native_restore_not_verified")
    incremental = report.get("incremental") or {}
    require(incremental.get("status") == "ok" and incremental.get("database_preservation_passed") is True,
            "incremental_migration_not_passed")
    require(preserved_phase(incremental)
            and incremental.get("idempotency_differences") == [], "incremental_differences_or_missing_checks")
    startup = report.get("full_startup") or {}
    require(startup.get("strict_status") == "ok" and startup.get("fresh_process_per_pass") is True
            and (startup.get("all_old_fields_and_sequences_unchanged") is True
                 or startup.get("all_old_fields_except_verified_signature_scope_and_sequences_unchanged") is True), "full_startup_not_passed")
    require(preserved_phase(startup)
            and startup.get("idempotency_differences") == [], "full_startup_differences_or_missing_checks")
    runs = startup.get("runs")
    require(isinstance(runs, list) and len(runs) == 2, "two_fresh_startup_runs_required")
    for index, run in enumerate(runs if isinstance(runs, list) else []):
        require(isinstance(run, dict) and run.get("status") == "ok"
                and isinstance(run.get("required_table_count"), int) and run["required_table_count"] > 0
                and run.get("present_required_table_count") == run["required_table_count"]
                and run.get("index_failed") == 0 and run.get("skipped_step_count") == 0,
                f"startup_run_{index + 1}_incomplete")
    isolation = report.get("isolation") or {}
    require(isolation.get("dedicated_new_cluster") is True and isolation.get("dedicated_new_database") is True
            and isolation.get("production_tables_modified") is False and isolation.get("app_or_workers_started") is False,
            "offline_isolation_not_verified")
    try:
        actual_sources = migration_source_hashes(repo_root)
        recorded = report.get("migration_source_sha256")
        require(isinstance(recorded, dict) and set(recorded) == set(actual_sources), "migration_source_set_mismatch")
        if isinstance(recorded, dict):
            require(recorded == actual_sources, "migration_source_digest_mismatch")
    except OSError:
        actual_sources = {}
        blockers.append("required_migration_source_unavailable")
    return {"status": "failed" if blockers else "ok", "database_engine": "postgres",
            "backup_sha256": backup_hash, "migration_source_file_count": len(actual_sources),
            "blockers": blockers, "production_data_modified": False,
            "scope": "Native database migration gate only; attachment backup and live deployment checks remain separate."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--backup-file", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--json-output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.json_output.exists() or args.json_output.resolve() in {args.report.resolve(), args.backup_file.resolve()}:
        parser.error("Validation output must be a new path distinct from report and backup")
    try:
        result = validate_report(json.loads(args.report.read_text(encoding="utf-8")),
                                 repo_root=args.repo_root, backup_file=args.backup_file)
    except Exception as exc:
        result = {"status": "failed", "error_type": type(exc).__name__, "production_data_modified": False}
    with args.json_output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"Native PostgreSQL migration gate: {result['status']}; report: {args.json_output}")
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
