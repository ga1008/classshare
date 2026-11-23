"""Read a teacher ZIP into an empty private directory and validate its v1 contracts."""

from __future__ import annotations

from collections import defaultdict, deque
from functools import lru_cache
from hashlib import sha256
from html.parser import HTMLParser
import json
import math
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import unicodedata
from urllib.parse import urlsplit, unquote
import zipfile
import zlib

from jsonschema import Draft202012Validator
from PIL import Image, UnidentifiedImageError
from fastapi import HTTPException

from .minigame_runtime_rules import validate_private

BASE = Path(__file__).resolve().parents[2]
SCHEMAS = BASE / "docs/minigames/contracts"
SDK = BASE / "static/minigames/lan-game-sdk.v1.js"
MIB = 1024 * 1024
MAX_ZIP, MAX_TOTAL, MAX_FILE = 64 * MIB, 192 * MIB, 32 * MIB
TEXT_LIMIT = 8 * MIB
MIME = {
    "html": "text/html",
    "css": "text/css",
    "js": "text/javascript",
    "json": "application/json",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "wav": "audio/wav",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "woff2": "font/woff2",
}
RESERVED = re.compile(r"^(?:con|nul|aux|prn|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)", re.I)
CSS_LINKS = re.compile(r"""url\(\s*["']?([^\s)'"\x00]+)|@import\s+["']([^"']+)""", re.I)


class InvalidPackage(ValueError):
    def __init__(self, code, message, file="", field=""):
        super().__init__(message)
        self.issue = {"code": code, "message": message, "file": file, "field": field}


def fail(code, message, file="", field=""):
    raise InvalidPackage(code, message, file, field)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def parse_json(source, filename, *, integers_only=True):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result or key in {"__proto__", "constructor", "prototype"}:
                raise ValueError("对象键重复或保留")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(value)

    try:
        obj = json.loads(source, object_pairs_hook=pairs, parse_constant=invalid_constant)
        stack = [(obj, 0)]
        while stack:
            node, depth = stack.pop()
            if depth > 16 or isinstance(node, float) and (integers_only or not math.isfinite(node)):
                raise ValueError("嵌套深度或数值超出协议")
            if isinstance(node, dict):
                stack.extend((entry, depth + 1) for entry in node.values())
            elif isinstance(node, list):
                stack.extend((entry, depth + 1) for entry in node)
        return obj
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InvalidPackage(
            "INVALID_JSON", "需要 UTF-8、唯一字段、有界整数及不超过 16 层的 JSON。", filename
        ) from exc


@lru_cache(maxsize=4)
def validator(name):
    return Draft202012Validator(json.loads((SCHEMAS / (name + ".schema.json")).read_text(encoding="utf-8")))


def validate_schema(data, name, filename):
    error = next(validator(name).iter_errors(data), None)
    if error:
        field = ".".join(str(k) for k in error.absolute_path)
        fail(
            "INVALID_CONTRACT",
            "字段未满足 " + str(error.validator) + " 约束，请对照作者协议。",
            filename,
            field,
        )


def clean_member(raw):
    path = unicodedata.normalize("NFC", raw)
    parts = path.split("/")
    if (
        len(path.encode("utf-8")) > 240
        or len(parts) > 16
        or any(
            not part or part.startswith(".") or part.endswith((" ", ".")) or RESERVED.match(part)
            for part in parts
        )
        or re.search(r'[\\:%#?*"<>|\x00-\x1f\x7f]', path)
    ):
        fail("INVALID_PATH", "包内需要跨平台有效的相对路径。")
    return path


def member_type(path):
    ext = PurePosixPath(path).suffix.lower().lstrip(".")
    if (
        path in {"main.html", "config.json", "contracts/rules.json", "contracts/writeback.json"}
        or (path.startswith("js/") and ext == "js")
        or (path.startswith("css/") and ext == "css")
        or (path.startswith("static/") and ext in set(MIME) - {"html", "css", "js"})
    ):
        return MIME[ext]
    fail("FILE_TYPE", "此文件类型或所在目录不受游戏包协议支持。", path)


def extract(source, runtime):
    entries = []
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        if len(infos) > 2000:
            fail("ENTRY_QUOTA", "ZIP 条目超过 2000。")
        seen, dirs, files = set(), set(), set()
        names = {}
        declared = 0
        for info in infos:
            directory = info.is_dir()
            path = clean_member(info.orig_filename[:-1] if directory else info.orig_filename)
            kind = stat.S_IFMT(info.external_attr >> 16)
            if (
                info.flag_bits & 1
                or kind not in (0, stat.S_IFDIR if directory else stat.S_IFREG)
                or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
            ):
                fail("ZIP_ENTRY", "仅支持未加密的普通文件和目录。", path)
            key = path.casefold()
            prefixes = ["/".join(path.split("/")[:n]) for n in range(1, path.count("/") + 2)]
            if key in seen or any(p.casefold() in files for p in prefixes) or (not directory and key in dirs):
                fail("DUPLICATE_PATH", "包内文件或目录重名。", path)
            for prefix in prefixes:
                if names.setdefault(prefix.casefold(), prefix) != prefix:
                    fail("DUPLICATE_PATH", "文件名或上级目录大小写不一致。", path)
            seen.add(key)
            dirs.update(p.casefold() for p in prefixes[:-1])
            if directory:
                dirs.add(key)
                continue
            files.add(key)
            declared += info.file_size
            if (
                len(files) > 1000
                or info.file_size > MAX_FILE
                or declared > MAX_TOTAL
                or info.file_size > max(info.compress_size, 1) * 1500
            ):
                fail("EXPANDED_QUOTA", "展开体积、压缩比或文件数超出配额。", path)
            entries.append((path, info))
        if not entries:
            fail("EMPTY_ZIP", "ZIP 内没有可读取的文件。")
        roots = {p.split("/", 1)[0] for p, _ in entries}
        wrapped = len(roots) == 1 and all("/" in p for p, _ in entries)
        manifest, total = {}, 0
        for original, info in entries:
            path = original.split("/", 1)[1] if wrapped else original
            mime = member_type(path)
            target = runtime / path
            target.parent.mkdir(parents=True, exist_ok=True)
            digest, size = sha256(), 0
            with archive.open(info) as src, target.open("xb") as out:
                while block := src.read(256 * 1024):
                    size += len(block)
                    total += len(block)
                    if size > MAX_FILE or total > MAX_TOTAL:
                        fail("EXPANDED_QUOTA", "实际展开体积超出配额。", path)
                    digest.update(block)
                    out.write(block)
            if size != info.file_size:
                fail("ZIP_ENTRY", "文件大小校验不一致。", path)
            manifest[path] = {"size": size, "mime": mime, "sha256": digest.hexdigest()}
    return manifest


def text_file(root, path, files):
    if path not in files:
        fail("MISSING_FILE", "包内缺少必需文件。", path)
    if files[path]["size"] > TEXT_LIMIT:
        fail("TEXT_QUOTA", "协议、脚本和样式单文件上限为 8 MiB。", path)
    try:
        return (root / path).read_text(encoding="utf-8-sig")
    except UnicodeError as exc:
        raise InvalidPackage("TEXT_ENCODING", "文本文件必须为 UTF-8。", path) from exc


def check_link(base, raw, files):
    if not raw or raw.startswith("#"):
        return
    try:
        url = urlsplit(raw)
    except ValueError as exc:
        raise InvalidPackage("EXTERNAL_RESOURCE", "请改为有效的包内相对路径。", base) from exc
    member = unquote(url.path)
    if (
        url.scheme
        or url.netloc
        or url.query
        or not member
        or member.startswith(("/", "\\"))
        or "\\" in member
    ):
        fail("EXTERNAL_RESOURCE", "运行素材须使用包内相对路径且无查询串。", base)
    target = posixpath.normpath(posixpath.join(posixpath.dirname(base), member))
    if target not in files:
        fail("MISSING_RESOURCE", "包内不存在相对引用：" + target[:160], base)


class EntryParser(HTMLParser):
    def __init__(self, files):
        super().__init__()
        self.files = files
        self.scripts = []
        self.in_script = False

    def handle_starttag(self, tag, attrs):
        vals = dict(attrs)
        if tag in {"base", "iframe", "frame", "object", "embed", "form"} or (
            tag == "meta" and (vals.get("http-equiv") or "").lower() == "refresh"
        ):
            fail("HTML_ELEMENT", "入口包含沙盒不支持的元素。", "main.html", tag)
        if any(k.lower().startswith("on") for k, _ in attrs):
            fail("INLINE_SCRIPT", "事件处理器请写入本地 classic JS 文件。", "main.html")
        if tag == "script":
            self.in_script = True
            if not vals.get("src") or (vals.get("type") or "").lower() not in (
                "",
                "text/javascript",
                "application/javascript",
            ):
                fail("SCRIPT_TYPE", "v1 仅允许包内外置 classic JS。", "main.html")
            self.scripts.append(posixpath.normpath(vals["src"]))
        for key in ("href", "src", "poster", "action", "data"):
            if vals.get(key):
                check_link("main.html", vals[key], self.files)
        for candidate in (vals.get("srcset") or "").split(","):
            if candidate.strip():
                check_link("main.html", candidate.strip().split()[0], self.files)

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_script = False

    def handle_data(self, data):
        if self.in_script and data.strip():
            fail("INLINE_SCRIPT", "入口包含内联脚本。", "main.html")


def check_shape_definition(shape, name, depth=0):
    wf = "contracts/writeback.json"
    if (
        depth > 8
        or shape.get("minimum", 0) > shape.get("maximum", 0)
        or shape.get("minLength", 0) > shape.get("maxLength", 0)
    ):
        fail("SAVE_SHAPE", "存档字段层级或上下限不合法。", wf, name)
    if shape["type"] == "object":
        if not set(shape["required"]) <= shape["properties"].keys():
            fail("SAVE_SHAPE", "required 中含未声明的字段。", wf, name)
        for child in shape["properties"].values():
            check_shape_definition(child, name, depth + 1)
    elif shape["type"] == "array":
        check_shape_definition(shape["items"], name, depth + 1)


def check_contracts(config, writeback, rules):
    multi = config["modes"]["multiplayer"]
    if multi and (
        multi["minPlayers"] > multi["maxPlayers"]
        or any(not multi["minPlayers"] <= n <= multi["maxPlayers"] for n in multi["randomSizes"])
    ):
        fail("PLAYER_RANGE", "人数须在所声明的闭区间内。", "config.json", "modes.multiplayer")
    if not config["title"].strip() or not config["summary"].strip():
        fail("EMPTY_TEXT", "标题与简介不能为空。", "config.json")
    wf = "contracts/writeback.json"
    if bool(writeback["privateFields"]) != ("save.private" in config["capabilities"]):
        fail("SAVE_CAPABILITY", "私有字段与 save.private 声明不一致。", wf)
    for name, spec in writeback["privateFields"].items():
        if spec["type"] == "document":
            check_shape_definition(spec["schema"], name)
    try:
        validate_private(
            {k: v["default"] for k, v in writeback["privateFields"].items()}, writeback["privateFields"]
        )
    except HTTPException:
        fail("SAVE_DEFAULT", "存档字段的默认值不符合声明。", wf)
    milestones = writeback["milestones"]
    resume = [m["resumeState"] for m in milestones]
    if len({m["id"] for m in milestones}) != len(milestones) or len(set(resume)) != len(resume):
        fail("MILESTONE", "里程碑与恢复状态必须唯一。", wf)
    rf = "contracts/rules.json"
    if rules is None:
        if resume != [None]:
            fail("MILESTONE", "自由单机只能有一个空恢复状态。", wf)
        return
    if rules["engine"] == "grid-line-v1":
        if rules["line"] > rules["size"]:
            fail("E_RULES_LINE_EXCEEDS_SIZE", "连线长度超出棋盘尺寸。", rf, "line")
        if (
            not multi
            or multi["kind"] != "versus"
            or multi["minPlayers"] != 2
            or multi["maxPlayers"] != 2
            or resume != ["board"]
        ):
            fail("GRID_MODE", "棋盘需要双人对战与唯一 board 恢复点。", rf)
        return
    states, first = rules["states"], rules["initial"]
    if (
        first not in states
        or resume[0] != first
        or any(n not in states or states[n]["outcome"] for n in resume)
    ):
        fail("RULE_STATE", "初始或恢复状态不可游玩。", rf)
    edges, previous, keys = defaultdict(list), defaultdict(list), set()
    for edge in rules["transitions"]:
        start, end = edge["from"], edge["to"]
        key = (start, edge["type"], canonical(edge["payload"]))
        if start not in states or end not in states or states[start]["outcome"] or key in keys:
            fail("RULE_EDGE", "存在缺失状态、终态出边或有歧义的操作。", rf)
        keys.add(key)
        edges[start].append(edge)
        previous[end].append(start)
    terminals = {n for n, state in states.items() if state["outcome"]}
    if multi and multi["kind"] == "coop" and any(states[n]["outcome"] == "actorWins" for n in terminals):
        fail("RULE_OUTCOME", "合作模式不能使用单人获胜结算。", rf)
    reaches_end, pending = set(terminals), deque(terminals)
    while pending:
        for parent in previous[pending.popleft()]:
            if parent not in reaches_end:
                reaches_end.add(parent)
                pending.append(parent)
    if reaches_end != set(states):
        fail("RULE_DEAD_END", "存在无法到达结局的状态。", rf)
    ranks = {node: i for i, node in enumerate(resume)}
    pending = deque([(first, 0)])
    visited = {(first, 0)}
    budget = 100_000
    while pending:
        node, rank = pending.popleft()
        for edge in edges[node]:
            budget -= 1
            if budget < 0:
                fail("RULE_BUDGET", "规则图超出验收运算预算。", rf)
            dst = edge["to"]
            new_rank = max(rank, ranks.get(dst, rank))
            if new_rank > rank + 1:
                fail("MILESTONE_SKIP", "存在首次跨越多个里程碑的路径。", rf)
            if (dst, new_rank) not in visited:
                visited.add((dst, new_rank))
                pending.append((dst, new_rank))
    if {n for n, _ in visited} != set(states):
        fail("RULE_UNREACHABLE", "存在无法到达的规则状态。", rf)
    if multi and all(edge["actor"] == "turn" for edge in rules["transitions"]):
        size = multi["maxPlayers"] if multi["privateRooms"] else max(multi["randomSizes"])
        queue = deque([(first, 1)])
        seen, seats = {(first, 1)}, set()
        while queue and len(seats) < size:
            node, seat = queue.popleft()
            if edges[node]:
                seats.add(seat)
            for edge in edges[node]:
                next_seat = seat % size + 1 if edge["turn"] == "next" else seat
                pair = (edge["to"], next_seat)
                if pair not in seen:
                    seen.add(pair)
                    queue.append(pair)
        if len(seats) < size:
            absent = "、".join(map(str, sorted(set(range(1, size + 1)) - seats)))
            fail("E_RULES_IDLE_SEAT", f"全新 {size} 人对局中，席位 {absent} 始终没有合法操作。", rf, first)


def validate_package(source, directory):
    if source.stat().st_size > MAX_ZIP:
        fail("ZIP_QUOTA", "压缩包上限为 64 MiB。")
    runtime = directory / "runtime"
    runtime.mkdir()
    try:
        files = extract(source, runtime)
    except (zipfile.BadZipFile, RuntimeError, UnicodeError, NotImplementedError, EOFError, zlib.error) as exc:
        raise InvalidPackage("INVALID_ZIP", "无法完整读取 ZIP，请重新打包。") from exc
    config = parse_json(text_file(runtime, "config.json", files), "config.json")
    validate_schema(config, "config", "config.json")
    writeback_path, rules_path = config["contracts"]["writeback"], config["contracts"]["rules"]
    writeback = parse_json(text_file(runtime, writeback_path, files), writeback_path)
    validate_schema(writeback, "writeback", writeback_path)
    rules = None
    if rules_path:
        rules = parse_json(text_file(runtime, rules_path, files), rules_path)
        validate_schema(rules, "rules", rules_path)
    check_contracts(config, writeback, rules)
    if files.get(config["sdk"], {}).get("sha256") != sha256(SDK.read_bytes()).hexdigest():
        fail("SDK_DIGEST", "请使用与平台发行版完全一致的 SDK。", config["sdk"])
    offline = "js/offline-contracts.js"
    content = text_file(runtime, offline, files).strip()
    match = re.fullmatch(r"window\.LanGameOfflineContracts\s*=\s*(\{.*\})\s*;", content, re.S)
    expected = {"config": config, "writeback": writeback, "rules": rules}
    if not match or canonical(parse_json(match.group(1), offline)) != canonical(expected):
        fail("OFFLINE_CONTRACTS", "离线描述与包内 JSON 不一致。", offline)
    entry = EntryParser(files)
    entry.feed(text_file(runtime, "main.html", files))
    entry.close()
    if entry.scripts[:2] != [config["sdk"], offline] or len(entry.scripts) < 3:
        fail("SCRIPT_ORDER", "先加载 SDK、离线描述，再加载游戏控制器。", "main.html")
    for path in config.get("assets", []):
        if path not in files:
            fail("MISSING_RESOURCE", "缺少声明的静态资源。", path)
    for path, meta in files.items():
        if path.startswith("css/"):
            for a, b in CSS_LINKS.findall(text_file(runtime, path, files)):
                check_link(path, a or b, files)
        elif path.startswith("js/"):
            text_file(runtime, path, files)
        elif path.startswith("static/") and meta["mime"] == "application/json":
            parse_json(text_file(runtime, path, files), path, integers_only=False)
        elif meta["mime"].startswith("image/"):
            try:
                with Image.open(runtime / path) as pic:
                    if Image.MIME.get(pic.format) != meta["mime"] or pic.width * pic.height > 20_000_000:
                        fail("IMAGE_FORMAT", "图片格式或分辨率不受支持。", path)
                    pic.verify()
            except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
                raise InvalidPackage("IMAGE_FORMAT", "无法解码图片。", path) from exc
        elif meta["mime"].startswith(("audio/", "video/", "font/")):
            with (runtime / path).open("rb") as stream:
                head = stream.read(16)
            valid = {
                "audio/mpeg": head.startswith(b"ID3")
                or len(head) >= 2
                and head[0] == 255
                and head[1] & 224 == 224,
                "audio/wav": head.startswith(b"RIFF") and head[8:12] == b"WAVE",
                "audio/ogg": head.startswith(b"OggS"),
                "video/mp4": head[4:8] == b"ftyp",
                "video/webm": head.startswith(bytes.fromhex("1A45DFA3")),
                "font/woff2": head.startswith(b"wOF2"),
            }
            if not valid[meta["mime"]]:
                fail("MEDIA_FORMAT", "素材文件头与扩展名不匹配。", path)
    icon = config["icon"]
    if icon not in files:
        fail("MISSING_ICON", "缺少游戏图标。", icon)
    with Image.open(runtime / icon) as pic:
        if not (128 <= pic.width <= 1024 and 128 <= pic.height <= 1024):
            fail("ICON_SIZE", "图标宽、高须各在 128 至 1024 像素之间。", icon)
        image = pic.convert("RGBA")
        image.thumbnail((160, 160))
        image.save(directory / "thumbnail.png")
    return {**expected, "files": files, "digest": sha256(canonical(files).encode()).hexdigest()}
