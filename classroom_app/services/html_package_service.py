"""HTML 包（html package）学习文档服务。

约定的包结构（与 Markdown 学习文档并列的另一种课程材料形态）：

- 包根目录内有 ``main.html`` 作为课程首页（容忍 ``index.html``）。
- 每次课一个子文件夹，命名 ``lesson_N``（阿拉伯数字，如 ``lesson_11``），
  文件夹内有本课次入口 ``lesson_N.html``，以及本课次的配套静态资源。
- 课次共享资源放在根目录下与课次文件夹不同名的独立文件夹内
  （如 ``common/``、``assets/``），课次页面用 ``../common/...`` 相对路径引用。

本服务负责：
- :func:`parse_html_package` —— 判断一个文件夹节点是否为合法 HTML 包并解析结构。
- :func:`find_html_package_root` —— 从包内任意节点向上定位包根。
- :func:`apply_package_session_bindings` —— 确定性地把 main.html 绑定为课程首页、
  ``lesson_N`` 入口绑定到第 N 次课（不依赖 AI 猜测）。
- :func:`extract_html_text` —— 抽取 HTML 文字内容供 AI 知识注入使用。
"""

from __future__ import annotations

import html as html_module
import re
from datetime import datetime
from typing import Any

from .materials_service import (
    is_git_internal_material_path,
    sync_classroom_learning_material_assignments,
)

LESSON_DIR_RE = re.compile(r"^lesson[_-]?0*(\d{1,3})$", re.IGNORECASE)
LESSON_ENTRY_RE = re.compile(r"^lesson[_-]?0*(\d{1,3})\.html?$", re.IGNORECASE)
MAIN_ENTRY_NAMES = ("main.html", "main.htm", "index.html", "index.htm")
HTML_FILE_EXTENSIONS = {"html", "htm"}
# 向上寻找包根时最多回溯的层级（入口文件距包根最多 2 层：pkg/lesson_N/lesson_N.html）。
MAX_PACKAGE_ANCESTOR_DEPTH = 4


def _row_get(row, key, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _path_parts(path_value: str | None) -> list[str]:
    return [part for part in str(path_value or "").replace("\\", "/").split("/") if part and part != "."]


def lesson_number_from_dir_name(name: str | None) -> int:
    match = LESSON_DIR_RE.match(str(name or "").strip())
    return int(match.group(1)) if match else 0


def lesson_number_from_entry_name(name: str | None) -> int:
    match = LESSON_ENTRY_RE.match(str(name or "").strip())
    return int(match.group(1)) if match else 0


def is_package_entry_candidate_name(name: str | None) -> bool:
    """节点名是否可能是 HTML 包入口（main.html / lesson_N.html / lesson_N 目录）。

    用于批量列表序列化时做廉价预过滤，避免对每个 HTML 文件都做包解析。
    """
    normalized = str(name or "").strip().lower()
    if normalized in MAIN_ENTRY_NAMES:
        return True
    return bool(LESSON_ENTRY_RE.match(normalized) or LESSON_DIR_RE.match(normalized))


def _load_subtree_rows(conn, folder_row) -> list[dict]:
    root_id = _row_get(folder_row, "root_id")
    folder_path = str(_row_get(folder_row, "material_path") or "")
    if root_id is None or not folder_path:
        return []
    rows = conn.execute(
        """
        SELECT id, parent_id, root_id, name, material_path, preview_type, node_type, file_ext, file_hash
        FROM course_materials
        WHERE root_id = ?
          AND (material_path = ? OR material_path LIKE ?)
        """,
        (root_id, folder_path, f"{folder_path}/%"),
    ).fetchall()
    return [dict(row) for row in rows if not is_git_internal_material_path(_row_get(row, "material_path"))]


def _relative_parts(base_parts: list[str], path_value: str) -> list[str] | None:
    parts = _path_parts(path_value)
    if parts[: len(base_parts)] != base_parts:
        return None
    return parts[len(base_parts):]


def _pick_lesson_entry(lesson_number: int, files_at_root: list[dict]) -> dict | None:
    """课次文件夹内选入口：lesson_N.html > index.html > 唯一的 html 文件。"""
    html_files = [
        row for row in files_at_root
        if str(_row_get(row, "file_ext") or "").lower() in HTML_FILE_EXTENSIONS
        or str(_row_get(row, "name") or "").lower().endswith((".html", ".htm"))
    ]
    for row in html_files:
        if lesson_number_from_entry_name(_row_get(row, "name")) == lesson_number:
            return row
    for row in html_files:
        if str(_row_get(row, "name") or "").strip().lower() in ("index.html", "index.htm"):
            return row
    if len(html_files) == 1:
        return html_files[0]
    return None


def _main_entry_priority(row: dict) -> int:
    name = str(_row_get(row, "name") or "").strip().lower()
    try:
        return MAIN_ENTRY_NAMES.index(name)
    except ValueError:
        return len(MAIN_ENTRY_NAMES)


def parse_html_package(conn, folder_row) -> dict[str, Any] | None:
    """解析一个文件夹节点是否为 HTML 包。

    返回结构（不是包时返回 None）::

        {
            "root_node_id": int,            # 包根节点 id
            "root_id": int,                 # course_materials.root_id
            "root_path": str,
            "main_entry": {..row..} | None, # main.html 文件行
            "main_relpath": "main.html",
            "lessons": [
                {"number": 3, "folder": {..}, "entry": {..}, "entry_relpath": "lesson_3/lesson_3.html"}
            ],
            "lesson_by_number": {3: lesson_item},
            "shared_dirs": ["common", ...],
        }
    """
    if _row_get(folder_row, "node_type") != "folder":
        return None
    if is_git_internal_material_path(_row_get(folder_row, "material_path")):
        return None

    root_path = str(_row_get(folder_row, "material_path") or "")
    base_parts = _path_parts(root_path)
    subtree = _load_subtree_rows(conn, folder_row)
    if not subtree:
        return None

    main_entry: dict | None = None
    lesson_dirs: dict[int, dict] = {}
    files_by_parent_rel: dict[str, list[dict]] = {}
    shared_dirs: list[str] = []

    for row in subtree:
        rel = _relative_parts(base_parts, str(row.get("material_path") or ""))
        if rel is None or not rel:
            continue
        if row.get("node_type") == "folder" and len(rel) == 1:
            number = lesson_number_from_dir_name(rel[0])
            if number > 0:
                # 同一编号出现多个目录（如 lesson_3 与 lesson_03）时保留先出现的。
                lesson_dirs.setdefault(number, row)
            else:
                shared_dirs.append(rel[0])
            continue
        if row.get("node_type") == "file":
            if len(rel) == 1 and str(row.get("name") or "").strip().lower() in MAIN_ENTRY_NAMES:
                if main_entry is None or _main_entry_priority(row) < _main_entry_priority(main_entry):
                    main_entry = row
            parent_rel = "/".join(rel[:-1])
            files_by_parent_rel.setdefault(parent_rel, []).append(row)

    lessons: list[dict] = []
    for number in sorted(lesson_dirs):
        folder = lesson_dirs[number]
        folder_rel = _relative_parts(base_parts, str(folder.get("material_path") or ""))
        folder_rel_text = "/".join(folder_rel or [])
        entry = _pick_lesson_entry(number, files_by_parent_rel.get(folder_rel_text, []))
        if not entry:
            continue
        lessons.append(
            {
                "number": number,
                "folder": folder,
                "entry": entry,
                "entry_relpath": f"{folder_rel_text}/{entry.get('name')}",
            }
        )

    if not main_entry or not lessons:
        return None

    return {
        "root_node_id": int(_row_get(folder_row, "id") or 0),
        "root_id": int(_row_get(folder_row, "root_id") or 0),
        "root_path": root_path,
        "main_entry": main_entry,
        "main_relpath": str(main_entry.get("name") or "main.html"),
        "lessons": lessons,
        "lesson_by_number": {item["number"]: item for item in lessons},
        "shared_dirs": sorted(set(shared_dirs)),
    }


def find_html_package_root(conn, material_row) -> dict[str, Any] | None:
    """从包内任意节点（入口文件 / 课次文件夹 / 包根自身）向上定位 HTML 包。"""
    if material_row is None:
        return None
    if _row_get(material_row, "node_type") == "folder":
        package = parse_html_package(conn, material_row)
        if package:
            return package

    current_parent_id = _row_get(material_row, "parent_id")
    for _depth in range(MAX_PACKAGE_ANCESTOR_DEPTH):
        if not current_parent_id:
            return None
        parent = conn.execute(
            "SELECT * FROM course_materials WHERE id = ? LIMIT 1",
            (int(current_parent_id),),
        ).fetchone()
        if not parent or _row_get(parent, "node_type") != "folder":
            return None
        package = parse_html_package(conn, parent)
        if package:
            return package
        current_parent_id = _row_get(parent, "parent_id")
    return None


def package_relative_path(package: dict[str, Any], material_path: str | None) -> str:
    rel = _relative_parts(_path_parts(package.get("root_path")), str(material_path or ""))
    return "/".join(rel or [])


def build_package_outline_text(package: dict[str, Any]) -> str:
    lines = [f"HTML 包根目录：{package.get('root_path')}"]
    lines.append(f"- 课程首页：{package.get('main_relpath')}")
    for lesson in package.get("lessons", []):
        lines.append(f"- 第 {lesson['number']} 次课：{lesson['entry_relpath']}")
    if package.get("shared_dirs"):
        lines.append(f"- 共享资源目录：{'、'.join(package['shared_dirs'])}")
    return "\n".join(lines)


_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def extract_html_text(raw_html: str, *, max_chars: int = 12000) -> str:
    """把 HTML 压成纯文本（去 script/style/标签，解实体，压空白）。

    LessonDoc 2.0 壳页的正文全部在内嵌 JSON 里（会被 script 剥除规则清空），
    检测到标志时改抽 JSON 文本字段；失败静默回落旧路径。
    """
    text = str(raw_html or "")
    if 'data-lessondoc="' in text[:2000]:
        try:
            from .lessondoc import extract_deck_text, extract_embedded_json

            payload = extract_embedded_json(text)
            if payload:
                extracted = extract_deck_text(payload, max_chars=max_chars)
                if extracted:
                    return extracted
        except Exception:
            pass
    text = _SCRIPT_STYLE_RE.sub(" ", text)
    text = _COMMENT_RE.sub(" ", text)
    text = re.sub(r"</(p|div|li|tr|h[1-6]|section|article|br)\s*>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub(" ", text)
    text = html_module.unescape(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip() + "\n[内容已截断]"
    return cleaned


def load_material_file_text(conn, material_row) -> str:
    """读取文件节点的文本内容（多编码兜底），失败返回空串。"""
    file_hash = _row_get(material_row, "file_hash")
    if not file_hash:
        return ""
    from .file_service import resolve_global_file_path

    file_path = resolve_global_file_path(str(file_hash))
    if not file_path:
        return ""
    try:
        raw_bytes = file_path.read_bytes()
    except OSError:
        return ""
    for encoding in ("utf-8", "gbk", "gb2312", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw_bytes.decode("utf-8", errors="replace")


def apply_package_session_bindings(
    conn,
    *,
    package: dict[str, Any],
    offering_ids: list[int],
    teacher_id: int,
) -> dict[str, Any]:
    """确定性绑定：main.html → 课程首页；lesson_N 入口 → 各课堂第 N 次课。

    只做加法与覆盖（首页/课次主材料指向包入口），不清理其他已绑定材料。
    返回与 AI 自动绑定接口一致形状的 assignments 摘要。
    """
    now = datetime.now().isoformat()
    valid_home_assignments: list[dict] = []
    valid_assignments: list[dict] = []
    skipped: list[dict] = []

    main_entry_id = int(_row_get(package.get("main_entry"), "id") or 0)
    lesson_by_number = package.get("lesson_by_number") or {}

    for offering_id in offering_ids:
        offering = conn.execute(
            "SELECT id, teacher_id FROM class_offerings WHERE id = ? AND teacher_id = ? LIMIT 1",
            (int(offering_id), int(teacher_id)),
        ).fetchone()
        if not offering:
            skipped.append({"class_offering_id": offering_id, "reason": "课堂不存在或无权操作"})
            continue

        if main_entry_id > 0:
            conn.execute(
                "UPDATE class_offerings SET home_learning_material_id = ? WHERE id = ? AND teacher_id = ?",
                (main_entry_id, int(offering_id), int(teacher_id)),
            )
            valid_home_assignments.append(
                {
                    "target_type": "home",
                    "class_offering_id": int(offering_id),
                    "session_id": None,
                    "session_title": "课程首页",
                    "order_index": 0,
                    "material_id": main_entry_id,
                    "material_path": str(_row_get(package.get("main_entry"), "material_path") or ""),
                    "confidence": "high",
                    "source": "html_package",
                }
            )

        session_rows = conn.execute(
            """
            SELECT id, order_index, title
            FROM class_offering_sessions
            WHERE class_offering_id = ?
            ORDER BY order_index
            """,
            (int(offering_id),),
        ).fetchall()
        bound_material_ids: list[int] = [main_entry_id] if main_entry_id > 0 else []
        for session in session_rows:
            order_index = int(_row_get(session, "order_index") or 0)
            lesson = lesson_by_number.get(order_index)
            if not lesson:
                continue
            entry_id = int(_row_get(lesson.get("entry"), "id") or 0)
            if entry_id <= 0:
                continue
            conn.execute(
                """
                UPDATE class_offering_sessions
                SET learning_material_id = ?, updated_at = ?
                WHERE id = ? AND class_offering_id = ?
                """,
                (entry_id, now, int(_row_get(session, "id")), int(offering_id)),
            )
            bound_material_ids.append(entry_id)
            valid_assignments.append(
                {
                    "target_type": "lesson",
                    "class_offering_id": int(offering_id),
                    "session_id": int(_row_get(session, "id")),
                    "session_title": str(_row_get(session, "title") or ""),
                    "order_index": order_index,
                    "material_id": entry_id,
                    "material_path": str(_row_get(lesson.get("entry"), "material_path") or ""),
                    "confidence": "high",
                    "source": "html_package",
                }
            )

        if bound_material_ids:
            sync_classroom_learning_material_assignments(
                conn,
                class_offering_id=int(offering_id),
                teacher_id=int(teacher_id),
                material_ids=bound_material_ids,
            )

    matched_numbers = {item["order_index"] for item in valid_assignments}
    unmatched = [
        {"lesson_number": lesson["number"], "entry_relpath": lesson["entry_relpath"]}
        for lesson in package.get("lessons", [])
        if lesson["number"] not in matched_numbers
    ]

    return {
        "status": "success",
        "message": (
            f"已按 HTML 包规范绑定：{len(valid_home_assignments)} 个课程首页、{len(valid_assignments)} 个课次入口。"
            + (f" {len(unmatched)} 个课次文件夹没有对应课次序号，未绑定。" if unmatched else "")
        ),
        "binding_mode": "html_package",
        "total_assignments": len(valid_assignments),
        "total_home_assignments": len(valid_home_assignments),
        "assignments": valid_home_assignments + valid_assignments,
        "lesson_assignments": valid_assignments,
        "home_assignments": valid_home_assignments,
        "skipped_assignments": skipped,
        "unmatched_lessons": unmatched,
    }
