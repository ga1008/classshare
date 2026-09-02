"""LessonDoc 学习文档包服务:包骨架落库/清单读写/pack 登记表 CRUD.

职责边界:
- 本模块负责「包在材料库中的实体」与「pack 登记表」;AI 生成执行与任务队列
  在 session_material_generation_service(P2 接入,document_type="lessondoc")。
- 课堂绑定不在这里:复用 html_package_service.apply_package_session_bindings。
- 写路径唯一约定:清单以包内 course.json 文件为真源,写文件后同步刷
  course_doc_packs.manifest_cache_json 缓存(列表页加速,读仍以文件为准)。

循环导入守则:对 session_material_generation_service 的行创建辅助与
html_package_service 的文本读取一律**函数内惰性导入**。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ...db.schema_course_doc_packs import ensure_course_doc_pack_schema
from ...db.connection import execute_insert_returning_id, get_configured_db_engine
from . import assets as assets_module
from . import render, spec, validate

PACK_FOLDER_SUFFIX = "-学习文档包"
MANIFEST_FILE_NAME = "course.json"
LESSON_STATUSES = frozenset({"pending", "queued", "running", "ready", "failed", "excluded"})


def _now_iso() -> str:
    return datetime.now().isoformat()


class LessonDocPackError(ValueError):
    """包操作的业务错误(调用方转 4xx)."""


# ---------------------------------------------------------------- 行辅助(惰性导入)

def _row_helpers():
    from ..session_material_generation_service import (  # noqa: PLC0415
        _create_file_row,
        _create_folder_row,
        _material_path_join,
        _store_markdown_bytes,
    )

    return _create_file_row, _create_folder_row, _material_path_join, _store_markdown_bytes


def _load_file_text(conn, material_row) -> str | None:
    from ..html_package_service import load_material_file_text  # noqa: PLC0415

    return load_material_file_text(conn, material_row)


def _update_file_content(conn, material_id: int, content: str, now: str) -> None:
    _, _, _, store = _row_helpers()
    file_hash, file_size = store(content)
    conn.execute(
        "UPDATE course_materials SET file_hash = ?, file_size = ?, updated_at = ? WHERE id = ?",
        (file_hash, file_size, now, int(material_id)),
    )


def _find_child(conn, *, teacher_id: int, parent_id: int, name: str):
    return conn.execute(
        """
        SELECT * FROM course_materials
        WHERE teacher_id = ? AND parent_id = ? AND name = ?
        LIMIT 1
        """,
        (int(teacher_id), int(parent_id), name),
    ).fetchone()


# ---------------------------------------------------------------- pack 登记表

def _serialize_pack(row) -> dict[str, Any]:
    pack = dict(row)
    try:
        pack["manifest_cache"] = json.loads(pack.get("manifest_cache_json") or "{}")
    except (ValueError, TypeError):
        pack["manifest_cache"] = {}
    return pack


def get_pack_by_root(conn, root_material_id: int) -> dict[str, Any] | None:
    ensure_course_doc_pack_schema(conn)
    row = conn.execute(
        "SELECT * FROM course_doc_packs WHERE root_material_id = ? LIMIT 1",
        (int(root_material_id),),
    ).fetchone()
    return _serialize_pack(row) if row else None


def get_pack(conn, pack_id: int) -> dict[str, Any] | None:
    ensure_course_doc_pack_schema(conn)
    row = conn.execute(
        "SELECT * FROM course_doc_packs WHERE id = ? LIMIT 1",
        (int(pack_id),),
    ).fetchone()
    return _serialize_pack(row) if row else None


def list_packs_for_course(conn, *, course_id: int, teacher_id: int) -> list[dict[str, Any]]:
    ensure_course_doc_pack_schema(conn)
    rows = conn.execute(
        """
        SELECT * FROM course_doc_packs
        WHERE course_id = ? AND teacher_id = ? AND status = 'active'
        ORDER BY updated_at DESC, id DESC
        """,
        (int(course_id), int(teacher_id)),
    ).fetchall()
    return [_serialize_pack(row) for row in rows]


def attach_pack_metadata(conn, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给材料列表条目批量附加 LessonDoc 包元数据(仿 attach_render_metadata)。

    命中包根的条目写 ``lessondoc_pack``(含 id/theme/ready_count/total_count),
    未命中写 None——字段恒存在,前端不必做 hasOwnProperty 判断。
    单条 SQL 批量查询,避免 N+1;任何异常都静默降级为"全部未命中"
    (材料列表不能因为附加信息失败而整体 500)。
    """
    if not items:
        return items
    for item in items:
        item["lessondoc_pack"] = None
    try:
        ensure_course_doc_pack_schema(conn)
        folder_ids = [
            int(item["id"])
            for item in items
            if item.get("node_type") == "folder" and item.get("id") is not None
        ]
        if not folder_ids:
            return items
        placeholders = ",".join("?" for _ in folder_ids)
        rows = conn.execute(
            f"""
            SELECT p.id, p.root_material_id, p.theme, p.spec_version,
                   p.assets_fingerprint,
                   SUM(CASE WHEN l.gen_status IS NOT NULL AND l.gen_status != 'excluded'
                            THEN 1 ELSE 0 END) AS total_count,
                   SUM(CASE WHEN l.gen_status = 'ready' THEN 1 ELSE 0 END) AS ready_count
            FROM course_doc_packs p
            LEFT JOIN course_doc_pack_lessons l ON l.pack_id = p.id
            WHERE p.status = 'active' AND p.root_material_id IN ({placeholders})
            GROUP BY p.id, p.root_material_id, p.theme, p.spec_version,
                     p.assets_fingerprint
            """,
            folder_ids,
        ).fetchall()
        current_fp = assets_module.assets_fingerprint()
        by_root = {int(row["root_material_id"]): row for row in rows}
        for item in items:
            row = by_root.get(int(item["id"])) if item.get("id") is not None else None
            if row is None:
                continue
            item["lessondoc_pack"] = {
                "pack_id": int(row["id"]),
                "theme": row["theme"],
                "spec_version": row["spec_version"],
                "ready_count": int(row["ready_count"] or 0),
                "total_count": int(row["total_count"] or 0),
                # 引擎版本治理(R5):包内 assets 是生成时刻的副本,平台引擎
                # 升级后指纹不再一致 → 前端提示「引擎可更新」。
                "assets_outdated": str(row["assets_fingerprint"] or "") != current_fp,
            }
    except Exception:
        pass
    return items


def find_pack_for_offering(conn, *, class_offering_id: int) -> dict[str, Any] | None:
    """课堂 → 它当前绑定的 LessonDoc 包(没有则 None)。

    解析路径:先看课堂首页主材料,再看各课次主材料;任一材料上溯到包根后
    命中 pack 登记表即返回。用 ``find_html_package_root`` 复用既有上溯逻辑,
    因此手工上传的旧 HTML 包不会误判为 LessonDoc 包(它们没有 pack 行)。
    """
    from ..html_package_service import find_html_package_root  # noqa: PLC0415

    ensure_course_doc_pack_schema(conn)
    candidate_ids: list[int] = []
    home = conn.execute(
        "SELECT home_learning_material_id FROM class_offerings WHERE id = ? LIMIT 1",
        (int(class_offering_id),),
    ).fetchone()
    if home is not None and home["home_learning_material_id"]:
        candidate_ids.append(int(home["home_learning_material_id"]))
    session_rows = conn.execute(
        """
        SELECT learning_material_id FROM class_offering_sessions
        WHERE class_offering_id = ? AND learning_material_id IS NOT NULL
        ORDER BY order_index
        """,
        (int(class_offering_id),),
    ).fetchall()
    candidate_ids.extend(int(row["learning_material_id"]) for row in session_rows)

    seen: set[int] = set()
    for material_id in candidate_ids:
        if material_id in seen:
            continue
        seen.add(material_id)
        row = conn.execute(
            "SELECT * FROM course_materials WHERE id = ? LIMIT 1", (material_id,)
        ).fetchone()
        if row is None:
            continue
        package = find_html_package_root(conn, row)
        if not package:
            continue
        pack = get_pack_by_root(conn, int(package["root_node_id"]))
        if pack and pack.get("status") == "active":
            return pack
    return None


def list_pack_lessons(conn, pack_id: int) -> list[dict[str, Any]]:
    ensure_course_doc_pack_schema(conn)
    rows = conn.execute(
        "SELECT * FROM course_doc_pack_lessons WHERE pack_id = ? ORDER BY lesson_no",
        (int(pack_id),),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["warnings"] = json.loads(item.get("warnings_json") or "[]")
        except (ValueError, TypeError):
            item["warnings"] = []
        result.append(item)
    return result


def update_lesson_state(
    conn,
    *,
    pack_id: int,
    lesson_no: int,
    gen_status: str | None = None,
    user_hint: str | None = None,
    last_task_id: int | None = None,
    warnings: list[str] | None = None,
) -> None:
    """Upsert 单课状态行(缺行自动补建)."""
    ensure_course_doc_pack_schema(conn)
    if gen_status is not None and gen_status not in LESSON_STATUSES:
        raise LessonDocPackError(f"未知课次状态: {gen_status}")
    now = _now_iso()
    row = conn.execute(
        "SELECT id FROM course_doc_pack_lessons WHERE pack_id = ? AND lesson_no = ? LIMIT 1",
        (int(pack_id), int(lesson_no)),
    ).fetchone()
    if row is None:
        execute_insert_returning_id(
            conn,
            """
            INSERT INTO course_doc_pack_lessons
                (pack_id, lesson_no, gen_status, user_hint, last_task_id, warnings_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(pack_id),
                int(lesson_no),
                gen_status or "pending",
                user_hint or "",
                last_task_id,
                json.dumps(warnings or [], ensure_ascii=False),
                now,
            ),
            engine=get_configured_db_engine(),
        )
        return
    sets = ["updated_at = ?"]
    params: list[Any] = [now]
    if gen_status is not None:
        sets.append("gen_status = ?")
        params.append(gen_status)
    if user_hint is not None:
        sets.append("user_hint = ?")
        params.append(user_hint)
    if last_task_id is not None:
        sets.append("last_task_id = ?")
        params.append(int(last_task_id))
    if warnings is not None:
        sets.append("warnings_json = ?")
        params.append(json.dumps(warnings, ensure_ascii=False))
    params.append(int(row["id"]))
    conn.execute(
        f"UPDATE course_doc_pack_lessons SET {', '.join(sets)} WHERE id = ?",
        tuple(params),
    )


def touch_pack(conn, pack_id: int, *, theme: str | None = None) -> None:
    ensure_course_doc_pack_schema(conn)
    if theme:
        conn.execute(
            "UPDATE course_doc_packs SET theme = ?, updated_at = ? WHERE id = ?",
            (theme, _now_iso(), int(pack_id)),
        )
    else:
        conn.execute(
            "UPDATE course_doc_packs SET updated_at = ? WHERE id = ?",
            (_now_iso(), int(pack_id)),
        )


def archive_pack_for_material(conn, material_id: int) -> bool:
    """材料删除链钩子:被删节点若是包根,pack 置 archived(登记表不删,留审计)."""
    ensure_course_doc_pack_schema(conn)
    cursor = conn.execute(
        "UPDATE course_doc_packs SET status = 'archived', updated_at = ? "
        "WHERE root_material_id = ? AND status != 'archived'",
        (_now_iso(), int(material_id)),
    )
    return bool(getattr(cursor, "rowcount", 0))


# ---------------------------------------------------------------- 清单读写

def read_manifest(conn, pack: dict[str, Any]) -> dict[str, Any]:
    """从包内 course.json 读清单(真源);读不到时回退缓存."""
    row = _find_child(
        conn,
        teacher_id=int(pack["teacher_id"]),
        parent_id=int(pack["root_material_id"]),
        name=MANIFEST_FILE_NAME,
    )
    if row is not None:
        text = _load_file_text(conn, row)
        if text:
            try:
                payload = json.loads(text)
                if isinstance(payload, dict):
                    return payload
            except (ValueError, TypeError):
                pass
    cache = pack.get("manifest_cache")
    if isinstance(cache, dict) and cache:
        return cache
    raise LessonDocPackError("学习文档包的课程清单(course.json)缺失或损坏")


def write_manifest(conn, pack: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    """校验清单 → 写 course.json + 重渲 main.html + 刷缓存。返回告警."""
    clean, warnings = validate.validate_manifest(manifest)
    now = _now_iso()
    teacher_id = int(pack["teacher_id"])
    root_id_val = int(pack["root_material_id"])
    manifest_text = json.dumps(clean, ensure_ascii=False, indent=2)

    manifest_row = _find_child(conn, teacher_id=teacher_id, parent_id=root_id_val, name=MANIFEST_FILE_NAME)
    if manifest_row is None:
        _create_pack_file(conn, pack, name=MANIFEST_FILE_NAME, content=manifest_text, now=now)
    else:
        _update_file_content(conn, int(manifest_row["id"]), manifest_text, now)

    home_html = render.render_home_html(clean)
    home_row = _find_child(conn, teacher_id=teacher_id, parent_id=root_id_val, name="main.html")
    if home_row is None:
        _create_pack_file(conn, pack, name="main.html", content=home_html, now=now)
    else:
        _update_file_content(conn, int(home_row["id"]), home_html, now)

    conn.execute(
        "UPDATE course_doc_packs SET manifest_cache_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(clean, ensure_ascii=False), now, int(pack["id"])),
    )
    return warnings


def _create_pack_file(conn, pack: dict[str, Any], *, name: str, content: str, now: str,
                      parent_id: int | None = None, parent_path: str | None = None) -> dict[str, Any]:
    create_file, _, path_join, _ = _row_helpers()
    root_row = conn.execute(
        "SELECT * FROM course_materials WHERE id = ? LIMIT 1",
        (int(pack["root_material_id"]),),
    ).fetchone()
    if root_row is None:
        raise LessonDocPackError("学习文档包的包根材料不存在")
    base_parent = int(parent_id if parent_id is not None else pack["root_material_id"])
    base_path = parent_path if parent_path is not None else str(root_row["material_path"])
    return create_file(
        conn,
        teacher_id=int(pack["teacher_id"]),
        parent_id=base_parent,
        root_id=int(root_row["root_id"]),
        material_path=path_join(base_path, name),
        name=name,
        content=content,
        now=now,
    )


# ---------------------------------------------------------------- 课次文件写入

def write_lesson_files(conn, pack: dict[str, Any], lesson_no: int, deck: dict[str, Any]) -> list[str]:
    """deck(已过 validate_deck)→ 写 lesson_N/lesson_N.html(新建或覆盖)。返回告警."""
    clean, warnings = validate.validate_deck(deck, expected_lesson=lesson_no)
    now = _now_iso()
    teacher_id = int(pack["teacher_id"])
    root_id_val = int(pack["root_material_id"])
    _, create_folder, path_join, _ = _row_helpers()

    root_row = conn.execute(
        "SELECT * FROM course_materials WHERE id = ? LIMIT 1", (root_id_val,)
    ).fetchone()
    if root_row is None:
        raise LessonDocPackError("学习文档包的包根材料不存在")

    dir_name = f"lesson_{int(lesson_no)}"
    entry_name = f"{dir_name}.html"
    folder_row = _find_child(conn, teacher_id=teacher_id, parent_id=root_id_val, name=dir_name)
    if folder_row is None:
        folder_row = create_folder(
            conn,
            teacher_id=teacher_id,
            parent_id=root_id_val,
            root_id=int(root_row["root_id"]),
            material_path=path_join(str(root_row["material_path"]), dir_name),
            name=dir_name,
            now=now,
        )
    html_text = render.render_lesson_html(clean)
    entry_row = _find_child(conn, teacher_id=teacher_id, parent_id=int(folder_row["id"]), name=entry_name)
    if entry_row is None:
        _create_pack_file(
            conn,
            pack,
            name=entry_name,
            content=html_text,
            now=now,
            parent_id=int(folder_row["id"]),
            parent_path=str(folder_row["material_path"]),
        )
    else:
        _update_file_content(conn, int(entry_row["id"]), html_text, now)
    return warnings


# ---------------------------------------------------------------- 建包骨架

def create_pack_skeleton(
    conn,
    *,
    teacher_id: int,
    course_id: int,
    manifest: dict[str, Any],
    theme: str | None = None,
    pack_name: str | None = None,
) -> dict[str, Any]:
    """创建包骨架:根目录 + assets 引擎副本 + README + course.json + main.html
    + pack 登记 + 逐课状态行(pending)。

    返回 {pack, root_material_id, warnings}。调用方负责 conn.commit()。
    """
    ensure_course_doc_pack_schema(conn)
    clean, warnings = validate.validate_manifest(manifest)
    if theme:
        clean["theme"] = theme
    course_name = str((clean.get("course") or {}).get("name") or "").strip()
    name = (pack_name or f"{course_name}{PACK_FOLDER_SUFFIX}").strip()
    if not name:
        raise LessonDocPackError("包名不能为空")

    existing = conn.execute(
        """
        SELECT id FROM course_materials
        WHERE teacher_id = ? AND parent_id IS NULL AND name = ?
        LIMIT 1
        """,
        (int(teacher_id), name),
    ).fetchone()
    if existing is not None:
        raise LessonDocPackError(f"材料库根目录已存在同名文件夹「{name}」,请换个包名")

    now = _now_iso()
    create_file, create_folder, path_join, _ = _row_helpers()

    root_row = create_folder(
        conn,
        teacher_id=int(teacher_id),
        parent_id=None,
        root_id=None,
        material_path=name,
        name=name,
        now=now,
    )
    root_material_id = int(root_row["id"])
    root_tree_id = int(root_row["root_id"])

    assets_row = create_folder(
        conn,
        teacher_id=int(teacher_id),
        parent_id=root_material_id,
        root_id=root_tree_id,
        material_path=path_join(name, "assets"),
        name="assets",
        now=now,
    )
    for asset_name, asset_text in assets_module.load_all_assets().items():
        create_file(
            conn,
            teacher_id=int(teacher_id),
            parent_id=int(assets_row["id"]),
            root_id=root_tree_id,
            material_path=path_join(str(assets_row["material_path"]), asset_name),
            name=asset_name,
            content=asset_text,
            now=now,
        )

    readme = (
        f"# {course_name} · 课程学习文档包\n\n"
        "- 入口:`main.html`(课程首页,含总览思维导图与课次导航)\n"
        "- 课次:`lesson_N/lesson_N.html`(内嵌 LessonDoc 2.0 配置 JSON,可直接编辑)\n"
        "- 本包由 lanshare 平台课程学习文档模板生成,离线双击 main.html 即可使用。\n"
    )
    create_file(
        conn,
        teacher_id=int(teacher_id),
        parent_id=root_material_id,
        root_id=root_tree_id,
        material_path=path_join(name, "README.md"),
        name="README.md",
        content=readme,
        now=now,
    )

    pack_id = execute_insert_returning_id(
        conn,
        """
        INSERT INTO course_doc_packs
            (root_material_id, course_id, teacher_id, spec_version, theme, status,
             assets_fingerprint, manifest_cache_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
        """,
        (
            root_material_id,
            int(course_id),
            int(teacher_id),
            spec.SPEC_VERSION,
            str(clean.get("theme") or spec.DEFAULT_THEME),
            assets_module.assets_fingerprint(),
            json.dumps(clean, ensure_ascii=False),
            now,
            now,
        ),
        engine=get_configured_db_engine(),
    )
    pack = get_pack(conn, pack_id)

    manifest_warnings = write_manifest(conn, pack, clean)
    warnings.extend(w for w in manifest_warnings if w not in warnings)

    for lesson in clean.get("lessons") or []:
        update_lesson_state(
            conn,
            pack_id=pack_id,
            lesson_no=int(lesson["n"]),
            gen_status="pending",
            user_hint=str(lesson.get("userHint") or ""),
        )
    return {"pack": get_pack(conn, pack_id), "root_material_id": root_material_id, "warnings": warnings}


# ---------------------------------------------------------------- 引擎刷新

def refresh_pack_assets(conn, pack: dict[str, Any]) -> int:
    """把平台最新引擎覆盖进包内 assets/(只动 assets,不碰内容文件)。返回更新文件数."""
    now = _now_iso()
    teacher_id = int(pack["teacher_id"])
    assets_row = _find_child(
        conn, teacher_id=teacher_id, parent_id=int(pack["root_material_id"]), name="assets"
    )
    if assets_row is None:
        raise LessonDocPackError("学习文档包缺少 assets 目录")
    updated = 0
    for asset_name, asset_text in assets_module.load_all_assets().items():
        file_row = _find_child(conn, teacher_id=teacher_id, parent_id=int(assets_row["id"]), name=asset_name)
        if file_row is None:
            _create_pack_file(
                conn,
                pack,
                name=asset_name,
                content=asset_text,
                now=now,
                parent_id=int(assets_row["id"]),
                parent_path=str(assets_row["material_path"]),
            )
        else:
            _update_file_content(conn, int(file_row["id"]), asset_text, now)
        updated += 1
    conn.execute(
        "UPDATE course_doc_packs SET assets_fingerprint = ?, updated_at = ? WHERE id = ?",
        (assets_module.assets_fingerprint(), now, int(pack["id"])),
    )
    return updated
