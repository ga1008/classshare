"""Shared world for the 散修 Phase B tests: synthetic teacher/class/student/course/offering,
a stubbed AI caller, zeroed sleeps and temp directories. Never touches a real DB or model.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import tempfile
from pathlib import Path
from unittest.mock import patch

from classroom_app import config, database
from classroom_app.services import blog_safety_service, blog_service, chat_handler
from classroom_app.services.semester_identity_service import current_identity
from classroom_app.services.wanderer_identity_service import invalidate_wanderer_cache
from classroom_app.services.wanderer_settings_service import (
    SETTING_DAILY_AI_CALL_CAP,
    daily_ai_calls_key,
    wanderer_runtime_setting_set,
)
from tests.wanderer_test_support import WandererDbTestCase, reset_wanderer_caches

ONLY_BLOG = {"blog_browse": 1, "blog_like": 1, "blog_comment": 1, "reply": 1, "dm_reply": 1,
             "classroom_visit": 0, "classroom_message": 0, "knowledge_read": 0, "promotion": 0, "blog_post": 0}
ONLY_CLASSROOM = {**{key: 0 for key in ONLY_BLOG}, "classroom_visit": 1, "classroom_message": 1}
_RESET_TABLES = (
    "wanderer_experience_notes",
    "wanderer_social_inbox",
    "wanderer_relationships", "wanderer_legacies",
    "wanderer_action_log", "wanderer_memory_threads", "wanderer_dao_reviews", "message_center_notifications",
    "private_messages", "chat_logs", "blog_likes", "blog_comments", "blog_posts", "scheduled_tasks",
    "material_search_chunks", "material_search_documents", "course_material_assignments", "course_materials",
)


def turn(action="reply", parts=("好",), like=False, mood="平静") -> str:
    return json.dumps({"action": action, "parts": list(parts), "mood": mood, "like": like,
                       "reason": "想安静一会儿" if action == "skip" else "想和对方聊聊"}, ensure_ascii=False)


class StubCaller:
    """Returns canned texts in order (the last one repeats); records every call."""

    def __init__(self, *texts: str, error: Exception | None = None):
        self.texts = list(texts) or [turn()]
        self.error = error
        self.calls: list[tuple[str, dict, dict]] = []

    async def __call__(self, endpoint, json_payload, **kwargs):
        self.calls.append((endpoint, json_payload, kwargs))
        if self.error is not None:
            raise self.error
        text = self.texts[min(len(self.calls) - 1, len(self.texts) - 1)]
        return {"status": "success", "response_text": text}

    @property
    def labels(self) -> list[str]:
        return [payload["task_label"] for _endpoint, payload, _kwargs in self.calls]


class ZeroSleep:
    def __init__(self):
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class WandererWorldTestCase(WandererDbTestCase):
    """One synthetic school per test class; per-test cleanup of everything the tick writes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with database.get_db_connection() as conn:
            from classroom_app.services.material_search_schema import ensure_material_search_schema

            ensure_material_search_schema(conn)
            cls.teacher_id = int(conn.execute("SELECT id FROM teachers ORDER BY id LIMIT 1").fetchone()[0])
            conn.execute("INSERT INTO classes (name, created_by_teacher_id) VALUES (?, ?)", ("合成一班", cls.teacher_id))
            cls.class_id = int(conn.execute("SELECT id FROM classes WHERE name = '合成一班'").fetchone()[0])
            conn.execute("INSERT INTO students (student_id_number, name, class_id) VALUES (?, ?, ?)",
                         ("S-0001", "真实同学", cls.class_id))
            cls.student_pk = int(conn.execute("SELECT id FROM students WHERE student_id_number = 'S-0001'").fetchone()[0])
            conn.execute("INSERT INTO courses (name, created_by_teacher_id) VALUES (?, ?)",
                         ("Python 程序设计", cls.teacher_id))
            course_id = int(conn.execute("SELECT id FROM courses WHERE name = 'Python 程序设计'").fetchone()[0])
            conn.execute("INSERT INTO class_offerings (class_id, course_id, teacher_id, semester) VALUES (?, ?, ?, ?)",
                         (cls.class_id, course_id, cls.teacher_id, current_identity().canonical_name))
            cls.offering_id = int(conn.execute("SELECT id FROM class_offerings ORDER BY id DESC LIMIT 1").fetchone()[0])
            conn.commit()
        cls.teacher = {"id": cls.teacher_id, "role": "teacher", "name": "合成教师"}
        cls.student = {"id": cls.student_pk, "role": "student", "name": "真实同学", "class_id": cls.class_id}

    def setUp(self):
        super().setUp()
        temp = tempfile.TemporaryDirectory(prefix="wanderer-b-")
        self.addCleanup(temp.cleanup)
        for target, attribute, value in (
            (config, "DATA_DIR", Path(temp.name) / "data"),
            (chat_handler, "CHAT_LOG_DIR", Path(temp.name) / "chat"),
            (blog_safety_service, "COMMENTS_PER_WINDOW", 10_000),
        ):
            patcher = patch.object(target, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {"WANDERER_ENABLED": "true", "MAIN_APP_INTERNAL_URL": "http://127.0.0.1:9"})
        env.start()
        self.addCleanup(env.stop)
        self._reset_world()

    def _reset_world(self):
        with self.connect() as conn:
            for table in _RESET_TABLES:
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM wanderer_knowledge_entries WHERE source_kind <> 'seed'")
            conn.execute("UPDATE wanderer_knowledge_entries SET status = 'active'")
            conn.execute("DELETE FROM wanderer_runtime_settings")
            wanderer_runtime_setting_set(conn, daily_ai_calls_key(), "0")
            wanderer_runtime_setting_set(conn, SETTING_DAILY_AI_CALL_CAP, "4096")
            conn.execute("UPDATE wanderer_agents SET status = 'active', promotion_requested = 0, "
                         "promotion_requested_at = NULL, warnings_count = 0, last_warning_at = NULL, stats_json = '{}', "
                         "born_at = created_at, lifespan_weeks = 0, life_penalty_weeks = 0, life_extensions = 0, "
                         "talents_json = '{}', legacy_id = NULL, last_extend_at = NULL, last_appeal_at = NULL, "
                         "last_resurrect_at = NULL, appeal_requested = 0, appeal_requested_at = NULL, appeal_note = '', "
                         "origin = CASE WHEN origin = 'reincarnated' THEN 'seed' ELSE origin END, "
                         "knowledge_chars = (SELECT COALESCE(SUM(k.chars), 0) FROM wanderer_knowledge_entries k "
                         "WHERE k.wanderer_id = wanderer_agents.id AND k.status = 'active')")
            conn.commit()
        reset_wanderer_caches()

    # -- helpers ------------------------------------------------------------

    def set_wanderer(self, slug: str, **fields) -> dict:
        assignments, values = [], []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            values.append(json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value)
        with self.connect() as conn:
            conn.execute(f"UPDATE wanderer_agents SET {', '.join(assignments)} WHERE slug = ?", (*values, slug))
            conn.commit()
            invalidate_wanderer_cache()
            return self.wanderer(conn, slug)

    def make_post(self, title="今天的篮球训练", content="大家觉得训练强度怎么样？", author=None, tags=None) -> int:
        with self.connect() as conn:
            result = blog_service.create_post(conn, author or self.teacher, title=title, content_md=content,
                                              tags=tags or [])
            conn.commit()
        return int(result["id"])

    def tick(self, caller, wanderer_ids, *, seed=7, sleep=None, now=None, force=True):
        from classroom_app.services.wanderer_behavior_service import run_wanderer_tick_once

        return asyncio.run(run_wanderer_tick_once(
            database.get_db_connection, wanderer_ids=wanderer_ids, ai_caller=caller, force=force,
            sleep=sleep or ZeroSleep(), rng=random.Random(seed), now=now,
        ))

    def rows(self, sql: str, params=()) -> list[dict]:
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def actions(self, wanderer_id: int) -> list[dict]:
        return self.rows("SELECT * FROM wanderer_action_log WHERE wanderer_id = ? ORDER BY id", (wanderer_id,))
