# ==============================================================================
# 文档文本+图片提取模块 (ai_assistant_doc_extract.py)
# ==============================================================================
# 从各类文档格式中提取文本和嵌入图片，供 AI 批改服务使用。
# 仅依赖 Python 标准库和已安装的包 (openpyxl, xlrd)。

import base64
import mimetypes
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None


# --- XML 命名空间常量 ---
_NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_TAG_WT = f"{{{_NS_W}}}t"
_TAG_WP = f"{{{_NS_W}}}p"
_TAG_AT = f"{{{_NS_A}}}t"

# --- 提取限制 ---
MAX_EXTRACTED_IMAGES_PER_DOC = 10
MAX_EXTRACTED_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_OOXML_ENTRIES = 2048
MAX_OOXML_EXPANDED_BYTES = 64 * 1024 * 1024

# --- 支持的文档扩展名 ---
EXTRACTABLE_EXTENSIONS = frozenset({".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".pdf"})

# --- 图片扩展名（用于从文档中识别图片文件）---
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp"})
_VECTOR_IMAGE_EXTENSIONS = frozenset({".svg", ".emf", ".wmf"})


@dataclass
class ExtractResult:
    """文档提取结果。"""
    text: str = ""
    truncated: bool = False
    # 每个元素: {"filename": "image1.png", "data_url": "data:image/png;base64,..."}
    images: list[dict[str, str]] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def has_images(self) -> bool:
        return bool(self.images)


def extract_document_text(
    file_path: Path,
    ext: str,
    max_bytes: int = 2 * 1024 * 1024,
) -> ExtractResult:
    """从文档中提取文本和嵌入图片。

    Args:
        file_path: 文件路径
        ext: 文件扩展名（小写，带点号）
        max_bytes: 最大提取文本字节数

    Returns:
        ExtractResult 包含文本、截断标志和嵌入图片列表
    """
    ext = ext.lower()
    if not isinstance(file_path, Path):
        file_path = Path(file_path)

    dispatch = {
        ".docx": _extract_from_docx,
        ".pptx": _extract_from_pptx,
        ".xlsx": _extract_from_xlsx,
        ".xls": _extract_from_xls,
        ".doc": _extract_from_doc,
        ".pdf": _extract_from_pdf,
    }
    extractor = dispatch.get(ext)
    if extractor is None:
        return ExtractResult()

    try:
        if ext in {".docx", ".pptx", ".xlsx"} or (ext in {".doc", ".xls"} and zipfile.is_zipfile(file_path)):
            with zipfile.ZipFile(file_path, "r") as archive:
                entries = archive.infolist()
                if len(entries) > MAX_OOXML_ENTRIES or sum(item.file_size for item in entries) > MAX_OOXML_EXPANDED_BYTES:
                    return ExtractResult(issues=["文档解压内容超过安全提取上限，请拆分文件后重试"])
        return extractor(file_path, max_bytes)
    except Exception as exc:
        print(f"[DOC_EXTRACT] 提取 {file_path.name} 失败: {exc}")
        return ExtractResult(issues=["文档结构无法完整解析，请转换为 PDF 后重试"])


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------
def _extract_from_docx(file_path: Path, max_bytes: int) -> ExtractResult:
    """从 DOCX (ZIP + XML) 中提取段落文本和嵌入图片。"""
    text_parts: list[str] = []
    images: list[dict[str, str]] = []
    issues: list[str] = []

    with zipfile.ZipFile(file_path, "r") as zf:
        namelist = zf.namelist()

        # 提取文本
        if "word/document.xml" in namelist:
            with zf.open("word/document.xml") as xml_file:
                tree = ET.parse(xml_file)
            for para in tree.getroot().iter(_TAG_WP):
                texts = [t.text for t in para.iter(_TAG_WT) if t.text]
                line = "".join(texts)
                if line.strip():
                    text_parts.append(line)

        # 提取嵌入图片 (word/media/)
        images = _extract_images_from_zip(zf, "word/media/", issues=issues)
        _inspect_zip_visual_completeness(zf, "word/", issues)

    text, truncated = _truncate_text("\n".join(text_parts), max_bytes)
    return ExtractResult(text=text, truncated=truncated, images=images, issues=issues)


# ---------------------------------------------------------------------------
# PPTX
# ---------------------------------------------------------------------------
def _extract_from_pptx(file_path: Path, max_bytes: int) -> ExtractResult:
    """从 PPTX (ZIP + XML) 中提取幻灯片文本和嵌入图片。"""
    text_parts: list[str] = []
    images: list[dict[str, str]] = []
    issues: list[str] = []

    with zipfile.ZipFile(file_path, "r") as zf:
        namelist = zf.namelist()

        # 提取文本
        slide_names = sorted(
            [n for n in namelist if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)],
            key=lambda n: int(re.search(r"slide(\d+)", n).group(1)),
        )
        for name in slide_names:
            with zf.open(name) as xml_file:
                tree = ET.parse(xml_file)
            texts = [t.text for t in tree.getroot().iter(_TAG_AT) if t.text]
            if texts:
                slide_num = re.search(r"slide(\d+)", name).group(1)
                text_parts.append(f"[幻灯片 {slide_num}]\n" + "\n".join(texts))

        # 提取嵌入图片 (ppt/media/)
        images = _extract_images_from_zip(zf, "ppt/media/", issues=issues)
        _inspect_zip_visual_completeness(zf, "ppt/", issues)

    text, truncated = _truncate_text("\n\n".join(text_parts), max_bytes)
    return ExtractResult(text=text, truncated=truncated, images=images, issues=issues)


# ---------------------------------------------------------------------------
# XLSX (openpyxl)
# ---------------------------------------------------------------------------
def _extract_from_xlsx(file_path: Path, max_bytes: int) -> ExtractResult:
    """提取 XLSX 单元格与嵌图，并显式报告无法还原的图表/绘图。"""
    issues: list[str] = []
    with zipfile.ZipFile(file_path, "r") as zf:
        images = _extract_images_from_zip(zf, "xl/media/", issues=issues)
        _inspect_zip_visual_completeness(zf, "xl/", issues)
    try:
        import openpyxl
    except ImportError:
        issues.append("表格读取组件不可用，无法完整读取单元格，请转换为 PDF 后重试")
        return ExtractResult(images=images, issues=issues)

    parts: list[str] = []
    # A file stream also supports real XLSX files carrying a legacy .xls name.
    with file_path.open("rb") as source:
        wb = openpyxl.load_workbook(source, read_only=True, data_only=True)
        try:
            for sheet in wb.worksheets:
                rows: list[str] = []
                for row in sheet.iter_rows(values_only=True):
                    cells = [str(c).strip() if c is not None else "" for c in row]
                    if any(cells):
                        rows.append("\t".join(cells))
                if rows:
                    parts.append(f"[工作表: {sheet.title}]\n" + "\n".join(rows))
        finally:
            wb.close()

    text, truncated = _truncate_text("\n\n".join(parts), max_bytes)
    return ExtractResult(text=text, truncated=truncated, images=images, issues=issues)


# ---------------------------------------------------------------------------
# XLS (xlrd)
# ---------------------------------------------------------------------------
def _extract_from_xls(file_path: Path, max_bytes: int) -> ExtractResult:
    """从 XLS 中提取单元格文本（使用 xlrd）。"""
    if zipfile.is_zipfile(file_path):
        with zipfile.ZipFile(file_path, "r") as archive:
            if "xl/workbook.xml" in archive.namelist():
                return _extract_from_xlsx(file_path, max_bytes)
    issues = ["旧版 XLS 无法核验嵌入图片完整性，AI 使用前请另存为 XLSX 或 PDF"]
    try:
        import xlrd
    except ImportError:
        text, truncated = _extract_text_fallback_binary(file_path, max_bytes)
        return ExtractResult(text=text, truncated=truncated, issues=issues)

    parts: list[str] = []
    wb = xlrd.open_workbook(file_path)
    for sheet in wb.sheets():
        rows: list[str] = []
        for row_idx in range(sheet.nrows):
            cells = [
                str(sheet.cell_value(row_idx, col_idx)).strip()
                for col_idx in range(sheet.ncols)
            ]
            if any(c for c in cells):
                rows.append("\t".join(cells))
        if rows:
            parts.append(f"[工作表: {sheet.name}]\n" + "\n".join(rows))

    text, truncated = _truncate_text("\n\n".join(parts), max_bytes)
    return ExtractResult(text=text, truncated=truncated, issues=issues)


# ---------------------------------------------------------------------------
# DOC (旧版二进制格式)
# ---------------------------------------------------------------------------
def _extract_from_doc(file_path: Path, max_bytes: int) -> ExtractResult:
    """从旧版 .doc 二进制文件中尽量提取可读文本。"""
    # 方法 1: 尝试用 zipfile 打开（某些 .doc 实际上是 .docx）
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            if "word/document.xml" in zf.namelist():
                return _extract_from_docx(file_path, max_bytes)
    except zipfile.BadZipFile:
        pass

    # 方法 2: 二进制扫描提取可读文本
    text, truncated = _extract_text_fallback_binary(file_path, max_bytes)
    return ExtractResult(text=text, truncated=truncated,
                         issues=["旧版 DOC 无法核验嵌入图片完整性，AI 使用前请另存为 DOCX 或 PDF"])


# ---------------------------------------------------------------------------
# PDF (PyMuPDF)
# ---------------------------------------------------------------------------
def _extract_from_pdf(file_path: Path, max_bytes: int) -> ExtractResult:
    """从 PDF 中提取文本和嵌入图片（使用 PyMuPDF）。"""
    if fitz is None:
        return ExtractResult()

    try:
        doc = fitz.open(str(file_path))
    except Exception:
        return ExtractResult()

    text_parts: list[str] = []
    images: list[dict[str, str]] = []

    try:
        max_pages = min(len(doc), 50)
        for page_num in range(max_pages):
            page = doc[page_num]
            text = page.get_text("text").strip()
            if text:
                text_parts.append(f"[第 {page_num + 1} 页]\n{text}")

            # 提取嵌入图片
            image_list = page.get_images(full=True)
            for img_info in image_list:
                if len(images) >= MAX_EXTRACTED_IMAGES_PER_DOC:
                    break
                xref = img_info[0]
                try:
                    base_image = doc.extract_image(xref)
                    if not base_image or not base_image.get("image"):
                        continue
                    img_data = base_image["image"]
                    if not (0 < len(img_data) <= MAX_EXTRACTED_IMAGE_BYTES):
                        continue
                    img_ext = base_image.get("ext", "png")
                    mime = f"image/{img_ext}" if img_ext != "jpg" else "image/jpeg"
                    b64 = base64.b64encode(img_data).decode("utf-8")
                    images.append({
                        "filename": f"page{page_num + 1}_img{len(images) + 1}.{img_ext}",
                        "data_url": f"data:{mime};base64,{b64}",
                    })
                except Exception:
                    continue
    finally:
        doc.close()

    text, truncated = _truncate_text("\n\n".join(text_parts), max_bytes)
    return ExtractResult(text=text, truncated=truncated, images=images)


def render_pdf_pages_to_data_urls(
    file_path: Path,
    dpi: int = 150,
    max_pages: int = 10,
) -> list[dict[str, str]]:
    """将 PDF 页面渲染为 PNG data URL，供视觉能力的 AI 模型使用。

    Args:
        file_path: PDF 文件路径
        dpi: 渲染分辨率（默认150）
        max_pages: 最大渲染页数

    Returns:
        列表，每项包含 filename 和 data_url
    """
    if fitz is None:
        return []

    try:
        doc = fitz.open(str(file_path))
    except Exception:
        return []

    results: list[dict[str, str]] = []
    try:
        for page_num in range(min(len(doc), max_pages)):
            page = doc[page_num]
            zoom = dpi / 72
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            try:
                png_bytes = pix.tobytes("png")
            finally:
                pix = None  # 释放像素内存

            if len(png_bytes) > MAX_EXTRACTED_IMAGE_BYTES:
                continue

            b64 = base64.b64encode(png_bytes).decode("utf-8")
            results.append({
                "filename": f"page_{page_num + 1}.png",
                "data_url": f"data:image/png;base64,{b64}",
            })
    finally:
        doc.close()

    return results


# ---------------------------------------------------------------------------
# 通用辅助函数
# ---------------------------------------------------------------------------
def _inspect_zip_visual_completeness(
    zf: zipfile.ZipFile, document_prefix: str, issues: list[str],
) -> None:
    """Detect OOXML visuals that a text-plus-raster manifest cannot represent.

    No relationship is fetched remotely. Plain text, ordinary spreadsheet
    cells and bitmap picture geometry remain supported. The caller decides
    whether to render the document or reject this incomplete extraction.
    """
    names = set(zf.namelist())

    def issue(message: str) -> None:
        if message not in issues:
            issues.append(message)

    for name in sorted(names):
        if not name.startswith(document_prefix):
            continue
        if name.endswith(".rels"):
            try:
                root = ET.fromstring(zf.read(name))
            except (ET.ParseError, KeyError, OSError):
                issue("文档资源关系无法完整解析，请转换为 PDF 后重试")
                continue
            for relation in root:
                kind = str(relation.get("Type") or "").rsplit("/", 1)[-1]
                if kind in {"chart", "chartEx", "diagramData", "diagramLayout", "diagramQuickStyle", "diagramColors"}:
                    issue("文档包含图表或 SmartArt，尚不能完整还原，请转换为 PDF 后重试")
                if kind != "image":
                    continue
                target = unquote(str(relation.get("Target") or "").split("#", 1)[0])
                if relation.get("TargetMode", "").lower() == "external":
                    issue("文档包含外部链接图片，未读取该图片，请嵌入图片或转换为 PDF 后重试")
                    continue
                base = name.split("_rels/", 1)[0]
                resolved = posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join(base, target))
                if resolved not in names:
                    issue("文档引用的图片文件缺失，请修复文件或转换为 PDF 后重试")
                elif Path(resolved).suffix.lower() not in _IMAGE_EXTENSIONS:
                    issue("文档包含 SVG 或其他不支持的矢量图片，请转换为 PDF 后重试")
                elif not resolved.startswith(f"{document_prefix}media/"):
                    issue("文档图片存储位置无法完整解析，请转换为 PDF 后重试")
            continue
        if not name.endswith((".xml", ".vml")) or "/media/" in name:
            continue
        try:
            root = ET.fromstring(zf.read(name))
        except (ET.ParseError, KeyError, OSError):
            issue("文档内容结构无法完整解析，请转换为 PDF 后重试")
            continue
        parents = {child: parent for parent in root.iter() for child in parent}
        for element in root.iter():
            namespace, _, local = element.tag.rpartition("}")
            if "/drawingml/2006/chart" in namespace or "/drawingml/2006/diagram" in namespace:
                issue("文档包含图表或 SmartArt，尚不能完整还原，请转换为 PDF 后重试")
            if "/officeDocument/2006/math" in namespace and local in {"oMath", "oMathPara"}:
                issue("文档包含公式排版，尚不能完整还原，请转换为 PDF 后重试")
            if local in {"oleObject", "object", "control"} and namespace:
                issue("文档包含嵌入对象，尚不能完整还原，请转换为 PDF 后重试")
            if local not in {"prstGeom", "custGeom", "cxnSp", "shape", "rect", "oval", "line", "polyline", "curve", "group"}:
                continue
            # VML and DrawingML shapes carry visible geometry absent from text.
            if not ("drawingml" in namespace or "urn:schemas-microsoft-com:vml" in namespace):
                continue
            ancestry = [element]
            parent = parents.get(element)
            while parent is not None:
                ancestry.append(parent)
                parent = parents.get(parent)
            if any(node.tag.rsplit("}", 1)[-1] == "pic" for node in ancestry):
                continue
            shape = next((node for node in ancestry if node.tag.rsplit("}", 1)[-1] in {"sp", "shape"}), element)
            # A normal text box is represented by its extracted text; picture
            # frames are represented by the referenced bitmap, not a new shape.
            if any(node.get("txBox") in {"1", "true"} for node in shape.iter()):
                continue
            if element.get("prst") == "rect" and any(node.tag.rsplit("}", 1)[-1] == "ph" for node in shape.iter()):
                continue
            if "urn:schemas-microsoft-com:vml" in namespace and any(
                node.tag.rsplit("}", 1)[-1] in {"imagedata", "ClientData"} for node in shape.iter()
            ):
                continue
            issue("文档包含矢量绘图，尚不能完整还原，请转换为 PDF 后重试")


def _extract_images_from_zip(
    zf: zipfile.ZipFile,
    media_prefix: str,
    *, issues: list[str] | None = None,
) -> list[dict[str, str]]:
    """从 ZIP 文档的媒体目录中提取图片并转为 base64 data URL。

    Args:
        zf: 已打开的 ZipFile
        media_prefix: 媒体目录前缀，如 "word/media/" 或 "ppt/media/"

    Returns:
        图片列表，每项包含 filename 和 data_url
    """
    images: list[dict[str, str]] = []

    media_files = sorted(
        [n for n in zf.namelist() if n.startswith(media_prefix)],
        key=lambda n: n,
    )

    for name in media_files:
        if name.endswith("/"):
            continue
        ext = Path(name).suffix.lower()
        if ext not in _IMAGE_EXTENSIONS:
            if issues is not None and ext in _VECTOR_IMAGE_EXTENSIONS:
                message = "文档包含 SVG 或其他不支持的矢量图片，请转换为 PDF 后重试"
                if message not in issues:
                    issues.append(message)
            continue
        if len(images) >= MAX_EXTRACTED_IMAGES_PER_DOC:
            if issues is not None:
                issues.append("文档图片超过提取数量上限")
            break

        try:
            if zf.getinfo(name).file_size > MAX_EXTRACTED_IMAGE_BYTES:
                if issues is not None:
                    issues.append("文档图片超过大小上限")
                continue
            data = zf.read(name)
        except Exception:
            if issues is not None:
                issues.append("文档图片无法读取")
            continue

        if len(data) > MAX_EXTRACTED_IMAGE_BYTES:
            continue
        if len(data) == 0:
            if issues is not None:
                issues.append("文档含空图片")
            continue

        mime = mimetypes.guess_type(name)[0] or "image/png"
        b64 = base64.b64encode(data).decode("utf-8")
        images.append({
            "filename": Path(name).name,
            "data_url": f"data:{mime};base64,{b64}",
        })

    return images


def _extract_text_fallback_binary(file_path: Path, max_bytes: int) -> tuple[str, bool]:
    """从二进制文件中提取可打印文本（最后手段）。"""
    raw = file_path.read_bytes()
    text = raw.decode("utf-8", errors="ignore")
    cleaned = "".join(
        ch for ch in text
        if ch.isprintable() or ch in {"\n", "\r", "\t"}
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    lines = [line.strip() for line in cleaned.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    if len(cleaned) < 10:
        return "", False
    return _truncate_text(cleaned, max_bytes)


def _truncate_text(text: str, max_bytes: int) -> tuple[str, bool]:
    """按字节截断文本，返回 (文本, 是否被截断)。"""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    truncated_bytes = encoded[:max_bytes]
    return truncated_bytes.decode("utf-8", errors="ignore"), True
