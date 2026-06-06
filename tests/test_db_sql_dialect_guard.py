from pathlib import Path


SQLITE_ONLY_PATTERNS = {
    "INSERT_OR_IGNORE": "INSERT OR IGNORE",
    "INSERT_OR_REPLACE": "INSERT OR REPLACE",
    "SQLITE_MASTER": "sqlite_master",
    "PRAGMA": "PRAGMA",
    "CREATE_TABLE": "CREATE TABLE IF NOT EXISTS",
    "ALTER_TABLE": "ALTER TABLE",
}

SQLITE_ONLY_RUNTIME_SQL_ALLOWLIST = {
    "classroom_app/routers/materials_parts/final_material_helpers.py": {"INSERT_OR_IGNORE"},
    "classroom_app/services/academic_course_exam_sync_service.py": {"CREATE_TABLE"},
    "classroom_app/services/academic_course_sync_service.py": {"INSERT_OR_IGNORE"},
    "classroom_app/services/background_task_ledger_service.py": {"SQLITE_MASTER", "PRAGMA"},
    "classroom_app/services/base_resource_modes_service.py": {"SQLITE_MASTER"},
    "classroom_app/services/blog_news_crawler_service.py": {"INSERT_OR_IGNORE"},
    "classroom_app/services/chat_handler.py": {"INSERT_OR_REPLACE", "CREATE_TABLE", "ALTER_TABLE"},
    "classroom_app/services/discussion_attachment_service.py": {"CREATE_TABLE", "PRAGMA", "ALTER_TABLE"},
    "classroom_app/services/email_notification_service.py": {"INSERT_OR_IGNORE"},
    "classroom_app/services/materials_service.py": {"INSERT_OR_IGNORE"},
    "classroom_app/services/message_center_service.py": {"CREATE_TABLE", "PRAGMA", "ALTER_TABLE"},
    "classroom_app/services/organization_management_service.py": {"SQLITE_MASTER"},
    "classroom_app/services/organization_scope_service.py": {"SQLITE_MASTER"},
    "classroom_app/services/wrong_question_summary_service.py": {"CREATE_TABLE", "PRAGMA", "ALTER_TABLE"},
}


def _runtime_python_files() -> list[Path]:
    roots = (Path("classroom_app/services"), Path("classroom_app/routers"))
    return sorted(path for root in roots for path in root.rglob("*.py"))


def test_runtime_sqlite_only_sql_is_explicitly_allowlisted_and_guarded():
    unexpected_hits: list[str] = []
    unguarded_allowlist_hits: list[str] = []

    for path in _runtime_python_files():
        text = path.read_text(encoding="utf-8")
        rel_path = path.as_posix()
        allowed_patterns = SQLITE_ONLY_RUNTIME_SQL_ALLOWLIST.get(rel_path, set())
        found_patterns = {name for name, needle in SQLITE_ONLY_PATTERNS.items() if needle in text}
        for pattern_name in sorted(found_patterns - allowed_patterns):
            unexpected_hits.append(f"{rel_path}: {pattern_name}")
        if found_patterns:
            if "get_configured_db_engine" not in text or "postgres" not in text:
                unguarded_allowlist_hits.append(rel_path)

    assert not unexpected_hits, (
        "New runtime SQLite-only SQL must be reviewed, engine-gated, and added to "
        "SQLITE_ONLY_RUNTIME_SQL_ALLOWLIST with focused PostgreSQL tests:\n"
        + "\n".join(unexpected_hits)
    )
    assert not unguarded_allowlist_hits, (
        "Allowlisted runtime SQLite-only SQL must stay behind explicit database-engine guards:\n"
        + "\n".join(sorted(set(unguarded_allowlist_hits)))
    )
