"""Fill derivable fields for '未盘点' registry entries and mark them '已盘点'.

Only entries whose status is 未盘点 are touched. Manually curated entries keep
every field. Derived values are recorded in `notes` so reviewers can tell them
apart from human-verified statements. `states`/`components` stay empty: they
are filled when a page is actually migrated, not by static inference.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "docs" / "lq-migration-registry.json"

EXTENDS_RE = re.compile(r"""{%\s*extends\s+["']([^"']+)["']""")
COND_EXTENDS_RE = re.compile(r"""{%\s*extends\s+["']([^"']+)["']\s+if\s.+?else\s+["']([^"']+)["']""")
INCLUDE_RE = re.compile(r"""{%\s*include\s+["']([^"']+)["']""")
ASSET_URL_RE = re.compile(r"""asset_url\(\s*["']([^"']+)["']""")
STATIC_RE = re.compile(r"""/static/((?:js|css|vendor|dist)/[^"'?\s)]+)""")
ISLAND_RE = re.compile(r"""data-lanshare-island=["']([^"']+)["']""")
VITE_RE = re.compile(r"""vite_entry_tags\(\s*["']([^"']+)["']""")
DATA_ATTR_RE = re.compile(r"""\s(data-[a-z0-9-]+)=""")
API_RE = re.compile(r"""['"`](/api/[A-Za-z0-9_./{}$-]+)""")
VERSION_RE = re.compile(
    r"""\b(expected_[a-z_]*(?:version|revision|updated_at)|submission_version"""
    r"""|render_revision|revision|version_token|context_token)\b"""
)
IMPORT_RE = re.compile(r"""import\s+(?:[^'"]*?from\s+)?['"]([^'"]+)['"]""")

LAYOUTS = {
    "manage/layout.html": "sidebar（manage/layout.html）",
    "base_navbar.html": "topbar（base_navbar.html）",
    "base_centered.html": "centered（base_centered.html）",
    "resume/layout.html": "resume 独立壳（resume/layout.html）",
    "base.html": "base.html 直接派生（immersive/自定义）",
}


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def extends_chain(template: str, seen: set[str] | None = None) -> list[str]:
    seen = seen if seen is not None else set()
    if template in seen:
        return []
    seen.add(template)
    text = read(ROOT / "templates" / template)
    match = COND_EXTENDS_RE.search(text)
    if match:
        bases = [match.group(1), match.group(2)]
    else:
        match = EXTENDS_RE.search(text)
        bases = [match.group(1)] if match else []
    chain: list[str] = []
    for base in bases:
        chain.append(base)
        chain.extend(extends_chain(base, seen))
    return chain


def layout_for(chain: list[str], template: str) -> str:
    for base in chain:
        if base in LAYOUTS:
            return LAYOUTS[base]
    if chain:
        return "待核对：" + " → ".join(chain)
    if template.startswith(("partials/", "macros/")):
        return "片段/宏（无 extends，随宿主页渲染）"
    return "独立完整文档（无 extends；lq-editor 族或独立页）"


def collect_assets(template: str) -> tuple[list[str], list[str], list[str]]:
    """Assets referenced by the template and its own includes (not its shell)."""
    assets: list[str] = []
    islands: list[str] = []
    texts: list[str] = []
    stack = [template]
    seen: set[str] = set()
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        text = read(ROOT / "templates" / name)
        texts.append(text)
        for inc in INCLUDE_RE.findall(text):
            if inc.startswith(("partials/", "macros/")):
                stack.append(inc)
        assets.extend(ref.split("?")[0] for ref in ASSET_URL_RE.findall(text))
        assets.extend(ref.split("?")[0] for ref in STATIC_RE.findall(text))
        islands.extend(ISLAND_RE.findall(text))
        islands.extend(VITE_RE.findall(text))
    return sorted(set(assets)), sorted(set(islands)), texts


def module_closure(assets: list[str]) -> list[str]:
    seen: set[str] = set()
    stack = ["static/" + a for a in assets if a.startswith("js/") and (ROOT / "static" / a).is_file()]
    while stack:
        rel = stack.pop()
        if rel in seen:
            continue
        seen.add(rel)
        path = ROOT / rel
        if not path.is_file():
            continue
        for spec in IMPORT_RE.findall(read(path)):
            spec = spec.split("?")[0]
            if spec.startswith("/static/"):
                target = "static/" + spec[len("/static/"):]
            elif spec.startswith("."):
                resolved = (path.parent / spec).resolve()
                try:
                    target = resolved.relative_to(ROOT).as_posix()
                except ValueError:
                    continue
            else:
                continue
            if (ROOT / target).is_file():
                stack.append(target)
    return sorted(seen)


def roles_for(dependencies: list[str]) -> list[str]:
    deps = " ".join(dependencies)
    roles: set[str] = set()
    if "super_admin" in deps or "require_admin" in deps:
        roles.add("super_admin")
    if "get_current_teacher" in deps or "teacher" in deps:
        roles.add("teacher")
    if "get_current_student" in deps or "student" in deps:
        roles.add("student")
    if "preference_user" in deps:
        roles.update({"student", "teacher"})
    if "get_current_user" in deps and not roles:
        roles.update({"student", "teacher"})
    return sorted(roles) if roles else ["anonymous"]


def main() -> int:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    changed = 0
    for entry in data["entries"]:
        if entry.get("status") != "未盘点":
            continue
        template = entry["template"].removeprefix("templates/")
        chain = extends_chain(template)
        assets, islands, texts = collect_assets(template)
        closure = module_closure(assets)
        corpus = "\n".join(texts + [read(ROOT / rel) for rel in closure])
        evidence = entry.get("routeEvidence") or {}
        dependencies = evidence.get("dependencies") or []
        entry["roles"] = roles_for(dependencies)
        entry["resourceScope"] = (
            ("由路由依赖推导：" + ", ".join(dependencies)) if dependencies
            else "无显式依赖（匿名或在处理函数内自行校验）"
        ) + "；资源归属待迁移时人工核对"
        entry["layout"] = layout_for(chain, template)
        entry["assets"] = assets
        entry["islands"] = islands
        entry["controller"] = closure
        entry["domHooks"] = sorted(set(DATA_ATTR_RE.findall("\n".join(texts))))[:120]
        entry["apis"] = sorted(set(API_RE.findall(corpus)))[:80]
        entry["versionFields"] = sorted(set(VERSION_RE.findall(corpus)))
        entry.setdefault("states", [])
        entry.setdefault("components", [])
        entry.setdefault("notes", []).append(
            "2026-09-21 自动盘点（tools/ui/lq_registry_triage.py）："
            "roles/layout/assets/islands/controller/domHooks/apis/versionFields 由源码静态推导；"
            "states/components 留待迁移时填写。这是盘点，不是业务或视觉验收。"
        )
        entry["status"] = "已盘点"
        changed += 1
    REGISTRY.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"triaged {changed} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
