"""Ordinary chat attachment ACL plus stable text extraction or media references."""
import hashlib
import json
from pathlib import Path

from fastapi import HTTPException

SELECTORS = {"social_media_id", "private_attachment_id"}
DOCUMENTS = {".docx", ".doc", ".pdf", ".pptx", ".ppt", ".xlsx", ".xls"}
TEXT = {".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm", ".py", ".js", ".ts", ".css", ".yaml", ".yml", ".log"}


def _source(conn, actor, kind, identifier):
    from .social_common import SocialError
    try:
        return _load_source(conn, actor, kind, identifier)
    except SocialError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


def _load_source(conn, actor, kind, identifier):
    user = actor.as_user()
    if kind == "social_media_id":
        from . import social_media_service as media
        from .social_media_store import SOCIAL_STORE_ROOT
        from .agent_bridge_service import allowed_file_roots
        info = media.resolve_download(conn, user, identifier)
        row = media.load_media(conn, identifier)
        roots = [*allowed_file_roots(), Path(SOCIAL_STORE_ROOT)]
        url, mime, scope = f"/api/social/media/{identifier}", info["mime"], row["conversation_key"]
    else:
        from .message_center_service import load_private_message_attachment_for_user, resolve_private_message_attachment_file_payload
        from .agent_bridge_service import allowed_file_roots
        found = load_private_message_attachment_for_user(conn, user, identifier)
        if found is None:
            raise HTTPException(404, "附件不存在。")
        row = dict(found)
        info = resolve_private_message_attachment_file_payload(found, "original")
        if info is None:
            raise HTTPException(404, "附件文件不存在。")
        roots = allowed_file_roots()
        message = conn.execute("SELECT conversation_key FROM private_messages WHERE id=?", (row["message_id"],)).fetchone()
        url, mime, scope = f"/api/message-center/private/attachments/{identifier}/original", info["mime_type"], message["conversation_key"]
    revision = hashlib.sha256(json.dumps([kind, identifier, row, str(info["path"])],
        ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()
    return {"path": info["path"], "roots": roots, "filename": info["filename"], "mime_type": mime,
            "size": int(row.get("file_size") or 0), "url": url, "revision": revision, "scope": {"kind": "conversation", "key": scope}}


def read_attachment(conn, actor, args):
    if not isinstance(args, dict) or set(args) - (SELECTORS | {"revision", "read_text"}):
        raise HTTPException(400, "附件读取只接受附件编号、revision 和 read_text。")
    selected = [(key, value) for key, value in args.items() if key in SELECTORS and value is not None]
    if len(selected) != 1 or type(selected[0][1]) is not int or selected[0][1] <= 0:
        raise HTTPException(400, "每次请提供一个合法的聊天附件编号。")
    if type(args.get("read_text", True)) is not bool:
        raise HTTPException(400, "read_text 必须为布尔值。")
    kind, identifier = selected[0]
    source = _source(conn, actor, kind, identifier)
    if args.get("revision") is not None and args["revision"] != source["revision"]:
        raise HTTPException(409, "附件或访问归属已变化，请重新读取。")
    result = {key: source[key] for key in ("filename", "mime_type", "size", "url", "revision", "scope")}
    result.update({kind: identifier, "perception": "reference_only", "content_observed": False,
                   "description": "这是已授权的附件引用；尚未读取图片、音频或视频内容。"})
    suffix = Path(source["filename"]).suffix.lower()
    if args.get("read_text", True) and (suffix in DOCUMENTS | TEXT or source["mime_type"].startswith("text/")):
        from .agent_bridge_service import read_platform_file
        try:
            extracted = read_platform_file(str(source["path"]), filename=source["filename"], authorized_roots=source["roots"])
        except ValueError as exc:
            result["extraction_error"] = str(exc)
        else:
            extracted.pop("path", None)
            result.update(extracted, perception="document_text" if suffix in DOCUMENTS else "text",
                          content_observed=True, description="正文文本来自原附件；文档内图片尚未进行视觉识别。")
    current = _source(conn, actor, kind, identifier)
    if current["revision"] != source["revision"]:
        raise HTTPException(409, "读取期间附件或访问权限发生变化。")
    return result
