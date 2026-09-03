"""Resolve an owned material, nested entry or pending skeleton to an editor target."""

import re
import os
from pathlib import PurePosixPath

from . import editor_service as editor, pack_service


def editor_enabled():
    return os.environ.get("LESSONDOC_EDITOR_ENABLED", "true").strip().lower() not in {"0", "false", "off", "no"}


def inspect_material(conn, *, material_id, teacher_id, subpath=""):
    from ..html_package_service import find_html_package_root
    from ..material_render_service import resolve_render_file

    row = conn.execute("SELECT * FROM course_materials WHERE id=?", (material_id,)).fetchone()
    if row is None:
        raise editor.EditorError("MATERIAL_NOT_FOUND", "材料不存在", 404)
    if int(row["teacher_id"]) != teacher_id:
        raise editor.EditorError("FORBIDDEN", "只能编辑自己的材料", 403)
    if not editor_enabled():
        return dict(editable=False, reason_code="EDITOR_DISABLED", reason="学习文档编辑器暂时维护中，现有文档仍可阅读", legacy_convertible=False)
    if subpath:
        path = PurePosixPath(subpath)
        if "\\" in subpath or ":" in subpath or path.is_absolute() or ".." in path.parts:
            raise editor.EditorError("INVALID_PATH", "材料路径无效")
        row = resolve_render_file(conn, row, subpath)
        if row is None or int(row["teacher_id"]) != teacher_id:
            raise editor.EditorError("MATERIAL_NOT_FOUND", "未找到指定的文档页面", 404)
    target = dict(row)
    context = pack_service.registered_material_context(conn, target)
    if context:
        pack, relative = context["pack"], context["relative_path"]
        editor.owned_pack(conn, pack["id"], teacher_id)
        if relative in {".", "main.html", "course.json"}:
            number = 0
        else:
            match = re.fullmatch(r"lesson_(\d+)(?:/lesson_\1\.html)?", relative)
            if not match:
                return dict(editable=False, reason_code="PACKAGE_ASSET", reason="这是文档资源，请打开课程首页或课次页面进行编辑", pack_id=pack["id"], legacy_convertible=False)
            number = int(match.group(1))
            editor._lesson_state(conn, pack, number)
        return dict(editable=True, reason_code="EDITABLE", kind="lesson" if number else "home", pack_id=pack["id"],
                    lesson_no=number, root_material_id=pack["root_material_id"], course_id=pack["course_id"], legacy_convertible=False,
                    editor_url=f"/materials/lessondoc-editor/{pack['id']}?lesson={number}")
    package = find_html_package_root(conn, target)
    if package:
        root = conn.execute("SELECT teacher_id FROM course_materials WHERE id=?", (package["root_node_id"],)).fetchone()
        if root is not None and int(root["teacher_id"]) == teacher_id:
            return dict(editable=False, reason_code="LEGACY_CONVERSION_REQUIRED", reason="旧版学习文档需要先转换，转换结果将保存为新文档包",
                        legacy_convertible=True, root_material_id=package["root_node_id"])
    return dict(editable=False, reason_code="UNSUPPORTED_MATERIAL", reason="此编辑器支持学习文档包；其他材料请使用其对应的编辑方式", legacy_convertible=False)


def legacy_context(conn, *, material_id, teacher_id):
    target = inspect_material(conn, material_id=material_id, teacher_id=teacher_id)
    if not target.get("legacy_convertible"):
        raise editor.EditorError("LEGACY_REQUIRED", target.get("reason") or "此材料无需转换", 409)
    # Match the existing import route's owner/offering/academic-sync authorization.
    courses = conn.execute("""SELECT c.id,c.name FROM courses c WHERE c.created_by_teacher_id=?
        OR EXISTS(SELECT 1 FROM class_offerings o WHERE o.course_id=c.id AND o.teacher_id=?)
        OR EXISTS(SELECT 1 FROM teacher_academic_course_sync_items s WHERE s.course_id=c.id AND s.teacher_id=?)
        ORDER BY c.name,c.id""", (teacher_id, teacher_id, teacher_id)).fetchall()
    return dict(root_material_id=target["root_material_id"], courses=[dict(row) for row in courses])
