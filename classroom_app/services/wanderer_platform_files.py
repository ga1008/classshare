"""File transport from authorized platform references or bounded authored text."""
import hashlib
from pathlib import PurePosixPath

from fastapi import HTTPException

from .agent_platform_multipart_service import MAX_FORM_FILE_BYTES, encode_form_parts, form_values

SELECTORS = frozenset({"material_id", "submission_file_id", "collaboration_file_id", "course_file_id"})


def encode_wanderer_upload(conn, authority, operation, normalized, references):
    from .agent_platform_download_service import source_snapshot
    from .agent_scoped_read_service import _platform_file
    references = [] if references is None else references
    if not operation.allows_files or not isinstance(references, list) or len(references) > operation.max_files:
        raise HTTPException(400, "附件数量或能力不允许上传。")
    files, receipts, names = [], [], set()
    remaining = MAX_FORM_FILE_BYTES
    for ref in references:
        if not isinstance(ref, dict) or set(ref) - (SELECTORS | {"filename", "content_text", "revision"}):
            raise HTTPException(400, "附件只接受授权平台文件编号或本人编写的 content_text。")
        selected = [(key, ref[key]) for key in SELECTORS if ref.get(key) is not None]
        source = None
        if "content_text" in ref:
            if selected or not isinstance(ref["content_text"], str) or len(ref["content_text"].encode()) > 65536:
                raise HTTPException(400, "自编附件不能混合文件来源，且每个最多 64KiB。")
            data = ref["content_text"].encode()
        else:
            if len(selected) != 1 or type(selected[0][1]) is not int or selected[0][1] <= 0:
                raise HTTPException(400, "每个附件必须有一个合法平台文件来源。")
            source = _platform_file(conn, authority.actor, *selected[0])
            with source_snapshot(source["path"]) as snapshot:
                data = snapshot["stream"].read(remaining + 1)
            current = _platform_file(conn, authority.actor, *selected[0])
            if source["binding"] != current["binding"]:
                raise HTTPException(409, "附件权限或来源已变化。")
        digest = hashlib.sha256(data).hexdigest()
        if ref.get("revision") is not None and ref["revision"] != digest:
            raise HTTPException(409, "附件内容版本已变化。")
        filename = ref.get("filename") or (source["filename"] if source else "note.txt")
        if (not isinstance(filename, str) or not 1 <= len(filename) <= 240
                or any(c in filename for c in '\\/:"\r\n\x00') or filename.startswith(".")
                or PurePosixPath(filename).suffix.lower() in operation.forbidden_file_suffixes
                or filename in names):
            raise HTTPException(400, "附件文件名或类型无效。")
        remaining -= len(data)
        if remaining < 0:
            raise HTTPException(413, "附件总量超出平台上传限制。")
        names.add(filename)
        files.append((filename, data))
        receipts.append({"filename": filename, "sha256": digest, "size": len(data),
                         "source": dict(selected) if source else {"kind": "authored_text"}})
    schema = operation.parameters.get("body", {})
    values = form_values({key: value for key, value in normalized["body"].items()
        if not (value is None and schema.get(key, {}).get("nullable") and schema.get(key, {}).get("type") == "integer")})
    return encode_form_parts(values, files, operation.file_field_name, receipts, normalized)
