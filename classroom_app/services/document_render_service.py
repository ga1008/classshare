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
import base64
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
RENDER_VERSION = "document-renderer-v2"
RENDER_TOKEN_PREFIX = "dr1."
DEFAULT_TOKEN_TTL_SECONDS = 2 * 60 * 60


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


def _content_key(content: bytes, source_format: str, render_profile: str = "") -> str:
    digest = hashlib.sha256()
    digest.update(RENDER_VERSION.encode("utf-8"))
    digest.update(b"\0")
    digest.update(str(source_format or "").lower().encode("utf-8"))
    digest.update(b"\0")
    digest.update(str(render_profile or "").encode("utf-8"))
    digest.update(b"\0")
    digest.update(content)
    return digest.hexdigest()


def _secret_key_bytes() -> bytes:
    return hashlib.sha256(str(SECRET_KEY or "lanshare-document-renderer").encode("utf-8")).digest()


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _render_user_scope(user: dict[str, Any] | None) -> dict[str, str]:
    raw = user or {}
    user_id = raw.get("id") or raw.get("user_id") or raw.get("pk") or ""
    role = raw.get("role") or raw.get("user_role") or ""
    return {"id": str(user_id), "role": str(role or "")}


def is_valid_render_key(key: str) -> bool:
    text = str(key or "")
    return len(text) == 64 and all(ch in "0123456789abcdef" for ch in text.lower())


def issue_render_token(key: str, *, user: dict[str, Any], ttl_seconds: int | None = None) -> str:
    if not is_valid_render_key(key):
        raise DocumentRenderError("预览缓存标识无效，请重新生成预览。")
    scope = _render_user_scope(user)
    if not scope["id"] or not scope["role"]:
        raise DocumentRenderError("预览需要有效的登录用户上下文。")
    ttl = max(60, min(int(ttl_seconds or DEFAULT_TOKEN_TTL_SECONDS), 24 * 60 * 60))
    payload = {
        "v": 1,
        "key": key,
        "scope": scope,
        "exp": int(time.time()) + ttl,
    }
    payload_b64 = _b64encode(_canonical_json(payload).encode("utf-8"))
    signature = hmac.new(_secret_key_bytes(), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return RENDER_TOKEN_PREFIX + payload_b64 + "." + _b64encode(signature)


def sign_render_key(key: str) -> str:
    """Legacy helper kept for imports; new preview pages use issue_render_token."""
    payload = f"document-render:{key}".encode("utf-8")
    return hmac.new(_secret_key_bytes(), payload, hashlib.sha256).hexdigest()


def verify_render_token(key: str, token: str | None, *, user: dict[str, Any] | None = None) -> bool:
    raw = str(token or "").strip()
    if not is_valid_render_key(key) or not raw.startswith(RENDER_TOKEN_PREFIX):
        return False
    try:
        body = raw[len(RENDER_TOKEN_PREFIX) :]
        payload_b64, signature_b64 = body.split(".", 1)
        expected = hmac.new(_secret_key_bytes(), payload_b64.encode("ascii"), hashlib.sha256).digest()
        actual = _b64decode(signature_b64)
        if not hmac.compare_digest(expected, actual):
            return False
        payload = json.loads(_b64decode(payload_b64).decode("utf-8"))
    except Exception:
        return False
    if payload.get("key") != key:
        return False
    if int(payload.get("exp") or 0) < int(time.time()):
        return False
    if _render_user_scope(user) != dict(payload.get("scope") or {}):
        return False
    return True


class DocumentRenderService:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or DATA_ROOT / "tmp" / "document_renderer")
        self.max_concurrent = _env_int("LANSHARE_DOCUMENT_RENDER_MAX_CONCURRENCY", 1)
        self.queue_timeout_seconds = _env_int("LANSHARE_DOCUMENT_RENDER_QUEUE_TIMEOUT_SECONDS", 45)
        self.ttl_seconds = _env_int("LANSHARE_DOCUMENT_RENDER_TTL_SECONDS", 60 * 60 * 24)
        self.token_ttl_seconds = _env_int("LANSHARE_DOCUMENT_RENDER_TOKEN_TTL_SECONDS", DEFAULT_TOKEN_TTL_SECONDS)
        self.max_pages = _env_int("LANSHARE_DOCUMENT_RENDER_MAX_PAGES", 200)
        self.medium_zoom = float(os.getenv("LANSHARE_DOCUMENT_RENDER_MEDIUM_ZOOM", "1.45") or 1.45)
        self.large_zoom = float(os.getenv("LANSHARE_DOCUMENT_RENDER_LARGE_ZOOM", "2.35") or 2.35)
        self._semaphore = threading.BoundedSemaphore(self.max_concurrent)
        self._locks_root = self.root / "_locks"
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
        key = _content_key(content, normalized_format, self._render_profile_key())
        job_root = self._job_root(key)
        lock = self._key_lock(key)
        with lock:
            cached = self._load_ready_job(key)
            if cached:
                return cached

            with self._job_file_lock(key):
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
        if not is_valid_render_key(key):
            raise DocumentRenderNotFound("预览缓存标识无效，请刷新预览页面重新生成。")
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
            with self._job_file_lock(key):
                if page_path.exists():
                    self._touch_manifest(job)
                    return page_path
                with self._renderer_slot():
                    pdf_file = str(job.manifest.get("pdf_file") or "document.pdf")
                    pdf_path = job.root / pdf_file
                    if not pdf_path.exists():
                        raise DocumentRenderNotFound("PDF 中间文件缓存已过期，请刷新预览页面。")
                    zoom = self.large_zoom if normalized_size == "large" else self.medium_zoom
                    self._render_pdf_pages(
                        pdf_path,
                        job.root,
                        zoom=zoom,
                        size_name=normalized_size,
                        page_number=page_number,
                    )
                    self._touch_manifest(job)
                    return page_path

    def render_preview_html(
        self,
        job: RenderedDocumentJob,
        *,
        title: str,
        user: dict[str, Any],
        eyebrow: str = "文档真实预览",
        download_label: str = "下载文件",
    ) -> str:
        token = issue_render_token(job.key, user=user, ttl_seconds=self.token_ttl_seconds)
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
                f"<button class=\"doc-preview-card is-page-pending\" type=\"button\" "
                f"data-page-index=\"{page['number'] - 1}\" data-page-status=\"idle\" "
                f"aria-label=\"查看第 {page['number']} 页大图\">"
                f"<span class=\"doc-preview-card__paper\">"
                f"<img data-page-image alt=\"第 {page['number']} 页预览图\" decoding=\"async\" hidden>"
                f"<span class=\"doc-preview-card__placeholder\" data-page-placeholder>"
                f"<span class=\"doc-preview-page-spinner\"></span>"
                f"<strong>正在渲染</strong><em>第 {page['number']} / {job.page_count} 页</em>"
                f"</span></span>"
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
    width: min(1180px, calc(100vw - 28px));
    margin: 0 auto;
    padding: clamp(18px, 3vw, 34px) 0 34px;
    display: grid;
    place-items: center;
    overflow: hidden;
  }}
  .doc-preview-deck-shell {{
    position: relative;
    width: 100%;
    height: min(920px, calc(100vh - 132px));
    min-height: 430px;
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    align-items: center;
    gap: clamp(10px, 2vw, 20px);
  }}
  .doc-preview-pages {{
    position: relative;
    width: min(700px, 82vw);
    height: 100%;
    min-height: 430px;
    margin: 0 auto;
    outline: none;
    perspective: 1800px;
    perspective-origin: 50% 42%;
    transform-style: preserve-3d;
    touch-action: pan-y;
  }}
  .doc-preview-card {{
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    min-width: 0;
    margin: 0;
    padding: 0;
    border: 0;
    background: transparent;
    cursor: pointer;
    opacity: 0;
    pointer-events: none;
    transform-style: preserve-3d;
    will-change: transform, opacity, filter;
    transition:
      transform 360ms cubic-bezier(0.22, 0.8, 0.3, 1),
      opacity 220ms ease,
      filter 220ms ease;
  }}
  .doc-preview-card.is-visible {{ pointer-events: auto; }}
  .doc-preview-card.is-active {{ cursor: zoom-in; }}
  .doc-preview-card:focus-visible {{ outline: none; }}
  .doc-preview-card:focus-visible .doc-preview-card__paper {{
    border-color: rgba(14, 165, 233, 0.52);
    box-shadow:
      0 0 0 4px rgba(14, 165, 233, 0.16),
      0 34px 84px rgba(22, 32, 51, 0.22);
  }}
  .doc-preview-card__paper {{
    position: relative;
    height: 100%;
    display: grid;
    place-items: center;
    overflow: hidden;
    border-radius: 8px;
    border: 1px solid rgba(117, 129, 149, 0.22);
    background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
    box-shadow: 0 22px 54px rgba(22, 32, 51, 0.18);
    transition: box-shadow 180ms ease, border-color 180ms ease;
  }}
  .doc-preview-card.is-active:hover .doc-preview-card__paper,
  .doc-preview-card.is-side:hover .doc-preview-card__paper {{
    border-color: rgba(14, 165, 233, 0.35);
    box-shadow: 0 32px 76px rgba(22, 32, 51, 0.22);
  }}
  .doc-preview-card img {{
    display: block;
    width: 100%;
    height: 100%;
    object-fit: contain;
    background: #fff;
    opacity: 0;
    transition: opacity 180ms ease;
  }}
  .doc-preview-card.is-page-ready img {{ opacity: 1; }}
  .doc-preview-card__placeholder {{
    position: absolute;
    inset: 0;
    display: grid;
    gap: 8px;
    place-content: center;
    justify-items: center;
    padding: 24px;
    color: #475569;
    background:
      linear-gradient(135deg, rgba(14, 165, 233, 0.10), rgba(15, 118, 110, 0.08)),
      #ffffff;
    text-align: center;
    transition: opacity 180ms ease;
  }}
  .doc-preview-card__placeholder strong {{
    font-size: 0.95rem;
    font-weight: 850;
  }}
  .doc-preview-card__placeholder em {{
    font-style: normal;
    font-size: 0.78rem;
    color: var(--muted);
  }}
  .doc-preview-card.is-page-ready .doc-preview-card__placeholder {{
    opacity: 0;
    pointer-events: none;
  }}
  .doc-preview-card.is-page-error .doc-preview-card__placeholder {{
    color: #92400e;
    background:
      linear-gradient(135deg, rgba(251, 191, 36, 0.14), rgba(14, 165, 233, 0.08)),
      #fff7ed;
  }}
  .doc-preview-card.is-page-error .doc-preview-page-spinner {{ display: none; }}
  .doc-preview-page-spinner {{
    width: 28px;
    height: 28px;
    border-radius: 999px;
    border: 3px solid rgba(14, 165, 233, 0.18);
    border-top-color: var(--sky);
    animation: docPreviewSpin 0.9s linear infinite;
  }}
  .doc-preview-card__meta {{
    position: absolute;
    left: 14px;
    bottom: 14px;
    display: none;
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
  .doc-preview-deck-btn {{
    width: 42px;
    height: 42px;
    border: 1px solid rgba(117, 129, 149, 0.24);
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.88);
    color: var(--ink);
    cursor: pointer;
    font-size: 1.45rem;
    line-height: 1;
    box-shadow: 0 18px 42px rgba(22, 32, 51, 0.10);
    transition: transform 160ms ease, border-color 160ms ease, color 160ms ease, box-shadow 160ms ease;
  }}
  .doc-preview-deck-btn:hover,
  .doc-preview-deck-btn:focus-visible {{
    color: #075985;
    border-color: rgba(14, 165, 233, 0.34);
    box-shadow: 0 22px 54px rgba(22, 32, 51, 0.14);
    transform: translateY(-1px);
    outline: none;
  }}
  .doc-preview-deck-status {{
    position: absolute;
    left: 50%;
    bottom: 12px;
    z-index: 130;
    min-height: 30px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0 12px;
    border: 1px solid rgba(117, 129, 149, 0.18);
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.86);
    color: #334155;
    font-size: 0.82rem;
    font-weight: 800;
    box-shadow: 0 16px 38px rgba(22, 32, 51, 0.10);
    transform: translateX(-50%);
    pointer-events: none;
  }}
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
  .doc-preview-lightbox__controls {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }}
  .doc-preview-zoom-group {{
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 4px;
    border: 1px solid rgba(255, 255, 255, 0.18);
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.10);
  }}
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
  .doc-preview-icon-btn:disabled {{
    cursor: default;
    opacity: 0.42;
  }}
  .doc-preview-zoom-reset {{
    min-width: 56px;
    padding: 0 10px;
    font-size: 0.78rem;
    font-weight: 800;
  }}
  .doc-preview-lightbox__body {{
    position: relative;
    min-height: 0;
    display: grid;
    place-items: center;
    padding: 0 clamp(12px, 3vw, 30px) clamp(18px, 4vw, 34px);
    overflow: hidden;
    touch-action: none;
  }}
  .doc-preview-large-frame {{
    display: grid;
    place-items: center;
    max-width: min(1180px, 96vw);
    max-height: calc(100vh - 108px);
    overflow: visible;
  }}
  .doc-preview-large-image {{
    max-width: 100%;
    max-height: calc(100vh - 108px);
    width: auto;
    height: auto;
    border-radius: 8px;
    background: #fff;
    box-shadow: var(--shadow);
    cursor: grab;
    user-select: none;
    transform-origin: center center;
    will-change: transform;
    transition: filter 160ms ease, opacity 160ms ease, transform 120ms ease;
  }}
  .doc-preview-large-image.is-loading-large {{
    filter: saturate(0.72);
    opacity: 0.82;
  }}
  .doc-preview-large-image.is-zoomed {{ cursor: grab; }}
  .doc-preview-large-image.is-panning {{
    cursor: grabbing;
    transition: filter 160ms ease, opacity 160ms ease;
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
  .doc-preview-loading.is-error .doc-preview-spinner {{ display: none; }}
  .doc-preview-loading__actions {{ display: flex; gap: 8px; align-items: center; }}
  .doc-preview-loading__actions[hidden] {{ display: none; }}
  .doc-preview-retry {{
    min-height: 32px;
    border: 1px solid rgba(14, 165, 233, 0.24);
    border-radius: 999px;
    background: #fff;
    color: #075985;
    padding: 0 12px;
    font-weight: 750;
    cursor: pointer;
  }}
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
    .doc-preview-stage {{ width: min(100vw - 20px, 560px); padding-bottom: 28px; }}
    .doc-preview-deck-shell {{
      height: calc(100vh - 158px);
      min-height: 400px;
      grid-template-columns: 1fr;
      gap: 0;
    }}
    .doc-preview-pages {{
      width: min(78vw, 420px);
      min-height: 360px;
    }}
    .doc-preview-deck-btn {{
      position: absolute;
      top: 50%;
      z-index: 150;
      transform: translateY(-50%);
    }}
    .doc-preview-deck-btn:hover,
    .doc-preview-deck-btn:focus-visible {{ transform: translateY(-50%); }}
    .doc-preview-deck-btn[data-deck-prev] {{ left: 4px; }}
    .doc-preview-deck-btn[data-deck-next] {{ right: 4px; }}
    .doc-preview-deck-status {{ bottom: 4px; }}
    .doc-preview-lightbox__bar {{ align-items: flex-start; flex-wrap: wrap; }}
    .doc-preview-lightbox__controls {{ gap: 6px; }}
    .doc-preview-zoom-group {{ order: 2; }}
    .doc-preview-icon-btn {{ width: 36px; height: 36px; }}
    .doc-preview-zoom-reset {{ min-width: 52px; }}
  }}
  @media (prefers-reduced-motion: reduce) {{
    .doc-preview-card,
    .doc-preview-card__paper,
    .doc-preview-deck-btn,
    .doc-preview-large-image {{ transition: none; }}
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
    <div class="doc-preview-deck-shell">
      <button class="doc-preview-deck-btn" type="button" data-deck-prev aria-label="上一页">‹</button>
      <div class="doc-preview-pages" data-page-deck tabindex="0" aria-label="文档页面，使用鼠标滚轮或方向键切换页面">{page_cards}</div>
      <button class="doc-preview-deck-btn" type="button" data-deck-next aria-label="下一页">›</button>
      <div class="doc-preview-deck-status" data-deck-count aria-live="polite">1 / {job.page_count}</div>
    </div>
  </section>
</main>
<div class="doc-preview-lightbox" data-lightbox hidden>
  <div class="doc-preview-lightbox__bar">
    <strong data-lightbox-title>{escaped_title}</strong>
    <div class="doc-preview-lightbox__controls">
      <div class="doc-preview-zoom-group" aria-label="页面缩放">
        <button class="doc-preview-icon-btn" type="button" data-zoom-out aria-label="缩小">−</button>
        <button class="doc-preview-icon-btn doc-preview-zoom-reset" type="button" data-zoom-reset aria-label="还原缩放">100%</button>
        <button class="doc-preview-icon-btn" type="button" data-zoom-in aria-label="放大">+</button>
      </div>
      <button class="doc-preview-icon-btn" type="button" data-prev aria-label="上一页">‹</button>
      <span data-lightbox-count>1 / {job.page_count}</span>
      <button class="doc-preview-icon-btn" type="button" data-next aria-label="下一页">›</button>
      <button class="doc-preview-icon-btn" type="button" data-close aria-label="关闭">×</button>
    </div>
  </div>
  <div class="doc-preview-lightbox__body">
    <div class="doc-preview-large-frame" data-large-frame>
      <img class="doc-preview-large-image" data-large-image alt="高清页面预览" draggable="false">
    </div>
    <div class="doc-preview-loading" data-loading>
      <span class="doc-preview-spinner"></span>
      <strong data-loading-text>正在生成高清预览...</strong>
      <div class="doc-preview-loading__actions" data-loading-actions hidden>
        <button class="doc-preview-retry" type="button" data-retry-large>重试高清图</button>
      </div>
    </div>
  </div>
</div>
<script>
(() => {{
  const pages = {pages_json};
  const lightbox = document.querySelector('[data-lightbox]');
  const image = document.querySelector('[data-large-image]');
  const loading = document.querySelector('[data-loading]');
  const loadingText = document.querySelector('[data-loading-text]');
  const loadingActions = document.querySelector('[data-loading-actions]');
  const counter = document.querySelector('[data-lightbox-count]');
  const lightboxBody = document.querySelector('.doc-preview-lightbox__body');
  const imageFrame = document.querySelector('[data-large-frame]');
  const zoomOut = document.querySelector('[data-zoom-out]');
  const zoomReset = document.querySelector('[data-zoom-reset]');
  const zoomIn = document.querySelector('[data-zoom-in]');
  const stage = document.querySelector('.doc-preview-stage');
  const deck = document.querySelector('[data-page-deck]');
  const deckCards = Array.from(document.querySelectorAll('[data-page-index]'));
  const deckPrev = document.querySelector('[data-deck-prev]');
  const deckNext = document.querySelector('[data-deck-next]');
  const deckCounter = document.querySelector('[data-deck-count]');
  const pageStates = pages.map(() => 'idle');
  let activeIndex = 0;
  let deckIndex = 0;
  let latestRequestId = 0;
  let wheelAccumulator = 0;
  let wheelLockUntil = 0;
  let zoomScale = 1;
  let panX = 0;
  let panY = 0;
  let panState = null;
  const minZoom = 0.6;
  const maxZoom = 4;

  function clampIndex(index) {{
    if (!pages.length) return 0;
    return (index + pages.length) % pages.length;
  }}

  function clampValue(value, min, max) {{
    return Math.min(max, Math.max(min, value));
  }}

  function signedOffset(index, base) {{
    let offset = index - base;
    if (pages.length > 2) {{
      const half = pages.length / 2;
      if (offset > half) offset -= pages.length;
      if (offset < -half) offset += pages.length;
    }}
    return offset;
  }}

  function cardTransform(offset, distance) {{
    const clamped = Math.min(distance, 4);
    const lateral = offset * 54;
    const vertical = clamped * 16;
    const depth = 64 - clamped * 132;
    const rotateY = offset * -6;
    const rotateX = clamped ? 3.4 : 0;
    const scale = 1 - clamped * 0.055;
    return 'translate3d(' + lateral + 'px, ' + vertical + 'px, ' + depth + 'px) '
      + 'rotateX(' + rotateX + 'deg) rotateY(' + rotateY + 'deg) scale(' + scale + ')';
  }}

  function getPageCard(index) {{
    return deckCards[index] || null;
  }}

  function setPageState(index, status) {{
    pageStates[index] = status;
    const card = getPageCard(index);
    const page = pages[index];
    if (!card || !page) return;
    card.dataset.pageStatus = status;
    card.classList.toggle('is-page-loading', status === 'loading');
    card.classList.toggle('is-page-ready', status === 'ready');
    card.classList.toggle('is-page-error', status === 'error');
    card.classList.toggle('is-page-pending', status === 'idle' || status === 'loading');
    const placeholder = card.querySelector('[data-page-placeholder]');
    if (!placeholder) return;
    const label = placeholder.querySelector('strong');
    const hint = placeholder.querySelector('em');
    if (label) label.textContent = status === 'error' ? '渲染失败' : '正在渲染';
    if (hint) hint.textContent = status === 'error'
      ? '点击重试第 ' + page.number + ' 页'
      : '第 ' + page.number + ' / ' + pages.length + ' 页';
  }}

  function loadMediumPage(index, options = {{}}) {{
    const page = pages[index];
    const card = getPageCard(index);
    if (!page || !card) return;
    if (!options.force && (pageStates[index] === 'loading' || pageStates[index] === 'ready')) return;
    const imageEl = card.querySelector('[data-page-image]');
    if (!imageEl) return;
    setPageState(index, 'loading');
    const loader = new Image();
    loader.onload = () => {{
      imageEl.src = loader.src;
      imageEl.hidden = false;
      setPageState(index, 'ready');
    }};
    loader.onerror = () => {{
      setPageState(index, 'error');
    }};
    loader.src = page.mediumUrl;
  }}

  function requestVisiblePages() {{
    deckCards.forEach((card, index) => {{
      if (card.classList.contains('is-visible')) loadMediumPage(index);
    }});
  }}

  function updateDeck(options = {{}}) {{
    if (!deckCards.length) return;
    deckCards.forEach((card, index) => {{
      const offset = signedOffset(index, deckIndex);
      const distance = Math.abs(offset);
      const visible = distance <= 3 || pages.length <= 4;
      const hiddenOffset = offset === 0 ? 4 : Math.sign(offset) * 4;
      const opacity = visible ? Math.max(0.14, 1 - distance * 0.24) : 0;
      const saturation = Math.max(0.72, 1 - distance * 0.08);
      const brightness = Math.max(0.84, 1 - distance * 0.035);

      card.classList.toggle('is-active', index === deckIndex);
      card.classList.toggle('is-side', index !== deckIndex && visible);
      card.classList.toggle('is-visible', visible);
      card.style.transform = visible ? cardTransform(offset, distance) : cardTransform(hiddenOffset, 4);
      card.style.opacity = String(opacity);
      card.style.filter = visible && distance ? 'saturate(' + saturation + ') brightness(' + brightness + ')' : 'none';
      card.style.zIndex = String(120 - distance * 9 - (offset > 0 ? 1 : 0));
      card.tabIndex = index === deckIndex ? 0 : -1;
      card.setAttribute('aria-current', index === deckIndex ? 'page' : 'false');
      card.setAttribute('aria-hidden', visible ? 'false' : 'true');
    }});
    const current = pages[deckIndex];
    if (deckCounter && current) deckCounter.textContent = current.number + ' / ' + pages.length;
    if (options.focus && deckCards[deckIndex]) deckCards[deckIndex].focus({{ preventScroll: true }});
    requestVisiblePages();
  }}

  function goToDeck(index, options = {{}}) {{
    if (!pages.length) return;
    deckIndex = clampIndex(index);
    updateDeck(options);
  }}

  function stepDeck(delta, options = {{}}) {{
    goToDeck(deckIndex + delta, options);
  }}

  function clampPan() {{
    if (!image || !lightboxBody) return;
    if (zoomScale <= 1.01) {{
      panX = 0;
      panY = 0;
      return;
    }}
    const frameRect = lightboxBody.getBoundingClientRect();
    const renderedWidth = (image.offsetWidth || frameRect.width) * zoomScale;
    const renderedHeight = (image.offsetHeight || frameRect.height) * zoomScale;
    const maxX = Math.max(0, (renderedWidth - frameRect.width) / 2 + 80);
    const maxY = Math.max(0, (renderedHeight - frameRect.height) / 2 + 80);
    panX = clampValue(panX, -maxX, maxX);
    panY = clampValue(panY, -maxY, maxY);
  }}

  function applyZoomState() {{
    clampPan();
    image.style.transform = 'translate3d(' + panX + 'px, ' + panY + 'px, 0) scale(' + zoomScale + ')';
    image.classList.toggle('is-zoomed', zoomScale > 1.01);
    if (zoomReset) zoomReset.textContent = Math.round(zoomScale * 100) + '%';
    if (zoomOut) zoomOut.disabled = zoomScale <= minZoom + 0.01;
    if (zoomIn) zoomIn.disabled = zoomScale >= maxZoom - 0.01;
  }}

  function setZoom(nextScale, options = {{}}) {{
    const previousScale = zoomScale;
    zoomScale = clampValue(nextScale, minZoom, maxZoom);
    if (options.anchor && previousScale > 0) {{
      const rect = image.getBoundingClientRect();
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      const ratio = zoomScale / previousScale;
      panX -= (options.anchor.x - centerX) * (ratio - 1);
      panY -= (options.anchor.y - centerY) * (ratio - 1);
    }}
    if (options.resetPan || zoomScale <= 1.01) {{
      panX = 0;
      panY = 0;
    }}
    applyZoomState();
  }}

  function resetZoom() {{
    zoomScale = 1;
    panX = 0;
    panY = 0;
    applyZoomState();
  }}

  function stopPan() {{
    if (!panState) return;
    panState = null;
    image.classList.remove('is-panning');
  }}

  function openPage(index) {{
    activeIndex = clampIndex(index);
    deckIndex = activeIndex;
    updateDeck();
    const page = pages[activeIndex];
    if (!page) return;
    const requestId = ++latestRequestId;
    stopPan();
    resetZoom();
    lightbox.hidden = false;
    loading.hidden = false;
    loading.classList.remove('is-error');
    loadingText.textContent = '正在生成高清预览...';
    loadingActions.hidden = true;
    image.classList.add('is-loading-large');
    image.src = page.mediumUrl;
    counter.textContent = page.number + ' / ' + pages.length;
    const loader = new Image();
    loader.onload = () => {{
      if (requestId !== latestRequestId) return;
      image.src = loader.src;
      image.classList.remove('is-loading-large');
      loading.hidden = true;
    }};
    loader.onerror = () => {{
      if (requestId !== latestRequestId) return;
      image.classList.remove('is-loading-large');
      loading.classList.add('is-error');
      loadingText.textContent = '高清预览生成失败，已保留清晰中图';
      loadingActions.hidden = false;
    }};
    loader.src = page.largeUrl;
  }}

  function closeLightbox() {{
    stopPan();
    lightbox.hidden = true;
    updateDeck({{ focus: true }});
  }}

  deckCards.forEach((card) => {{
    card.addEventListener('click', () => {{
      const index = Number(card.dataset.pageIndex || 0);
      if (pageStates[index] === 'error') {{
        loadMediumPage(index, {{ force: true }});
        return;
      }}
      if (index === deckIndex) {{
        openPage(index);
      }} else {{
        goToDeck(index, {{ focus: true }});
      }}
    }});
  }});
  deckPrev.addEventListener('click', () => stepDeck(-1, {{ focus: true }}));
  deckNext.addEventListener('click', () => stepDeck(1, {{ focus: true }}));
  deck.addEventListener('keydown', (event) => {{
    if (!lightbox.hidden) return;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {{
      event.preventDefault();
      stepDeck(1, {{ focus: true }});
    }} else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {{
      event.preventDefault();
      stepDeck(-1, {{ focus: true }});
    }} else if (event.key === 'Enter' || event.key === ' ') {{
      event.preventDefault();
      openPage(deckIndex);
    }}
  }});
  stage.addEventListener('wheel', (event) => {{
    if (!lightbox.hidden || !pages.length) return;
    if (Math.abs(event.deltaY) < Math.abs(event.deltaX)) return;
    event.preventDefault();
    const now = Date.now();
    wheelAccumulator += event.deltaY;
    if (now < wheelLockUntil) return;
    if (Math.abs(wheelAccumulator) < 32) return;
    stepDeck(wheelAccumulator > 0 ? 1 : -1);
    wheelAccumulator = 0;
    wheelLockUntil = now + 220;
  }}, {{ passive: false }});
  zoomOut.addEventListener('click', () => setZoom(zoomScale * 0.86));
  zoomIn.addEventListener('click', () => setZoom(zoomScale * 1.16));
  zoomReset.addEventListener('click', () => resetZoom());
  image.addEventListener('wheel', (event) => {{
    event.preventDefault();
    event.stopPropagation();
    const factor = event.deltaY < 0 ? 1.14 : 0.88;
    setZoom(zoomScale * factor, {{ anchor: {{ x: event.clientX, y: event.clientY }} }});
  }}, {{ passive: false }});
  image.addEventListener('pointerdown', (event) => {{
    if (event.button !== 0 || zoomScale <= 1.01) return;
    event.preventDefault();
    panState = {{
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      panX,
      panY,
    }};
    image.classList.add('is-panning');
    try {{
      image.setPointerCapture(event.pointerId);
    }} catch {{
      /* Pointer capture is optional. */
    }}
  }});
  image.addEventListener('pointermove', (event) => {{
    if (!panState || event.pointerId !== panState.pointerId) return;
    event.preventDefault();
    panX = panState.panX + event.clientX - panState.startX;
    panY = panState.panY + event.clientY - panState.startY;
    applyZoomState();
  }});
  image.addEventListener('pointerup', stopPan);
  image.addEventListener('pointercancel', stopPan);
  image.addEventListener('lostpointercapture', stopPan);
  image.addEventListener('dragstart', (event) => event.preventDefault());
  document.querySelector('[data-close]').addEventListener('click', closeLightbox);
  document.querySelector('[data-prev]').addEventListener('click', () => openPage(activeIndex - 1));
  document.querySelector('[data-next]').addEventListener('click', () => openPage(activeIndex + 1));
  document.querySelector('[data-retry-large]').addEventListener('click', (event) => {{
    event.stopPropagation();
    openPage(activeIndex);
  }});
  lightbox.addEventListener('click', (event) => {{
    if (event.target === lightbox) closeLightbox();
  }});
  window.addEventListener('keydown', (event) => {{
    if (lightbox.hidden) return;
    if (event.key === 'Escape') closeLightbox();
    if (event.key === 'ArrowLeft') openPage(activeIndex - 1);
    if (event.key === 'ArrowRight') openPage(activeIndex + 1);
    if (event.key === '+' || event.key === '=') {{
      event.preventDefault();
      setZoom(zoomScale * 1.16);
    }}
    if (event.key === '-' || event.key === '_') {{
      event.preventDefault();
      setZoom(zoomScale * 0.86);
    }}
    if (event.key === '0') {{
      event.preventDefault();
      resetZoom();
    }}
  }});
  window.addEventListener('resize', () => applyZoomState());
  updateDeck();
  applyZoomState();
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
        for manifest_path in self._iter_manifest_paths():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                last_access = float(manifest.get("last_access_at") or manifest.get("updated_at") or 0)
            except (OSError, ValueError, TypeError):
                last_access = 0
            if current - last_access <= self.ttl_seconds:
                continue
            key = str(manifest.get("key") or manifest_path.parent.name)
            if is_valid_render_key(key):
                try:
                    with self._job_file_lock(key, timeout_seconds=0):
                        shutil.rmtree(manifest_path.parent, ignore_errors=True)
                except DocumentRenderQueueBusy:
                    continue
                continue
            job_root = manifest_path.parent
            try:
                shutil.rmtree(job_root, ignore_errors=True)
            except OSError:
                pass

    def cache_stats(self) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "root": str(self.root),
            "max_concurrent": self.max_concurrent,
            "queue_timeout_seconds": self.queue_timeout_seconds,
            "ttl_seconds": self.ttl_seconds,
            "token_ttl_seconds": self.token_ttl_seconds,
            "max_pages": self.max_pages,
            "medium_zoom": self.medium_zoom,
            "large_zoom": self.large_zoom,
            "job_count": 0,
            "total_bytes": 0,
            "medium_pages": 0,
            "large_pages": 0,
        }
        if not self.root.exists():
            return stats
        now = time.time()
        oldest_access: float | None = None
        newest_access: float | None = None
        for manifest_path in self._iter_manifest_paths():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            stats["job_count"] += 1
            last_access = float(manifest.get("last_access_at") or manifest.get("updated_at") or 0)
            if last_access:
                oldest_access = last_access if oldest_access is None else min(oldest_access, last_access)
                newest_access = last_access if newest_access is None else max(newest_access, last_access)
            try:
                children = list(manifest_path.parent.iterdir())
            except OSError:
                continue
            for child in children:
                if not child.is_file():
                    continue
                try:
                    stats["total_bytes"] += child.stat().st_size
                except OSError:
                    continue
                name = child.name
                if name.endswith(".medium.png"):
                    stats["medium_pages"] += 1
                elif name.endswith(".large.png"):
                    stats["large_pages"] += 1
        stats["oldest_access_age_seconds"] = int(now - oldest_access) if oldest_access else None
        stats["newest_access_age_seconds"] = int(now - newest_access) if newest_access else None
        return stats

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

        page_count = self._inspect_pdf_page_count(pdf_path)
        now = time.time()
        manifest = {
            "version": RENDER_VERSION,
            "render_profile": self._render_profile(),
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

    def _inspect_pdf_page_count(self, pdf_path: Path) -> int:
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
            return page_count
        finally:
            doc.close()

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
                self._atomic_write_bytes(page_path, pix.tobytes("png"))
            return page_count
        finally:
            doc.close()

    def _load_ready_job(self, key: str) -> RenderedDocumentJob | None:
        if not is_valid_render_key(key):
            return None
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
        if manifest.get("render_profile") != self._render_profile():
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
        tmp_path = job_root / f"manifest.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.json.tmp"
        final_path = job_root / "manifest.json"
        tmp_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(final_path)

    def _atomic_write_bytes(self, final_path: Path, content: bytes) -> None:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = final_path.with_name(f"{final_path.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp")
        tmp_path.write_bytes(content)
        tmp_path.replace(final_path)

    def _render_profile(self) -> dict[str, Any]:
        return {
            "medium_zoom": round(float(self.medium_zoom), 4),
            "large_zoom": round(float(self.large_zoom), 4),
            "max_pages": int(self.max_pages),
        }

    def _render_profile_key(self) -> str:
        return _canonical_json(self._render_profile())

    def _iter_manifest_paths(self):
        if not self.root.exists():
            return
        try:
            prefix_roots = list(self.root.iterdir())
        except OSError:
            return
        for prefix_root in prefix_roots:
            if not prefix_root.is_dir() or prefix_root.name.startswith("_"):
                continue
            try:
                manifest_paths = list(prefix_root.glob("*/manifest.json"))
            except OSError:
                continue
            for manifest_path in manifest_paths:
                yield manifest_path

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

    def _lock_path_for_key(self, key: str) -> Path:
        return self._locks_root / f"job-{key}.lock"

    def _prepare_lock_handle(self, handle) -> None:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)

    def _try_lock_handle(self, handle) -> None:
        self._prepare_lock_handle(handle)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_handle(self, handle) -> None:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass

    @contextmanager
    def _file_lock(self, lock_path: Path, *, timeout_seconds: int | float, busy_message: str):
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        handle = lock_path.open("a+b")
        acquired = False
        try:
            while True:
                try:
                    self._try_lock_handle(handle)
                    acquired = True
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise DocumentRenderQueueBusy(busy_message) from exc
                    time.sleep(0.08)
            yield
        finally:
            if acquired:
                self._unlock_handle(handle)
            handle.close()

    @contextmanager
    def _job_file_lock(self, key: str, *, timeout_seconds: int | float | None = None):
        timeout = self.queue_timeout_seconds if timeout_seconds is None else timeout_seconds
        with self._file_lock(
            self._lock_path_for_key(key),
            timeout_seconds=timeout,
            busy_message="同一文档正在生成预览，请稍后刷新。",
        ):
            yield

    @contextmanager
    def _global_renderer_slot(self):
        self._locks_root.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + float(self.queue_timeout_seconds)
        handle = None
        acquired = False
        try:
            while True:
                for index in range(max(1, self.max_concurrent)):
                    candidate = self._locks_root / f"slot-{index}.lock"
                    candidate.parent.mkdir(parents=True, exist_ok=True)
                    current = candidate.open("a+b")
                    try:
                        self._try_lock_handle(current)
                    except OSError:
                        current.close()
                        continue
                    handle = current
                    acquired = True
                    break
                if acquired:
                    break
                if time.monotonic() >= deadline:
                    raise DocumentRenderQueueBusy("文档渲染任务较多，请稍后重试。")
                time.sleep(0.08)
            yield
        finally:
            if handle is not None:
                if acquired:
                    self._unlock_handle(handle)
                handle.close()

    @contextmanager
    def _renderer_slot(self):
        acquired = self._semaphore.acquire(timeout=self.queue_timeout_seconds)
        if not acquired:
            raise DocumentRenderQueueBusy("文档渲染任务较多，请稍后重试。")
        try:
            with self._global_renderer_slot():
                yield
        finally:
            self._semaphore.release()


document_render_service = DocumentRenderService()
