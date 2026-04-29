# ==============================================================================
# 文档文本+图片提取模块 (ai_assistant_doc_extract.py)
# ==============================================================================
# 从各类文档格式中提取文本和嵌入图片，供 AI 批改服务使用。
# 仅依赖 Python 标准库和已安装的包 (openpyxl, xlrd)。

import base64
import mimetypes
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

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

# --- 支持的文档扩展名 ---
EXTRACTABLE_EXTENSIONS = frozenset({".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".pdf"})

# --- 图片扩展名（用于从文档中识别图片文件）---
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp", ".emf", ".wmf"})


@dataclass
class ExtractResult:
    """文档提取结果。"""
    text: str = ""
    truncated: bool = False
    # 每个元素: {"filename": "image1.png", "data_url": "data:image/png;base64,..."}
    images: list[dict[str, str]] = field(default_factory=list)

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
        return extractor(file_path, max_bytes)
    except Exception as exc:
        print(f"[DOC_EXTRACT] 提取 {file_path.name} 失败: {exc}")
        return ExtractResult()


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------
def _extract_from_docx(file_path: Path, max_bytes: int) -> ExtractResult:
    """从 DOCX (ZIP + XML) 中提取段落文本和嵌入图片。"""
    text_parts: list[str] = []
    images: list[dict[str, str]] = []

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
        images = _extract_images_from_zip(zf, "word/media/")

    text, truncated = _truncate_text("\n".join(text_parts), max_bytes)
    return ExtractResult(text=text, truncated=truncated, images=images)


# ---------------------------------------------------------------------------
# PPTX
# ---------------------------------------------------------------------------
def _extract_from_pptx(file_path: Path, max_bytes: int) -> ExtractResult:
    """从 PPTX (ZIP + XML) 中提取幻灯片文本和嵌入图片。"""
    text_parts: list[str] = []
    images: list[dict[str, str]] = []

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
        images = _extract_images_from_zip(zf, "ppt/media/")

    text, truncated = _truncate_text("\n\n".join(text_parts), max_bytes)
    return ExtractResult(text=text, truncated=truncated, images=images)


# ---------------------------------------------------------------------------
# XLSX (openpyxl)
# ---------------------------------------------------------------------------
def _extract_from_xlsx(file_path: Path, max_bytes: int) -> ExtractResult:
    """从 XLSX 中提取单元格文本（使用 openpyxl）。XLSX 的图片暂不提取。"""
    try:
        import openpyxl
    except ImportError:
        text, truncated = _extract_text_fallback_binary(file_path, max_bytes)
        return ExtractResult(text=text, truncated=truncated)

    parts: list[str] = []
    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
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
    return ExtractResult(text=text, truncated=truncated)


# ---------------------------------------------------------------------------
# XLS (xlrd)
# ---------------------------------------------------------------------------
def _extract_from_xls(file_path: Path, max_bytes: int) -> ExtractResult:
    """从 XLS 中提取单元格文本（使用 xlrd）。"""
    try:
        import xlrd
    except ImportError:
        text, truncated = _extract_text_fallback_binary(file_path, max_bytes)
        return ExtractResult(text=text, truncated=truncated)

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
    return ExtractResult(text=text, truncated=truncated)


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
    return ExtractResult(text=text, truncated=truncated)


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
def _extract_images_from_zip(
    zf: zipfile.ZipFile,
    media_prefix: str,
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
        if len(images) >= MAX_EXTRACTED_IMAGES_PER_DOC:
            break

        ext = Path(name).suffix.lower()
        if ext not in _IMAGE_EXTENSIONS:
            continue

        try:
            data = zf.read(name)
        except Exception:
            continue

        if len(data) > MAX_EXTRACTED_IMAGE_BYTES:
            continue
        if len(data) == 0:
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
