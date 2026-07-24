# -*- coding: utf-8 -*-
"""登录"人生一言"投放服务（游戏加载屏式提示）.

设计目标是**登录路径零额外压力**（生产 2c/4GB 服 ~200 并发）：

- 表结构懒建 + 种子包幂等入库，每进程只跑一次；
- 生效池（global ∪ school ∪ department，见 ``schema_life_tips``）按
  ``(school_code, department, audience)`` 做进程内 TTL 缓存，命中时选句
  是纯内存操作，不查库；
- 每次投放返回 3 条候选，前端用 localStorage 过滤最近看过的，"每次登录
  不重样"的成本放在客户端；
- 配图清单来自 ``static/img/life_tips/manifest.json``（构建产物，可缺失），
  缺图时前端回落到渐变背景。

Callers: ``routers/ui_parts/common.py``（登录 JSON 响应）与
``routers/learning.py``（cultivation-profile 接口，覆盖表单登录的 cookie
reveal 路径）。
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

from ..db.connection import get_configured_db_engine
from ..db.schema_life_tips import ensure_life_tip_schema
from .life_tip_seed_data import LIFE_TIP_SEED_PACK, TEACHER_TIP_SEED_PACK

POOL_CACHE_TTL_SECONDS = 600
TIP_CANDIDATE_COUNT = 3
IMAGE_DIR_URL = "/static/img/life_tips"

_seed_lock = threading.Lock()
_seeded = False

_pool_lock = threading.Lock()
_pool_cache: dict[tuple[str, str, str], tuple[float, list[dict[str, Any]]]] = {}

_manifest_lock = threading.Lock()
_manifest_cache: tuple[float, list[dict[str, Any]]] | None = None


def _normalise_tip_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip()


def tip_content_hash(text: str) -> str:
    return hashlib.sha256(_normalise_tip_text(text).encode("utf-8")).hexdigest()


def ensure_life_tip_runtime(conn: Any) -> None:
    """建表 + 种子包入库，进程内只做一次（幂等，可安全并发调用）。"""
    global _seeded
    if _seeded:
        return
    with _seed_lock:
        if _seeded:
            return
        ensure_life_tip_schema(conn)
        _seed_life_tips(conn)
        _seeded = True


def _tip_insert_ignore_sql() -> str:
    """`?` 占位的按引擎 insert-ignore（走连接门面的 qmark→psycopg 转换）。

    注意不要用 ``db.sql.insert_ignore_sql``：它的 postgres 输出是 ``$n``
    占位，只适配迁移注册表那条原生执行路径，与运行时连接门面不兼容
    （门面只转换 ``?``，``$n`` 会导致 "0 placeholders" 报错）。
    """
    base = (
        "INSERT INTO life_tips ("
        "scope, school_code, department, category, audience, "
        "tip_text, source_kind, source_ref, status, weight, content_hash"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    if get_configured_db_engine() == "postgres":
        return base + " ON CONFLICT (content_hash) DO NOTHING"
    return base.replace("INSERT INTO", "INSERT OR IGNORE INTO", 1)


def _seed_life_tips(conn: Any) -> None:
    statement_sql = _tip_insert_ignore_sql()
    for category, tip_text in LIFE_TIP_SEED_PACK:
        conn.execute(
            statement_sql,
            (
                "global", "", "", category, "student",
                tip_text, "seed", "", "active", 1,
                tip_content_hash(tip_text),
            ),
        )
    for category, tip_text in TEACHER_TIP_SEED_PACK:
        conn.execute(
            statement_sql,
            (
                "global", "", "", category, "teacher",
                tip_text, "seed", "", "active", 1,
                tip_content_hash(tip_text),
            ),
        )


def insert_life_tip(
    conn: Any,
    *,
    scope: str,
    tip_text: str,
    category: str,
    school_code: str = "",
    department: str = "",
    audience: str = "student",
    source_kind: str = "manual",
    source_ref: str = "",
    status: str = "active",
) -> bool:
    """插入一条提示（content_hash 去重）。返回是否真的新增。

    公文生成管线（scheduler handler）与后台管理共用这一个入口。
    """
    ensure_life_tip_runtime(conn)
    cursor = conn.execute(
        _tip_insert_ignore_sql(),
        (
            scope, school_code.strip(), department.strip(), category.strip() or "人生大实话",
            audience, tip_text.strip(), source_kind, source_ref.strip(), status, 1,
            tip_content_hash(tip_text),
        ),
    )
    rowcount = getattr(cursor, "rowcount", 0) or 0
    invalidate_pool_cache()
    return rowcount > 0


def invalidate_pool_cache() -> None:
    with _pool_lock:
        _pool_cache.clear()


def _load_pool_from_db(
    conn: Any,
    *,
    school_code: str,
    department: str,
    audience_role: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, category, tip_text, source_ref, weight
        FROM life_tips
        WHERE status = 'active'
          AND weight > 0
          AND audience IN (?, 'all')
          AND (
                scope = 'global'
                OR (scope = 'school' AND school_code = ?)
                OR (scope = 'department' AND school_code = ? AND department = ?)
              )
        """,
        (audience_role, school_code, school_code, department),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "category": row["category"],
            "text": row["tip_text"],
            "source_ref": row["source_ref"] or "",
            "weight": int(row["weight"] or 1),
        }
        for row in rows
    ]


def _get_pool(
    conn: Any,
    *,
    school_code: str,
    department: str,
    audience_role: str,
) -> list[dict[str, Any]]:
    key = (school_code, department, audience_role)
    now = time.monotonic()
    with _pool_lock:
        cached = _pool_cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
    pool = _load_pool_from_db(
        conn,
        school_code=school_code,
        department=department,
        audience_role=audience_role,
    )
    with _pool_lock:
        _pool_cache[key] = (now + POOL_CACHE_TTL_SECONDS, pool)
    return pool


def _load_image_manifest() -> list[dict[str, Any]]:
    """读取配图清单（带 mtime 缓存）。缺失/损坏时返回空列表 → 前端走渐变兜底。"""
    global _manifest_cache
    manifest_path = Path(__file__).resolve().parents[2] / "static" / "img" / "life_tips" / "manifest.json"
    try:
        mtime = manifest_path.stat().st_mtime
    except OSError:
        return []
    with _manifest_lock:
        if _manifest_cache and _manifest_cache[0] == mtime:
            return _manifest_cache[1]
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            images = [
                item for item in (payload.get("images") or [])
                if isinstance(item, dict) and item.get("file")
            ]
        except (OSError, ValueError):
            images = []
        _manifest_cache = (mtime, images)
        return images


def _pick_image_url(category: str) -> Optional[str]:
    images = _load_image_manifest()
    if not images:
        return None
    matched = [item for item in images if category in (item.get("categories") or [])]
    chosen = random.choice(matched or images)
    return f"{IMAGE_DIR_URL}/{chosen['file']}"


def build_login_tip_payload(
    conn: Any,
    *,
    school_code: str = "",
    department: str = "",
    role: str = "student",
) -> Optional[dict[str, Any]]:
    """构造登录响应里的 ``login_tip`` 字段。

    返回 3 条随机候选（前端按 localStorage 去重后取第一条展示）。
    池为空时返回 None，前端回落到纯修为卡。
    """
    try:
        ensure_life_tip_runtime(conn)
        pool = _get_pool(
            conn,
            school_code=(school_code or "").strip(),
            department=(department or "").strip(),
            audience_role=role,
        )
    except Exception as exc:
        # 提示语永远是锦上添花，任何失败都不能影响登录。
        print(f"[LIFE_TIP] 登录提示加载失败: {exc}")
        return None
    if not pool:
        return None
    candidates = _weighted_sample(pool, min(TIP_CANDIDATE_COUNT, len(pool)))
    tips = []
    for tip in candidates:
        payload_tip = {key: value for key, value in tip.items() if key != "weight"}
        tips.append({**payload_tip, "image_url": _pick_image_url(tip["category"])})
    return {"tips": tips}


def _weighted_sample(pool: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """按 weight 无放回加权抽样（好评句更常出现，weight 0 已在查询层剔除）。"""
    remaining = list(pool)
    picked: list[dict[str, Any]] = []
    while remaining and len(picked) < count:
        weights = [max(1, int(tip.get("weight") or 1)) for tip in remaining]
        chosen = random.choices(remaining, weights=weights, k=1)[0]
        picked.append(chosen)
        remaining.remove(chosen)
    return picked


FEEDBACK_WEIGHT_BASE = 1
FEEDBACK_WEIGHT_MIN = 0
FEEDBACK_WEIGHT_MAX = 5


def record_tip_feedback(
    conn: Any,
    *,
    tip_id: int,
    user_role: str,
    user_pk: int,
    verdict: int,
) -> dict[str, Any]:
    """记录"有用/无感"投票（每人每句一票，可改票）并回写权重。

    weight = clamp(1 + Σverdict, 0, 5)；跌到 0 的句子从投放池消失，
    好评句在加权采样里更常被抽中。
    """
    ensure_life_tip_runtime(conn)
    normalized_verdict = 1 if int(verdict) >= 0 else -1
    row = conn.execute(
        "SELECT id FROM life_tips WHERE id = ? LIMIT 1",
        (int(tip_id),),
    ).fetchone()
    if not row:
        raise ValueError("提示不存在")

    # `?` 占位 + 双引擎通用的 ON CONFLICT DO UPDATE（sqlite ≥3.24 同语法）。
    conn.execute(
        """
        INSERT INTO life_tip_feedback (tip_id, user_role, user_pk, verdict)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (tip_id, user_role, user_pk)
        DO UPDATE SET verdict = excluded.verdict, updated_at = CURRENT_TIMESTAMP
        """,
        (int(tip_id), str(user_role or "student"), int(user_pk), normalized_verdict),
    )

    totals = conn.execute(
        "SELECT COALESCE(SUM(verdict), 0) AS score, COUNT(*) AS votes "
        "FROM life_tip_feedback WHERE tip_id = ?",
        (int(tip_id),),
    ).fetchone()
    score = int(totals["score"] or 0)
    weight = max(FEEDBACK_WEIGHT_MIN, min(FEEDBACK_WEIGHT_MAX, FEEDBACK_WEIGHT_BASE + score))
    conn.execute(
        "UPDATE life_tips SET weight = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (weight, int(tip_id)),
    )
    invalidate_pool_cache()
    return {"tip_id": int(tip_id), "weight": weight, "votes": int(totals["votes"] or 0)}


def build_login_tip_payload_for_student(conn: Any, student_id: int) -> Optional[dict[str, Any]]:
    """按学生主键查其学校/系部后构造投放载荷（cultivation-profile 路径用）。"""
    try:
        row = conn.execute(
            "SELECT school_code, department FROM students WHERE id = ?",
            (int(student_id),),
        ).fetchone()
    except Exception as exc:
        print(f"[LIFE_TIP] 学生系部信息查询失败: {exc}")
        return None
    if not row:
        return None
    return build_login_tip_payload(
        conn,
        school_code=row["school_code"] or "",
        department=row["department"] or "",
        role="student",
    )


MANAGE_PAGE_SIZE = 50
ALLOWED_TIP_STATUSES = ("active", "retired", "draft")


def list_life_tips_for_manage(
    conn: Any,
    *,
    scope: str = "",
    category: str = "",
    status: str = "",
    source_kind: str = "",
    audience: str = "",
    keyword: str = "",
    page: int = 1,
) -> dict[str, Any]:
    """治理页列表：筛选 + 分页 + 每句反馈计数。"""
    ensure_life_tip_runtime(conn)
    conditions: list[str] = []
    params: list[Any] = []
    for column, value in (
        ("scope", scope), ("category", category), ("status", status),
        ("source_kind", source_kind), ("audience", audience),
    ):
        if value:
            conditions.append(f"t.{column} = ?")
            params.append(value)
    if keyword.strip():
        conditions.append("(t.tip_text LIKE ? OR t.source_ref LIKE ?)")
        needle = f"%{keyword.strip()}%"
        params.extend([needle, needle])
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    total = int(conn.execute(
        f"SELECT COUNT(*) AS c FROM life_tips t {where_sql}",
        tuple(params),
    ).fetchone()["c"])

    safe_page = max(1, int(page or 1))
    offset = (safe_page - 1) * MANAGE_PAGE_SIZE
    rows = conn.execute(
        f"""
        SELECT t.id, t.scope, t.school_code, t.department, t.category, t.audience,
               t.tip_text, t.source_kind, t.source_ref, t.status, t.weight,
               t.created_at,
               COALESCE(f.up_votes, 0) AS up_votes,
               COALESCE(f.down_votes, 0) AS down_votes
        FROM life_tips t
        LEFT JOIN (
            SELECT tip_id,
                   SUM(CASE WHEN verdict > 0 THEN 1 ELSE 0 END) AS up_votes,
                   SUM(CASE WHEN verdict < 0 THEN 1 ELSE 0 END) AS down_votes
            FROM life_tip_feedback
            GROUP BY tip_id
        ) f ON f.tip_id = t.id
        {where_sql}
        ORDER BY t.id DESC
        LIMIT {MANAGE_PAGE_SIZE} OFFSET {offset}
        """,
        tuple(params),
    ).fetchall()

    return {
        "total": total,
        "page": safe_page,
        "page_size": MANAGE_PAGE_SIZE,
        "items": [dict(row) for row in rows],
    }


def set_life_tip_status(conn: Any, *, tip_id: int, status: str) -> bool:
    """下架/恢复/转正提示（active | retired | draft）。"""
    if status not in ALLOWED_TIP_STATUSES:
        raise ValueError("非法状态")
    ensure_life_tip_runtime(conn)
    cursor = conn.execute(
        "UPDATE life_tips SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, int(tip_id)),
    )
    invalidate_pool_cache()
    return (getattr(cursor, "rowcount", 0) or 0) > 0


def build_login_tip_payload_for_teacher(conn: Any, teacher_id: int) -> Optional[dict[str, Any]]:
    """教师登录提示：按教师所属学校/系部取 audience='teacher' 池。"""
    try:
        row = conn.execute(
            "SELECT school_code, department FROM teachers WHERE id = ?",
            (int(teacher_id),),
        ).fetchone()
    except Exception as exc:
        print(f"[LIFE_TIP] 教师系部信息查询失败: {exc}")
        return None
    if not row:
        return None
    return build_login_tip_payload(
        conn,
        school_code=row["school_code"] or "",
        department=row["department"] or "",
        role="teacher",
    )
