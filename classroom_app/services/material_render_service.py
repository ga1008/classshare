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

from urllib.parse import quote

from .html_package_service import (
    MAX_PACKAGE_ANCESTOR_DEPTH,
    find_html_package_root,
    is_package_entry_candidate_name,
    package_relative_path,
)
from .materials_service import is_git_internal_material_path, normalize_material_path


HTML_EXTENSIONS = {"html", "htm"}
# main.html 是 HTML 包规范的首页入口，优先级最高；index.* 兼容普通前端项目。
HTML_INDEX_BASENAMES = ("main.html", "main.htm", "index.html", "index.htm", "index.xhtml")


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


class MaterialRenderReadIndex:
    """Short-lived structural index; contains no permission decisions.

    Prefetch only requested subtrees and at most the package ancestor depth,
    keeping large unrelated roots and CSS/JS/blob metadata out of the read.
    A new index is built for each serialization, so edits and revocations
    cannot reuse results from an earlier request.
    """

    def __init__(self, conn, material_rows):
        rows = [dict(row) for row in material_rows]
        self.conn = conn
        self.rows_by_id = {int(row["id"]): row for row in rows}
        self.subtrees_by_id: dict[int, list[dict]] = {}
        self.package_cache: dict[int, dict | None] = {}
        candidates = [int(row["id"]) for row in self.rows_by_id.values()
                      if row.get("parent_id") and is_package_entry_candidate_name(row.get("name"))]
        for offset in range(0, len(candidates), 200):
            ids = candidates[offset:offset+200]
            placeholders = ",".join("?" for _ in ids)
            ancestors = conn.execute(
                f"""WITH RECURSIVE ancestors AS (
                    SELECT m.*, 0 AS render_ancestor_depth FROM course_materials m WHERE m.id IN ({placeholders})
                    UNION ALL
                    SELECT p.*, a.render_ancestor_depth + 1 FROM course_materials p
                    JOIN ancestors a ON p.id = a.parent_id
                    WHERE a.render_ancestor_depth < ?
                ) SELECT * FROM ancestors""",
                (*ids, MAX_PACKAGE_ANCESTOR_DEPTH),
            ).fetchall()
            for row in ancestors:
                data = dict(row)
                data.pop("render_ancestor_depth", None)
                self.rows_by_id[int(data["id"])] = data

        folders = [row for row in self.rows_by_id.values()
                   if row.get("node_type") == "folder" and row.get("root_id") is not None
                   and row.get("material_path") and not is_git_internal_material_path(row["material_path"])]
        folder_keys = {(int(row["root_id"]), str(row["material_path"])): int(row["id"]) for row in folders}
        self.subtrees_by_id = {int(row["id"]): [] for row in folders}
        # Collapse nested scopes. The package root already contains its lesson
        # folders; querying each lesson again would duplicate the same rows.
        scopes = []
        for root_id, path in sorted(folder_keys, key=lambda value: (value[0], len(value[1]), value[1])):
            parts = _material_path_parts(path)
            if any((root_id, "/".join(parts[:depth])) in folder_keys for depth in range(1, len(parts))):
                continue
            scopes.append((root_id, path, path + "/"))
        for offset in range(0, len(scopes), 100):
            batch = scopes[offset:offset+100]
            placeholders = ",".join("(?,?,?)" for _ in batch)
            descendants = conn.execute(
                f"""WITH scopes(root_id, path, prefix) AS (VALUES {placeholders})
                SELECT m.id, m.parent_id, m.root_id, m.name, m.material_path,
                       m.preview_type, m.node_type, m.file_ext, m.file_hash
                FROM course_materials m JOIN scopes s ON m.root_id = s.root_id
                 AND (m.material_path = s.path OR SUBSTR(m.material_path, 1, LENGTH(s.prefix)) = s.prefix)
                WHERE m.node_type = 'folder' OR (m.node_type = 'file' AND (
                    LOWER(m.file_ext) IN ('html', 'htm') OR LOWER(m.name) LIKE '%.html' OR LOWER(m.name) LIKE '%.htm'))""",
                tuple(value for scope in batch for value in scope),
            ).fetchall()
            for row in descendants:
                data = dict(row)
                path = str(data.get("material_path") or "")
                if is_git_internal_material_path(path):
                    continue
                parts = _material_path_parts(path)
                for depth in range(1, len(parts)+1):
                    folder_id = folder_keys.get((int(data["root_id"]), "/".join(parts[:depth])))
                    if folder_id is not None:
                        self.subtrees_by_id[folder_id].append(data)

    def find_package(self, material_row):
        return find_html_package_root(
            self.conn, material_row, rows_by_id=self.rows_by_id,
            subtrees_by_id=self.subtrees_by_id, package_cache=self.package_cache,
        )

    def get_node(self, material_id):
        return self.rows_by_id.get(int(material_id))


class HtmlRenderer:
    """HTML 单体文件 / 前端项目目录渲染器。"""

    kind = "html"
    label = "渲染"

    def _is_html_file(self, row) -> bool:
        if _row_get(row, "node_type") != "file":
            return False
        ext = str(_row_get(row, "file_ext") or "").lower() or _file_extension(_row_get(row, "name"))
        return ext in HTML_EXTENSIONS

    def _find_folder_entry(self, conn, folder_row, *, subtree_rows=None) -> dict | None:
        root_id = _row_get(folder_row, "root_id")
        folder_path = str(_row_get(folder_row, "material_path") or "")
        if root_id is None or not folder_path:
            return None
        prefix = f"{folder_path}/"
        rows = subtree_rows if subtree_rows is not None else conn.execute(
            """
            SELECT id, parent_id, root_id, name, material_path, preview_type, node_type, file_ext
            FROM course_materials
            WHERE root_id = ?
              AND node_type = 'file'
              AND SUBSTR(material_path, 1, LENGTH(?)) = ?
              AND (LOWER(file_ext) IN ('html', 'htm')
                   OR (COALESCE(file_ext, '') = '' AND (LOWER(name) LIKE '%.html' OR LOWER(name) LIKE '%.htm')))
            """,
            (root_id, prefix, prefix),
        ).fetchall()

        folder_depth = len(_material_path_parts(folder_path))
        best_row = None
        best_score = None
        for row in rows:
            if _row_get(row, "node_type") != "file":
                continue
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

    def resolve(self, conn, material_row, *, read_index=None) -> dict | None:
        if material_row is None:
            return None
        if is_git_internal_material_path(_row_get(material_row, "material_path")):
            return None

        node_type = _row_get(material_row, "node_type")
        entry_row: dict | None = None
        if node_type == "file" and self._is_html_file(material_row):
            entry_row = dict(material_row)
        elif node_type == "folder":
            entry_row = self._find_folder_entry(
                conn, material_row,
                subtree_rows=None if read_index is None else read_index.subtrees_by_id.get(int(material_row["id"]), []),
            )

        if not entry_row:
            return None

        node_id = int(_row_get(material_row, "id") or 0)
        if node_id <= 0:
            return None

        # HTML 包入口（main.html / lesson_N.html / lesson_N 目录）锚定到包根渲染：
        # URL 变成 /materials/render/{包根}/{相对路径}，浏览器可自行解析
        # ../common/... 之类的共享资源引用，服务端不需要放行目录穿越。
        render_node_id = node_id
        render_subpath = ""
        if is_package_entry_candidate_name(_row_get(material_row, "name")):
            package = find_html_package_root(conn, material_row) if read_index is None else read_index.find_package(material_row)
            if package and int(package.get("root_node_id") or 0) > 0:
                package_root_id = int(package["root_node_id"])
                relpath = package_relative_path(package, entry_row.get("material_path"))
                if package_root_id != node_id and relpath:
                    render_node_id = package_root_id
                    render_subpath = relpath

        if render_subpath:
            encoded = quote(render_subpath)
            render_url = f"/materials/render/{render_node_id}/{encoded}"
            shell_url = f"/materials/render-view/{render_node_id}?path={encoded}"
        else:
            render_url = f"/materials/render/{render_node_id}/"
            shell_url = f"/materials/render-view/{render_node_id}"

        return {
            "kind": self.kind,
            "label": self.label,
            "node_id": render_node_id,
            "source_node_id": node_id,
            "entry_id": int(entry_row.get("id") or 0),
            "entry_path": str(entry_row.get("material_path") or ""),
            "entry_name": str(entry_row.get("name") or ""),
            "render_subpath": render_subpath,
            "render_url": render_url,
            "shell_url": shell_url,
        }


# 渲染器注册表 —— 新增渲染类型时在此追加即可。
MATERIAL_RENDERERS: list[MaterialRenderer] = [HtmlRenderer()]


def resolve_render_target(conn, material_row, *, read_index=None) -> dict | None:
    """按注册顺序解析第一个能渲染该材料的渲染目标。"""
    for renderer in MATERIAL_RENDERERS:
        target = renderer.resolve(conn, material_row, read_index=read_index) if isinstance(renderer, HtmlRenderer) else renderer.resolve(conn, material_row)
        if target:
            return target
    return None


def resolve_render_targets(conn, material_rows) -> dict[int, dict | None]:
    """Resolve one request's entries using shared, permission-free structure."""
    rows = list(material_rows)
    read_index = MaterialRenderReadIndex(conn, rows)
    return {int(row["id"]): resolve_render_target(conn, row, read_index=read_index) for row in rows}


def attach_render_metadata(conn, items: list[dict]) -> list[dict]:
    """为序列化后的材料字典批量附加渲染元数据。"""
    targets = resolve_render_targets(conn, items)
    for item in items:
        target = targets[int(item["id"])]
        if target:
            item["is_renderable"] = True
            item["render_kind"] = target["kind"]
            item["render_label"] = target["label"]
            item["render_url"] = target["render_url"]
            item["render_shell_url"] = target.get("shell_url") or ""
            item["render_entry_id"] = target["entry_id"]
        else:
            item["is_renderable"] = False
            item["render_kind"] = ""
            item["render_label"] = ""
            item["render_url"] = ""
            item["render_shell_url"] = ""
            item["render_entry_id"] = 0
    return items


def _resolve_base_and_entry(conn, node_row, *, render_target=None) -> tuple[str, str]:
    """返回 (base_path, default_entry_relative_path)。

    - 文件夹节点：base 为该目录，入口为目录内最佳 HTML。
    - HTML 文件节点：base 为其所在目录，入口为该文件本身。
    """
    node_type = _row_get(node_row, "node_type")
    node_path = str(_row_get(node_row, "material_path") or "")
    if node_type == "folder":
        target = HtmlRenderer().resolve(conn, node_row) if render_target is None else render_target
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


def resolve_render_file(conn, node_row, subpath: str = "", *, render_target=None):
    """渲染路由解析：根据节点与子路径，定位最终要返回的文件行。

    返回 ``course_materials`` 行（sqlite Row / dict），定位失败时返回 None。
    会对子路径做归一化以防目录穿越，并确保目标在 base 目录之内、同一 root 内。
    render_target 仅复用当前请求已经解析的入口；不承载或缓存访问授权。
    """
    relative = str(subpath or "").strip().strip("/")
    if relative:
        node_path = str(_row_get(node_row, "material_path") or "")
        base_path = node_path if _row_get(node_row, "node_type") == "folder" else _parent_path(node_path)
        default_entry = ""
    else:
        base_path, default_entry = _resolve_base_and_entry(conn, node_row, render_target=render_target)
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
