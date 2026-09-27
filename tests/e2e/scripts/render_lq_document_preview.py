"""Render the actual preview HTML without importing the app, DB or .env."""
import importlib.util
import json
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = "_lq_document_preview_fixture"


def module(name, **attrs):
    result = types.ModuleType(name)
    result.__dict__.update(attrs)
    sys.modules[name] = result
    return result


module(PACKAGE, __path__=[])
module(PACKAGE + ".services", __path__=[])
module(PACKAGE + ".config", SECRET_KEY="isolated-document-preview-fixture", TEMPLATES_DIR=ROOT / "templates")
module(PACKAGE + ".storage_paths", DATA_ROOT=ROOT / ".codex-temp" / "document-preview-fixture-unused")
module(PACKAGE + ".frontend_assets", asset_url=lambda name: "/static/" + (
    "css/tailwind-app.css" if name == "tailwind_app" else name) + "?v=isolated")


def no_conversion(*args, **kwargs):
    raise AssertionError("The presentation fixture must never convert or touch stored documents")


module(PACKAGE + ".services.libreoffice_service", LibreOfficeBusy=RuntimeError, convert_office_file=no_conversion)
spec = importlib.util.spec_from_file_location(PACKAGE + ".services.document_render_service", ROOT / "classroom_app/services/document_render_service.py")
renderer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = renderer
spec.loader.exec_module(renderer)
job = renderer.RenderedDocumentJob("a" * 64, {"page_count": 8, "filename": "fixture.pdf"}, ROOT / ".codex-temp" / "document-preview-fixture-unused")
service = renderer.DocumentRenderService(root=job.root)
options = dict(title="真实文档预览 · 合成教学材料", user={"id": 7, "role": "teacher"})

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({
        "preview": service.render_preview_html(job, **options),
        "disabled": service.render_preview_html(job, **options, download_disabled_reason="请先补齐业务字段后再导出。"),
        "error": service.render_error_html(title="合成预览", message="转换暂时不可用，请稍后重试。"),
        "head": renderer._preview_theme_head(),
        "isolated": not any(name in ("app", "core", "classroom_app") or name.startswith("classroom_app.") for name in sys.modules),
    }, ensure_ascii=False))
