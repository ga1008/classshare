"""Preview shell for lesson-plan exports.

The preview intentionally embeds the same PDF export endpoint used by the
download buttons. Keeping preview and export on one render path avoids the old
HTML mock layout drifting away from the actual Word/PDF output.
"""

from __future__ import annotations

import html
from typing import Any
from urllib.parse import quote


_STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; background: #eef1f5; font-family: "Microsoft YaHei","PingFang SC",sans-serif; color: #111827; }
.lp-export-preview { min-height: 100vh; display: flex; flex-direction: column; }
.lp-export-preview__bar { display: flex; align-items: center; gap: 10px; padding: 10px 14px; background: #fff;
    border-bottom: 1px solid #e5e7eb; position: sticky; top: 0; z-index: 2; }
.lp-export-preview__bar strong { font-size: 14px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lp-export-preview__bar a { color: #075985; text-decoration: none; font-size: 13px; white-space: nowrap; }
.lp-export-preview__paper { flex: 1; min-height: 820px; background: #dfe5ec; }
.lp-export-preview__paper object,
.lp-export-preview__paper iframe { display: block; width: 100%; height: 100%; min-height: 820px; border: 0; }
.lp-export-preview__fallback { padding: 32px; text-align: center; color: #475569; }
@media print {
    .lp-export-preview__bar { display: none; }
    .lp-export-preview__paper { min-height: 100vh; }
}
"""


def _esc(value: Any) -> str:
    return html.escape(str(value or ""))


def render_plan_html(plan: dict[str, Any]) -> str:
    """A standalone preview page backed by the real PDF export."""
    plan_id = quote(str(plan.get("id") or ""), safe="")
    title = _esc((plan.get("cover") or {}).get("course_name") or plan.get("title") or "教案预览")
    pdf_url = f"/api/lesson-plans/{plan_id}/export?fmt=pdf&inline=1"
    png_url = f"/api/lesson-plans/{plan_id}/export?fmt=png&inline=1"
    return (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title} · 导出预览</title><style>{_STYLE}</style></head>"
        "<body><main class='lp-export-preview'>"
        "<div class='lp-export-preview__bar'>"
        f"<strong>{title}</strong>"
        f"<a href='{pdf_url}' target='_blank' rel='noopener'>打开 PDF</a>"
        f"<a href='{png_url}' target='_blank' rel='noopener'>打开 PNG</a>"
        "</div>"
        "<section class='lp-export-preview__paper'>"
        f"<object data='{pdf_url}' type='application/pdf'>"
        "<div class='lp-export-preview__fallback'>"
        "浏览器无法内嵌 PDF 预览。请使用上方链接打开 PDF 或 PNG。"
        "</div>"
        "</object>"
        "</section>"
        "</main></body></html>"
    )
