from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT / "templates"
STATIC_DIR = ROOT / "static"
FRONTEND_DIR = ROOT / "frontend" / "src"
ROUTERS_DIR = ROOT / "classroom_app" / "routers"
DEFAULT_OUTPUT = ROOT / "docs" / "frontend-migration-inventory.md"


TEMPLATE_REF_RE = re.compile(r"{%\s*(?:extends|include)\s+[\"']([^\"']+)[\"']")
TEMPLATE_FROM_RE = re.compile(r"{%\s*from\s+[\"']([^\"']+)[\"']")
ASSET_URL_RE = re.compile(r"asset_url\(\s*[\"']([^\"']+)[\"']")
STATIC_REF_RE = re.compile(r"[\"']/(static/(?:js|css|vendor|dist)/[^\"']+)[\"']")
VITE_ENTRY_RE = re.compile(r"vite_entry_tags\(\s*[\"']([^\"']+)[\"']")
ISLAND_RE = re.compile(r"data-lanshare-island=[\"']([^\"']+)[\"']")
ROUTE_RE = re.compile(
    r"@(router|app)\.(get|post|put|patch|delete|websocket)\(\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
HTML_NAME_RE = re.compile(r"[\"']([^\"']+\.html)[\"']")


WORKFLOW_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("认证与账号", ("login", "register", "session_expired", "permission_denied", "student_security")),
    ("首页与课堂", ("dashboard", "classroom_main", "semester_calendar", "learning_path")),
    ("作业与提交", ("assignment", "submission", "grading")),
    ("考试与试卷", ("exam", "paper")),
    ("资料与文件", ("material", "materials", "viewer", "whiteboard")),
    ("系统与超管", ("manage/system", "manage\\system", "super_admin", "agent_keys", "diagnostics", "organizations", "password_resets")),
    ("消息、反馈、博客、个人中心", ("message", "feedback", "blog", "profile")),
    ("教师管理中心", ("manage/", "manage\\", "workflow", "classes", "courses", "offerings", "textbooks")),
)


@dataclass(frozen=True)
class TemplateInventory:
    path: str
    extends: tuple[str, ...]
    includes: tuple[str, ...]
    assets: tuple[str, ...]
    vite_entries: tuple[str, ...]
    islands: tuple[str, ...]


@dataclass(frozen=True)
class RouterInventory:
    path: str
    route_count: int
    route_samples: tuple[str, ...]
    templates: tuple[str, ...]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _dedupe(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return tuple(result)


def _normalize_static_ref(value: str) -> str:
    cleaned = value.split("?", 1)[0].replace("\\", "/").lstrip("/")
    if cleaned.startswith("static/"):
        cleaned = cleaned[len("static/") :]
    return PurePosixPath(cleaned).as_posix()


def collect_templates() -> list[TemplateInventory]:
    templates: list[TemplateInventory] = []
    for path in sorted(TEMPLATES_DIR.rglob("*.html")):
        text = _read_text(path)
        refs = list(TEMPLATE_REF_RE.findall(text)) + list(TEMPLATE_FROM_RE.findall(text))
        assets = list(ASSET_URL_RE.findall(text))
        assets.extend(_normalize_static_ref(match) for match in STATIC_REF_RE.findall(text))
        templates.append(
            TemplateInventory(
                path=path.relative_to(TEMPLATES_DIR).as_posix(),
                extends=tuple(ref for ref in _dedupe(refs) if ref.endswith(".html")),
                includes=tuple(ref for ref in _dedupe(refs) if ref.endswith(".html")),
                assets=_dedupe(assets),
                vite_entries=_dedupe(list(VITE_ENTRY_RE.findall(text))),
                islands=_dedupe(list(ISLAND_RE.findall(text))),
            )
        )
    return templates


def collect_routers() -> list[RouterInventory]:
    routers: list[RouterInventory] = []
    for path in sorted(ROUTERS_DIR.rglob("*.py")):
        text = _read_text(path)
        routes = [f"{method.upper()} {route}" for _, method, route in ROUTE_RE.findall(text)]
        templates = HTML_NAME_RE.findall(text) if "TemplateResponse" in text else []
        routers.append(
            RouterInventory(
                path=_rel(path),
                route_count=len(routes),
                route_samples=tuple(routes[:8]),
                templates=_dedupe(templates),
            )
        )
    return routers


def collect_vite_manifest() -> dict[str, dict]:
    manifest_path = STATIC_DIR / "dist" / "manifest.json"
    if not manifest_path.is_file():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def collect_static_scripts() -> list[tuple[str, int, int]]:
    template_assets = []
    for template in collect_templates():
        template_assets.extend(template.assets)

    scripts: list[tuple[str, int, int]] = []
    for path in sorted((STATIC_DIR / "js").glob("*.js")):
        rel_path = path.relative_to(STATIC_DIR).as_posix()
        referenced_count = sum(1 for asset in template_assets if _normalize_static_ref(asset) == rel_path)
        scripts.append((rel_path, path.stat().st_size, referenced_count))
    return scripts


def classify_template(template_path: str) -> str:
    lowered = template_path.lower()
    for label, needles in WORKFLOW_RULES:
        if any(needle in lowered for needle in needles):
            return label
    return "其他页面"


def _format_list(values: tuple[str, ...], limit: int = 5) -> str:
    if not values:
        return "-"
    visible = list(values[:limit])
    suffix = f" 等 {len(values)} 项" if len(values) > limit else ""
    return "`" + "`, `".join(visible) + "`" + suffix


def _format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def render_inventory() -> str:
    templates = collect_templates()
    routers = collect_routers()
    vite_manifest = collect_vite_manifest()
    scripts = collect_static_scripts()
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines: list[str] = [
        "# LanShare 前端迁移清单",
        "",
        f"> 生成时间：{generated_at}（Asia/Shanghai）。",
        "> 生成命令：`python tools/frontend_migration_inventory.py`。",
        "",
        "## 用途",
        "",
        "这个清单是前端现代化迁移的功能防漏基线。迁移任何页面前，必须先确认对应模板、脚本、路由和业务入口已经在这里被识别；迁移后再用它对照检查入口是否消失、脚本是否重复、Vite island 是否按页面加载。",
        "",
        "## 当前 Vite Islands",
        "",
        "| 入口 | 产物 | 共享依赖 |",
        "| --- | --- | --- |",
    ]

    if vite_manifest:
        for entry_name, entry in sorted(vite_manifest.items()):
            if not entry.get("isEntry"):
                continue
            imports = tuple(str(item) for item in entry.get("imports", []))
            lines.append(
                f"| `{entry_name}` | `{entry.get('file', '-')}` | {_format_list(imports, limit=4)} |"
            )
    else:
        lines.append("| - | 尚未生成 `static/dist/manifest.json` | - |")

    lines.extend(
        [
            "",
            "## 业务域覆盖图",
            "",
            "| 业务域 | 模板数量 | 关键模板 | 关键脚本线索 |",
            "| --- | ---: | --- | --- |",
        ]
    )

    grouped_templates: dict[str, list[TemplateInventory]] = {}
    for template in templates:
        grouped_templates.setdefault(classify_template(template.path), []).append(template)

    for label in sorted(grouped_templates):
        items = grouped_templates[label]
        key_templates = tuple(item.path for item in items[:6])
        asset_refs: list[str] = []
        for item in items:
            asset_refs.extend(asset for asset in item.assets if asset.startswith("js/"))
        lines.append(
            f"| {label} | {len(items)} | {_format_list(key_templates, limit=6)} | {_format_list(_dedupe(asset_refs), limit=6)} |"
        )

    lines.extend(
        [
            "",
            "## 模板清单",
            "",
            "| 模板 | 继承/引用 | 静态脚本与样式 | Vite 入口 | Island |",
            "| --- | --- | --- | --- | --- |",
        ]
    )

    for template in templates:
        lines.append(
            "| "
            f"`{template.path}` | "
            f"{_format_list(template.includes, limit=4)} | "
            f"{_format_list(template.assets, limit=5)} | "
            f"{_format_list(template.vite_entries, limit=3)} | "
            f"{_format_list(template.islands, limit=3)} |"
        )

    lines.extend(
        [
            "",
            "## 路由与模板线索",
            "",
            "| 路由文件 | 路由数量 | 路由样例 | 模板线索 |",
            "| --- | ---: | --- | --- |",
        ]
    )

    for router in routers:
        if router.route_count == 0 and not router.templates:
            continue
        lines.append(
            f"| `{router.path}` | {router.route_count} | {_format_list(router.route_samples, limit=4)} | {_format_list(router.templates, limit=5)} |"
        )

    lines.extend(
        [
            "",
            "## 高风险传统脚本",
            "",
            "这些脚本体积较大或被多个模板引用，后续迁移前应先补对应 Playwright/接口冒烟测试。",
            "",
            "| 脚本 | 大小 | 模板引用次数 |",
            "| --- | ---: | ---: |",
        ]
    )

    for rel_path, size, referenced_count in sorted(scripts, key=lambda item: item[1], reverse=True)[:18]:
        lines.append(f"| `{rel_path}` | {_format_size(size)} | {referenced_count} |")

    lines.extend(
        [
            "",
            "## 迁移验收规则",
            "",
            "1. 模板迁移前：在“模板清单”中定位原模板、静态脚本、Vite 入口和 island。",
            "2. 路由迁移前：在“路由与模板线索”中确认相关 router 文件、HTTP 方法和模板响应。",
            "3. 传统脚本迁移前：先记录旧脚本公开的全局函数、DOM 选择器、`data-*` 协议和接口路径。",
            "4. React island 上线后：必须确认旧入口仍可达，旧权限仍由后端判定，旧 API 错误态仍能显示。",
            "5. 每次迁移完成后：重新运行本工具并检查 Vite 入口、模板引用和高风险脚本体积是否符合预期。",
        ]
    )

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the LanShare frontend migration inventory.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Markdown output path.")
    args = parser.parse_args()

    output = args.output
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_inventory(), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
