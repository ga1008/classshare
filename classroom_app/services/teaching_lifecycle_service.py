"""Shared ordinary-Web review and deletion of unused teaching roots.

No DDL, commit, file deletion or Agent authorization lives here. Logical
reference writers use the same parent lock because several legacy tables have
no FK. Classroom hard deletion is intentionally outside these two operations.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3

from fastapi import HTTPException

from ..db.connection import get_configured_db_engine


_ROOTS = {"class": ("classes", "班级"), "course": ("courses", "课程")}
MAX_REVIEW_ROWS = 50000
MAX_REVIEW_BYTES = 16 * 1024 * 1024

# Explicit local references. Blocked objects are identified, not exported; the
# full values of rows actually deleted/detached are bound in the review hash.
_REFERENCES = {
    "class": (
        ("students", "class_id", "block", "学生"),
        ("class_offerings", "class_id", "block", "主课堂"),
        ("class_offering_class_links", "class_id", "block", "合班关联"),
        ("course_files", "class_id", "block", "课程文件范围"),
        ("blog_posts", "visible_class_id", "block", "班级定向博客"),
        ("student_login_audit_logs", "class_id", "block", "学生登录历史"),
        ("student_password_reset_requests", "class_id", "block", "学生密码重置申请"),
        ("learning_stage_exam_attempts", "class_id", "block", "学习考试历史"),
        ("teacher_academic_roster_sync_items", "class_id", "detach", "教务名册同步历史"),
        ("teacher_academic_roster_memberships", "class_id", "detach", "教务名册关系"),
        ("teacher_academic_teaching_class_mappings", "admin_class_id", "block", "教务行政班映射"),
        ("teacher_academic_course_exam_items", "class_id", "detach", "教务考试安排"),
        ("teacher_academic_exam_roster_items", "class_id", "detach", "教务考试名册"),
        ("teacher_academic_exam_roster_students", "class_id", "detach", "教务考试考生记录"),
    ),
    "course": (
        ("class_offerings", "course_id", "block", "课堂"),
        ("assignments", "course_id", "block", "作业"),
        ("course_files", "course_id", "block", "课程文件"),
        ("chunked_uploads", "course_id", "block", "文件上传会话"),
        ("course_doc_packs", "course_id", "block", "学习文档包"),
        ("lesson_plans", "course_id", "block", "教案"),
        ("assessment_plans", "course_id", "block", "考核计划"),
        ("teacher_evaluations", "course_id", "block", "教师评学文档"),
        ("course_lessons", "course_id", "delete", "课程课次模板"),
        ("teacher_academic_course_sync_items", "course_id", "detach", "教务课程同步历史"),
        ("teacher_academic_course_session_occurrences", "course_id", "detach", "教务真实排课"),
        ("teacher_academic_roster_sync_items", "course_id", "detach", "教务名册同步历史"),
        ("teacher_academic_course_exam_items", "course_id", "detach", "教务考试安排"),
        ("teacher_academic_exam_roster_items", "course_id", "detach", "教务考试名册"),
        ("student_portfolio_items", "course_id", "detach", "学生作品档案"),
    ),
}


def lock_teaching_parent(conn, kind: str, resource_id: int, *, nowait: bool = False) -> dict:
    """Acquire before domain writes; the caller retains its transaction."""
    if kind not in _ROOTS or type(resource_id) is not int or resource_id <= 0:
        raise ValueError("Invalid teaching parent")
    table, label = _ROOTS[kind]
    if isinstance(conn, sqlite3.Connection):
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (resource_id,)).fetchone()
    else:
        try:
            row = conn.execute(f"SELECT * FROM {table} WHERE id = ? FOR UPDATE" + (" NOWAIT" if nowait else ""), (resource_id,)).fetchone()
        except Exception as exc:
            if str(getattr(exc, "sqlstate", "")) == "55P03":
                raise HTTPException(409, "教学资源正在变更，请稍后重试。") from exc
            raise
    if not row:
        raise HTTPException(404, f"{label}已不存在，请刷新后重试。")
    return dict(row)


def lock_teaching_context(conn, *, course_id: int | None = None, class_offering_id: int | None = None,
                          nowait: bool = False) -> dict:
    """Acquire course first, then reload a logical classroom write target."""
    if not class_offering_id:
        return lock_teaching_parent(conn, "course", int(course_id), nowait=nowait) if course_id else {}
    offering_id = int(class_offering_id)
    initial = conn.execute("SELECT course_id FROM class_offerings WHERE id=?", (offering_id,)).fetchone()
    if not initial:
        raise HTTPException(404, "课堂已不存在或已合并，请重新选择课堂。")
    actual_course = int(initial["course_id"])
    if course_id and int(course_id) != actual_course:
        raise HTTPException(409, "课程与课堂的关联已变化，请重新选择。")
    lock_teaching_parent(conn, "course", actual_course, nowait=nowait)
    try:
        row = conn.execute("SELECT * FROM class_offerings WHERE id=?"
            + ("" if isinstance(conn, sqlite3.Connection) else " FOR UPDATE" + (" NOWAIT" if nowait else "")), (offering_id,)).fetchone()
    except Exception as exc:
        if str(getattr(exc, "sqlstate", "")) == "55P03":
            raise HTTPException(409, "课堂正在变更，请稍后重新选择。") from exc
        raise
    if not row or int(row["course_id"]) != actual_course:
        raise HTTPException(409, "课堂在等待期间已变化或合并，请重新选择。")
    return dict(row)


def lock_document_teaching_relink(conn, *, table: str, document_id: str, course_id=None, class_offering_id=None):
    """Only changed relation fields take parent locks; content edits stay local.

    Callers may already hold their document row from an ordinary content save.
    NOWAIT avoids reversing the merge's course -> classroom -> document order.
    """
    if table not in {"lesson_plans", "assessment_plans", "teacher_evaluations"}:
        raise ValueError("Unsupported teaching document")
    if course_id is None and class_offering_id is None:
        return
    row = conn.execute(f"SELECT course_id,class_offering_id FROM {table} WHERE id=?", (str(document_id),)).fetchone()
    if not row:
        raise HTTPException(404, "教学文档已不存在。")
    before = (row["course_id"], row["class_offering_id"])
    new_course = row["course_id"] if course_id is None else int(course_id) or None
    new_offering = row["class_offering_id"] if class_offering_id is None else int(class_offering_id) or None
    if before == (new_course, new_offering):
        return
    lock_teaching_context(conn, course_id=new_course, class_offering_id=new_offering, nowait=True)
    try:
        fresh = conn.execute(f"SELECT course_id,class_offering_id FROM {table} WHERE id=?"
            + ("" if isinstance(conn, sqlite3.Connection) else " FOR UPDATE NOWAIT"), (str(document_id),)).fetchone()
    except Exception as exc:
        if str(getattr(exc, "sqlstate", "")) == "55P03":
            raise HTTPException(409, "文档正在更新，请稍后重试。") from exc
        raise
    if not fresh or (fresh["course_id"], fresh["class_offering_id"]) != before:
        raise HTTPException(409, "教学文档的课程归属已变化，请重新读取后保存。")


def _columns(conn) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    if get_configured_db_engine() == "postgres":
        for row in conn.execute("SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='public'").fetchall():
            result.setdefault(str(row["table_name"]), set()).add(str(row["column_name"]))
    else:
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            name = str(row["name"])
            escaped = name.replace('"', '""')
            result[name] = {str(col["name"]) for col in conn.execute(f'PRAGMA table_info("{escaped}")').fetchall()}
    return result


def _authorize(conn, kind: str, resource_id: int, user: dict) -> dict:
    from .resource_access_service import teacher_can_manage_class, teacher_can_manage_course
    if kind not in _ROOTS or user.get("role") != "teacher":
        raise HTTPException(403, "当前身份不能管理该教学资源。")
    table, label = _ROOTS[kind]
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (int(resource_id),)).fetchone()
    check = teacher_can_manage_class if kind == "class" else teacher_can_manage_course
    if not row or not check(conn, user["id"], row):
        raise HTTPException(403, f"无权删除该{label}或资源不存在。")
    return dict(row)


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _snapshot(conn, kind: str, root: dict) -> tuple[str, list[dict], list[str]]:
    columns = _columns(conn)
    resource_id = int(root["id"])
    encoded = [_canonical({"root": root, "policy": _REFERENCES[kind]})]
    total_bytes = len(encoded[0].encode("utf-8"))
    total_rows = 0
    impacts = []
    blockers = []
    for table, column, effect, label in _REFERENCES[kind]:
        if column not in columns.get(table, set()):
            continue
        projection = f'id, "{column}"' if effect == "block" and "id" in columns[table] else "*"
        cursor = conn.execute(f'SELECT {projection} FROM "{table}" WHERE "{column}" = ?', (resource_id,))
        serialized = []
        while True:
            batch = cursor.fetchmany(200)
            if not batch:
                break
            for row in batch:
                value = _canonical(dict(row))
                total_rows += 1
                total_bytes += len(value.encode("utf-8"))
                if total_rows > MAX_REVIEW_ROWS or total_bytes > MAX_REVIEW_BYTES:
                    raise HTTPException(413, "关联数据超过单次核对上限，请分步整理后重试；未生成部分确认。")
                serialized.append(value)
        encoded.append(_canonical({"table": table, "column": column, "effect": effect, "rows": sorted(serialized)}))
        if serialized:
            impacts.append({"key": table, "label": label, "effect": effect, "count": len(serialized)})
            if effect == "block":
                blockers.append(f"仍有关联的{label} {len(serialized)} 条，请先在原业务处理。")

    # Combined academic class IDs are a real local relation in JSON, not an FK.
    if kind == "class" and "admin_class_ids_json" in columns.get("teacher_academic_teaching_class_mappings", set()):
        cursor = conn.execute("SELECT id,admin_class_ids_json FROM teacher_academic_teaching_class_mappings WHERE admin_class_ids_json LIKE ?",
                              (f"%{resource_id}%",))
        matches = []
        while True:
            batch = cursor.fetchmany(200)
            if not batch:
                break
            for row in batch:
                total_rows += 1
                total_bytes += len(_canonical(dict(row)).encode("utf-8"))
                if total_rows > MAX_REVIEW_ROWS or total_bytes > MAX_REVIEW_BYTES:
                    raise HTTPException(413, "关联数据超过单次核对上限，请分步整理后重试；未生成部分确认。")
                try:
                    ids = json.loads(row["admin_class_ids_json"] or "[]")
                except (ValueError, TypeError):
                    ids = [resource_id]  # Malformed related mappings cannot authorize deletion.
                if not isinstance(ids, list) or resource_id in ids or str(resource_id) in ids:
                    matches.append(dict(row))
        encoded.append(_canonical({"academic_mapping_classes": sorted(_canonical(row) for row in matches)}))
        if matches:
            blockers.append(f"仍有教务合班映射 {len(matches)} 条，请先在教务映射中处理。")

    # New direct local references require an explicit lifecycle policy.
    known = {(table, column) for table, column, _, _ in _REFERENCES[kind]}
    local_names = {"class_id", "visible_class_id", "admin_class_id"} if kind == "class" else {"course_id"}
    for table in sorted(columns):
        for column in sorted(columns[table] & local_names):
            if (table, column) in known:
                continue
            safe_table = table.replace('"', '""')
            count = int(conn.execute(f'SELECT COUNT(*) FROM "{safe_table}" WHERE "{column}" = ?', (resource_id,)).fetchone()[0])
            blockers.append(f"有一类业务关联尚未登记删除处置规则（当前 {count} 条引用），已阻止删除，请联系管理员核对。")
            encoded.append(_canonical({"unknown_reference": [table, column, count]}))
    return hashlib.sha256("\n".join(encoded).encode("utf-8")).hexdigest(), impacts, blockers


def build_teaching_delete_review(conn, *, kind: str, resource_id: int, user: dict) -> dict:
    root = _authorize(conn, kind, resource_id, user)
    before, impacts, blockers = _snapshot(conn, kind, root)
    fresh = _authorize(conn, kind, resource_id, user)
    after, _, _ = _snapshot(conn, kind, fresh)
    if before != after:
        raise HTTPException(409, "核对期间教学资源或引用已变化，请重新预览。")
    _, label = _ROOTS[kind]
    warnings = [{"code": "irreversible_root_delete", "message": f"将永久删除{label}“{root['name']}”，平台没有一键恢复入口。"}]
    if any(item["effect"] == "detach" for item in impacts):
        warnings.append({"code": "historical_links_detached", "message": "列出的教务或学习历史记录保留，但解除与本资源的关联。"})
    if kind == "course" and any(item["key"] == "teacher_academic_course_session_occurrences" for item in impacts):
        warnings.append({"code": "academic_course_reappears", "message": "该课程来自教务真实排课；同名可能对应不同课程号，删除后下一次教务同步仍可能重建课程。"})
    return {"kind": "teaching_delete", "title": f"核对并删除{label}", "summary": f"{label}：{root['name']}（#{resource_id}）",
            "resource_id": resource_id, "resource_kind": kind, "review_hash": after,
            "impact_sections": impacts, "warnings": warnings, "blockers": blockers,
            "can_execute": not blockers, "expected_confirmation_text": str(root["name"])}


def delete_unused_teaching_resource(conn, *, kind: str, resource_id: int, user: dict,
                                    expected_review_hash: str, confirmation_text: str) -> dict:
    _authorize(conn, kind, resource_id, user)
    lock_teaching_parent(conn, kind, resource_id)
    review = build_teaching_delete_review(conn, kind=kind, resource_id=resource_id, user=user)
    if not review["can_execute"]:
        raise HTTPException(409, " ".join(review["blockers"]))
    if expected_review_hash != review["review_hash"]:
        raise HTTPException(409, "教学资源或引用已变化，本次确认已失效，请重新预览。")
    if confirmation_text.strip() != review["expected_confirmation_text"].strip():
        raise HTTPException(400, "请输入本次预览中的完整资源名称。")
    table, label = _ROOTS[kind]
    conn.execute(f"DELETE FROM {table} WHERE id = ?", (int(resource_id),))
    return {"status": "success", "label": f"已删除{label}", "ref_id": resource_id,
            "resource_kind": kind, "resource_name": review["expected_confirmation_text"],
            "review_hash": review["review_hash"], "impact_sections": review["impact_sections"],
            "url": f"/manage/teaching/{'classes' if kind == 'class' else 'courses'}"}


def validate_teaching_confirmation_inputs(inputs: dict, review: dict) -> None:
    if not isinstance(inputs, dict) or set(inputs) != {"accepted_warning_codes", "confirmation_note", "confirmation_text"}:
        raise HTTPException(400, "请提交完整核对声明、说明和手工确认名称。")
    codes, note, confirmation = inputs["accepted_warning_codes"], inputs["confirmation_note"], inputs["confirmation_text"]
    if (not isinstance(codes, list) or len(codes) > 30
            or any(not isinstance(code, str) or not 1 <= len(code) <= 100 for code in codes)
            or len(set(codes)) != len(codes) or not isinstance(note, str) or not 1 <= len(note) <= 2000 or not note.strip()
            or not isinstance(confirmation, str) or len(confirmation) > 500
            or any(0xD800 <= ord(char) <= 0xDFFF for text in [note, confirmation, *codes] for char in text)):
        raise HTTPException(400, "核对声明格式无效。")
    if set(codes) != {warning["code"] for warning in review["warnings"]}:
        raise HTTPException(400, "请逐项核对并接受本次预览中的全部警告。")
    if confirmation.strip() != review["expected_confirmation_text"].strip():
        raise HTTPException(400, "请输入本次预览中的完整名称。")


def delete_teaching_resource_from_web(conn, *, kind: str, resource_id: int, user: dict, payload: dict) -> dict:
    expected_keys = {"expected_review_hash", "accepted_warning_codes", "confirmation_note", "confirmation_text"}
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise HTTPException(400, "请先重新读取删除影响并完成本次确认。")
    revision = payload["expected_review_hash"]
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision):
        raise HTTPException(400, "删除确认版本无效，请重新预览。")
    review = build_teaching_delete_review(conn, kind=kind, resource_id=resource_id, user=user)
    if revision != review["review_hash"]:
        raise HTTPException(409, "教学数据已变化，本次确认失效，请重新预览。")
    validate_teaching_confirmation_inputs({key: value for key, value in payload.items() if key != "expected_review_hash"}, review)
    return delete_unused_teaching_resource(conn, kind=kind, resource_id=resource_id, user=user,
        expected_review_hash=revision, confirmation_text=payload["confirmation_text"])
