"""Source ownership inventory, not a claim of browser or business acceptance.

Reads authored Jinja, native JavaScript and React sources without importing the
application. Original migration statuses are never written or promoted. Every
entry remains pending until an independently reviewed component/consumer
contract and runtime scenario accepts it.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
KINDS = ("button", "field", "select", "surface", "menu", "dialog", "tab", "choice", "toolbar", "layer", "content-slot", "handle", "chip", "domain", "status", "unknown")
KIND_NAMES = {
    "domain": ("region",),
    "button": ("button", "btn", "action_entry", "fab", "disclosure", "nav_item"),
    "status": ("badge", "avatar", "spinner", "progress", "skeleton", "status", "save_status", "conflict"),
    "field": ("field", "input", "textarea", "range", "form_section", "error_summary", "date", "upload", "composer", "file", "color"),
    "select": ("select", "native_select", "combobox", "listbox"),
    "surface": ("card", "surface", "panel", "list", "row", "empty", "bubble", "prose", "collapsible", "workspace"),
    "menu": ("menu", "dropdown", "popover", "nav_menu", "tooltip"),
    "dialog": ("dialog", "modal", "sheet", "drawer", "confirm", "alert", "choose", "lightbox", "viewer"),
    "tab": ("tabs", "tab", "segment"),
    "choice": ("checkbox", "radio", "switch", "switch_control", "selection", "chip", "chip_row"),
    "toolbar": ("toolbar", "topbar", "filter_bar", "form_actions", "page_head", "sidebar", "dock", "navigation", "crumbs", "steps", "page_layout", "editor"),
}
OWNER_HINTS = {
    "button": "shared/button", "field": "shared/forms", "select": "shared/selection",
    "surface": "shared/content", "menu": "shared/menu-tooltip", "dialog": "shared/layer-dialog",
    "layer": "shared/layer", "content-slot": "domain/content", "handle": "domain/geometry", "chip": "shared/selection", "domain": "domain/registered",
    "tab": "shared/navigation", "choice": "shared/forms-selection", "toolbar": "shared/shell-content", "status": "shared/status", "unknown": "review-required",
}
# This is a candidate assignment, never registration or acceptance by inference.
DOMAIN_HINTS = (
    ("course_schedule", "domain/course-schedule"), ("classroom", "domain/classroom"),
    ("assignment", "domain/assignment"), ("submission", "domain/submission"),
    ("exam", "domain/exam"), ("resume", "domain/resume"), ("blog", "domain/blog"),
    ("ai_workspace", "domain/ai-workspace"), ("ai_chat", "domain/ai-chat"),
    ("material", "domain/materials"), ("profile", "domain/profile"),
    ("dashboard", "domain/dashboard"), ("manage/", "domain/manage"),
)
NATIVE_FACTORIES = {
    "createComponent", "componentMarkup", "componentTree", "createForm", "formMarkup",
    "createContent", "contentMarkup", "createShell", "shellMarkup", "createSelection",
    "selectionMarkup", "createNavigation", "navigationMarkup", "createDialog", "dialogMarkup",
    "createWorkspace", "workspaceMarkup", "createUpload", "uploadMarkup",
}
ATTR = re.compile(r"([\w:@-]+)\s*=\s*(\"(?:\\.|[^\"])*\"|'(?:\\.|[^'])*'|\{(?:[^{}]|\{[^{}]*\})*\}|[^\s>]+)", re.S)
TAG_START = re.compile(r"<([A-Za-z][\w.:-]*)(?=[\s/>])|<(\{\{[\s\S]*?\}\})")
COMMENTS = re.compile(r"<!--.*?-->|\{#.*?#\}", re.S)


def mask(value: str) -> str:
    return "".join("\n" if char == "\n" else " " for char in value)


def kind_for_name(name: str) -> str:
    normalized = re.sub(r"([a-z])([A-Z])", r"\1_\2", name).lower().replace("-", "_")
    if normalized.replace("_", "-") in KINDS: return normalized.replace("_", "-")
    normalized = re.sub(r"^(?:lq_|create_|open_|render_)", "", normalized)
    for kind, names in KIND_NAMES.items():
        if normalized in names:
            return kind
    # Domain/React names such as TeacherPickerDialog remain candidates.
    for kind, names in KIND_NAMES.items():
        if any(normalized.endswith("_" + part) for part in names):
            return kind
    return "unknown"


def js_without_comments(source: str) -> str:
    """Preserve offsets/strings/JSX while removing comments, including JSX comments."""
    chars = list(source)
    i, quote = 0, None
    while i < len(source):
        char = source[i]
        if quote:
            if char == "\\":
                i += 2
                continue
            if char == quote:
                quote = None
        elif char in "'\"`":
            quote = char
        elif source.startswith("//", i):
            end = source.find("\n", i)
            end = len(source) if end < 0 else end
            chars[i:end] = mask(source[i:end]); i = end
            continue
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            end = len(source) if end < 0 else end + 2
            chars[i:end] = mask(source[i:end]); i = end
            continue
        i += 1
    return "".join(chars)


def exclude_text_sinks(source: str) -> str:
    # Direct plain-text sinks cannot create UI. Keep other string literals as
    # candidates: an unrecognized template factory must not silently disappear.
    pattern = re.compile(r"(?:\.(?:textContent|innerText|value)\s*=\s*|\bcreateTextNode\s*\(\s*)(?P<q>['\"`])(?:\\.|(?!(?P=q))[\s\S])*(?P=q)")
    return pattern.sub(lambda match: mask(match.group()), source)


def tags(source: str):
    """Opening tags with quoted attributes and Jinja/JSX expressions intact."""
    position = 0
    while match := TAG_START.search(source, position):
        tag = match.group(1) or "dynamic"
        i, quote, depth = match.end(), None, 0
        while i < len(source):
            char = source[i]
            if quote:
                if char == "\\":
                    i += 2
                    continue
                if char == quote:
                    quote = None
            elif char in "'\"`":
                quote = char
            elif char == "{":
                depth += 1
            elif char == "}" and depth:
                depth -= 1
            elif char == ">" and not depth:
                raw = source[match.end():i]
                attributes = {key: value for key, value in ATTR.findall(raw)}
                yield match.start(), tag, attributes, source[match.start():i + 1]
                break
            i += 1
        position = max(match.end(), i + 1)


def value(attributes: dict, name: str) -> str:
    result = attributes.get(name, "")
    if len(result) > 1 and result[0] in "'\"" and result[-1] == result[0]:
        return result[1:-1]
    return result


def tag_kind(tag: str, attributes: dict) -> str | None:
    declared = value(attributes, "data-lq-component")
    if declared:
        return declared if declared in KINDS else kind_for_name(declared)
    role = value(attributes, "role")
    cls = value(attributes, "class") or value(attributes, "className")
    typ = value(attributes, "type")
    if tag == "dynamic": return "unknown"
    if role in ("dialog", "alertdialog") or tag == "dialog": return "dialog"
    if role in ("tab", "tablist"): return "tab"
    if role in ("menu", "menubar", "menuitem", "menuitemcheckbox", "menuitemradio"): return "menu"
    if role in ("combobox", "listbox") or tag == "select": return "select"
    if role in ("checkbox", "radio", "switch", "radiogroup") or tag == "input" and typ in ("checkbox", "radio"): return "choice"
    if role == "button" or tag in ("button", "summary") or tag == "input" and typ in ("button", "submit", "reset"): return "button"
    if tag == "input" and typ == "hidden": return None
    if tag in ("input", "textarea", "fieldset") or role in ("textbox", "searchbox", "slider", "spinbutton"): return "field"
    if role == "toolbar": return "toolbar"
    if tag[0].isupper(): return kind_for_name(tag.split(".")[-1])
    for kind, pattern in (
        ("dialog", r"(?:^|\s)[\w-]*(?:modal|dialog|sheet|drawer|overlay)(?:--[\w-]+)?(?:\s|$)"),
        ("menu", r"(?:^|\s)[\w-]*(?:menu|dropdown|popover)(?:--[\w-]+)?(?:\s|$)"),
        ("tab", r"(?:^|\s)[\w-]*(?:tabs|segment|tablist)(?:\s|$)"),
        ("toolbar", r"(?:^|\s)[\w-]*(?:toolbar|topbar|filter-bar|form-actions)(?:--[\w-]+)?(?:\s|$)"),
        ("surface", r"(?:^|\s)[\w-]*(?:card|panel|surface)(?:\s|$)"),
        ("button", r"(?:^|\s)(?:btn(?:-[\w-]+)?|lq-btn(?:--[\w-]+)?|[\w-]*-button(?:--[\w-]+)?)(?:\s|$)"),
    ):
        if kind in ("dialog", "menu", "tab", "toolbar", "surface") and tag in ("label", "span", "strong", "em", "small", "h1", "h2", "h3", "p", "i"): continue
        if re.search(pattern, cls): return kind
    return None


def shared_definition(file: str) -> bool:
    return file.startswith(("templates/macros/lq/", "static/js/lq/")) or file == "frontend/src/components/lq-presentation.tsx"


def owner_hint(file: str, kind: str) -> str:
    if shared_definition(file): return OWNER_HINTS[kind]
    return next((owner for needle, owner in DOMAIN_HINTS if needle in file), OWNER_HINTS[kind])


CANONICAL_CLASSES = {
    'button': ('lq-btn', 'lq-disclosure-trigger'), 'field': ('lq-form-section', 'lq-field', 'lq-input', 'lq-textarea', 'lq-range', 'lq-native-file', 'lq-native-color'),
    'input': ('lq-input',), 'textarea': ('lq-textarea',), 'file': ('lq-native-file',), 'color': ('lq-native-color',), 'range': ('lq-range',),
    'select': ('lq-select', 'lq-selection'), 'choice': ('lq-domain-graph-choice', 'lq-btn', 'lq-checkbox', 'lq-radio', 'lq-switch', 'lq-chip'),
    'checkbox': ('lq-checkbox',), 'radio': ('lq-radio',), 'switch': ('lq-switch',), 'disclosure': ('lq-disclosure-trigger',),
    'surface': ('lq-surface', 'lq-card', 'lq-glass'), 'dialog': ('lq-native-dialog', 'lq-glass', 'lq-dialog', 'lq-dialog-root', 'lq-sheet', 'lq-drawer', 'lq-domain-popover'),
    'menu': ('lq-menu__item', 'lq-menu', 'lq-nav-menu', 'lq-popover', 'lq-domain-region', 'lq-domain-popover'),
    'selection': ('lq-selection', 'lq-domain-raised'), 'popover': ('lq-glass', 'lq-popover', 'lq-domain-popover'), 'tab': ('lq-tabs', 'lq-tabs__tab', 'lq-tabs__list', 'lq-segment'),
    'workspace': ('lq-workspace', 'lq-domain-workspace'), 'drawer': ('lq-drawer', 'lq-glass'),
    'toolbar': ('lq-domain-toolbar', 'lq-toolbar', 'lq-topbar', 'lq-filter-bar', 'lq-page-head'), 'layer': ('lq-domain-region', 'lq-layer'),
    'content-slot': ('lq-domain-content-slot',), 'handle': ('lq-domain-handle',), 'chip': ('lq-chip',), 'domain': ('lq-domain-region', 'lq-domain-control'), 'region': ('lq-domain-region',),
}

# Exact canonical roots from component-props.js, content.js, forms.js and status.js.
CANONICAL_CLASSES.update({kind: ('lq-' + kind.replace('_', '-'),) for kind in (
    'badge', 'avatar', 'spinner', 'progress', 'skeleton', 'status', 'save_status', 'alert', 'conflict',
    'card', 'list', 'row', 'empty', 'page_head', 'filter_bar', 'prose', 'bubble',
    'form_section', 'form_actions', 'error_summary', 'topbar', 'nav_item', 'sidebar', 'dock', 'fab', 'crumbs', 'steps', 'editor', 'page_layout', 'viewer', 'split', 'tabs', 'segment')})

def canonical_contract(declared: str, cls: str, material: str = '') -> dict:
    expected = CANONICAL_CLASSES.get(declared, ())
    if declared in ('dialog', 'popover', 'drawer', 'selection', 'surface') and material == 'raised':
        expected = (*expected, 'lq-domain-raised')
    tokens = set(re.findall(r'[\w-]+', cls))
    matches = sorted(set(expected) & tokens)
    return {'status': 'not-declared' if not declared else 'canonical-declared-pending' if matches else 'canonical-resolution-pending' if any(token in cls for token in ('{{', '${', '{')) else 'declared-missing-canonical',
            'expectedAny': list(expected), 'matched': matches, 'acceptance': 'pending'}


def entry(file: str, source: str, offset: int, kind: str, syntax: str, tag: str, cls: str = "", attrs: dict | None = None, provenance: str | None = None, snippet: str = "") -> dict:
    marked = bool(re.search(r"\blq-[\w-]+", cls) or any(key.startswith("data-lq-") for key in (attrs or {})))
    declared = value(attrs or {}, "data-lq-component")
    provenance = provenance or ("shared-definition" if shared_definition(file) else "component-declared" if declared else "lq-marked-native" if marked else "legacy-or-native")
    return {"file": file, "line": source.count("\n", 0, offset) + 1, "column": offset - source.rfind("\n", 0, offset), "offset": offset,
            "kind": kind, "syntax": syntax,
            "tagOrFactory": tag, "class": cls, "id": value(attrs or {}, "id"),
            "provenance": provenance, "status": "unknown" if kind == "unknown" else "pending",
            "declaredComponent": declared or None, "canonicalContract": canonical_contract(declared, cls, value(attrs or {}, "data-lq-material")),
            "suggestedOwner": owner_hint(file, kind), "ownerStatus": "suggested-not-accepted",
            "source": re.sub(r"\s+", " ", snippet).strip()[:240]}


def imported_symbols(source: str) -> dict[str, tuple[str, str]]:
    result = {}
    for match in re.finditer(r"import\s*\{([^}]+)\}\s*from\s*['\"]([^'\"]+)['\"]", source):
        for item in match.group(1).split(","):
            pieces = re.split(r"\s+as\s+", item.strip())
            if pieces and re.fullmatch(r"[\w$]+", pieces[0]): result[pieces[-1]] = (pieces[0], match.group(2))
    return result


def typescript_ranges(sources: dict[str, str]) -> dict:
    if not sources: return {}
    result = subprocess.run(["node", str(Path(__file__).with_name("audit_lq_jsx.cjs"))],
        input=json.dumps(sources, ensure_ascii=True), text=True, capture_output=True, encoding="utf-8", check=True)
    return json.loads(result.stdout)


def scan_js(file: str, source: str, offset: int = 0, full_source: str | None = None, parsed: dict | None = None) -> list[dict]:
    full_source = source if full_source is None else full_source
    cleaned = exclude_text_sinks(js_without_comments(source))
    imports = imported_symbols(cleaned)
    entries = []
    if file.endswith((".ts", ".tsx", ".jsx")):
        parsed = parsed if parsed is not None else typescript_ranges({file: source})[file]
        if parsed["parseDiagnostics"]:
            raise ValueError(f"Cannot silently inventory invalid TypeScript/JSX in {file}: {parsed['parseDiagnostics']}")
        found_tags = [(start + at, tag, attrs, snippet) for start, end, _ in parsed["ranges"] for at, tag, attrs, snippet in tags(cleaned[start:end])]
    else:
        found_tags = tags(cleaned)
    for at, tag, attrs, snippet in found_tags:
        kind = tag_kind(tag, attrs)
        if kind is None and "dangerouslySetInnerHTML" in attrs: kind = "unknown"
        if kind is None: continue
        original, module = imports.get(tag, (tag, ""))
        if module == 'lucide-react' and not any(key in attrs for key in ('onClick', 'onKeyDown', 'tabIndex', 'role')):
            row = entry(file, full_source, at + offset, 'content-slot', 'react-jsx', tag,
                value(attrs, 'className'), attrs, provenance='external-icon-content-slot', snippet=snippet)
            row['slotEvidence'] = 'Imported lucide-react SVG icon without interaction props; surrounding controls are inventoried independently.'
            entries.append(row)
            continue
        if original != tag: kind = kind_for_name(original)
        verified = "lq-presentation" in module or "/lq/" in module
        syntax = "react-jsx" if tag[0].isupper() or file.endswith((".jsx", ".tsx")) else "js-html-candidate"
        entries.append(entry(file, full_source, at + offset, kind, syntax, tag,
            value(attrs, "class") or value(attrs, "className"), attrs,
            "shared-component-call" if verified else None, snippet))
    for match in re.finditer(r"\b(?:[\w$]+\.)*createElement(?:NS)?\s*\(\s*(?:(['\"])(.*?)\1|([^,)\n]+))", cleaned):
        literal, expression = match.group(2), match.group(3)
        tag = literal or (expression or "").strip()
        if "createElementNS" in match.group():
            # SVG construction is decorative; it is not an interactive root.
            continue
        tail = cleaned[match.end():match.end() + 800]
        boundary = re.search(r"\breturn\b|^\s*(?:export\s+)?(?:async\s+)?function\b", tail, re.M)
        if boundary: tail = tail[:boundary.start()]
        assignment = re.search(r"\b([\w$]+)\s*=\s*$", cleaned[max(0, match.start() - 100):match.start()])
        cls = ""
        if assignment:
            name = re.escape(assignment.group(1))
            found = re.search(rf"\b{name}\.(?:className\s*=|setAttribute\(\s*['\"]class['\"]\s*,)\s*(['\"`])(.*?)\1", tail, re.S)
            if found: cls = found.group(2)
            else:
                dynamic_class = re.search(rf"\b{name}\.className\s*=\s*([^;\n]+)", tail)
                if dynamic_class: cls = '{runtime-class: ' + dynamic_class.group(1) + '}'
            for added in re.finditer(rf"\b{name}\.classList\.add\(([^)]*)\)", tail):
                cls += ' ' + ' '.join(re.findall(r"['\"](lq-[\w-]+)['\"]", added.group(1)))
        factory_attrs = {'class': cls}
        if assignment:
            for attr, pattern in [('data-lq-component', rf"\b{name}\.dataset\.lqComponent\s*=\s*(['\"])(.*?)\1"), ('data-lq-material', rf"\b{name}\.dataset\.lqMaterial\s*=\s*(['\"])(.*?)\1"), ('type', rf"\b{name}\.type\s*=\s*(['\"])(.*?)\1")]:
                found = re.search(pattern, tail)
                if found: factory_attrs[attr] = found.group(2)
        kind = tag_kind(tag, factory_attrs) if literal else "unknown"
        if kind is None: continue
        row = entry(file, full_source, offset + match.start(), kind, "create-element", tag, cls, factory_attrs, snippet=match.group())
        if assignment:
            adapters = [alias for alias, (original, module) in imports.items() if original in ('adoptDomainControl', 'adoptDomainContentSlot', 'adoptDomainHandle') and '/lq/domain-controls' in module]
            if file.startswith('static/js/resume_'): adapters.append('RZ.adoptControl' if not file.endswith('resume_common.js') else 'adoptControl')
            for adapter in adapters:
                call = re.search(rf"\b{re.escape(adapter)}\(\s*{name}\s*(?:,\s*\{{([^}}]*)\}})?\s*\)", tail)
                if not call: continue
                declared_kind = re.search(r"kind\s*:\s*['\"]([\w-]+)['\"]", call.group(1) or '')
                original_adapter = imports.get(adapter, (adapter, ''))[0]
                if original_adapter == 'adoptDomainContentSlot': row['kind'] = 'content-slot'
                elif original_adapter == 'adoptDomainHandle': row['kind'] = 'handle'
                if declared_kind and original_adapter not in ('adoptDomainContentSlot', 'adoptDomainHandle'):
                    row['kind'] = kind_for_name(declared_kind.group(1))
                    row['status'] = 'unknown' if row['kind'] == 'unknown' else 'pending'
                row['provenance'] = 'shared-adapter-call-candidate'
                row['adapterCall'] = adapter
                row['canonicalContract'] = {'status': 'adapter-call-pending-runtime', 'expectedAny': [], 'matched': [], 'acceptance': 'pending'}
                break
        entries.append(row)
    # Escaped markup literals may need a string decoder before their attributes
    # can be resolved. Keep them explicit instead of silently reporting zero.
    for match in re.finditer(r"<(?:button|input|textarea|select|dialog|div|section|a)\b[^<>\n]*\\[\"']", cleaned):
        if any(row['offset'] == offset + match.start() for row in entries): continue
        entries.append(entry(file, full_source, offset + match.start(), 'unknown', 'escaped-html-candidate', 'serialized-markup', provenance='runtime-output-unknown', snippet=match.group()))
    factory_names = set(NATIVE_FACTORIES) | {key for key, (name, module) in imports.items() if name in NATIVE_FACTORIES and "/lq/" in module}
    for match in re.finditer(r"\b(" + "|".join(sorted(factory_names)) + r")\s*\(\s*(['\"])([\w-]+)\2", cleaned):
        name, component = match.group(1), match.group(3)
        kind = kind_for_name(component)
        original, module = imports.get(name, (name, ""))
        verified = shared_definition(file) or "/lq/" in module
        entries.append(entry(file, full_source, offset + match.start(), kind, "component-factory-call", original,
            provenance="shared-component-call" if verified else "factory-ownership-unknown", snippet=match.group()))
    for match in re.finditer(r"\.(?:innerHTML|outerHTML)\s*=\s*|\.insertAdjacentHTML\s*\(\s*['\"][^'\"]+['\"]\s*,\s*|\.createContextualFragment\s*\(\s*", cleaned):
        tail = cleaned[match.end():]
        literal = re.match(r"(['\"`])((?:\\.|(?!(?:\1))[\s\S])*?)\1", tail)
        if literal:
            content = literal.group(2)
            # Clearing a container or assigning fully visible literal markup
            # creates no additional unknown output beyond the tag records.
            if not content.strip() or list(tags(content)) and "${" not in content: continue
        snippet = cleaned[match.start():match.end() + 100].split("\n", 1)[0]
        entries.append(entry(file, full_source, offset + match.start(), "unknown", "dynamic-html-sink", match.group().strip(),
            provenance="runtime-output-unknown", snippet=snippet))
    return entries


def scan_source(file: str, source: str, parsed: dict | None = None) -> list[dict]:
    if file.endswith('.py'):
        # Inspect generated iframe/chrome markup without importing app code or
        # contacting a DB. Joined strings remain explicit unresolved slots.
        result = []
        lines = source.splitlines(keepends=True)

        class Strings(ast.NodeVisitor):
            def visit_Expr(self, node):
                if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
                    self.generic_visit(node)  # Ignore docstrings, never UI.

            def collect(self, node, fragment):
                if not re.search(r'<(?:button|input|select|textarea|dialog|div|section|header|aside|a)\b', fragment): return
                start = sum(len(line) for line in lines[:node.lineno - 1])
                end = sum(len(line) for line in lines[:node.end_lineno])
                cursor = start
                for row in scan_source(file + '.html', fragment):
                    found = source.find('<' + row['tagOrFactory'], cursor, end)
                    at = found if found >= 0 else start
                    if found >= 0: cursor = found + len(row['tagOrFactory']) + 1
                    row.update(file=file, line=source.count('\n', 0, at) + 1, column=at - source.rfind('\n', 0, at), offset=at,
                               syntax='python-generated-' + row['syntax'], sourceExpressionLine=node.lineno)
                    result.append(row)

            def visit_Constant(self, node):
                if isinstance(node.value, str): self.collect(node, node.value)

            def visit_JoinedStr(self, node):
                self.collect(node, ''.join(value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else '{{python-expression}}' for value in node.values))

        Strings().visit(ast.parse(source))
        return result
    if not file.endswith(".html"): return scan_js(file, source, parsed=parsed)
    cleaned = COMMENTS.sub(lambda m: mask(m.group()), source)
    entries = []
    for match in re.finditer(r"<script\b([^>]*)>(.*?)</script\s*>", cleaned, re.S | re.I):
        attrs = dict(ATTR.findall(match.group(1)))
        if value(attrs, "type") not in ("application/json", "application/ld+json", "text/plain"):
            entries.extend(scan_js(file, match.group(2), match.start(2), source))
    markup = re.sub(r"<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>", lambda m: mask(m.group()), cleaned, flags=re.S | re.I)
    for at, tag, attrs, snippet in tags(markup):
        kind = tag_kind(tag, attrs)
        if kind is not None: entries.append(entry(file, source, at, kind, "jinja-html", tag, value(attrs, "class"), attrs, snippet=snippet))
    aliases = {}
    for match in re.finditer(r"\{%[-+]?\s*from\s+['\"](macros/lq/[^'\"]+)['\"]\s+import\s+(.+?)%\}", cleaned, re.S):
        for name in match.group(2).strip(" -+").split(","):
            pieces = re.split(r"\s+as\s+", name.strip())
            if re.fullmatch(r"\w+", pieces[0]): aliases[pieces[-1]] = (pieces[0], match.group(1))
    for block in re.finditer(r"\{(?:\{|%)[\s\S]*?(?:\}|%)\}", cleaned):
        if re.match(r"\{%[-+]?\s*(?:macro|from|import)\b", block.group()): continue
        for call in re.finditer(r"\b([\w]+)\s*\(", block.group()):
            name = call.group(1)
            if name not in aliases: continue
            original, module = aliases[name]
            entries.append(entry(file, source, block.start() + call.start(), kind_for_name(original), "jinja-component-call", original,
                provenance="shared-component-call", snippet=call.group() + " from " + module))
    return sorted(entries, key=lambda item: (item["offset"], item["syntax"], item["tagOrFactory"]))


def source_files(root: Path):
    for folder, endings in (("templates", {".html", ".js"}), ("static/js", {".js", ".mjs"}), ("frontend/src", {".js", ".jsx", ".ts", ".tsx"})):
        for path in sorted((root / folder).rglob("*")):
            if not path.is_file() or path.suffix not in endings: continue
            if path.relative_to(root).as_posix() in {'static/js/echarts.min.js', 'static/js/marked.min.js'}: continue
            if any(part in {"node_modules", "dist", "assets", "vendor", "__pycache__"} for part in path.relative_to(root).parts): continue
            if ".generated." in path.name and path.suffix != ".html" or ".test." in path.name or ".spec." in path.name or path.name.endswith(".d.ts"): continue
            yield path
    for path in sorted((root / 'classroom_app').rglob('*.py')):
        if re.search(r'<(?:button|input|select|textarea|dialog)\b', path.read_text(encoding='utf-8')):
            yield path


def inventory_drift(root: Path) -> dict:
    # Existing generator, in memory only; it preserves manual acceptance fields.
    try:
        from tools.ui.lq_inventory import build_registry
    except ModuleNotFoundError:
        from lq_inventory import build_registry
    registry_file = root / "docs/lq-migration-registry.json"
    previous = json.loads(registry_file.read_text(encoding="utf-8")) if registry_file.is_file() else {"entries": []}
    current = build_registry(root, previous)
    old_ids = {e["id"] for e in previous["entries"]}; new_ids = {e["id"] for e in current["entries"]}
    direct = sorted({e["template"] for e in current["entries"] if e["routePattern"]})
    ordinary = [name for name in direct if not name.startswith(("templates/dev/", "templates/partials/"))]
    profile_source = root / "classroom_app/routers/profile.py"
    dynamic_routes = []
    if profile_source.is_file():
        tree = ast.parse(profile_source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_api_route" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and arg.value == "/manage/me": dynamic_routes.append("/manage/me")
            if isinstance(node, ast.For) and isinstance(node.iter, (ast.Tuple, ast.List)) and any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "add_api_route" for n in ast.walk(node)):
                for value_node in node.iter.elts:
                    if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str): dynamic_routes.append("/manage/me/" + value_node.value)
    exceptional = [name for name in ("error.html", "session_expired.html", "material_reader_unavailable.html") if (root / "templates" / name).is_file()]
    route_ids = {(e["method"], e["routePattern"]) for e in current["entries"] if e["routePattern"]}
    return {"storedTemplateCount": previous.get("coverage", {}).get("templateCount"), "currentTemplateCount": current["coverage"]["templateCount"],
        "storedEntryCount": len(previous["entries"]), "currentEntryCount": len(current["entries"]),
        "addedIdentities": sorted(new_ids - old_ids), "removedIdentities": sorted(old_ids - new_ids),
        "sourcePageMethodPaths": len(route_ids), "directTemplates": direct, "ordinaryPageTemplates": ordinary,
        "dynamicPageRoutesMissingFromGenerator": sorted(set(dynamic_routes)), "exceptionalResponseTemplates": exceptional,
        "unresolvedTemplates": current["coverage"]["unresolvedTemplates"],
        "storedStatuses": dict(Counter(e["status"] for e in previous["entries"])),
        "emptyStates": sum(not e.get("states") for e in previous["entries"]), "emptyTests": sum(not e.get("tests") for e in previous["entries"]),
        "limitations": current["limitations"] + ["Dynamic profile routes are explicitly resolved from their registration loop; the source-only generator is not a runtime route snapshot."]}


def build_report(root: Path) -> dict:
    entries, files = [], []
    sources = {path.relative_to(root).as_posix(): path.read_text(encoding="utf-8") for path in source_files(root)}
    parsed = typescript_ranges({name: source for name, source in sources.items() if name.endswith((".ts", ".tsx", ".jsx"))})
    for name, source in sources.items():
        found = scan_source(name, source, parsed=parsed.get(name))
        entries.extend(found)
        files.append({"file": name, "sha256": hashlib.sha256(source.encode()).hexdigest(), "entryCount": len(found)})
    for index, item in enumerate(entries, 1): item["auditId"] = f"lq-source-{index:05d}"
    by_file = Counter(e["file"] for e in entries)
    domains = defaultdict(list)
    for item in entries:
        if item["suggestedOwner"].startswith("domain/"): domains[item["suggestedOwner"]].append(item)
    return {"schemaVersion": 1, "generator": "python -B tools/ui/audit_lq_components.py", "acceptance": "pending",
        "scope": {"sourceRoots": ["templates", "static/js", "frontend/src"], "excluded": ["generated JavaScript", "built/dist/vendor assets", "third-party static/js/echarts.min.js and marked.min.js (their rendered DOM remains in browser inventory)", "test sources", "declaration-only TypeScript", "comments", "CSS", "hidden inputs", "direct plain-text sinks", "JSON script payloads"],
            "countMeaning": "Source occurrences, including mutually exclusive Jinja branches; not rendered element counts. Component calls and their definitions are separate records, not a completion ratio."},
        "summary": {"files": len(files), "htmlTemplates": sum(f["file"].endswith(".html") for f in files), "entries": len(entries),
            "byKind": dict(Counter(e["kind"] for e in entries)), "bySyntax": dict(Counter(e["syntax"] for e in entries)),
            "byCanonicalContract": dict(Counter(e["canonicalContract"]["status"] for e in entries)),
            "byProvenance": dict(Counter(e["provenance"] for e in entries)), "byStatus": dict(Counter(e["status"] for e in entries)),
            "largestFiles": [{"file": f, "entries": n} for f, n in by_file.most_common(25)]},
        "registryDrift": inventory_drift(root),
        "domainOwnerCandidates": [{"owner": owner, "registrationStatus": "pending-review", "files": sorted({e["file"] for e in items}), "entryCount": len(items)} for owner, items in sorted(domains.items())],
        "sharedDefinitionFiles": [f["file"] for f in files if shared_definition(f["file"])], "files": files, "entries": entries,
        "limitations": ["Static lexical inventory, not a JavaScript interpreter or a full JSX/Jinja execution engine. Unresolved dynamic tags/factories remain unknown.",
            "Class names, inherited CSS, and data-lq attributes are provenance hints only; none imply component or business acceptance.",
            "JS strings with UI markup remain candidates unless proven to be plain-text sinks; dynamically assembled markup and runtime-only API content still require DOM inventory.",
            "Source occurrences cannot enumerate data-dependent instance counts, authorization branches, native popups, shadow DOM, portal/iframe states, or generated server HTML.",
            "Pair the source inventory with the isolated runtime route/DOM/state matrix; all pending and unknown records need an owner, contract, and scenario or an explicit reviewed exception."]}


def browser_route_inventory(root: Path) -> dict:
    try:
        from tools.ui.lq_inventory import build_registry
    except ModuleNotFoundError:
        from lq_inventory import build_registry
    registry = build_registry(root)
    routes = {}
    for item in registry['entries']:
        if not item.get('routePattern'): continue
        key = (item['method'], item['routePattern'])
        row = routes.setdefault(key, {'method': key[0], 'path': key[1], 'templates': [], 'dependencies': [], 'source': item['routeEvidence']['source'], 'line': item['routeEvidence']['line'], 'provenance': 'source-registry'})
        row['templates'].append(item['template'])
        row['dependencies'] = sorted(set(row['dependencies']) | set(item['routeEvidence']['dependencies']))
    for url in inventory_drift(root)['dynamicPageRoutesMissingFromGenerator']:
        routes[('GET', url)] = {'method': 'GET', 'path': url, 'templates': ['templates/manage/profile.html'], 'dependencies': ['get_current_teacher'], 'source': 'classroom_app/routers/profile.py', 'provenance': 'explicit-registration-loop'}
    for row in routes.values():
        url = row['path']
        if url == '/auth/forbidden': roles = ['student', 'teacher']
        elif url.startswith(('/student/', '/teacher/')): roles = ['anonymous']
        elif url.startswith('/manage/system/') and not url.endswith('/life-tips'): roles = ['superTeacher']
        elif url.startswith('/materials/lessondoc-editor/') or url.startswith('/api/classrooms/') or url.startswith('/manage') or url.endswith('/edit') or url == '/exam/new' or url.endswith('/wrong-summary') or url == '/dev/lq': roles = ['teacher']
        elif url.startswith(('/resume', '/exam/take')) or url in ('/points', '/achievements', '/learning-path', '/career-path', '/wrong-book', '/report-card', '/feedback-review', '/profile'): roles = ['student']
        else: roles = ['student', 'teacher']
        row.update(roles=roles, rolesStatus='candidate-verify-server-authorization', scope='fragment' if url.startswith('/api/') else 'development' if url == '/dev/lq' else 'page', status='pending')
        row['parameters'] = re.findall(r'\{([^}]+)\}', url)
        if row['method'] != 'GET': row['execution'] = 'domain-scenario-required-no-generic-post'
        elif url == '/dev/lq': row['execution'] = 'requires-preview-feature-flag'
        else: row['execution'] = 'synthetic-get'
    return {'schemaVersion': 1, 'acceptance': 'pending', 'routes': [routes[key] for key in sorted(routes)],
        'exceptionalDocuments': inventory_drift(root)['exceptionalResponseTemplates'],
        'domainScenarioSuites': ['tests/e2e/components/lq-dialogs.spec.ts', 'tests/e2e/components/lq-legacy-bridges.spec.ts', 'tests/e2e/components/lq-shells.spec.ts', 'tests/e2e/components/lq-schedule-bridge.spec.ts', 'tests/e2e/specs/lq-s4-manage-pages.spec.ts', 'tests/e2e/specs/lq-s4-profile-appearance-business.spec.ts', 'tests/e2e/specs/lq-s5-archive.spec.ts', 'tests/e2e/specs/lq-s6-attendance.spec.ts'],
        'limitations': ['Role candidates and GET reachability do not prove authorization or business-state coverage.', 'POST, exceptional response documents, missing synthetic entities, preview flags, and dynamic dialogs remain explicit gaps until their named domain scenarios execute.', 'Never substitute a 200 login/status/redirect page for the requested template.']}


def markdown(report: dict) -> str:
    s, drift = report["summary"], report["registryDrift"]
    lines = ["# 全平台组件源码审计基线（2026-09-27）", "", "此报告是源码入口清单，不是组件完成率或页面验收证明。所有记录仍为 pending/unknown；JSON 保存逐条文件、行号、类名、来源、建议 owner 与源文件 SHA256。", "",
        f"扫描 {s['files']} 个作者源码文件，覆盖 {s['htmlTemplates']} 个 HTML 模板，发现 {s['entries']} 个组件/控件候选入口。共享定义和调用分别登记；互斥模板分支均计入，不能作为运行时控件总数。", "",
        "| 类别 | 源码入口数 |", "|---|---:|", *[f"| {kind} | {s['byKind'].get(kind, 0)} |" for kind in KINDS], "",
        "| 来源 | 数量 |", "|---|---:|", *[f"| {key} | {value} |" for key, value in sorted(s['byProvenance'].items())], "",
        "## 页面台账缺口", "", f"原台账 {drift['storedTemplateCount']} 模板/{drift['storedEntryCount']} 条，内存重建为 {drift['currentTemplateCount']} 模板/{drift['currentEntryCount']} 条；原文件及手工验收状态未更改。",
        f"常规页面模板根 {len(drift['ordinaryPageTemplates'])} 个；生成器发现 {drift['sourcePageMethodPaths']} 个页面方法路径，另显式注册的个人中心页面 {len(drift['dynamicPageRoutesMissingFromGenerator'])} 个需补充。异常响应文档另计。", "",
        *[f"- 新增台账身份：`{item}`" for item in drift['addedIdentities']],
        *[f"- 动态入口：`GET {item}` → `templates/manage/profile.html`" for item in drift['dynamicPageRoutesMissingFromGenerator']],
        *[f"- 异常文档：`templates/{item}`" for item in drift['exceptionalResponseTemplates']], "",
        "## 按源码入口数量排序", "", "| 文件 | 候选入口 |", "|---|---:|",
        *[f"| `{item['file']}` | {item['entries']} |" for item in s['largestFiles']], "",
        "## 穷尽覆盖门禁", "", "1. 路由快照与真实 app.routes 对齐；直接、add_api_route、重定向、异常响应和动态片段分别登记，未知路由不能默认为通过。",
        "2. 逐个源码入口确认共享组件、域组件或有理由的例外；只加类名/颜色的桥接仍为 pending。域 owner 候选必须人工确认，不能按路径自动接受。",
        "3. 对全部页面根生成授权角色×320/390/768/1024/1440宽度×浅/深色的基础可达性与控件盘点；隐藏、空数据、禁用、繁忙、错误、权限、冲突、弹层/iframe等状态由领域场景补齐。",
        "4. 运行时所有 button/field/select/surface/menu/dialog/tab/choice/toolbar 必须映射 source occurrence 或共享工厂及领域 owner；动态 DOM 新增控件也纳入，unknown 不可吞掉。",
        "5. 组件合同验证默认/悬停/按下/键盘/选中/禁用/繁忙、关闭否决、焦点归还、草稿与表单归属、20次开关资源清理；交互只动画transform/opacity并尊重reduced-motion。",
        "6. 建立来源SHA、组件合同SHA、浏览器场景结果三者绑定的验收记录；源码变化使相关记录失效，不覆盖旧报告为全绿。",
        "7. 相同场景比较渲染宿主、事件耗时、长任务、布局偏移与请求数量；CSS继承或静态类命中不能代替真浏览器证据。", "",
        "## 边界", "", *[f"- {item}" for item in report['limitations']], ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, help="Write complete JSON; absent means summary to stdout only")
    parser.add_argument("--markdown", type=Path, help="Write human-readable baseline")
    parser.add_argument("--summary", type=Path, help="Write machine-readable summary JSON")
    parser.add_argument("--findings", type=Path, help="Write flat finding JSON for reviewed codemods")
    parser.add_argument("--browser-routes", type=Path, help="Write exhaustive source-derived browser route candidates")
    args = parser.parse_args()
    report = build_report(args.root.resolve())
    for target, content in ((args.output, json.dumps(report, ensure_ascii=False, indent=2) + "\n"), (args.markdown, markdown(report))):
        if target:
            target.parent.mkdir(parents=True, exist_ok=True); target.write_text(content, encoding="utf-8")
    for target, payload in ((args.summary, {"acceptance": report["acceptance"], "summary": report["summary"], "registryDrift": report["registryDrift"]}), (args.findings, report["entries"])):
        if target:
            target.parent.mkdir(parents=True, exist_ok=True); target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.browser_routes:
        args.browser_routes.parent.mkdir(parents=True, exist_ok=True)
        args.browser_routes.write_text(json.dumps(browser_route_inventory(args.root.resolve()), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"acceptance": report["acceptance"], "summary": report["summary"], "registryDrift": {key: report["registryDrift"][key] for key in ("storedTemplateCount", "currentTemplateCount", "storedEntryCount", "currentEntryCount", "sourcePageMethodPaths")}}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
