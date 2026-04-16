from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import importlib.util
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE_DB = PROJECT_ROOT / "data" / "classroom.db"
DEFAULT_MAIN_HOST = "127.0.0.1"
DEFAULT_MAIN_PORT = 18700
DEFAULT_AI_PORT = 18701
DEFAULT_STUDENT_COUNT = 100
DEFAULT_MAX_CONNECTIONS = 300

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _ensure_venv_python() -> None:
    if os.getenv("LANSHARE_LOAD_TEST_BOOTSTRAPPED") == "1":
        return

    required_modules = ("httpx", "websockets")
    if all(importlib.util.find_spec(module_name) is not None for module_name in required_modules):
        return

    venv_python = PROJECT_ROOT / "venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        return

    env = os.environ.copy()
    env["LANSHARE_LOAD_TEST_BOOTSTRAPPED"] = "1"
    completed = subprocess.run(
        [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]],
        cwd=PROJECT_ROOT,
        env=env,
    )
    raise SystemExit(completed.returncode)


_ensure_venv_python()

import httpx
import websockets


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


def _truncate(text: Any, limit: int = 240) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 1, 0)].rstrip() + "…"


def _percentile(samples: list[float], ratio: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    if len(ordered) == 1:
        return float(ordered[0])
    position = max(0.0, min(1.0, ratio)) * (len(ordered) - 1)
    lower_index = int(position)
    upper_index = min(len(ordered) - 1, lower_index + 1)
    if lower_index == upper_index:
        return float(ordered[lower_index])
    lower_value = float(ordered[lower_index])
    upper_value = float(ordered[upper_index])
    weight = position - lower_index
    return lower_value + (upper_value - lower_value) * weight


@dataclass(slots=True)
class TargetOffering:
    id: int
    class_id: int
    teacher_id: int
    class_name: str
    course_name: str
    assignment_count: int
    material_assignment_count: int
    course_file_count: int
    chat_log_count: int


@dataclass(slots=True)
class SeededStudent:
    index: int
    student_pk: int
    student_id_number: str
    name: str
    password: str


@dataclass(slots=True)
class AssignmentTarget:
    id: int
    title: str
    is_exam: bool


@dataclass(slots=True)
class MaterialTarget:
    root_id: int
    file_id: int
    file_name: str


@dataclass(slots=True)
class ActionAggregate:
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    skipped: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    sample_errors: list[str] = field(default_factory=list)

    def record_success(self, latency_ms: float) -> None:
        self.attempts += 1
        self.successes += 1
        self.latencies_ms.append(latency_ms)

    def record_failure(self, latency_ms: float, error_message: str) -> None:
        self.attempts += 1
        self.failures += 1
        self.latencies_ms.append(latency_ms)
        if len(self.sample_errors) < 5 and error_message:
            self.sample_errors.append(_truncate(error_message, 300))

    def record_skip(self) -> None:
        self.skipped += 1

    def to_dict(self) -> dict[str, Any]:
        success_rate = round((self.successes / self.attempts) * 100.0, 2) if self.attempts else 0.0
        return {
            "attempts": int(self.attempts),
            "successes": int(self.successes),
            "failures": int(self.failures),
            "skipped": int(self.skipped),
            "success_rate": success_rate,
            "p50_ms": round(_percentile(self.latencies_ms, 0.50), 2),
            "p95_ms": round(_percentile(self.latencies_ms, 0.95), 2),
            "p99_ms": round(_percentile(self.latencies_ms, 0.99), 2),
            "max_ms": round(max(self.latencies_ms), 2) if self.latencies_ms else 0.0,
            "sample_errors": list(self.sample_errors),
        }


class SummaryRecorder:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._actions: dict[str, ActionAggregate] = {}

    async def record_success(self, action: str, latency_ms: float) -> None:
        async with self._lock:
            self._actions.setdefault(action, ActionAggregate()).record_success(latency_ms)

    async def record_failure(self, action: str, latency_ms: float, error_message: str) -> None:
        async with self._lock:
            self._actions.setdefault(action, ActionAggregate()).record_failure(latency_ms, error_message)

    async def record_skip(self, action: str) -> None:
        async with self._lock:
            self._actions.setdefault(action, ActionAggregate()).record_skip()

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            return {
                action: aggregate.to_dict()
                for action, aggregate in sorted(self._actions.items())
            }


class JsonlLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8")
        self._lock = asyncio.Lock()

    async def write(self, payload: dict[str, Any]) -> None:
        normalized = dict(payload)
        normalized.setdefault("at", _now_iso())
        async with self._lock:
            self._handle.write(_safe_json(normalized) + "\n")
            self._handle.flush()

    def close(self) -> None:
        try:
            self._handle.close()
        except Exception:
            pass


@dataclass(slots=True)
class StartedProcess:
    name: str
    process: subprocess.Popen[Any]
    log_path: Path
    log_handle: Any


@dataclass(slots=True)
class ScenarioContext:
    base_url: str
    ws_url: str
    offering: TargetOffering
    students: list[SeededStudent]
    assignment_target: Optional[AssignmentTarget]
    material_target: Optional[MaterialTarget]
    ai_mode: str


def clone_sqlite_database(source_path: Path, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source_path) as source_conn, sqlite3.connect(target_path) as target_conn:
        source_conn.backup(target_conn)


def _row_to_target_offering(row: sqlite3.Row) -> TargetOffering:
    return TargetOffering(
        id=int(row["id"]),
        class_id=int(row["class_id"]),
        teacher_id=int(row["teacher_id"]),
        class_name=str(row["class_name"] or ""),
        course_name=str(row["course_name"] or ""),
        assignment_count=int(row["assignment_count"] or 0),
        material_assignment_count=int(row["material_assignment_count"] or 0),
        course_file_count=int(row["course_file_count"] or 0),
        chat_log_count=int(row["chat_log_count"] or 0),
    )


def select_target_offering(db_path: Path, requested_offering_id: Optional[int] = None) -> TargetOffering:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                o.id,
                o.class_id,
                o.teacher_id,
                cl.name AS class_name,
                c.name AS course_name,
                (SELECT COUNT(*) FROM assignments a WHERE a.class_offering_id = o.id AND a.status = 'published') AS assignment_count,
                (SELECT COUNT(*) FROM course_material_assignments ma WHERE ma.class_offering_id = o.id) AS material_assignment_count,
                (SELECT COUNT(*) FROM course_files f WHERE f.course_id = o.course_id AND f.is_public = 1 AND f.is_teacher_resource = 0) AS course_file_count,
                (SELECT COUNT(*) FROM chat_logs l WHERE l.class_offering_id = o.id) AS chat_log_count
            FROM class_offerings o
            JOIN classes cl ON cl.id = o.class_id
            JOIN courses c ON c.id = o.course_id
            ORDER BY o.id
            """
        ).fetchall()

    if not rows:
        raise RuntimeError("当前数据库没有可用的课堂，无法执行压测。")

    candidates = [_row_to_target_offering(row) for row in rows]
    if requested_offering_id is not None:
        for item in candidates:
            if item.id == int(requested_offering_id):
                return item
        raise RuntimeError(f"未找到 class_offering_id={requested_offering_id} 的课堂。")

    def _score(item: TargetOffering) -> tuple[float, int]:
        richness = (
            item.assignment_count * 10
            + item.material_assignment_count * 15
            + item.course_file_count * 4
            + min(item.chat_log_count, 200) * 0.1
        )
        return richness, item.id

    return max(candidates, key=_score)


def discover_assignment_target(db_path: Path, class_offering_id: int) -> Optional[AssignmentTarget]:
    now_iso = datetime.now().isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, title, exam_paper_id
            FROM assignments
            WHERE class_offering_id = ?
              AND status = 'published'
              AND (due_at IS NULL OR due_at = '' OR due_at > ?)
            ORDER BY CASE WHEN exam_paper_id IS NOT NULL THEN 0 ELSE 1 END, id DESC
            LIMIT 1
            """,
            (class_offering_id, now_iso),
        ).fetchone()
    if not row:
        return None
    return AssignmentTarget(
        id=int(row["id"]),
        title=str(row["title"] or ""),
        is_exam=bool(row["exam_paper_id"]),
    )


def discover_material_target(db_path: Path, class_offering_id: int) -> Optional[MaterialTarget]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
                a.material_id AS root_id,
                child.id AS file_id,
                child.name AS file_name
            FROM course_material_assignments a
            JOIN course_materials root ON root.id = a.material_id
            JOIN course_materials child ON child.root_id = root.root_id
            WHERE a.class_offering_id = ?
              AND child.node_type = 'file'
            ORDER BY child.id ASC
            LIMIT 1
            """,
            (class_offering_id,),
        ).fetchone()
    if not row:
        return None
    return MaterialTarget(
        root_id=int(row["root_id"]),
        file_id=int(row["file_id"]),
        file_name=str(row["file_name"] or ""),
    )


def seed_test_students(
    *,
    db_path: Path,
    offering: TargetOffering,
    student_count: int,
    run_id: str,
) -> list[SeededStudent]:
    from classroom_app.dependencies import get_password_hash

    seeded_students: list[SeededStudent] = []
    suffix = "".join(ch for ch in run_id if ch.isdigit())[-8:] or "00000000"
    timestamp = datetime.now().isoformat()

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        for index in range(1, student_count + 1):
            student_id_number = f"LT{suffix}{index:03d}"
            name = f"LoadTestStudent{index:03d}"
            password = f"LtPass{index:03d}Aa9"
            cursor = conn.execute(
                """
                INSERT INTO students (
                    student_id_number,
                    name,
                    class_id,
                    gender,
                    email,
                    hashed_password,
                    password_reset_required,
                    password_updated_at,
                    description
                )
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    student_id_number,
                    name,
                    offering.class_id,
                    "unknown",
                    f"loadtest-{suffix}-{index:03d}@example.test",
                    get_password_hash(password),
                    timestamp,
                    f"load-test:{run_id}",
                ),
            )
            seeded_students.append(
                SeededStudent(
                    index=index,
                    student_pk=int(cursor.lastrowid),
                    student_id_number=student_id_number,
                    name=name,
                    password=password,
                )
            )
        conn.commit()

    return seeded_students


def write_seed_credentials(path: Path, students: list[SeededStudent]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "student_pk": student.student_pk,
            "student_id_number": student.student_id_number,
            "name": student.name,
            "password": student.password,
        }
        for student in students
    ]
    path.write_text(_safe_json(payload), encoding="utf-8")


def build_process_env(
    *,
    temp_root: Path,
    db_path: Path,
    host: str,
    main_port: int,
    ai_port: int,
    ai_mode: str,
) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["MAIN_HOST"] = host
    env["MAIN_PORT"] = str(main_port)
    env["MAIN_DB_PATH"] = str(db_path)
    env["MAIN_DATA_DIR"] = str(temp_root / "data")
    env["MAIN_HOMEWORK_SUBMISSIONS_DIR"] = str(temp_root / "homework_submissions")
    env["MAIN_CHAT_LOG_DIR"] = str(temp_root / "chat_logs")
    env["MAIN_ATTENDANCE_DIR"] = str(temp_root / "attendance")
    env["MAIN_SHARE_DIR"] = str(temp_root / "shared_files")
    env["MAIN_ROSTER_DIR"] = str(temp_root / "rosters")
    env["MAIN_CHUNKED_UPLOADS_DIR"] = str(temp_root / "chunked_uploads")

    if ai_mode in {"mock", "real"}:
        env["AI_HOST"] = host
        env["AI_PORT"] = str(ai_port)
        env["AI_ASSISTANT_URL"] = f"http://{host}:{ai_port}"
    else:
        env["AI_ASSISTANT_URL"] = "http://127.0.0.1:9"

    return env


def start_service_process(
    *,
    name: str,
    command: list[str],
    env: dict[str, str],
    log_path: Path,
) -> StartedProcess:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return StartedProcess(name=name, process=process, log_path=log_path, log_handle=log_handle)


async def wait_for_health(url: str, *, timeout_seconds: float = 60.0) -> dict[str, Any]:
    deadline = time.perf_counter() + timeout_seconds
    async with httpx.AsyncClient(timeout=5.0) as client:
        last_error = ""
        while time.perf_counter() < deadline:
            try:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
                if payload.get("status") == "ok":
                    return payload
                last_error = _safe_json(payload)
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
            await asyncio.sleep(0.5)
    raise RuntimeError(f"等待健康检查超时: {url} / {last_error}")


async def terminate_process(started: Optional[StartedProcess]) -> dict[str, Any]:
    if started is None:
        return {"started": False, "terminated": True}

    process = started.process
    if process.poll() is not None:
        try:
            started.log_handle.close()
        except Exception:
            pass
        return {"started": True, "terminated": True, "returncode": process.returncode}

    process.terminate()
    try:
        await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=10.0)
    except asyncio.TimeoutError:
        process.kill()
        await asyncio.to_thread(process.wait)
    try:
        started.log_handle.close()
    except Exception:
        pass
    return {"started": True, "terminated": True, "returncode": process.returncode}


def make_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Isolated full-stack load test for Lanshare.")
    parser.add_argument("--source-db", default=str(DEFAULT_SOURCE_DB), help="源数据库路径。")
    parser.add_argument("--class-offering-id", type=int, default=None, help="指定要压测的课堂 ID。")
    parser.add_argument("--student-count", type=int, default=DEFAULT_STUDENT_COUNT, help="压测学生数量。")
    parser.add_argument("--host", default=DEFAULT_MAIN_HOST, help="后端监听地址。")
    parser.add_argument("--port", type=int, default=DEFAULT_MAIN_PORT, help="主后端端口。")
    parser.add_argument("--ai-port", type=int, default=DEFAULT_AI_PORT, help="AI 服务端口。")
    parser.add_argument(
        "--ai-mode",
        choices=("mock", "real", "skip"),
        default="mock",
        help="AI 依赖模式：mock=本地模拟，real=启动真实 ai_assistant.py，skip=跳过 AI 相关链路。",
    )
    parser.add_argument("--startup-timeout", type=float, default=60.0, help="服务启动超时时间（秒）。")
    parser.add_argument("--request-timeout", type=float, default=45.0, help="客户端请求超时时间（秒）。")
    parser.add_argument("--max-connections", type=int, default=DEFAULT_MAX_CONNECTIONS, help="HTTP 连接池上限。")
    parser.add_argument("--keep-artifacts", action="store_true", help="保留临时数据库、日志和凭据文件。")
    parser.add_argument("--artifact-dir", default="", help="显式指定产物目录。留空则自动创建临时目录。")
    return parser.parse_args()


async def timed_http_request(
    *,
    action: str,
    user_label: str,
    logger: JsonlLogger,
    recorder: SummaryRecorder,
    request_coro_factory: Callable[[], Any],
    validator: Optional[Callable[[Any], None]] = None,
) -> Any:
    started_at = time.perf_counter()
    try:
        result = await request_coro_factory()
        if validator is not None:
            validator(result)
        latency_ms = (time.perf_counter() - started_at) * 1000.0
        await recorder.record_success(action, latency_ms)
        await logger.write(
            {
                "event": "action",
                "action": action,
                "user": user_label,
                "success": True,
                "latency_ms": round(latency_ms, 2),
            }
        )
        return result
    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.perf_counter() - started_at) * 1000.0
        await recorder.record_failure(action, latency_ms, str(exc))
        await logger.write(
            {
                "event": "action",
                "action": action,
                "user": user_label,
                "success": False,
                "latency_ms": round(latency_ms, 2),
                "error": _truncate(exc),
            }
        )
        raise


def _require_status_success(payload: dict[str, Any]) -> None:
    if str(payload.get("status") or "").lower() != "success":
        raise RuntimeError(_safe_json(payload))


async def login_student(
    client: httpx.AsyncClient,
    *,
    student: SeededStudent,
) -> str:
    response = await client.post(
        "/api/student/login/password",
        data={
            "identifier": student.student_id_number,
            "password": student.password,
            "next": "/dashboard",
        },
    )
    response.raise_for_status()
    payload = response.json()
    _require_status_success(payload)
    access_token = response.cookies.get("access_token")
    if not access_token:
        raise RuntimeError("登录响应未返回 access_token cookie")
    return str(access_token)


async def fetch_html(
    client: httpx.AsyncClient,
    *,
    path: str,
    access_token: str,
) -> str:
    response = await client.get(path, cookies={"access_token": access_token})
    response.raise_for_status()
    body = response.text
    if "<html" not in body.lower():
        raise RuntimeError(f"{path} 未返回 HTML 页面")
    return body


async def fetch_json(
    client: httpx.AsyncClient,
    *,
    path: str,
    access_token: str,
) -> dict[str, Any]:
    response = await client.get(path, cookies={"access_token": access_token})
    response.raise_for_status()
    return response.json()


async def post_json(
    client: httpx.AsyncClient,
    *,
    path: str,
    access_token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = await client.post(path, json=payload, cookies={"access_token": access_token})
    response.raise_for_status()
    return response.json()


async def delete_request(
    client: httpx.AsyncClient,
    *,
    path: str,
    access_token: str,
) -> dict[str, Any]:
    response = await client.delete(path, cookies={"access_token": access_token})
    response.raise_for_status()
    return response.json()


async def submit_assignment(
    client: httpx.AsyncClient,
    *,
    assignment_id: int,
    access_token: str,
    student: SeededStudent,
) -> dict[str, Any]:
    answers_payload = {
        "answers": {
            "q1": f"load-test answer from {student.student_id_number}",
            "q2": "A",
        }
    }
    response = await client.post(
        f"/api/assignments/{assignment_id}/submit",
        data={
            "answers_json": _safe_json(answers_payload),
            "manifest": "",
        },
        cookies={"access_token": access_token},
    )
    response.raise_for_status()
    payload = response.json()
    _require_status_success(payload)
    return payload


async def create_ai_session(
    client: httpx.AsyncClient,
    *,
    class_offering_id: int,
    access_token: str,
) -> str:
    response = await client.post(
        f"/api/ai/chat/session/new/{class_offering_id}",
        cookies={"access_token": access_token},
    )
    response.raise_for_status()
    payload = response.json()
    _require_status_success(payload)
    session_uuid = str((payload.get("session") or {}).get("session_uuid") or "").strip()
    if not session_uuid:
        raise RuntimeError("AI 会话创建成功但 session_uuid 为空")
    return session_uuid


async def run_ai_chat(
    client: httpx.AsyncClient,
    *,
    class_offering_id: int,
    access_token: str,
    session_uuid: str,
    student: SeededStudent,
) -> str:
    response_text_parts: list[str] = []
    async with client.stream(
        "POST",
        "/api/ai/chat",
        data={
            "message": f"请用一句话回应压测学生 {student.student_id_number}",
            "session_uuid": session_uuid,
            "class_offering_id": str(class_offering_id),
            "deep_thinking": "false",
        },
        cookies={"access_token": access_token},
    ) as response:
        response.raise_for_status()
        async for chunk in response.aiter_text():
            if chunk:
                response_text_parts.append(chunk)

    response_text = "".join(response_text_parts).strip()
    if not response_text:
        raise RuntimeError("AI 聊天流式响应为空")
    return response_text


async def poll_private_ai_job(
    client: httpx.AsyncClient,
    *,
    job_id: int,
    access_token: str,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    deadline = time.perf_counter() + timeout_seconds
    while time.perf_counter() < deadline:
        response = await client.get(
            f"/api/message-center/private/ai-jobs/{job_id}",
            cookies={"access_token": access_token},
        )
        response.raise_for_status()
        payload = response.json()
        _require_status_success(payload)
        job = payload.get("job") or {}
        status_value = str(job.get("status") or "")
        if status_value in {"completed", "failed"}:
            return payload
        await asyncio.sleep(0.4)
    raise RuntimeError(f"私信 AI job {job_id} 轮询超时")


async def run_discussion_websocket(
    *,
    ws_url: str,
    class_offering_id: int,
    access_token: str,
    student: SeededStudent,
    mention_assistant: bool,
) -> None:
    uri = f"{ws_url}/ws/{class_offering_id}"
    message_text = f"Load test ping from {student.student_id_number}"
    if mention_assistant:
        message_text += " @助教 请回一个简短确认。"

    async with websockets.connect(
        uri,
        additional_headers=[("Cookie", f"access_token={access_token}")],
        open_timeout=10,
        close_timeout=5,
    ) as websocket:
        for _ in range(3):
            try:
                await asyncio.wait_for(websocket.recv(), timeout=1.5)
            except asyncio.TimeoutError:
                break

        await websocket.send(
            json.dumps(
                {
                    "action": "send_message",
                    "text": message_text,
                },
                ensure_ascii=False,
            )
        )

        try:
            await asyncio.wait_for(websocket.recv(), timeout=3.5)
        except asyncio.TimeoutError:
            pass
        await asyncio.sleep(0.3)


async def execute_student_scenario(
    *,
    client: httpx.AsyncClient,
    ctx: ScenarioContext,
    student: SeededStudent,
    logger: JsonlLogger,
    recorder: SummaryRecorder,
) -> dict[str, Any]:
    user_label = student.student_id_number
    token = ""
    submission_created = False
    selected_contact_identity = ""
    selected_contact_scope: Optional[int] = None

    await logger.write(
        {
            "event": "scenario_start",
            "user": user_label,
            "student_pk": student.student_pk,
        }
    )

    try:
        token = await timed_http_request(
            action="student_login",
            user_label=user_label,
            logger=logger,
            recorder=recorder,
            request_coro_factory=lambda: login_student(client, student=student),
        )
    except Exception:
        await logger.write({"event": "scenario_abort", "user": user_label, "reason": "login_failed"})
        return {"user": user_label, "success": False}

    async def _best_effort(action: str, func: Callable[[], Any], *, skip: bool = False) -> Any:
        if skip:
            await recorder.record_skip(action)
            await logger.write({"event": "action", "action": action, "user": user_label, "success": True, "skipped": True})
            return None
        try:
            return await timed_http_request(
                action=action,
                user_label=user_label,
                logger=logger,
                recorder=recorder,
                request_coro_factory=func,
            )
        except Exception:
            return None

    await _best_effort("dashboard_page", lambda: fetch_html(client, path="/dashboard", access_token=token))
    await _best_effort(
        "classroom_page",
        lambda: fetch_html(client, path=f"/classroom/{ctx.offering.id}", access_token=token),
    )
    await _best_effort(
        "behavior_batch",
        lambda: post_json(
            client,
            path=f"/api/classrooms/{ctx.offering.id}/behavior/batch",
            access_token=token,
            payload={
                "page_key": "classroom_main",
                "events": [
                    {
                        "action_type": "page_enter",
                        "summary_text": "load-test enter classroom",
                        "page_key": "classroom_main",
                        "payload": {"student_id_number": student.student_id_number},
                    },
                    {
                        "action_type": "presence_heartbeat",
                        "summary_text": "load-test heartbeat",
                        "page_key": "classroom_main",
                        "payload": {"focused": True, "visibility_state": "visible", "idle_seconds": 0},
                    },
                ],
            },
        ),
    )
    await _best_effort(
        "classroom_files",
        lambda: fetch_json(client, path=f"/api/courses/{ctx.offering.id}/files", access_token=token),
    )
    await _best_effort(
        "materials_root",
        lambda: fetch_json(client, path=f"/api/classrooms/{ctx.offering.id}/materials", access_token=token),
        skip=ctx.material_target is None,
    )
    if ctx.material_target is not None:
        await _best_effort(
            "materials_folder",
            lambda: fetch_json(
                client,
                path=f"/api/classrooms/{ctx.offering.id}/materials?parent_id={ctx.material_target.root_id}",
                access_token=token,
            ),
        )
        await _best_effort(
            "material_viewer",
            lambda: fetch_html(
                client,
                path=f"/materials/view/{ctx.material_target.file_id}",
                access_token=token,
            ),
        )
    else:
        await recorder.record_skip("materials_folder")
        await recorder.record_skip("material_viewer")

    await _best_effort(
        "discussion_mood",
        lambda: fetch_json(client, path=f"/api/classrooms/{ctx.offering.id}/discussion-mood", access_token=token),
    )
    await _best_effort(
        "message_center_bootstrap",
        lambda: fetch_json(client, path="/api/message-center/bootstrap", access_token=token),
    )
    await _best_effort(
        "message_center_summary",
        lambda: fetch_json(client, path="/api/message-center/summary", access_token=token),
    )
    await _best_effort(
        "message_center_contacts",
        lambda: fetch_json(client, path="/api/message-center/private/contacts", access_token=token),
    )

    if student.index % 3 == 1 and ctx.ai_mode != "skip":
        selected_contact_identity = f"assistant:{ctx.offering.id}"
        selected_contact_scope = ctx.offering.id
    elif student.index % 3 == 2 and len(ctx.students) > 1:
        peer = ctx.students[(student.index) % len(ctx.students)]
        if peer.student_pk != student.student_pk:
            selected_contact_identity = f"student:{peer.student_pk}"
            selected_contact_scope = None
    if not selected_contact_identity:
        selected_contact_identity = f"teacher:{ctx.offering.teacher_id}"
        selected_contact_scope = ctx.offering.id

    private_message_payload = await _best_effort(
        "private_message_send",
        lambda: post_json(
            client,
            path="/api/message-center/private/messages",
            access_token=token,
            payload={
                "contact_identity": selected_contact_identity,
                "class_offering_id": selected_contact_scope,
                "content": f"load-test private message from {student.student_id_number}",
            },
        ),
    )
    if private_message_payload:
        await _best_effort(
            "private_message_conversation",
            lambda: fetch_json(
                client,
                path=(
                    f"/api/message-center/private/conversation?contact={selected_contact_identity}"
                    + (f"&scope={selected_contact_scope}" if selected_contact_scope else "")
                ),
                access_token=token,
            ),
        )
        ai_reply_job = private_message_payload.get("ai_reply_job") or {}
        if ai_reply_job.get("id") is not None:
            await _best_effort(
                "private_message_ai_job",
                lambda: poll_private_ai_job(
                    client,
                    job_id=int(ai_reply_job["id"]),
                    access_token=token,
                ),
            )
        else:
            await recorder.record_skip("private_message_ai_job")
    else:
        await recorder.record_skip("private_message_conversation")
        await recorder.record_skip("private_message_ai_job")

    if ctx.assignment_target is not None:
        assignment_path = (
            f"/exam/take/{ctx.assignment_target.id}"
            if ctx.assignment_target.is_exam
            else f"/assignment/{ctx.assignment_target.id}"
        )
        await _best_effort(
            "assignment_page",
            lambda: fetch_html(client, path=assignment_path, access_token=token),
        )
        submit_payload = await _best_effort(
            "assignment_submit",
            lambda: submit_assignment(
                client,
                assignment_id=ctx.assignment_target.id,
                access_token=token,
                student=student,
            ),
        )
        submission_created = submit_payload is not None
        should_withdraw = submission_created and student.index % 10 == 0
        await _best_effort(
            "assignment_withdraw",
            lambda: delete_request(
                client,
                path=f"/api/assignments/{ctx.assignment_target.id}/withdraw",
                access_token=token,
            ),
            skip=not should_withdraw,
        )
    else:
        await recorder.record_skip("assignment_page")
        await recorder.record_skip("assignment_submit")
        await recorder.record_skip("assignment_withdraw")

    if ctx.ai_mode != "skip":
        session_uuid = await _best_effort(
            "ai_session_create",
            lambda: create_ai_session(
                client,
                class_offering_id=ctx.offering.id,
                access_token=token,
            ),
        )
        await _best_effort(
            "ai_chat_stream",
            lambda: run_ai_chat(
                client,
                class_offering_id=ctx.offering.id,
                access_token=token,
                session_uuid=str(session_uuid or ""),
                student=student,
            ),
            skip=not session_uuid,
        )
    else:
        await recorder.record_skip("ai_session_create")
        await recorder.record_skip("ai_chat_stream")

    await _best_effort(
        "discussion_websocket",
        lambda: run_discussion_websocket(
            ws_url=ctx.ws_url,
            class_offering_id=ctx.offering.id,
            access_token=token,
            student=student,
            mention_assistant=ctx.ai_mode != "skip" and student.index % 5 == 0,
        ),
    )

    await logger.write(
        {
            "event": "scenario_complete",
            "user": user_label,
            "submission_created": submission_created,
            "contact_identity": selected_contact_identity,
        }
    )
    return {"user": user_label, "success": True}


async def collect_server_snapshot(base_url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        health_response = await client.get(f"{base_url}/api/internal/health")
        health_response.raise_for_status()
        metrics_response = await client.get(f"{base_url}/api/internal/metrics")
        metrics_response.raise_for_status()
        return {
            "health": health_response.json(),
            "metrics": metrics_response.json(),
        }


def build_final_report(
    *,
    started_at: str,
    completed_at: str,
    duration_seconds: float,
    ctx: ScenarioContext,
    students: list[SeededStudent],
    credentials_path: str,
    action_summary: dict[str, Any],
    scenario_results: list[dict[str, Any]],
    server_snapshot: dict[str, Any],
    artifact_dir: str,
    kept_artifacts: bool,
    process_logs: dict[str, str],
) -> dict[str, Any]:
    succeeded = sum(1 for item in scenario_results if item.get("success"))
    failed = len(scenario_results) - succeeded
    any_failures = failed > 0 or any(summary.get("failures") for summary in action_summary.values())
    return {
        "status": "partial_failure" if any_failures else "success",
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_seconds": round(duration_seconds, 2),
        "offering": {
            "id": ctx.offering.id,
            "class_name": ctx.offering.class_name,
            "course_name": ctx.offering.course_name,
            "teacher_id": ctx.offering.teacher_id,
            "assignment_count": ctx.offering.assignment_count,
            "material_assignment_count": ctx.offering.material_assignment_count,
            "course_file_count": ctx.offering.course_file_count,
            "chat_log_count": ctx.offering.chat_log_count,
        },
        "load_profile": {
            "student_count": len(students),
            "ai_mode": ctx.ai_mode,
            "material_target": (
                {
                    "root_id": ctx.material_target.root_id,
                    "file_id": ctx.material_target.file_id,
                    "file_name": ctx.material_target.file_name,
                }
                if ctx.material_target
                else None
            ),
            "assignment_target": (
                {
                    "id": ctx.assignment_target.id,
                    "title": ctx.assignment_target.title,
                    "is_exam": ctx.assignment_target.is_exam,
                }
                if ctx.assignment_target
                else None
            ),
        },
        "scenario_summary": {
            "completed": succeeded,
            "failed": failed,
        },
        "credential_sample": [
            {
                "student_id_number": student.student_id_number,
                "password": student.password,
            }
            for student in students[:3]
        ],
        "credentials_path": credentials_path,
        "action_summary": action_summary,
        "server_snapshot": server_snapshot,
        "artifacts": {
            "kept": kept_artifacts,
            "artifact_dir": artifact_dir if kept_artifacts else "",
            "process_logs": process_logs if kept_artifacts else {},
        },
    }


async def main() -> int:
    args = make_args()
    source_db = Path(args.source_db).resolve()
    if not source_db.exists():
        raise FileNotFoundError(f"源数据库不存在: {source_db}")

    started_at_iso = _now_iso()
    run_started_at = time.perf_counter()
    run_id = datetime.now().strftime("%Y%m%d%H%M%S")

    artifact_root: Path
    cleanup_artifact_root = False
    if args.artifact_dir:
        artifact_root = Path(args.artifact_dir).resolve()
        artifact_root.mkdir(parents=True, exist_ok=True)
    else:
        artifact_root = Path(tempfile.mkdtemp(prefix="lanshare-loadtest-"))
        cleanup_artifact_root = not args.keep_artifacts

    cloned_db_path = artifact_root / "isolated" / "classroom.db"
    process_logs_dir = artifact_root / "logs"
    credentials_path = artifact_root / "credentials" / "students.json"
    operation_log_path = artifact_root / "logs" / "operation.jl"

    logger = JsonlLogger(operation_log_path)
    recorder = SummaryRecorder()
    main_process: Optional[StartedProcess] = None
    ai_process: Optional[StartedProcess] = None
    report: Optional[dict[str, Any]] = None

    try:
        await logger.write({"event": "run_start", "source_db": str(source_db), "run_id": run_id})

        clone_sqlite_database(source_db, cloned_db_path)
        offering = select_target_offering(cloned_db_path, args.class_offering_id)
        students = seed_test_students(
            db_path=cloned_db_path,
            offering=offering,
            student_count=max(1, int(args.student_count)),
            run_id=run_id,
        )
        write_seed_credentials(credentials_path, students)

        assignment_target = discover_assignment_target(cloned_db_path, offering.id)
        material_target = discover_material_target(cloned_db_path, offering.id)

        base_env = build_process_env(
            temp_root=artifact_root / "isolated",
            db_path=cloned_db_path,
            host=args.host,
            main_port=int(args.port),
            ai_port=int(args.ai_port),
            ai_mode=args.ai_mode,
        )

        if args.ai_mode == "mock":
            ai_process = start_service_process(
                name="mock-ai",
                command=[sys.executable, str(PROJECT_ROOT / "tools" / "mock_ai_assistant.py")],
                env=base_env,
                log_path=process_logs_dir / "mock-ai.log",
            )
            await wait_for_health(
                f"http://{args.host}:{args.ai_port}/api/internal/health",
                timeout_seconds=float(args.startup_timeout),
            )
        elif args.ai_mode == "real":
            ai_process = start_service_process(
                name="ai-assistant",
                command=[sys.executable, str(PROJECT_ROOT / "ai_assistant.py")],
                env=base_env,
                log_path=process_logs_dir / "ai-assistant.log",
            )
            await wait_for_health(
                f"http://{args.host}:{args.ai_port}/api/internal/health",
                timeout_seconds=float(args.startup_timeout),
            )

        main_process = start_service_process(
            name="main-backend",
            command=[sys.executable, str(PROJECT_ROOT / "main.py")],
            env=base_env,
            log_path=process_logs_dir / "main-backend.log",
        )
        await wait_for_health(
            f"http://{args.host}:{args.port}/api/internal/health",
            timeout_seconds=float(args.startup_timeout),
        )

        base_url = f"http://{args.host}:{args.port}"
        ws_url = f"ws://{args.host}:{args.port}"
        ctx = ScenarioContext(
            base_url=base_url,
            ws_url=ws_url,
            offering=offering,
            students=students,
            assignment_target=assignment_target,
            material_target=material_target,
            ai_mode=args.ai_mode,
        )

        limits = httpx.Limits(
            max_keepalive_connections=max(20, int(args.max_connections)),
            max_connections=max(20, int(args.max_connections)),
        )

        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=float(args.request_timeout),
            limits=limits,
            follow_redirects=False,
        ) as client:
            scenario_tasks = [
                execute_student_scenario(
                    client=client,
                    ctx=ctx,
                    student=student,
                    logger=logger,
                    recorder=recorder,
                )
                for student in students
            ]
            scenario_results = await asyncio.gather(*scenario_tasks)

        await asyncio.sleep(1.0)
        server_snapshot = await collect_server_snapshot(base_url)
        action_summary = await recorder.snapshot()
        completed_at_iso = _now_iso()
        report = build_final_report(
            started_at=started_at_iso,
            completed_at=completed_at_iso,
            duration_seconds=time.perf_counter() - run_started_at,
            ctx=ctx,
            students=students,
            credentials_path=str(credentials_path) if args.keep_artifacts else "",
            action_summary=action_summary,
            scenario_results=scenario_results,
            server_snapshot=server_snapshot,
            artifact_dir=str(artifact_root),
            kept_artifacts=bool(args.keep_artifacts),
            process_logs={
                "main_backend": str((process_logs_dir / "main-backend.log").resolve()),
                **({"ai_service": str(ai_process.log_path.resolve())} if ai_process is not None else {}),
                "operation_log": str(operation_log_path.resolve()),
            },
        )
        print(_safe_json(report))
    finally:
        logger.close()
        await terminate_process(main_process)
        await terminate_process(ai_process)
        if cleanup_artifact_root:
            try:
                shutil.rmtree(artifact_root, ignore_errors=True)
            except Exception:
                pass

    return 0 if report and report.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
