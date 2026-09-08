"""Validate the audited FineReport layout and retain its native PNG bytes.

This is deliberately a recognizer for one verified source layout, not an RTF
renderer. Visible report values may change; formatting and resource definitions
must still match the Word-verified native template before that template is used.
"""

from __future__ import annotations

import hashlib
import io
import re
import struct
import zlib
from dataclasses import dataclass, replace
from typing import Iterator

from PIL import Image

from .academic_final_material_source_service import NativeAcademicSourceError


_LAYOUT_ERROR = "教务原件版式与已校验原版不一致，已停止导出以免改变格式"
_MAX_SOURCE_BYTES = 32 * 1024 * 1024
_MAX_GROUP_DEPTH = 128
_MAX_TOKENS = 2_000_000
_RESOURCE_DESTINATIONS = {b"fonttbl", b"colortbl", b"stylesheet"}
_HIDDEN_DESTINATIONS = _RESOURCE_DESTINATIONS | {
    b"info", b"pict", b"shppict", b"nonshppict", b"generator",
    b"listtable", b"listoverridetable", b"xmlnstbl", b"datastore",
    b"themedata", b"colorschememapping",
}
_EXPECTED_IMAGE_SIZES = ((29, 95), (643, 200), (29, 190))
_REQUIRED_LABELS = (
    "广西外国语学院课程试卷分析表", "课程名称", "学生成绩分布图",
    "简要分析试题结构", "教学院长审核意见",
)

# Physical \row / \cell positions in the audited source, matching the native
# DOCX's direct w:tr / w:tc children (not expanded merged columns). Everything
# else is fixed text, including empty cells and the signature-label padding.
_DYNAMIC_TEXT_SLOTS = frozenset({
    (2, 0),  # Invisible source value is copied verbatim, never retained as "34".
    (3, 1), (3, 3), (3, 5), (4, 1),
    (4, 4), (4, 6), (5, 2), (5, 4), (5, 6),
    (6, 2), (6, 4), (6, 7), (6, 9), (7, 1),
    *((row, column) for row in (10, 11) for column in range(2, 7)),
    (12, 2), (12, 4), (13, 2), (13, 4), (13, 6),
    (18, 1), (20, 0), (20, 1),
})
_MARKING_LABEL_SLOTS = frozenset((14, column) for column in range(1, 6))

# Calculated from the audited native iText 2.1.7 FineReport RTF on 2026-09-08
# (.codex-temp/final-material-reports/analysis_source.rtf), excluding report
# values and image payloads. No student/course data is retained in these hashes.
# Audited source SHA-256:
# a3f127c671e98b506b06e960d205e296060788cb802764afca00ae062fdbb9ab
_EXPECTED_LAYOUT_SHA256 = "87edccd59c49fc131b58c2e58eb8577dbe76cfb1d0d23201c66c6767d1c0bce9"
_EXPECTED_RESOURCE_SHA256 = "20520d73c5a6e4d46cfc5d32e41526a0692d89a175eea8c96ec7364cc616e995"
_EXPECTED_STATIC_TEXT_SHA256 = "7b41944843665e949e1ea2310d5a8119c4c60014a28134e48876081ef1cd43bb"


class _InvalidRtf(ValueError):
    pass


@dataclass
class _Frame:
    hidden: bool = False
    resource: bool = False
    unicode_skip: int = 1
    picture: int | None = None


def _tokens(content: bytes) -> Iterator[tuple[str, bytes, int | None]]:
    """Scan bytes, consuming every ``\\binN`` payload before reading syntax.

    Decoding the entire RTF to text or searching for closing braces is unsafe:
    embedded PNG data can contain backslashes, braces and apparent RTF words.
    """
    index, length, count = 0, len(content), 0
    while index < length:
        count += 1
        if count > _MAX_TOKENS:
            raise _InvalidRtf("too many tokens")
        marker = content[index]
        if marker in (123, 125):
            yield ("open" if marker == 123 else "close", b"", None)
            index += 1
            continue
        if marker != 92:
            end = index + 1
            while end < length and content[end] not in (92, 123, 125):
                end += 1
            yield "text", content[index:end].replace(b"\r", b"").replace(b"\n", b""), None
            index = end
            continue
        index += 1
        if index >= length:
            raise _InvalidRtf("truncated escape")
        marker = content[index]
        if marker in (92, 123, 125):
            yield "literal", content[index:index + 1], None
            index += 1
            continue
        if marker == 39:
            raw = content[index + 1:index + 3]
            if len(raw) != 2 or any(value not in b"0123456789abcdefABCDEF" for value in raw):
                raise _InvalidRtf("invalid hex escape")
            yield "literal", bytes((int(raw, 16),)), None
            index += 3
            continue
        if not (65 <= marker <= 90 or 97 <= marker <= 122):
            yield "symbol", content[index:index + 1], None
            index += 1
            continue
        start = index
        while index < length and (65 <= content[index] <= 90 or 97 <= content[index] <= 122):
            index += 1
        word = content[start:index]
        if len(word) > 32:
            raise _InvalidRtf("oversized control word")
        parameter_start = index
        if index < length and content[index] == 45:
            index += 1
        digit_start = index
        while index < length and 48 <= content[index] <= 57:
            index += 1
        if index - digit_start > 10 or (digit_start > parameter_start and digit_start == index):
            raise _InvalidRtf("invalid parameter")
        parameter = int(content[parameter_start:index]) if index > digit_start else None
        if index < length and content[index] == 32:
            index += 1
        if word == b"bin":
            if parameter is None or parameter < 0 or parameter > length - index:
                raise _InvalidRtf("truncated binary data")
            yield "binary", content[index:index + parameter], None
            index += parameter
        else:
            yield "control", word, parameter


def _feed(digest, tag: bytes, value: bytes = b"") -> None:
    digest.update(tag)
    digest.update(len(value).to_bytes(4, "big"))
    digest.update(value)


def _static_text_fingerprint(rows: list[list[str]], outside_text: str) -> str:
    """Bind every fixed string to its physical cell, with narrow fill slots.

    The period cell keeps its punctuation and fixed Chinese labels while its
    numeric year/semester values can change. Marking choices share a cell with
    a fixed label, so only a trailing selection check is excluded there.
    """
    digest = hashlib.sha256()
    _feed(digest, b"V", b"gxufl-native-analysis-static-cell-text-v1")
    for row_index, row in enumerate(rows):
        for column_index, text in enumerate(row):
            slot = row_index, column_index
            position = f"{row_index},{column_index}".encode("ascii")
            if slot in _DYNAMIC_TEXT_SLOTS:
                _feed(digest, b"D", position)
                continue
            if slot == (1, 0):
                text = re.sub(r"[0-9]+", "#", text)
            elif slot in _MARKING_LABEL_SLOTS:
                text = re.sub(r"[ \t]*[√✓✔][ \t]*$", "", text)
            _feed(digest, b"S", position)
            _feed(digest, b"T", text.encode("utf-8", errors="surrogatepass"))
    _feed(digest, b"O", outside_text.encode("utf-8", errors="surrogatepass"))
    return digest.hexdigest()


def _inspect_rtf(content: bytes) -> tuple[str, str, str, list[bytes], str, str]:
    """Return independent structural/resource fingerprints and source media.

    Control words and parameters, including image goal sizes, keep their exact
    order and group boundaries. Only literal text, Unicode characters/fallbacks
    and binary length/content are excluded from the structural fingerprint.
    Font/color/style text has its own fingerprint, so changing a font's name
    cannot masquerade as a harmless visible-field edit.
    """
    if not isinstance(content, bytes) or not 0 < len(content) <= _MAX_SOURCE_BYTES:
        raise _InvalidRtf("invalid source size")
    if not content.lstrip(b" \t\r\n").startswith(b"{\\rtf1"):
        raise _InvalidRtf("not a supported RTF document")
    layout, resources = hashlib.sha256(), hashlib.sha256()
    _feed(layout, b"V", b"gxufl-native-analysis-layout-v1")
    _feed(resources, b"V", b"gxufl-native-analysis-resources-v1")
    stack: list[_Frame] = []
    visible: list[str] = []
    rows: list[list[str]] = []
    current_row: list[str] = []
    current_cell: list[str] = []
    outside_text: list[str] = []
    in_table = False
    pictures: list[list[bytes]] = []
    png_markers: set[int] = set()
    root_closed = False
    pending_fallback = 0

    def add_text(text: str) -> None:
        visible.append(text)
        (current_cell if in_table else outside_text).append(text)

    for kind, value, parameter in _tokens(content):
        if kind == "open":
            if root_closed or len(stack) >= _MAX_GROUP_DEPTH:
                raise _InvalidRtf("invalid group structure")
            pending_fallback = 0  # Unescaped braces terminate Unicode fallback.
            frame = replace(stack[-1]) if stack else _Frame()
            stack.append(frame)
            _feed(layout, b"{")
            if frame.resource:
                _feed(resources, b"{")
            continue
        if kind == "close":
            if not stack:
                raise _InvalidRtf("unbalanced group")
            pending_fallback = 0
            if stack[-1].resource:
                _feed(resources, b"}")
            stack.pop()
            _feed(layout, b"}")
            root_closed = not stack
            continue
        if not stack:
            if kind != "text" or value.strip(b" \t"):
                raise _InvalidRtf("content outside root")
            continue
        frame = stack[-1]
        if kind in {"text", "literal"}:
            if frame.picture is not None and value.strip(b" \t"):
                raise _InvalidRtf("unexpected text in binary picture")
            if pending_fallback:
                skipped = min(pending_fallback, len(value))
                value = value[skipped:]
                pending_fallback -= skipped
            if value:
                if frame.resource:
                    _feed(resources, b"T", value)
                elif not frame.hidden:
                    add_text(value.decode("latin-1"))
            continue
        if pending_fallback:
            # The audited writer emits ordinary or escaped literal fallbacks.
            # Fail closed for control/binary fallbacks instead of guessing at
            # alternate reader semantics that could conceal a layout change.
            raise _InvalidRtf("unsupported Unicode fallback")
        if kind == "binary":
            _feed(layout, b"C", b"bin")
            if frame.picture is None or not value:
                raise _InvalidRtf("binary outside picture")
            pictures[frame.picture].append(value)
            continue
        if kind == "symbol":
            _feed(layout, b"S", value)
            if value == b"*":
                frame.hidden = True
            continue
        if value == b"u":
            if parameter is None or not -32768 <= parameter <= 65535:
                raise _InvalidRtf("invalid Unicode value")
            character = chr(parameter & 0xFFFF)
            if frame.resource:
                _feed(resources, b"U", character.encode("utf-16-be", errors="surrogatepass"))
            elif not frame.hidden:
                add_text(character)
            pending_fallback = frame.unicode_skip
            continue
        _feed(layout, b"C", value + b"\0" + (str(parameter).encode("ascii") if parameter is not None else b""))
        if value == b"uc":
            if parameter is None or not 0 <= parameter <= 16:
                raise _InvalidRtf("invalid Unicode skip")
            frame.unicode_skip = parameter
        if value in _HIDDEN_DESTINATIONS:
            frame.hidden = True
        if value in _RESOURCE_DESTINATIONS:
            frame.resource = True
            _feed(resources, b"R", value)
        if value == b"pict":
            if frame.picture is not None:
                raise _InvalidRtf("nested picture")
            frame.picture = len(pictures)
            pictures.append([])
        if value == b"pngblip":
            if frame.picture is None:
                raise _InvalidRtf("PNG declaration outside picture")
            png_markers.add(frame.picture)
        if not frame.hidden:
            if value == b"trowd":
                in_table = True
            elif value == b"cell":
                if not in_table:
                    raise _InvalidRtf("cell outside table")
                current_row.append("".join(current_cell))
                current_cell.clear()
                visible.append("\n")
            elif value == b"row":
                if not in_table or not current_row or current_cell:
                    raise _InvalidRtf("incomplete table row")
                rows.append(current_row[:])
                current_row.clear()
                in_table = False
                visible.append("\n")
            elif value in {b"par", b"line", b"tab"}:
                add_text("\t" if value == b"tab" else "\n")
    if stack or not root_closed or pending_fallback or in_table or current_row or current_cell:
        raise _InvalidRtf("incomplete RTF document")
    if any(len(chunks) != 1 or index not in png_markers for index, chunks in enumerate(pictures)):
        raise _InvalidRtf("unsupported picture encoding")
    return (
        layout.hexdigest(), resources.hexdigest(), "".join(visible),
        [chunks[0] for chunks in pictures], _static_text_fingerprint(rows, "".join(outside_text)),
        rows[2][0] if len(rows) > 2 and rows[2] else "",
    )


def _validate_png(content: bytes, expected_size: tuple[int, int]) -> None:
    """Validate complete original PNG bytes, bounded by the audited dimensions."""
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise _InvalidRtf("not a PNG")
    position, found_header, found_data, found_end = 8, False, False, False
    while position < len(content):
        if len(content) - position < 12:
            raise _InvalidRtf("truncated PNG chunk")
        length = struct.unpack_from(">I", content, position)[0]
        end = position + length + 12
        if end > len(content):
            raise _InvalidRtf("truncated PNG payload")
        kind = content[position + 4:position + 8]
        payload = content[position + 8:end - 4]
        crc = struct.unpack_from(">I", content, end - 4)[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != crc:
            raise _InvalidRtf("PNG checksum mismatch")
        if not found_header:
            if kind != b"IHDR" or length != 13 or struct.unpack_from(">II", payload) != expected_size:
                raise _InvalidRtf("unexpected PNG dimensions")
            found_header = True
        elif kind == b"IHDR":
            raise _InvalidRtf("duplicate PNG header")
        if kind == b"IDAT":
            found_data = True
        if kind == b"IEND":
            if length or end != len(content):
                raise _InvalidRtf("invalid PNG ending")
            found_end = True
        position = end
    if not (found_header and found_data and found_end):
        raise _InvalidRtf("incomplete PNG")
    with Image.open(io.BytesIO(content)) as image:
        if image.format != "PNG" or image.size != expected_size:
            raise _InvalidRtf("unexpected image")
        image.load()


def inspect_native_exam_analysis_source(content: bytes) -> dict[str, bytes | str]:
    """Return untouched media and the exact text of the invisible source cell.

    Callers must first obtain ``content`` from a hydrated NativeAcademicSource.
    This function does not read arbitrary paths, modify sources or invoke any
    converter. Unknown layouts stop export rather than being reconstructed.
    """
    try:
        layout, resources, visible, pictures, static_text, hidden_text = _inspect_rtf(content)
        if (layout != _EXPECTED_LAYOUT_SHA256 or resources != _EXPECTED_RESOURCE_SHA256
                or static_text != _EXPECTED_STATIC_TEXT_SHA256):
            raise _InvalidRtf("unrecognized source layout")
        compact_text = "".join(visible.split())
        if any(label not in compact_text for label in _REQUIRED_LABELS):
            raise _InvalidRtf("missing fixed labels")
        if len(pictures) != len(_EXPECTED_IMAGE_SIZES):
            raise _InvalidRtf("unexpected picture count")
        for picture, size in zip(pictures, _EXPECTED_IMAGE_SIZES):
            _validate_png(picture, size)
    except (ValueError, TypeError, OSError, SyntaxError, struct.error, zlib.error, Image.DecompressionBombError) as exc:
        raise NativeAcademicSourceError(_LAYOUT_ERROR) from exc
    return {
        "vertical_label_png": pictures[0],
        "chart_png": pictures[1],
        "analysis_label_png": pictures[2],
        "hidden_text": hidden_text,
    }
