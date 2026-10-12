"""Creator-studio statistics (docs/blog-closed-loop-plan-2026-10-12.md §9.4).

Numbers are kept per author *face* (identity + display mode): pen-name totals
never mix with real-name totals, so a pen-name dashboard cannot be used to
work out who the pen name is. A nightly task (02:00, 24 h recurrence) rolls
the previous days into ``blog_author_daily_stats`` and backfills missing days
of the last 30; today is computed live for one author only.

Definitions
- views      deduplicated reading sessions (one ``blog_post_views`` row per
             reader per post per clock hour), the author's own visits excluded
- readers    distinct readers that day
- completed  sessions that scrolled to the end (``completed = 1``)
- likes      likes on the face's posts by other people
- comments   comments on the face's posts by other people
- followers  new followers of that face (follows are real-named, plan D10)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional

TASK_KIND = "blog.author_daily_stats"
TASK_DEDUPE_KEY = "blog:author-daily-stats"
ROLLUP_HOUR = 2
BACKFILL_DAYS = 30
TREND_DAYS = 30
MODES = ("real_name", "nickname", "anonymous")
# Follow target types per face (pen-name follows are stored as "pen").
FOLLOW_TYPE_BY_MODE = {"real_name": "author", "nickname": "pen"}
_METRICS = ("views", "readers", "completed_reads", "likes", "comments", "followers_delta")


def _day_bounds(day: date) -> tuple[str, str]:
    """Date-only bounds: they order correctly against both "YYYY-MM-DDTHH:MM"
    (ISO, written by the app) and "YYYY-MM-DD HH:MM" (CURRENT_TIMESTAMP defaults)."""
    return day.isoformat(), (day + timedelta(days=1)).isoformat()


def _collect(conn, day: date, author_identity: Optional[str] = None) -> dict[tuple[str, str], dict[str, int]]:
    """All metrics for one day, keyed by (author_identity, display_mode)."""
    start, end = _day_bounds(day)
    author_sql = " AND p.author_identity = ?" if author_identity else ""
    author_params: tuple = (author_identity,) if author_identity else ()
    totals: dict[tuple[str, str], dict[str, int]] = {}

    def add(rows, fields: tuple[str, ...]) -> None:
        for row in rows:
            key = (str(row["author_identity"]), str(row["display_mode"] or "real_name"))
            bucket = totals.setdefault(key, {metric: 0 for metric in _METRICS})
            for field in fields:
                bucket[field] += int(row[field] or 0)

    add(conn.execute(
        f"""
        SELECT p.author_identity, p.author_display_mode AS display_mode,
               COUNT(*) AS views, COUNT(DISTINCT v.viewer_identity) AS readers,
               SUM(CASE WHEN v.completed = 1 THEN 1 ELSE 0 END) AS completed_reads
        FROM blog_post_views v JOIN blog_posts p ON p.id = v.post_id
        WHERE v.first_viewed_at >= ? AND v.first_viewed_at < ? AND v.viewer_identity <> p.author_identity{author_sql}
        GROUP BY p.author_identity, p.author_display_mode
        """,
        (start, end, *author_params),
    ).fetchall(), ("views", "readers", "completed_reads"))
    add(conn.execute(
        f"""
        SELECT p.author_identity, p.author_display_mode AS display_mode, COUNT(*) AS likes
        FROM blog_likes l JOIN blog_posts p ON p.id = l.target_id
        WHERE l.target_type = 'post' AND l.created_at >= ? AND l.created_at < ?
          AND l.user_identity <> p.author_identity{author_sql}
        GROUP BY p.author_identity, p.author_display_mode
        """,
        (start, end, *author_params),
    ).fetchall(), ("likes",))
    add(conn.execute(
        f"""
        SELECT p.author_identity, p.author_display_mode AS display_mode, COUNT(*) AS comments
        FROM blog_comments c JOIN blog_posts p ON p.id = c.post_id
        WHERE c.created_at >= ? AND c.created_at < ? AND c.author_identity <> p.author_identity{author_sql}
        GROUP BY p.author_identity, p.author_display_mode
        """,
        (start, end, *author_params),
    ).fetchall(), ("comments",))
    follow_author_sql = " AND target_key = ?" if author_identity else ""
    add(conn.execute(
        f"""
        SELECT target_key AS author_identity,
               CASE WHEN target_type = 'pen' THEN 'nickname' ELSE 'real_name' END AS display_mode,
               COUNT(*) AS followers_delta
        FROM blog_follows
        WHERE target_type IN ('author', 'pen') AND created_at >= ? AND created_at < ?{follow_author_sql}
        GROUP BY target_key, target_type
        """,
        (start, end, *author_params),
    ).fetchall(), ("followers_delta",))
    return totals


def rollup_day(conn, day: date) -> int:
    """Recompute one day for every author (idempotent upsert). Returns rows written."""
    now = datetime.now().isoformat(timespec="seconds")
    written = 0
    for (identity, mode), metrics in _collect(conn, day).items():
        conn.execute(
            """
            INSERT INTO blog_author_daily_stats (
                author_identity, display_mode, stat_date, views, readers, completed_reads,
                likes, comments, followers_delta, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (author_identity, display_mode, stat_date) DO UPDATE SET
                views = excluded.views, readers = excluded.readers, completed_reads = excluded.completed_reads,
                likes = excluded.likes, comments = excluded.comments, followers_delta = excluded.followers_delta,
                updated_at = excluded.updated_at
            """,
            (identity, mode, day.isoformat(), *(metrics[name] for name in _METRICS), now),
        )
        written += 1
    return written


def run_rollup(conn, *, today: Optional[date] = None) -> dict[str, int]:
    """Yesterday always; earlier days of the backfill window only when missing."""
    today = today or date.today()
    present = {
        str(row["stat_date"])
        for row in conn.execute(
            "SELECT DISTINCT stat_date FROM blog_author_daily_stats WHERE stat_date >= ?",
            ((today - timedelta(days=BACKFILL_DAYS)).isoformat(),),
        ).fetchall()
    }
    yesterday = today - timedelta(days=1)
    days = [today - timedelta(days=offset) for offset in range(1, BACKFILL_DAYS + 1)]
    pending = [day for day in days if day == yesterday or day.isoformat() not in present]
    rows = sum(rollup_day(conn, day) for day in pending)
    return {"days": len(pending), "rows": rows}


def next_run_at(now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now()
    target = now.replace(hour=ROLLUP_HOUR, minute=0, second=0, microsecond=0)
    return target if target > now else target + timedelta(days=1)


def ensure_rollup_task(conn) -> int:
    """Armed at startup; the 24 h recurrence keeps it at ~02:00 (it drifts by
    seconds). On a fresh install the first run is in a few minutes, so the
    studio does not show 30 empty days until the first night."""
    from .scheduled_task_service import schedule_task

    empty = conn.execute("SELECT 1 FROM blog_author_daily_stats LIMIT 1").fetchone() is None
    first_run = datetime.now() + timedelta(minutes=5) if empty else next_run_at()
    return schedule_task(
        conn, task_kind=TASK_KIND, run_at=first_run, payload={}, dedupe_key=TASK_DEDUPE_KEY,
        recurrence_seconds=24 * 3600, title="Blog author daily stats", priority=80, max_attempts=3,
        replace=False,
    )


def handle_rollup_task(task: dict[str, Any]) -> str:
    from ..database import get_db_connection

    with get_db_connection() as conn:
        result = run_rollup(conn)
        conn.commit()
    return f"rolled up {result['days']} day(s), {result['rows']} row(s)"


# --- studio overview ------------------------------------------------------------

def _series(conn, identity: str, mode: str, today: date) -> list[dict[str, Any]]:
    since = (today - timedelta(days=TREND_DAYS - 1)).isoformat()
    stored = {
        str(row["stat_date"]): dict(row)
        for row in conn.execute(
            "SELECT * FROM blog_author_daily_stats WHERE author_identity = ? AND display_mode = ? AND stat_date >= ?",
            (identity, mode, since),
        ).fetchall()
    }
    # Today, and yesterday until the 02:00 rollup has stored it, are live.
    live = {offset: _collect(conn, today - timedelta(days=offset), identity).get((identity, mode), {})
            for offset in (0, 1) if offset == 0 or (today - timedelta(days=offset)).isoformat() not in stored}
    series = []
    for offset in range(TREND_DAYS - 1, -1, -1):
        day = today - timedelta(days=offset)
        source = live[offset] if offset in live else stored.get(day.isoformat(), {})
        series.append({"date": day.isoformat(), **{name: int(source.get(name) or 0) for name in _METRICS}})
    return series


def author_modes(conn, identity: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT author_display_mode FROM blog_posts WHERE author_identity = ? AND status <> 'deleted'",
        (identity,),
    ).fetchall()
    present = {str(row["author_display_mode"] or "real_name") for row in rows}
    return [mode for mode in MODES if mode in present] or ["real_name"]


def follower_count(conn, identity: str, mode: str) -> Optional[int]:
    target_type = FOLLOW_TYPE_BY_MODE.get(mode)
    if target_type is None:
        return None  # Anonymous posts cannot be followed.
    row = conn.execute(
        "SELECT COUNT(*) AS total FROM blog_follows WHERE target_type = ? AND target_key = ?",
        (target_type, identity),
    ).fetchone()
    return int(row["total"] or 0)


def overview(conn, identity: str, mode: str, *, today: Optional[date] = None) -> dict[str, Any]:
    today = today or date.today()
    totals = conn.execute(
        """
        SELECT COUNT(*) AS posts, COALESCE(SUM(view_count), 0) AS views, COALESCE(SUM(like_count), 0) AS likes,
               COALESCE(SUM(comment_count), 0) AS comments
        FROM blog_posts WHERE author_identity = ? AND author_display_mode = ? AND status = 'published'
        """,
        (identity, mode),
    ).fetchone()
    series = _series(conn, identity, mode, today)
    month_views = sum(day["views"] for day in series)
    month_completed = sum(day["completed_reads"] for day in series)
    return {
        "mode": mode,
        "published_posts": int(totals["posts"] or 0),
        "total_views": int(totals["views"] or 0),
        "views_7d": sum(day["views"] for day in series[-7:]),
        # Unknown (None) rather than 0 % when nobody has read anything yet.
        "completion_rate": round(month_completed * 100 / month_views) if month_views else None,
        "likes": int(totals["likes"] or 0),
        "comments": int(totals["comments"] or 0),
        "followers": follower_count(conn, identity, mode),
        "trend": [{"date": day["date"], "views": day["views"]} for day in series],
    }
