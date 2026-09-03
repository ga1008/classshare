"""Bounded media intake, ordinary material references and package-relative reuse."""

import copy
import posixpath
from html import escape
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from lxml import etree, html
from PIL import Image, UnidentifiedImageError

from ...db.connection import execute_insert_returning_id
from ..file_service import resolve_global_file_path, store_file_object_globally
from ..materials_service import infer_material_profile
from . import editor_service as editor, pack_service
from .model import walk_model
from .paths import local_src_ok
from .validate_html import sanitize_svg_markup

MIB = 1024 * 1024
FORMATS = {
    "png": ("image", "image/png"), "jpg": ("image", "image/jpeg"), "jpeg": ("image", "image/jpeg"),
    "gif": ("image", "image/gif"), "webp": ("image", "image/webp"), "svg": ("image", "image/svg+xml"),
    "mp3": ("audio", "audio/mpeg"), "wav": ("audio", "audio/wav"), "ogg": ("audio", "audio/ogg"),
    "m4a": ("audio", "audio/mp4"), "mp4": ("video", "video/mp4"), "webm": ("video", "video/webm"),
}
LIMITS = {"image": 8 * MIB, "audio": 20 * MIB, "video": 100 * MIB}


def upload_profile(filename, content_type):
    ext = PurePosixPath(str(filename).replace("\\", "/")).suffix.lower().lstrip(".")
    if ext not in FORMATS:
        raise editor.EditorError("UNSUPPORTED_MEDIA", "支持 PNG、JPG、GIF、WebP、SVG、MP3、WAV、OGG、M4A、MP4 和 WebM", 415)
    kind, mime = FORMATS[ext]
    supplied = str(content_type or "").split(";", 1)[0].lower()
    aliases = {"audio/x-wav": "audio/wav", "image/jpg": "image/jpeg", "application/ogg": "audio/ogg"}
    if aliases.get(supplied, supplied) not in {"", "application/octet-stream", mime}:
        raise editor.EditorError("MEDIA_TYPE_MISMATCH", "文件扩展名与内容类型不一致", 415)
    return dict(extension=ext, kind=kind, mime=mime, limit=LIMITS[kind])


def verify_and_store(stream, profile):
    stream.seek(0)
    head = stream.read(64)
    ext = profile["extension"]
    valid = (
        (ext == "png" and head.startswith(b"\x89PNG\r\n\x1a\n")) or
        (ext in {"jpg", "jpeg"} and head.startswith(b"\xff\xd8\xff")) or
        (ext == "gif" and head.startswith((b"GIF87a", b"GIF89a"))) or
        (ext == "webp" and head.startswith(b"RIFF") and head[8:12] == b"WEBP") or
        (ext == "wav" and head.startswith(b"RIFF") and head[8:12] == b"WAVE") or
        (ext == "mp3" and (head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 255 and head[1] & 224 == 224))) or
        (ext == "ogg" and head.startswith(b"OggS")) or
        (ext in {"mp4", "m4a"} and head[4:8] == b"ftyp") or
        (ext == "webm" and head.startswith(b"\x1a\x45\xdf\xa3"))
    )
    warnings = []
    if ext == "svg":
        stream.seek(0)
        try:
            raw = stream.read().decode("utf-8-sig")
            source = etree.fromstring(raw.encode("utf-8"), etree.XMLParser(resolve_entities=False, no_network=True))
            if etree.QName(source).localname != "svg":
                raise ValueError("not svg")
            clean = sanitize_svg_markup(raw, warnings, where="素材")
            root = etree.fromstring(clean.encode("utf-8"), etree.XMLParser(resolve_entities=False, no_network=True))
            root.set("xmlns", "http://www.w3.org/2000/svg")
            payload = etree.tostring(root, encoding="utf-8")
        except (ValueError, UnicodeError, etree.XMLSyntaxError) as exc:
            raise editor.EditorError("INVALID_MEDIA", "SVG 内容无效", 422) from exc
        stream.seek(0)
        stream.truncate()
        stream.write(payload)
        valid = True
    if not valid:
        raise editor.EditorError("INVALID_MEDIA", "文件内容与所选媒体格式不符", 422)
    if profile["kind"] == "image" and ext != "svg":
        stream.seek(0)
        try:
            with Image.open(stream) as picture:
                if picture.width * picture.height > 36_000_000:
                    raise editor.EditorError("IMAGE_TOO_LARGE", "图片分辨率不能超过 3600 万像素", 413)
                picture.verify()
        except editor.EditorError:
            raise
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
            raise editor.EditorError("INVALID_MEDIA", "图片文件损坏或无法读取", 422) from exc
    stream.seek(0, 2)
    if stream.tell() > profile["limit"]:
        raise editor.EditorError("MEDIA_TOO_LARGE", "素材超过大小限制", 413)
    result = store_file_object_globally(stream)
    result.update(extension=ext, kind=profile["kind"], mime=profile["mime"], warnings=warnings)
    return result


def folder(conn, parent, name, teacher_id):
    _, create_folder, path_join, _ = pack_service._row_helpers()
    row = pack_service._find_child(conn, teacher_id=teacher_id, parent_id=parent["id"], name=name)
    if row is not None:
        if row["node_type"] != "folder":
            raise editor.EditorError("MEDIA_FOLDER_CONFLICT", f"资源目录 {name} 被同名文件占用", 409)
        return dict(row)
    return create_folder(conn, teacher_id=teacher_id, parent_id=parent["id"], root_id=parent["root_id"],
                         material_path=path_join(parent["material_path"], name), name=name, now=pack_service._now_iso())


def reference_file(conn, parent, *, teacher_id, file_hash, file_size, extension, mime=None):
    """A normal course_materials row keeps shared bytes alive across template deletion."""
    name = file_hash + "." + extension
    row = pack_service._find_child(conn, teacher_id=teacher_id, parent_id=parent["id"], name=name)
    if row is not None:
        if row["node_type"] != "file" or row["file_hash"] != file_hash:
            raise editor.EditorError("MEDIA_REFERENCE_CONFLICT", "素材名称已被其他文件占用", 409)
        return dict(row)
    profile = infer_material_profile(name, mime)
    now = pack_service._now_iso()
    row_id = execute_insert_returning_id(conn, """INSERT INTO course_materials
        (teacher_id,parent_id,root_id,material_path,name,node_type,mime_type,preview_type,ai_capability,file_ext,file_hash,file_size,
         ai_parse_status,ai_optimize_status,created_at,updated_at)
         VALUES(?,?,?,?,?,'file',?,?,?,?,?,?,'idle','idle',?,?)""",
         (teacher_id, parent["id"], parent["root_id"], parent["material_path"] + "/" + name, name, profile["mime_type"],
          profile["preview_type"], profile["ai_capability"], extension, file_hash, file_size, now, now))
    return dict(conn.execute("SELECT * FROM course_materials WHERE id=?", (row_id,)).fetchone())


def pack_media_folder(conn, pack):
    root = dict(conn.execute("SELECT * FROM course_materials WHERE id=?", (pack["root_material_id"],)).fetchone())
    shared = folder(conn, root, "assets", pack["teacher_id"])
    return folder(conn, shared, "media", pack["teacher_id"])


def describe(row, pack, lesson_no):
    path = "assets/media/" + row["name"]
    return dict(material_id=row["id"], file_hash=row["file_hash"], name=row["name"], kind=FORMATS.get(row["file_ext"], ("file",))[0],
                size=row["file_size"], src=("../" if lesson_no else "") + path,
                preview_url=f"/materials/render/{pack['root_material_id']}/{quote(path)}")


def attach_upload(conn, *, pack_id, teacher_id, lesson_no, stored):
    pack = editor._lock_pack(conn, pack_id, teacher_id)
    editor._lesson_state(conn, pack, lesson_no)
    row = reference_file(conn, pack_media_folder(conn, pack), teacher_id=teacher_id, file_hash=stored["hash"],
                         file_size=stored["size"], extension=stored["extension"], mime=stored["mime"])
    return {**describe(row, pack, lesson_no), "warnings": stored["warnings"]}


def list_media(conn, *, pack_id, teacher_id, lesson_no=0, after_id=0, limit=100):
    pack = editor.owned_pack(conn, pack_id, teacher_id)
    editor._lesson_state(conn, pack, lesson_no)
    root = conn.execute("SELECT material_path,root_id FROM course_materials WHERE id=?", (pack["root_material_id"],)).fetchone()
    prefix = str(root["material_path"])
    escaped = prefix.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    params = [teacher_id, root["root_id"], escaped + "/%", after_id, *FORMATS, limit + 1]
    rows = conn.execute(f"""SELECT * FROM course_materials WHERE teacher_id=? AND root_id=? AND material_path LIKE ? ESCAPE '!'
        AND id>? AND node_type='file' AND file_ext IN ({','.join('?' for _ in FORMATS)}) ORDER BY id LIMIT ?""", tuple(params)).fetchall()
    items = []
    for raw in rows[:limit]:
        row = dict(raw)
        relative = posixpath.relpath(row["material_path"], prefix)
        base = f"lesson_{lesson_no}" if lesson_no else "."
        src = posixpath.relpath(relative, base)
        # Existing lesson-local assets can be selected from their lesson or home;
        # another lesson's private relative directory is outside the URL contract.
        if not local_src_ok(src):
            continue
        items.append(dict(material_id=row["id"], name=row["name"], size=row["file_size"], kind=FORMATS[row["file_ext"]][0], src=src,
                          file_hash=row["file_hash"], preview_url=f"/materials/render/{pack['root_material_id']}/{quote(relative)}"))
    return dict(items=items, next_cursor=int(rows[limit - 1]["id"]) if len(rows) > limit else None)


def map_resources(document, transform):
    """Map media/background paths and parsed HTML resources without textual substitution."""
    out = copy.deepcopy(document)
    for _, node in walk_model(out):
        for field in ("src", "poster", "href"):
            if isinstance(node.get(field), str) and node[field] and not node[field].startswith("#"):
                node[field] = transform(node[field])
        if node.get("type") == "html" and node.get("body"):
            root = html.fragment_fromstring(node["body"], create_parent="div")
            for el in root.iterdescendants():
                for attr in ("src", "href"):
                    value = el.get(attr)
                    if value and not value.startswith("#"):
                        el.set(attr, transform(value))
            node["body"] = escape(root.text or "", quote=False) + "".join(html.tostring(el, encoding="unicode", method="html") for el in root)
    return out


def resolve_reference(conn, pack, lesson_no, src, *, relative_dir=None):
    if not local_src_ok(src):
        raise editor.EditorError("INVALID_MEDIA_PATH", "素材路径不合法", 422)
    decoded = src
    for _ in range(4):
        decoded = unquote(decoded)
    parts = urlsplit(decoded)
    base = relative_dir if relative_dir is not None else f"lesson_{lesson_no}" if lesson_no else ""
    relative = posixpath.normpath(posixpath.join(base, parts.path))
    if relative.startswith("../") or relative.startswith("/"):
        raise editor.EditorError("INVALID_MEDIA_PATH", "素材路径不在当前文档包内", 422)
    root = conn.execute("SELECT material_path,root_id FROM course_materials WHERE id=?", (pack["root_material_id"],)).fetchone()
    row = conn.execute("SELECT * FROM course_materials WHERE teacher_id=? AND root_id=? AND material_path=? AND node_type='file'",
                       (pack["teacher_id"], root["root_id"], root["material_path"] + "/" + relative)).fetchone()
    if row is None or not resolve_global_file_path(row["file_hash"]):
        raise editor.EditorError("MEDIA_MISSING", f"引用素材不存在：{src}", 422, resource=src)
    return dict(row), parts


def copy_document_resources(conn, *, document, source_pack, target_pack, lesson_no, source_dir=None):
    """Legacy migration retains normal file references before the original is removed."""
    mapped = {}
    parent = None
    def retain(src):
        nonlocal parent
        if src not in mapped:
            row, parts = resolve_reference(conn, source_pack, lesson_no, src, relative_dir=source_dir)
            if parent is None:
                parent = pack_media_folder(conn, target_pack)
            reference = reference_file(conn, parent, teacher_id=target_pack["teacher_id"], file_hash=row["file_hash"],
                                       file_size=row["file_size"], extension=row["file_ext"] or "bin", mime=row["mime_type"])
            mapped[src] = urlunsplit(("", "", describe(reference, target_pack, lesson_no)["src"], parts.query, parts.fragment))
        return mapped[src]
    return map_resources(document, retain)


def check_references(conn, pack, lesson_no, document):
    missing, checked = [], set()
    def check(src):
        if src in checked:
            return src
        checked.add(src)
        try:
            resolve_reference(conn, pack, lesson_no, src)
        except editor.EditorError as exc:
            missing.append(dict(code=exc.code, path=src, severity="error", destructive=False, message=str(exc)))
        return src
    map_resources(document, check)
    return missing
