"""Inventory route surfaces without importing or starting the LanShare application.

The AST result is a review queue, not an authorization policy or a tool registry.
Unknown expressions, disconnected routers and dynamic registrations remain visible.
Optional runtime/OpenAPI JSON snapshots reconcile what was actually mounted; this
program never imports an app, runs a factory, calls an endpoint, or fetches a URL.

Usage::

    python tools/agent_capability_inventory.py
    python tools/agent_capability_inventory.py --check
    python tools/agent_capability_inventory.py --runtime-json routes.json --openapi-json openapi.json

``snapshot_runtime_routes(already_loaded_app)`` can be called by an isolated test
harness to produce routes.json. It does not run application lifespan handlers.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from dataclasses import dataclass, field
import hashlib
import inspect
import json
from pathlib import Path
import re
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
ROUTE_METHODS = HTTP_METHODS | {"websocket", "api_route", "route", "add_api_route", "add_route", "add_websocket_route", "mount"}
UNKNOWN = object()
SCHEMA_VERSION = 1


def expression(node: ast.AST | None) -> str:
    return ast.unparse(node) if node is not None else ""


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def operation_key(method: str, path: str) -> str:
    """Stable across source line movement, sorting and handler renaming."""
    return "route." + digest(method.upper() + "\n" + path)


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    return next((item.value for item in call.keywords if item.arg == name), None)


def _callee(node: ast.AST) -> str:
    return expression(node.func) if isinstance(node, ast.Call) else expression(node)


@dataclass
class Module:
    name: str
    path: Path
    tree: ast.Module
    imports: dict[str, str] = field(default_factory=dict)
    stars: list[str] = field(default_factory=list)
    assignments: dict[str, ast.AST] = field(default_factory=dict)
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = field(default_factory=dict)


class Inventory:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.modules: dict[str, Module] = {}
        self.routers: dict[str, dict[str, Any]] = {}
        self.declarations: list[dict[str, Any]] = []
        self.includes: list[dict[str, Any]] = []
        self.issues: list[dict[str, Any]] = []
        self._scanned: set[str] = set()
        self._missing: set[str] = set()

    def source(self, module: Module, node: ast.AST) -> dict[str, Any]:
        return {"file": module.path.relative_to(self.root).as_posix(), "line": node.lineno}

    def module_path(self, name: str) -> Path | None:
        base = self.root.joinpath(*name.split("."))
        for path in (base.with_suffix(".py"), base / "__init__.py"):
            if path.is_file():
                return path
        return None

    def module_name(self, path: Path) -> str:
        parts = list(path.relative_to(self.root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)

    def import_aliases(self, module: Module, nodes: list[ast.AST]) -> tuple[dict[str, str], list[str]]:
        aliases: dict[str, str] = {}
        stars: list[str] = []
        package = module.name if module.path.name == "__init__.py" else module.name.rpartition(".")[0]
        for node in nodes:
            if isinstance(node, ast.Import):
                for item in node.names:
                    aliases[item.asname or item.name.split(".")[0]] = item.name if item.asname else item.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parts = package.split(".") if package else []
                    parent = ".".join(parts[: len(parts) - node.level + 1])
                    base = ".".join(part for part in (parent, node.module) if part)
                else:
                    base = node.module or ""
                for item in node.names:
                    if item.name == "*":
                        stars.append(base)
                    else:
                        aliases[item.asname or item.name] = ".".join(part for part in (base, item.name) if part)
        return aliases, stars

    def load(self, name: str) -> Module | None:
        if name in self.modules:
            return self.modules[name]
        if name in self._missing:
            return None
        path = self.module_path(name)
        if path is None:
            self._missing.add(name)
            return None
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except (SyntaxError, UnicodeError, OSError) as exc:
            self.issues.append({"kind": "unparsed_file", "file": path.relative_to(self.root).as_posix(), "detail": str(exc)})
            self._missing.add(name)
            return None
        module = Module(name, path, tree)
        self.modules[name] = module
        module.imports, module.stars = self.import_aliases(module, tree.body)
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        module.assignments[target.id] = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                module.assignments[node.target.id] = node.value
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                module.functions[node.name] = node
        return module

    def qualify(self, module: Module, value: str, seen: frozenset[str] = frozenset(), local: dict[str, str] | None = None) -> str:
        first, dot, rest = value.partition(".")
        marker = module.name + ":" + value
        if marker in seen:
            return module.name + "." + value
        seen = seen | {marker}
        aliases = {**module.imports, **(local or {})}
        if first in aliases:
            target = aliases[first] + (dot + rest if dot else "")
            # Resolve re-exported names, including ``from .core import app``.
            target_module, _, target_name = target.rpartition(".")
            loaded = self.load(target_module)
            if loaded and (target_name in loaded.imports or target_name in loaded.assignments):
                return self.qualify(loaded, target_name, seen)
            return target
        if first in module.assignments:
            assigned = module.assignments[first]
            if isinstance(assigned, (ast.Name, ast.Attribute)):
                return self.qualify(module, expression(assigned) + (dot + rest if dot else ""), seen, local)
            return module.name + "." + value
        if first in module.functions:
            return module.name + "." + value
        candidates: list[str] = []
        for star in module.stars:
            imported = self.load(star)
            if imported and (first in imported.imports or first in imported.assignments or first in imported.functions):
                candidates.append(self.qualify(imported, value, seen))
        if candidates:
            # Python star imports overwrite earlier bindings; module exports can
            # be dynamic, so the manifest still requires authorization review.
            return candidates[-1]
        return module.name + "." + value

    def literal(self, module: Module, node: ast.AST | None, env: dict[str, Any] | None = None, seen: frozenset[str] = frozenset()) -> Any:
        """A deliberately small evaluator: never eval(), import, or call code."""
        env = env or {}
        if node is None:
            return UNKNOWN
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in env:
                return env[node.id]
            marker = module.name + "." + node.id
            if marker in seen or len(seen) > 25:
                return UNKNOWN
            seen = seen | {marker}
            if node.id in module.assignments:
                return self.literal(module, module.assignments[node.id], env, seen)
            target = self.qualify(module, node.id)
            other_name, _, symbol = target.rpartition(".")
            other = self.load(other_name)
            if other and symbol in other.assignments:
                return self.literal(other, other.assignments[symbol], {}, seen)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            values = [self.literal(module, item, env, seen) for item in node.elts]
            return UNKNOWN if any(item is UNKNOWN for item in values) else values
        if isinstance(node, ast.Dict):
            pairs = [(self.literal(module, key, env, seen), self.literal(module, value, env, seen)) for key, value in zip(node.keys, node.values)]
            if any(key is UNKNOWN or value is UNKNOWN for key, value in pairs):
                return UNKNOWN
            try:
                return dict(pairs)
            except TypeError:
                return UNKNOWN
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = self.literal(module, node.left, env, seen), self.literal(module, node.right, env, seen)
            if isinstance(left, str) and isinstance(right, str):
                return left + right
            if isinstance(left, list) and isinstance(right, list):
                return left + right
        if isinstance(node, ast.JoinedStr):
            parts: list[str] = []
            for item in node.values:
                if isinstance(item, ast.Constant):
                    parts.append(str(item.value))
                elif isinstance(item, ast.FormattedValue) and item.format_spec is None:
                    value = self.literal(module, item.value, env, seen)
                    if value is UNKNOWN:
                        return UNKNOWN
                    parts.append(repr(value) if item.conversion == 114 else str(value))
                else:
                    return UNKNOWN
            return "".join(parts)
        if isinstance(node, ast.Subscript):
            value, key = self.literal(module, node.value, env, seen), self.literal(module, node.slice, env, seen)
            if value is not UNKNOWN and key is not UNKNOWN:
                try:
                    return value[key]
                except (KeyError, IndexError, TypeError):
                    pass
        return UNKNOWN

    def dependencies(self, module: Module, nodes: list[ast.AST | None], origin: str, local: dict[str, str] | None = None) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for node in nodes:
            if node is None:
                continue
            if isinstance(node, ast.Name) and node.id in module.assignments:
                node = module.assignments[node.id]
            for call in ast.walk(node):
                if not isinstance(call, ast.Call) or _callee(call).split(".")[-1] not in {"Depends", "Security"}:
                    continue
                dep = call.args[0] if call.args else _keyword(call, "dependency")
                dep_target = dep.func if isinstance(dep, ast.Call) else dep
                result.append({
                    "callable": self.qualify(module, expression(dep_target), local=local) if dep_target else "<annotation-inferred>",
                    "expression": expression(dep), "origin": origin, "source": self.source(module, call),
                    "scopes": expression(_keyword(call, "scopes")),
                })
        return result

    def dependency_closure(self, initial: list[dict[str, Any]]) -> list[dict[str, Any]]:
        queue = list(initial)
        seen = {item["callable"] for item in initial}
        result: list[dict[str, Any]] = []
        while queue and len(seen) < 100:
            parent = queue.pop(0)
            module_name, _, name = parent["callable"].rpartition(".")
            module = self.load(module_name)
            node = module.functions.get(name) if module else None
            if node is None:
                continue
            # Includes factory-returned dependency functions; these are hints,
            # not proof that a branch runs or that resource policy is complete.
            params: list[ast.AST] = []
            for fn in ast.walk(node):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    params.extend(self.parameter_nodes(fn))
            for item in self.dependencies(module, params, "dependency:" + parent["callable"]):
                if item["callable"] not in seen:
                    seen.add(item["callable"])
                    result.append(item)
                    queue.append(item)
        return result

    @staticmethod
    def parameter_nodes(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
        return [item for item in [*fn.args.defaults, *fn.args.kw_defaults, *(arg.annotation for arg in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs])] if item is not None]

    def handler_metadata(self, module: Module, handler: ast.AST | None) -> dict[str, Any]:
        if isinstance(handler, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn = handler
            name = module.name + "." + fn.name
        else:
            name = self.qualify(module, expression(handler))
            fn = module.functions.get(expression(handler))
        result: dict[str, Any] = {"handler": name, "dependencies": [], "authorization_candidates": [], "service_candidates": [], "parameters": []}
        if fn is None:
            result["handler_resolution"] = "dynamic_or_imported_endpoint_review_required"
            return result
        local, _ = self.import_aliases(module, list(ast.walk(fn)))
        result["dependencies"] = self.dependencies(module, self.parameter_nodes(fn), "handler", local)
        result["parameters"] = [{"name": arg.arg, "annotation": expression(arg.annotation)} for arg in [*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs]]
        calls: set[str] = set()
        for call in ast.walk(fn):
            if isinstance(call, ast.Call):
                qualified = self.qualify(module, _callee(call), local=local)
                calls.add(qualified)
        result["authorization_candidates"] = sorted(name for name in calls if re.search(r"\.(?:_?require|_?ensure.*access|_?authorize|_?can_|_?is_super_admin|_?validate.*identity)", name))
        result["service_candidates"] = sorted(name for name in calls if ".services." in name)
        result["handler_resolution"] = "ast_direct_calls_only"
        return result

    def _route(self, module: Module, call: ast.Call, handler: ast.AST | None, env: dict[str, Any], context: list[str], ordinal: int) -> None:
        method_name = call.func.attr
        receiver = self.qualify(module, expression(call.func.value))
        path_node = call.args[0] if call.args else _keyword(call, "path")
        path = self.literal(module, path_node, env)
        methods_node = _keyword(call, "methods")
        methods_value = self.literal(module, methods_node, env)
        if method_name in HTTP_METHODS:
            methods = [method_name.upper()]
        elif "websocket" in method_name:
            methods = ["WEBSOCKET"]
        elif method_name == "mount":
            methods = ["MOUNT"]
        elif methods_node is None:
            methods = ["GET"]
        elif isinstance(methods_value, list) and methods_value and all(isinstance(item, str) for item in methods_value):
            methods = sorted({item.upper() for item in methods_value})
        else:
            methods = ["UNKNOWN"]
        if handler is None:
            handler = call.args[1] if len(call.args) > 1 else _keyword(call, "endpoint")
        metadata = self.handler_metadata(module, handler)
        metadata["dependencies"] += self.dependencies(module, [_keyword(call, "dependencies")], "route")
        self.declarations.append({
            "router": receiver, "relative_path": path if isinstance(path, str) else None,
            "path_expression": expression(path_node), "methods": methods,
            "methods_expression": expression(methods_node), "source": self.source(module, call),
            "registration_kind": method_name, "registration_context": context, "ordinal": ordinal,
            "response_class": expression(_keyword(call, "response_class")),
            "response_model": expression(_keyword(call, "response_model")),
            "include_in_schema": self.literal(module, _keyword(call, "include_in_schema"), env) is not False,
            **metadata,
        })

    def scan(self, module: Module) -> None:
        if module.name in self._scanned:
            return
        self._scanned.add(module.name)
        self._statements(module, module.tree.body, {}, [])

    def _statements(self, module: Module, statements: list[ast.stmt], env: dict[str, Any], context: list[str]) -> None:
        for node in statements:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                factory = _callee(value) if isinstance(value, ast.Call) else ""
                constructor = module.imports.get(factory, factory).split(".")[-1]
                if isinstance(value, ast.Call) and constructor in {"APIRouter", "FastAPI"}:
                    for target in targets:
                        if isinstance(target, ast.Name):
                            prefix_node = _keyword(value, "prefix")
                            prefix = "" if prefix_node is None else self.literal(module, prefix_node, env)
                            self.routers[module.name + "." + target.id] = {
                                "prefix": prefix if isinstance(prefix, str) else None,
                                "prefix_expression": expression(prefix_node), "kind": constructor,
                                "dependencies": self.dependencies(module, [_keyword(value, "dependencies")], "router"),
                                "source": self.source(module, node), "context": context,
                                "include_in_schema": self.literal(module, _keyword(value, "include_in_schema"), env) is not False,
                            }
                continue
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for ordinal, decorator in enumerate(node.decorator_list):
                    if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr in ROUTE_METHODS:
                        self._route(module, decorator, node, env, context, ordinal)
                # A route declared inside a factory is retained, never assumed
                # to have been registered just because its source exists.
                self._statements(module, node.body, env, context + ["function:" + node.name])
                continue
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                call = node.value
                if not isinstance(call.func, ast.Attribute):
                    continue
                if call.func.attr == "include_router":
                    child = call.args[0] if call.args else _keyword(call, "router")
                    prefix_node = _keyword(call, "prefix")
                    prefix = "" if prefix_node is None else self.literal(module, prefix_node, env)
                    self.includes.append({
                        "parent": self.qualify(module, expression(call.func.value)),
                        "child": self.qualify(module, expression(child)),
                        "prefix": prefix if isinstance(prefix, str) else None,
                        "prefix_expression": expression(prefix_node),
                        "dependencies": self.dependencies(module, [_keyword(call, "dependencies")], "include_router"),
                        "source": self.source(module, call), "context": context,
                        "include_in_schema": self.literal(module, _keyword(call, "include_in_schema"), env) is not False,
                    })
                elif call.func.attr in {"add_api_route", "add_route", "add_websocket_route", "mount"}:
                    self._route(module, call, None, env, context, 0)
                continue
            if isinstance(node, (ast.For, ast.AsyncFor)):
                values = self.literal(module, node.iter, env)
                if isinstance(values, (list, tuple, dict)) and len(values) <= 200 and isinstance(node.target, ast.Name):
                    for item in values:
                        self._statements(module, node.body, {**env, node.target.id: item}, context)
                else:
                    self._statements(module, node.body, env, context + ["dynamic_loop:" + expression(node.iter)])
                self._statements(module, node.orelse, env, context + ["loop_else"])
            elif isinstance(node, ast.If):
                self._statements(module, node.body, env, context + ["conditional:" + expression(node.test)])
                self._statements(module, node.orelse, env, context + ["conditional_else:" + expression(node.test)])
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                self._statements(module, node.body, env, context + ["context_manager"])
            elif isinstance(node, ast.ClassDef):
                self._statements(module, node.body, env, context + ["class:" + node.name])
            elif isinstance(node, (ast.Try, ast.TryStar)):
                self._statements(module, node.body, env, context + ["try"])
                for branch in [*node.handlers]:
                    self._statements(module, branch.body, env, context + ["except"])
                self._statements(module, node.orelse + node.finalbody, env, context + ["try_else_finally"])

    def collect(self, routers_dir: str, entrypoints: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
        paths = sorted((self.root / routers_dir).rglob("*.py"))
        if not paths:
            self.issues.append({"kind": "empty_router_source", "directory": routers_dir})
        roots: list[str] = []
        for path in paths:
            module = self.load(self.module_name(path))
            if module:
                self.scan(module)
        for entrypoint in entrypoints:
            file_name, separator, name = entrypoint.partition(":")
            if not separator:
                name = "app"
            path = self.root / file_name
            module = self.load(self.module_name(path))
            if module:
                self.scan(module)
                roots.append(self.qualify(module, name))
            else:
                self.issues.append({"kind": "unresolved_entrypoint", "entrypoint": entrypoint})
        # Imported app/router re-exports may live outside routers_dir. Resolve
        # the graph without importing their Python modules into the process.
        changed = True
        while changed:
            before = len(self._scanned)
            symbols = roots + [part for edge in self.includes for part in (edge["parent"], edge["child"])]
            for symbol in symbols:
                module = self.load(symbol.rpartition(".")[0])
                if module:
                    self.scan(module)
            changed = len(self._scanned) != before
        by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in self.includes:
            by_parent[edge["parent"]].append(edge)
            if edge["child"] not in self.routers:
                self.issues.append({"kind": "unresolved_include", **edge})
        mounts: dict[str, list[dict[str, Any]]] = defaultdict(list)

        def walk(router: str, prefix: str | None, dependencies: list[dict[str, Any]], chain: list[dict[str, Any]], ancestors: tuple[str, ...], context: list[str], in_schema: bool) -> None:
            if router in ancestors:
                self.issues.append({"kind": "include_cycle", "routers": [*ancestors, router]})
                return
            definition = self.routers.get(router)
            if not definition:
                self.issues.append({"kind": "unresolved_router", "router": router})
                return
            own_prefix = definition["prefix"]
            full_prefix = prefix + own_prefix if prefix is not None and own_prefix is not None else None
            deps = dependencies + definition["dependencies"]
            conditions = context + definition["context"]
            visible = in_schema and definition["include_in_schema"]
            mounts[router].append({"prefix": full_prefix, "dependencies": deps, "chain": chain, "context": conditions, "include_in_schema": visible})
            for edge in by_parent[router]:
                joined = full_prefix + edge["prefix"] if full_prefix is not None and edge["prefix"] is not None else None
                walk(edge["child"], joined, deps + edge["dependencies"], chain + [edge], (*ancestors, router), conditions + edge["context"], visible and edge["include_in_schema"])

        for router in roots:
            walk(router, "", [], [], (), [], True)
        rows: list[dict[str, Any]] = []
        for declaration in self.declarations:
            options = mounts.get(declaration["router"])
            if not options:
                definition = self.routers.get(declaration["router"], {})
                options = [{"prefix": definition.get("prefix"), "dependencies": definition.get("dependencies", []), "chain": [], "context": [], "include_in_schema": definition.get("include_in_schema", True)}]
            for mount in options:
                path = mount["prefix"] + declaration["relative_path"] if mount["prefix"] is not None and declaration["relative_path"] is not None else None
                deps = mount["dependencies"] + declaration["dependencies"]
                for method in declaration["methods"]:
                    row = {key: value for key, value in declaration.items() if key not in {"methods", "dependencies", "ordinal"}}
                    mount_chain = [{"parent": edge["parent"], "child": edge["child"], "prefix": edge["prefix"], "source": edge["source"]} for edge in mount["chain"]]
                    identity = json.dumps([method, path, declaration["handler"], declaration["path_expression"], declaration["ordinal"], [(edge["parent"], edge["child"], edge["prefix"]) for edge in mount_chain]], sort_keys=True)
                    row.update({
                        "registration_id": "registration." + digest(identity),
                        "operation_key": operation_key(method, path) if path is not None and method != "UNKNOWN" else "unresolved." + digest(identity),
                        "method": method, "path": path, "mounted": declaration["router"] in mounts,
                        "mount_chain": mount_chain, "dependencies": deps,
                        "dependency_closure": self.dependency_closure(deps),
                        "include_in_schema": mount["include_in_schema"] and declaration["include_in_schema"],
                        "registration_context": mount["context"] + declaration["registration_context"],
                        "authorization_status": "requires_domain_policy_review",
                        "agent_execution_status": "not_verified",
                        "agent_tool": None, "resource_boundary": None, "side_effects": None,
                        "acceptance_tests": [], "delivery_phase": "P1_review_then_P5_or_P6",
                        "evidence": ["ast"],
                    })
                    reasons = []
                    if path is None:
                        reasons.append("dynamic_path_or_prefix")
                    if method == "UNKNOWN":
                        reasons.append("dynamic_methods")
                    if not row["mounted"]:
                        reasons.append("not_reachable_from_entrypoints")
                    if row["registration_context"]:
                        reasons.append("conditional_or_dynamic_registration")
                    if method == "MOUNT":
                        reasons.append("mounted_asgi_children_require_runtime_snapshot")
                    row["review_reasons"] = reasons
                    row["domain"] = infer_domain(row)
                    row["classification"] = classify(row)
                    rows.append(row)
        # Duplicate registrations remain separate, even when endpoint/path are
        # identical. Never silently deduplicate potentially shadowed routes.
        counts: Counter[str] = Counter()
        for row in rows:
            key = row["registration_id"]
            counts[key] += 1
            if counts[key] > 1:
                row["registration_id"] = key + "." + str(counts[key])
        return rows, roots


def infer_domain(row: dict[str, Any]) -> str:
    path = row.get("path") or ""
    file_name = row.get("source", {}).get("file", "")
    parts = file_name.replace("\\", "/").split("/")
    stem = Path(file_name).stem
    if "manage_parts" in parts:
        if stem.startswith("classes_courses"):
            return "classes_courses"
        return stem
    if "homework_parts" in parts:
        return "assignments_exams"
    if "materials_parts" in parts:
        return "materials"
    if "ui_parts" in parts:
        return stem.removesuffix("_pages")
    if "mp" in parts:
        return "miniapp_" + stem
    if "routers" in parts:
        return stem
    return "internal" if "/internal/" in path or path.startswith("/health") else "application"


def classify(row: dict[str, Any]) -> dict[str, str]:
    """Conservative surface grouping. No grouping grants or excludes a tool."""
    path = row.get("path") or ""
    file_name = row.get("source", {}).get("file", "")
    if not path:
        category, reason = "unknown", "Unresolved route expression; runtime inventory and manual review required."
    elif path.startswith(("/api/internal/", "/api/agent-bridge", "/health")):
        category, reason = "internal", "Machine-facing path; review caller policy and any user-facing equivalent."
    elif re.search(r"/(?:login|logout|register|password-reset|reset-password|refresh-token)(?:/|$)", path) or "/api/mp/auth/" in path:
        category, reason = "auth", "Authentication/session route; verify which user workflows require a safe form."
    elif "HTMLResponse" in row.get("response_class", "") or ("/ui_parts/" in file_name and row.get("method") == "GET" and not path.startswith("/api/")) or "manage_redirects" in file_name:
        category, reason = "ui", "Page or redirect surface; map underlying business operations before exclusion."
    elif path in {"/favicon.ico", "/robots.txt", "/static"}:
        category, reason = "public", "Static/public path candidate; authorization has not been proved by AST."
    elif path.startswith("/api/") or row.get("dependencies") or row.get("authorization_candidates"):
        category, reason = "user_business", "Business endpoint candidate; action/resource/role parity still needs review."
    else:
        category, reason = "unknown", "Absence of a declared dependency is not evidence of public access."
    return {"category": category, "status": "inferred_review_required", "reason": reason}


def snapshot_runtime_routes(app: Any) -> dict[str, Any]:
    """Inspect an already-created ASGI app; callers own safe app construction."""
    rows: list[dict[str, Any]] = []

    def walk(routes: Any, prefix: str = "") -> None:
        for route in routes or []:
            path = prefix + str(getattr(route, "path", ""))
            endpoint = getattr(route, "endpoint", None)
            children = getattr(route, "routes", None)
            if children is None:
                children = getattr(getattr(route, "app", None), "routes", None)
            if children:
                walk(children, path)
                continue
            methods = getattr(route, "methods", None)
            if not methods:
                methods = ["WEBSOCKET" if "WebSocket" in type(route).__name__ else "MOUNT"]
            try:
                source_file = inspect.getsourcefile(endpoint) if endpoint else None
                source_line = inspect.getsourcelines(endpoint)[1] if endpoint else None
            except (TypeError, OSError):
                source_file, source_line = None, None
            for method in sorted(methods):
                rows.append({"method": method, "path": path, "name": getattr(route, "name", ""), "handler": (getattr(endpoint, "__module__", "") + "." + getattr(endpoint, "__qualname__", "")).strip("."), "source_file": source_file, "source_line": source_line, "include_in_schema": getattr(route, "include_in_schema", False)})

    walk(getattr(app, "routes", []))
    return {"routes": rows, "note": "Metadata only; application lifespan and endpoints were not invoked by this function."}


def _snapshot_rows(snapshot: dict[str, Any] | list[dict[str, Any]] | None, kind: str) -> list[dict[str, Any]]:
    if snapshot is None:
        return []
    if kind == "runtime":
        rows = snapshot if isinstance(snapshot, list) else snapshot.get("routes")
        if not isinstance(rows, list):
            raise ValueError("runtime snapshot must contain a routes array")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("path"), str) or not isinstance(row.get("method"), str):
                raise ValueError("runtime routes require string method and path")
        return rows
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("paths"), dict):
        raise ValueError("OpenAPI snapshot must contain a paths object")
    rows = []
    for path, item in snapshot["paths"].items():
        for method, definition in item.items():
            if method.lower() in HTTP_METHODS:
                rows.append({"method": method.upper(), "path": path, "operation_id": definition.get("operationId"), "security": definition.get("security", snapshot.get("security")), "request_body": definition.get("requestBody"), "parameters": item.get("parameters", []) + definition.get("parameters", [])})
    return rows


def reconcile(rows: list[dict[str, Any]], runtime: Any = None, openapi: Any = None) -> dict[str, Any]:
    comparison: dict[str, Any] = {}
    for kind, snapshot in (("runtime", runtime), ("openapi", openapi)):
        if snapshot is None:
            comparison[kind] = {"status": "not_supplied", "note": "AST is not proof of runtime registration or authorization."}
            continue
        observed = _snapshot_rows(snapshot, kind)
        by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            if row.get("path") is not None and row["method"] != "UNKNOWN" and row.get("mounted"):
                by_key[(row["method"], row["path"])].append(row)
        missing = []
        observed_keys: set[tuple[str, str]] = set()
        for observation in observed:
            key = (observation["method"].upper(), observation["path"])
            observed_keys.add(key)
            matches = by_key.get(key, [])
            if matches:
                for row in matches:
                    if kind not in row["evidence"]:
                        row["evidence"].append(kind)
                    row.setdefault("observations", {})[kind] = observation
            else:
                missing.append({"method": key[0], "path": key[1]})
                new_row = {
                    "registration_id": kind + "." + digest(key[0] + "\n" + key[1]),
                    "operation_key": operation_key(*key), "method": key[0], "path": key[1],
                    "mounted": True, "handler": observation.get("handler") or "<snapshot-only>",
                    "source": {}, "evidence": [kind], "observations": {kind: observation},
                    "review_reasons": ["not_matched_to_ast_declaration"],
                    "dependencies": [], "dependency_closure": [], "authorization_candidates": [],
                    "service_candidates": [], "parameters": [], "mount_chain": [],
                    "include_in_schema": observation.get("include_in_schema", True),
                    "authorization_status": "requires_domain_policy_review", "agent_execution_status": "not_verified",
                    "agent_tool": None, "resource_boundary": None, "side_effects": None, "acceptance_tests": [],
                    "delivery_phase": "P1_review_then_P5_or_P6", "domain": "snapshot_review",
                }
                new_row["classification"] = classify(new_row)
                rows.append(new_row)
                by_key[key].append(new_row)
        absent = [{"method": method, "path": path} for (method, path), candidates in by_key.items() if (method, path) not in observed_keys and any("ast" in row["evidence"] and row["method"] != "MOUNT" and (kind != "openapi" or (row.get("include_in_schema") and row["method"] != "WEBSOCKET")) for row in candidates)]
        comparison[kind] = {"status": "compared", "observed_count": len(observed), "snapshot_only": missing, "ast_only": sorted(absent, key=lambda item: (item["path"], item["method"])), "note": "Differences require review; OpenAPI omits hidden routes/websockets and does not prove resource authorization."}
    return comparison


def apply_reviewed_adapters(report: dict[str, Any], reviewed: Any, root: Path) -> None:
    """Attach explicit local adapter evidence without treating a Web route as fully covered.

    Method, path and handler must match one static mounted registration. Evidence
    hashes make changed implementations require a new review instead of silently
    inheriting an old approval. This report never changes the execution registry.
    """
    if not isinstance(reviewed, dict) or reviewed.get("schema_version") != 1 or not isinstance(reviewed.get("adapters"), list):
        raise ValueError("reviewed adapters must be a schema_version=1 object with an adapters list")
    issues, accepted, seen = [], [], set()
    for item in reviewed["adapters"]:
        key = item.get("capability_key") if isinstance(item, dict) else None
        if not isinstance(key, str) or not key or key in seen:
            issues.append({"capability_key": key, "reason": "invalid_or_duplicate_capability_key"})
            continue
        seen.add(key)
        required = ("kind", "method", "path", "handler", "roles", "resource_boundary", "receipt", "verification", "limitations", "source_files")
        if any(not item.get(field) for field in required) or item["kind"] not in {"read", "write", "request", "secure_input", "file_read"} or not isinstance(item["source_files"], list):
            issues.append({"capability_key": key, "reason": "incomplete_explicit_review"})
            continue
        if item["kind"] == "request" and item.get("guarantee") != "observed_http_result_not_verified_business":
            issues.append({"capability_key": key, "reason": "request_requires_observation_guarantee"})
            continue
        if item["kind"] == "secure_input" and item.get("guarantee") != "user_only_authenticated_confirmation":
            issues.append({"capability_key": key, "reason": "secure_input_requires_user_only_confirmation"})
            continue
        if item["kind"] == "file_read" and item.get("guarantee") != "authorized_file_snapshot":
            issues.append({"capability_key": key, "reason": "file_read_requires_authorized_snapshot"})
            continue
        candidates = [row for row in report["operations"] if row["method"] == item["method"] and row["path"] == item["path"]]
        if len(candidates) != 1 or not candidates[0].get("mounted") or candidates[0]["handler"] != item["handler"]:
            issues.append({"capability_key": key, "reason": "route_missing_changed_or_ambiguous"})
            continue
        invalid = []
        for source in item["source_files"]:
            relative = source.get("file", "") if isinstance(source, dict) else ""
            path = (root / relative).resolve()
            if not relative or not path.is_relative_to(root.resolve()) or not path.is_file():
                invalid.append(relative or "missing_source")
            elif hashlib.sha256(path.read_bytes()).hexdigest() != source.get("sha256"):
                invalid.append(relative)
        if invalid:
            issues.append({"capability_key": key, "reason": "review_source_changed_or_missing", "files": invalid})
            continue
        entry = {**item, "status": "locally_verified_adapter", "runtime_acceptance": "not_verified", "web_route_coverage": "parameter_or_action_subset",
                 "business_completion": "observed_http_only" if item["kind"] == "request" else "authenticated_user_transaction_receipt" if item["kind"] == "secure_input" else "authorized_snapshot_not_binary_delivery" if item["kind"] == "file_read" else "requires_domain_job_verification" if item.get("async_domain_job") else "transaction_receipt" if item["kind"] == "write" else "authorized_read"}
        accepted.append(entry)
        row = candidates[0]
        row.setdefault("reviewed_adapters", []).append(key)
        row["agent_execution_status"] = "reviewed_subset_needs_remaining_adapter"
        row.setdefault("agent_tools", [])
        tool = "authenticated_user_confirmation" if item["kind"] == "secure_input" else "platform_file" if item["kind"] == "file_read" else "platform_" + item["kind"]
        if tool not in row["agent_tools"]:
            row["agent_tools"].append(tool)
        row["agent_tool"] = row["agent_tools"][0]
        row["authorization_status"] = "reviewed_normal_policy_for_adapter_subset"
    report["reviewed_adapters"] = accepted
    report["review_issues"] = issues
    report["summary"].update({"locally_verified_read_adapters": sum(item["kind"] == "read" for item in accepted),
                              "locally_verified_write_adapters": sum(item["kind"] == "write" for item in accepted),
                              "locally_reviewed_request_adapters": sum(item["kind"] == "request" for item in accepted),
                              "locally_verified_secure_input_adapters": sum(item["kind"] == "secure_input" for item in accepted),
                              "locally_verified_file_read_adapters": sum(item["kind"] == "file_read" for item in accepted),
                              "async_domain_job_adapters": sum(bool(item.get("async_domain_job")) for item in accepted),
                              "review_evidence_issues": len(issues), "fully_covered_web_routes": 0})


def build_inventory(root: Path = REPO_ROOT, *, routers_dir: str = "classroom_app/routers", entrypoints: list[str] | None = None, runtime: Any = None, openapi: Any = None, reviewed: Any = None) -> dict[str, Any]:
    scanner = Inventory(root)
    rows, roots = scanner.collect(routers_dir, entrypoints or ["classroom_app/app.py:app"])
    comparisons = reconcile(rows, runtime, openapi)
    rows.sort(key=lambda row: (row.get("path") or "~", row["method"], row["registration_id"]))
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        grouped[row["operation_key"]].append(row["registration_id"])
    duplicates = [{"operation_key": key, "registrations": registrations} for key, registrations in sorted(grouped.items()) if len(registrations) > 1]
    sources = [{"file": module.path.relative_to(scanner.root).as_posix(), "sha256": hashlib.sha256(module.path.read_bytes()).hexdigest()} for module in sorted(scanner.modules.values(), key=lambda item: item.path.as_posix())]
    report = {
        "schema_version": SCHEMA_VERSION,
        "generator": "tools/agent_capability_inventory.py",
        "semantics": {
            "classification": "Every classification is a review candidate, never an authorization or exclusion decision.",
            "coverage": "Every observed declaration is retained; dynamic factories/loops may represent multiple runtime routes.",
            "authorization": "Dependencies and direct call hints do not prove complete resource policy; no capability is marked supported.",
            "stable_keys": "operation_key hashes method + full path; duplicates are retained as separate registration_id rows.",
            "snapshot_safety": "Only caller-supplied JSON is read; no app imports, network calls or business operations.",
        },
        "entrypoints": roots, "source_files": sources,
        "summary": {
            "ast_declarations": len(scanner.declarations), "operation_rows": len(rows),
            "unique_operation_keys": len(grouped), "classification_counts": dict(sorted(Counter(row["classification"]["category"] for row in rows).items())),
            "unresolved_or_conditional_rows": sum(bool(row["review_reasons"]) for row in rows),
            "unmounted_rows": sum(not row.get("mounted") for row in rows), "supported_capabilities": 0,
            "authorization_review_required": len(rows), "duplicate_operation_keys": len(duplicates),
        },
        "graph_issues": scanner.issues, "duplicate_operations": duplicates,
        "comparisons": comparisons, "operations": rows,
    }
    if reviewed is not None:
        apply_reviewed_adapters(report, reviewed, root)
    return report


def render_markdown(report: dict[str, Any]) -> str:
    def cell(value: Any) -> str:
        return str(value or "—").replace("|", "\\|").replace("\n", " ").replace("\r", " ")

    summary = report["summary"]
    lines = [
        "# Agent 全量路由与能力审查台账", "",
        "本文件由 `python tools/agent_capability_inventory.py` 生成；JSON 保存完整依赖、挂载链、服务候选和待补齐字段。",
        "**路由分类不授予权限，路由数量不是业务能力数量。** 显式附加的本地适配证据只覆盖注明的参数或动作，不代表整条 Web 路由、生产运行或所有身份已验收。", "",
        f"AST 注册声明 {summary['ast_declarations']}；方法/挂载展开后 {summary['operation_rows']} 行；稳定操作键 {summary['unique_operation_keys']} 个。",
        f"未解析/条件注册 {summary['unresolved_or_conditional_rows']} 行；未挂载 {summary['unmounted_rows']} 行；重复操作键 {summary['duplicate_operation_keys']} 个；整条 Web 路由覆盖 / 生产验收 0。", "",
        "## 审查与再生成", "",
        "1. 按行核对真实业务服务、当前身份、所有有效组织、对象可读/可用/可管理规则和副作用。",
        "2. 合并同一业务的多入口后填写工具名、资源边界、操作回执、验收用例和交付阶段；界面/内部/认证候选不得直接当作排除项。",
        "3. 动态路径、条件注册和未挂载项必须保留并与隔离测试产生的运行时路由快照对照。",
        "4. `--runtime-json routes.json --openapi-json openapi.json` 可加入对照；脚本不导入应用、不启动服务、不请求接口。",
        "5. `--check` 校验已生成文件是否过期，可作 CI 门禁；它不代表权限审查已完成。`--fail-on-unresolved` 可额外要求静态解析无缺口。", "",
        "6. `--reviewed-json docs/agent-capability-reviewed.json` 加入经人工审查的本地适配证据；方法/路径/handler 必须唯一匹配且证据文件 SHA256 未变。修改实现后必须重新审查，不能仅刷哈希冒充验收。", "",
        "JSON 中 `service_candidates/authorization_candidates` 仅为 AST 直接调用线索，不能替代调用链及领域规则审查。", "",
        "## 来源对照", "",
    ]
    for kind, comparison in report["comparisons"].items():
        lines.append(f"- {kind}: {comparison['status']}" + (f"；仅快照 {len(comparison['snapshot_only'])}、仅 AST {len(comparison['ast_only'])}。" if comparison["status"] == "compared" else "；尚未提供，不能宣称线上挂载完整一致。"))
    lines += ["", "## 解析问题", ""]
    for issue in report["graph_issues"]:
        lines.append("- " + cell(json.dumps(issue, ensure_ascii=False, sort_keys=True)))
    if not report["graph_issues"]:
        lines.append("未发现 include_router 图错误。动态注册仍列在各行的待审查项中。")
    if "reviewed_adapters" in report:
        lines += ["", "## 已核对的本地适配范围", "",
                  f"A 层读适配 {summary['locally_verified_read_adapters']}、事务写适配 {summary['locally_verified_write_adapters']}；B 层受控 HTTP 请求 {summary['locally_reviewed_request_adapters']}；C 层仅用户安全确认 {summary['locally_verified_secure_input_adapters']}、授权文件来源 {summary['locally_verified_file_read_adapters']}，异步领域作业能力 {summary['async_domain_job_adapters']} 项还需独立完成核验。证据待复核 {summary['review_evidence_issues']}。未列出的功能仍需适配，以下能力仍需真实 DSH / PostgreSQL / 生产身份验收。", "",
                  "A 写回执证明领域行与操作账本一同提交；B 回执仅证明正常 HTTP 调用观察到的状态与响应，超时/崩溃可能 uncertain，不能自动当成业务成功或盲目重放。C 异步领域作业须跟踪原 job、真实材料绑定与成品完整性，排队成功不等于生成完成。相同 Web 路由可同时有 A/B 局部入口，不重复宣称整条路由覆盖。", "",
                  "C 安全输入必须由当前用户在确认界面填写，密码不提供给模型、不进入提案/任务/回执；不能通过 MCP platform_write 执行，也不计入 A 事务写数量。", "",
                  "C 文件来源仅提供按正常下载权限获取的有界文本或文档抽取与快照 SHA256，返回前复核身份和来源绑定；不代表二进制已交付到 runner，也不计入 A 读适配数量。Linux 原语探针与真实 HTTP 身份测试分别提供证据，不能互相替代。", "",
                  "| 能力键 | 类型 / 身份 | 已覆盖范围与限制 | 本地验证 |", "|---|---|---|---|"]
        for item in report["reviewed_adapters"]:
            lines.append("| " + " | ".join(cell(value) for value in [item["capability_key"], item["kind"] + " / " + ", ".join(item["roles"]) + (" / administrator" if item.get("requires_super_admin") else ""), item["limitations"], ", ".join(item["verification"])]) + " |")
        for issue in report["review_issues"]:
            lines.append("- 待复核：" + cell(json.dumps(issue, ensure_ascii=False, sort_keys=True)))
    lines += ["", "## 路由台账", "", "| 操作键 | 方法与完整路径 | 业务域 / 分类候选 | 身份依赖 / 授权候选 | Handler / 源码 | 待审查 |", "|---|---|---|---|---|---|"]
    for row in report["operations"]:
        deps = sorted({item["callable"].rsplit(".", 1)[-1] for item in row["dependencies"] + row.get("dependency_closure", [])})
        guards = [item.rsplit(".", 1)[-1] for item in row.get("authorization_candidates", [])]
        source = row.get("source", {})
        location = f"{source.get('file', '')}:{source.get('line', '')}" if source else "snapshot"
        path = row.get("path") if row.get("path") is not None else "<动态表达式: " + row.get("path_expression", "") + ">"
        reasons = ("局部适配 " + ", ".join(row["reviewed_adapters"]) + "；其余 needs_adapter" if row.get("reviewed_adapters") else ", ".join(row["review_reasons"]) or "领域授权 / 工具 / 回执 / 验收")
        lines.append("| " + " | ".join(cell(value) for value in [row["operation_key"], row["method"] + " " + path, row["domain"] + " / " + row["classification"]["category"], ", ".join(deps + guards) or "无显式依赖≠公开", row["handler"] + " · " + location, reasons]) + " |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--routers-dir", default="classroom_app/routers")
    parser.add_argument("--entrypoint", action="append", help="repo-relative file.py:app; may be repeated")
    parser.add_argument("--runtime-json", type=Path)
    parser.add_argument("--openapi-json", type=Path)
    parser.add_argument("--reviewed-json", type=Path)
    parser.add_argument("--output-json", default="docs/agent-capability-matrix.json")
    parser.add_argument("--output-md", default="docs/agent-capability-matrix.md")
    parser.add_argument("--check", action="store_true", help="fail if generated files differ; do not write")
    parser.add_argument("--fail-on-unresolved", action="store_true", help="also fail on graph/registration gaps")
    args = parser.parse_args(argv)
    read_json = lambda path: json.loads(path.read_text(encoding="utf-8-sig")) if path else None
    report = build_inventory(args.root, routers_dir=args.routers_dir, entrypoints=args.entrypoint, runtime=read_json(args.runtime_json), openapi=read_json(args.openapi_json), reviewed=read_json(args.reviewed_json))
    outputs = [(args.root / args.output_json, json.dumps(report, ensure_ascii=False, indent=2) + "\n"), (args.root / args.output_md, render_markdown(report))]
    stale: list[str] = []
    for path, content in outputs:
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != content:
                stale.append(str(path))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    print(json.dumps({"summary": report["summary"], "graph_issue_count": len(report["graph_issues"]), "stale_files": stale}, ensure_ascii=False))
    return 1 if stale or report.get("review_issues") or (args.fail_on_unresolved and (report["graph_issues"] or report["summary"]["unresolved_or_conditional_rows"])) else 0


if __name__ == "__main__":
    raise SystemExit(main())
