"""可渲染材料解析（renderer registry）。

在原有的 Markdown / 文本 / 图片在线预览之外，新增"直接渲染"能力：
教师上传的 HTML 单体文件、或携带 html+css+js 的前端项目目录，可以在浏览器中
直接渲染，而不是查看源码。

设计为一个可扩展的渲染器注册表：每种可渲染类型实现一个 ``MaterialRenderer``，
负责"检测某个材料节点是否可渲染"以及"找到渲染入口文件"。后续若要支持更多
类型（如 Jupyter、SVG 动画、可执行的 Mermaid 文档等），只需新增一个渲染器并
注册到 :data:`MATERIAL_RENDERERS`，无需改动调用方。

对外主要接口：
- :func:`resolve_render_target` —— 解析单个材料节点的渲染目标（或 None）。
- :func:`attach_render_metadata` —— 为一批序列化后的材料字典批量附加渲染元数据。
- :func:`resolve_render_file` —— 渲染路由按子路径解析出最终要返回的文件行。
"""

from __future__ import annotations

from typing import Iterable, Protocol

from .materials_service import is_git_internal_material_path, normalize_material_path


HTML_EXTENSIONS = {"html", "htm"}
HTML_INDEX_BASENAMES = ("index.html", "index.htm", "index.xhtml")


def _row_get(row, key, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _file_extension(name: str | None) -> str:
    text = str(name or "").strip()
    if "." not in text or text.startswith("."):
        return ""
    return text.rsplit(".", 1)[-1].lower()


def _material_path_parts(path_value: str | None) -> list[str]:
    return [part for part in str(path_value or "").replace("\\", "/").split("/") if part and part != "."]


def _parent_path(path_value: str | None) -> str:
    parts = _material_path_parts(path_value)
    return "/".join(parts[:-1])


class MaterialRenderer(Protocol):
    """渲染器协议。新增渲染类型时实现本协议即可。"""

    kind: str
    label: str

    def resolve(self, conn, material_row) -> dict | None:
        """返回渲染目标 dict（至少含 ``entry_id``），不可渲染时返回 None。"""
        ...


class HtmlRenderer:
    """HTML 单体文件 / 前端项目目录渲染器。"""

    kind = "html"
    label = "渲染"

    def _is_html_file(self, row) -> bool:
        if _row_get(row, "node_type") != "file":
            return False
        ext = str(_row_get(row, "file_ext") or "").lower() or _file_extension(_row_get(row, "name"))
        return ext in HTML_EXTENSIONS

    def _find_folder_entry(self, conn, folder_row) -> dict | None:
        root_id = _row_get(folder_row, "root_id")
        folder_path = str(_row_get(folder_row, "material_path") or "")
        if root_id is None or not folder_path:
            return None
        rows = conn.execute(
            """
            SELECT id, parent_id, root_id, name, material_path, preview_type, node_type, file_ext
            FROM course_materials
            WHERE root_id = ?
              AND node_type = 'file'
              AND (material_path = ? OR material_path LIKE ?)
            """,
            (root_id, folder_path, f"{folder_path}/%"),
        ).fetchall()

        folder_depth = len(_material_path_parts(folder_path))
        best_row = None
        best_score = None
        for row in rows:
            path_value = str(_row_get(row, "material_path") or "")
            if is_git_internal_material_path(path_value):
                continue
            ext = str(_row_get(row, "file_ext") or "").lower() or _file_extension(_row_get(row, "name"))
            if ext not in HTML_EXTENSIONS:
                continue
            name_lower = str(_row_get(row, "name") or "").strip().lower()
            relative_depth = max(0, len(_material_path_parts(path_value)) - folder_depth)
            # 入口优先级：根级 index.html > 浅层 index.html > 浅层任意 html。
            score = (
                1 if name_lower in HTML_INDEX_BASENAMES else 0,
                -relative_depth,
                0 if ext == "html" else -1,
                -len(path_value),
            )
            if best_score is None or score > best_score:
                best_score = score
                best_row = dict(row)
        return best_row

    def resolve(self, conn, material_row) -> dict | None:
        if material_row is None:
            return None
        if is_git_internal_material_path(_row_get(material_row, "material_path")):
            return None

        node_type = _row_get(material_row, "node_type")
        entry_row: dict | None = None
        if node_type == "file" and self._is_html_file(material_row):
            entry_row = dict(material_row)
        elif node_type == "folder":
            entry_row = self._find_folder_entry(conn, material_row)

        if not entry_row:
            return None

        node_id = int(_row_get(material_row, "id") or 0)
        if node_id <= 0:
            return None
        return {
            "kind": self.kind,
            "label": self.label,
            "node_id": node_id,
            "entry_id": int(entry_row.get("id") or 0),
            "entry_path": str(entry_row.get("material_path") or ""),
            "entry_name": str(entry_row.get("name") or ""),
            "render_url": f"/materials/render/{node_id}/",
        }


# 渲染器注册表 —— 新增渲染类型时在此追加即可。
MATERIAL_RENDERERS: list[MaterialRenderer] = [HtmlRenderer()]


def resolve_render_target(conn, material_row) -> dict | None:
    """按注册顺序解析第一个能渲染该材料的渲染目标。"""
    for renderer in MATERIAL_RENDERERS:
        target = renderer.resolve(conn, material_row)
        if target:
            return target
    return None


def attach_render_metadata(conn, items: list[dict]) -> list[dict]:
    """为序列化后的材料字典批量附加渲染元数据。"""
    for item in items:
        target = resolve_render_target(conn, item)
        if target:
            item["is_renderable"] = True
            item["render_kind"] = target["kind"]
            item["render_label"] = target["label"]
            item["render_url"] = target["render_url"]
            item["render_entry_id"] = target["entry_id"]
        else:
            item["is_renderable"] = False
            item["render_kind"] = ""
            item["render_label"] = ""
            item["render_url"] = ""
            item["render_entry_id"] = 0
    return items


def _resolve_base_and_entry(conn, node_row) -> tuple[str, str]:
    """返回 (base_path, default_entry_relative_path)。

    - 文件夹节点：base 为该目录，入口为目录内最佳 HTML。
    - HTML 文件节点：base 为其所在目录，入口为该文件本身。
    """
    node_type = _row_get(node_row, "node_type")
    node_path = str(_row_get(node_row, "material_path") or "")
    if node_type == "folder":
        target = HtmlRenderer().resolve(conn, node_row)
        if not target:
            return node_path, ""
        entry_path = str(target.get("entry_path") or "")
        base_parts = _material_path_parts(node_path)
        entry_parts = _material_path_parts(entry_path)
        relative = "/".join(entry_parts[len(base_parts):]) if entry_parts[: len(base_parts)] == base_parts else entry_path
        return node_path, relative

    # 单体 HTML 文件：base 为父目录，入口为文件名。
    base_path = _parent_path(node_path)
    file_name = _material_path_parts(node_path)[-1] if _material_path_parts(node_path) else node_path
    return base_path, file_name


def resolve_render_file(conn, node_row, subpath: str = ""):
    """渲染路由解析：根据节点与子路径，定位最终要返回的文件行。

    返回 ``course_materials`` 行（sqlite Row / dict），定位失败时返回 None。
    会对子路径做归一化以防目录穿越，并确保目标在 base 目录之内、同一 root 内。
    """
    base_path, default_entry = _resolve_base_and_entry(conn, node_row)
    relative = str(subpath or "").strip().strip("/")
    if not relative:
        relative = default_entry
    if not relative:
        return None

    combined = f"{base_path}/{relative}" if base_path else relative
    # normalize_material_path 会拒绝 ".." 等穿越，返回干净的 posix 路径。
    target_path = normalize_material_path(combined)

    # 目标必须位于 base 目录之内（base 为空表示 root 级别，允许）。
    if base_path and not (target_path == base_path or target_path.startswith(f"{base_path}/")):
        return None
    if is_git_internal_material_path(target_path):
        return None

    root_id = _row_get(node_row, "root_id")
    row = conn.execute(
        """
        SELECT *
        FROM course_materials
        WHERE root_id = ?
          AND material_path = ?
          AND node_type = 'file'
        LIMIT 1
        """,
        (root_id, target_path),
    ).fetchone()
    return row
