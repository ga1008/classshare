"""Normalize an imported project manifest and derive explicit per-topic read scopes."""

import hashlib
import posixpath
import re
import unicodedata
from collections import deque
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from fastapi import HTTPException


MAX_TOPICS = 300
MAX_FILES = 600
MAX_PARSE_BYTES = 2 * 1024 * 1024
MAX_LINKS = 800
MAX_PACKAGE_LINKS = 12000
HTML = {".html", ".htm"}
ENTRY_TYPES = HTML | {".pdf", ".docx", ".doc", ".md", ".txt", ".ppt", ".pptx", ".odt", ".rtf"}
PASSIVE_ASSETS = {
    ".css", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".mp4", ".webm", ".ogg", ".vtt"
}
PRIVATE_INDEX_NAMES = {"main", "index", "readme", "rules", "materials", "总则", "目录", "公共说明"}
CSS_URL = re.compile(r"""(?:url\(\s*["']?([^"')\s]+)|@import\s+["']([^"']+))""", re.I)


def fail(message):
    raise HTTPException(422, message)


def safe_path(value):
    if not isinstance(value, str):
        fail("题包文件路径必须为文本。")
    path = unicodedata.normalize("NFC", value.replace("\\", "/"))
    if (len(path) > 600 or any(part in {"", ".", ".."} or part.startswith(".") for part in path.split("/"))
            or re.search(r"[:\x00-\x1f\x7f]", path)):
        fail(f"题包相对路径无效：{path[:120]}")
    return path


def _text(value, label, limit=200, *, required=False):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value or (required and not value.strip()):
        fail(f"{label}为空、格式不正确或超过 {limit} 字。")
    return value.strip()


def _key(value, label):
    value = _text(value, label, 96, required=True)
    if not re.fullmatch(r"[\w.-]+", value) or value in {".", ".."}:
        fail(f"{label}只支持文字、数字、点、横线和下划线。")
    return value


def _path_list(value, label):
    if not isinstance(value, list) or len(value) > MAX_FILES:
        fail(f"{label}需要文件路径列表，最多 {MAX_FILES} 项。")
    return list(dict.fromkeys(safe_path(v) for v in value))


def normalize_manifest(raw, fallback_title):
    if not isinstance(raw, dict) or type(raw.get("formatVersion")) is not int or raw["formatVersion"] != 1:
        fail("题包清单需要 formatVersion 为 1。")
    if not isinstance(raw.get("topics"), list) or len(raw["topics"]) > MAX_TOPICS:
        fail(f"题包需要 topics 列表，最多 {MAX_TOPICS} 题。")
    result = {
        "formatVersion": 1, "packageKey": _key(raw.get("packageKey"), "题包标识"),
        "version": _text(raw.get("version"), "版本", 80, required=True),
        "title": _text(raw.get("title") or fallback_title, "题包名称", required=True),
        "common": _path_list(raw.get("common", []), "公共说明"),
        "commonAssets": _path_list(raw.get("commonAssets", []), "公共配套文件"),
        "topics": [],
    }
    keys = set()
    for i, value in enumerate(raw["topics"], 1):
        if not isinstance(value, dict):
            fail(f"第 {i} 项题目格式不正确。")
        key = _key(value.get("key"), "题目标识")
        if key in keys:
            fail(f"题目标识重复：{key}")
        keys.add(key)
        number = value.get("number", str(i))
        if type(number) is int:
            number = str(number)
        pool = value.get("pool", "regular")
        if pool not in ("regular", "reserve"):
            fail("题池须选择 regular 或 reserve。")
        result["topics"].append({
            "key": key, "number": _text(number, "展示题号", 60),
            "title": _text(value.get("title"), "题名", required=True),
            "brief": _text(value.get("brief", ""), "题目简介", 16000),
            "entry": safe_path(value["entry"]) if value.get("entry") else "",
            "attachments": _path_list(value.get("attachments", []), "题目附件"),
            "assets": _path_list(value.get("assets", []), "题目配套文件"),
            "pool": pool,
        })
    return result


def suggest_manifest(files, title):
    topics = []
    # A catalogue and rule page may contain every group's requirements.
    for path in files:
        name = PurePosixPath(path)
        if name.suffix.lower() not in ENTRY_TYPES or name.stem.lower() in PRIVATE_INDEX_NAMES:
            continue
        if len(topics) == MAX_TOPICS:
            break
        topics.append({"key": "t-" + hashlib.sha256(path.encode()).hexdigest()[:16],
                       "number": str(len(topics) + 1), "title": name.stem[:200],
                       "entry": path, "pool": "regular"})
    # Two unrelated legacy kits can have the same name, group numbers and
    # filenames. Only an explicit manifest can declare continuity after edits.
    source = hashlib.sha256()
    for path, data in sorted(files.items()):
        source.update(path.encode() + b"\0" + hashlib.sha256(data).digest())
    return {"formatVersion": 1, "packageKey": "p-" + source.hexdigest()[:24],
            "version": "1", "title": title, "common": [], "topics": topics}


class _References(HTMLParser):
    """Navigation is checked for broken links but never implicitly granted."""
    def __init__(self):
        super().__init__()
        self.links = set()
        self.has_body = False
        self._skip = None
        self._in_head = False
        self.overflow = False

    def add(self, value, asset):
        if value and len(value) <= 2048:
            if len(self.links) < MAX_LINKS:
                self.links.add((value, asset))
            else:
                self.overflow = True

    def css(self, text):
        for match in CSS_URL.finditer(text):
            self.add(match[1] or match[2], True)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "head":
            self._in_head = True
        if tag in {"script", "style", "title"} and not self._skip:
            self._skip = tag
        if tag in {"img", "video", "audio", "object", "iframe"}:
            self.has_body = True
        if a.get("style"):
            self.css(a["style"])
        for attr in ("href", "src", "poster", "data", "srcset"):
            value = a.get(attr)
            if not value:
                continue
            asset = (tag in {"img", "source", "video", "audio", "track"} or
                     (tag == "link" and "stylesheet" in (a.get("rel") or "").lower().split()))
            if attr == "srcset":
                for option in value.split(","):
                    if option.strip():
                        self.add(option.split()[0], asset)
            else:
                self.add(value, asset)

    def handle_endtag(self, tag):
        if tag == "head":
            self._in_head = False
        if self._skip == tag:
            self._skip = None

    def handle_data(self, data):
        if self._skip == "style":
            self.css(data)
        if not self._skip and not self._in_head and data.strip():
            self.has_body = True


def _local_reference(parent, target):
    try:
        parsed = urlsplit(target.strip())
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    path = unquote(parsed.path).replace("\\", "/")
    if path.startswith("/"):
        return None
    return posixpath.normpath(posixpath.join(posixpath.dirname(parent), path))


def inspect_resource(path, payload):
    ext = PurePosixPath(path).suffix.lower()
    if ext not in HTML | {".css", ".md", ".txt"} or not payload:
        return {}, bool(payload), []
    if len(payload) > MAX_PARSE_BYTES:
        return {}, False, [f"{path} 超过文本预览检查上限，请拆分或转换为 PDF。"]
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = payload.decode("gb18030", errors="replace")
    refs = _References()
    if ext in HTML:
        try:
            refs.feed(text)
            refs.close()
        except (ValueError, AssertionError):
            return {}, False, [f"{path} 的 HTML 格式无法解析，请修复。"]
    elif ext == ".css":
        refs.css(text)
    else:
        return {}, bool(text.strip()), []
    paths = {}
    for target, asset in sorted(refs.links):
        rel = _local_reference(path, target)
        if rel:
            paths[rel] = paths.get(rel, False) or (asset and PurePosixPath(rel).suffix.lower() in PASSIVE_ASSETS)
    warnings = [f"{path} 的页面链接超过 {MAX_LINKS} 项，请拆分后导入。"] if refs.overflow else []
    return paths, (bool(text.strip()) if ext == ".css" else refs.has_body) and not warnings, warnings


def compile_mapping(manifest, files, inspections):
    """files maps canonical paths to sizes; inspections contain only offline parse results."""
    if sum(len(refs) for refs, _, _ in inspections.values()) > MAX_PACKAGE_LINKS:
        fail(f"整个题包超过 {MAX_PACKAGE_LINKS} 个页面引用，请拆分导入。")
    package_issues = set()
    for path, size in files.items():
        links, _, warnings = inspections[path]
        package_issues.update(warnings)
        if not size:
            package_issues.add(f"空文件：{path}")
        for target in links:
            if target not in files:
                package_issues.add(f"{path} 引用了缺失文件：{target}")

    # A navigation link or an automatically discovered image must not turn a
    # neighbour's topic folder into an implicit grant. Explicit shared assets
    # (e.g. a common style sheet) can still be used by multiple topics.
    direct_owners, directory_owners = {}, {}
    for item in manifest["topics"]:
        for path in filter(None, [item["entry"], *item["attachments"], *item["assets"]]):
            direct_owners.setdefault(path, set()).add(item["key"])
        directory = posixpath.dirname(item["entry"])
        if directory:
            directory_owners.setdefault(directory, set()).add(item["key"])
    def owners(path):
        if path in direct_owners:
            return direct_owners[path]
        parent = posixpath.dirname(path)
        while parent:
            if parent in directory_owners:
                return directory_owners[parent]
            parent = posixpath.dirname(parent)
        return set()

    def scope(declared, key):
        visited, approved, problems = set(), set(), set()
        declared = set(declared)
        pending = deque(declared)
        while pending:
            path = pending.popleft()
            if path in visited:
                continue
            visited.add(path)
            if path not in files or not files[path]:
                problems.add(f"文件缺失或为空：{path}")
                continue
            assigned = owners(path)
            if (not key or path not in declared) and assigned and key not in assigned:
                problems.add(f"文件属于其他题目的范围：{path}")
                continue
            if path in {"project-topics.json", "manifest.json"}:
                problems.add(f"题目清单仅教师可见：{path}")
                continue
            approved.add(path)
            links, _, warnings = inspections[path]
            problems.update(warnings)
            for target, passive in links.items():
                if passive and target not in visited:
                    pending.append(target)
                elif not key and not passive and target not in declared:
                    problems.add(f"公共页面关联了未确认为公共的文件：{path} → {target}")
        return approved, sorted(problems)

    common, common_issues = scope(manifest["common"] + manifest["commonAssets"], "")
    for path in manifest["common"]:
        if path in files and not inspections[path][1]:
            common_issues.append(f"公共说明没有可读内容：{path}")
    scopes, items = {"": common}, []
    for raw in manifest["topics"]:
        entry = raw["entry"]
        declared = raw["attachments"] + raw["assets"] + ([entry] if entry else [])
        granted, errors = scope(declared, raw["key"])
        if entry and entry in files and (not inspections[entry][1] or PurePosixPath(entry).suffix.lower() not in ENTRY_TYPES):
            errors.append(f"任务书没有可读正文或类型不支持：{entry}")
        if errors:
            status = "missing"
        elif entry:
            status = "complete"
        else:
            status = "brief" if raw["brief"] else "missing"
        if status == "missing" and not errors:
            errors.append("尚未填写题目简介或绑定任务书。")
        if status != "missing":
            visible = granted | common
            for path in granted:
                for target, automatic in inspections[path][0].items():
                    if not automatic and target in files and target not in visible:
                        package_issues.add(f"{path} 关联的文件仅教师可见：{target}")
        scopes[raw["key"]] = granted
        items.append({**raw, "readiness": status, "issues": errors})
    return items, scopes, sorted(package_issues), common_issues
