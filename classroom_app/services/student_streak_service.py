"""学生学习连击（streak）。

记录学生"连续活跃天数"：当天在平台上有任何被行为追踪记录的动作即算活跃。
- 同一天重复活跃幂等；昨天活跃过则 +1；中断则从 1 重新计。
- ``get_student_streak`` 读取时会把"已断签"折算为 0，写入端无需定时任务修剪。

挂钩点：behavior_tracking 批量落库时 best-effort 调 ``record_student_activity``，
失败绝不影响行为写入主链路。展示端：学生 cockpit 统计卡。
未来的事件总线/成就系统可直接复用这两个纯函数接口。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .academic_service import china_now

_SCHEMA_READY = False


def ensure_streak_schema(conn: Any) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    # 主键即 student_id，无自增列，sqlite/postgres 可共用同一份 DDL。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS student_activity_streaks (
            student_id INTEGER PRIMARY KEY,
            current_streak INTEGER NOT NULL DEFAULT 0,
            longest_streak INTEGER NOT NULL DEFAULT 0,
            last_active_date TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    # 活跃日志：每个活跃日一行，是补签修复与后续分析的事实来源。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS student_activity_days (
            student_id INTEGER NOT NULL,
            active_date TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'activity',
            PRIMARY KEY (student_id, active_date)
        )
        """
    )
    _SCHEMA_READY = True


def _today() -> date:
    return china_now().date()


def _log_active_day(conn: Any, student_id: int, day_text: str, *, source: str = "activity") -> None:
    existing = conn.execute(
        "SELECT 1 FROM student_activity_days WHERE student_id = ? AND active_date = ?",
        (int(student_id), day_text),
    ).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO student_activity_days (student_id, active_date, source) VALUES (?, ?, ?)",
            (int(student_id), day_text, source),
        )


def record_student_activity(conn: Any, student_id: int, *, active_date: date | None = None) -> dict[str, Any]:
    """记一次活跃并返回最新连击。同日幂等，跨日按连续性累计/重置。
    返回含 ``is_new_day``：当天首次活跃为 True（供积分等下游一次性奖励用）。"""
    ensure_streak_schema(conn)
    today = active_date or _today()
    today_text = today.isoformat()
    now_iso = china_now().replace(tzinfo=None).isoformat(timespec="seconds")

    row = conn.execute(
        "SELECT current_streak, longest_streak, last_active_date FROM student_activity_streaks WHERE student_id = ?",
        (int(student_id),),
    ).fetchone()

    if row is None:
        conn.execute(
            """
            INSERT INTO student_activity_streaks (student_id, current_streak, longest_streak, last_active_date, updated_at)
            VALUES (?, 1, 1, ?, ?)
            """,
            (int(student_id), today_text, now_iso),
        )
        _log_active_day(conn, student_id, today_text)
        return {"current_streak": 1, "longest_streak": 1, "last_active_date": today_text, "is_new_day": True}

    last_text = str(row["last_active_date"] or "")
    if last_text == today_text:
        return {
            "current_streak": int(row["current_streak"]),
            "longest_streak": int(row["longest_streak"]),
            "last_active_date": last_text,
            "is_new_day": False,
        }

    yesterday_text = (today - timedelta(days=1)).isoformat()
    if last_text == yesterday_text:
        current = int(row["current_streak"]) + 1
    else:
        current = 1
    longest = max(current, int(row["longest_streak"]))
    conn.execute(
        """
        UPDATE student_activity_streaks
        SET current_streak = ?, longest_streak = ?, last_active_date = ?, updated_at = ?
        WHERE student_id = ?
        """,
        (current, longest, today_text, now_iso, int(student_id)),
    )
    _log_active_day(conn, student_id, today_text)
    return {"current_streak": current, "longest_streak": longest, "last_active_date": today_text, "is_new_day": True}


def _recompute_streak_from_log(conn: Any, student_id: int, *, today: date) -> dict[str, Any]:
    """从活跃日志重算 current/longest（补签后调用，保证与日志一致）。"""
    rows = conn.execute(
        "SELECT active_date FROM student_activity_days WHERE student_id = ? ORDER BY active_date",
        (int(student_id),),
    ).fetchall()
    days = []
    for row in rows:
        try:
            days.append(date.fromisoformat(str(row["active_date"])))
        except ValueError:
            continue
    if not days:
        return {"current_streak": 0, "longest_streak": 0, "last_active_date": ""}

    longest = run = 1
    for prev, cur in zip(days, days[1:]):
        run = run + 1 if (cur - prev).days == 1 else 1
        longest = max(longest, run)

    last_day = days[-1]
    current = 0
    if (today - last_day).days <= 1:
        current = 1
        cursor = last_day
        day_set = {d for d in days}
        while (cursor - timedelta(days=1)) in day_set:
            cursor -= timedelta(days=1)
            current += 1

    now_iso = china_now().replace(tzinfo=None).isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE student_activity_streaks
        SET current_streak = ?, longest_streak = ?, last_active_date = ?, updated_at = ?
        WHERE student_id = ?
        """,
        (current, max(longest, current), last_day.isoformat(), now_iso, int(student_id)),
    )
    return {
        "current_streak": current,
        "longest_streak": max(longest, current),
        "last_active_date": last_day.isoformat(),
    }


def repair_missed_day(conn: Any, student_id: int, *, today: date | None = None, max_lookback_days: int = 7) -> dict[str, Any]:
    """补签：把最近一个缺勤日补进日志并重算连击（商店"补签卡"的效果函数）。

    只补"能把两段连击接起来"的最近缺口；近 ``max_lookback_days`` 天内无可补缺口
    时返回 {"repaired": False}，调用方应拒绝扣费。
    """
    ensure_streak_schema(conn)
    today = today or _today()
    rows = conn.execute(
        "SELECT active_date FROM student_activity_days WHERE student_id = ? AND active_date >= ? ORDER BY active_date",
        (int(student_id), (today - timedelta(days=max_lookback_days)).isoformat()),
    ).fetchall()
    day_set = set()
    for row in rows:
        try:
            day_set.add(date.fromisoformat(str(row["active_date"])))
        except ValueError:
            continue
    if not day_set:
        return {"repaired": False, "reason": "近期没有活跃记录，无从补起"}

    # 从最近往回找第一个"前后都活跃、中间缺一天"的缺口。
    for offset in range(1, max_lookback_days):
        candidate = today - timedelta(days=offset)
        if candidate in day_set:
            continue
        if (candidate + timedelta(days=1)) in day_set and (candidate - timedelta(days=1)) in day_set:
            _log_active_day(conn, student_id, candidate.isoformat(), source="repair_card")
            streak = _recompute_streak_from_log(conn, student_id, today=today)
            return {"repaired": True, "repaired_date": candidate.isoformat(), **streak}
    return {"repaired": False, "reason": "近 7 天没有可以缝合的断签缺口"}


def get_student_streak(conn: Any, student_id: int) -> dict[str, Any]:
    """读取连击；断签（最后活跃早于昨天）折算 current=0，longest 保留。"""
    ensure_streak_schema(conn)
    row = conn.execute(
        "SELECT current_streak, longest_streak, last_active_date FROM student_activity_streaks WHERE student_id = ?",
        (int(student_id),),
    ).fetchone()
    if row is None:
        return {"current_streak": 0, "longest_streak": 0, "last_active_date": "", "active_today": False}

    today = _today()
    last_text = str(row["last_active_date"] or "")
    current = int(row["current_streak"])
    if last_text not in (today.isoformat(), (today - timedelta(days=1)).isoformat()):
        current = 0
    return {
        "current_streak": current,
        "longest_streak": int(row["longest_streak"]),
        "last_active_date": last_text,
        "active_today": last_text == today.isoformat(),
    }
