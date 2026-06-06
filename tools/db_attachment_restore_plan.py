from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMP_ROOT = REPO_ROOT / ".codex-temp"
DEFAULT_RUNTIME_ROOT = TEMP_ROOT / "db-attachment-restore-plan"
DEFAULT_REMEDIATION_REPORT = TEMP_ROOT / "db-remediation-plan-current" / "reports" / "remediation-plan.json"
DEFAULT_SOURCE_DB = REPO_ROOT / "data" / "classroom.db"
DEFAULT_DATA_ROOT = REPO_ROOT / "data"
EXCEPTION_SCOPE = "sqlite-to-postgresql-cutover-missing-submission-files"
EXCEPTION_MANIFEST_VERSION = 1
REQUIRED_EXCEPTION_ACKNOWLEDGEMENTS = (
    "original_files_unavailable_after_search",
    "database_records_will_not_be_deleted_to_hide_missing_files",
    "historical_attachments_may_remain_unopenable_after_cutover",
    "cutover_can_continue_without_restoring_these_specific_files",
)


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
        raise ValueError(f"attachment restore runtime root must stay under {temp_root}; got {runtime_root}")
    return runtime_root


def _resolve_path(raw: str | Path | None, default: Path) -> Path:
    path = Path(raw) if raw else default
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


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


def _connect_readonly_copy(source_db: Path, runtime_root: Path) -> tuple[sqlite3.Connection, Path]:
    copied_db = runtime_root / "db" / "classroom.attachment-restore.db"
    _copy_sqlite_database(source_db, copied_db)
    conn = sqlite3.connect(copied_db)
    conn.row_factory = sqlite3.Row
    return conn, copied_db


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_key(path: Path) -> str:
    return str(path.resolve() if path.exists() else path.absolute()).replace("\\", "/").lower()


def _safe_parts(relative_path: str) -> tuple[str, ...]:
    parts = []
    for part in PurePosixPath(str(relative_path or "").replace("\\", "/")).parts:
        cleaned = str(part).strip("/\\")
        if not cleaned or cleaned in {".", ".."}:
            continue
        parts.append(cleaned)
    return tuple(parts)


def _canonical_target(
    *,
    data_root: Path,
    course_id: Any,
    assignment_id: Any,
    student_pk_id: Any,
    relative_path: str,
    original_filename: str,
) -> Path | None:
    if course_id in (None, "") or assignment_id in (None, "") or student_pk_id in (None, ""):
        return None
    parts = _safe_parts(relative_path or original_filename)
    if not parts:
        return None
    return data_root / "files" / "submissions" / str(course_id) / str(assignment_id) / str(student_pk_id) / Path(*parts)


def _scan_search_roots(
    roots: Sequence[Path],
    *,
    expected_name: str,
    expected_size: int,
    expected_hash: str,
) -> list[dict[str, Any]]:
    if expected_size < 0:
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
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size != expected_size and path.name != expected_name:
                continue
            try:
                digest = _sha256_file(path)
            except OSError:
                continue
            match = "sha256_and_size" if digest == expected_hash and stat.st_size == expected_size else "name_or_size_only"
            key = _path_key(path)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "path": str(path),
                    "size_bytes": stat.st_size,
                    "sha256": digest,
                    "match": match,
                    "trusted": match == "sha256_and_size",
                }
            )
    return candidates


def _enrich_missing_items(
    conn: sqlite3.Connection,
    missing_items: Sequence[dict[str, Any]],
    *,
    data_root: Path,
    search_roots: Sequence[Path],
) -> list[dict[str, Any]]:
    rows_by_file_id: dict[int, sqlite3.Row] = {}
    if missing_items:
        placeholders = ",".join("?" for _ in missing_items)
        ids = [int(item["id"]) for item in missing_items]
        for row in conn.execute(
            f"""
            SELECT sf.id, sf.submission_id, sf.original_filename, sf.relative_path,
                   sf.stored_path, sf.file_hash, sf.file_size,
                   s.assignment_id, s.student_pk_id,
                   a.course_id, a.title AS assignment_title,
                   c.name AS course_name
            FROM submission_files sf
            LEFT JOIN submissions s ON s.id = sf.submission_id
            LEFT JOIN assignments a ON a.id = s.assignment_id
            LEFT JOIN courses c ON c.id = a.course_id
            WHERE sf.id IN ({placeholders})
            ORDER BY sf.id
            """,
            ids,
        ):
            rows_by_file_id[int(row["id"])] = row

    enriched: list[dict[str, Any]] = []
    for raw_item in missing_items:
        item = dict(raw_item)
        row = rows_by_file_id.get(int(item["id"]))
        if row is not None:
            item.update({key: row[key] for key in row.keys()})
        target = _canonical_target(
            data_root=data_root,
            course_id=item.get("course_id"),
            assignment_id=item.get("assignment_id"),
            student_pk_id=item.get("student_pk_id"),
            relative_path=str(item.get("relative_path") or ""),
            original_filename=str(item.get("original_filename") or ""),
        )
        expected_hash = str(item.get("file_hash") or "").strip().lower()
        expected_size = int(item.get("file_size") or -1)
        already_restored = False
        target_hash = ""
        if target and target.is_file():
            try:
                target_hash = _sha256_file(target)
                already_restored = target.stat().st_size == expected_size and target_hash == expected_hash
            except OSError:
                already_restored = False
        candidates = _scan_search_roots(
            search_roots,
            expected_name=str(item.get("original_filename") or item.get("relative_path") or ""),
            expected_size=expected_size,
            expected_hash=expected_hash,
        )
        trusted_candidates = [candidate for candidate in candidates if candidate.get("trusted")]
        item.update(
            {
                "canonical_target_path": str(target) if target else "",
                "canonical_target_exists": bool(target and target.is_file()),
                "canonical_target_sha256": target_hash,
                "already_restored": already_restored,
                "candidate_files": candidates,
                "trusted_candidate_count": len(trusted_candidates),
                "recommended_action": (
                    "already_restored"
                    if already_restored
                    else "restore_trusted_candidate"
                    if trusted_candidates
                    else "restore_from_remote_or_backup_or_accept_exception"
                ),
            }
        )
        enriched.append(item)
    return enriched


def _normalize_id_list(values: Iterable[Any]) -> set[int]:
    ids: set[int] = set()
    for value in values:
        try:
            ids.add(int(value))
        except (TypeError, ValueError):
            continue
    return ids


def _validate_exception_manifest(path: Path | None, missing_ids: set[int]) -> dict[str, Any]:
    if path is None:
        return {"provided": False, "valid": False, "accepted_ids": [], "missing_acceptances": sorted(missing_ids)}
    try:
        manifest = _load_json(path)
    except Exception as exc:
        return {"provided": True, "valid": False, "error": str(exc), "path": str(path)}

    accepted_ids = _normalize_id_list(manifest.get("accepted_missing_submission_file_ids") or [])
    required_text_fields = ("approved_by", "approved_at", "reason", "business_acknowledgement")
    missing_fields = [field for field in required_text_fields if not str(manifest.get(field) or "").strip()]
    acknowledgement_values = manifest.get("required_acknowledgements") or {}
    missing_acknowledgements = [
        name
        for name in REQUIRED_EXCEPTION_ACKNOWLEDGEMENTS
        if acknowledgement_values.get(name) is not True
    ]
    scope_valid = str(manifest.get("scope") or "").strip() == EXCEPTION_SCOPE
    try:
        manifest_version = int(manifest.get("manifest_version") or 0)
    except (TypeError, ValueError):
        manifest_version = 0
    version_valid = manifest_version == EXCEPTION_MANIFEST_VERSION
    missing_acceptances = sorted(missing_ids - accepted_ids)
    unexpected_acceptances = sorted(accepted_ids - missing_ids)
    valid = (
        scope_valid
        and version_valid
        and not missing_fields
        and not missing_acknowledgements
        and not missing_acceptances
        and not unexpected_acceptances
        and bool(missing_ids)
    )
    return {
        "provided": True,
        "valid": valid,
        "path": str(path),
        "scope_valid": scope_valid,
        "manifest_version_valid": version_valid,
        "approved_by": manifest.get("approved_by", ""),
        "approved_at": manifest.get("approved_at", ""),
        "accepted_ids": sorted(accepted_ids),
        "missing_fields": missing_fields,
        "missing_acknowledgements": missing_acknowledgements,
        "missing_acceptances": missing_acceptances,
        "unexpected_acceptances": unexpected_acceptances,
    }


def _exception_template(missing_items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    item_summaries = []
    for item in missing_items:
        item_summaries.append(
            {
                "submission_file_id": int(item.get("id") or 0),
                "submission_id": item.get("submission_id"),
                "assignment_id": item.get("assignment_id"),
                "assignment_title": item.get("assignment_title"),
                "course_id": item.get("course_id"),
                "course_name": item.get("course_name"),
                "student_pk_id": item.get("student_pk_id"),
                "original_filename": item.get("original_filename"),
                "relative_path": item.get("relative_path"),
                "stored_path": item.get("stored_path"),
                "expected_sha256": item.get("file_hash"),
                "expected_file_size": item.get("file_size"),
                "canonical_target_path": item.get("canonical_target_path"),
                "canonical_target_exists": bool(item.get("canonical_target_exists")),
                "trusted_candidate_count": int(item.get("trusted_candidate_count") or 0),
                "recommended_action": item.get("recommended_action"),
            }
        )
    missing_ids = [int(item["submission_file_id"]) for item in item_summaries if int(item.get("submission_file_id") or 0)]
    return {
        "scope": EXCEPTION_SCOPE,
        "manifest_version": EXCEPTION_MANIFEST_VERSION,
        "approved_by": "",
        "approved_at": "",
        "reason": "",
        "business_acknowledgement": "",
        "required_acknowledgements": {
            "original_files_unavailable_after_search": False,
            "database_records_will_not_be_deleted_to_hide_missing_files": False,
            "historical_attachments_may_remain_unopenable_after_cutover": False,
            "cutover_can_continue_without_restoring_these_specific_files": False,
        },
        "accepted_missing_submission_file_ids": list(missing_ids),
        "missing_submission_files": item_summaries,
        "notes": (
            "Fill this file only after a business owner decides these historical attachments can remain unavailable "
            "after cutover. Do not use this manifest to hide newly missing files or to delete database records."
        ),
    }


def _remote_find_commands(items: Sequence[dict[str, Any]]) -> list[str]:
    commands: list[str] = []
    seen: set[tuple[str, int, str]] = set()
    for item in items:
        filename = str(item.get("original_filename") or item.get("relative_path") or "")
        size = int(item.get("file_size") or -1)
        digest = str(item.get("file_hash") or "").strip().lower()
        key = (filename, size, digest)
        if key in seen or size <= 0:
            continue
        seen.add(key)
        escaped_filename = filename.replace("'", "'\"'\"'")
        commands.append(
            "find /lanshare -path /lanshare/data/postgres -prune -o "
            f"-type f \\( -name '*{escaped_filename}*' -o -size {size}c \\) "
            "-print 2>/dev/null | while IFS= read -r file; do "
            f"sha256sum \"$file\" | grep -i '^{digest} ' && stat -c '%n\\t%s' \"$file\"; done"
        )
    return commands


def build_attachment_restore_plan(
    runtime_root: Path | str | None = None,
    *,
    remediation_report: Path | str | None = None,
    source_db: Path | str | None = None,
    data_root: Path | str | None = None,
    search_roots: Sequence[Path | str] = (),
    exception_manifest: Path | str | None = None,
) -> dict[str, Any]:
    runtime = resolve_runtime_root(runtime_root)
    if runtime.exists():
        shutil.rmtree(runtime)
    reports_dir = runtime / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    remediation_path = _resolve_path(remediation_report, DEFAULT_REMEDIATION_REPORT)
    remediation = _load_json(remediation_path)
    missing_items = remediation.get("files", {}).get("missing_submission_files") or []
    source = _resolve_path(source_db or remediation.get("source_db"), DEFAULT_SOURCE_DB)
    data = _resolve_path(data_root, DEFAULT_DATA_ROOT)
    roots = [_resolve_path(root, Path(root)) for root in search_roots]
    default_roots = [data / "files" / "submissions", REPO_ROOT / "homework_submissions"]
    effective_roots = []
    seen_roots: set[str] = set()
    for root in [*default_roots, *roots]:
        key = _path_key(root)
        if key not in seen_roots:
            seen_roots.add(key)
            effective_roots.append(root)

    conn, copied_db = _connect_readonly_copy(source, runtime)
    try:
        enriched = _enrich_missing_items(conn, missing_items, data_root=data, search_roots=effective_roots)
    finally:
        conn.close()

    missing_ids = {int(item["id"]) for item in enriched}
    exception_path = _resolve_path(exception_manifest, Path(exception_manifest)) if exception_manifest else None
    exception = _validate_exception_manifest(exception_path, missing_ids)
    all_already_restored = bool(enriched) and all(bool(item.get("already_restored")) for item in enriched)
    exception_valid = bool(exception.get("valid"))
    unresolved = [item for item in enriched if not item.get("already_restored")]
    file_blocker_cleared = all_already_restored or exception_valid or not enriched
    template_path = reports_dir / "missing-attachment-exception-template.json"
    template_path.write_text(json.dumps(_exception_template(enriched), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "status": "ok" if file_blocker_cleared else "blocked",
        "generated_at": _now(),
        "runtime_root": str(runtime),
        "remediation_report": str(remediation_path),
        "source_db": str(source),
        "copied_db": str(copied_db),
        "data_root": str(data),
        "search_roots": [str(root) for root in effective_roots],
        "missing_count": len(enriched),
        "already_restored_count": len(enriched) - len(unresolved),
        "unresolved_count": len(unresolved),
        "items": enriched,
        "exception_manifest": exception,
        "exception_template": str(template_path),
        "remote_find_commands": _remote_find_commands(enriched),
        "cutover_effect": {
            "file_blocker_cleared": file_blocker_cleared,
            "all_missing_files_restored": all_already_restored or not enriched,
            "accepted_exception_manifest_valid": exception_valid,
        },
        "safety": {
            "source_db_was_copied": True,
            "production_data_modified": False,
            "filesystem_modified": False,
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
    print(f"attachment restore plan written: {output}")


def write_markdown(report: dict[str, Any], output: Path | None) -> None:
    if output is None:
        return
    lines = [
        "# Attachment Restore Plan",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Missing count: `{report.get('missing_count')}`",
        f"- Already restored count: `{report.get('already_restored_count')}`",
        f"- Unresolved count: `{report.get('unresolved_count')}`",
        f"- Exception manifest valid: `{report.get('exception_manifest', {}).get('valid')}`",
        f"- Production data modified: `{report.get('safety', {}).get('production_data_modified')}`",
        f"- Filesystem modified: `{report.get('safety', {}).get('filesystem_modified')}`",
        "",
        "## Items",
        "",
    ]
    for item in report.get("items", []):
        lines.append(
            f"- `submission_files.id={item.get('id')}` `{item.get('original_filename')}` "
            f"-> `{item.get('recommended_action')}` target=`{item.get('canonical_target_path')}`"
        )
    lines.extend(["", "## Remote Find Commands", ""])
    commands = report.get("remote_find_commands", [])
    if commands:
        lines.append("```bash")
        lines.extend(commands)
        lines.append("```")
    else:
        lines.append("- None")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"attachment restore markdown written: {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a read-only restore or exception plan for missing submission files.")
    parser.add_argument("--runtime-root", type=str)
    parser.add_argument("--remediation-report", type=Path)
    parser.add_argument("--source-db", type=Path)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--search-root", type=Path, action="append", default=[])
    parser.add_argument("--exception-manifest", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args(argv)

    try:
        report = build_attachment_restore_plan(
            args.runtime_root,
            remediation_report=args.remediation_report,
            source_db=args.source_db,
            data_root=args.data_root,
            search_roots=args.search_root,
            exception_manifest=args.exception_manifest,
        )
    except Exception as exc:
        report = {
            "status": "failed",
            "error": str(exc),
            "production_data_modified": False,
            "filesystem_modified": False,
            "remote_data_modified": False,
        }
    write_json(report, args.json_output)
    write_markdown(report, args.markdown_output)
    return 0 if report.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
