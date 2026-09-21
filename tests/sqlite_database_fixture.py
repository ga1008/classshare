"""An owned SQLite database for tests that use the application connection API.

Use ``cls.enterClassContext(isolated_sqlite_database())`` in setUpClass so
configuration and caches are restored even when class initialization fails.
This fixture changes no application schema code and never uses the configured
PostgreSQL URL. The backend test runner additionally blocks real PG connections.
"""

from contextlib import ExitStack, contextmanager
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from classroom_app import config, database


# These process-wide caches describe the currently selected database. Give the
# fixture its own values, leaving the original mutable sets untouched so nested
# fixtures and the next test recover the state for their own database.
_SCHEMA_MODULES = (
    "academic_final_materials", "agent_ext", "ai_jobs", "assessment_plans",
    "career_engagement", "career_path", "course_doc_packs", "gongwen",
    "lessondoc_editor", "lesson_plans", "life_tips", "material_whiteboards",
    "offering_class_links", "offering_merge", "polls", "resume", "retake",
    "scheduler", "session_learning_materials", "signature_workflow",
    "smart_schedule", "study_group_scheme", "teacher_evaluations", "wechat_mp",
)
_SERVICE_MODULES = (
    "student_achievement_service", "student_points_service", "student_streak_service",
)
_REQUIRED_TABLES = frozenset({
    "teachers", "students", "classes", "courses", "class_offerings",
    "assignments", "submissions", "scheduled_tasks", "class_offering_class_links",
    "assignment_group_bindings", "user_ui_preferences",
})


@contextmanager
def isolated_sqlite_database():
    """Initialize, validate and remove one private DB, restoring all overrides."""
    with TemporaryDirectory(prefix="lanshare-fixture-") as folder, ExitStack() as stack:
        db_path = Path(folder).resolve() / "classroom.db"
        for target, attribute, value in (
            (config, "DB_ENGINE", "sqlite"),
            (config, "DB_PATH", db_path),
            (database, "DB_PATH", db_path),
            (config, "DATABASE_URL", ""),
            (config, "POSTGRES_BACKEND_READY", False),
        ):
            stack.enter_context(patch.object(target, attribute, value))

        module_names = [f"classroom_app.db.schema_{name}" for name in _SCHEMA_MODULES]
        module_names += [f"classroom_app.services.{name}" for name in _SERVICE_MODULES]
        for name in module_names:
            module = import_module(name)
            for attribute in ("_SCHEMA_READY", "_SCHEMA_READY_ENGINES", "_READY_KEYS"):
                if hasattr(module, attribute):
                    fresh_value = False if attribute == "_SCHEMA_READY" else set()
                    stack.enter_context(patch.object(module, attribute, fresh_value))

        database.init_database()
        with database.get_db_connection() as conn:
            actual_path = Path(conn.execute("PRAGMA database_list").fetchone()[2]).resolve()
            if actual_path != db_path:
                raise RuntimeError("Test fixture did not select its owned SQLite database")
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = _REQUIRED_TABLES - tables
            if missing:
                raise RuntimeError(f"Incomplete test fixture schema: {', '.join(sorted(missing))}")
        yield db_path
