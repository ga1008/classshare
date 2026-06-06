from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from classroom_app.storage_paths import relative_path_variants
from tools import db_inventory


DEFAULT_RUNTIME_ROOT = db_inventory.TEMP_ROOT / "db-file-integrity"
SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")
PATH_TRAVERSAL_RE = re.compile(r"(^|[\\/])\.\.([\\/]|$)")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _path_key(path: Path) -> str:
    return str(path.resolve() if path.exists() else path.absolute()).replace("\\", "/").lower()


def _unique_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = _path_key(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return tuple(result)


def _root_manifest(*, data_root: Path, repo_root: Path) -> dict[str, tuple[Path, ...]]:
    return {
        "submissions": _unique_paths(
            (
                data_root / "files" / "submissions",
                repo_root / "homework_submissions",
            )
        ),
        "global_files": _unique_paths(
            (
                data_root / "media" / "blobs" / "sha256",
                repo_root / "storage" / "global_files",
            )
        ),
        "signatures": _unique_paths(
            (
                data_root / "media" / "signatures" / "sha256",
                repo_root / "storage" / "signatures",
            )
        ),
        "textbook_attachments": _unique_paths(
            (
                data_root / "files" / "textbook_attachments",
                repo_root / "storage" / "textbook_attachments",
            )
        ),
        "chunked_uploads": _unique_paths(
            (
                data_root / "tmp" / "chunked_uploads",
                repo_root / "storage" / "chunked_uploads",
            )
        ),
    }


def _is_under_any_root(path: Path, roots: Sequence[Path]) -> bool:
    try:
        resolved = path.resolve() if path.exists() else path.absolute()
    except OSError:
        resolved = path.absolute()
    for root in roots:
        try:
            root_resolved = root.resolve() if root.exists() else root.absolute()
            if resolved == root_resolved or root_resolved in resolved.parents:
                return True
        except OSError:
            continue
    return False


def _extract_relative_after_markers(stored_path: str, markers: Sequence[str]) -> str | None:
    normalized = str(stored_path or "").replace("\\", "/").strip()
    if not normalized:
        return None
    for marker in sorted({item.strip("/") for item in markers if item.strip("/")}, key=len, reverse=True):
        for token in (f"/{marker}/", f"{marker}/"):
            index = normalized.rfind(token)
            if index < 0:
                continue
            relative_path = normalized[index + len(token) :].strip("/")
            return relative_path or None
    return None


def _resolve_stored_path(
    stored_path: str,
    *,
    roots: Sequence[Path],
    markers: Sequence[str],
) -> Path | None:
    if not stored_path:
        return None
    direct_path = Path(stored_path)
    if direct_path.is_file():
        return direct_path
    relative_path = _extract_relative_after_markers(stored_path, markers)
    if relative_path:
        for variant in relative_path_variants(relative_path):
            for root in roots:
                candidate = root.joinpath(*PurePosixPath(variant).parts)
                if candidate.is_file():
                    return candidate
    if not direct_path.is_absolute():
        normalized_relative = str(stored_path).replace("\\", "/").strip("/")
        if normalized_relative:
            for variant in relative_path_variants(normalized_relative):
                for root in roots:
                    candidate = root.joinpath(*PurePosixPath(variant).parts)
                    if candidate.is_file():
                        return candidate
    return None


def _sharded_hash_path(root: Path, file_hash: str, suffix: str = "") -> Path:
    normalized = file_hash.lower()
    filename = f"{normalized}{suffix}"
    if len(normalized) >= 4:
        return root / normalized[:2] / normalized[2:4] / filename
    return root / filename


def _global_hash_candidates(file_hash: str, roots: Sequence[Path]) -> tuple[Path, ...]:
    normalized = str(file_hash or "").strip().lower()
    if not SHA256_RE.fullmatch(normalized):
        return ()
    candidates: list[Path] = []
    for root in roots:
        candidates.append(_sharded_hash_path(root, normalized))
        candidates.append(root / normalized)
    return _unique_paths(candidates)


def _signature_candidates(row: dict[str, Any], roots: Sequence[Path]) -> tuple[Path, ...]:
    candidates: list[Path] = []
    stored_path = str(row.get("stored_path") or "").strip()
    if stored_path:
        direct = Path(stored_path)
        candidates.append(direct)
        if not direct.is_absolute():
            normalized = stored_path.replace("\\", "/").strip("/")
            candidates.extend(root.joinpath(*PurePosixPath(normalized).parts) for root in roots)
    file_hash = str(row.get("file_hash") or "").strip().lower()
    file_ext = str(row.get("file_ext") or "").strip().lower()
    if file_hash and file_ext:
        suffix = file_ext if file_ext.startswith(".") else f".{file_ext}"
        for root in roots:
            candidates.append(_sharded_hash_path(root, file_hash, suffix))
            candidates.append(root / f"{file_hash}{suffix}")
    return _unique_paths(candidates)


def _resolve_first(candidates: Sequence[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    )


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row["name"]) for row in conn.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()]


def _file_hash_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    columns = _columns(conn, table)
    return [
        column
        for column in columns
        if column == "file_hash"
        or column.endswith("_file_hash")
        or column in {"avatar_file_hash", "cover_file_hash", "source_file_hash", "thumbnail_file_hash", "preview_file_hash"}
    ]


def _scan_file_field_inventory(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    path_markers = ("path", "filename", "file_name", "file_hash", "stored_path", "attachment")
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for row in rows:
        table = str(row["name"])
        matched = [
            column
            for column in _columns(conn, table)
            if any(marker in column.lower() for marker in path_markers)
        ]
        if matched:
            count = int(conn.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table)}").fetchone()[0])
            inventory.append({"table": table, "rows": count, "fields": matched})
    return inventory


def _add_reference(
    references: list[dict[str, Any]],
    *,
    table: str,
    row_id: Any,
    field: str,
    stored_value: str,
    required: bool,
    resolved_path: Path | None,
    candidates: Sequence[Path],
    roots: Sequence[Path],
    kind: str,
) -> None:
    value = str(stored_value or "").strip()
    unsafe = bool(PATH_TRAVERSAL_RE.search(value.replace("\\", "/")))
    outside_known_roots = bool(resolved_path and roots and not _is_under_any_root(resolved_path, roots))
    references.append(
        {
            "table": table,
            "row_id": row_id,
            "field": field,
            "kind": kind,
            "stored_value": value,
            "required": required,
            "resolved": bool(resolved_path),
            "resolved_path": str(resolved_path) if resolved_path else "",
            "candidate_count": len(candidates),
            "path_traversal_risk": unsafe,
            "outside_known_roots": outside_known_roots,
        }
    )


def _submission_references(conn: sqlite3.Connection, roots: Sequence[Path]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for table in ("submission_files", "submission_draft_files"):
        if not _table_exists(conn, table):
            continue
        for row in conn.execute(
            f"""
            SELECT id, stored_path, relative_path, original_filename
            FROM {_quote_identifier(table)}
            WHERE COALESCE(TRIM(stored_path), '') != ''
            ORDER BY id
            """
        ):
            stored_path = str(row["stored_path"] or "")
            resolved = _resolve_stored_path(
                stored_path,
                roots=roots,
                markers=("homework_submissions", "files/submissions"),
            )
            _add_reference(
                references,
                table=table,
                row_id=row["id"],
                field="stored_path",
                stored_value=stored_path,
                required=True,
                resolved_path=resolved,
                candidates=(resolved,) if resolved else (),
                roots=roots,
                kind="submission_path",
            )
    return references


def _signature_references(conn: sqlite3.Connection, roots: Sequence[Path]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    if not _table_exists(conn, "electronic_signatures"):
        return references
    for row in conn.execute(
        """
        SELECT id, file_hash, file_ext, stored_path
        FROM electronic_signatures
        WHERE COALESCE(TRIM(file_hash), '') != ''
           OR COALESCE(TRIM(stored_path), '') != ''
        ORDER BY id
        """
    ):
        row_dict = dict(row)
        candidates = _signature_candidates(row_dict, roots)
        _add_reference(
            references,
            table="electronic_signatures",
            row_id=row["id"],
            field="stored_path/file_hash",
            stored_value=str(row["stored_path"] or row["file_hash"] or ""),
            required=True,
            resolved_path=_resolve_first(candidates),
            candidates=candidates,
            roots=roots,
            kind="signature",
        )
    return references


def _textbook_references(conn: sqlite3.Connection, roots: Sequence[Path]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    if not _table_exists(conn, "textbooks"):
        return references
    for row in conn.execute(
        """
        SELECT id, attachment_path
        FROM textbooks
        WHERE COALESCE(TRIM(attachment_path), '') != ''
        ORDER BY id
        """
    ):
        stored_path = str(row["attachment_path"] or "")
        resolved = _resolve_stored_path(
            stored_path,
            roots=roots,
            markers=("textbook_attachments", "files/textbook_attachments"),
        )
        _add_reference(
            references,
            table="textbooks",
            row_id=row["id"],
            field="attachment_path",
            stored_value=stored_path,
            required=True,
            resolved_path=resolved,
            candidates=(resolved,) if resolved else (),
            roots=roots,
            kind="textbook_attachment",
        )
    return references


def _global_hash_references(conn: sqlite3.Connection, roots: Sequence[Path]) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    table_specific_hash_tables = {"electronic_signatures", "submission_files", "submission_draft_files"}
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for table_row in rows:
        table = str(table_row["name"])
        if table in table_specific_hash_tables:
            continue
        columns = _file_hash_columns(conn, table)
        if not columns:
            continue
        table_columns = _columns(conn, table)
        id_column = "id" if "id" in table_columns else None
        select_columns = [id_column] if id_column else []
        select_columns.extend(columns)
        select_sql = ", ".join(_quote_identifier(column) for column in select_columns)
        where_sql = " OR ".join(f"COALESCE(TRIM({_quote_identifier(column)}), '') != ''" for column in columns)
        for row in conn.execute(f"SELECT {select_sql} FROM {_quote_identifier(table)} WHERE {where_sql}"):
            row_id = row[id_column] if id_column else ""
            for column in columns:
                file_hash = str(row[column] or "").strip().lower()
                if not file_hash:
                    continue
                candidates = _global_hash_candidates(file_hash, roots)
                _add_reference(
                    references,
                    table=table,
                    row_id=row_id,
                    field=column,
                    stored_value=file_hash,
                    required=True,
                    resolved_path=_resolve_first(candidates),
                    candidates=candidates,
                    roots=roots,
                    kind="global_hash",
                )
    return references


def _scan_orphans(
    roots_by_kind: dict[str, tuple[Path, ...]],
    referenced_paths: set[str],
    *,
    max_samples: int = 80,
) -> dict[str, Any]:
    by_kind: dict[str, Any] = {}
    for kind, roots in roots_by_kind.items():
        total_files = 0
        orphan_count = 0
        samples: list[str] = []
        for root in roots:
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                total_files += 1
                if _path_key(path) in referenced_paths:
                    continue
                orphan_count += 1
                if len(samples) < max_samples:
                    samples.append(str(path))
        by_kind[kind] = {
            "roots": [str(root) for root in roots],
            "total_files": total_files,
            "orphan_files": orphan_count,
            "orphan_samples": samples,
        }
    return by_kind


def build_file_integrity_report(
    runtime_root: Path,
    source_db: Path,
    *,
    data_root: Path | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    runtime_root = db_inventory.resolve_runtime_root(str(runtime_root))
    copied_db = db_inventory.copy_sqlite_database(runtime_root, source_db)
    active_data_root = (data_root or (REPO_ROOT / "data")).resolve()
    active_repo_root = (repo_root or REPO_ROOT).resolve()
    roots = _root_manifest(data_root=active_data_root, repo_root=active_repo_root)
    known_roots = _unique_paths(root for roots_for_kind in roots.values() for root in roots_for_kind)

    conn = sqlite3.connect(copied_db)
    try:
        conn.row_factory = sqlite3.Row
        inventory = _scan_file_field_inventory(conn)
        references: list[dict[str, Any]] = []
        references.extend(_submission_references(conn, roots["submissions"]))
        references.extend(_signature_references(conn, roots["signatures"]))
        references.extend(_textbook_references(conn, roots["textbook_attachments"]))
        references.extend(_global_hash_references(conn, roots["global_files"]))
    finally:
        conn.close()

    referenced_paths = {
        _path_key(Path(item["resolved_path"]))
        for item in references
        if item.get("resolved_path")
    }
    duplicate_refs: dict[str, int] = {}
    for item in references:
        path = item.get("resolved_path")
        if not path:
            continue
        key = _path_key(Path(path))
        duplicate_refs[key] = duplicate_refs.get(key, 0) + 1
    duplicate_samples = [
        {"path": path, "reference_count": count}
        for path, count in sorted(duplicate_refs.items(), key=lambda pair: pair[1], reverse=True)
        if count > 1
    ][:80]
    missing = [item for item in references if item["required"] and not item["resolved"]]
    traversal = [item for item in references if item["path_traversal_risk"]]
    outside_roots = [item for item in references if item["outside_known_roots"]]

    return {
        "status": "ok",
        "generated_at": _now(),
        "source_db": str(source_db),
        "runtime_root": str(runtime_root),
        "copied_db": str(copied_db),
        "data_root": str(active_data_root),
        "repo_root": str(active_repo_root),
        "safety": {
            "runtime_root_under_codex_temp": True,
            "source_db_was_copied_with_sqlite_backup_api": True,
            "production_data_modified": False,
            "filesystem_modified": False,
        },
        "roots": {kind: [str(root) for root in paths] for kind, paths in roots.items()},
        "known_roots": [str(root) for root in known_roots],
        "file_field_inventory": inventory,
        "references_checked": len(references),
        "resolved_references": sum(1 for item in references if item["resolved"]),
        "missing_references": len(missing),
        "path_traversal_risks": len(traversal),
        "outside_known_root_references": len(outside_roots),
        "duplicate_resolved_paths": len(duplicate_samples),
        "missing_reference_samples": missing[:80],
        "path_traversal_samples": traversal[:80],
        "outside_known_root_samples": outside_roots[:80],
        "duplicate_path_samples": duplicate_samples,
        "orphan_files": _scan_orphans(
            {
                "submissions": roots["submissions"],
                "global_files": roots["global_files"],
                "signatures": roots["signatures"],
                "textbook_attachments": roots["textbook_attachments"],
            },
            referenced_paths,
        ),
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# LanShare File Metadata Integrity Report",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Source DB: `{report['source_db']}`",
        f"- Copied DB: `{report['copied_db']}`",
        f"- Data root: `{report['data_root']}`",
        f"- Production data modified: `{report['safety']['production_data_modified']}`",
        f"- Filesystem modified: `{report['safety']['filesystem_modified']}`",
        f"- References checked: `{report['references_checked']}`",
        f"- Resolved references: `{report['resolved_references']}`",
        f"- Missing references: `{report['missing_references']}`",
        f"- Path traversal risks: `{report['path_traversal_risks']}`",
        f"- Outside-known-root references: `{report['outside_known_root_references']}`",
        f"- Duplicate resolved paths: `{report['duplicate_resolved_paths']}`",
        "",
        "## File Field Inventory",
        "",
        "| Table | Rows | Fields |",
        "| --- | ---: | --- |",
    ]
    for item in report["file_field_inventory"]:
        lines.append(f"| `{item['table']}` | {item['rows']} | {', '.join(f'`{field}`' for field in item['fields'])} |")
    lines.extend(["", "## Root Scan And Orphan Samples", "", "| Kind | Total Files | Orphan Files | Roots |", "| --- | ---: | ---: | --- |"])
    for kind, item in report["orphan_files"].items():
        roots = "<br>".join(f"`{root}`" for root in item["roots"])
        lines.append(f"| {kind} | {item['total_files']} | {item['orphan_files']} | {roots} |")
    lines.extend(["", "## Missing Reference Samples", "", "| Table | Row | Field | Kind | Stored Value |", "| --- | --- | --- | --- | --- |"])
    for item in report["missing_reference_samples"][:80]:
        stored = str(item["stored_value"]).replace("|", "\\|")[:160]
        lines.append(f"| `{item['table']}` | `{item['row_id']}` | `{item['field']}` | {item['kind']} | `{stored}` |")
    lines.extend(["", "## Duplicate Resolved Path Samples", "", "| Path | Reference Count |", "| --- | ---: |"])
    for item in report["duplicate_path_samples"][:80]:
        lines.append(f"| `{item['path']}` | {item['reference_count']} |")
    return "\n".join(lines) + "\n"


def _write_json(report: dict[str, Any], output: Path | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if output is None:
        print(text)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text + "\n", encoding="utf-8")


def _write_markdown(report: dict[str, Any], output: Path | None) -> None:
    if output is None:
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown_report(report), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a read-only LanShare file metadata integrity report.")
    parser.add_argument("--runtime-root", type=str)
    parser.add_argument("--source-db", type=str)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args(argv)

    try:
        report = build_file_integrity_report(
            db_inventory.resolve_runtime_root(args.runtime_root or str(DEFAULT_RUNTIME_ROOT)),
            db_inventory.source_db_path(args.source_db),
            data_root=args.data_root,
            repo_root=args.repo_root,
        )
    except Exception as exc:
        report = {
            "status": "failed",
            "generated_at": _now(),
            "error": str(exc),
            "runtime_root": str(args.runtime_root or DEFAULT_RUNTIME_ROOT),
            "source_db": str(args.source_db or ""),
            "production_data_modified": False,
            "filesystem_modified": False,
        }

    _write_json(report, args.json_output)
    if report.get("status") == "ok":
        _write_markdown(report, args.markdown_output)
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
