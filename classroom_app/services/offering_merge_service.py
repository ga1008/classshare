"""历史双开课堂「合并向导」引擎（P4.0，方案见 docs/combined-class-offering-plan §9）。

把同教师、同课程、同学期、班级互斥的多个课堂合并为一个合班课堂：
目录驱动（``MERGE_RULES`` 登记全部挂 offering 的表）+ 未登记表守卫 +
dry-run 预检 + 单事务执行 + JSON 快照兜底 + 审计日志。

P4.0 采用**作业并存模式**：被并课堂的作业整体迁入主课堂并加
「（原XX班）」标题后缀，提交与成绩随作业原样保留，零数据风险。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from ..db.connection import begin_immediate_transaction, execute_insert_returning_id, get_configured_db_engine
from ..db.schema_offering_merge import ensure_offering_merge_schema
from .academic_service import china_now
from .offering_membership_service import (
    offering_class_ids,
    replace_offering_class_links,
)

# ---------------------------------------------------------------------------
# 策略目录
# ---------------------------------------------------------------------------

STRATEGY_REPOINT = "repoint"
STRATEGY_REPOINT_GUARDED = "repoint_guarded"  # UNIQUE(offering, <conflict_key>)：预检探撞
STRATEGY_DEDUP_SKIP = "dedup_skip"            # 与 target 撞唯一键的 source 行归档后删除
STRATEGY_KEEP_TARGET = "keep_target"          # offering 级单例：保留 target，source 归档删除
STRATEGY_SESSION_STRUCTURE = "session_structure"  # 课次结构：映射引用后删 source 行
STRATEGY_ASSIGNMENT_COEXIST = "assignment_coexist"
STRATEGY_LINKS = "links"                      # 收尾统一处理


@dataclass(frozen=True)
class MergeRule:
    strategy: str
    offering_column: str = "class_offering_id"
    conflict_key: tuple[str, ...] = ()
    session_ref_column: str = ""  # 非空 ⇒ 该列引用 class_offering_sessions.id，需按课次映射改写


def _rules() -> dict[str, MergeRule]:
    repoint_tables = [
        "ai_chat_sessions", "ai_psychology_profiles", "ai_usage_log", "assessment_plans",
        "assignment_group_bindings", "chat_logs", "chunked_uploads", "classroom_behavior_events",
        "classroom_behavior_profiles", "classroom_live_activities", "classroom_live_help_signals",
        "classroom_live_questions", "classroom_todos", "course_files", "cultivation_alerts",
        "cultivation_score_events", "discussion_attachments", "group_assignment_member_results",
        "group_invitations", "group_schemes", "learning_stage_exam_attempts", "lesson_plans",
        "message_center_notifications", "peer_reviews", "private_message_ai_jobs",
        "private_message_attachments", "private_message_audit_logs", "private_messages",
        "smart_classroom_checkin_sessions", "smart_classroom_checkin_students",
        "smart_classroom_schedule_items", "student_growth_events",
        "student_learning_path_item_states", "student_portfolio_items", "study_groups",
        "teacher_academic_course_exam_items", "teacher_academic_exam_roster_items",
        "teacher_academic_exam_roster_students", "teacher_evaluations",
    ]
    guarded = {
        "learning_progress_snapshots": ("student_id",),
        "learning_stage_status": ("student_id", "stage_key"),
        "learning_certificates": ("student_id", "stage_key"),
        "learning_material_progress": ("student_id", "material_id"),
        "cultivation_weekly_snapshots": ("student_id", "week_start"),
        "cultivation_score_event_archives": ("student_id", "archive_month", "event_type", "component"),
        "classroom_retake_students": ("student_id",),
    }
    # 聚合缓存/统计类：教师行两边必然重复（如行为状态、表情统计），
    # 保留主课堂侧、source 冲突行随快照归档后去重，不阻断合并。
    dedup = {
        "classroom_behavior_states": ("user_role", "user_pk"),
        "smart_attendance_student_advice": ("student_id", "fingerprint"),
        "emoji_usage_stats": ("user_id", "user_role", "emoji_type", "emoji_key"),
        "custom_emojis": ("owner_user_id", "owner_user_role", "file_hash"),
        "poll_assignments": ("poll_id",),
        "course_material_assignments": ("material_id",),
        "smart_attendance_daily_tasks": ("teacher_id", "task_type", "task_date"),
    }
    keep_target = [
        "ai_class_configs", "discussion_mood_snapshots", "chat_log_migrations",
        "academic_final_material_batches",
    ]

    rules: dict[str, MergeRule] = {}
    for table in repoint_tables:
        rules[table] = MergeRule(STRATEGY_REPOINT)
    for table, key in guarded.items():
        rules[table] = MergeRule(STRATEGY_REPOINT_GUARDED, conflict_key=key)
    for table, key in dedup.items():
        rules[table] = MergeRule(STRATEGY_DEDUP_SKIP, conflict_key=key)
    for table in keep_target:
        rules[table] = MergeRule(STRATEGY_KEEP_TARGET)
    rules["assignments"] = MergeRule(STRATEGY_ASSIGNMENT_COEXIST)
    rules["class_offering_sessions"] = MergeRule(STRATEGY_SESSION_STRUCTURE)
    rules["class_offering_class_links"] = MergeRule(STRATEGY_LINKS, offering_column="offering_id")
    # 课次引用列（这三张的 offering 列策略见各自条目）
    rules["class_offering_learning_materials"] = MergeRule(
        STRATEGY_DEDUP_SKIP, conflict_key=("session_id", "material_id"), session_ref_column="session_id"
    )
    rules["learning_material_progress"] = MergeRule(
        STRATEGY_REPOINT_GUARDED, conflict_key=("student_id", "material_id"), session_ref_column="session_id"
    )
    rules["session_material_generation_tasks"] = MergeRule(
        STRATEGY_REPOINT, session_ref_column="session_id"
    )
    return rules


MERGE_RULES: dict[str, MergeRule] = _rules()

# 自身/合并机制表——不参与迁移
IGNORED_OFFERING_TABLES = {
    "class_offerings",
    "offering_merge_archives",
    "offering_merge_logs",
}


class OfferingMergeError(ValueError):
    """合并前置条件或执行校验失败。"""


def _now_iso() -> str:
    return china_now().replace(tzinfo=None).isoformat(timespec="seconds")


def _offering_column_tables(conn: Any) -> dict[str, str]:
    """实际库中所有含 offering 引用列的表 → 列名（engine-aware）。"""
    engine = get_configured_db_engine()
    found: dict[str, str] = {}
    if engine == "postgres":
        rows = conn.execute(
            """
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema = 'public'
              AND column_name IN ('class_offering_id', 'offering_id')
            """
        ).fetchall()
        for row in rows:
            found[str(row["table_name"])] = str(row["column_name"])
    else:
        tables = [
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        ]
        for table in tables:
            for col in conn.execute(f"PRAGMA table_info({table})").fetchall():
                if str(col["name"]) in ("class_offering_id", "offering_id"):
                    found[table] = str(col["name"])
    return found


def find_unregistered_offering_tables(conn: Any) -> list[str]:
    """守卫：实际存在但未登记进 MERGE_RULES 的挂表（应为空）。"""
    actual = _offering_column_tables(conn)
    return sorted(set(actual) - set(MERGE_RULES) - IGNORED_OFFERING_TABLES)


# ---------------------------------------------------------------------------
# 候选检测
# ---------------------------------------------------------------------------

def find_merge_candidates(conn: Any, teacher_id: int) -> list[dict[str, Any]]:
    """同教师+课程+学期存在多个课堂且班级互斥 → 合并候选组。"""
    rows = conn.execute(
        """
        SELECT o.id, o.course_id, o.semester_id, COALESCE(o.semester, '') AS semester,
               c.name AS course_name, cl.name AS class_name,
               COALESCE(c.academic_metadata_json, '') AS course_metadata
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        WHERE o.teacher_id = ?
        ORDER BY o.course_id, o.id
        """,
        (int(teacher_id),),
    ).fetchall()

    groups: dict[tuple, list[dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        key = (int(item["course_id"]), str(item["semester_id"] or item["semester"]))
        groups.setdefault(key, []).append(item)

    candidates: list[dict[str, Any]] = []
    for (course_id, _semester_key), items in groups.items():
        if len(items) < 2:
            continue
        class_sets = {
            int(item["id"]): set(offering_class_ids(conn, int(item["id"])))
            for item in items
        }
        seen: set[int] = set()
        disjoint = True
        for ids in class_sets.values():
            if ids & seen:
                disjoint = False
                break
            seen |= ids
        if not disjoint:
            continue
        academic_confirmed = False
        try:
            metadata = json.loads(items[0].get("course_metadata") or "{}")
            academic_confirmed = len(metadata.get("combined_admin_classes") or []) > 1
        except (TypeError, ValueError):
            pass
        offerings = []
        for item in items:
            offering_id = int(item["id"])
            stats_row = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM assignments a WHERE a.class_offering_id = ?) AS assignment_count,
                    (SELECT COUNT(*) FROM class_offering_sessions s WHERE s.class_offering_id = ?) AS session_count
                """,
                (offering_id, offering_id),
            ).fetchone()
            student_count = 0
            class_ids = sorted(class_sets[offering_id])
            if class_ids:
                placeholders = ",".join("?" for _ in class_ids)
                student_count = int(
                    conn.execute(
                        f"""
                        SELECT COUNT(*) AS n FROM students
                        WHERE class_id IN ({placeholders})
                          AND COALESCE(enrollment_status, 'active') = 'active'
                        """,
                        tuple(class_ids),
                    ).fetchone()["n"]
                )
            offerings.append(
                {
                    "offering_id": offering_id,
                    "class_name": item["class_name"],
                    "class_ids": class_ids,
                    "student_count": student_count,
                    "assignment_count": int(stats_row["assignment_count"]),
                    "session_count": int(stats_row["session_count"]),
                }
            )
        recommended = max(
            offerings,
            key=lambda o: (o["session_count"], o["assignment_count"], -o["offering_id"]),
        )
        candidates.append(
            {
                "course_id": course_id,
                "course_name": items[0]["course_name"],
                "semester": items[0]["semester"],
                "academic_confirmed_combined": academic_confirmed,
                "offerings": offerings,
                "recommended_target_id": recommended["offering_id"],
            }
        )
    return candidates


# ---------------------------------------------------------------------------
# 预检 / dry-run
# ---------------------------------------------------------------------------

def _load_merge_offerings(
    conn: Any, *, teacher_id: int, target_offering_id: int, source_offering_ids: list[int]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_ids = sorted({int(v) for v in source_offering_ids if int(v) > 0})
    if not source_ids:
        raise OfferingMergeError("请至少选择一个被合并课堂")
    if int(target_offering_id) in source_ids:
        raise OfferingMergeError("主课堂不能同时作为被合并课堂")
    all_ids = [int(target_offering_id), *source_ids]
    placeholders = ",".join("?" for _ in all_ids)
    rows = conn.execute(
        f"""
        SELECT o.*, c.name AS course_name, cl.name AS class_name
        FROM class_offerings o
        JOIN courses c ON c.id = o.course_id
        JOIN classes cl ON cl.id = o.class_id
        WHERE o.id IN ({placeholders})
        """,
        tuple(all_ids),
    ).fetchall()
    by_id = {int(row["id"]): dict(row) for row in rows}
    missing = [oid for oid in all_ids if oid not in by_id]
    if missing:
        raise OfferingMergeError(f"课堂不存在：#{'、#'.join(str(m) for m in missing)}")
    for offering in by_id.values():
        if int(offering["teacher_id"]) != int(teacher_id):
            raise OfferingMergeError("只能合并您本人的课堂")
    target = by_id[int(target_offering_id)]
    sources = [by_id[oid] for oid in source_ids]
    semester_key = str(target.get("semester_id") or target.get("semester") or "")
    for source in sources:
        if int(source["course_id"]) != int(target["course_id"]):
            raise OfferingMergeError("只能合并同一门课程的课堂")
        if str(source.get("semester_id") or source.get("semester") or "") != semester_key:
            raise OfferingMergeError("只能合并同一学期的课堂")
    class_seen: set[int] = set()
    for offering in [target, *sources]:
        ids = set(offering_class_ids(conn, int(offering["id"])))
        if ids & class_seen:
            raise OfferingMergeError("课堂之间存在重叠班级，不能视为双开合并")
        class_seen |= ids
    return target, sources


def _table_exists(conn: Any, table: str) -> bool:
    if get_configured_db_engine() == "postgres":
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name = ?",
            (table,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (table,),
        ).fetchone()
    return bool(row)


def _count_rows(conn: Any, table: str, column: str, offering_ids: list[int]) -> int:
    placeholders = ",".join("?" for _ in offering_ids)
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM {table} WHERE {column} IN ({placeholders})",
        tuple(offering_ids),
    ).fetchone()
    return int(row["n"])


def _conflict_count(
    conn: Any, table: str, column: str, conflict_key: tuple[str, ...],
    target_id: int, source_ids: list[int],
) -> int:
    join_clause = " AND ".join(f"a.{col} = b.{col}" for col in conflict_key)
    placeholders = ",".join("?" for _ in source_ids)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM {table} a
        JOIN {table} b ON {join_clause}
        WHERE a.{column} IN ({placeholders})
          AND b.{column} = ?
        """,
        (*source_ids, target_id),
    ).fetchone()
    return int(row["n"])


def build_merge_preview(
    conn: Any, *, teacher_id: int, target_offering_id: int, source_offering_ids: list[int]
) -> dict[str, Any]:
    """只读 dry-run：每表迁移行数、去重丢弃数、冲突阻断、课次对齐。"""
    ensure_offering_merge_schema(conn)
    unregistered = find_unregistered_offering_tables(conn)
    if unregistered:
        raise OfferingMergeError(
            "存在未登记进合并目录的课堂关联表，为防数据丢失已拒绝合并："
            + "、".join(unregistered)
        )
    target, sources = _load_merge_offerings(
        conn,
        teacher_id=teacher_id,
        target_offering_id=target_offering_id,
        source_offering_ids=source_offering_ids,
    )
    source_ids = [int(s["id"]) for s in sources]

    tables: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    for table, rule in sorted(MERGE_RULES.items()):
        if rule.strategy == STRATEGY_LINKS or not _table_exists(conn, table):
            continue
        source_rows = _count_rows(conn, table, rule.offering_column, source_ids)
        entry: dict[str, Any] = {
            "table": table,
            "strategy": rule.strategy,
            "source_rows": source_rows,
        }
        if source_rows and rule.strategy == STRATEGY_REPOINT_GUARDED:
            conflicts = _conflict_count(
                conn, table, rule.offering_column, rule.conflict_key,
                int(target["id"]), source_ids,
            )
            entry["conflicts"] = conflicts
            if conflicts:
                blockers.append(
                    f"{table}：{conflicts} 条记录在主课堂与被并课堂重复"
                    f"（按 {'、'.join(rule.conflict_key)}），可能是转班学生的历史数据，请先人工处理"
                )
        if source_rows and rule.strategy == STRATEGY_DEDUP_SKIP:
            duplicates = _conflict_count(
                conn, table, rule.offering_column, rule.conflict_key,
                int(target["id"]), source_ids,
            )
            entry["dedup_dropped"] = duplicates
            if duplicates:
                warnings.append(f"{table}：{duplicates} 条与主课堂重复的下发/关联将去重（快照保留）")
        if source_rows and rule.strategy == STRATEGY_KEEP_TARGET:
            warnings.append(f"{table}：保留主课堂配置，被并课堂的 {source_rows} 条归档后删除")
        tables.append(entry)

    target_orders = {
        int(row["order_index"])
        for row in conn.execute(
            "SELECT order_index FROM class_offering_sessions WHERE class_offering_id = ?",
            (int(target["id"]),),
        ).fetchall()
    }
    unmatched_sessions = 0
    for source_id in source_ids:
        for row in conn.execute(
            "SELECT order_index FROM class_offering_sessions WHERE class_offering_id = ?",
            (source_id,),
        ).fetchall():
            if int(row["order_index"]) not in target_orders:
                unmatched_sessions += 1
    if unmatched_sessions:
        warnings.append(
            f"被并课堂有 {unmatched_sessions} 个课次在主课堂无同序号课次，其材料/记录将挂到主课堂最近课次"
        )

    return {
        "target": {
            "offering_id": int(target["id"]),
            "course_name": target["course_name"],
            "class_name": target["class_name"],
        },
        "sources": [
            {"offering_id": int(s["id"]), "class_name": s["class_name"]} for s in sources
        ],
        "tables": [t for t in tables if t["source_rows"]],
        "total_source_rows": sum(t["source_rows"] for t in tables),
        "blockers": blockers,
        "warnings": warnings,
        "can_execute": not blockers,
    }


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------

def _snapshot_offerings(conn: Any, offering_ids: list[int]) -> dict[str, Any]:
    payload: dict[str, Any] = {"offerings": [], "tables": {}}
    placeholders = ",".join("?" for _ in offering_ids)
    payload["offerings"] = [
        dict(row)
        for row in conn.execute(
            f"SELECT * FROM class_offerings WHERE id IN ({placeholders})",
            tuple(offering_ids),
        ).fetchall()
    ]
    for table, rule in MERGE_RULES.items():
        if not _table_exists(conn, table):
            continue
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE {rule.offering_column} IN ({placeholders})",
            tuple(offering_ids),
        ).fetchall()
        if rows:
            payload["tables"][table] = [dict(row) for row in rows]
    return payload


def _session_id_map(conn: Any, target_id: int, source_ids: list[int]) -> dict[int, int]:
    """source 课次 id → target 课次 id（按 order_index；无同序号则挂最近课次）。"""
    target_rows = conn.execute(
        "SELECT id, order_index FROM class_offering_sessions WHERE class_offering_id = ? ORDER BY order_index",
        (target_id,),
    ).fetchall()
    by_order = {int(r["order_index"]): int(r["id"]) for r in target_rows}
    ordered = sorted(by_order)
    mapping: dict[int, int] = {}
    for source_id in source_ids:
        for row in conn.execute(
            "SELECT id, order_index FROM class_offering_sessions WHERE class_offering_id = ?",
            (source_id,),
        ).fetchall():
            order = int(row["order_index"])
            if order in by_order:
                mapping[int(row["id"])] = by_order[order]
            elif ordered:
                fallback = max((o for o in ordered if o <= order), default=ordered[0])
                mapping[int(row["id"])] = by_order[fallback]
    return mapping


def execute_offering_merge(
    conn: Any,
    *,
    teacher_id: int,
    target_offering_id: int,
    source_offering_ids: list[int],
    confirm_class_name: str = "",
) -> dict[str, Any]:
    """单事务执行合并；任何失败整体回滚（调用方负责 rollback/commit）。"""
    started = time.monotonic()
    preview = build_merge_preview(
        conn,
        teacher_id=teacher_id,
        target_offering_id=target_offering_id,
        source_offering_ids=source_offering_ids,
    )
    if not preview["can_execute"]:
        raise OfferingMergeError("预检存在阻断项，无法执行：" + "；".join(preview["blockers"]))
    target, sources = _load_merge_offerings(
        conn,
        teacher_id=teacher_id,
        target_offering_id=target_offering_id,
        source_offering_ids=source_offering_ids,
    )
    expected_confirm = str(target.get("class_name") or "").strip()
    if str(confirm_class_name or "").strip() != expected_confirm:
        raise OfferingMergeError(f"确认文本不匹配：请输入主课堂班级名「{expected_confirm}」")

    source_ids = [int(s["id"]) for s in sources]
    merge_token = f"merge-{int(target['id'])}-{_now_iso().replace(':', '').replace('-', '')}"
    begin_immediate_transaction(conn)

    # 1. 快照（同事务：与迁移前状态强一致）
    snapshot = _snapshot_offerings(conn, [int(target["id"]), *source_ids])
    archive_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO offering_merge_archives (
            merge_token, teacher_id, target_offering_id, source_offering_ids_json, payload_json
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            merge_token,
            int(teacher_id),
            int(target["id"]),
            json.dumps(source_ids),
            json.dumps(snapshot, ensure_ascii=False, default=str),
        ),
    )

    summary: dict[str, Any] = {}
    src_placeholders = ",".join("?" for _ in source_ids)

    # 2. 课次映射（在任何 repoint 前算好并改写引用）
    session_map = _session_id_map(conn, int(target["id"]), source_ids)
    for table, rule in MERGE_RULES.items():
        if not rule.session_ref_column or not _table_exists(conn, table):
            continue
        updated = 0
        for old_id, new_id in session_map.items():
            cursor = conn.execute(
                f"UPDATE {table} SET {rule.session_ref_column} = ? "
                f"WHERE {rule.session_ref_column} = ? AND {rule.offering_column} IN ({src_placeholders})",
                (new_id, old_id, *source_ids),
            )
            updated += int(cursor.rowcount or 0)
        if updated:
            summary[f"{table}.session_remapped"] = updated

    # 3. 按目录迁移
    for table, rule in sorted(MERGE_RULES.items()):
        if rule.strategy in (STRATEGY_LINKS, STRATEGY_SESSION_STRUCTURE):
            continue
        if not _table_exists(conn, table):
            continue
        column = rule.offering_column
        if rule.strategy == STRATEGY_KEEP_TARGET:
            cursor = conn.execute(
                f"DELETE FROM {table} WHERE {column} IN ({src_placeholders})",
                tuple(source_ids),
            )
            if cursor.rowcount:
                summary[f"{table}.dropped_keep_target"] = int(cursor.rowcount)
            continue
        if rule.strategy == STRATEGY_ASSIGNMENT_COEXIST:
            moved = 0
            for source in sources:
                suffix = f"（原{source['class_name']}）"
                cursor = conn.execute(
                    f"UPDATE {table} SET {column} = ?, title = title || ? WHERE {column} = ?",
                    (int(target["id"]), suffix, int(source["id"])),
                )
                moved += int(cursor.rowcount or 0)
            if moved:
                summary[f"{table}.repointed_with_suffix"] = moved
            continue
        dropped = 0
        moved = 0
        for source_id in source_ids:
            if rule.strategy == STRATEGY_DEDUP_SKIP and rule.conflict_key:
                join_clause = " AND ".join(
                    f"a.{col} = b.{col}" for col in rule.conflict_key
                )
                if get_configured_db_engine() == "postgres":
                    delete_sql = (
                        f"DELETE FROM {table} a USING {table} b "
                        f"WHERE {join_clause} AND a.{column} = ? AND b.{column} = ?"
                    )
                else:
                    key_cols = ", ".join(rule.conflict_key)
                    delete_sql = (
                        f"DELETE FROM {table} WHERE {column} = ? AND ({key_cols}) IN "
                        f"(SELECT {key_cols} FROM {table} b WHERE b.{column} = ?)"
                    )
                cursor = conn.execute(delete_sql, (source_id, int(target["id"])))
                dropped += int(cursor.rowcount or 0)
            cursor = conn.execute(
                f"UPDATE {table} SET {column} = ? WHERE {column} = ?",
                (int(target["id"]), source_id),
            )
            moved += int(cursor.rowcount or 0)
        if dropped:
            summary[f"{table}.dedup_dropped"] = dropped
        if moved:
            summary[f"{table}.repointed"] = moved

    # 4. 课次结构与 links 收尾
    cursor = conn.execute(
        f"DELETE FROM class_offering_sessions WHERE class_offering_id IN ({src_placeholders})",
        tuple(source_ids),
    )
    summary["class_offering_sessions.dropped_after_remap"] = int(cursor.rowcount or 0)

    merged_class_ids: list[int] = list(offering_class_ids(conn, int(target["id"])))
    source_class_names: dict[int, str] = {}
    for source in sources:
        for class_id in offering_class_ids(conn, int(source["id"])):
            if class_id not in merged_class_ids:
                merged_class_ids.append(class_id)
                source_class_names[class_id] = str(source["class_name"] or "")
    conn.execute(
        f"DELETE FROM class_offering_class_links WHERE offering_id IN ({src_placeholders})",
        tuple(source_ids),
    )
    conn.execute(
        f"DELETE FROM class_offerings WHERE id IN ({src_placeholders}) AND teacher_id = ?",
        (*source_ids, int(teacher_id)),
    )
    link_result = replace_offering_class_links(
        conn,
        offering_id=int(target["id"]),
        teacher_id=int(teacher_id),
        class_ids=merged_class_ids,
        primary_class_id=int(target["class_id"]),
        source="merge",
        academic_class_names=source_class_names,
    )

    duration_ms = int((time.monotonic() - started) * 1000)
    execute_insert_returning_id(
        conn,
        """
        INSERT INTO offering_merge_logs (
            merge_token, teacher_id, target_offering_id, source_offering_ids_json,
            summary_json, archive_id, duration_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            merge_token,
            int(teacher_id),
            int(target["id"]),
            json.dumps(source_ids),
            json.dumps(summary, ensure_ascii=False),
            int(archive_id),
            duration_ms,
        ),
    )

    return {
        "status": "success",
        "merge_token": merge_token,
        "archive_id": int(archive_id),
        "target_offering_id": int(target["id"]),
        "source_offering_ids": source_ids,
        "combined_class_names": link_result.get("combined_class_names"),
        "summary": summary,
        "duration_ms": duration_ms,
        "message": (
            f"已将 {len(source_ids)} 个课堂并入「{target['course_name']} / "
            f"{link_result.get('combined_class_names') or target['class_name']}」；"
            f"数据快照 #{archive_id} 已留存。"
        ),
    }
