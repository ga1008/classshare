from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


STUDENT_COUNT = 100
PRIVATE_MESSAGE_COUNT = 50
TEACHER_EMAIL = "loadtest.teacher@example.com"
TEACHER_PASSWORD = "TeacherPass123"
INITIAL_PASSWORD_TEMPLATE = "InitPass{index:03d}A1"
CHANGED_PASSWORD_TEMPLATE = "ChangedPass{index:03d}B2"


@dataclass(slots=True)
class StudentSeed:
    student_pk: int
    name: str
    student_id_number: str


@dataclass(slots=True)
class StudentSession:
    student: StudentSeed
    password: str
    access_token: str


class _MockAIResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload, ensure_ascii=False)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"mock ai response failed with {self.status_code}",
                request=httpx.Request("POST", "http://mock-ai.local"),
                response=httpx.Response(self.status_code, text=self.text),
            )

    def json(self) -> dict[str, Any]:
        return dict(self._payload)


def _seed_test_data(student_count: int) -> dict[str, Any]:
    from classroom_app.database import get_db_connection
    from classroom_app.dependencies import get_password_hash

    students: list[StudentSeed] = []
    with get_db_connection() as conn:
        teacher_cursor = conn.execute(
            """
            INSERT INTO teachers (name, email, hashed_password)
            VALUES (?, ?, ?)
            """,
            ("Load Test Teacher", TEACHER_EMAIL, get_password_hash(TEACHER_PASSWORD)),
        )
        teacher_id = int(teacher_cursor.lastrowid)

        class_cursor = conn.execute(
            """
            INSERT INTO classes (name, created_by_teacher_id, description)
            VALUES (?, ?, ?)
            """,
            ("Load Test Class", teacher_id, "Synthetic class for concurrency smoke tests."),
        )
        class_id = int(class_cursor.lastrowid)

        course_cursor = conn.execute(
            """
            INSERT INTO courses (name, description, credits, created_by_teacher_id)
            VALUES (?, ?, ?, ?)
            """,
            ("Load Test Course", "Synthetic course for concurrency smoke tests.", 2.0, teacher_id),
        )
        course_id = int(course_cursor.lastrowid)

        offering_cursor = conn.execute(
            """
            INSERT INTO class_offerings (class_id, course_id, teacher_id, semester, schedule_info)
            VALUES (?, ?, ?, ?, ?)
            """,
            (class_id, course_id, teacher_id, "2026-Spring", "Mon 08:00"),
        )
        class_offering_id = int(offering_cursor.lastrowid)

        for index in range(1, student_count + 1):
            name = f"Student{index:03d}"
            student_id_number = f"202600{index:03d}"
            student_cursor = conn.execute(
                """
                INSERT INTO students (student_id_number, name, class_id, gender, email)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    student_id_number,
                    name,
                    class_id,
                    "unknown",
                    f"student{index:03d}@example.test",
                ),
            )
            students.append(
                StudentSeed(
                    student_pk=int(student_cursor.lastrowid),
                    name=name,
                    student_id_number=student_id_number,
                )
            )

        conn.commit()

    return {
        "teacher_id": teacher_id,
        "class_id": class_id,
        "course_id": course_id,
        "class_offering_id": class_offering_id,
        "students": students,
    }


def _extract_access_token(response: httpx.Response) -> str:
    access_token = response.cookies.get("access_token")
    if not access_token:
        raise RuntimeError("missing access_token cookie")
    return access_token


async def _run_phase(label: str, coroutines: list[asyncio.Future | asyncio.Task | Any]) -> tuple[dict[str, Any], list[Any]]:
    started_at = time.perf_counter()
    results = await asyncio.gather(*coroutines, return_exceptions=True)
    elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
    failures = [result for result in results if isinstance(result, Exception)]
    summary = {
        "label": label,
        "total": len(results),
        "failures": len(failures),
        "elapsed_ms": elapsed_ms,
    }
    if failures:
        summary["sample_failure"] = repr(failures[0])
    return summary, results


def _assert_phase_success(summary: dict[str, Any], results: list[Any]) -> None:
    if summary["failures"]:
        raise RuntimeError(f"{summary['label']} failed: {summary.get('sample_failure')}")
    if not results:
        raise RuntimeError(f"{summary['label']} returned no results")


async def _setup_student_password(
    client: httpx.AsyncClient,
    student: StudentSeed,
) -> StudentSession:
    initial_password = INITIAL_PASSWORD_TEMPLATE.format(index=student.student_pk)
    identity_response = await client.post(
        "/api/student/login/identity",
        data={
            "name": student.name,
            "student_id_number": student.student_id_number,
            "next": "/dashboard",
        },
    )
    identity_response.raise_for_status()
    identity_payload = identity_response.json()
    setup_token = str(identity_payload.get("setup_token") or "").strip()
    if not setup_token:
        raise RuntimeError(f"missing setup_token for {student.student_id_number}")

    setup_response = await client.post(
        "/api/student/password/setup",
        data={
            "setup_token": setup_token,
            "password": initial_password,
            "confirm_password": initial_password,
            "next": "/dashboard",
        },
    )
    setup_response.raise_for_status()
    setup_payload = setup_response.json()
    if setup_payload.get("status") != "success":
        raise RuntimeError(json.dumps(setup_payload, ensure_ascii=False))

    return StudentSession(
        student=student,
        password=initial_password,
        access_token=_extract_access_token(setup_response),
    )


async def _login_student(
    client: httpx.AsyncClient,
    student_session: StudentSession,
) -> StudentSession:
    response = await client.post(
        "/api/student/login/password",
        data={
            "identifier": student_session.student.student_id_number,
            "password": student_session.password,
            "next": "/dashboard",
        },
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "success":
        raise RuntimeError(json.dumps(payload, ensure_ascii=False))
    return StudentSession(
        student=student_session.student,
        password=student_session.password,
        access_token=_extract_access_token(response),
    )


async def _change_student_password(
    client: httpx.AsyncClient,
    student_session: StudentSession,
) -> StudentSession:
    new_password = CHANGED_PASSWORD_TEMPLATE.format(index=student_session.student.student_pk)
    response = await client.post(
        "/api/student/password/change",
        data={
            "current_password": student_session.password,
            "new_password": new_password,
            "confirm_password": new_password,
        },
        cookies={"access_token": student_session.access_token},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "success":
        raise RuntimeError(json.dumps(payload, ensure_ascii=False))
    return StudentSession(
        student=student_session.student,
        password=new_password,
        access_token=student_session.access_token,
    )


async def _send_behavior_batch(
    client: httpx.AsyncClient,
    class_offering_id: int,
    student_session: StudentSession,
) -> dict[str, Any]:
    response = await client.post(
        f"/api/classrooms/{class_offering_id}/behavior/batch",
        json={
            "page_key": "classroom_main",
            "events": [
                {
                    "action_type": "page_enter",
                    "summary_text": "enter classroom",
                    "page_key": "classroom_main",
                    "payload": {"student_id": student_session.student.student_id_number},
                },
                {
                    "action_type": "page_focus",
                    "summary_text": "focus classroom",
                    "page_key": "classroom_main",
                    "payload": {"focused": True},
                },
                {
                    "action_type": "presence_heartbeat",
                    "summary_text": "",
                    "page_key": "classroom_main",
                    "payload": {"focused": True, "visibility_state": "visible", "idle_seconds": 0},
                },
            ],
        },
        cookies={"access_token": student_session.access_token},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") not in {"success", "degraded"}:
        raise RuntimeError(json.dumps(payload, ensure_ascii=False))
    return payload


async def _fetch_discussion_mood(
    client: httpx.AsyncClient,
    class_offering_id: int,
    student_session: StudentSession,
) -> dict[str, Any]:
    response = await client.get(
        f"/api/classrooms/{class_offering_id}/discussion-mood",
        cookies={"access_token": student_session.access_token},
    )
    response.raise_for_status()
    payload = response.json()
    if not str(payload.get("headline") or "").strip():
        raise RuntimeError(f"missing discussion mood headline for {student_session.student.student_id_number}")
    return payload


async def _send_private_message(
    client: httpx.AsyncClient,
    class_offering_id: int,
    teacher_id: int,
    student_session: StudentSession,
) -> dict[str, Any]:
    response = await client.post(
        "/api/message-center/private/messages",
        json={
            "contact_identity": f"teacher:{teacher_id}",
            "class_offering_id": class_offering_id,
            "content": f"load-test private message from {student_session.student.student_id_number}",
        },
        cookies={"access_token": student_session.access_token},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "success":
        raise RuntimeError(json.dumps(payload, ensure_ascii=False))
    return payload


async def _fetch_message_center_summary(
    client: httpx.AsyncClient,
    access_token: str,
) -> dict[str, Any]:
    response = await client.get(
        "/api/message-center/summary",
        cookies={"access_token": access_token},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "success":
        raise RuntimeError(json.dumps(payload, ensure_ascii=False))
    return payload


async def _login_teacher(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/teacher/login",
        data={
            "email": TEACHER_EMAIL,
            "password": TEACHER_PASSWORD,
            "next": "/dashboard",
        },
        follow_redirects=False,
    )
    if response.status_code not in {302, 303}:
        raise RuntimeError(f"teacher login failed: {response.status_code} {response.text}")
    return _extract_access_token(response)


async def _generate_assignment_via_ai(
    client: httpx.AsyncClient,
    teacher_access_token: str,
    index: int,
) -> dict[str, Any]:
    response = await client.post(
        "/api/ai/generate_assignment",
        json={
            "prompt": f"Generate assignment #{index}",
            "model_type": "standard",
        },
        cookies={"access_token": teacher_access_token},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "success":
        raise RuntimeError(json.dumps(payload, ensure_ascii=False))
    return payload


async def _run_chat_persistence_smoke(class_offering_id: int, message_count: int) -> dict[str, Any]:
    from classroom_app.services.chat_handler import save_chat_message

    started_at = time.perf_counter()
    await asyncio.gather(
        *[
            save_chat_message(
                class_offering_id,
                {
                    "sender": f"ChatUser{index:03d}",
                    "role": "student",
                    "message": f"chat persistence message {index:03d}",
                    "timestamp": datetime.now().strftime("%H:%M"),
                    "logged_at": datetime.now().isoformat(),
                    "user_id": f"student:{index:03d}",
                },
            )
            for index in range(1, message_count + 1)
        ]
    )
    return {
        "label": "chat_persistence",
        "total": message_count,
        "failures": 0,
        "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 2),
    }


def _collect_database_stats() -> dict[str, int]:
    from classroom_app.database import get_db_connection

    with get_db_connection() as conn:
        return {
            "user_sessions": int(conn.execute("SELECT COUNT(*) FROM user_sessions").fetchone()[0]),
            "student_login_audit_logs": int(conn.execute("SELECT COUNT(*) FROM student_login_audit_logs").fetchone()[0]),
            "private_messages": int(conn.execute("SELECT COUNT(*) FROM private_messages").fetchone()[0]),
            "message_center_notifications": int(conn.execute("SELECT COUNT(*) FROM message_center_notifications").fetchone()[0]),
            "chat_logs": int(conn.execute("SELECT COUNT(*) FROM chat_logs").fetchone()[0]),
        }


async def _main() -> None:
    with tempfile.TemporaryDirectory(prefix="lanshare-loadtest-", ignore_cleanup_errors=True) as tmp:
        temp_root = Path(tmp)
        os.environ["MAIN_DB_PATH"] = str(temp_root / "classroom.db")

        from classroom_app import config
        from classroom_app.app import app, shutdown_event, startup_event
        from classroom_app.core import ai_client
        from classroom_app.services import chat_handler

        config.CHAT_LOG_DIR = temp_root / "chat_logs"
        chat_handler.CHAT_LOG_DIR = config.CHAT_LOG_DIR
        chat_handler._chat_log_schema_ready = False
        chat_handler._migrated_rooms.clear()

        async def _mock_ai_post(path: str, *args, **kwargs):
            normalized_path = str(path)
            if normalized_path.endswith("/api/ai/generate-assignment"):
                return _MockAIResponse(
                    {
                        "status": "success",
                        "title": "Mock Assignment",
                        "requirements_md": "1. Read the prompt\\n2. Answer briefly\\n3. Submit before class ends",
                    }
                )
            if normalized_path.endswith("/api/ai/chat"):
                return _MockAIResponse(
                    {
                        "status": "success",
                        "response_text": "Mock AI response",
                        "response_json": {
                            "mood_label": "warm",
                            "headline": "Mock headline",
                            "detail": "Mock detail",
                        },
                    }
                )
            return _MockAIResponse({"status": "success"})

        ai_client.post = _mock_ai_post  # type: ignore[method-assign]

        await startup_event()
        try:
            seed = _seed_test_data(STUDENT_COUNT)
            class_offering_id = int(seed["class_offering_id"])
            teacher_id = int(seed["teacher_id"])
            students = list(seed["students"])

            transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
            phase_summaries: list[dict[str, Any]] = []

            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                follow_redirects=False,
                timeout=120.0,
            ) as client:
                setup_summary, setup_results = await _run_phase(
                    "password_setup",
                    [_setup_student_password(client, student) for student in students],
                )
                _assert_phase_success(setup_summary, setup_results)
                student_sessions = [result for result in setup_results if isinstance(result, StudentSession)]
                phase_summaries.append(setup_summary)

                login_summary, login_results = await _run_phase(
                    "password_login",
                    [_login_student(client, session) for session in student_sessions],
                )
                _assert_phase_success(login_summary, login_results)
                student_sessions = [result for result in login_results if isinstance(result, StudentSession)]
                phase_summaries.append(login_summary)

                password_change_summary, password_change_results = await _run_phase(
                    "password_change",
                    [_change_student_password(client, session) for session in student_sessions],
                )
                _assert_phase_success(password_change_summary, password_change_results)
                student_sessions = [result for result in password_change_results if isinstance(result, StudentSession)]
                phase_summaries.append(password_change_summary)

                behavior_summary, behavior_results = await _run_phase(
                    "behavior_batch",
                    [_send_behavior_batch(client, class_offering_id, session) for session in student_sessions],
                )
                _assert_phase_success(behavior_summary, behavior_results)
                phase_summaries.append(behavior_summary)

                discussion_mood_summary, discussion_mood_results = await _run_phase(
                    "discussion_mood",
                    [_fetch_discussion_mood(client, class_offering_id, session) for session in student_sessions],
                )
                _assert_phase_success(discussion_mood_summary, discussion_mood_results)
                phase_summaries.append(discussion_mood_summary)

                teacher_access_token = await _login_teacher(client)

                ai_summary, ai_results = await _run_phase(
                    "ai_generate_assignment",
                    [
                        _generate_assignment_via_ai(client, teacher_access_token, index)
                        for index in range(1, STUDENT_COUNT + 1)
                    ],
                )
                _assert_phase_success(ai_summary, ai_results)
                phase_summaries.append(ai_summary)

                private_message_summary, private_message_results = await _run_phase(
                    "private_messages",
                    [
                        _send_private_message(client, class_offering_id, teacher_id, session)
                        for session in student_sessions[:PRIVATE_MESSAGE_COUNT]
                    ],
                )
                _assert_phase_success(private_message_summary, private_message_results)
                phase_summaries.append(private_message_summary)

                teacher_summary = await _fetch_message_center_summary(client, teacher_access_token)
                teacher_unread_total = int(teacher_summary["summary"]["unread_total"])
                if teacher_unread_total < PRIVATE_MESSAGE_COUNT:
                    raise RuntimeError(
                        f"teacher unread_total too small: {teacher_unread_total} < {PRIVATE_MESSAGE_COUNT}"
                    )

                student_summary_phase, student_summary_results = await _run_phase(
                    "message_center_summary",
                    [_fetch_message_center_summary(client, session.access_token) for session in student_sessions],
                )
                _assert_phase_success(student_summary_phase, student_summary_results)
                phase_summaries.append(student_summary_phase)

            chat_summary = await _run_chat_persistence_smoke(class_offering_id, STUDENT_COUNT)
            phase_summaries.append(chat_summary)

            database_stats = _collect_database_stats()
            if database_stats["chat_logs"] < STUDENT_COUNT:
                raise RuntimeError(f"chat log count too small: {database_stats['chat_logs']}")
            if database_stats["user_sessions"] < STUDENT_COUNT:
                raise RuntimeError(f"user session count too small: {database_stats['user_sessions']}")

            result_payload = {
                "status": "success",
                "student_count": STUDENT_COUNT,
                "teacher_unread_total": teacher_unread_total,
                "phases": phase_summaries,
                "database_stats": database_stats,
                "database_path": os.environ["MAIN_DB_PATH"],
            }
            print(json.dumps(result_payload, ensure_ascii=False, indent=2))
        finally:
            await shutdown_event()


if __name__ == "__main__":
    asyncio.run(_main())
