from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_ROOT = REPO_ROOT / ".codex-temp"
DEFAULT_TARGET_ROOT = REPO_ROOT / "target" / "target03" / "P01"


REPORT_PATHS = {
    "migration_readiness": "db-migration-readiness-current/migration-readiness.json",
    "file_integrity": "db-file-integrity-current/file-integrity.json",
    "pg_lab": "pg-migration-lab/reports/lab-summary.json",
    "postgres_preflight": "pg-migration-lab/reports/postgres-preflight.json",
    "backup_rollback": "db-backup-rollback-current/reports/backup-rollback.json",
    "performance_acceptance": "db-performance-acceptance-current/reports/performance-acceptance.json",
}

OPTIONAL_REPORT_PATHS = {
    "remediation_plan": "db-remediation-plan-current/reports/remediation-plan.json",
    "attachment_restore_plan": "db-attachment-restore-plan-current/reports/attachment-restore-plan.json",
}

SUPPORTED_GATE_PHASES = {"pre-cutover", "final-cutover"}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {"status": "missing", "error": f"missing report: {path}"}
    except Exception as exc:
        return {"status": "unreadable", "error": str(exc)}


def _target_execution_status(target_root: Path) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    for path in sorted(target_root.glob("T*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        statuses.append(
            {
                "file": str(path),
                "name": path.name,
                "has_execution_record": "执行记录" in text,
                "mentions_no_cutover": "不允许" in text and ("切换" in text or "部署" in text),
            }
        )
    return statuses


def _add_blocker(blockers: list[dict[str, str]], blocker_id: str, message: str, details: str = "") -> None:
    blockers.append({"id": blocker_id, "message": message, "details": details})


def build_cutover_gate_report(
    *,
    report_root: Path = DEFAULT_REPORT_ROOT,
    target_root: Path = DEFAULT_TARGET_ROOT,
    phase: str = "final-cutover",
) -> dict[str, Any]:
    normalized_phase = str(phase or "final-cutover").strip().lower()
    if normalized_phase not in SUPPORTED_GATE_PHASES:
        raise ValueError(f"phase must be one of {sorted(SUPPORTED_GATE_PHASES)}, got {phase!r}")
    report_root = report_root.resolve()
    target_root = target_root.resolve()
    reports = {name: _load_json(report_root / relative) for name, relative in REPORT_PATHS.items()}
    optional_reports = {name: _load_json(report_root / relative) for name, relative in OPTIONAL_REPORT_PATHS.items()}
    target_statuses = _target_execution_status(target_root)
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    missing_reports = [name for name, data in reports.items() if data.get("status") in {"missing", "unreadable"}]
    if missing_reports:
        _add_blocker(blockers, "CUT-R001", "Required cutover reports are missing or unreadable.", ", ".join(missing_reports))

    migration = reports.get("migration_readiness", {})
    if int(migration.get("foreign_key_violations") or 0) > 0:
        remediation = optional_reports.get("remediation_plan", {})
        remediation_effect = remediation.get("cutover_effect") or {}
        if (
            remediation.get("status") == "ok"
            and remediation.get("apply_to_copy") is True
            and remediation_effect.get("foreign_key_blocker_cleared_on_copy") is True
        ):
            warnings.append(
                {
                    "id": "CUT-W002",
                    "message": "SQLite source has foreign-key violations, but the copied-database remediation plan clears them.",
                    "details": "Migration must use the remediated copy and preserve the remediation report.",
                }
            )
        else:
            _add_blocker(
                blockers,
                "CUT-R002",
                "SQLite source still has foreign-key violations.",
                f"foreign_key_violations={migration.get('foreign_key_violations')}",
            )

    file_integrity = reports.get("file_integrity", {})
    if int(file_integrity.get("missing_references") or 0) > 0:
        attachment_restore = optional_reports.get("attachment_restore_plan", {})
        restore_effect = attachment_restore.get("cutover_effect") or {}
        if (
            attachment_restore.get("status") == "ok"
            and restore_effect.get("file_blocker_cleared") is True
            and (
                restore_effect.get("all_missing_files_restored") is True
                or restore_effect.get("accepted_exception_manifest_valid") is True
            )
        ):
            warnings.append(
                {
                    "id": "CUT-W003",
                    "message": "File integrity report still has missing references, but an attachment restore plan explicitly clears or accepts them.",
                    "details": "Preserve the attachment restore plan and any exception manifest in the cutover report.",
                }
            )
        else:
            _add_blocker(
                blockers,
                "CUT-R003",
                "File metadata integrity report still has missing references.",
                f"missing_references={file_integrity.get('missing_references')}",
            )

    pg_lab = reports.get("pg_lab", {})
    pg_target = pg_lab.get("postgres_target") or {}
    if not pg_target.get("actual_postgres_data_load_executed", False):
        _add_blocker(blockers, "CUT-R004", "PostgreSQL data load has not been executed in the lab report.")

    postgres_preflight = reports.get("postgres_preflight", {})
    postgres_cutover_requested = bool(postgres_preflight.get("postgres_cutover_requested", False))
    if normalized_phase == "pre-cutover" and postgres_cutover_requested:
        _add_blocker(
            blockers,
            "CUT-R011",
            "Pre-cutover phase must not request production PostgreSQL cutover yet.",
            "Run the final-cutover phase after stage 4 configuration is intentionally changed.",
        )
    if normalized_phase == "final-cutover" and not postgres_cutover_requested:
        _add_blocker(blockers, "CUT-R005", "Real docker.env is not requesting PostgreSQL cutover.")
    if normalized_phase == "pre-cutover" and not postgres_cutover_requested:
        warnings.append(
            {
                "id": "CUT-W004",
                "message": "docker.env is still in SQLite mode, as expected before stage 4.",
                "details": "Use final-cutover phase only after the planned configuration switch.",
            }
        )
    if postgres_preflight.get("blockers"):
        _add_blocker(
            blockers,
            "CUT-R006",
            "PostgreSQL deployment preflight has blockers.",
            ", ".join(item.get("id", "") for item in postgres_preflight.get("blockers", [])),
        )

    backup = reports.get("backup_rollback", {})
    if not backup.get("sqlite_restore_drill_executed", False):
        _add_blocker(blockers, "CUT-R007", "SQLite restore drill has not executed.")
    if not backup.get("postgres_restore_drill_executed", False):
        _add_blocker(blockers, "CUT-R008", "PostgreSQL dump/restore drill has not executed.")

    performance = reports.get("performance_acceptance", {})
    gates = performance.get("acceptance_gates") or {}
    if not gates.get("postgres_baseline_recorded", False):
        _add_blocker(blockers, "CUT-R009", "PostgreSQL performance baseline has not been recorded.")
    if not gates.get("remote_docker_load_test_recorded", False):
        _add_blocker(blockers, "CUT-R010", "Remote Docker Compose load test has not been recorded.")

    missing_execution_records = [item["name"] for item in target_statuses if not item.get("has_execution_record")]
    if missing_execution_records:
        warnings.append(
            {
                "id": "CUT-W001",
                "message": "Some target files do not have execution records yet.",
                "details": ", ".join(missing_execution_records),
            }
        )

    return {
        "status": "blocked" if blockers else "ready",
        "phase": normalized_phase,
        "report_root": str(report_root),
        "target_root": str(target_root),
        "reports": {
            name: {
                "status": data.get("status"),
                "path": str(report_root / REPORT_PATHS[name]),
            }
            for name, data in reports.items()
        },
        "optional_reports": {
            name: {
                "status": data.get("status"),
                "path": str(report_root / OPTIONAL_REPORT_PATHS[name]),
            }
            for name, data in optional_reports.items()
        },
        "target_statuses": target_statuses,
        "blockers": blockers,
        "warnings": warnings,
        "safety": {
            "production_data_modified": False,
            "remote_data_modified": False,
            "cutover_executed": False,
        },
    }


def write_json(report: dict[str, Any], output: Path | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if output is None:
        print(text)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text + "\n", encoding="utf-8")
    print(f"cutover gate report written: {output}")


def write_markdown(report: dict[str, Any], output: Path | None) -> None:
    if output is None:
        return
    lines = [
        "# Final Cutover Gate",
        "",
        f"- Phase: `{report.get('phase', 'final-cutover')}`",
        f"- Status: `{report.get('status')}`",
        f"- Production data modified: `{report.get('safety', {}).get('production_data_modified')}`",
        f"- Remote data modified: `{report.get('safety', {}).get('remote_data_modified')}`",
        f"- Cutover executed: `{report.get('safety', {}).get('cutover_executed')}`",
        "",
        "## Blockers",
        "",
    ]
    blockers = report.get("blockers", [])
    if blockers:
        for item in blockers:
            lines.append(f"- `{item['id']}` {item['message']} {item.get('details', '')}".rstrip())
    else:
        lines.append("- None")
    lines.extend(["", "## Warnings", ""])
    warnings = report.get("warnings", [])
    if warnings:
        for item in warnings:
            lines.append(f"- `{item['id']}` {item['message']} {item.get('details', '')}".rstrip())
    else:
        lines.append("- None")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"cutover gate markdown written: {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the final SQLite to PostgreSQL cutover gate report.")
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--target-root", type=Path, default=DEFAULT_TARGET_ROOT)
    parser.add_argument("--phase", choices=sorted(SUPPORTED_GATE_PHASES), default="final-cutover")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args(argv)

    report = build_cutover_gate_report(report_root=args.report_root, target_root=args.target_root, phase=args.phase)
    write_json(report, args.json_output)
    write_markdown(report, args.markdown_output)
    return 0 if report.get("status") == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
