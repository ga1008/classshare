import asyncio
import json
import math
import random
import sys
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import WebSocket

from ..config import CHAT_LOG_DIR
from ..database import get_db_connection

REFRESH_DEBOUNCE_SECONDS = 5
INITIAL_HISTORY_WINDOW_HOURS = 24
HISTORY_PAGE_SIZE = 20
ALIAS_SWITCH_COOLDOWN_SECONDS = 10
ALIAS_SWITCH_LIMIT_PER_ENTRY = 6

TEMPORARY_NAMES = [
    "令狐冲", "杨过", "小龙女", "张无忌", "赵敏", "黄蓉", "郭靖", "周芷若", "乔峰", "段誉",
    "虚竹", "王语嫣", "东方不败", "任我行", "岳不群", "风清扬", "萧峰", "慕容复", "胡斐", "程灵素",
    "达芬奇", "爱因斯坦", "牛顿", "特斯拉", "图灵", "居里夫人", "霍金", "伽利略", "莎士比亚", "贝多芬",
    "莫扎特", "梵高", "毕加索", "亚里士多德", "柏拉图", "苏格拉底", "拿破仑", "凯撒", "哥伦布", "南丁格尔",
    "诸葛亮", "曹操", "刘备", "孙权", "关羽", "张飞", "赵云", "周瑜", "司马懿", "吕布",
    "貂蝉", "王昭君", "西施", "杨玉环", "李白", "杜甫", "白居易", "苏轼", "王安石", "李清照",
    "叶文洁", "罗辑", "章北海", "云天明", "程心", "史强", "汪淼", "丁仪", "申玉菲", "魏成",
    "哈利波特", "赫敏", "邓布利多", "甘道夫", "弗罗多", "阿拉贡", "孙悟空", "唐僧", "猪八戒", "沙僧",
    "马斯克", "乔布斯", "扎克伯格", "马云", "马化腾", "李彦宏", "雷军", "任正非", "董明珠", "王健林",
    "宙斯", "雅典娜", "阿波罗", "奥丁", "索尔", "洛基", "女娲", "伏羲", "神农", "黄帝",
]

_chat_log_schema_ready = False
_migrated_rooms: set[int] = set()
_chat_log_state_lock = threading.Lock()
_chat_log_migration_lock = threading.Lock()
_room_log_locks: Dict[int, asyncio.Lock] = {}
_room_log_locks_guard = asyncio.Lock()


def ensure_chat_log_schema() -> None:
    global _chat_log_schema_ready
    if _chat_log_schema_ready:
        return

    with _chat_log_state_lock:
        if _chat_log_schema_ready:
            return

        with get_db_connection() as conn:
            try:
                conn.execute("ALTER TABLE chat_logs ADD COLUMN logged_at TEXT")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE chat_logs ADD COLUMN message_type TEXT DEFAULT 'text'")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE chat_logs ADD COLUMN emoji_payload_json TEXT")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE chat_logs ADD COLUMN attachments_json TEXT")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE chat_logs ADD COLUMN quote_message_id INTEGER")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE chat_logs ADD COLUMN quote_payload_json TEXT")
            except Exception:
                pass

            conn.execute(
                "UPDATE chat_logs SET logged_at = timestamp "
                "WHERE (logged_at IS NULL OR logged_at = '') AND instr(timestamp, 'T') > 0"
            )
            conn.execute(
                "UPDATE chat_logs SET message_type = 'text' "
                "WHERE message_type IS NULL OR message_type = ''"
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_log_migrations
                (
                    class_offering_id INTEGER PRIMARY KEY,
                    migrated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (class_offering_id) REFERENCES class_offerings (id) ON DELETE CASCADE
                )
                """
            )

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_logs_room_logged_at "
                "ON chat_logs (class_offering_id, logged_at DESC, id DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_logs_sender_logged_at "
                "ON chat_logs (user_role, user_id, logged_at DESC, id DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_logs_room_id "
                "ON chat_logs (class_offering_id, id DESC)"
            )
            conn.execute("DROP INDEX IF EXISTS idx_chat_logs_legacy_dedupe")
            conn.commit()

        _chat_log_schema_ready = True


def get_log_path_for_room(room_id: int) -> Path:
    return CHAT_LOG_DIR / f"classroom_{room_id}.log"


async def get_chat_room_lock(room_id: int) -> asyncio.Lock:
    async with _room_log_locks_guard:
        lock = _room_log_locks.get(room_id)
        if lock is None:
            lock = asyncio.Lock()
            _room_log_locks[room_id] = lock
        return lock


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def format_display_time(logged_at: Optional[str], fallback: str = "") -> str:
    parsed = parse_iso_datetime(logged_at)
    if parsed is not None:
        return parsed.strftime("%H:%M")

    if isinstance(fallback, str) and len(fallback) >= 16 and "T" in fallback:
        return fallback[11:16]
    return str(fallback or "")


def normalize_history_message(raw_message: dict) -> Optional[dict]:
    if not isinstance(raw_message, dict):
        return None

    sender = raw_message.get("sender") or raw_message.get("user_name") or "课堂成员"
    role = raw_message.get("role") or raw_message.get("user_role") or "student"
    message = raw_message.get("message")
    logged_at = raw_message.get("logged_at")

    if message is None:
        return None

    timestamp = raw_message.get("timestamp") or ""
    if not logged_at and isinstance(timestamp, str) and "T" in timestamp:
        logged_at = timestamp

    display_time = format_display_time(logged_at, str(timestamp or ""))
    custom_emojis = raw_message.get("custom_emojis")
    attachments = raw_message.get("attachments")
    quote = raw_message.get("quote")
    return {
        "type": "chat",
        "sender": str(sender),
        "role": str(role),
        "message": str(message),
        "timestamp": display_time,
        "logged_at": logged_at,
        "user_id": raw_message.get("user_id"),
        "message_type": str(raw_message.get("message_type") or "text"),
        "custom_emojis": custom_emojis if isinstance(custom_emojis, list) else [],
        "attachments": attachments if isinstance(attachments, list) else [],
        "quote_message_id": raw_message.get("quote_message_id"),
        "quote": quote if isinstance(quote, dict) else None,
    }


def normalize_legacy_message_for_db(room_id: int, log_file: Path, raw_message: dict) -> Optional[tuple]:
    normalized = normalize_history_message(raw_message)
    if not normalized:
        return None

    logged_at = normalized.get("logged_at")
    if not logged_at:
        fallback_ts = str(raw_message.get("timestamp") or "")
        if len(fallback_ts) == 5 and ":" in fallback_ts:
            try:
                file_time = datetime.fromtimestamp(log_file.stat().st_mtime)
                hour, minute = fallback_ts.split(":")
                logged_at = file_time.replace(
                    hour=int(hour),
                    minute=int(minute),
                    second=0,
                    microsecond=0,
                ).isoformat()
            except Exception:
                logged_at = datetime.fromtimestamp(log_file.stat().st_mtime).isoformat()
        else:
            logged_at = datetime.fromtimestamp(log_file.stat().st_mtime).isoformat()

    return (
        room_id,
        str(normalized.get("user_id") or raw_message.get("user_id") or normalized["sender"]),
        normalized["sender"],
        normalized["role"],
        normalized["message"],
        normalized["timestamp"],
        logged_at,
        str(normalized.get("message_type") or "text"),
        json.dumps(normalized.get("custom_emojis") or [], ensure_ascii=False)
        if normalized.get("custom_emojis")
        else None,
        json.dumps(normalized.get("attachments") or [], ensure_ascii=False)
        if normalized.get("attachments")
        else None,
        normalized.get("quote_message_id"),
        json.dumps(normalized.get("quote") or {}, ensure_ascii=False)
        if normalized.get("quote")
        else None,
    )


def ensure_room_history_migrated(room_id: int) -> None:
    ensure_chat_log_schema()
    if room_id in _migrated_rooms:
        return

    with _chat_log_migration_lock:
        if room_id in _migrated_rooms:
            return

        with get_db_connection() as conn:
            already_migrated = conn.execute(
                "SELECT 1 FROM chat_log_migrations WHERE class_offering_id = ? LIMIT 1",
                (room_id,),
            ).fetchone()
            if already_migrated is not None:
                _migrated_rooms.add(room_id)
                return

        log_file = get_log_path_for_room(room_id)
        if not log_file.exists():
            with get_db_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO chat_log_migrations (class_offering_id, migrated_at) VALUES (?, ?)",
                    (room_id, datetime.now().isoformat()),
                )
                conn.commit()
            _migrated_rooms.add(room_id)
            return

        rows_to_insert: List[tuple] = []
        try:
            with open(log_file, "r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        parsed = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    db_row = normalize_legacy_message_for_db(room_id, log_file, parsed)
                    if db_row:
                        rows_to_insert.append(db_row)
        except Exception as exc:
            print(f"[ERROR] 加载课堂聊天日志失败 (课堂: {room_id}): {exc}", file=sys.stderr)
            _migrated_rooms.add(room_id)
            return

        if rows_to_insert:
            try:
                with get_db_connection() as conn:
                    for row in rows_to_insert:
                        exists = conn.execute(
                            """
                            SELECT 1
                            FROM chat_logs
                            WHERE class_offering_id = ?
                              AND user_id = ?
                              AND user_name = ?
                              AND message = ?
                              AND logged_at = ?
                            LIMIT 1
                            """,
                            (row[0], row[1], row[2], row[4], row[6]),
                        ).fetchone()
                        if exists is None:
                            conn.execute(
                                """
                                INSERT INTO chat_logs
                                (
                                    class_offering_id,
                                    user_id,
                                    user_name,
                                    user_role,
                                    message,
                                    timestamp,
                                    logged_at,
                                    message_type,
                                    emoji_payload_json,
                                    attachments_json,
                                    quote_message_id,
                                    quote_payload_json
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                row,
                            )
                    conn.execute(
                        "INSERT OR REPLACE INTO chat_log_migrations (class_offering_id, migrated_at) VALUES (?, ?)",
                        (room_id, datetime.now().isoformat()),
                    )
                    conn.commit()
            except Exception as exc:
                print(f"[ERROR] 迁移课堂聊天日志失败 (课堂: {room_id}): {exc}", file=sys.stderr)
                return
        else:
            with get_db_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO chat_log_migrations (class_offering_id, migrated_at) VALUES (?, ?)",
                    (room_id, datetime.now().isoformat()),
                )
                conn.commit()

        _migrated_rooms.add(room_id)


def row_to_chat_message(row) -> dict:
    logged_at = row["logged_at"] if "logged_at" in row.keys() else None
    payload = {
        "id": row["id"],
        "type": "chat",
        "user_id": row["user_id"] if "user_id" in row.keys() else None,
        "sender": row["user_name"],
        "role": row["user_role"],
        "message": row["message"],
        "timestamp": format_display_time(logged_at, row["timestamp"]),
        "logged_at": logged_at,
    }
    if "message_type" in row.keys():
        payload["message_type"] = row["message_type"] or "text"
    if "emoji_payload_json" in row.keys() and row["emoji_payload_json"]:
        try:
            payload["custom_emojis"] = json.loads(row["emoji_payload_json"])
        except json.JSONDecodeError:
            payload["custom_emojis"] = []
    if "attachments_json" in row.keys() and row["attachments_json"]:
        try:
            payload["attachments"] = json.loads(row["attachments_json"])
        except json.JSONDecodeError:
            payload["attachments"] = []
    if "quote_message_id" in row.keys() and row["quote_message_id"] is not None:
        payload["quote_message_id"] = row["quote_message_id"]
    if "quote_payload_json" in row.keys() and row["quote_payload_json"]:
        try:
            payload["quote"] = json.loads(row["quote_payload_json"])
        except json.JSONDecodeError:
            payload["quote"] = None
    return payload


def get_initial_history_payload(room_id: int) -> dict:
    ensure_room_history_migrated(room_id)
    cutoff = (datetime.now() - timedelta(hours=INITIAL_HISTORY_WINDOW_HOURS)).isoformat()

    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                id,
                user_id,
                user_name,
                user_role,
                message,
                timestamp,
                logged_at,
                message_type,
                emoji_payload_json,
                attachments_json,
                quote_message_id,
                quote_payload_json
            FROM chat_logs
            WHERE class_offering_id = ?
              AND COALESCE(logged_at, timestamp) >= ?
            ORDER BY id ASC
            """,
            (room_id, cutoff),
        ).fetchall()

        messages = [row_to_chat_message(row) for row in rows]
        oldest_message_id = messages[0]["id"] if messages else None

        if oldest_message_id is None:
            has_more = (
                conn.execute(
                    "SELECT 1 FROM chat_logs WHERE class_offering_id = ? LIMIT 1",
                    (room_id,),
                ).fetchone()
                is not None
            )
        else:
            has_more = (
                conn.execute(
                    "SELECT 1 FROM chat_logs WHERE class_offering_id = ? AND id < ? LIMIT 1",
                    (room_id, oldest_message_id),
                ).fetchone()
                is not None
            )

    return {
        "type": "history",
        "mode": "initial",
        "data": messages,
        "has_more": has_more,
        "oldest_message_id": oldest_message_id,
    }


def get_older_history_payload(room_id: int, before_id: Optional[int]) -> dict:
    ensure_room_history_migrated(room_id)

    with get_db_connection() as conn:
        if before_id is None:
            rows = conn.execute(
                """
                SELECT
                    id,
                    user_id,
                    user_name,
                    user_role,
                    message,
                    timestamp,
                    logged_at,
                    message_type,
                    emoji_payload_json,
                    attachments_json,
                    quote_message_id,
                    quote_payload_json
                FROM chat_logs
                WHERE class_offering_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (room_id, HISTORY_PAGE_SIZE),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                    id,
                    user_id,
                    user_name,
                    user_role,
                    message,
                    timestamp,
                    logged_at,
                    message_type,
                    emoji_payload_json,
                    attachments_json,
                    quote_message_id,
                    quote_payload_json
                FROM chat_logs
                WHERE class_offering_id = ?
                  AND id < ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (room_id, before_id, HISTORY_PAGE_SIZE),
            ).fetchall()

        messages = [row_to_chat_message(row) for row in reversed(rows)]
        oldest_message_id = messages[0]["id"] if messages else before_id

        if messages:
            has_more = (
                conn.execute(
                    "SELECT 1 FROM chat_logs WHERE class_offering_id = ? AND id < ? LIMIT 1",
                    (room_id, oldest_message_id),
                ).fetchone()
                is not None
            )
        else:
            has_more = False

    return {
        "type": "history",
        "mode": "older",
        "data": messages,
        "has_more": has_more,
        "oldest_message_id": oldest_message_id,
    }


async def load_initial_history_payload(room_id: int) -> dict:
    return await asyncio.to_thread(get_initial_history_payload, room_id)


async def load_older_history_payload(room_id: int, before_id: Optional[int]) -> dict:
    return await asyncio.to_thread(get_older_history_payload, room_id, before_id)


def _save_chat_message_sync(room_id: int, message: dict) -> dict:
    ensure_room_history_migrated(room_id)

    sender = str(message.get("sender") or "课堂成员")
    role = str(message.get("role") or "student")
    content = str(message.get("message") or "")
    timestamp = str(message.get("timestamp") or "")
    logged_at = str(message.get("logged_at") or datetime.now().isoformat())
    user_id = str(message.get("user_id") or sender)
    message_type = str(message.get("message_type") or "text")
    emoji_payload = message.get("custom_emojis") or []
    emoji_payload_json = json.dumps(emoji_payload, ensure_ascii=False) if emoji_payload else None
    attachments = message.get("attachments") or []
    attachments_json = json.dumps(attachments, ensure_ascii=False) if attachments else None
    quote = message.get("quote") or None
    quote_message_id = message.get("quote_message_id")
    quote_payload_json = json.dumps(quote, ensure_ascii=False) if isinstance(quote, dict) and quote else None

    try:
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO chat_logs
                (
                    class_offering_id,
                    user_id,
                    user_name,
                    user_role,
                    message,
                    timestamp,
                    logged_at,
                    message_type,
                    emoji_payload_json,
                    attachments_json,
                    quote_message_id,
                    quote_payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    room_id,
                    user_id,
                    sender,
                    role,
                    content,
                    timestamp,
                    logged_at,
                    message_type,
                    emoji_payload_json,
                    attachments_json,
                    quote_message_id,
                    quote_payload_json,
                ),
            )
            conn.commit()
            message_id = cursor.lastrowid
    except Exception as exc:
        print(f"[ERROR] 保存课堂聊天记录到数据库失败 (课堂: {room_id}): {exc}", file=sys.stderr)
        raise

    CHAT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = get_log_path_for_room(room_id)
    try:
        with open(log_file, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(message, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"[ERROR] 保存课堂聊天记录到文件失败 (课堂: {room_id}): {exc}", file=sys.stderr)

    response = {
        "id": message_id,
        "type": "chat",
        "user_id": user_id,
        "sender": sender,
        "role": role,
        "message": content,
        "timestamp": timestamp,
        "logged_at": logged_at,
        "message_type": message_type,
    }
    if emoji_payload:
        response["custom_emojis"] = emoji_payload
    if attachments:
        response["attachments"] = attachments
    if quote_message_id is not None:
        response["quote_message_id"] = quote_message_id
    if quote_payload_json:
        response["quote"] = quote
    return response


async def save_chat_message(room_id: int, message: dict) -> dict:
    room_lock = await get_chat_room_lock(room_id)
    async with room_lock:
        return await asyncio.to_thread(_save_chat_message_sync, room_id, dict(message))

class MultiRoomConnectionManager:
    def __init__(self):
        self.rooms: Dict[int, Dict[str, WebSocket]] = {}
        self.connection_state: Dict[str, dict] = {}
        self.room_participants: Dict[int, Dict[str, set[str]]] = {}
        self.room_user_info: Dict[int, Dict[str, dict]] = {}
        self.room_display_names: Dict[int, Dict[str, str]] = {}
        self.room_alias_sessions: Dict[int, Dict[str, dict]] = {}
        self.disconnect_tasks: Dict[Tuple[int, str], asyncio.Task] = {}
        self.name_pool = TEMPORARY_NAMES.copy()

    def get_room_id(self, websocket: WebSocket) -> int:
        return int(websocket.path_params.get("class_offering_id", 0))

    def ensure_room(self, room_id: int) -> None:
        self.rooms.setdefault(room_id, {})
        self.room_participants.setdefault(room_id, {})
        self.room_user_info.setdefault(room_id, {})
        self.room_display_names.setdefault(room_id, {})
        self.room_alias_sessions.setdefault(room_id, {})

    def get_display_name(self, room_id: int, participant_key: str, fallback: str = "课堂成员") -> str:
        return self.room_display_names.get(room_id, {}).get(participant_key, fallback)

    def get_available_aliases(self, room_id: int, participant_key: str) -> List[str]:
        current_name = self.room_display_names.get(room_id, {}).get(participant_key)
        occupied_names = {
            name
            for key, name in self.room_display_names.get(room_id, {}).items()
            if key != participant_key
        }
        return [
            name
            for name in self.name_pool
            if name not in occupied_names and name != current_name
        ]

    def ensure_alias_session(self, room_id: int, participant_key: str) -> dict:
        self.ensure_room(room_id)
        session = self.room_alias_sessions[room_id].get(participant_key)
        if session is None:
            session = {
                "switches_used": 0,
                "last_switched_at": None,
            }
            self.room_alias_sessions[room_id][participant_key] = session
        return session

    def get_alias_next_switch_at(self, last_switched_at: Optional[str]) -> Optional[datetime]:
        switched_at = parse_iso_datetime(last_switched_at)
        if switched_at is None:
            return None
        return switched_at + timedelta(seconds=ALIAS_SWITCH_COOLDOWN_SECONDS)

    def get_alias_cooldown_remaining_seconds(self, last_switched_at: Optional[str]) -> int:
        next_switch_at = self.get_alias_next_switch_at(last_switched_at)
        if next_switch_at is None:
            return 0

        now = datetime.now(next_switch_at.tzinfo) if next_switch_at.tzinfo else datetime.now()
        remaining_seconds = (next_switch_at - now).total_seconds()
        if remaining_seconds <= 0:
            return 0
        return math.ceil(remaining_seconds)

    def get_alias_switch_status(self, room_id: int, participant_key: str, user: dict) -> dict:
        is_student = user.get("role") == "student"
        session = self.ensure_alias_session(room_id, participant_key) if is_student else {
            "switches_used": 0,
            "last_switched_at": None,
        }
        available_aliases = self.get_available_aliases(room_id, participant_key) if is_student else []
        switches_used = int(session.get("switches_used") or 0)
        switches_remaining = max(ALIAS_SWITCH_LIMIT_PER_ENTRY - switches_used, 0)
        cooldown_remaining_seconds = self.get_alias_cooldown_remaining_seconds(session.get("last_switched_at"))
        next_switch_at = self.get_alias_next_switch_at(session.get("last_switched_at"))

        reason = None
        can_switch = False
        if is_student:
            can_switch = bool(available_aliases) and switches_remaining > 0 and cooldown_remaining_seconds <= 0
            if switches_remaining <= 0:
                reason = "limit_reached"
            elif cooldown_remaining_seconds > 0:
                reason = "cooldown"
            elif not available_aliases:
                reason = "no_alias_available"

        return {
            "can_switch": can_switch,
            "reason": reason,
            "available_aliases": available_aliases,
            "available_alias_count": len(available_aliases),
            "switches_used": switches_used,
            "switches_remaining": switches_remaining,
            "cooldown_remaining_seconds": cooldown_remaining_seconds,
            "next_switch_at": next_switch_at.isoformat() if next_switch_at is not None else None,
        }

    def build_fallback_alias(self, room_id: int) -> str:
        occupied_names = set(self.room_display_names.get(room_id, {}).values())
        index = 1
        while True:
            candidate = f"同学{index:03d}"
            if candidate not in occupied_names:
                return candidate
            index += 1

    def assign_display_name(self, room_id: int, participant_key: str, user: dict) -> str:
        if user.get("role") == "teacher":
            display_name = str(user.get("name") or "教师")
            self.room_display_names[room_id][participant_key] = display_name
            return display_name

        self.ensure_alias_session(room_id, participant_key)
        existing_name = self.room_display_names[room_id].get(participant_key)
        if existing_name:
            return existing_name

        available_aliases = self.get_available_aliases(room_id, participant_key)
        chosen_name = random.choice(available_aliases) if available_aliases else self.build_fallback_alias(room_id)
        self.room_display_names[room_id][participant_key] = chosen_name
        return chosen_name

    def build_display_name_payload(self, room_id: int, participant_key: str, user: dict) -> dict:
        display_name = self.get_display_name(room_id, participant_key, str(user.get("name") or "课堂成员"))
        alias_status = self.get_alias_switch_status(room_id, participant_key, user)
        return {
            "type": "user_display_name",
            "display_name": display_name,
            "can_switch_alias": alias_status["can_switch"],
            "remaining_alias_count": alias_status["available_alias_count"],
            "available_alias_count": alias_status["available_alias_count"],
            "switch_limit": ALIAS_SWITCH_LIMIT_PER_ENTRY,
            "switches_used": alias_status["switches_used"],
            "switches_remaining": alias_status["switches_remaining"],
            "cooldown_seconds": ALIAS_SWITCH_COOLDOWN_SECONDS,
            "cooldown_remaining_seconds": alias_status["cooldown_remaining_seconds"],
            "next_switch_at": alias_status["next_switch_at"],
            "switch_block_reason": alias_status["reason"],
            "is_temporary_alias": user.get("role") == "student",
        }

    async def send_display_name_payload(self, room_id: int, connection_id: str) -> None:
        websocket = self.rooms.get(room_id, {}).get(connection_id)
        connection_info = self.connection_state.get(connection_id)
        if not websocket or not connection_info:
            return

        payload = self.build_display_name_payload(
            room_id,
            connection_info["participant_key"],
            connection_info["user"],
        )
        await websocket.send_text(json.dumps(payload, ensure_ascii=False))

    async def broadcast_alias_states(self, room_id: int) -> None:
        for connection_id in list(self.rooms.get(room_id, {}).keys()):
            try:
                await self.send_display_name_payload(room_id, connection_id)
            except Exception as exc:
                print(f"[CHAT] 发送代号状态失败 (课堂: {room_id}, 连接: {connection_id}): {exc}", file=sys.stderr)

    async def connect(self, websocket: WebSocket, user: dict) -> str:
        await websocket.accept()
        room_id = self.get_room_id(websocket)
        if room_id == 0:
            await websocket.close(reason="Invalid room ID")
            return ""

        participant_key = str(user["id"])
        reconnect_key = (room_id, participant_key)
        is_reconnect = reconnect_key in self.disconnect_tasks
        if is_reconnect:
            task = self.disconnect_tasks.pop(reconnect_key)
            task.cancel()

        self.ensure_room(room_id)
        was_present = bool(self.room_participants[room_id].get(participant_key))
        self.room_user_info[room_id][participant_key] = dict(user)
        self.assign_display_name(room_id, participant_key, user)

        connection_id = uuid.uuid4().hex
        self.rooms[room_id][connection_id] = websocket
        self.room_participants[room_id].setdefault(participant_key, set()).add(connection_id)
        self.connection_state[connection_id] = {
            "room_id": room_id,
            "participant_key": participant_key,
            "user": dict(user),
        }

        await self.send_display_name_payload(room_id, connection_id)
        await websocket.send_text(json.dumps(await load_initial_history_payload(room_id), ensure_ascii=False))
        await self.broadcast_user_list(room_id)
        await self.broadcast_alias_states(room_id)

        if not was_present and not is_reconnect:
            display_name = self.get_display_name(room_id, participant_key, str(user.get("name") or "课堂成员"))
            await self.broadcast(
                room_id,
                json.dumps({"type": "system", "message": f"{display_name} 加入了课堂。"}, ensure_ascii=False),
            )

        return connection_id

    async def disconnect(self, connection_id: str) -> None:
        connection_info = self.connection_state.pop(connection_id, None)
        if not connection_info:
            return

        room_id = connection_info["room_id"]
        participant_key = connection_info["participant_key"]
        user = connection_info["user"]

        self.rooms.get(room_id, {}).pop(connection_id, None)

        participant_connections = self.room_participants.get(room_id, {}).get(participant_key)
        if participant_connections is not None:
            participant_connections.discard(connection_id)
            if participant_connections:
                return
            self.room_participants[room_id].pop(participant_key, None)

        user_name = self.get_display_name(room_id, participant_key, str(user.get("name") or "课堂成员"))
        await self.broadcast_user_list(room_id)

        task = asyncio.create_task(
            self.delayed_leave_broadcast(room_id, participant_key, user_name)
        )
        self.disconnect_tasks[(room_id, participant_key)] = task

    async def delayed_leave_broadcast(self, room_id: int, participant_key: str, user_name: str) -> None:
        try:
            await asyncio.sleep(REFRESH_DEBOUNCE_SECONDS)

            if self.room_participants.get(room_id, {}).get(participant_key):
                return

            self.room_user_info.get(room_id, {}).pop(participant_key, None)
            self.room_display_names.get(room_id, {}).pop(participant_key, None)
            self.room_alias_sessions.get(room_id, {}).pop(participant_key, None)

            await self.broadcast(
                room_id,
                json.dumps({"type": "system", "message": f"{user_name} 离开了课堂。"}, ensure_ascii=False),
            )
            await self.broadcast_alias_states(room_id)
        except asyncio.CancelledError:
            pass
        finally:
            self.disconnect_tasks.pop((room_id, participant_key), None)

    async def switch_temporary_name(self, room_id: int, participant_key: str) -> dict:
        user = self.room_user_info.get(room_id, {}).get(participant_key)
        if not user or user.get("role") != "student":
            return {
                "success": False,
                "reason": "forbidden",
                "message": "只有学生可以切换代号。",
                "alias_state": self.build_display_name_payload(room_id, participant_key, user or {"name": "课堂成员", "role": "student"}),
            }

        alias_status = self.get_alias_switch_status(room_id, participant_key, user)
        if not alias_status["can_switch"]:
            if alias_status["reason"] == "cooldown":
                message = f"{alias_status['cooldown_remaining_seconds']}s 后才能再次切换代号。"
            elif alias_status["reason"] == "limit_reached":
                message = f"本次进入研讨室最多只能切换 {ALIAS_SWITCH_LIMIT_PER_ENTRY} 次代号。"
            else:
                message = "当前没有可用的新代号。"

            return {
                "success": False,
                "reason": alias_status["reason"],
                "message": message,
                "alias_state": self.build_display_name_payload(room_id, participant_key, user),
            }

        previous_name = self.get_display_name(room_id, participant_key, "课堂成员")
        new_name = random.choice(alias_status["available_aliases"])
        session = self.ensure_alias_session(room_id, participant_key)
        session["switches_used"] = int(session.get("switches_used") or 0) + 1
        session["last_switched_at"] = datetime.now().isoformat()
        self.room_display_names[room_id][participant_key] = new_name
        return {
            "success": True,
            "reason": None,
            "message": f"已切换为 {new_name}",
            "previous_name": previous_name,
            "new_name": new_name,
            "alias_state": self.build_display_name_payload(room_id, participant_key, user),
        }

    async def broadcast(self, room_id: int, message: str) -> None:
        for connection_id, websocket in list(self.rooms.get(room_id, {}).items()):
            try:
                await websocket.send_text(message)
            except Exception as exc:
                print(f"[CHAT] 广播失败 (课堂: {room_id}, 连接: {connection_id}): {exc}", file=sys.stderr)

    async def broadcast_user_list(self, room_id: int) -> None:
        participants = []
        for participant_key in self.room_participants.get(room_id, {}):
            user = self.room_user_info.get(room_id, {}).get(participant_key)
            if not user:
                continue
            participants.append({
                "name": self.get_display_name(room_id, participant_key, str(user.get("name") or "课堂成员")),
                "role": user.get("role", "student"),
            })

        participants.sort(key=lambda item: (item["role"] != "teacher", item["name"]))
        await self.broadcast(
            room_id,
            json.dumps({"type": "user_list", "data": participants}, ensure_ascii=False),
        )


manager = MultiRoomConnectionManager()
