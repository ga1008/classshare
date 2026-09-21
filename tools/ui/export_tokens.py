"""Export typed S1 tokens from CSS; this is not part of the npm build chain."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOKEN_SOURCE = ROOT / "static/css/lq/tokens.css"
PALETTES = ("teal", "indigo", "sky", "mint", "violet", "rose")
APPEARANCES = ("light", "dark")
TYPES = {"hsl-channels", "hsl-alpha-channels", "color", "rgb-channels-comma", "shadow", "image", "length", "percentage", "number", "integer", "time", "easing", "font-family", "transition"}
DECLARATION = re.compile(r"(--[\w-]+)\s*:\s*([^;{}]+);\s*/\*\s*@type\s+([\w-]+)\s*\*/")
REFERENCE = re.compile(r"var\(\s*(--[\w-]+)\s*\)")
CHANNELS = r"-?\d+(?:\.\d+)?\s+\d+(?:\.\d+)?%\s+\d+(?:\.\d+)?%"


def read_definitions(source: Path | None = None) -> list[dict]:
    """Read annotated leaf blocks; preserve media and selector scopes."""
    path = Path(source) if source is not None else TOKEN_SOURCE
    text = path.read_text(encoding="utf-8")
    clean = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group()), text, flags=re.S)
    stack = []
    boundary = 0
    definitions = []
    for brace in re.finditer(r"[{}]", clean):
        if brace.group() == "{":
            stack.append((clean[boundary:brace.start()].strip(), brace.end()))
        else:
            if not stack:
                raise ValueError("Unbalanced token CSS")
            selector, start = stack.pop()
            body = text[start:brace.start()]
            if "{" not in clean[start:brace.start()]:
                declarations = list(DECLARATION.finditer(body))
                declared = re.findall(r"(--[\w-]+)\s*:", clean[start:brace.start()])
                if len(declared) != len(declarations):
                    raise ValueError(f"Every token requires @type metadata: {selector}")
                for match in declarations:
                    name, value, kind = match.groups()
                    if kind not in TYPES:
                        raise ValueError(f"Unknown token type {kind}: {name}")
                    definitions.append({"name": name, "type": kind, "value": value.strip(), "selector": selector,
                                        "media": [s.removeprefix("@media ") for s, _ in stack if s.startswith("@media ")],
                                        "line": text.count("\n", 0, start + match.start()) + 1})
        boundary = brace.end()
    if stack:
        raise ValueError("Unbalanced token CSS")
    return definitions


def _applies(definition: dict, palette: str, appearance: str, viewport_width: int) -> bool:
    selector = definition["selector"]
    if not selector.startswith(":root"):
        return False
    for attr, expected in re.findall(r'\[([\w-]+)="([\w-]+)"\]', selector):
        if {"data-ui-palette": palette, "data-appearance": appearance}.get(attr) != expected:
            return False
    for media in definition["media"]:
        match = re.fullmatch(r"\(max-width:\s*(\d+)px\)", media)
        if not match:
            raise ValueError(f"Unsupported token media scope: {media}")
        if viewport_width > int(match[1]):
            return False
    return True


def resolve_theme(palette: str, appearance: str, source: Path | None = None, *, viewport_width: int = 1280) -> dict[str, str]:
    """Resolve the root cascade and aliases directly from the canonical CSS."""
    if palette not in PALETTES or appearance not in APPEARANCES:
        raise ValueError("Unknown palette or appearance")
    values = {}
    kinds = {}
    for definition in read_definitions(source):
        name = definition["name"]
        if name in kinds and kinds[name] != definition["type"]:
            raise ValueError(f"Token type changes by scope: {name}")
        kinds[name] = definition["type"]
        if _applies(definition, palette, appearance, viewport_width):
            values[name] = definition["value"]
    resolved = {}

    def resolve(name, chain=()):
        if name in chain:
            raise ValueError(f"Circular alias: {' -> '.join((*chain, name))}")
        if name not in values:
            raise ValueError(f"Undefined root token {name}")
        if name not in resolved:
            value = REFERENCE.sub(lambda m: resolve(m[1], (*chain, name)), values[name])
            if "var(" in value:
                raise ValueError(f"Unsupported token reference: {name}")
            if kinds[name] == "hsl-channels" and not re.fullmatch(CHANNELS, value):
                raise ValueError(f"Invalid HSL channels: {name}={value}")
            if kinds[name] == "hsl-alpha-channels" and not re.fullmatch(CHANNELS + r"\s*/\s*(?:0|0?\.\d+|1(?:\.0+)?)", value):
                raise ValueError(f"Invalid alpha HSL channels: {name}={value}")
            resolved[name] = value
        return resolved[name]

    for name in values:
        resolve(name)
    return resolved


def export_tokens(source: Path | None = None) -> dict:
    path = Path(source) if source is not None else TOKEN_SOURCE
    registry = {}
    for item in read_definitions(path):
        record = registry.setdefault(item["name"], {"type": item["type"], "definitions": []})
        if record["type"] != item["type"]:
            raise ValueError(f"Token type changes by scope: {item['name']}")
        record["definitions"].append({key: item[key] for key in ("selector", "media", "value", "line")})
    return {"schema_version": 1, "source": "static/css/lq/tokens.css", "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "tokens": registry,
            "themes": {palette: {appearance: resolve_theme(palette, appearance, path) for appearance in APPEARANCES} for palette in PALETTES},
            "notes": ["HSL alpha-channel tokens are wrapped once; opacity modifiers are not supported.",
                      "Scoped monitor colors and mobile size overrides are preserved in definitions; themes represent desktop documentElement values.",
                      "Runtime/component variables such as --ls-schedule-stage-height stay local and are not global tokens."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=TOKEN_SOURCE)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/lq-tokens.json")
    args = parser.parse_args()
    payload = export_tokens(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tokens": len(payload["tokens"]), "palettes": len(payload["themes"]), "output": str(args.output)}))


if __name__ == "__main__":
    main()
