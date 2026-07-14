"""间隔重复复习队列（B3 收尾）。

基于既有"心法检验"数据（learning_material_progress）生成每日复习卡：
- **未通过重试**（最高优先）：做过检验但还没通过的材料；
- **记忆巩固**：已掌握的材料落入 3 / 7 / 21 天复习窗口（简化版间隔重复，
  窗口带宽容差，错过一档自然落入下一档，无需定时任务维护状态）。

纯只读、无新表；展示端挂学生 cockpit"下一步"卡片。未来若引入完整 SM-2，
只需替换 ``REVIEW_WINDOWS`` 判定，调用方不动。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .academic_service import china_now

# (窗口起始天数, 窗口结束天数, 展示文案)。带宽让"错过当天"仍会被提醒。
REVIEW_WINDOWS: tuple[tuple[int, int, str], ...] = (
    (3, 4, "学完 3 天了，快速回顾一遍防遗忘"),
    (7, 9, "一周复习点到了，巩固长期记忆"),
    (21, 25, "三周记忆节点，最后一次加固"),
)
MAX_REVIEW_ITEMS = 3


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "").replace("T", " ").strip())
    except ValueError:
        return None


def _days_since(value: Any, *, now: datetime) -> int | None:
    parsed = _parse_dt(value)
    if parsed is None:
        return None
    return (now.date() - parsed.date()).days


def _load_progress_rows(conn: Any, student_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT p.class_offering_id, p.material_id, p.mastered, p.mastered_at,
               p.mastery_attempts, p.completed, p.updated_at,
               m.name AS material_name, c.name AS course_name
        FROM learning_material_progress p
        JOIN course_materials m ON m.id = p.material_id
        JOIN class_offerings o ON o.id = p.class_offering_id
        JOIN courses c ON c.id = o.course_id
        WHERE p.student_id = ?
          AND p.completed = 1
        ORDER BY p.updated_at DESC
        LIMIT 200
        """,
        (int(student_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def build_review_queue(conn: Any, student_id: int, *, limit: int = MAX_REVIEW_ITEMS) -> list[dict[str, Any]]:
    """返回今天该复习的材料卡（未通过重试优先，其后按记忆窗口）。"""
    now = china_now().replace(tzinfo=None)
    retry_items: list[dict[str, Any]] = []
    window_items: list[dict[str, Any]] = []

    for row in _load_progress_rows(conn, student_id):
        base = {
            "material_id": int(row["material_id"]),
            "material_name": str(row["material_name"] or "课程材料"),
            "course_name": str(row["course_name"] or "课程"),
            "class_offering_id": int(row["class_offering_id"]),
            "link_url": f"/classroom/{int(row['class_offering_id'])}",
        }
        attempts = int(row.get("mastery_attempts") or 0)
        if not int(row.get("mastered") or 0):
            # 只提醒真的尝试过检验的材料，未做过检验的不打扰。
            if attempts > 0:
                retry_items.append({
                    **base,
                    "due_kind": "retry",
                    "reason_label": f"心法检验还没通过（已尝试 {attempts} 次），再战一回",
                })
            continue

        days = _days_since(row.get("mastered_at"), now=now)
        if days is None:
            continue
        for start, end, label in REVIEW_WINDOWS:
            if start <= days <= end:
                window_items.append({
                    **base,
                    "due_kind": f"window_{start}d",
                    "reason_label": label,
                })
                break

    return (retry_items + window_items)[: max(1, limit)]


def build_review_next_steps(conn: Any, student_id: int, *, limit: int = 2) -> list[dict[str, Any]]:
    """转换成 cockpit"下一步"卡片结构（与既有 next_steps 字段完全同构）。"""
    steps = []
    for item in build_review_queue(conn, student_id, limit=limit):
        steps.append({
            "kind": "review",
            "label": "重试检验" if item["due_kind"] == "retry" else "间隔复习",
            "title": item["material_name"],
            "description": item["reason_label"],
            "href": item["link_url"],
            "tone": "danger" if item["due_kind"] == "retry" else "primary",
            "due_label": item["course_name"],
        })
    return steps
