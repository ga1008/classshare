"""Conservative, source-only inventory; generated hints are never acceptance proof."""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

STATUSES = ("未盘点", "已盘点", "组件就绪", "迁移中", "业务验收通过", "视觉验收通过", "待发布", "已发布", "旧代码可删除")
MANUAL_FIELDS = ("roles", "resourceScope", "layout", "controller", "controllerNotes", "migrationScope", "generatedAssets", "apis", "versionFields", "states", "components", "before", "tests", "migrationFlag", "sharedCssConsumers", "exceptions", "rollbackUnit", "status", "notes")
REF = re.compile(r"{%\s*(?:extends|include|from|import)\s+['\"]([^'\"]+\.html)['\"]")
ASSET = re.compile(r"asset_url\(\s*['\"]([^'\"]+)['\"]|['\"]/static/([^'\"?]+)")


def string(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def scan_routes(source: str, filename: str) -> list[dict]:
    tree = ast.parse(source, filename=filename)
    functions = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    prefixes = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        if ast.unparse(node.value.func).endswith("APIRouter"):
            prefix = next((string(k.value) for k in node.value.keywords if k.arg == "prefix"), "")
            for target in node.targets:
                if isinstance(target, ast.Name):
                    prefixes[target.id] = prefix or ""
    result = []
    for fn in tree.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = [d for d in fn.decorator_list if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr.lower() in {"get", "post"} and d.args and string(d.args[0])]
        if not decorators:
            continue
        # Follow local render helpers without attaching every template in a module
        # to every endpoint. Keep cross-module/dynamic registration limitations explicit.
        pending, visited, nodes = [fn], set(), []
        while pending:
            current = pending.pop()
            if current.name in visited:
                continue
            visited.add(current.name)
            nodes.extend(ast.walk(current))
            for node in ast.walk(current):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions:
                    pending.append(functions[node.func.id])
        templates = sorted({n.value for n in nodes if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.endswith(".html") and n.value != ".html" and "\n" not in n.value})
        patterns = sorted({"".join(v.value if isinstance(v, ast.Constant) else "*" for v in n.values) for n in nodes if isinstance(n, ast.JoinedStr) and n.values and isinstance(n.values[-1], ast.Constant) and str(n.values[-1].value).endswith(".html")})
        calls = [n for n in nodes if isinstance(n, ast.Call)]
        dynamic = any(ast.unparse(n.func).endswith("TemplateResponse") and not any(string(a) and string(a).endswith(".html") for a in [*n.args, *(k.value for k in n.keywords)]) for n in calls)
        dependencies = sorted({ast.unparse(n.args[0]) for n in calls if ast.unparse(n.func) == "Depends" and n.args})
        for d in decorators:
            result.append({"routePattern": prefixes.get(ast.unparse(d.func.value), "") + string(d.args[0]), "method": d.func.attr.upper(), "source": filename, "line": fn.lineno, "endpoint": fn.name, "templates": templates, "templatePatterns": patterns, "dependencies": dependencies, "dynamicTemplate": dynamic})
    return result


def build_registry(root: Path, previous: dict | None = None) -> dict:
    asset_manifest_path = root / "static/vendor/manifest.json"
    asset_manifest = json.loads(asset_manifest_path.read_text(encoding="utf-8")) if asset_manifest_path.is_file() else {}
    templates = {p.relative_to(root / "templates").as_posix(): p.read_text(encoding="utf-8") for p in sorted((root / "templates").rglob("*.html"))}
    refs = {name: sorted(set(REF.findall(text))) for name, text in templates.items()}
    routes = [r for p in sorted((root / "classroom_app" / "routers").rglob("*.py")) for r in scan_routes(p.read_text(encoding="utf-8"), p.relative_to(root).as_posix())]
    from fnmatch import fnmatchcase
    for route in routes:
        route["templates"] = sorted(set(route["templates"]) | {name for name in templates if any(fnmatchcase(name, pattern) for pattern in route["templatePatterns"])})
    owners = {name: [] for name in templates}
    for route in routes:
        pending, visited = list(route["templates"]), set()
        while pending:
            name = pending.pop()
            if name in visited or name not in templates:
                continue
            visited.add(name)
            owners[name].append(route["routePattern"])
            pending.extend(refs[name])
    prior = {e["id"]: e for e in (previous or {}).get("entries", [])}
    entries = []
    for name, text in templates.items():
        direct = [r for r in routes if name in r["templates"]]
        kind = "macro" if name.startswith("macros/") else "partial" if name.startswith("partials/") else "document" if re.search(r"<!doctype|<html\b", text, re.I) else "template"
        assets = sorted({asset_manifest.get(a or b, {}).get("path", a or b) for a, b in ASSET.findall(text)})
        for route in direct or [None]:
            identity = f"{route['method']} {route['routePattern']}::{name}" if route else f"template::{name}"
            entry = {
                "id": identity, "routePattern": route["routePattern"] if route else None,
                "method": route["method"] if route else None, "template": f"templates/{name}", "kind": kind,
                "ownerRoutes": sorted(set(owners[name])), "routeEvidence": route,
                "roles": [], "resourceScope": "待人工核对端点及服务端权限", "layout": "待核对",
                "assets": assets, "islands": sorted(set(re.findall(r'data-lanshare-island=[\"\']([^\"\']+)', text))),
                "templateReferences": refs[name], "controller": [],
                "domHooks": sorted(set(re.findall(r'\b(?:id|data-testid)=[\"\']([^\"\'{}]+)[\"\']', text))),
                "apis": [], "versionFields": sorted(set(re.findall(r'\b(?:expected_\w+|SUBMISSION_VERSION)\b', text))),
                "states": [], "components": [], "inventorySourceSha256": hashlib.sha256(text.encode()).hexdigest(),
                "before": {"sourceSha256": None, "screenshots": [], "status": "未采集；当前盘点源码不能冒充改动前快照"},
                "tests": [], "migrationFlag": None, "sharedCssConsumers": [], "exceptions": [],
                "rollbackUnit": {"templates": [f"templates/{name}"], "assets": assets, "schema": [], "verified": False},
                "status": "未盘点", "notes": [],
            }
            if identity in prior:
                for key in MANUAL_FIELDS:
                    if key in prior[identity]:
                        entry[key] = prior[identity][key]
                # Partial scopes include reviewed transitive runtime dependencies
                # that a direct-template regex cannot rediscover. Keep those
                # positive records until their owner explicitly reviews removal.
                if (entry.get("migrationScope") or {}).get("kind") == "partial":
                    entry["assets"] = sorted(set(assets) | set(prior[identity].get("assets", [])))
            entries.append(entry)
    current = {e["id"] for e in entries}
    removed = [e for e in (previous or {}).get("entries", []) if e["id"] not in current]
    removed.extend((previous or {}).get("retiredEntries", []))
    removed.extend((previous or {}).get("supersededInventoryEntries", []))
    removed = list({e["id"]: e for e in removed if e["id"] not in current}.values())
    retired = [e for e in removed if e["template"].removeprefix("templates/") not in templates]
    superseded = [e for e in removed if e not in retired]
    return {
        "schemaVersion": 1,
        "generator": "python tools/frontend_migration_inventory.py --lq-registry",
        "statusOrder": list(STATUSES),
        "coverage": {"templateCount": len(templates), "entryCount": len(entries), "unresolvedTemplates": [n for n in templates if not owners[n]], "dynamicTemplateEndpoints": [r for r in routes if r["dynamicTemplate"]]},
        "limitations": ["AST routes and dependencies are source hints; helper-generated templates, external include_router prefixes and dynamic templates require manual verification.", "An inventoried file is not a migrated or accepted page. Regeneration preserves manual state and before evidence.", "Removed entries remain in retiredEntries for explicit retirement review."],
        "entries": entries,
        "retiredEntries": retired,
        "supersededInventoryEntries": superseded,
    }


def write_registry(root: Path, output: Path) -> dict:
    previous = json.loads(output.read_text(encoding="utf-8")) if output.exists() else None
    registry = build_registry(root, previous)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return registry
