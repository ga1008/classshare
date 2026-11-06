# -*- coding: utf-8 -*-
"""登录"人生一言"投放服务（游戏加载屏式提示）.

设计目标是**登录路径零额外压力**（生产 2c/4GB 服 ~200 并发）：

- 启动期自有事务幂等补种，提交并归还连接后才发布进程就绪；
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
from .life_tip_seed_data import (
    LIFE_TIP_SEED_PACK,
    LIFE_TIP_SEED_SOURCE_REFS,
    TEACHER_TIP_SEED_PACK,
)

POOL_CACHE_TTL_SECONDS = 600
TIP_CANDIDATE_COUNT = 3
IMAGE_DIR_URL = "/static/img/life_tips"

_seed_lock = threading.Lock()
_seeded = False
_ready_db_key: tuple[str, str] | None = None
_SEED_TRANSACTION_LOCK = 742819036119
_SCHEMA_OBJECTS = ("life_tips", "life_tip_source_ledger", "life_tip_feedback", "personal_greetings",
                   "idx_life_tips_scope_lookup", "idx_life_tip_feedback_tip")

_pool_lock = threading.Lock()
_pool_fill_lock = threading.Lock()
_pool_generation = 0
_pool_cache: dict[tuple[Any, ...], tuple[float, list[dict[str, Any]]]] = {}

_manifest_lock = threading.Lock()
_manifest_cache: tuple[float, list[dict[str, Any]]] | None = None


def _normalise_tip_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip()


def tip_content_hash(text: str) -> str:
    return hashlib.sha256(_normalise_tip_text(text).encode("utf-8")).hexdigest()


def ensure_life_tip_runtime(conn: Any) -> None:
    """Prepare the supplied transaction without committing or sharing its data.

    The physical witness also works for independent SQLite :memory: databases.
    A successful call is not evidence of its caller's later commit.
    """
    if _schema_exists(conn) and _seed_witness(conn):
        return
    if get_configured_db_engine() == "postgres":
        conn.execute("SELECT pg_advisory_xact_lock(?)", (_SEED_TRANSACTION_LOCK,))
    elif not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    if not _schema_exists(conn):
        ensure_life_tip_schema(conn)
    if not _seed_witness(conn):
        _seed_life_tips(conn)


def _schema_exists(conn: Any) -> bool:
    marks = ",".join("?" for _ in _SCHEMA_OBJECTS)
    if get_configured_db_engine() == "postgres":
        rows = conn.execute(f"SELECT c.relname AS name FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND c.relname IN ({marks}) AND c.relkind IN ('r','p','i')", _SCHEMA_OBJECTS).fetchall()
    else:
        rows = conn.execute(f"SELECT name FROM sqlite_master WHERE name IN ({marks}) AND type IN ('table','index')", _SCHEMA_OBJECTS).fetchall()
    return {row["name"] for row in rows} == set(_SCHEMA_OBJECTS)


def _seed_witness(conn: Any) -> bool:
    hashes = {tip_content_hash(text) for _, text in (*LIFE_TIP_SEED_PACK, *TEACHER_TIP_SEED_PACK)}
    marks = ",".join("?" for _ in hashes)
    rows = conn.execute(f"SELECT content_hash,source_kind,status FROM life_tips WHERE content_hash IN ({marks}) OR (source_kind='seed' AND status='active')", tuple(sorted(hashes))).fetchall()
    found = {row["content_hash"] for row in rows}
    return hashes <= found and not any(row["source_kind"] == "seed" and row["status"] == "active" and row["content_hash"] not in hashes for row in rows)


def _runtime_db_key() -> tuple[str, str]:
    from .. import config
    engine = get_configured_db_engine()
    target = str(config.DB_PATH.resolve()) if engine == "sqlite" else str(config.DATABASE_URL)
    return engine, hashlib.sha256(target.encode()).hexdigest()


def _close_owned_connection(conn: Any, work) -> Any:
    """Publish nothing until both commit and close have actually succeeded."""
    try:
        result = work(conn)
        conn.commit()
    except BaseException as primary:
        for operation in ("rollback", "close"):
            try:
                getattr(conn, operation)()
            except BaseException as cleanup:
                primary.add_note(f"Life-tip {operation} cleanup also failed: {type(cleanup).__name__}")
        raise
    conn.close()
    return result


def warm_life_tip_runtime(*, blocking: bool = True) -> bool:
    """Own one short seed transaction before accepting requests or claims."""
    global _seeded, _ready_db_key
    key = _runtime_db_key()
    if _seeded and _ready_db_key == key:
        return True
    if not _seed_lock.acquire(blocking=blocking):
        return False
    try:
        if _seeded and _ready_db_key == key:
            return True
        from ..database import get_db_connection
        _close_owned_connection(get_db_connection(), ensure_life_tip_runtime)
        if _runtime_db_key() != key:
            return False
        invalidate_pool_cache()
        _ready_db_key, _seeded = key, True
        return True
    finally:
        _seed_lock.release()


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
                tip_text, "seed", LIFE_TIP_SEED_SOURCE_REFS.get(("student", tip_text), ""), "active", 1,
                tip_content_hash(tip_text),
            ),
        )
    for category, tip_text in TEACHER_TIP_SEED_PACK:
        conn.execute(
            statement_sql,
            (
                "global", "", "", category, "teacher",
                tip_text, "seed", LIFE_TIP_SEED_SOURCE_REFS.get(("teacher", tip_text), ""), "active", 1,
                tip_content_hash(tip_text),
            ),
        )

    # 种子同步：文案修订后旧句不再出现在当前包里，自动下架——
    # 让"改文案"天然等于"换句"，不会新旧并存重复投放。
    current_hashes = {
        tip_content_hash(text)
        for _, text in (*LIFE_TIP_SEED_PACK, *TEACHER_TIP_SEED_PACK)
    }
    rows = conn.execute(
        "SELECT id, content_hash FROM life_tips "
        "WHERE source_kind = 'seed' AND status = 'active'"
    ).fetchall()
    for row in rows:
        if row["content_hash"] not in current_hashes:
            conn.execute(
                "UPDATE life_tips SET status = 'retired', updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (row["id"],),
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
    global _pool_generation
    with _pool_lock:
        _pool_generation += 1
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
    # A supplied connection may contain pending writes. Its snapshot must
    # never become another request's process cache, even after a later rollback.
    return _load_pool_from_db(
        conn,
        school_code=school_code,
        department=department,
        audience_role=audience_role,
    )


def _get_committed_pool(*, school_code: str, department: str, audience_role: str) -> list[dict[str, Any]]:
    if not warm_life_tip_runtime(blocking=False):
        return []
    key = (_runtime_db_key(), school_code, department, audience_role)
    with _pool_lock:
        cached = _pool_cache.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
    # Cold scopes wait before borrowing a connection; hot scopes stay in memory.
    with _pool_fill_lock:
        with _pool_lock:
            cached = _pool_cache.get(key)
            if cached and cached[0] > time.monotonic():
                return cached[1]
            generation = _pool_generation
        from ..database import get_db_connection
        pool = _close_owned_connection(get_db_connection(), lambda conn: _load_pool_from_db(
            conn, school_code=school_code, department=department, audience_role=audience_role))
        with _pool_lock:
            if pool and generation == _pool_generation and key[0] == _runtime_db_key():
                _pool_cache[key] = (time.monotonic() + POOL_CACHE_TTL_SECONDS, pool)
        return pool


def build_committed_login_tip_payload(*, user_id: int, role: str = "student") -> Optional[dict[str, Any]]:
    """Read current scope and cache only our own successfully committed pool.

    Call outside every caller-owned DB unit. Failed warmup keeps the existing
    optional-tip degradation; cold retries never hold a pool slot while waiting.
    """
    if role not in {"student", "teacher"}:
        return None
    try:
        if not warm_life_tip_runtime(blocking=False):
            return None
        from ..database import get_db_connection
        table = "students" if role == "student" else "teachers"
        row = _close_owned_connection(get_db_connection(), lambda conn: conn.execute(
            f"SELECT school_code,department FROM {table} WHERE id=?", (int(user_id),)).fetchone())
        if not row:
            return None
        pool = _get_committed_pool(school_code=str(row["school_code"] or "").strip(),
            department=str(row["department"] or "").strip(), audience_role=role)
        return _payload_from_pool(pool)
    except Exception as exc:
        print(f"[LIFE_TIP] Committed login tips unavailable: {type(exc).__name__}")
        return None


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


TAG_HIT_WEIGHT = 8


def _pick_image_url(category: str, tip_text: str = "") -> Optional[str]:
    """分类圈定候选集，再按"图片标签命中提示语文本"加权抽样。

    纯内存操作：manifest 常驻缓存，候选 ~几十张 × 每张 ≤8 个标签的
    子串检查，微秒级——登录路径零感知。无标签命中时回落均匀随机，
    旧图（无 tags 字段）天然兼容。
    """
    images = _load_image_manifest()
    if not images:
        return None
    matched = [item for item in images if category in (item.get("categories") or [])]
    pool = matched or images
    weights = [
        1 + TAG_HIT_WEIGHT * sum(
            1 for tag in (item.get("tags") or []) if tag and tag in tip_text
        )
        for item in pool
    ]
    chosen = random.choices(pool, weights=weights, k=1)[0]
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
    return _payload_from_pool(pool)


def _payload_from_pool(pool: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not pool:
        return None
    candidates = _weighted_sample(pool, min(TIP_CANDIDATE_COUNT, len(pool)))
    tips = []
    for tip in candidates:
        payload_tip = {key: value for key, value in tip.items() if key != "weight"}
        tips.append({
            **payload_tip,
            "image_url": _pick_image_url(tip["category"], tip.get("text") or ""),
        })
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
