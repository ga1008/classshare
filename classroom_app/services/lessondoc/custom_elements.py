"""Teacher-scoped reusable elements; instances own independent media references."""

import copy
import json
import uuid
from urllib.parse import urlsplit, urlunsplit

from ...db.connection import execute_insert_returning_id
from ...db.schema_lessondoc_editor import ensure_lessondoc_editor_schema
from ..file_service import resolve_global_file_path
from . import editor_service as editor, media, pack_service, spec
from .model import check_budget, walk_model
from .validate_html import sanitize_svg_markup


def normalize_elements(elements):
    try:
        check_budget(elements)
    except ValueError as exc:
        raise editor.EditorError("INVALID_ELEMENT", str(exc), 422) from exc
    if not isinstance(elements, list) or not 1 <= len(elements) <= spec.MAX_POSITIONED_PER_SLIDE:
        raise editor.EditorError("INVALID_ELEMENT", "请选择 1—40 个元素", 422)
    models = copy.deepcopy(elements)
    for i, model in enumerate(models):
        if isinstance(model, dict) and model.get("flowFrame"):
            model["frame"] = model.pop("flowFrame")
        if isinstance(model, dict) and not model.get("frame"):
            model["frame"] = dict(x=80, y=80 + i * 24, w=600, h=320)
    wrapper = dict(spec="lessondoc/2.0", kind="lesson", lesson=1, title="元素", slides=[dict(id="s_template", layout="canvas", objects=models)])
    clean, warnings, diagnostics = editor.normalize_document(wrapper, 1)
    if any(item["destructive"] for item in diagnostics):
        raise editor.EditorError("CONTENT_LOSS", "自定义元素存在无法保存的内容", 422, diagnostics=diagnostics)
    result = clean["slides"][0]["objects"]
    # A reusable element has no document navigation context. Object actions that
    # refer outside its subtree have already been removed by normal validation.
    for _, node in walk_model(result):
        if node.get("actions"):
            original = node["actions"]
            node["actions"] = [action for action in original if action["do"] not in {"goto", "prev", "next"}]
            if len(node["actions"]) != len(original):
                warnings.append("已移除依赖原课次页序的跳转动作")
            if not node["actions"]:
                node.pop("actions")
    return result, warnings


def normalize_element(element):
    elements, warnings = normalize_elements([element])
    return elements[0], warnings


def _library(conn, teacher_id):
    # Lock before looking for the root, including the first concurrent creation.
    changed = conn.execute("UPDATE teachers SET id=id WHERE id=?", (teacher_id,))
    if not changed.rowcount:
        raise editor.EditorError("FORBIDDEN", "教师账号不存在", 403)
    name = "学习文档元素素材"
    root = conn.execute("SELECT * FROM course_materials WHERE teacher_id=? AND parent_id IS NULL AND name=? AND node_type='folder' ORDER BY id LIMIT 1",
                        (teacher_id, name)).fetchone()
    if root is not None:
        return dict(root)
    _, create_folder, _, _ = pack_service._row_helpers()
    return create_folder(conn, teacher_id=teacher_id, parent_id=None, root_id=None, material_path=name, name=name, now=pack_service._now_iso())


def _owned(conn, element_id, teacher_id):
    ensure_lessondoc_editor_schema(conn)
    row = conn.execute("SELECT * FROM lessondoc_custom_elements WHERE id=? AND teacher_id=?", (element_id, teacher_id)).fetchone()
    if row is None:
        raise editor.EditorError("ELEMENT_NOT_FOUND", "自定义元素不存在或不属于你", 404)
    return dict(row)


def list_elements(conn, *, teacher_id, before_id=0, limit=60):
    ensure_lessondoc_editor_schema(conn)
    rows = conn.execute("""SELECT id,name,category,thumbnail_svg,updated_at FROM lessondoc_custom_elements
        WHERE teacher_id=? AND (?=0 OR id<?) ORDER BY id DESC LIMIT ?""", (teacher_id, before_id, before_id, limit + 1)).fetchall()
    return dict(items=[dict(row) for row in rows[:limit]], next_cursor=rows[limit - 1]["id"] if len(rows) > limit else None)


def save_element(conn, *, teacher_id, pack_id, lesson_no, name, element, category="custom", thumbnail_svg=""):
    ensure_lessondoc_editor_schema(conn)
    model, warnings = normalize_element(element)
    label = str(name or "").strip()[:80]
    if not label:
        raise editor.EditorError("NAME_REQUIRED", "请输入元素名称", 422)
    thumbnail = sanitize_svg_markup(str(thumbnail_svg or "")[:32000], warnings, where="元素缩略图")
    library = _library(conn, teacher_id)
    pack = editor._lock_pack(conn, pack_id, teacher_id)
    editor._lesson_state(conn, pack, lesson_no)
    dependencies = {}
    def retain(src):
        if src not in dependencies:
            row, _ = media.resolve_reference(conn, pack, lesson_no, src)
            reference = media.reference_file(conn, library, teacher_id=teacher_id, file_hash=row["file_hash"], file_size=row["file_size"],
                                             extension=row["file_ext"] or "bin", mime=row["mime_type"])
            dependencies[src] = dict(src=src, material_id=reference["id"], file_hash=reference["file_hash"], extension=reference["file_ext"])
        return src
    media.map_resources(model, retain)
    count = conn.execute("SELECT COUNT(*) FROM lessondoc_custom_elements WHERE teacher_id=?", (teacher_id,)).fetchone()[0]
    if count >= 300:
        raise editor.EditorError("ELEMENT_LIMIT", "最多保留 300 个自定义元素，请先整理元素库", 422)
    now = pack_service._now_iso()
    element_id = execute_insert_returning_id(conn, """INSERT INTO lessondoc_custom_elements
        (teacher_id,name,category,model_json,source_pack_id,media_json,thumbnail_svg,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?)""", (teacher_id, label, str(category or "custom")[:40], json.dumps(model, ensure_ascii=False),
        pack_id, json.dumps(list(dependencies.values()), ensure_ascii=False), thumbnail, now, now))
    return dict(id=element_id, name=label, warnings=warnings)


def rename_element(conn, *, teacher_id, element_id, name):
    _owned(conn, element_id, teacher_id)
    label = str(name or "").strip()[:80]
    if not label:
        raise editor.EditorError("NAME_REQUIRED", "请输入元素名称", 422)
    conn.execute("UPDATE lessondoc_custom_elements SET name=?,updated_at=? WHERE id=? AND teacher_id=?", (label, pack_service._now_iso(), element_id, teacher_id))
    return dict(id=element_id, name=label)


def delete_element(conn, *, teacher_id, element_id):
    _owned(conn, element_id, teacher_id)
    conn.execute("DELETE FROM lessondoc_custom_elements WHERE id=? AND teacher_id=?", (element_id, teacher_id))
    # Shared resources stay as ordinary library materials; another template or
    # already inserted instance can refer to the same bytes.
    return dict(deleted=True)


def _unique_instance(model):
    result = copy.deepcopy(model)
    mapping = {node["id"]: "b_" + uuid.uuid4().hex[:16] for _, node in walk_model(result) if node.get("id") and node.get("type") in spec.BLOCK_TYPES}
    for _, node in walk_model(result):
        if node.get("type") in spec.BLOCK_TYPES and node.get("id") in mapping:
            node["id"] = mapping[node["id"]]
        for action in node.get("actions") or []:
            if action.get("target") in mapping:
                action["target"] = mapping[action["target"]]
    return result


def _materialize(conn, *, pack, lesson_no, model, dependencies):
    dependencies = list(dependencies)
    if not dependencies:
        return _unique_instance(model)
    target = media.pack_media_folder(conn, pack)
    mapping = {}
    for dependency in dependencies:
        row = conn.execute("SELECT * FROM course_materials WHERE id=? AND teacher_id=? AND file_hash=? AND node_type='file'",
                           (dependency["material_id"], pack["teacher_id"], dependency["file_hash"])).fetchone()
        if row is None or not resolve_global_file_path(dependency["file_hash"]):
            raise editor.EditorError("MEDIA_MISSING", f"元素素材已被移除：{dependency['src']}", 422)
        row = dict(row)
        copied = media.reference_file(conn, target, teacher_id=pack["teacher_id"], file_hash=row["file_hash"], file_size=row["file_size"],
                                     extension=row["file_ext"] or "bin", mime=row["mime_type"])
        # Query and fragment refer to the resource itself, not its former folder.
        parts = urlsplit(dependency["src"])
        mapping[dependency["src"]] = urlunsplit(("", "", media.describe(copied, pack, lesson_no)["src"], parts.query, parts.fragment))
    return _unique_instance(media.map_resources(model, lambda src: mapping[src]))


def insert_element(conn, *, teacher_id, element_id, pack_id, lesson_no):
    template = _owned(conn, element_id, teacher_id)
    pack = editor._lock_pack(conn, pack_id, teacher_id)
    editor._lesson_state(conn, pack, lesson_no)
    model = _materialize(conn, pack=pack, lesson_no=lesson_no, model=json.loads(template["model_json"]), dependencies=json.loads(template["media_json"]))
    return dict(element=model, name=template["name"])


def copy_element(conn, *, teacher_id, source_pack_id, source_lesson_no, pack_id, lesson_no, element):
    result = copy_elements(conn, teacher_id=teacher_id, source_pack_id=source_pack_id, source_lesson_no=source_lesson_no,
                           pack_id=pack_id, lesson_no=lesson_no, elements=[element])
    return dict(element=result["elements"][0], warnings=result["warnings"])


def copy_elements(conn, *, teacher_id, source_pack_id, source_lesson_no, pack_id, lesson_no, elements):
    model, warnings = normalize_elements(elements)
    # Lock packs in a stable order; two tabs copying in opposite directions cannot deadlock.
    packs = {pid: editor._lock_pack(conn, pid, teacher_id) for pid in sorted({source_pack_id, pack_id})}
    source, destination = packs[source_pack_id], packs[pack_id]
    editor._lesson_state(conn, source, source_lesson_no)
    editor._lesson_state(conn, destination, lesson_no)
    dependencies = {}
    def collect(src):
        if src not in dependencies:
            row, _ = media.resolve_reference(conn, source, source_lesson_no, src)
            dependencies[src] = dict(src=src, material_id=row["id"], file_hash=row["file_hash"])
        return src
    media.map_resources(model, collect)
    return dict(elements=_materialize(conn, pack=destination, lesson_no=lesson_no, model=model, dependencies=dependencies.values()), warnings=warnings)
