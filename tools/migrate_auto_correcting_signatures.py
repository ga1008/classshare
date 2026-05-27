from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from classroom_app.config import SIGNATURES_DIR
from classroom_app.database import get_db_connection, init_database
from classroom_app.services.organization_scope_service import build_org_scope, load_teacher_org_scope
from classroom_app.services.signature_service import signature_relative_path, signature_write_path


DEFAULT_SOURCE_ROOT = Path(r"C:\Users\AngelWei\Nutstore\1\Projects\autoCorrecting")
DEFAULT_SOURCE_DB = DEFAULT_SOURCE_ROOT / "data" / "grading_system_v3.db"
DEFAULT_SOURCE_SIGNATURES_DIR = DEFAULT_SOURCE_ROOT / "uploads" / "signatures"
LEGACY_SOURCE_NAME = "autoCorrecting"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_source_file(row: sqlite3.Row, source_signatures_dir: Path) -> Path | None:
    stored = Path(str(row["file_path"] or ""))
    if stored.is_file():
        return stored
    basename = Path(str(row["file_path"] or "").replace("\\", "/")).name
    if basename:
        candidate = source_signatures_dir / basename
        if candidate.is_file():
            return candidate
    for candidate in source_signatures_dir.glob(f"{row['file_hash']}.*"):
        if candidate.is_file():
            return candidate
    return None


def _normalize_ext(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".jpeg":
        return ".jpg"
    if ext not in {".png", ".jpg"}:
        return ".png"
    return ext


def _mime_for_ext(ext: str) -> str:
    return "image/jpeg" if ext == ".jpg" else "image/png"


def _load_super_admin_scope(conn: sqlite3.Connection) -> tuple[int | None, dict[str, str], str]:
    row = conn.execute(
        """
        SELECT id, name
        FROM teachers
        WHERE COALESCE(is_super_admin, 0) = 1
        ORDER BY id
        LIMIT 1
        """
    ).fetchone()
    if row:
        return int(row["id"]), load_teacher_org_scope(conn, int(row["id"])), str(row["name"] or "平台导入")
    fallback = conn.execute("SELECT id, name, school_code, school_name, college, department FROM teachers ORDER BY id LIMIT 1").fetchone()
    if fallback:
        return (
            int(fallback["id"]),
            build_org_scope(
                school_code=fallback["school_code"],
                school_name=fallback["school_name"],
                college=fallback["college"],
                department=fallback["department"],
            ),
            str(fallback["name"] or "平台导入"),
        )
    return None, build_org_scope(), "平台导入"


def _identity_maps(conn: sqlite3.Connection) -> tuple[dict[str, sqlite3.Row], dict[str, sqlite3.Row]]:
    teachers = {
        str(row["name"] or "").strip(): row
        for row in conn.execute(
            """
            SELECT id, name, school_code, school_name, college, department
            FROM teachers
            WHERE COALESCE(is_active, 1) = 1
            """
        )
        if str(row["name"] or "").strip()
    }
    students: dict[str, sqlite3.Row] = {}
    duplicate_student_names: set[str] = set()
    for row in conn.execute(
        """
        SELECT id, name, school_code, school_name, college, department
        FROM students
        WHERE COALESCE(enrollment_status, 'active') = 'active'
        """
    ):
        name = str(row["name"] or "").strip()
        if not name:
            continue
        if name in students:
            duplicate_student_names.add(name)
            continue
        students[name] = row
    for name in duplicate_student_names:
        students.pop(name, None)
    return teachers, students


def _owner_for_signature(
    row: sqlite3.Row,
    *,
    teachers: dict[str, sqlite3.Row],
    students: dict[str, sqlite3.Row],
    super_scope: dict[str, str],
) -> dict[str, Any]:
    name = str(row["name"] or "").strip()
    if name in teachers:
        teacher = teachers[name]
        scope = build_org_scope(
            school_code=teacher["school_code"],
            school_name=teacher["school_name"],
            college=teacher["college"],
            department=teacher["department"],
        )
        return {
            "owner_role": "teacher",
            "owner_id": int(teacher["id"]),
            "owner_name_snapshot": name,
            "subject_role": "teacher",
            "subject_name": name,
            "scope_level": "college",
            "scope": scope,
            "matched": "teacher",
        }
    if name in students:
        student = students[name]
        scope = build_org_scope(
            school_code=student["school_code"],
            school_name=student["school_name"],
            college=student["college"],
            department=student["department"],
        )
        return {
            "owner_role": "student",
            "owner_id": int(student["id"]),
            "owner_name_snapshot": name,
            "subject_role": "student",
            "subject_name": name,
            "scope_level": "personal",
            "scope": scope,
            "matched": "student",
        }
    return {
        "owner_role": "system",
        "owner_id": None,
        "owner_name_snapshot": "autoCorrecting 导入",
        "subject_role": "teacher",
        "subject_name": name,
        "scope_level": "college",
        "scope": super_scope,
        "matched": "system",
    }


def migrate_signatures(source_db: Path, source_signatures_dir: Path, *, dry_run: bool = False) -> dict[str, int]:
    if not source_db.is_file():
        raise FileNotFoundError(f"source database not found: {source_db}")
    if not source_signatures_dir.is_dir():
        raise FileNotFoundError(f"source signatures folder not found: {source_signatures_dir}")

    init_database()
    source_conn = _connect(source_db)
    summary = {
        "source_rows": 0,
        "inserted": 0,
        "skipped_existing": 0,
        "missing_files": 0,
        "matched_teacher": 0,
        "matched_student": 0,
        "matched_system": 0,
    }
    with get_db_connection() as target_conn:
        super_teacher_id, super_scope, super_name = _load_super_admin_scope(target_conn)
        teachers, students = _identity_maps(target_conn)
        rows = source_conn.execute(
            """
            SELECT s.*, u.username AS uploader_name
            FROM signatures s
            LEFT JOIN users u ON u.id = s.uploaded_by
            ORDER BY s.id
            """
        ).fetchall()
        summary["source_rows"] = len(rows)
        for row in rows:
            legacy_id = str(row["id"])
            existing = target_conn.execute(
                """
                SELECT id
                FROM electronic_signatures
                WHERE legacy_source = ?
                  AND legacy_id = ?
                LIMIT 1
                """,
                (LEGACY_SOURCE_NAME, legacy_id),
            ).fetchone()
            if existing:
                summary["skipped_existing"] += 1
                continue
            source_file = _resolve_source_file(row, source_signatures_dir)
            if not source_file:
                summary["missing_files"] += 1
                continue
            ext = _normalize_ext(source_file)
            file_hash = _file_sha256(source_file)
            target_file = signature_write_path(file_hash, ext)
            relative_path = signature_relative_path(file_hash, ext)
            owner = _owner_for_signature(row, teachers=teachers, students=students, super_scope=super_scope)
            summary[f"matched_{owner['matched']}"] += 1
            metadata = {
                "source_db": str(source_db),
                "source_file_path": str(row["file_path"] or ""),
                "source_file_resolved": str(source_file),
                "source_uploaded_by": row["uploaded_by"],
                "source_uploader_name": row["uploader_name"] or "",
                "source_created_at": row["created_at"] or "",
                "source_file_hash": row["file_hash"] or "",
                "matched_owner": owner["matched"],
                "imported_by_teacher_id": super_teacher_id,
                "imported_by_teacher_name": super_name,
            }
            if dry_run:
                continue
            target_file.parent.mkdir(parents=True, exist_ok=True)
            if not target_file.is_file():
                shutil.copy2(source_file, target_file)
            target_conn.execute(
                """
                INSERT INTO electronic_signatures (
                    name, subject_name, subject_role, scope_level,
                    owner_role, owner_id, owner_name_snapshot,
                    school_code, school_name, college, department,
                    file_hash, file_ext, mime_type, stored_path, file_size,
                    description, legacy_source, legacy_id, metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    str(row["name"] or "").strip() or f"导入签名 {legacy_id}",
                    owner["subject_name"],
                    owner["subject_role"],
                    owner["scope_level"],
                    owner["owner_role"],
                    owner["owner_id"],
                    owner["owner_name_snapshot"],
                    owner["scope"]["school_code"],
                    owner["scope"]["school_name"],
                    owner["scope"]["college"],
                    owner["scope"]["department"],
                    file_hash,
                    ext,
                    _mime_for_ext(ext),
                    str(relative_path).replace("\\", "/"),
                    int(target_file.stat().st_size),
                    "从 autoCorrecting 签名库迁入",
                    LEGACY_SOURCE_NAME,
                    legacy_id,
                    json.dumps(metadata, ensure_ascii=False),
                    row["created_at"] or None,
                ),
            )
            summary["inserted"] += 1
        if not dry_run:
            target_conn.commit()
    source_conn.close()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate electronic signatures from autoCorrecting into LanShare.")
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument("--source-signatures-dir", type=Path, default=DEFAULT_SOURCE_SIGNATURES_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    summary = migrate_signatures(args.source_db, args.source_signatures_dir, dry_run=args.dry_run)
    print(f"LanShare signatures folder: {SIGNATURES_DIR}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
