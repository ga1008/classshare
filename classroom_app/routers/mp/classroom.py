"""小程序课堂 tab 聚合：我的课堂列表 + 各课堂"进行中"计数。

只做一件事——给课堂 tab 一个轻量轮询源（学生/教师各自的课堂 + 进行中
投票数/互动数/求助数/我的举手状态）。进入某课堂后的投票、随堂测、
提问、举手动作全部直调既有 /api/polls/* 与 /api/classroom-interactions/*
（bearer 直通），此处绝不复制业务。

平台签到来自智慧课堂外部同步（无原生签到），小程序不另造。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends

from ...db.connection import get_db_connection
from ...services.dashboard_service import _load_student_offerings, _load_teacher_offerings
from ...services.poll_service import ensure_poll_schema
from .deps import get_current_mp_user

router = APIRouter(prefix="/classroom")


def _count_by_offering(conn: Any, sql: str, params: tuple) -> dict[int, int]:
    counts: dict[int, int] = {}
    for row in conn.execute(sql, params).fetchall():
        counts[int(row["offering_id"])] = int(row["n"] or 0)
    return counts


def build_live_overview(conn: Any, user: dict) -> dict[str, Any]:
    """课堂列表 + 进行中计数（纯读，一次三条聚合查询）。"""
    role = str(user.get("role") or "")
    user_pk = int(user["id"])
    is_teacher = role == "teacher"
    offerings = (
        _load_teacher_offerings(conn, user_pk) if is_teacher else _load_student_offerings(conn, user_pk)
    )
    ids = [int(item["id"]) for item in offerings]
    if not ids:
        return {"role": role, "offerings": [], "live_count": 0}

    ensure_poll_schema(conn)
    placeholders = ",".join("?" for _ in ids)
    now = datetime.now().isoformat(timespec="seconds")

    poll_counts = _count_by_offering(
        conn,
        f"""
        SELECT pa.class_offering_id AS offering_id, COUNT(DISTINCT p.id) AS n
        FROM polls p
        JOIN poll_assignments pa ON pa.poll_id = p.id
        WHERE pa.class_offering_id IN ({placeholders})
          AND p.status = 'active'
          AND (p.deadline_at IS NULL OR p.deadline_at = '' OR p.deadline_at > ?)
        GROUP BY pa.class_offering_id
        """,
        (*ids, now),
    )
    try:
        activity_counts = _count_by_offering(
            conn,
            f"""
            SELECT class_offering_id AS offering_id, COUNT(*) AS n
            FROM classroom_live_activities
            WHERE class_offering_id IN ({placeholders}) AND status = 'active'
            GROUP BY class_offering_id
            """,
            tuple(ids),
        )
        if is_teacher:
            signal_counts = _count_by_offering(
                conn,
                f"""
                SELECT class_offering_id AS offering_id, COUNT(*) AS n
                FROM classroom_live_help_signals
                WHERE class_offering_id IN ({placeholders}) AND status = 'active'
                GROUP BY class_offering_id
                """,
                tuple(ids),
            )
            my_signals: dict[int, str] = {}
        else:
            signal_counts = {}
            my_signals = {
                int(row["class_offering_id"]): str(row["signal_type"] or "")
                for row in conn.execute(
                    f"""
                    SELECT class_offering_id, signal_type
                    FROM classroom_live_help_signals
                    WHERE class_offering_id IN ({placeholders})
                      AND student_id = ? AND status = 'active'
                    """,
                    (*ids, user_pk),
                ).fetchall()
            }
    except Exception as exc:
        # 互动表由课堂页首次访问时 runtime 建表；未建表前视为无进行中互动。
        print(f"[WECHAT_MP] live activity count skipped: {exc}")
        activity_counts, signal_counts, my_signals = {}, {}, {}

    items = []
    for offering in offerings:
        oid = int(offering["id"])
        polls = poll_counts.get(oid, 0)
        activities = activity_counts.get(oid, 0)
        items.append(
            {
                "id": oid,
                "course_name": str(offering.get("course_name") or ""),
                "class_name": str(offering.get("class_name") or ""),
                "teacher_name": str(offering.get("teacher_name") or ""),
                "student_count": int(offering.get("student_count") or 0),
                "active_poll_count": polls,
                "active_activity_count": activities,
                "active_signal_count": signal_counts.get(oid, 0),
                "my_signal": my_signals.get(oid, ""),
                "is_live": polls > 0 or activities > 0,
            }
        )
    items.sort(key=lambda item: (not item["is_live"], item["course_name"]))
    return {
        "role": role,
        "offerings": items,
        "live_count": sum(1 for item in items if item["is_live"]),
    }


@router.get("/live")
def mp_classroom_live(user: dict = Depends(get_current_mp_user)):
    with get_db_connection() as conn:
        data = build_live_overview(conn, user)
        conn.commit()
    return {"success": True, "data": data, "error": None}
