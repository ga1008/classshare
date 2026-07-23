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
from ..db.sql import insert_ignore_sql
from .life_tip_seed_data import LIFE_TIP_SEED_PACK

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


def _seed_life_tips(conn: Any) -> None:
    engine = get_configured_db_engine()
    statement = insert_ignore_sql(
        engine,
        "life_tips",
        (
            "scope", "school_code", "department", "category", "audience",
            "tip_text", "source_kind", "source_ref", "status", "weight",
            "content_hash",
        ),
        conflict_columns=("content_hash",),
    )
    for category, tip_text in LIFE_TIP_SEED_PACK:
        conn.execute(
            statement.sql,
            (
                "global", "", "", category, "student",
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
    engine = get_configured_db_engine()
    statement = insert_ignore_sql(
        engine,
        "life_tips",
        (
            "scope", "school_code", "department", "category", "audience",
            "tip_text", "source_kind", "source_ref", "status", "weight",
            "content_hash",
        ),
        conflict_columns=("content_hash",),
    )
    cursor = conn.execute(
        statement.sql,
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
        SELECT id, category, tip_text, source_ref
        FROM life_tips
        WHERE status = 'active'
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
    candidates = random.sample(pool, min(TIP_CANDIDATE_COUNT, len(pool)))
    tips = []
    for tip in candidates:
        tips.append({**tip, "image_url": _pick_image_url(tip["category"])})
    return {"tips": tips}


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
