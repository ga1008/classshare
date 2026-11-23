"""Teacher handoff materials, built from the same contracts and tools as this release."""

from functools import lru_cache
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from markupsafe import Markup

from .lesson_plan_markdown import blocks_to_html, parse_blocks

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "docs/minigames/ai-author-spec.md"
KIT_FILES = (
    "tools/minigame_pack.py",
    "classroom_app/__init__.py",
    "classroom_app/services/__init__.py",
    "classroom_app/services/minigame_package_validation.py",
    "classroom_app/services/minigame_runtime_rules.py",
    "static/minigames/lan-game-sdk.v1.js",
    "docs/minigames/contracts/config.schema.json",
    "docs/minigames/contracts/writeback.schema.json",
    "docs/minigames/contracts/rules.schema.json",
    "docs/minigames/contracts/grid-rules.schema.json",
    "docs/minigames/downloads/star-map.zip",
    "docs/minigames/downloads/beacon-relay.zip",
    "docs/minigames/downloads/three-in-line.zip",
)


@lru_cache(maxsize=1)
def guide_bytes() -> bytes:
    return SPEC.read_bytes()


@lru_cache(maxsize=1)
def guide_page():
    blocks = parse_blocks(guide_bytes().decode("utf-8"))
    contents = []
    for block in blocks:
        if block["type"] == "heading" and block["level"] == 2:
            anchor = f"section-{len(contents) + 1}"
            block["anchor"] = anchor
            contents.append({"id": anchor, "title": block["text"]})
    return Markup(blocks_to_html(blocks, heading_offset=0)), contents


@lru_cache(maxsize=1)
def kit_bytes() -> bytes:
    lock = (ROOT / "requirements.lock.txt").read_text(encoding="utf-8").splitlines()
    pins = {line.partition("==")[0].lower(): line for line in lock if "==" in line}
    requirements = "".join(pins[name] + "\n" for name in ("fastapi", "jsonschema", "pillow"))
    members = {"AI-GAME-SPEC.md": guide_bytes(), "requirements-author.txt": requirements.encode("utf-8")}
    members.update((name, (ROOT / name).read_bytes()) for name in KIT_FILES)
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for name, data in members.items():
            entry = ZipInfo(name)
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data, compress_type=ZIP_DEFLATED)
    return output.getvalue()
