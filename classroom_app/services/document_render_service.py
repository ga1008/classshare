"""Shared document preview renderer.

The renderer accepts the final downloadable document bytes (DOCX, XLSX, or
PDF), stores them in a content-addressed cache, converts Office files to PDF
when needed, and renders page PNGs from that same final artifact. It gives
preview pages and downloads one shared source of truth while keeping expensive
LibreOffice/PyMuPDF work behind a small process-local queue.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import mimetypes
import os
import shutil
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ..config import SECRET_KEY
from ..storage_paths import DATA_ROOT
from .libreoffice_service import convert_office_file


DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF_MEDIA_TYPE = "application/pdf"
PNG_MEDIA_TYPE = "image/png"

SUPPORTED_FORMATS = {"doc", "docx", "xls", "xlsx", "pdf"}
RENDER_VERSION = "document-renderer-v1"


class DocumentRenderError(RuntimeError):
    """Base class for document renderer failures."""


class DocumentRenderNotFound(DocumentRenderError):
    """Raised when a cached render job cannot be found."""


class DocumentRenderQueueBusy(DocumentRenderError):
    """Raised when all renderer slots stay busy beyond the queue timeout."""


@dataclass(frozen=True)
class RenderedDocumentJob:
    key: str
    manifest: dict[str, Any]
    root: Path

    @property
    def page_count(self) -> int:
        return int(self.manifest.get("page_count") or 0)

    @property
    def filename(self) -> str:
        return str(self.manifest.get("filename") or "document")

    @property
    def media_type(self) -> str:
        return str(self.manifest.get("media_type") or "application/octet-stream")


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _safe_filename(value: str | None, fallback: str = "document") -> str:
    raw = str(value or "").strip() or fallback
    raw = raw.replace("\\", "_").replace("/", "_").replace("\x00", "")
    return raw[:160] or fallback


def _infer_format(filename: str | None, media_type: str | None, explicit: str | None = None) -> str:
    if explicit:
        normalized = str(explicit).strip().lower().lstrip(".")
        if normalized in SUPPORTED_FORMATS:
            return normalized
    suffix = Path(str(filename or "")).suffix.lower().lstrip(".")
    if suffix in SUPPORTED_FORMATS:
        return suffix
    media = str(media_type or "").lower()
    if "pdf" in media:
        return "pdf"
    if "spreadsheet" in media or "excel" in media:
        return "xlsx"
    if "word" in media or "document" in media:
        return "docx"
    return suffix or "docx"


def _media_type_for_format(source_format: str, filename: str | None = None) -> str:
    normalized = str(source_format or "").lower().lstrip(".")
    if normalized == "pdf":
        return PDF_MEDIA_TYPE
    if normalized in {"xls", "xlsx"}:
        return XLSX_MEDIA_TYPE
    if normalized in {"doc", "docx"}:
        return DOCX_MEDIA_TYPE
    guessed = mimetypes.guess_type(filename or "")[0]
    return guessed or "application/octet-stream"


def _content_key(content: bytes, source_format: str) -> str:
    digest = hashlib.sha256()
    digest.update(RENDER_VERSION.encode("utf-8"))
    digest.update(b"\0")
    digest.update(str(source_format or "").lower().encode("utf-8"))
    digest.update(b"\0")
    digest.update(content)
    return digest.hexdigest()


def sign_render_key(key: str) -> str:
    payload = f"document-render:{key}".encode("utf-8")
    return hmac.new(str(SECRET_KEY).encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_render_token(key: str, token: str | None) -> bool:
    expected = sign_render_key(key)
    return hmac.compare_digest(expected, str(token or ""))


class DocumentRenderService:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or DATA_ROOT / "tmp" / "document_renderer")
        self.max_concurrent = _env_int("LANSHARE_DOCUMENT_RENDER_MAX_CONCURRENCY", 1)
        self.queue_timeout_seconds = _env_int("LANSHARE_DOCUMENT_RENDER_QUEUE_TIMEOUT_SECONDS", 45)
        self.ttl_seconds = _env_int("LANSHARE_DOCUMENT_RENDER_TTL_SECONDS", 60 * 60 * 24)
        self.max_pages = _env_int("LANSHARE_DOCUMENT_RENDER_MAX_PAGES", 80)
        self.medium_zoom = float(os.getenv("LANSHARE_DOCUMENT_RENDER_MEDIUM_ZOOM", "1.45") or 1.45)
        self.large_zoom = float(os.getenv("LANSHARE_DOCUMENT_RENDER_LARGE_ZOOM", "2.35") or 2.35)
        self._semaphore = threading.BoundedSemaphore(self.max_concurrent)
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._cleanup_lock = threading.Lock()
        self._last_cleanup_at = 0.0

    def render_artifact(
        self,
        content: bytes,
        *,
        filename: str,
        media_type: str | None = None,
        source_format: str | None = None,
    ) -> RenderedDocumentJob:
        if not content:
            raise DocumentRenderError("文档内容为空，无法生成预览。")
        normalized_format = _infer_format(filename, media_type, source_format)
        if normalized_format not in SUPPORTED_FORMATS:
            raise DocumentRenderError(f"暂不支持 {normalized_format or '未知'} 文档预览。")

        self.cleanup_expired_maybe()
        key = _content_key(content, normalized_format)
        job_root = self._job_root(key)
        lock = self._key_lock(key)
        with lock:
            cached = self._load_ready_job(key)
            if cached:
                return cached

            with self._renderer_slot():
                cached = self._load_ready_job(key)
                if cached:
                    return cached
                return self._build_job(
                    key,
                    content,
                    filename=_safe_filename(filename),
                    media_type=media_type or _media_type_for_format(normalized_format, filename),
                    source_format=normalized_format,
                    job_root=job_root,
                )

    def get_job(self, key: str) -> RenderedDocumentJob:
        job = self._load_ready_job(key)
        if not job:
            raise DocumentRenderNotFound("预览缓存已过期，请刷新预览页面重新生成。")
        return job

    def get_download_path(self, key: str) -> tuple[Path, str, str]:
        job = self.get_job(key)
        document_name = str(job.manifest.get("document_file") or "")
        document_path = job.root / document_name
        if not document_name or not document_path.exists():
            raise DocumentRenderNotFound("预览文档缓存已过期，请刷新后重新导出。")
        self._touch_manifest(job)
        return document_path, job.filename, job.media_type

    def get_page_image_path(self, key: str, page_number: int, *, size: str = "medium") -> Path:
        job = self.get_job(key)
        page_count = job.page_count
        if page_number < 1 or page_number > page_count:
            raise DocumentRenderNotFound("预览页不存在。")
        normalized_size = "large" if size == "large" else "medium"
        page_path = self._page_path(job.root, page_number, normalized_size)
        if page_path.exists():
            self._touch_manifest(job)
            return page_path

        lock = self._key_lock(key)
        with lock:
            if page_path.exists():
                self._touch_manifest(job)
                return page_path
            if normalized_size != "large":
                raise DocumentRenderNotFound("预览页缓存已过期，请刷新预览页面。")
            with self._renderer_slot():
                pdf_file = str(job.manifest.get("pdf_file") or "document.pdf")
                pdf_path = job.root / pdf_file
                if not pdf_path.exists():
                    raise DocumentRenderNotFound("PDF 中间文件缓存已过期，请刷新预览页面。")
                self._render_pdf_pages(pdf_path, job.root, zoom=self.large_zoom, size_name="large", page_number=page_number)
                self._touch_manifest(job)
                return page_path

    def render_preview_html(
        self,
        job: RenderedDocumentJob,
        *,
        title: str,
        eyebrow: str = "文档真实预览",
        download_label: str = "下载文件",
    ) -> str:
        token = sign_render_key(job.key)
        page_payload = []
        for page_number in range(1, job.page_count + 1):
            base = f"/api/document-renderer/jobs/{quote(job.key)}/pages/{page_number}?token={quote(token)}"
            page_payload.append(
                {
                    "number": page_number,
                    "mediumUrl": f"{base}&size=medium",
                    "largeUrl": f"{base}&size=large",
                }
            )
        download_url = f"/api/document-renderer/jobs/{quote(job.key)}/download?token={quote(token)}"
        escaped_title = html.escape(title or job.filename)
        escaped_eyebrow = html.escape(eyebrow)
        escaped_download_label = html.escape(download_label)
        pages_json = json.dumps(page_payload, ensure_ascii=False)
        page_cards = "\n".join(
            (
                f"<button class=\"doc-preview-card\" type=\"button\" data-page-index=\"{page['number'] - 1}\" "
                f"aria-label=\"查看第 {page['number']} 页大图\">"
                f"<span class=\"doc-preview-card__paper\"><img src=\"{html.escape(page['mediumUrl'])}\" "
                f"alt=\"第 {page['number']} 页预览图\" loading=\"lazy\"></span>"
                f"<span class=\"doc-preview-card__meta\"><strong>{page['number']}</strong><em>/ {job.page_count}</em></span>"
                "</button>"
            )
            for page in page_payload
        )
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escaped_title} · 预览</title>
<style>
  :root {{
    color-scheme: light;
    --ink: #172033;
    --muted: #667085;
    --line: rgba(117, 129, 149, 0.20);
    --paper: #ffffff;
    --surface: #f6f8fb;
    --teal: #0f766e;
    --sky: #0ea5e9;
    --gold: #b7791f;
    --shadow: 0 26px 80px rgba(22, 32, 51, 0.16);
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ min-height: 100%; margin: 0; }}
  body {{
    font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
    color: var(--ink);
    background:
      linear-gradient(120deg, rgba(14, 165, 233, 0.10), rgba(15, 118, 110, 0.04) 42%, rgba(183, 121, 31, 0.08)),
      var(--surface);
  }}
  .doc-preview-shell {{ min-height: 100vh; display: flex; flex-direction: column; }}
  .doc-preview-topbar {{
    position: sticky;
    top: 0;
    z-index: 5;
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 16px;
    align-items: center;
    padding: 14px clamp(16px, 4vw, 34px);
    border-bottom: 1px solid var(--line);
    background: rgba(255, 255, 255, 0.88);
    backdrop-filter: blur(16px);
  }}
  .doc-preview-title {{ min-width: 0; display: grid; gap: 4px; }}
  .doc-preview-title span {{ color: var(--teal); font-size: 0.78rem; font-weight: 800; letter-spacing: 0.08em; }}
  .doc-preview-title strong {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 1rem; }}
  .doc-preview-actions {{ display: flex; align-items: center; gap: 8px; }}
  .doc-preview-pill, .doc-preview-download {{
    min-height: 36px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0 12px;
    border-radius: 999px;
    font-size: 0.84rem;
    font-weight: 750;
    text-decoration: none;
    white-space: nowrap;
  }}
  .doc-preview-pill {{ border: 1px solid rgba(14, 165, 233, 0.20); color: #075985; background: rgba(240, 249, 255, 0.9); }}
  .doc-preview-download {{ border: 1px solid transparent; color: #fff; background: linear-gradient(135deg, var(--teal), #115e59); }}
  .doc-preview-stage {{
    flex: 1;
    width: min(1380px, calc(100vw - 28px));
    margin: 0 auto;
    padding: clamp(18px, 3vw, 34px) 0 42px;
  }}
  .doc-preview-pages {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: clamp(16px, 2.5vw, 26px);
    perspective: 1600px;
  }}
  .doc-preview-card {{
    position: relative;
    min-width: 0;
    padding: 0;
    border: 0;
    background: transparent;
    cursor: zoom-in;
    transform-style: preserve-3d;
  }}
  .doc-preview-card__paper {{
    display: block;
    overflow: hidden;
    border-radius: 8px;
    border: 1px solid rgba(117, 129, 149, 0.22);
    background: #fff;
    box-shadow: 0 18px 38px rgba(22, 32, 51, 0.14);
    transform: perspective(1400px) rotateX(2.2deg) rotateY(-1.8deg) translateY(0);
    transition: transform 180ms ease, box-shadow 180ms ease, border-color 180ms ease;
  }}
  .doc-preview-card:hover .doc-preview-card__paper,
  .doc-preview-card:focus-visible .doc-preview-card__paper {{
    transform: perspective(1400px) rotateX(0.8deg) rotateY(0deg) translateY(-5px);
    border-color: rgba(14, 165, 233, 0.35);
    box-shadow: 0 28px 64px rgba(22, 32, 51, 0.20);
  }}
  .doc-preview-card:focus-visible {{ outline: none; }}
  .doc-preview-card img {{ display: block; width: 100%; height: auto; background: #fff; }}
  .doc-preview-card__meta {{
    position: absolute;
    left: 14px;
    bottom: 14px;
    display: inline-flex;
    align-items: baseline;
    gap: 4px;
    min-height: 30px;
    padding: 0 10px;
    border-radius: 999px;
    border: 1px solid rgba(255, 255, 255, 0.74);
    background: rgba(23, 32, 51, 0.72);
    color: #fff;
    box-shadow: 0 10px 20px rgba(15, 23, 42, 0.18);
  }}
  .doc-preview-card__meta strong {{ font-size: 0.96rem; }}
  .doc-preview-card__meta em {{ font-style: normal; font-size: 0.76rem; opacity: 0.78; }}
  .doc-preview-lightbox[hidden] {{ display: none; }}
  .doc-preview-lightbox {{
    position: fixed;
    inset: 0;
    z-index: 20;
    display: grid;
    grid-template-rows: auto minmax(0, 1fr);
    background: rgba(15, 23, 42, 0.76);
    backdrop-filter: blur(18px);
  }}
  .doc-preview-lightbox__bar {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 12px clamp(14px, 3vw, 28px);
    color: #fff;
  }}
  .doc-preview-lightbox__bar strong {{ min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .doc-preview-lightbox__controls {{ display: flex; gap: 8px; align-items: center; }}
  .doc-preview-icon-btn {{
    width: 38px;
    height: 38px;
    border: 1px solid rgba(255, 255, 255, 0.24);
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.12);
    color: #fff;
    cursor: pointer;
    font-size: 1.15rem;
  }}
  .doc-preview-lightbox__body {{
    position: relative;
    min-height: 0;
    display: grid;
    place-items: center;
    padding: 0 clamp(12px, 3vw, 30px) clamp(18px, 4vw, 34px);
  }}
  .doc-preview-large-image {{
    max-width: min(1180px, 96vw);
    max-height: calc(100vh - 108px);
    width: auto;
    height: auto;
    border-radius: 8px;
    background: #fff;
    box-shadow: var(--shadow);
  }}
  .doc-preview-loading {{
    position: absolute;
    display: grid;
    gap: 10px;
    place-items: center;
    padding: 18px 22px;
    border-radius: 8px;
    background: rgba(255, 255, 255, 0.94);
    color: var(--ink);
    box-shadow: 0 18px 50px rgba(15, 23, 42, 0.20);
  }}
  .doc-preview-loading[hidden] {{ display: none; }}
  .doc-preview-spinner {{
    width: 26px;
    height: 26px;
    border-radius: 999px;
    border: 3px solid rgba(14, 165, 233, 0.18);
    border-top-color: var(--sky);
    animation: docPreviewSpin 0.9s linear infinite;
  }}
  @keyframes docPreviewSpin {{ to {{ transform: rotate(360deg); }} }}
  @media (max-width: 720px) {{
    .doc-preview-topbar {{ grid-template-columns: 1fr; }}
    .doc-preview-actions {{ justify-content: space-between; }}
    .doc-preview-pages {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>
<main class="doc-preview-shell">
  <header class="doc-preview-topbar">
    <div class="doc-preview-title">
      <span>{escaped_eyebrow}</span>
      <strong>{escaped_title}</strong>
    </div>
    <div class="doc-preview-actions">
      <span class="doc-preview-pill">共 {job.page_count} 页</span>
      <a class="doc-preview-download" href="{html.escape(download_url)}">{escaped_download_label}</a>
    </div>
  </header>
  <section class="doc-preview-stage">
    <div class="doc-preview-pages">{page_cards}</div>
  </section>
</main>
<div class="doc-preview-lightbox" data-lightbox hidden>
  <div class="doc-preview-lightbox__bar">
    <strong data-lightbox-title>{escaped_title}</strong>
    <div class="doc-preview-lightbox__controls">
      <button class="doc-preview-icon-btn" type="button" data-prev aria-label="上一页">‹</button>
      <span data-lightbox-count>1 / {job.page_count}</span>
      <button class="doc-preview-icon-btn" type="button" data-next aria-label="下一页">›</button>
      <button class="doc-preview-icon-btn" type="button" data-close aria-label="关闭">×</button>
    </div>
  </div>
  <div class="doc-preview-lightbox__body">
    <img class="doc-preview-large-image" data-large-image alt="高清页面预览">
    <div class="doc-preview-loading" data-loading>
      <span class="doc-preview-spinner"></span>
      <strong>正在生成高清预览...</strong>
    </div>
  </div>
</div>
<script>
(() => {{
  const pages = {pages_json};
  const lightbox = document.querySelector('[data-lightbox]');
  const image = document.querySelector('[data-large-image]');
  const loading = document.querySelector('[data-loading]');
  const counter = document.querySelector('[data-lightbox-count]');
  let activeIndex = 0;

  function clampIndex(index) {{
    if (!pages.length) return 0;
    return (index + pages.length) % pages.length;
  }}

  function openPage(index) {{
    activeIndex = clampIndex(index);
    const page = pages[activeIndex];
    if (!page) return;
    lightbox.hidden = false;
    loading.hidden = false;
    image.removeAttribute('src');
    counter.textContent = page.number + ' / ' + pages.length;
    const loader = new Image();
    loader.onload = () => {{
      image.src = loader.src;
      loading.hidden = true;
    }};
    loader.onerror = () => {{
      loading.querySelector('strong').textContent = '高清预览生成失败，请稍后重试';
    }};
    loader.src = page.largeUrl;
  }}

  document.querySelectorAll('[data-page-index]').forEach((card) => {{
    card.addEventListener('click', () => openPage(Number(card.dataset.pageIndex || 0)));
  }});
  document.querySelector('[data-close]').addEventListener('click', () => {{ lightbox.hidden = true; }});
  document.querySelector('[data-prev]').addEventListener('click', () => openPage(activeIndex - 1));
  document.querySelector('[data-next]').addEventListener('click', () => openPage(activeIndex + 1));
  lightbox.addEventListener('click', (event) => {{
    if (event.target === lightbox) lightbox.hidden = true;
  }});
  window.addEventListener('keydown', (event) => {{
    if (lightbox.hidden) return;
    if (event.key === 'Escape') lightbox.hidden = true;
    if (event.key === 'ArrowLeft') openPage(activeIndex - 1);
    if (event.key === 'ArrowRight') openPage(activeIndex + 1);
  }});
}})();
</script>
</body>
</html>"""

    def render_error_html(self, *, title: str, message: str) -> str:
        return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · 预览失败</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f6f8fb;color:#172033;font-family:"Microsoft YaHei","Segoe UI",sans-serif;}}
section{{width:min(560px,calc(100vw - 32px));padding:26px;border:1px solid rgba(117,129,149,.24);border-radius:8px;background:#fff;box-shadow:0 22px 60px rgba(22,32,51,.12);}}
h1{{margin:0 0 10px;font-size:1.15rem;}}p{{margin:0;color:#667085;line-height:1.7;}}
</style></head><body><section><h1>预览暂时不可用</h1><p>{html.escape(message)}</p></section></body></html>"""

    def cleanup_expired_maybe(self) -> None:
        now = time.time()
        if now - self._last_cleanup_at < 600:
            return
        if not self._cleanup_lock.acquire(blocking=False):
            return
        try:
            self._last_cleanup_at = now
            self.cleanup_expired(now=now)
        finally:
            self._cleanup_lock.release()

    def cleanup_expired(self, *, now: float | None = None) -> None:
        current = now or time.time()
        if not self.root.exists():
            return
        for manifest_path in self.root.glob("*/*/manifest.json"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                last_access = float(manifest.get("last_access_at") or manifest.get("updated_at") or 0)
            except (OSError, ValueError, TypeError):
                last_access = 0
            if current - last_access <= self.ttl_seconds:
                continue
            job_root = manifest_path.parent
            try:
                shutil.rmtree(job_root, ignore_errors=True)
            except OSError:
                pass

    def _build_job(
        self,
        key: str,
        content: bytes,
        *,
        filename: str,
        media_type: str,
        source_format: str,
        job_root: Path,
    ) -> RenderedDocumentJob:
        if job_root.exists():
            shutil.rmtree(job_root, ignore_errors=True)
        job_root.mkdir(parents=True, exist_ok=True)

        document_file = f"document.{source_format}"
        document_path = job_root / document_file
        document_path.write_bytes(content)
        pdf_file = "document.pdf"
        pdf_path = job_root / pdf_file
        if source_format == "pdf":
            pdf_path.write_bytes(content)
        else:
            conversion = convert_office_file(document_path, "pdf", timeout=120)
            pdf_path.write_bytes(conversion.output_bytes)

        page_count = self._render_pdf_pages(pdf_path, job_root, zoom=self.medium_zoom, size_name="medium")
        now = time.time()
        manifest = {
            "version": RENDER_VERSION,
            "key": key,
            "filename": filename,
            "media_type": media_type,
            "source_format": source_format,
            "document_file": document_file,
            "pdf_file": pdf_file,
            "page_count": page_count,
            "created_at": now,
            "updated_at": now,
            "last_access_at": now,
        }
        self._write_manifest(job_root, manifest)
        return RenderedDocumentJob(key=key, manifest=manifest, root=job_root)

    def _render_pdf_pages(
        self,
        pdf_path: Path,
        job_root: Path,
        *,
        zoom: float,
        size_name: str,
        page_number: int | None = None,
    ) -> int:
        try:
            import fitz
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise DocumentRenderError(f"缺少 PDF 渲染依赖 PyMuPDF: {exc}") from exc

        doc = fitz.open(pdf_path)
        try:
            page_count = int(doc.page_count)
            if page_count <= 0:
                raise DocumentRenderError("PDF 没有可渲染页面。")
            if page_count > self.max_pages:
                raise DocumentRenderError(f"文档共有 {page_count} 页，超过当前预览上限 {self.max_pages} 页。")
            matrix = fitz.Matrix(float(zoom), float(zoom))
            page_numbers = [page_number] if page_number else list(range(1, page_count + 1))
            for current_page in page_numbers:
                page = doc.load_page(current_page - 1)
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                page_path = self._page_path(job_root, current_page, size_name)
                pix.save(page_path)
            return page_count
        finally:
            doc.close()

    def _load_ready_job(self, key: str) -> RenderedDocumentJob | None:
        job_root = self._job_root(key)
        manifest_path = job_root / "manifest.json"
        if not manifest_path.exists():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if manifest.get("version") != RENDER_VERSION or manifest.get("key") != key:
            return None
        page_count = int(manifest.get("page_count") or 0)
        if page_count <= 0:
            return None
        document_file = str(manifest.get("document_file") or "")
        pdf_file = str(manifest.get("pdf_file") or "")
        if not document_file or not (job_root / document_file).exists():
            return None
        if not pdf_file or not (job_root / pdf_file).exists():
            return None
        for page_number in range(1, page_count + 1):
            if not self._page_path(job_root, page_number, "medium").exists():
                return None
        job = RenderedDocumentJob(key=key, manifest=manifest, root=job_root)
        self._touch_manifest(job)
        return job

    def _touch_manifest(self, job: RenderedDocumentJob) -> None:
        manifest = dict(job.manifest)
        manifest["last_access_at"] = time.time()
        self._write_manifest(job.root, manifest)
        job.manifest.clear()
        job.manifest.update(manifest)

    def _write_manifest(self, job_root: Path, manifest: dict[str, Any]) -> None:
        tmp_path = job_root / f"manifest.{threading.get_ident()}.json.tmp"
        final_path = job_root / "manifest.json"
        tmp_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(final_path)

    def _job_root(self, key: str) -> Path:
        return self.root / key[:2] / key

    def _page_path(self, job_root: Path, page_number: int, size_name: str) -> Path:
        return job_root / f"page-{page_number:03d}.{size_name}.png"

    def _key_lock(self, key: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    @contextmanager
    def _renderer_slot(self):
        acquired = self._semaphore.acquire(timeout=self.queue_timeout_seconds)
        if not acquired:
            raise DocumentRenderQueueBusy("文档渲染任务较多，请稍后重试。")
        try:
            yield
        finally:
            self._semaphore.release()


document_render_service = DocumentRenderService()
