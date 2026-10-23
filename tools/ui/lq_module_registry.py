"""LQ 模块使用记录本（docs/lq-module-registry.{md,json}）生成器。

只读扫描源码，不导入应用、不连数据库。回答两个问题：
  1. 平台有哪些液态玻璃共享模块（static/css/lq/components/*.css 一文件一模块，
     加上 pages/*.css 页面适配层），每个模块的入口（Jinja 宏 / JS 工厂 / Python props /
     React 适配器）和根类是什么；
  2. 每个模块被哪些模板、JS、React、后端 HTML 消费——改模块前先看这里评估影响面。

设计摘要、参数与规则由人工维护在 docs/lq-module-registry.notes.json；本脚本只合并，
不臆造。改任何 lq 模块或其消费者后重新生成：

    python tools/ui/lq_module_registry.py            # 重写 docs/lq-module-registry.{md,json}
    python tools/ui/lq_module_registry.py --check    # 与现有 JSON 比对，漂移则退出 1
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPONENT_CSS = ROOT / "static/css/lq/components"
PAGE_CSS = ROOT / "static/css/lq/pages"
MACROS = ROOT / "templates/macros/lq"
JS_DIR = ROOT / "static/js/lq"
PY_DIR = ROOT / "classroom_app"
REACT_ADAPTER = ROOT / "frontend/src/components/lq-presentation.tsx"
NOTES = ROOT / "docs/lq-module-registry.notes.json"
OUT_JSON = ROOT / "docs/lq-module-registry.json"
OUT_MD = ROOT / "docs/lq-module-registry.md"

# 模块 stem -> 入口文件 stem 的非同名映射（同名的自动匹配；None = 无 JS 入口）。
JS_ALIAS = {"button": "components", "chip": "components", "indicators": "components", "skeleton": "components",
            "layers": "layer", "lightbox": None, "search": None, "emoji-picker": None,
            "material-boundaries": None, "control-states": None, "control-geometry": None, "declarative": "domain-controls",
            "motion": None, "page-backdrop": None, "prose-copy": None, "react-bridges": None}
PY_ALIAS = {"button": "lq_components", "chip": "lq_components", "indicators": "lq_components", "skeleton": "lq_components",
            "menus": "lq_menu_tooltip", "tooltips": "lq_menu_tooltip", "nav-menu": "lq_nav_menu", "chip-row": "lq_chip_row"}
# data-lq-component="kind" / createComponent('kind') / lq_props('kind') -> 模块
KIND_MODULE = {
    "button": "button", "chip": "chip", "badge": "indicators", "avatar": "indicators", "spinner": "indicators",
    "progress": "indicators", "skeleton": "skeleton",
    "input": "forms", "textarea": "forms", "select": "forms", "checkbox": "forms", "radio": "forms", "switch": "forms",
    "range": "forms", "field": "forms", "file": "forms", "color": "forms", "date": "forms", "form_section": "forms",
    "form_actions": "forms", "error_summary": "forms",
    "surface": "content", "card": "content", "row": "content", "list": "content", "empty": "content", "bubble": "content",
    "prose": "content", "page_head": "content", "filter_bar": "content", "panel": "content",
    "dialog": "dialogs", "sheet": "dialogs", "drawer": "dialogs", "confirm": "dialogs", "choose": "dialogs", "modal": "dialogs",
    "menu": "menus", "popover": "menus", "tooltip": "tooltips", "tab": "navigation", "tabs": "navigation", "segment": "navigation",
    "toolbar": "declarative", "choice": "declarative", "region": "declarative", "collapsible": "collapsible",
    "combobox": "selection", "listbox": "selection", "composer": "composer", "upload": "upload", "dropzone": "upload",
    "status": "status", "save_status": "status", "alert": "status", "conflict": "status",
    "table": "tables", "pager": "tables", "bulk_bar": "tables", "nav_menu": "nav-menu", "chip_row": "chip-row",
    "split": "workspace", "viewer": "workspace", "shell": "shells", "topbar": "shells", "sidebar": "shells", "dock": "shells",
    "fab": "shells", "crumbs": "shells", "steps": "shells", "nav_item": "shells", "editor": "shells", "page_layout": "shells",
    "deadline_clock": "business", "job_status": "business", "question_navigator": "business",
    "avatar_stack": "insights", "insight_ring": "insights", "insight_bars": "insights", "insight_meter": "insights",
    "toast": "toast", "lightbox": "lightbox",
}
CONSUMER_GLOBS = (
    ("templates", "**/*.html", ("templates/macros/lq/",)),
    ("static/js", "**/*.js", ("static/js/lq/", "static/js/vendor", ".min.js", "static/js/echarts")),
    ("frontend/src", "**/*.ts*", ("frontend/src/components/lq-presentation.tsx", ".test.", "/__tests__/")),
    ("classroom_app", "**/*.py", ("classroom_app/lq", "__pycache__")),
)
CLASS_RE = re.compile(r"(?<![\w-])(lq-[a-z0-9]+(?:-[a-z0-9]+)*)")
DEF_RE = re.compile(r"\.(lq-[a-z0-9]+(?:-[a-z0-9]+)*)")  # 只认选择器里的类，不认 keyframes/变量名
MATERIALS_CSS = ROOT / "static/css/lq/materials.css"
FAMILY_RE = re.compile(r"^(lq-[a-z0-9]+(?:-[a-z0-9]+)*?)(?:__|--|$)")
KIND_RE = re.compile(r"""data-lq-component\s*=\s*["']([a-z_]+)["']|create(?:Component|Content|Form|Dialog|Status|Shell|Workspace|Table|Business|Insight|Upload|Navigation|Selection|Composer|ChipRow|Collapsible|Menu|Tooltip|NavMenu)\(\s*['"]([a-z_]+)['"]|lq_props\(\s*['"]([a-z_]+)['"]""")
MACRO_CALL_RE = re.compile(r"\b(lq_[a-z_]+)\s*\(")
IMPORT_RE = re.compile(r"""['"](?:\.{1,2}/)+lq/([a-z_-]+)\.js['"]""")
REACT_RE = re.compile(r"\b(Lq[A-Z][A-Za-z]+)\b")
PAGE_LINK_RE = re.compile(r"lq/pages/([a-z-]+)\.css")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def family(name: str) -> str:
    match = FAMILY_RE.match(name)
    return match.group(1) if match else name


def excluded(rel: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in rel for pattern in patterns)


def discover_modules(notes: dict) -> dict[str, dict]:
    """每个 components/*.css 是一个模块；根类按「谁定义得最多」归属。"""
    modules: dict[str, dict] = {}
    family_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for css in [MATERIALS_CSS, *sorted(COMPONENT_CSS.glob("*.css"))]:
        key = css.stem
        text = re.sub(r"/\*.*?\*/", "", read(css), flags=re.S)
        for match in DEF_RE.finditer(text):
            family_counts[family(match.group(1))][key] += 1
        modules[key] = {"css": css.relative_to(ROOT).as_posix(), "roots": [], "extraRoots": list(notes.get(key, {}).get("extraSelectors", []))}
    # 页面适配层定义的类（.lq-messages / .lq-profile …）是页面根，不归任何共享模块。
    page_families = {family(m.group(1)) for css in PAGE_CSS.glob("*.css") for m in DEF_RE.finditer(re.sub(r"/\*.*?\*/", "", read(css), flags=re.S))}
    for fam, owners in family_counts.items():
        owner = max(sorted(owners), key=lambda k: owners[k])
        if fam in page_families and family_counts[fam][owner] < 4:
            continue
        modules[owner]["roots"].append(fam)
    for key, module in modules.items():
        forced = set(notes.get(key, {}).get("forceRoots", []))
        for other in modules.values():
            other["roots"] = [root for root in other["roots"] if root not in forced]
        module["roots"] = sorted(set(module["roots"]) | forced)
    return modules


def macro_names(path: Path) -> list[str]:
    return [name for name in re.findall(r"{%\s*macro\s+([a-z_]+)", read(path)) if not name.startswith("_")]


def js_exports(path: Path) -> list[str]:
    return sorted(set(re.findall(r"^export\s+(?:async\s+)?(?:function|const|let|class)\s+([A-Za-z_$][\w$]*)", read(path), flags=re.M)))


def react_exports() -> list[str]:
    return sorted(set(re.findall(r"^export\s+(?:const|function)\s+(Lq[A-Za-z]+)", read(REACT_ADAPTER), flags=re.M)))


def entries_for(key: str, notes: dict, react_names: list[str]) -> dict:
    override = notes.get(key, {}).get("entries", {})
    macro_file = MACROS / f"{key}.html"
    js_stem = JS_ALIAS.get(key, key)
    js_file = JS_DIR / f"{js_stem}.js" if js_stem else None
    py_stem = PY_ALIAS.get(key, f"lq_{key.replace('-', '_')}")
    py_file = PY_DIR / f"{py_stem}.py"
    result = {
        "macros": {macro_file.relative_to(ROOT).as_posix(): macro_names(macro_file)} if macro_file.exists() else {},
        "js": {js_file.relative_to(ROOT).as_posix(): js_exports(js_file)} if js_file and js_file.exists() else {},
        "python": py_file.relative_to(ROOT).as_posix() if py_file.exists() else None,
        "react": [name for name in override.get("react", []) if name in react_names],
    }
    for extra in override.get("js", []):
        path = ROOT / extra
        if path.exists():
            result["js"][extra] = js_exports(path)
    for extra in override.get("macros", []):
        path = ROOT / extra
        if path.exists():
            result["macros"][extra] = macro_names(path)
    return result


def scan_consumers(modules: dict[str, dict], react_map: dict[str, str], macro_map: dict[str, str]) -> dict[str, dict[str, int]]:
    """文件 -> {模块: 命中次数}。命中来源：根类、data-lq-component/工厂 kind、宏调用、lq/*.js 导入、React 适配器名。"""
    root_map: dict[str, str] = {}
    for key, module in modules.items():
        for root in module["roots"]:
            root_map[root] = key
        for selector in module["extraRoots"]:
            root_map[selector.lstrip(".")] = key
    js_stem_map = {JS_ALIAS.get(key, key): key for key in modules if JS_ALIAS.get(key, key)}
    hits: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for base, pattern, excludes in CONSUMER_GLOBS:
        for path in sorted((ROOT / base).glob(pattern)):
            rel = path.relative_to(ROOT).as_posix()
            if not path.is_file() or excluded(rel, excludes):
                continue
            text = read(path)
            for match in CLASS_RE.finditer(text):
                owner = root_map.get(family(match.group(1)))
                if owner:
                    hits[rel][owner] += 1
            for selector, owner in root_map.items():
                if not selector.startswith("lq-") and re.search(rf"(?<![\w-]){re.escape(selector)}(?![\w-])", text):
                    hits[rel][owner] += 1
            for match in KIND_RE.finditer(text):
                kind = next(group for group in match.groups() if group)
                owner = KIND_MODULE.get(kind)
                if owner:
                    hits[rel][owner] += 1
            for match in MACRO_CALL_RE.finditer(text):
                owner = macro_map.get(match.group(1))
                if owner:
                    hits[rel][owner] += 1
            for match in IMPORT_RE.finditer(text):
                owner = js_stem_map.get(match.group(1))
                if owner:
                    hits[rel][owner] += 2
            if rel.endswith((".tsx", ".ts")):
                for match in REACT_RE.finditer(text):
                    owner = react_map.get(match.group(1))
                    if owner:
                        hits[rel][owner] += 1
    return hits


def discover_pages() -> dict[str, dict]:
    pages: dict[str, dict] = {}
    for css in sorted(PAGE_CSS.glob("*.css")):
        pages[css.stem] = {"css": css.relative_to(ROOT).as_posix(), "linkedBy": []}
    index = read(ROOT / "static/css/lq/index.css")
    for path in sorted((ROOT / "templates").glob("**/*.html")):
        rel = path.relative_to(ROOT).as_posix()
        for stem in sorted(set(PAGE_LINK_RE.findall(read(path)))):
            if stem in pages:
                pages[stem]["linkedBy"].append(rel)
    for stem, page in pages.items():
        page["bundledInIndex"] = f"pages/{stem}.css" in index
    return pages


def build() -> dict:
    notes = json.loads(read(NOTES)) if NOTES.exists() else {}
    modules = discover_modules(notes)
    react_names = react_exports()
    macro_map: dict[str, str] = {}
    react_map: dict[str, str] = {}
    for key, module in modules.items():
        module["entries"] = entries_for(key, notes, react_names)
        for names in module["entries"]["macros"].values():
            for name in names:
                macro_map[name] = key
        for name in module["entries"]["react"]:
            react_map[name] = key
    hits = scan_consumers(modules, react_map, macro_map)
    by_module: dict[str, list[dict]] = defaultdict(list)
    for rel, owners in hits.items():
        for owner, count in owners.items():
            by_module[owner].append({"path": rel, "hits": count})
    for key, module in modules.items():
        note = notes.get(key, {})
        files = sorted(by_module.get(key, []), key=lambda item: (-item["hits"], item["path"]))
        module.update({
            "title": note.get("title", key),
            "layer": note.get("layer", "component"),
            "notes": {field: note.get(field, "") for field in ("summary", "params", "states", "rules")},
            "consumers": {"count": len(files), "files": files},
        })
    return {
        "generatedAt": date.today().isoformat(),
        "generator": "python tools/ui/lq_module_registry.py",
        "modules": modules,
        "pages": discover_pages(),
        "consumersByFile": {rel: sorted(owners) for rel, owners in sorted(hits.items())},
        "limitations": [
            "源码扫描只证明「引用了模块的类/宏/工厂」，不证明运行时合同与业务验收；视觉与交互仍需浏览器证据。",
            "后端拼接的 HTML 字符串、动态类名与第三方 iframe 内容可能漏扫；新增模块时在 notes 里登记 extraSelectors。",
        ],
    }


LAYER_ORDER = {"foundation": 0, "component": 1, "composite": 2}


def module_order(modules: dict) -> list[str]:
    return sorted(modules, key=lambda k: (LAYER_ORDER.get(modules[k]["layer"], 9), k))


def render_md(data: dict) -> str:
    lines = [
        "# LQ 模块使用记录本",
        "",
        f"生成于 {data['generatedAt']}，命令 `{data['generator']}`（源码只读扫描；设计说明来自 `docs/lq-module-registry.notes.json`，由人工维护）。",
        "",
        "## 怎么用",
        "",
        "1. **改模块前**：找到模块条目，看「消费者」里列出的模板 / JS / React / 后端文件，逐个判断这次改动会不会破坏它们的布局、状态或业务钩子；影响面大的先写改进计划再动手。",
        "2. **改模块后**：重新运行生成器，把新的消费者、入口或根类记录进来，并在 notes 里更新设计摘要；`--check` 可作门禁，漂移即失败。",
        "3. **新增模块**：先在 notes 里写标题、摘要、参数、状态与规则，再提供 CSS / 宏 / JS / React 入口与至少一个真实消费者；不要先在页面里写私有组件。",
        "4. **页面只做布局**：`static/css/lq/pages/*.css` 是页面适配层，只允许摆放共享模块与留白，不得重新配色或造第二套材质；见文末「页面适配层」。",
        "",
        "设计原则真源：[液态玻璃组件开发合同](lq-component-contract-2026-09-27.md)、[组件 API](lq-components.md)、[场景色调契约](lq-scene-ink-2026-09-26.md)、[模板增补](lq-ui-template-sweep-2026-10-13.md)。",
        "",
        "## 总览",
        "",
        "| 模块 | 层 | 根类 | 入口 | 消费者文件数 |",
        "|---|---|---|---|---|",
    ]
    modules = data["modules"]
    for key in module_order(modules):
        module = modules[key]
        entry_bits = []
        if module["entries"]["macros"]:
            entry_bits.append("宏")
        if module["entries"]["js"]:
            entry_bits.append("JS")
        if module["entries"]["python"]:
            entry_bits.append("Py")
        if module["entries"]["react"]:
            entry_bits.append("React")
        all_roots = module["roots"] + module["extraRoots"]
        roots = ", ".join(f"`{root}`" for root in all_roots[:6]) or "—"
        if len(all_roots) > 6:
            roots += " …"
        lines.append(f"| [{module['title']}](#{key}) | {module['layer']} | {roots} | {' / '.join(entry_bits) or '仅 CSS'} | {module['consumers']['count']} |")
    lines += ["", "## 模块", ""]
    for key in module_order(modules):
        module = modules[key]
        lines += [f"### {key}", "", f"**{module['title']}** · 层：{module['layer']} · CSS：`{module['css']}`", ""]
        for label, field in (("设计摘要", "summary"), ("参数 / 变体", "params"), ("状态", "states"), ("规则", "rules")):
            if module["notes"].get(field):
                lines.append(f"- **{label}**：{module['notes'][field]}")
        roots = module["roots"] + module["extraRoots"]
        if roots:
            lines.append("- **根类**：" + ", ".join(f"`{root}`" for root in roots))
        entries = module["entries"]
        for path, names in entries["macros"].items():
            lines.append(f"- **Jinja 宏** `{path}`：" + ", ".join(f"`{name}`" for name in names))
        for path, names in entries["js"].items():
            lines.append(f"- **JS 入口** `{path}`：" + ", ".join(f"`{name}`" for name in names[:14]) + (" …" if len(names) > 14 else ""))
        if entries["python"]:
            lines.append(f"- **Python props** `{entries['python']}`")
        if entries["react"]:
            lines.append("- **React 适配器**：" + ", ".join(f"`{name}`" for name in entries["react"]) + f"（`{REACT_ADAPTER.relative_to(ROOT).as_posix()}`）")
        files = module["consumers"]["files"]
        lines.append(f"- **消费者**：{len(files)} 个文件")
        if files:
            lines += ["", "<details><summary>消费者清单（按命中次数）</summary>", ""]
            lines += [f"- `{item['path']}` ×{item['hits']}" for item in files]
            lines += ["", "</details>"]
        lines.append("")
    lines += ["## 页面适配层（static/css/lq/pages）", "", "| 页面 CSS | 链接它的模板 | 并入 index.css |", "|---|---|---|"]
    for stem, page in data["pages"].items():
        linked = ", ".join(f"`{path}`" for path in page["linkedBy"]) or "（仅 index.css 或未直接链接）"
        lines.append(f"| `{page['css']}` | {linked} | {'是' if page['bundledInIndex'] else '否'} |")
    lines += ["", "## 局限", ""] + [f"- {item}" for item in data["limitations"]] + [""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only compare with the committed JSON")
    args = parser.parse_args()
    data = build()
    if args.check:
        if not OUT_JSON.exists():
            print("registry missing; run without --check", file=sys.stderr)
            return 1
        current = json.loads(read(OUT_JSON))
        current.pop("generatedAt", None)
        fresh = {k: v for k, v in data.items() if k != "generatedAt"}
        if current != fresh:
            print("lq-module-registry drift: regenerate with python tools/ui/lq_module_registry.py", file=sys.stderr)
            return 1
        print("lq-module-registry up to date")
        return 0
    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    OUT_MD.write_text(render_md(data), encoding="utf-8", newline="\n")
    print(json.dumps({"modules": len(data["modules"]), "pages": len(data["pages"]), "consumerFiles": len(data["consumersByFile"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
