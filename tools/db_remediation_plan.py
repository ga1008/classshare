from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TEMP_ROOT = REPO_ROOT / ".codex-temp"
DEFAULT_RUNTIME_ROOT = TEMP_ROOT / "db-remediation-plan"
DEFAULT_DATA_ROOT = REPO_ROOT / "data"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def resolve_runtime_root(raw: str | Path | None = None) -> Path:
    runtime_root = Path(raw) if raw else DEFAULT_RUNTIME_ROOT
    if not runtime_root.is_absolute():
        runtime_root = REPO_ROOT / runtime_root
    runtime_root = runtime_root.resolve()
    temp_root = TEMP_ROOT.resolve()
    if runtime_root != temp_root and not _is_relative_to(runtime_root, temp_root):
        raise ValueError(f"remediation runtime root must stay under {temp_root}; got {runtime_root}")
    return runtime_root


def source_db_path(raw: str | Path | None = None) -> Path:
    if raw:
        source = Path(raw)
        if not source.is_absolute():
            source = REPO_ROOT / source
        source = source.resolve()
        if not source.is_file():
            raise FileNotFoundError(f"source database not found: {source}")
        return source
    for candidate in (REPO_ROOT / "data" / "db" / "classroom.db", REPO_ROOT / "data" / "classroom.db"):
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("Cannot find data/db/classroom.db or data/classroom.db")


def _copy_sqlite_database(source_db: Path, target_db: Path) -> None:
    target_db.parent.mkdir(parents=True, exist_ok=True)
    if target_db.exists():
        target_db.unlink()
    source_uri = source_db.resolve().as_uri() + "?mode=ro"
    source_conn = sqlite3.connect(source_uri, uri=True)
    target_conn = sqlite3.connect(target_db)
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _fk_violations(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _orphan_teacher_onboarding_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(conn, "teacher_onboarding_state") or not _table_exists(conn, "teachers"):
        return []
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT tos.*
            FROM teacher_onboarding_state tos
            LEFT JOIN teachers t ON t.id = tos.teacher_id
            WHERE t.id IS NULL
            ORDER BY tos.teacher_id
            """
        ).fetchall()
    ]


def _apply_foreign_key_remediation(conn: sqlite3.Connection) -> dict[str, Any]:
    orphan_rows = _orphan_teacher_onboarding_rows(conn)
    deleted = 0
    if orphan_rows:
        teacher_ids = [int(row["teacher_id"]) for row in orphan_rows]
        placeholders = ",".join("?" for _ in teacher_ids)
        cursor = conn.execute(
            f"DELETE FROM teacher_onboarding_state WHERE teacher_id IN ({placeholders})",
            teacher_ids,
        )
        deleted = int(cursor.rowcount or 0)
    return {
        "operation": "delete_orphan_teacher_onboarding_state_rows",
        "reason": "teacher_onboarding_state is derived per-teacher UI progress; rows without a teachers parent cannot be used by the application.",
        "rows_before": orphan_rows,
        "rows_deleted": deleted,
        "sql_template": "DELETE FROM teacher_onboarding_state WHERE teacher_id IN (...missing teacher ids...)",
    }


def _submission_roots(data_root: Path, repo_root: Path) -> tuple[Path, ...]:
    return (
        data_root / "files" / "submissions",
        repo_root / "homework_submissions",
    )


def _path_key(path: Path) -> str:
    return str(path.resolve() if path.exists() else path.absolute()).replace("\\", "/").lower()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _candidate_by_hash_and_size(roots: Sequence[Path], *, file_hash: str, file_size: int) -> list[dict[str, Any]]:
    if not file_hash or file_size < 0:
        return []
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                if path.stat().st_size != file_size:
                    continue
                digest = _sha256_file(path)
            except OSError:
                continue
            if digest != file_hash:
                continue
            key = _path_key(path)
            if key in seen:
                continue
            seen.add(key)
            candidates.append({"path": str(path), "size_bytes": file_size, "sha256": digest})
    return candidates


def _missing_submission_files(conn: sqlite3.Connection, data_root: Path, repo_root: Path) -> list[dict[str, Any]]:
    if not _table_exists(conn, "submission_files"):
        return []
    from classroom_app.storage_paths import resolve_migrated_file_path

    rows = conn.execute(
        """
        SELECT sf.id, sf.submission_id, sf.original_filename, sf.relative_path, sf.stored_path,
               sf.file_hash, sf.file_size, s.assignment_id, s.student_pk_id
        FROM submission_files sf
        LEFT JOIN submissions s ON s.id = sf.submission_id
        WHERE COALESCE(TRIM(sf.stored_path), '') != ''
        ORDER BY sf.id
        """
    ).fetchall()
    roots = _submission_roots(data_root, repo_root)
    missing: list[dict[str, Any]] = []
    for row in rows:
        stored_path = str(row["stored_path"] or "")
        if resolve_migrated_file_path(
            stored_path,
            active_root=roots[0],
            legacy_roots=roots[1:],
            markers=("homework_submissions", "files/submissions"),
        ):
            continue
        candidates = _candidate_by_hash_and_size(
            roots,
            file_hash=str(row["file_hash"] or ""),
            file_size=int(row["file_size"] or -1),
        )
        expected_relative = "/".join(
            part
            for part in (
                str(row["assignment_id"] or ""),
                str(row["student_pk_id"] or ""),
                str(row["relative_path"] or row["original_filename"] or ""),
            )
            if part
        )
        missing.append(
            {
                "id": int(row["id"]),
                "submission_id": row["submission_id"],
                "assignment_id": row["assignment_id"],
                "student_pk_id": row["student_pk_id"],
                "original_filename": row["original_filename"],
                "relative_path": row["relative_path"],
                "stored_path": stored_path,
                "file_hash": row["file_hash"],
                "file_size": row["file_size"],
                "candidate_files": candidates,
                "recommended_action": "rewrite_to_candidate" if candidates else "restore_from_backup_or_explicitly_exempt",
                "expected_relative_hint": expected_relative,
            }
        )
    return missing


def build_remediation_plan(
    runtime_root: Path | str | None = None,
    *,
    source_db: Path | str | None = None,
    data_root: Path | str | None = None,
    repo_root: Path | str | None = None,
    apply_to_copy: bool = False,
) -> dict[str, Any]:
    runtime_root = resolve_runtime_root(runtime_root)
    if runtime_root.exists():
        shutil.rmtree(runtime_root)
    copied_db = runtime_root / "db" / "classroom.remediation.db"
    reports_dir = runtime_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    source = source_db_path(source_db)
    _copy_sqlite_database(source, copied_db)
    data_root_path = Path(data_root).resolve() if data_root else DEFAULT_DATA_ROOT.resolve()
    repo_root_path = Path(repo_root).resolve() if repo_root else REPO_ROOT.resolve()

    conn = _connect(copied_db)
    try:
        fk_before = _fk_violations(conn)
        orphan_rows = _orphan_teacher_onboarding_rows(conn)
        applied_operations: list[dict[str, Any]] = []
        planned_operations = [
            {
                "operation": "delete_orphan_teacher_onboarding_state_rows",
                "rows": orphan_rows,
                "row_count": len(orphan_rows),
                "apply_scope": "copied migration database only",
            }
        ]
        if apply_to_copy:
            applied_operations.append(_apply_foreign_key_remediation(conn))
            conn.commit()
        fk_after = _fk_violations(conn)
        missing_files = _missing_submission_files(conn, data_root_path, repo_root_path)
    finally:
        conn.close()

    remaining_manual_files = [item for item in missing_files if not item["candidate_files"]]
    return {
        "status": "ok",
        "generated_at": _now(),
        "runtime_root": str(runtime_root),
        "source_db": str(source),
        "copied_db": str(copied_db),
        "apply_to_copy": bool(apply_to_copy),
        "foreign_key": {
            "violations_before": fk_before,
            "violations_after": fk_after,
            "orphan_teacher_onboarding_rows": orphan_rows,
            "planned_operations": planned_operations,
            "applied_operations": applied_operations,
        },
        "files": {
            "missing_submission_files": missing_files,
            "missing_count": len(missing_files),
            "manual_restore_required_count": len(remaining_manual_files),
            "candidate_rewrite_count": len(missing_files) - len(remaining_manual_files),
        },
        "cutover_effect": {
            "foreign_key_blocker_cleared_on_copy": len(fk_after) == 0,
            "file_blocker_cleared": len(missing_files) == 0,
        },
        "safety": {
            "source_db_was_copied": True,
            "writes_limited_to_copied_db": bool(apply_to_copy),
            "production_data_modified": False,
            "remote_data_modified": False,
        },
    }


def write_json(report: dict[str, Any], output: Path | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if output is None:
        print(text)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text + "\n", encoding="utf-8")
    print(f"remediation report written: {output}")


def write_markdown(report: dict[str, Any], output: Path | None) -> None:
    if output is None:
        return
    lines = [
        "# Database Remediation Plan",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Apply to copy: `{report.get('apply_to_copy')}`",
        f"- Copied DB: `{report.get('copied_db')}`",
        f"- FK before: `{len(report.get('foreign_key', {}).get('violations_before', []))}`",
        f"- FK after: `{len(report.get('foreign_key', {}).get('violations_after', []))}`",
        f"- Missing submission files: `{report.get('files', {}).get('missing_count')}`",
        f"- Manual restore required: `{report.get('files', {}).get('manual_restore_required_count')}`",
        f"- Production data modified: `{report.get('safety', {}).get('production_data_modified')}`",
        "",
        "## Missing Files",
        "",
    ]
    for item in report.get("files", {}).get("missing_submission_files", []):
        lines.append(
            f"- `submission_files.id={item['id']}` `{item['original_filename']}`: {item['recommended_action']}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"remediation markdown written: {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build copied-database remediation plan for migration blockers.")
    parser.add_argument("--runtime-root", type=str)
    parser.add_argument("--source-db", type=str)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--apply-to-copy", action="store_true")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args(argv)

    try:
        report = build_remediation_plan(
            args.runtime_root,
            source_db=args.source_db,
            data_root=args.data_root,
            repo_root=args.repo_root,
            apply_to_copy=args.apply_to_copy,
        )
    except Exception as exc:
        report = {
            "status": "failed",
            "error": str(exc),
            "production_data_modified": False,
            "remote_data_modified": False,
        }
    write_json(report, args.json_output)
    write_markdown(report, args.markdown_output)
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
