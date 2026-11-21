"""Import, inspect and version teacher-owned project topics without classroom publication."""

from datetime import datetime, timezone
import hashlib
import io
import json
import mimetypes
from pathlib import Path, PurePosixPath
import zipfile

from fastapi import HTTPException

from .file_service import bind_global_file_references, resolve_global_file_path, store_file_object_globally
from .material_zip_service import extract_zip_upload_entries
from .project_topic_manifest import (MAX_FILES, compile_mapping, fail, inspect_resource, normalize_manifest,
                                     safe_path, suggest_manifest)

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 96 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
_MANIFEST_NAMES = ("project-topics.json", "manifest.json")
_IGNORED = {"__macosx", ".ds_store", ".git", "thumbs.db"}


def _json(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _extract(entries):
    if not entries or len(entries) > MAX_FILES:
        fail(f"请选择 1 至 {MAX_FILES} 个题包文件。")
    if len(entries) == 1 and PurePosixPath(entries[0][0]).suffix.lower() == ".zip":
        name, payload = entries[0]
        try:
            parts = extract_zip_upload_entries(name, payload, max_compressed=MAX_UPLOAD_BYTES,
                                              max_total=MAX_TOTAL_BYTES, max_file=MAX_FILE_BYTES,
                                              max_entries=MAX_FILES, wrap=False)
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as exc:
            raise HTTPException(422, "无法读取压缩包内容，请检查文件是否损坏或加密。") from exc
        entries = [(p["relative_path"], p["payload"]) for p in parts]
    result, names, total = {}, set(), 0
    for path, payload in entries:
        if any(s.casefold() in _IGNORED for s in path.replace("\\", "/").split("/")):
            continue
        path = safe_path(path)
        if path.casefold() in names:
            fail(f"包内文件路径重复：{path}")
        if not isinstance(payload, bytes) or len(payload) > MAX_FILE_BYTES:
            fail(f"{path} 超过单个题包文件大小限制。")
        total += len(payload)
        if total > MAX_TOTAL_BYTES:
            fail("题包展开后的文件总大小超限。")
        result[path] = payload
        names.add(path.casefold())
    if not result:
        fail("题包没有可导入的文件。")
    roots = {p.split("/", 1)[0] for p in result}
    if len(roots) == 1 and all("/" in p for p in result):
        return {p.split("/", 1)[1]: data for p, data in result.items()}
    return result


def _embedded_manifest(files):
    for name in _MANIFEST_NAMES:
        if name not in files:
            continue
        if len(files[name]) > MAX_MANIFEST_BYTES:
            if name == "project-topics.json":
                fail("题包清单超过大小上限。")
            continue
        try:
            value = json.loads(files[name].decode("utf-8-sig"))
        except (ValueError, UnicodeError, RecursionError):
            if name == "project-topics.json":
                fail("project-topics.json 需要有效的 UTF-8 JSON。")
            continue
        if name == "project-topics.json" and not isinstance(value, dict):
            fail("project-topics.json 需要包含题包标识和题目列表。")
        if name == "project-topics.json" or (isinstance(value, dict) and "topics" in value):
            return value
    return None


def _package_row(conn, package_id):
    row = conn.execute("SELECT * FROM project_topic_packages WHERE id=?", (package_id,)).fetchone()
    if not row:
        raise HTTPException(404, "题包版本不存在。")
    return row


def ensure_teacher_package_owner(conn, package_id, teacher_id):
    if not conn.execute("SELECT 1 FROM project_topic_packages WHERE id=? AND teacher_id=?",
                        (package_id, teacher_id)).fetchone():
        raise HTTPException(404, "题包版本不存在。")


def _summary(row):
    return {key: row[key] for key in ("id", "title", "version", "package_key")} | {
        "mapping_confirmed": bool(row["mapping_confirmed"])
    }


def list_teacher_packages(conn, teacher_id):
    rows = conn.execute("""SELECT id,title,version,package_key,mapping_confirmed FROM project_topic_packages
                           WHERE teacher_id=? ORDER BY id DESC""", (teacher_id,)).fetchall()
    return [_summary(row) for row in rows]


def get_package_version(conn, package_id):
    row = _package_row(conn, package_id)
    manifest = json.loads(row["manifest_json"])
    topics = conn.execute("SELECT item_json FROM project_topic_items WHERE package_id=? ORDER BY ordinal",
                          (package_id,)).fetchall()
    files = conn.execute("SELECT path,size_bytes FROM project_topic_files WHERE package_id=? ORDER BY path",
                         (package_id,)).fetchall()
    return {**_summary(row), "manifest": manifest, "topics": [json.loads(t["item_json"]) for t in topics],
            "files": [{"path": f["path"], "size": f["size_bytes"]} for f in files], "common": manifest["common"],
            "issues": json.loads(row["issues_json"]), "common_issues": json.loads(row["common_issues_json"])}


def get_teacher_package(conn, package_id, teacher_id):
    ensure_teacher_package_owner(conn, package_id, teacher_id)
    return get_package_version(conn, package_id)


def get_existing_classroom_copies(conn, package_id, offering_id):
    """Warn the managing teacher about pre-existing classroom grants for the same bytes."""
    from .account_control_service import table_exists
    from .material_publication_service import get_classroom_publication_context, is_restricted_material
    from .materials_service import is_descendant_path, is_git_internal_material_path

    if not table_exists(conn, "course_material_assignments"):
        return []
    published, restricted = get_classroom_publication_context(conn, offering_id)
    roots = {}
    for node in published:
        roots.setdefault(node["root_id"], []).append(node["material_path"])
    if not roots:
        return []
    marks = ",".join("?" for _ in roots)
    candidates = conn.execute(f"""
        SELECT f.path,m.root_id,m.material_path FROM course_materials m
        JOIN project_topic_files f ON f.file_hash=m.file_hash
        WHERE f.package_id=? AND f.size_bytes>0 AND m.root_id IN ({marks})
          AND NOT EXISTS (SELECT 1 FROM project_topic_resource_scopes s
                          WHERE s.package_id=f.package_id AND s.path=f.path AND s.topic_key='')
        ORDER BY f.path,m.material_path""", (package_id, *roots)).fetchall()
    return [{"path": r["path"], "material_path": r["material_path"]} for r in candidates
            if not is_restricted_material(r, restricted) and not is_git_internal_material_path(r["material_path"])
            and any(is_descendant_path(r["material_path"], path) for path in roots[r["root_id"]])]


def _save(conn, teacher_id, manifest, file_hashes, compiled, confirmed):
    items, scopes, issues, common_issues = compiled
    fingerprint = hashlib.sha256(_json([manifest, confirmed, {k: v["hash"] for k, v in file_hashes.items()}]).encode()).hexdigest()
    bind_global_file_references(conn, [f["hash"] for f in file_hashes.values()])
    row = conn.execute("""INSERT INTO project_topic_packages
        (teacher_id,package_key,title,version,fingerprint,mapping_confirmed,manifest_json,common_json,issues_json,common_issues_json,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(teacher_id,fingerprint) DO NOTHING RETURNING id""",
        (teacher_id, manifest["packageKey"], manifest["title"], manifest["version"], fingerprint, int(confirmed),
         _json(manifest), _json(manifest["common"]), _json(issues), _json(common_issues),
         datetime.now(timezone.utc).isoformat())).fetchone()
    if not row:
        row = conn.execute("SELECT id FROM project_topic_packages WHERE teacher_id=? AND fingerprint=?",
                           (teacher_id, fingerprint)).fetchone()
        return get_package_version(conn, row["id"])
    package_id = row["id"]
    conn.executemany("INSERT INTO project_topic_files(package_id,path,file_hash,size_bytes) VALUES (?,?,?,?)",
                     [(package_id, path, meta["hash"], meta["size"]) for path, meta in file_hashes.items()])
    conn.executemany("INSERT INTO project_topic_items(package_id,topic_key,ordinal,item_json) VALUES (?,?,?,?)",
                     [(package_id, item["key"], i, _json(item)) for i, item in enumerate(items)])
    conn.executemany("INSERT INTO project_topic_resource_scopes(package_id,topic_key,path) VALUES (?,?,?)",
                     [(package_id, key, path) for key, paths in scopes.items() for path in sorted(paths)])
    return get_package_version(conn, package_id)


def import_package(conn, teacher_id, files, *, title=""):
    contents = _extract(files)
    initial_title = title or PurePosixPath(files[0][0].replace("\\", "/").split("/")[0]).stem or "项目题包"
    raw = _embedded_manifest(contents)
    confirmed = raw is not None
    if not confirmed:
        raw = suggest_manifest(contents, initial_title)
    elif isinstance(raw, dict) and title.strip():
        raw = {**raw, "title": title.strip()}
    manifest = normalize_manifest(raw, initial_title)
    inspections = {path: inspect_resource(path, data) for path, data in contents.items()}
    compiled = compile_mapping(manifest, {p: len(data) for p, data in contents.items()}, inspections)
    file_hashes = {path: store_file_object_globally(io.BytesIO(data)) for path, data in contents.items()}
    return _save(conn, teacher_id, manifest, file_hashes, compiled, confirmed)


def remap_package(conn, package_id, teacher_id, manifest):
    ensure_teacher_package_owner(conn, package_id, teacher_id)
    current = _package_row(conn, package_id)
    normalized = normalize_manifest(manifest, current["title"])
    rows = conn.execute("SELECT path,file_hash,size_bytes FROM project_topic_files WHERE package_id=? ORDER BY path",
                        (package_id,)).fetchall()
    file_hashes, inspections = {}, {}
    for row in rows:
        path, file_hash, size = row["path"], row["file_hash"], row["size_bytes"]
        location = resolve_global_file_path(file_hash)
        if location is None:
            raise HTTPException(409, f"题包源文件不可用，请重新导入：{path}")
        if PurePosixPath(path).suffix.lower() in {".html", ".htm", ".css", ".md", ".txt"}:
            inspections[path] = inspect_resource(path, location.read_bytes())
        else:
            inspections[path] = ({}, bool(size), [])
        file_hashes[path] = {"hash": file_hash, "size": size}
    compiled = compile_mapping(normalized, {p: meta["size"] for p, meta in file_hashes.items()}, inspections)
    return _save(conn, teacher_id, normalized, file_hashes, compiled, True)


def get_scoped_topic(conn, package_id, topic_key):
    # Reading one topic must never serialize an all-topics catalogue to the student.
    row = conn.execute("SELECT common_json FROM project_topic_packages WHERE id=?", (package_id,)).fetchone()
    if not row:
        raise HTTPException(404, "题包版本不存在。")
    topic = None
    if topic_key is not None:
        item = conn.execute("SELECT item_json FROM project_topic_items WHERE package_id=? AND topic_key=?",
                            (package_id, topic_key)).fetchone()
        if not item:
            raise HTTPException(404, "题目不存在。")
        topic = json.loads(item["item_json"])
        topic.pop("issues", None)
        topic.pop("assets", None)
    return {"common": json.loads(row["common_json"]), "topic": topic}


def get_scoped_resource(conn, package_id, path, *, topic_key=None, teacher_preview=False):
    path = safe_path(path)
    params = [package_id, path]
    query = "SELECT f.file_hash FROM project_topic_files f WHERE f.package_id=? AND f.path=?"
    if not teacher_preview:
        query += """ AND EXISTS (SELECT 1 FROM project_topic_resource_scopes s
                    WHERE s.package_id=f.package_id AND s.path=f.path AND s.topic_key IN ('',?))"""
        params.append(topic_key or "")
    row = conn.execute(query, params).fetchone()
    location = resolve_global_file_path(row["file_hash"]) if row else None
    if location is None:
        raise HTTPException(404, "文件不存在或不可读。")
    media = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return Path(location), media
