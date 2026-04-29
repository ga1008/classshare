import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt

from ..config import ALGORITHM, SECRET_KEY
from ..dependencies import normalize_ip

PASSWORD_MIN_LENGTH = 8
PASSWORD_POLICY_HINT = "密码至少 8 位，且必须同时包含字母和数字。"
PASSWORD_SETUP_TOKEN_EXPIRE_MINUTES = 15

_PASSWORD_POLICY_PATTERN = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).{%d,}$" % PASSWORD_MIN_LENGTH)


def validate_student_password(password: str) -> Optional[str]:
    normalized = str(password or "")
    if not _PASSWORD_POLICY_PATTERN.match(normalized):
        return PASSWORD_POLICY_HINT
    return None


def build_password_setup_token(
    student_id: int,
    next_path: str,
    flow_type: str,
    reset_request_id: Optional[int] = None,
) -> str:
    issued_at = datetime.now(timezone.utc)
    payload = {
        "purpose": "student_password_setup",
        "student_id": int(student_id),
        "next": next_path,
        "flow_type": flow_type,
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=PASSWORD_SETUP_TOKEN_EXPIRE_MINUTES),
    }
    if reset_request_id is not None:
        payload["reset_request_id"] = int(reset_request_id)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_password_setup_token(token: str) -> Optional[dict]:
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
    if payload.get("purpose") != "student_password_setup":
        return None
    return payload


def get_student_auth_record_by_pk(conn: sqlite3.Connection, student_pk: int):
    return conn.execute(
        """
        SELECT s.*, c.name AS class_name, c.created_by_teacher_id
        FROM students s
        JOIN classes c ON c.id = s.class_id
        WHERE s.id = ?
        """,
        (student_pk,),
    ).fetchone()


def get_student_auth_record_by_identity(
    conn: sqlite3.Connection,
    name: str,
    student_id_number: str,
):
    return conn.execute(
        """
        SELECT s.*, c.name AS class_name, c.created_by_teacher_id
        FROM students s
        JOIN classes c ON c.id = s.class_id
        WHERE s.name = ? AND s.student_id_number = ?
        """,
        (name.strip(), student_id_number.strip()),
    ).fetchone()


def get_student_auth_record_for_password_login(conn: sqlite3.Connection, identifier: str):
    normalized_identifier = str(identifier or "").strip()
    if not normalized_identifier:
        return None, None

    student = conn.execute(
        """
        SELECT s.*, c.name AS class_name, c.created_by_teacher_id
        FROM students s
        JOIN classes c ON c.id = s.class_id
        WHERE s.student_id_number = ?
        """,
        (normalized_identifier,),
    ).fetchone()
    if student:
        return student, "student_id_number"

    matches = conn.execute(
        """
        SELECT s.*, c.name AS class_name, c.created_by_teacher_id
        FROM students s
        JOIN classes c ON c.id = s.class_id
        WHERE s.name = ?
        ORDER BY s.id ASC
        """,
        (normalized_identifier,),
    ).fetchall()

    if not matches:
        return None, None
    if len(matches) > 1:
        raise ValueError("检测到重名学生，请改用学号登录。")
    return matches[0], "name"


def can_student_use_identity_login(student_row) -> bool:
    if not student_row:
        return False
    return not student_row["hashed_password"] or bool(student_row["password_reset_required"])


def parse_user_agent(user_agent: Optional[str]) -> dict:
    ua = str(user_agent or "").strip()
    lowered = ua.lower()

    device_type = "desktop"
    if "ipad" in lowered or "tablet" in lowered or ("android" in lowered and "mobile" not in lowered):
        device_type = "tablet"
    elif any(token in lowered for token in ("iphone", "android", "mobile", "windows phone")):
        device_type = "mobile"

    os_name = "未知系统"
    if "windows nt" in lowered:
        os_name = "Windows"
    elif "iphone" in lowered or "ipad" in lowered or "ios" in lowered:
        os_name = "iOS"
    elif "android" in lowered:
        os_name = "Android"
    elif "mac os x" in lowered or "macintosh" in lowered:
        os_name = "macOS"
    elif "linux" in lowered:
        os_name = "Linux"

    browser_name = "未知浏览器"
    if "edg/" in lowered:
        browser_name = "Edge"
    elif "chrome/" in lowered and "edg/" not in lowered:
        browser_name = "Chrome"
    elif "firefox/" in lowered:
        browser_name = "Firefox"
    elif "safari/" in lowered and "chrome/" not in lowered:
        browser_name = "Safari"
    elif "micromessenger" in lowered:
        browser_name = "微信"

    device_type_label = {
        "desktop": "桌面端",
        "mobile": "移动端",
        "tablet": "平板端",
    }.get(device_type, "未知设备")

    return {
        "device_type": device_type,
        "os_name": os_name,
        "browser_name": browser_name,
        "device_label": f"{device_type_label} / {os_name} / {browser_name}",
        "user_agent": ua,
    }


def record_student_login(
    conn: sqlite3.Connection,
    *,
    student_row,
    login_method: str,
    identifier_type: str,
    identifier_value: str,
    client_ip: Optional[str],
    user_agent: Optional[str],
) -> int:
    device_meta = parse_user_agent(user_agent)
    next_sequence = conn.execute(
        "SELECT COUNT(*) AS total FROM student_login_audit_logs WHERE student_id = ?",
        (student_row["id"],),
    ).fetchone()["total"] + 1

    conn.execute(
        """
        INSERT INTO student_login_audit_logs (
            student_id, class_id, class_name_snapshot, login_sequence, login_method,
            identifier_type, identifier_value, ip_address, user_agent,
            device_type, os_name, browser_name, device_label, logged_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            student_row["id"],
            student_row["class_id"],
            student_row["class_name"],
            next_sequence,
            login_method,
            identifier_type,
            str(identifier_value or "").strip(),
            normalize_ip(client_ip),
            device_meta["user_agent"],
            device_meta["device_type"],
            device_meta["os_name"],
            device_meta["browser_name"],
            device_meta["device_label"],
            datetime.now().isoformat(),
        ),
    )
    return next_sequence


def create_password_reset_request(
    conn: sqlite3.Connection,
    *,
    student_row,
    requester_ip: Optional[str],
    requester_user_agent: Optional[str],
) -> int:
    pending_request = conn.execute(
        """
        SELECT id, status
        FROM student_password_reset_requests
        WHERE student_id = ? AND status IN ('pending', 'approved')
        ORDER BY id DESC
        LIMIT 1
        """,
        (student_row["id"],),
    ).fetchone()
    if pending_request:
        status_value = pending_request["status"]
        if status_value == "pending":
            raise ValueError("您已经提交过找回密码申请，请等待教师审核。")
        raise ValueError("教师已通过您的找回密码申请，请直接使用姓名和学号重新设置密码。")

    device_meta = parse_user_agent(requester_user_agent)
    cursor = conn.execute(
        """
        INSERT INTO student_password_reset_requests (
            student_id, class_id, teacher_id, status,
            request_name, request_student_id_number, request_class_name,
            requester_ip, requester_user_agent, requester_device_type,
            requester_os_name, requester_browser_name, requester_device_label,
            submitted_at
        ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            student_row["id"],
            student_row["class_id"],
            student_row["created_by_teacher_id"],
            student_row["name"],
            student_row["student_id_number"],
            student_row["class_name"],
            normalize_ip(requester_ip),
            device_meta["user_agent"],
            device_meta["device_type"],
            device_meta["os_name"],
            device_meta["browser_name"],
            device_meta["device_label"],
            datetime.now().isoformat(),
        ),
    )
    return cursor.lastrowid


def mark_latest_approved_reset_request_completed(
    conn: sqlite3.Connection,
    student_id: int,
    approved_request_id: Optional[int] = None,
) -> None:
    if approved_request_id is not None:
        conn.execute(
            """
            UPDATE student_password_reset_requests
            SET status = 'completed', completed_at = ?
            WHERE id = ? AND student_id = ? AND status = 'approved'
            """,
            (datetime.now().isoformat(), approved_request_id, student_id),
        )
        return

    conn.execute(
        """
        UPDATE student_password_reset_requests
        SET status = 'completed', completed_at = ?
        WHERE id = (
            SELECT id
            FROM student_password_reset_requests
            WHERE student_id = ? AND status = 'approved'
            ORDER BY reviewed_at DESC, id DESC
            LIMIT 1
        )
        """,
        (datetime.now().isoformat(), student_id),
    )


def list_student_login_history(conn: sqlite3.Connection, student_id: int, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, login_sequence, login_method, identifier_type, identifier_value,
               ip_address, device_label, device_type, os_name, browser_name, user_agent, logged_at
        FROM student_login_audit_logs
        WHERE student_id = ?
        ORDER BY logged_at DESC, id DESC
        LIMIT ?
        """,
        (student_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def build_student_security_summary(conn: sqlite3.Connection, student_id: int) -> dict:
    total_login_row = conn.execute(
        "SELECT COUNT(*) AS total FROM student_login_audit_logs WHERE student_id = ?",
        (student_id,),
    ).fetchone()
    last_login_row = conn.execute(
        """
        SELECT login_sequence, login_method, ip_address, device_label, logged_at
        FROM student_login_audit_logs
        WHERE student_id = ?
        ORDER BY logged_at DESC, id DESC
        LIMIT 1
        """,
        (student_id,),
    ).fetchone()
    return {
        "total_logins": total_login_row["total"] if total_login_row else 0,
        "last_login": dict(last_login_row) if last_login_row else None,
    }
