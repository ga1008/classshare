"""Coordinate registered LessonDoc files with material Git writeback.

Capture before network I/O, prepare bounded JSON outside the transaction, then
lock/check before generic material sync. Protected sources never pass through the
generic raw file updater. The unified saver creates history and projections.
"""

import hashlib
import json
import re
import uuid
from pathlib import PurePosixPath

from ...db.schema_course_doc_packs import ensure_course_doc_pack_schema
from . import assets, editor_service as editor, pack_service, render, spec


def capture(conn, root_row, rows):
    ensure_course_doc_pack_schema(conn)
    by_id = {int(row["id"]): row for row in rows}
    by_id[int(root_row["id"])] = dict(root_row)
    result = {}
    for row in conn.execute("SELECT * FROM course_doc_packs WHERE teacher_id=? AND status='active' ORDER BY id", (root_row["teacher_id"],)).fetchall():
        pack = pack_service._serialize_pack(row)
        root = by_id.get(int(pack["root_material_id"]))
        if root is None:
            continue
        prefix = str(root["material_path"])
        pack_rows = [item for item in by_id.values() if item["material_path"] == prefix or item["material_path"].startswith(prefix + "/")]
        files = {str(PurePosixPath(item["material_path"]).relative_to(PurePosixPath(root_row["material_path"]))):
                 (int(item["id"]), item["node_type"], str(item.get("file_hash") or "")) for item in pack_rows}
        states = [dict(item) for item in conn.execute("SELECT lesson_no,gen_status,updated_at FROM course_doc_pack_lessons WHERE pack_id=? ORDER BY lesson_no", (pack["id"],)).fetchall()]
        relative = str(PurePosixPath(prefix).relative_to(PurePosixPath(root_row["material_path"])))
        result[int(pack["id"])] = dict(pack=pack, prefix="" if relative == "." else relative, files=files, states=states)
    return result


def _path(snapshot, name):
    return f"{snapshot['prefix']}/{name}" if snapshot["prefix"] else name


def _file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _read_text(path):
    if path.stat().st_size > 3 * 1024 * 1024:
        raise editor.EditorError("DOCUMENT_TOO_LARGE", f"Git 中的学习文档过大：{path.name}", 413)
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeError as exc:
        raise editor.EditorError("INVALID_GIT_DOCUMENT", f"Git 文档不是 UTF-8：{path.name}", 422) from exc


def prepare(workspace, baseline):
    prepared, protected = [], set()
    root = workspace.resolve()
    for pack_id, snapshot in baseline.items():
        home_path = _path(snapshot, "course.json")
        paths = {0: home_path}
        for state in snapshot["states"]:
            number = int(state["lesson_no"])
            paths[number] = _path(snapshot, f"lesson_{number}/lesson_{number}.html")
        derived = {_path(snapshot, "main.html")} | {_path(snapshot, f"assets/{name}") for name in spec.ASSET_FILES}
        protected.update(paths.values())
        protected.update(derived)
        pack_root = root / PurePosixPath(snapshot["prefix"])
        for candidate in pack_root.glob("lesson_*/lesson_*.html"):
            relative = candidate.relative_to(root).as_posix()
            if re.fullmatch(r"lesson_(\d+)/lesson_\1\.html", candidate.relative_to(pack_root).as_posix()) and relative not in paths.values():
                raise editor.EditorError("UNREGISTERED_GIT_LESSON", "Git 新增了尚未登记的课次，请先在课程管理中确定课次结构", 422)
        # Removing a registered package/source requires its ordinary archive or
        # lesson settings action, so bindings cannot silently point to deleted rows.
        for name in set(paths.values()) | derived:
            target = root / PurePosixPath(name)
            if root not in target.resolve().parents or target.is_symlink():
                raise editor.EditorError("INVALID_GIT_PATH", "学习文档文件必须位于仓库内", 422)
            if name in snapshot["files"] and not target.is_file():
                raise editor.EditorError("REGISTERED_DOCUMENT_REMOVED", f"Git 删除了已登记的学习文档文件：{name}；请使用材料管理操作调整文档包", 422)
        changes, warnings = [], []
        for number, name in paths.items():
            target = root / PurePosixPath(name)
            if not target.is_file():
                continue
            if target.stat().st_size > 3 * 1024 * 1024:
                raise editor.EditorError("DOCUMENT_TOO_LARGE", f"Git 中的学习文档过大：{name}", 413)
            content = target.read_bytes()
            old_hash = (snapshot["files"].get(name) or (None, None, editor.ABSENT_REVISION))[2]
            if hashlib.sha256(content).hexdigest() == old_hash:
                continue
            try:
                text = content.decode("utf-8-sig")
                document = render.extract_embedded_json(text) if number else json.loads(text)
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise editor.EditorError("INVALID_GIT_DOCUMENT", f"Git 文档无法读取：{name}", 422) from exc
            clean, notes, diagnostics = editor.normalize_document(document, number, editor_ids=False)
            if any(item["destructive"] for item in diagnostics):
                raise editor.EditorError("CONTENT_LOSS", f"Git 文档净化会丢失内容：{name}", 422, diagnostics=diagnostics)
            warnings.extend(notes)
            changes.append(dict(lesson_no=number, document=clean, revision=old_hash, path=name))
        # main.html is derived; a changed shell must be regenerated even when
        # course.json is unchanged. Embedded main-only data edits are not a source.
        main_path = _path(snapshot, "main.html")
        main = root / PurePosixPath(main_path)
        old_main_hash = (snapshot["files"].get(main_path) or (None, None, ""))[2]
        if main.is_file() and _file_digest(main) != old_main_hash:
            if not any(item["lesson_no"] == 0 for item in changes):
                try:
                    manifest = json.loads(_read_text(root / PurePosixPath(home_path)))
                except (json.JSONDecodeError, RecursionError) as exc:
                    raise editor.EditorError("INVALID_GIT_DOCUMENT", "课程清单不是有效 JSON", 422) from exc
                if render.extract_embedded_json(_read_text(main)) != manifest:
                    raise editor.EditorError("DERIVED_HOME_EDITED", "首页数据以 course.json 为准，请把 main.html 中的数据改动同步到 course.json", 422)
                changes.insert(0, dict(lesson_no=0, document=manifest, revision=snapshot["files"][home_path][2], path=home_path))
        asset_changes = any(
            (root / PurePosixPath(name)).is_file() and
            _file_digest(root / PurePosixPath(name)) != (snapshot["files"].get(name) or (None, None, ""))[2]
            for name in derived if name != main_path)
        if asset_changes:
            warnings.append("Git 中的 LessonDoc 引擎改动已替换为平台引擎，媒体文件保留")
        prepared.append(dict(pack_id=pack_id, snapshot=snapshot, changes=changes, refresh=asset_changes, warnings=warnings))
    return dict(packs=prepared, protected=protected)


def lock_and_check(conn, root_row, rows_loader, baseline):
    for pack_id in sorted(baseline):
        editor._lock_pack(conn, pack_id, int(root_row["teacher_id"]))
    current = capture(conn, root_row, rows_loader(conn, root_row))
    if set(current) != set(baseline) or any(current[key]["files"] != value["files"] or current[key]["states"] != value["states"] for key, value in baseline.items()):
        raise editor.EditorError("GIT_REVISION_CONFLICT", "Git 操作期间学习文档已更新，本次回写已取消；请重新执行以纳入最新修改", 409)


def apply(conn, prepared):
    entries, warnings = [], []
    for item in prepared["packs"]:
        pack = item["snapshot"]["pack"]
        warnings.extend(item["warnings"])
        for change in item["changes"]:
            saved = editor.save_document(conn, pack_id=pack["id"], teacher_id=pack["teacher_id"], lesson_no=change["lesson_no"],
                                         document=change["document"], expected_revision=change["revision"],
                                         operation_id="git_" + uuid.uuid4().hex, source="git", force_render=True)
            warnings.extend(saved["warnings"])
            row = conn.execute("SELECT material_path FROM course_materials WHERE id=?", (saved["material_id"],)).fetchone()
            entries.append(dict(status="updated" if change["revision"] != editor.ABSENT_REVISION else "inserted",
                                relative_path=change["path"], material_path=row["material_path"], id=saved["material_id"], material_id=saved["material_id"], node_type="file",
                                name=PurePosixPath(change["path"]).name, preview_type="html"))
        if item["refresh"] or pack.get("assets_fingerprint") != assets.assets_fingerprint():
            pack_service.refresh_pack_assets(conn, pack)
    return entries, warnings
