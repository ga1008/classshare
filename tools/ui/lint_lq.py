"""Foundation and migration-scoped guard. Runtime semantics need browser audits."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACTIVE = {"迁移中", "业务验收通过", "视觉验收通过", "待发布", "已发布", "旧代码可删除"}
RULES = {
    "native-confirm": re.compile(r"(?<![\w$.])(?:(?:window|globalThis|self)\s*\.\s*)?(?:confirm|alert)\s*\("),
    "manual-version": re.compile(r"\?v="),
    "legacy-class": re.compile(r"\b(?:btn btn-[\w-]+|modal-backdrop|app-topbar-action[\w-]*|ls-button[\w-]*|filter-chip[\w-]*|toast-container|badge-(?:primary|warning|success|danger|secondary))\b"),
    "legacy-toast": re.compile(r"\bshowToast\s*\("),
    "literal-z-index": re.compile(r"z-index\s*:\s*-?\d"),
    "literal-color": re.compile(r"#[\da-fA-F]{3,8}\b|\b(?:rgb|hsl)a?\(\s*[\d.]"),
}


def violations(path: str, text: str) -> list[dict]:
    findings = []
    # Ignore source comments and url() payloads, preserving line numbers.
    cleaned = re.sub(r"/\*.*?\*/|<!--.*?-->|url\([^)]*\)", lambda m: "\n" * m.group().count("\n"), text, flags=re.S)
    for line_number, line in enumerate(cleaned.splitlines(), 1):
        for code, pattern in RULES.items():
            if code == "literal-color" and (path.endswith("/tokens.css") or re.match(r"\s*--ls-c-[\w-]+\s*:", line)):
                continue
            # Named component declarations are not calls to the native dialog.
            # Strip only the declaration name; a native call in its body still
            # fails, as do bare/window/globalThis/self calls on other lines.
            candidate = re.sub(r"\bfunction\s+(?:confirm|alert)(?=\s*\()", "function dialogDeclaration", line) if code == "native-confirm" else line
            if pattern.search(candidate):
                findings.append({"path": path, "line": line_number, "rule": code})
        for match in re.finditer(r"\bstyle\s*=\s*([\"'])(.*?)\1", line):
            declarations = [s.strip() for s in match[2].split(";") if s.strip()]
            if any(not re.match(r"--[\w-]+\s*:", declaration) for declaration in declarations):
                findings.append({"path": path, "line": line_number, "rule": "inline-style"})
        filters = re.findall(r"(?:-webkit-)?backdrop-filter\s*:\s*([^;}]+)", line)
        creates_backdrop = any(not re.fullmatch(r"none(?:\s*!important)?", value.strip(), re.I) for value in filters)
        if creates_backdrop and not any(part in path for part in ("/lq/components/", "/lq/materials.css", "/lq/shell-")):
            findings.append({"path": path, "line": line_number, "rule": "blur-host"})
    return findings


def _path(root: Path, value: str) -> str:
    if not isinstance(value, str) or not value or re.search(r"[\x00-\x1f\\?#:]", value):
        raise ValueError(f"Registry needs a repository-relative file path: {value!r}")
    file = Path(value)
    if file.is_absolute() or ".." in file.parts or value != file.as_posix() or not (root / file).resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Registry path escapes repository or is not normalized: {value}")
    return value


def _paths(root: Path, values: list[str]) -> list[str]:
    if not isinstance(values, list):
        raise ValueError("Registry file paths must be a list")
    return [_path(root, value) for value in values]


def audit(root: Path, registry: dict, exceptions: list[dict], page: str | None = None) -> dict:
    scopes = {}
    required = set()
    reviewed = {}
    generated = set()
    matched_page = False
    for entry in registry["entries"]:
        if page and page not in {entry["id"], entry.get("routePattern"), entry.get("template")}:
            continue
        matched_page = True
        active = entry["status"] in ACTIVE
        dependencies = _paths(root, [entry["template"], *("static/" + a.lstrip("/") for a in entry.get("assets", [])), *entry.get("controller", [])])
        scope = entry.get("migrationScope")
        if scope is None:
            for path in dependencies:
                scopes[path] = scopes.get(path, False) or active
            continue
        # A partial migration is a positive source contract, not an exception:
        # new sources get every rule; shared sources retain warnings and require
        # renewed review after any byte changes. Existing runtime dependencies
        # are inventoried without claiming that their business UI has migrated.
        if not isinstance(scope, dict) or scope.get("kind") != "partial" or any(not isinstance(scope.get(key), str) or not scope[key].strip() for key in ("owner", "description", "reviewAt")):
            raise ValueError(f"Incomplete partial migration scope: {entry['id']}")
        notes = entry.get("controllerNotes", [])
        if not isinstance(notes, list):
            raise ValueError("controllerNotes must be structured records")
        for note in notes:
            if not isinstance(note, dict) or note.get("path") not in entry.get("controller", []) or not isinstance(note.get("description"), str) or not note["description"].strip():
                raise ValueError(f"Controller notes must describe a listed source: {entry['id']}")
            if "entrypoint" in note and (not isinstance(note["entrypoint"], str) or not note["entrypoint"].strip()):
                raise ValueError(f"Invalid controller entrypoint note: {entry['id']}")
        sources = _paths(root, scope.get("activeSources"))
        shared = scope.get("sharedSources")
        if not sources or not isinstance(shared, list) or not shared:
            raise ValueError(f"Partial scope needs active and reviewed shared sources: {entry['id']}")
        shared_paths = set()
        for item in shared:
            if not isinstance(item, dict) or not isinstance(item.get("scope"), str) or not item["scope"].strip() or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))):
                raise ValueError(f"Invalid shared-source review: {entry['id']}")
            path = _path(root, item.get("path"))
            tests = _paths(root, item.get("tests"))
            if not tests or any(not test.startswith("tests/") for test in tests):
                raise ValueError(f"Shared-source review needs actual test paths: {path}")
            if path in sources or path in shared_paths:
                raise ValueError(f"Overlapping partial source roles: {path}")
            shared_paths.add(path)
            if active:
                if path in reviewed and reviewed[path] != item["sha256"]:
                    raise ValueError(f"Conflicting shared-source review hashes: {path}")
                reviewed[path] = item["sha256"]
                required.update(tests)
        if entry["template"] not in {*sources, *shared_paths}:
            raise ValueError(f"Partial scope must cover its template: {entry['id']}")
        for path in [*dependencies, *shared_paths]:
            scopes.setdefault(path, False)
        for path in sources:
            scopes[path] = scopes.get(path, False) or active
        if active:
            required.update([*dependencies, *shared_paths, *sources])
        artifacts = entry.get("generatedAssets", [])
        if not isinstance(artifacts, list):
            raise ValueError("generatedAssets must be a list")
        for item in artifacts:
            if not isinstance(item, dict) or item.get("kind") not in {"compiled", "vendor"} or not isinstance(item.get("reason"), str) or not item["reason"].strip():
                raise ValueError(f"Invalid generated asset provenance: {entry['id']}")
            path = _path(root, item.get("path"))
            if path not in dependencies or path in {*sources, *shared_paths}:
                raise ValueError(f"Generated asset must be a separate loaded dependency: {path}")
            generated.add(path)
    if page and not matched_page:
        raise ValueError(f"Page is not registered: {page}")
    # Foundations remain active, including a --page audit. A partial consumer
    # must not downgrade the common code it relies on. S0's lq/pages/centered.css
    # is a legacy relocation and remains governed by its own page registry.
    for pattern in ("static/css/lq/*.css", "static/css/lq/components/**/*.css",
                    "static/js/lq/**/*.js", "templates/partials/lq_*", "templates/dev/lq*.html",
                    "templates/macros/lq/**/*.html"):
        for file in root.glob(pattern):
            if file.is_file():
                scopes[file.relative_to(root).as_posix()] = True
    # A complete active consumer (including foundation) always wins over another
    # consumer's partial/shared/generated classification.
    artifacts_only = {path for path in generated if not scopes.get(path)}
    required.update(generated)
    report = {"blocking": [], "warningCount": 0, "warningCountsByPath": {}, "exceptionCount": 0, "fileCount": len(scopes), "activeFileCount": sum(scopes.values()), "generatedAssets": sorted(artifacts_only), "reviewedSourceCount": len(reviewed), "limitations": ["Source guard; dynamic templates, DOM nesting, names, blur counts and animation semantics need runtime/S2 checks.", "Partial shared-source hashes require explicit review and linked SSR tests; dependency warnings are not migration acceptance."]}
    missing = {path for path in required if not (root / path).is_file()}
    for path in sorted(missing):
        report["blocking"].append({"path": path, "line": 0, "rule": "missing-file"})
    for path, digest in sorted(reviewed.items()):
        if path not in missing and hashlib.sha256((root / path).read_bytes()).hexdigest() != digest:
            report["blocking"].append({"path": path, "line": 0, "rule": "reviewed-source-changed"})
    for path, active in sorted(scopes.items()):
        file = (root / path).resolve()
        if not file.is_relative_to(root.resolve()):
            raise ValueError(f"Registry path escapes repository: {path}")
        if not file.is_file():
            if active and path not in missing:
                report["blocking"].append({"path": path, "line": 0, "rule": "missing-file"})
            continue
        if path in artifacts_only:
            continue
        if file.suffix not in {".html", ".css", ".js", ".mjs", ".ts", ".tsx"}:
            continue
        for finding in violations(path, file.read_text(encoding="utf-8")):
            waived = any(e.get("path") == path and finding["rule"] in e.get("rules", []) and all(e.get(k) for k in ("reason", "owner", "scope", "reviewAt")) for e in exceptions)
            if waived:
                report["exceptionCount"] += 1
            elif active:
                report["blocking"].append(finding)
            else:
                report["warningCount"] += 1
                report["warningCountsByPath"][path] = report["warningCountsByPath"].get(path, 0) + 1
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    registry = json.loads((ROOT / "docs/lq-migration-registry.json").read_text(encoding="utf-8"))
    exceptions = json.loads((ROOT / "docs/lq-lint-exceptions.json").read_text(encoding="utf-8"))["exceptions"]
    report = audit(ROOT, registry, exceptions, args.page)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report, "blocking": report["blocking"][:30]}, ensure_ascii=False))
    return 1 if report["blocking"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
