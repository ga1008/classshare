import asyncio
import base64
import configparser
import hashlib
import os
import re
import shlex
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from ..config import GLOBAL_FILES_DIR, SECRET_KEY
from .file_handler import delete_file_safely
from .materials_service import infer_material_profile, is_git_internal_material_path, serialize_material_row


REPO_STATUS_UNSCANNED = "unscanned"
REPO_STATUS_PLAIN = "plain"
REPO_STATUS_REPOSITORY = "repository"
REPO_STATUS_INVALID = "invalid"

DEFAULT_REMOTE_NAME = "origin"
DEFAULT_COMMIT_MESSAGE = "update by LS"
GIT_COMMAND_TIMEOUT_SECONDS = 180

_repo_locks: dict[int, asyncio.Lock] = {}
_repo_locks_guard = asyncio.Lock()
_fernet_instance: Fernet | None = None


def _now_iso() -> str:
    return datetime.now().isoformat()


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_repo_root_relative_path(root_path: str, material_path: str) -> str:
    root_parts = [part for part in PurePosixPath(str(root_path or "")).parts if part not in ("", ".")]
    material_parts = [part for part in PurePosixPath(str(material_path or "")).parts if part not in ("", ".")]
    if material_parts[: len(root_parts)] != root_parts:
        return str(material_path or "")
    remainder = material_parts[len(root_parts) :]
    return "/".join(remainder)


def _normalize_branch_name(raw_value: str | None) -> str:
    normalized = str(raw_value or "").strip()
    if normalized.startswith("refs/heads/"):
        return normalized.split("refs/heads/", 1)[1]
    if normalized.startswith("refs/remotes/"):
        parts = normalized.split("/")
        if len(parts) >= 4:
            return "/".join(parts[3:])
    return normalized


def _detect_git_provider(host: str | None) -> str:
    lowered = str(host or "").strip().lower()
    if not lowered:
        return ""
    if "github" in lowered:
        return "github"
    if "gitee" in lowered:
        return "gitee"
    if "gitlab" in lowered:
        return "gitlab"
    if lowered in {"localhost", "127.0.0.1"}:
        return "local"
    return "self-hosted"


def parse_git_remote_url(remote_url: str | None) -> dict:
    raw_url = str(remote_url or "").strip()
    result = {
        "raw_url": raw_url,
        "display_url": raw_url,
        "protocol": "",
        "host": "",
        "path": "",
        "remote_key": "",
        "provider": "",
        "username_hint": "",
    }
    if not raw_url:
        return result

    scp_like = re.match(r"^(?P<user>[^@\s/:]+)@(?P<host>[^:\s]+):(?P<path>.+)$", raw_url)
    if scp_like:
        host = scp_like.group("host").lower()
        repo_path = scp_like.group("path").strip().lstrip("/")
        remote_key = f"ssh://{host}/{repo_path.removesuffix('.git')}".rstrip("/")
        result.update(
            {
                "protocol": "ssh",
                "host": host,
                "path": repo_path,
                "remote_key": remote_key,
                "provider": _detect_git_provider(host),
                "username_hint": scp_like.group("user"),
            }
        )
        return result

    parsed = urlsplit(raw_url)
    protocol = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    path = parsed.path.lstrip("/")
    if parsed.port:
        host = f"{host}:{parsed.port}"

    sanitized_netloc = host
    display_url = raw_url
    if protocol:
        display_url = urlunsplit((protocol, sanitized_netloc, parsed.path or "", "", ""))

    remote_key = ""
    if protocol and host:
        remote_key = f"{protocol}://{host}/{path.removesuffix('.git')}".rstrip("/")

    result.update(
        {
            "display_url": display_url,
            "protocol": protocol,
            "host": host,
            "path": path,
            "remote_key": remote_key,
            "provider": _detect_git_provider(host),
            "username_hint": parsed.username or "",
        }
    )
    return result


def _get_fernet() -> Fernet:
    global _fernet_instance
    if _fernet_instance is None:
        digest = hashlib.sha256(str(SECRET_KEY or "").encode("utf-8")).digest()
        _fernet_instance = Fernet(base64.urlsafe_b64encode(digest))
    return _fernet_instance


def _encrypt_secret(secret: str) -> str:
    return _get_fernet().encrypt(str(secret or "").encode("utf-8")).decode("utf-8")


def _decrypt_secret(secret_encrypted: str | None) -> str:
    if not secret_encrypted:
        return ""
    try:
        return _get_fernet().decrypt(str(secret_encrypted).encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def _count_global_file_references(conn, file_hash: str) -> int:
    material_refs = conn.execute(
        "SELECT COUNT(*) FROM course_materials WHERE file_hash = ?",
        (file_hash,),
    ).fetchone()[0]
    course_refs = conn.execute(
        "SELECT COUNT(*) FROM course_files WHERE file_hash = ?",
        (file_hash,),
    ).fetchone()[0]
    return int(material_refs) + int(course_refs)


def _load_file_bytes(file_hash: str) -> bytes:
    file_path = Path(GLOBAL_FILES_DIR) / str(file_hash or "")
    if not file_path.exists():
        raise FileNotFoundError(f"材料文件不存在: {file_hash}")
    return file_path.read_bytes()


def _load_text_from_row(row: dict | None) -> str:
    if not row or row.get("node_type") != "file" or not row.get("file_hash"):
        return ""
    try:
        return _load_file_bytes(str(row["file_hash"])).decode("utf-8", errors="ignore")
    except FileNotFoundError:
        return ""


def _store_bytes_globally(payload_bytes: bytes) -> tuple[str, int]:
    file_hash = hashlib.sha256(payload_bytes).hexdigest()
    target_dir = Path(GLOBAL_FILES_DIR)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / file_hash
    if not target_path.exists():
        target_path.write_bytes(payload_bytes)
    return file_hash, len(payload_bytes)


def _build_git_entry_map(conn, root_row) -> dict[str, dict]:
    root_path = str(root_row["material_path"])
    git_root_path = f"{root_path}/.git"
    rows = conn.execute(
        """
        SELECT *
        FROM course_materials
        WHERE root_id = ?
          AND (material_path = ? OR material_path LIKE ?)
        ORDER BY LENGTH(material_path), material_path
        """,
        (root_row["id"], git_root_path, f"{git_root_path}/%"),
    ).fetchall()

    result: dict[str, dict] = {}
    for row in rows:
        row_dict = dict(row)
        result[_get_repo_root_relative_path(root_path, row_dict["material_path"])] = row_dict
    return result


def _load_packed_refs(entry_map: dict[str, dict]) -> dict[str, str]:
    packed_refs_row = entry_map.get(".git/packed-refs")
    packed_refs_text = _load_text_from_row(packed_refs_row)
    refs: dict[str, str] = {}
    for line in packed_refs_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("^"):
            continue
        parts = stripped.split(" ", 1)
        if len(parts) != 2:
            continue
        refs[parts[1].strip()] = parts[0].strip()
    return refs


def _collect_branch_candidates(entry_map: dict[str, dict], packed_refs: dict[str, str]) -> list[str]:
    branches: list[str] = []
    for relative_path in entry_map:
        if relative_path.startswith(".git/refs/heads/"):
            branch_name = relative_path.split(".git/refs/heads/", 1)[1].strip("/")
            if branch_name:
                branches.append(branch_name)
    for ref_name in packed_refs:
        if ref_name.startswith("refs/heads/"):
            branches.append(ref_name.split("refs/heads/", 1)[1].strip("/"))

    seen: set[str] = set()
    deduped: list[str] = []
    for branch in branches:
        if branch and branch not in seen:
            deduped.append(branch)
            seen.add(branch)
    return deduped


def _parse_git_config(entry_map: dict[str, dict]) -> configparser.RawConfigParser | None:
    config_text = _load_text_from_row(entry_map.get(".git/config"))
    if not config_text.strip():
        return None

    parser = configparser.RawConfigParser(strict=False)
    try:
        parser.read_string(config_text)
    except configparser.Error:
        return None
    return parser


def refresh_root_git_metadata(conn, root_material_id: int, root_row=None) -> dict:
    root_material_id = int(root_material_id)
    current_row = root_row or conn.execute(
        "SELECT * FROM course_materials WHERE id = ?",
        (root_material_id,),
    ).fetchone()
    if not current_row:
        raise HTTPException(404, "仓库材料不存在")

    row_dict = dict(current_row)
    detected_at = _now_iso()
    payload = {
        "git_repo_status": REPO_STATUS_PLAIN,
        "git_provider": "",
        "git_remote_name": "",
        "git_remote_url": "",
        "git_remote_host": "",
        "git_remote_protocol": "",
        "git_default_branch": "",
        "git_head_branch": "",
        "git_detect_error": "",
        "git_detected_at": detected_at,
    }

    if row_dict.get("node_type") != "folder" or _safe_int(row_dict.get("id")) != _safe_int(row_dict.get("root_id")):
        conn.execute(
            """
            UPDATE course_materials
            SET git_repo_status = ?, git_provider = ?, git_remote_name = ?, git_remote_url = ?,
                git_remote_host = ?, git_remote_protocol = ?, git_default_branch = ?, git_head_branch = ?,
                git_detect_error = ?, git_detected_at = ?
            WHERE id = ?
            """,
            (
                payload["git_repo_status"],
                payload["git_provider"],
                payload["git_remote_name"],
                payload["git_remote_url"],
                payload["git_remote_host"],
                payload["git_remote_protocol"],
                payload["git_default_branch"],
                payload["git_head_branch"],
                payload["git_detect_error"],
                payload["git_detected_at"],
                root_material_id,
            ),
        )
        row_dict.update(payload)
        return row_dict

    entry_map = _build_git_entry_map(conn, row_dict)
    git_dir_exists = ".git" in entry_map or any(path.startswith(".git/") for path in entry_map)
    head_row = entry_map.get(".git/HEAD")

    if not git_dir_exists:
        pass
    elif not head_row or head_row.get("node_type") != "file":
        payload["git_repo_status"] = REPO_STATUS_INVALID
        payload["git_detect_error"] = "检测到 .git 目录，但缺少 HEAD 文件。"
    else:
        head_text = _load_text_from_row(head_row).strip()
        if not head_text:
            payload["git_repo_status"] = REPO_STATUS_INVALID
            payload["git_detect_error"] = "无法读取 .git/HEAD 内容。"
        else:
            parser = _parse_git_config(entry_map)
            packed_refs = _load_packed_refs(entry_map)
            branch_candidates = _collect_branch_candidates(entry_map, packed_refs)

            head_branch = ""
            if head_text.startswith("ref:"):
                head_branch = _normalize_branch_name(head_text.split("ref:", 1)[1].strip())

            branch_remote = ""
            branch_merge = ""
            remote_name = ""
            remote_url = ""
            if parser is not None:
                if head_branch:
                    branch_section = f'branch "{head_branch}"'
                    if parser.has_section(branch_section):
                        branch_remote = str(parser.get(branch_section, "remote", fallback="")).strip()
                        branch_merge = _normalize_branch_name(parser.get(branch_section, "merge", fallback=""))

                remote_sections = [
                    section for section in parser.sections() if section.startswith('remote "') and section.endswith('"')
                ]
                remote_names = [section[8:-1] for section in remote_sections]
                remote_name = branch_remote or (DEFAULT_REMOTE_NAME if DEFAULT_REMOTE_NAME in remote_names else "")
                if not remote_name and remote_names:
                    remote_name = remote_names[0]
                if remote_name:
                    remote_section = f'remote "{remote_name}"'
                    remote_url = str(parser.get(remote_section, "url", fallback="")).strip()

            remote_head_branch = ""
            if remote_name:
                remote_head_row = entry_map.get(f".git/refs/remotes/{remote_name}/HEAD")
                remote_head_text = _load_text_from_row(remote_head_row).strip()
                if remote_head_text.startswith("ref:"):
                    remote_head_branch = _normalize_branch_name(remote_head_text.split("ref:", 1)[1].strip())

            if not remote_head_branch:
                for branch_name in branch_candidates:
                    if branch_name in {"main", "master", "dev"}:
                        remote_head_branch = branch_name
                        break

            default_branch = remote_head_branch or branch_merge or head_branch
            if not default_branch and branch_candidates:
                default_branch = branch_candidates[0]

            parsed_remote = parse_git_remote_url(remote_url)
            payload.update(
                {
                    "git_repo_status": REPO_STATUS_REPOSITORY,
                    "git_provider": parsed_remote["provider"],
                    "git_remote_name": remote_name,
                    "git_remote_url": parsed_remote["display_url"],
                    "git_remote_host": parsed_remote["host"],
                    "git_remote_protocol": parsed_remote["protocol"],
                    "git_default_branch": default_branch,
                    "git_head_branch": head_branch,
                    "git_detect_error": "",
                }
            )

    conn.execute(
        """
        UPDATE course_materials
        SET git_repo_status = ?, git_provider = ?, git_remote_name = ?, git_remote_url = ?,
            git_remote_host = ?, git_remote_protocol = ?, git_default_branch = ?, git_head_branch = ?,
            git_detect_error = ?, git_detected_at = ?
        WHERE id = ?
        """,
        (
            payload["git_repo_status"],
            payload["git_provider"],
            payload["git_remote_name"],
            payload["git_remote_url"],
            payload["git_remote_host"],
            payload["git_remote_protocol"],
            payload["git_default_branch"],
            payload["git_head_branch"],
            payload["git_detect_error"],
            payload["git_detected_at"],
            root_material_id,
        ),
    )
    row_dict.update(payload)
    return row_dict


def _apply_repository_metadata(item: dict, metadata: dict | None) -> dict:
    repo_meta = metadata or {}
    item["git_repo_status"] = str(repo_meta.get("git_repo_status") or item.get("git_repo_status") or REPO_STATUS_PLAIN)
    item["git_provider"] = str(repo_meta.get("git_provider") or item.get("git_provider") or "")
    item["git_remote_name"] = str(repo_meta.get("git_remote_name") or item.get("git_remote_name") or "")
    item["git_remote_url"] = str(repo_meta.get("git_remote_url") or item.get("git_remote_url") or "")
    item["git_remote_host"] = str(repo_meta.get("git_remote_host") or item.get("git_remote_host") or "")
    item["git_remote_protocol"] = str(repo_meta.get("git_remote_protocol") or item.get("git_remote_protocol") or "")
    item["git_default_branch"] = str(repo_meta.get("git_default_branch") or item.get("git_default_branch") or "")
    item["git_head_branch"] = str(repo_meta.get("git_head_branch") or item.get("git_head_branch") or "")
    item["git_detect_error"] = str(repo_meta.get("git_detect_error") or item.get("git_detect_error") or "")
    item["git_detected_at"] = str(repo_meta.get("git_detected_at") or item.get("git_detected_at") or "")
    item["is_git_repository"] = (
        item.get("node_type") == "folder"
        and _safe_int(item.get("id")) == _safe_int(item.get("root_id"))
        and item["git_repo_status"] == REPO_STATUS_REPOSITORY
    )
    return item


def attach_git_repository_metadata(conn, items: list[dict]) -> list[dict]:
    root_ids = sorted(
        {
            _safe_int(item.get("id"))
            for item in items
            if item.get("node_type") == "folder" and _safe_int(item.get("id")) == _safe_int(item.get("root_id"))
        }
    )
    if not root_ids:
        for item in items:
            _apply_repository_metadata(item, None)
        return items

    placeholders = ",".join("?" for _ in root_ids)
    existing_rows = conn.execute(
        f"""
        SELECT id, git_repo_status, git_provider, git_remote_name, git_remote_url, git_remote_host,
               git_remote_protocol, git_default_branch, git_head_branch, git_detect_error, git_detected_at
        FROM course_materials
        WHERE id IN ({placeholders})
        """,
        root_ids,
    ).fetchall()

    needs_refresh = [
        int(row["id"])
        for row in existing_rows
        if not row["git_detected_at"] or str(row["git_repo_status"] or "") == REPO_STATUS_UNSCANNED
    ]
    if needs_refresh:
        for root_id in needs_refresh:
            refresh_root_git_metadata(conn, root_id)
        conn.commit()
        existing_rows = conn.execute(
            f"""
            SELECT id, git_repo_status, git_provider, git_remote_name, git_remote_url, git_remote_host,
                   git_remote_protocol, git_default_branch, git_head_branch, git_detect_error, git_detected_at
            FROM course_materials
            WHERE id IN ({placeholders})
            """,
            root_ids,
        ).fetchall()

    root_metadata_map = {int(row["id"]): dict(row) for row in existing_rows}
    for item in items:
        metadata = root_metadata_map.get(_safe_int(item.get("id"))) if _safe_int(item.get("id")) == _safe_int(item.get("root_id")) else None
        _apply_repository_metadata(item, metadata)
    return items


def _repository_command_strings(root_item: dict) -> dict:
    remote_name = str(root_item.get("git_remote_name") or DEFAULT_REMOTE_NAME)
    branch_name = str(root_item.get("git_default_branch") or root_item.get("git_head_branch") or "")
    update_command = f"git pull {remote_name} {branch_name}".strip()
    commit_command = f'git add -A && git commit -m "{DEFAULT_COMMIT_MESSAGE}" && git push {remote_name} {branch_name}'.strip()
    return {
        "update": update_command,
        "commit_push": commit_command,
    }


def _load_saved_git_credential(conn, teacher_id: int, remote_info: dict) -> dict | None:
    remote_key = str(remote_info.get("remote_key") or "").strip()
    remote_host = str(remote_info.get("host") or "").strip().lower()
    if not remote_key and not remote_host:
        return None

    if remote_key:
        rows = conn.execute(
            """
            SELECT *
            FROM teacher_git_credentials
            WHERE teacher_id = ?
              AND (remote_key = ? OR remote_host = ?)
            ORDER BY CASE WHEN remote_key = ? THEN 0 ELSE 1 END,
                     COALESCE(last_used_at, updated_at, created_at) DESC,
                     id DESC
            """,
            (teacher_id, remote_key, remote_host, remote_key),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT *
            FROM teacher_git_credentials
            WHERE teacher_id = ?
              AND remote_host = ?
            ORDER BY COALESCE(last_used_at, updated_at, created_at) DESC, id DESC
            """,
            (teacher_id, remote_host),
        ).fetchall()
    for row in rows:
        row_dict = dict(row)
        secret = _decrypt_secret(row_dict.get("secret_encrypted"))
        if not secret:
            continue
        row_dict["secret"] = secret
        return row_dict
    return None


def save_material_repository_credential(
    conn,
    material_id: int,
    teacher_id: int,
    username: str,
    secret: str,
    auth_mode: str = "password",
) -> dict:
    root_row = conn.execute(
        "SELECT * FROM course_materials WHERE id = ? AND teacher_id = ?",
        (material_id, teacher_id),
    ).fetchone()
    if not root_row:
        raise HTTPException(404, "仓库材料不存在")

    root_dict = refresh_root_git_metadata(conn, material_id, root_row)
    root_item = serialize_material_row(root_dict)
    root_item = attach_git_repository_metadata(conn, [root_item])[0]
    if not root_item.get("is_git_repository"):
        raise HTTPException(400, "当前材料不是 Git 仓库")

    remote_info = parse_git_remote_url(root_item.get("git_remote_url"))
    if remote_info["protocol"] not in {"http", "https"}:
        raise HTTPException(400, "当前远程仓库使用的不是 HTTP/HTTPS 协议，无法保存登录表单凭据")

    normalized_secret = str(secret or "").strip()
    if not normalized_secret:
        raise HTTPException(400, "请输入密码或访问令牌")

    normalized_username = str(username or remote_info.get("username_hint") or "").strip()
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO teacher_git_credentials
        (teacher_id, remote_key, remote_host, remote_url, provider, auth_mode, username,
         secret_encrypted, created_at, updated_at, last_used_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(teacher_id, remote_key) DO UPDATE SET
            remote_host = excluded.remote_host,
            remote_url = excluded.remote_url,
            provider = excluded.provider,
            auth_mode = excluded.auth_mode,
            username = excluded.username,
            secret_encrypted = excluded.secret_encrypted,
            updated_at = excluded.updated_at
        """,
        (
            teacher_id,
            remote_info["remote_key"],
            remote_info["host"],
            remote_info["display_url"],
            remote_info["provider"],
            str(auth_mode or "password").strip() or "password",
            normalized_username,
            _encrypt_secret(normalized_secret),
            now,
            now,
        ),
    )
    conn.commit()
    return {
        "saved": True,
        "remote_host": remote_info["host"],
        "remote_url": remote_info["display_url"],
        "username": normalized_username,
        "provider": remote_info["provider"],
        "auth_mode": str(auth_mode or "password").strip() or "password",
    }


def get_material_repository_detail(conn, material_id: int, teacher_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM course_materials WHERE id = ? AND teacher_id = ?",
        (material_id, teacher_id),
    ).fetchone()
    if not row:
        raise HTTPException(404, "仓库材料不存在")

    row_dict = refresh_root_git_metadata(conn, material_id, row)
    conn.commit()
    root_item = serialize_material_row(row_dict)
    root_item = attach_git_repository_metadata(conn, [root_item])[0]
    if root_item.get("node_type") != "folder" or _safe_int(root_item.get("id")) != _safe_int(root_item.get("root_id")):
        raise HTTPException(400, "仅根目录文件夹支持仓库功能")
    if not root_item.get("is_git_repository"):
        raise HTTPException(400, root_item.get("git_detect_error") or "当前材料不是 Git 仓库")

    remote_info = parse_git_remote_url(root_item.get("git_remote_url"))
    credential = _load_saved_git_credential(conn, teacher_id, remote_info)
    preferred_branch = str(root_item.get("git_default_branch") or root_item.get("git_head_branch") or "").strip()
    commands = _repository_command_strings(root_item)
    return {
        "material_id": root_item["id"],
        "name": root_item["name"],
        "material_path": root_item["material_path"],
        "provider": root_item.get("git_provider") or remote_info["provider"],
        "remote_name": root_item.get("git_remote_name") or DEFAULT_REMOTE_NAME,
        "remote_url": root_item.get("git_remote_url") or remote_info["display_url"],
        "remote_host": root_item.get("git_remote_host") or remote_info["host"],
        "remote_protocol": root_item.get("git_remote_protocol") or remote_info["protocol"],
        "default_branch": preferred_branch,
        "head_branch": root_item.get("git_head_branch") or "",
        "status": root_item.get("git_repo_status") or REPO_STATUS_PLAIN,
        "detect_error": root_item.get("git_detect_error") or "",
        "detected_at": root_item.get("git_detected_at") or "",
        "credential_saved": bool(credential),
        "credential_username": str((credential or {}).get("username") or remote_info.get("username_hint") or ""),
        "credential_supported": (root_item.get("git_remote_protocol") or remote_info["protocol"]) in {"http", "https"},
        "can_update": bool((root_item.get("git_remote_url") or remote_info["display_url"]) and preferred_branch),
        "can_commit_push": bool((root_item.get("git_remote_url") or remote_info["display_url"]) and preferred_branch),
        "commands": commands,
        "default_commit_message": DEFAULT_COMMIT_MESSAGE,
    }


def _fetch_subtree_rows(conn, root_row) -> list[dict]:
    rows = conn.execute(
        """
        SELECT *
        FROM course_materials
        WHERE root_id = ?
          AND (material_path = ? OR material_path LIKE ?)
        ORDER BY LENGTH(material_path), material_path
        """,
        (root_row["id"], root_row["material_path"], f"{root_row['material_path']}/%"),
    ).fetchall()
    return [dict(row) for row in rows]


def _export_repository_workspace(conn, root_row, workspace_dir: Path) -> None:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    for row in _fetch_subtree_rows(conn, root_row):
        if _safe_int(row.get("id")) == _safe_int(root_row["id"]):
            continue
        relative_path = _get_repo_root_relative_path(root_row["material_path"], row["material_path"])
        target_path = workspace_dir / PurePosixPath(relative_path)
        if row["node_type"] == "folder":
            target_path.mkdir(parents=True, exist_ok=True)
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(_load_file_bytes(str(row["file_hash"])))


def _scan_workspace_entries(workspace_dir: Path) -> list[dict]:
    entries: list[dict] = []
    for path in sorted(workspace_dir.rglob("*"), key=lambda item: (len(item.relative_to(workspace_dir).parts), str(item))):
        relative_path = path.relative_to(workspace_dir).as_posix()
        if path.is_dir():
            entries.append({"relative_path": relative_path, "node_type": "folder"})
        elif path.is_file():
            entries.append({"relative_path": relative_path, "node_type": "file"})
    return entries


def _sync_workspace_to_repository(conn, root_row, workspace_dir: Path) -> tuple[dict, list[str]]:
    existing_rows = _fetch_subtree_rows(conn, root_row)
    existing_map = {
        _get_repo_root_relative_path(root_row["material_path"], row["material_path"]): row
        for row in existing_rows
    }
    scanned_entries = _scan_workspace_entries(workspace_dir)
    scanned_map = {entry["relative_path"]: entry for entry in scanned_entries}
    now = _now_iso()

    deleted_file_hashes: set[str] = set()
    summary = {
        "inserted": 0,
        "updated": 0,
        "deleted": 0,
        "unchanged": 0,
    }

    deleted_paths = [
        path
        for path in existing_map
        if path and path not in scanned_map
    ]
    for relative_path in sorted(deleted_paths, key=lambda item: item.count("/"), reverse=True):
        row = existing_map[relative_path]
        if row["node_type"] == "file" and row.get("file_hash"):
            deleted_file_hashes.add(str(row["file_hash"]))
        conn.execute("DELETE FROM course_materials WHERE id = ?", (row["id"],))
        summary["deleted"] += 1

    path_to_id = {"": int(root_row["id"])}
    root_prefix = str(root_row["material_path"])

    for entry in sorted(scanned_entries, key=lambda item: (item["relative_path"].count("/"), item["node_type"] != "folder", item["relative_path"])):
        relative_path = entry["relative_path"]
        parent_relative = str(PurePosixPath(relative_path).parent.as_posix())
        if parent_relative == ".":
            parent_relative = ""
        parent_id = path_to_id.get(parent_relative, int(root_row["id"]))
        material_path = f"{root_prefix}/{relative_path}" if relative_path else root_prefix
        existing_row = existing_map.get(relative_path)

        if existing_row and existing_row["node_type"] != entry["node_type"]:
            if existing_row["node_type"] == "file" and existing_row.get("file_hash"):
                deleted_file_hashes.add(str(existing_row["file_hash"]))
            conn.execute("DELETE FROM course_materials WHERE id = ?", (existing_row["id"],))
            summary["deleted"] += 1
            existing_row = None

        if entry["node_type"] == "folder":
            folder_name = PurePosixPath(relative_path).name
            if existing_row:
                path_to_id[relative_path] = int(existing_row["id"])
                if (
                    _safe_int(existing_row.get("parent_id")) != parent_id
                    or str(existing_row.get("material_path") or "") != material_path
                    or str(existing_row.get("name") or "") != folder_name
                ):
                    conn.execute(
                        """
                        UPDATE course_materials
                        SET parent_id = ?, material_path = ?, name = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (parent_id, material_path, folder_name, now, existing_row["id"]),
                    )
                    summary["updated"] += 1
                else:
                    summary["unchanged"] += 1
                continue

            cursor = conn.execute(
                """
                INSERT INTO course_materials
                (teacher_id, parent_id, root_id, material_path, name, node_type, mime_type,
                 preview_type, ai_capability, file_ext, file_hash, file_size,
                 ai_parse_status, ai_parse_result_json, ai_optimize_status, ai_optimized_markdown,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'folder', 'inode/directory', 'folder', 'none', '', NULL, 0,
                        'idle', NULL, 'idle', NULL, ?, ?)
                """,
                (
                    root_row["teacher_id"],
                    parent_id,
                    root_row["id"],
                    material_path,
                    folder_name,
                    now,
                    now,
                ),
            )
            path_to_id[relative_path] = int(cursor.lastrowid)
            summary["inserted"] += 1
            continue

        file_path = workspace_dir / PurePosixPath(relative_path)
        payload_bytes = file_path.read_bytes()
        file_hash, file_size = _store_bytes_globally(payload_bytes)
        profile = infer_material_profile(file_path.name)
        if existing_row:
            path_to_id[relative_path] = int(existing_row["id"])
            file_changed = (
                _safe_int(existing_row.get("parent_id")) != parent_id
                or str(existing_row.get("material_path") or "") != material_path
                or str(existing_row.get("name") or "") != file_path.name
                or str(existing_row.get("file_hash") or "") != file_hash
                or _safe_int(existing_row.get("file_size")) != file_size
                or str(existing_row.get("mime_type") or "") != profile["mime_type"]
                or str(existing_row.get("preview_type") or "") != profile["preview_type"]
                or str(existing_row.get("ai_capability") or "") != profile["ai_capability"]
                or str(existing_row.get("file_ext") or "") != profile["file_ext"]
            )
            if file_changed:
                if existing_row.get("file_hash") and str(existing_row["file_hash"]) != file_hash:
                    deleted_file_hashes.add(str(existing_row["file_hash"]))
                conn.execute(
                    """
                    UPDATE course_materials
                    SET parent_id = ?, material_path = ?, name = ?, mime_type = ?, preview_type = ?,
                        ai_capability = ?, file_ext = ?, file_hash = ?, file_size = ?,
                        ai_parse_status = 'idle', ai_parse_result_json = NULL,
                        ai_optimize_status = 'idle', ai_optimized_markdown = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        parent_id,
                        material_path,
                        file_path.name,
                        profile["mime_type"],
                        profile["preview_type"],
                        profile["ai_capability"],
                        profile["file_ext"],
                        file_hash,
                        file_size,
                        now,
                        existing_row["id"],
                    ),
                )
                summary["updated"] += 1
            else:
                summary["unchanged"] += 1
            continue

        cursor = conn.execute(
            """
            INSERT INTO course_materials
            (teacher_id, parent_id, root_id, material_path, name, node_type, mime_type,
             preview_type, ai_capability, file_ext, file_hash, file_size,
             ai_parse_status, ai_parse_result_json, ai_optimize_status, ai_optimized_markdown,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'file', ?, ?, ?, ?, ?, ?, 'idle', NULL, 'idle', NULL, ?, ?)
            """,
            (
                root_row["teacher_id"],
                parent_id,
                root_row["id"],
                material_path,
                file_path.name,
                profile["mime_type"],
                profile["preview_type"],
                profile["ai_capability"],
                profile["file_ext"],
                file_hash,
                file_size,
                now,
                now,
            ),
        )
        path_to_id[relative_path] = int(cursor.lastrowid)
        summary["inserted"] += 1

    if any(summary[key] > 0 for key in ("inserted", "updated", "deleted")):
        conn.execute(
            "UPDATE course_materials SET updated_at = ? WHERE id = ?",
            (now, root_row["id"]),
        )

    removable_hashes = [
        file_hash for file_hash in deleted_file_hashes if file_hash and _count_global_file_references(conn, file_hash) <= 0
    ]
    return summary, removable_hashes


def _build_combined_output(execution_log: list[dict]) -> str:
    blocks: list[str] = []
    for entry in execution_log:
        header = f"$ {entry['command']}\nexit code: {entry['returncode']}"
        stdout = str(entry.get("stdout") or "").strip()
        stderr = str(entry.get("stderr") or "").strip()
        payload = [header]
        if stdout:
            payload.append(stdout)
        if stderr:
            payload.append(stderr)
        blocks.append("\n".join(payload).strip())
    return "\n\n".join(block for block in blocks if block).strip()


def _classify_auth_failure(remote_info: dict, combined_output: str) -> dict:
    lowered = str(combined_output or "").lower()
    https_auth_patterns = (
        "authentication failed",
        "could not read username",
        "could not read password",
        "http basic: access denied",
        "terminal prompts disabled",
        "password authentication was removed",
    )
    ssh_auth_patterns = (
        "permission denied (publickey)",
        "permission denied, please try again",
        "no supported authentication methods available",
    )

    if remote_info.get("protocol") in {"http", "https"} and any(pattern in lowered for pattern in https_auth_patterns):
        return {
            "auth_required": True,
            "credential_supported": True,
            "message": "远程仓库需要认证，请填写用户名和密码或访问令牌后重试。",
        }
    if remote_info.get("protocol") == "ssh" and any(pattern in lowered for pattern in ssh_auth_patterns):
        return {
            "auth_required": False,
            "credential_supported": False,
            "message": "SSH 仓库认证失败，请在服务器上配置可用的 SSH Key，或将远程地址切换为 HTTPS。",
        }
    return {
        "auth_required": False,
        "credential_supported": remote_info.get("protocol") in {"http", "https"},
        "message": "",
    }


async def _get_repository_lock(root_id: int) -> asyncio.Lock:
    async with _repo_locks_guard:
        lock = _repo_locks.get(root_id)
        if lock is None:
            lock = asyncio.Lock()
            _repo_locks[root_id] = lock
        return lock


def _build_askpass_files(temp_dir: Path) -> Path:
    ps1_path = temp_dir / "askpass.ps1"
    cmd_path = temp_dir / "askpass.cmd"
    ps1_path.write_text(
        "param([string]$prompt)\n"
        "if (($prompt | Out-String) -match '(?i)username') {\n"
        "    [Console]::Out.Write($env:LS_GIT_USERNAME)\n"
        "} else {\n"
        "    [Console]::Out.Write($env:LS_GIT_PASSWORD)\n"
        "}\n",
        encoding="utf-8",
    )
    cmd_path.write_text(
        "@echo off\r\n"
        "powershell -NoProfile -ExecutionPolicy Bypass -File \"%~dp0askpass.ps1\" %*\r\n",
        encoding="utf-8",
    )
    return cmd_path


async def _run_git_command(command_tokens: list[str], cwd: Path, credential: dict | None = None) -> dict:
    temp_dir_obj = None
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"

    if credential and credential.get("secret"):
        temp_dir_obj = tempfile.TemporaryDirectory(prefix="lanshare-git-auth-")
        askpass_path = _build_askpass_files(Path(temp_dir_obj.name))
        env["GIT_ASKPASS"] = str(askpass_path)
        env["LS_GIT_USERNAME"] = str(credential.get("username") or "git")
        env["LS_GIT_PASSWORD"] = str(credential.get("secret") or "")

    process = await asyncio.create_subprocess_exec(
        *command_tokens,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    timed_out = False
    stdout_data = b""
    stderr_data = b""
    try:
        stdout_data, stderr_data = await asyncio.wait_for(process.communicate(), timeout=GIT_COMMAND_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        timed_out = True
        process.kill()
        stdout_data, stderr_data = await process.communicate()
    finally:
        if temp_dir_obj is not None:
            temp_dir_obj.cleanup()

    command_text = subprocess.list2cmdline(command_tokens)
    return {
        "command": command_text,
        "returncode": -1 if timed_out else int(process.returncode or 0),
        "stdout": stdout_data.decode("utf-8", errors="ignore"),
        "stderr": stderr_data.decode("utf-8", errors="ignore"),
        "timed_out": timed_out,
    }


async def _ensure_repository_identity(workspace_dir: Path, teacher_user: dict) -> list[dict]:
    execution_log: list[dict] = []
    name_result = await _run_git_command(["git", "config", "--get", "user.name"], workspace_dir)
    name_result["allow_failure"] = True
    execution_log.append(name_result)
    if name_result["returncode"] != 0 or not str(name_result.get("stdout") or "").strip():
        set_name_result = await _run_git_command(
            ["git", "config", "user.name", str(teacher_user.get("name") or f"Teacher {teacher_user['id']}")],
            workspace_dir,
        )
        execution_log.append(set_name_result)

    email_result = await _run_git_command(["git", "config", "--get", "user.email"], workspace_dir)
    email_result["allow_failure"] = True
    execution_log.append(email_result)
    if email_result["returncode"] != 0 or not str(email_result.get("stdout") or "").strip():
        email_value = str(teacher_user.get("email") or f"teacher-{teacher_user['id']}@lanshare.local")
        set_email_result = await _run_git_command(
            ["git", "config", "user.email", email_value],
            workspace_dir,
        )
        execution_log.append(set_email_result)
    return execution_log


def _parse_custom_command(command: str) -> list[str]:
    normalized = str(command or "").strip()
    if not normalized:
        raise HTTPException(400, "请输入 Git 命令")

    try:
        tokens = shlex.split(normalized, posix=False)
    except ValueError as exc:
        raise HTTPException(400, f"Git 命令格式错误: {exc}") from exc

    if not tokens:
        raise HTTPException(400, "请输入 Git 命令")
    if tokens[0].lower() != "git":
        tokens.insert(0, "git")
    return tokens


async def execute_material_repository_action(
    conn_factory,
    material_id: int,
    teacher_user: dict,
    action: str,
    custom_command: str = "",
) -> dict:
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in {"update", "commit_push", "custom"}:
        raise HTTPException(400, "不支持的仓库操作")

    with conn_factory() as conn:
        repo_detail = get_material_repository_detail(conn, material_id, int(teacher_user["id"]))
        remote_info = parse_git_remote_url(repo_detail["remote_url"])
        credential = _load_saved_git_credential(conn, int(teacher_user["id"]), remote_info)

    repo_lock = await _get_repository_lock(int(material_id))
    async with repo_lock:
        with tempfile.TemporaryDirectory(prefix=f"lanshare-repo-{material_id}-") as temp_dir_name:
            workspace_dir = Path(temp_dir_name) / "workspace"
            execution_log: list[dict] = []

            with conn_factory() as conn:
                root_row = conn.execute(
                    "SELECT * FROM course_materials WHERE id = ? AND teacher_id = ?",
                    (material_id, teacher_user["id"]),
                ).fetchone()
                if not root_row:
                    raise HTTPException(404, "仓库材料不存在")
                _export_repository_workspace(conn, dict(root_row), workspace_dir)

            message = ""
            if normalized_action == "update":
                if not repo_detail["can_update"]:
                    return {
                        "status": "failed",
                        "message": "当前仓库缺少远程地址或默认分支，无法执行自动更新。",
                        "execution_log": [],
                        "combined_output": "",
                        "repository": repo_detail,
                        "sync_summary": {"inserted": 0, "updated": 0, "deleted": 0, "unchanged": 0},
                    }
                execution_log.append(
                    await _run_git_command(
                        ["git", "pull", repo_detail["remote_name"], repo_detail["default_branch"]],
                        workspace_dir,
                        credential,
                    )
                )
                message = "仓库更新完成" if execution_log[-1]["returncode"] == 0 else "仓库更新失败"
            elif normalized_action == "commit_push":
                if not repo_detail["can_commit_push"]:
                    return {
                        "status": "failed",
                        "message": "当前仓库缺少远程地址或默认分支，无法执行提交并推送。",
                        "execution_log": [],
                        "combined_output": "",
                        "repository": repo_detail,
                        "sync_summary": {"inserted": 0, "updated": 0, "deleted": 0, "unchanged": 0},
                    }
                execution_log.extend(await _ensure_repository_identity(workspace_dir, teacher_user))
                execution_log.append(await _run_git_command(["git", "add", "-A"], workspace_dir, credential))
                status_result = await _run_git_command(["git", "status", "--porcelain"], workspace_dir, credential)
                execution_log.append(status_result)
                has_pending_changes = bool(str(status_result.get("stdout") or "").strip())
                if has_pending_changes:
                    commit_result = await _run_git_command(
                        ["git", "commit", "-m", DEFAULT_COMMIT_MESSAGE],
                        workspace_dir,
                        credential,
                    )
                    execution_log.append(commit_result)
                execution_log.append(
                    await _run_git_command(
                        ["git", "push", repo_detail["remote_name"], repo_detail["default_branch"]],
                        workspace_dir,
                        credential,
                    )
                )
                message = "提交并推送完成" if execution_log[-1]["returncode"] == 0 else "提交或推送失败"
            else:
                execution_log.append(await _run_git_command(_parse_custom_command(custom_command), workspace_dir, credential))
                message = "Git 命令执行完成" if execution_log[-1]["returncode"] == 0 else "Git 命令执行失败"

            removable_hashes: list[str] = []
            with conn_factory() as conn:
                latest_root_row = conn.execute(
                    "SELECT * FROM course_materials WHERE id = ? AND teacher_id = ?",
                    (material_id, teacher_user["id"]),
                ).fetchone()
                if not latest_root_row:
                    raise HTTPException(404, "仓库材料不存在")
                sync_summary, removable_hashes = _sync_workspace_to_repository(conn, dict(latest_root_row), workspace_dir)
                refreshed_row = refresh_root_git_metadata(conn, material_id, latest_root_row)

                if credential:
                    conn.execute(
                        "UPDATE teacher_git_credentials SET last_used_at = ? WHERE id = ?",
                        (_now_iso(), credential["id"]),
                    )
                conn.commit()
                updated_repository = get_material_repository_detail(conn, material_id, int(teacher_user["id"]))
                updated_repository["git_repo_status"] = refreshed_row.get("git_repo_status")

            for file_hash in removable_hashes:
                await delete_file_safely(Path(GLOBAL_FILES_DIR) / file_hash)

        combined_output = _build_combined_output(execution_log)
        auth_state = _classify_auth_failure(remote_info, combined_output)
        has_failures = any(entry["returncode"] != 0 and not entry.get("allow_failure") for entry in execution_log)

        if auth_state["auth_required"]:
            return {
                "status": "auth_required",
                "message": "远程仓库需要认证，请填写凭据后重试。",
                "execution_log": execution_log,
                "combined_output": combined_output,
                "repository": updated_repository,
                "sync_summary": sync_summary,
                "credential_saved": bool(credential),
                "credential_supported": auth_state["credential_supported"],
            }

        if has_failures:
            return {
                "status": "failed",
                "message": auth_state["message"] or message,
                "execution_log": execution_log,
                "combined_output": combined_output,
                "repository": updated_repository,
                "sync_summary": sync_summary,
                "credential_saved": bool(credential),
                "credential_supported": auth_state["credential_supported"],
            }

        return {
            "status": "success",
            "message": message,
            "execution_log": execution_log,
            "combined_output": combined_output,
            "repository": updated_repository,
            "sync_summary": sync_summary,
            "credential_saved": bool(credential),
            "credential_supported": auth_state["credential_supported"],
        }
