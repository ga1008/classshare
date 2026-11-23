"""Build three portable, uploadable teaching games from their maintained sources."""

import argparse
import io
import json
import math
from pathlib import Path
import struct
import wave
import zipfile
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/minigames"
BEACON = ROOT / "docs/minigames/example"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def encode(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def definitions():
    config = read_json(BEACON / "config.json")
    yield (
        config,
        read_json(BEACON / "contracts/writeback.json"),
        read_json(BEACON / "contracts/rules.json"),
    )

    def make_config(key, title, summary, multiplayer):
        return {
            **config,
            "gameKey": key,
            "title": title,
            "summary": summary,
            "icon": "static/icon.png",
            "standalone": "local-seats" if multiplayer else "solo",
            "modes": {"solo": not bool(multiplayer), "multiplayer": multiplayer},
            "capabilities": ["room.chat"] if multiplayer else [],
        }

    grid = make_config(
        "three-in-line",
        "双人连线棋",
        "两人轮流落子，在九宫棋盘中率先连成三子。",
        {
            "kind": "versus",
            "privateRooms": True,
            "minPlayers": 2,
            "maxPlayers": 2,
            "randomSizes": [2],
        },
    )
    yield (
        grid,
        {
            "formatVersion": 1,
            "series": "board",
            "saveVersion": 1,
            "privateFields": {},
            "milestones": [{"id": "board", "label": "棋盘", "resumeState": "board"}],
        },
        {"engine": "grid-line-v1", "size": 3, "line": 3},
    )
    puzzle = make_config(
        "star-map", "星图拼接", "观察图案与数列，补齐观星台的三块星图。", None
    )
    nodes = ["first", "second", "third", "complete"]
    edges = [
        {
            "from": a,
            "to": b,
            "type": "fit",
            "payload": {"part": part},
            "actor": "turn",
            "turn": "keep",
        }
        for a, b, part in zip(nodes, nodes[1:], ["triangle", "seven", "spark"])
    ]
    yield (
        puzzle,
        {
            "formatVersion": 1,
            "series": "stars",
            "saveVersion": 1,
            "privateFields": {},
            "milestones": [
                {"id": k, "label": label, "resumeState": k}
                for k, label in zip(nodes, ["辨形", "推演", "合图"])
            ],
        },
        {
            "engine": "finite-turn-v1",
            "initial": nodes[0],
            "states": {
                n: {"outcome": "completed" if n == "complete" else None} for n in nodes
            },
            "transitions": edges,
        },
    )


def icon(key):
    palettes = {
        "beacon-relay": ((17, 46, 72), (34, 92, 128), "#ffd987", "#7be8d0"),
        "three-in-line": ((24, 44, 62), (44, 86, 98), "#8af0d2", "#ffd482"),
        "star-map": ((30, 26, 68), (68, 52, 124), "#f5d487", "#b8a9ff"),
    }
    top, bottom, fg, accent = palettes[key]
    im = Image.new("RGBA", (384, 384))
    px = im.load()
    for y in range(384):
        t = y / 383.0
        r = round(top[0] * (1 - t) + bottom[0] * t)
        g = round(top[1] * (1 - t) + bottom[1] * t)
        b = round(top[2] * (1 - t) + bottom[2] * t)
        for x in range(384):
            px[x, y] = (r, g, b, 255)
    d = ImageDraw.Draw(im, "RGBA")
    d.ellipse((44, 44, 340, 340), outline=(255, 255, 255, 34), width=4)
    if key == "beacon-relay":
        d.polygon([(192, 120), (56, 74), (56, 152)], fill=(255, 217, 135, 52))
        d.polygon([(192, 120), (328, 74), (328, 152)], fill=(255, 217, 135, 52))
        d.polygon([(192, 58), (138, 118), (246, 118)], fill=fg)
        d.rounded_rectangle((154, 128, 230, 178), 10, fill=fg)
        d.polygon([(162, 190), (222, 190), (248, 294), (136, 294)], fill="#eef7f2")
        d.arc((64, 266, 320, 338), 14, 166, fill=accent, width=10)
    elif key == "star-map":
        d.ellipse((78, 78, 306, 306), outline=(184, 169, 255, 56), width=3)
        pts = [(76, 252), (138, 126), (224, 206), (304, 98)]
        d.line(pts, fill=accent, width=7)
        for x, y in pts:
            d.ellipse((x - 28, y - 28, x + 28, y + 28), fill=(245, 212, 135, 42))
            d.polygon(
                [
                    (x, y - 22),
                    (x + 6, y - 6),
                    (x + 22, y),
                    (x + 6, y + 6),
                    (x, y + 22),
                    (x - 6, y + 6),
                    (x - 22, y),
                    (x - 6, y - 6),
                ],
                fill=fg,
            )
    else:
        d.rounded_rectangle((64, 64, 320, 320), 36, fill=(255, 255, 255, 18))
        for v in (150, 234):
            d.line((76, v, 308, v), fill=(255, 255, 255, 64), width=5)
            d.line((v, 76, v, 308), fill=(255, 255, 255, 64), width=5)
        d.line((107, 107, 277, 277), fill=(138, 240, 210, 110), width=10)
        for x, y in ((107, 107), (192, 192), (277, 277)):
            d.ellipse((x - 27, y - 27, x + 27, y + 27), fill=fg)
        for x, y in ((192, 107), (107, 192)):
            d.ellipse((x - 24, y - 24, x + 24, y + 24), outline=accent, width=8)
    output = io.BytesIO()
    im.convert("RGB").resize((192, 192), Image.Resampling.LANCZOS).save(output, format="PNG")
    return output.getvalue()


def navigation_chime():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(
            b"".join(
                struct.pack(
                    "<h",
                    round(
                        4400
                        * math.sin(2 * math.pi * 660 * i / 16000)
                        * math.exp(-i / 1650)
                        * min(1, i / 96)
                    ),
                )
                for i in range(4800)
            )
        )
    return output.getvalue()


def build(directory):
    directory.mkdir(parents=True, exist_ok=True)
    for config, writeback, rules in definitions():
        files = {
            "main.html": (SOURCE / "main.html").read_bytes(),
            "css/game.css": (SOURCE / "game.css").read_bytes(),
            "js/game.js": (SOURCE / "game.js").read_bytes(),
            "js/lan-game-sdk.v1.js": (
                ROOT / "static/minigames/lan-game-sdk.v1.js"
            ).read_bytes(),
            "config.json": encode(config),
            "contracts/writeback.json": encode(writeback),
            "contracts/rules.json": encode(rules),
            config["icon"]: icon(config["gameKey"]),
            "js/offline-contracts.js": b"window.LanGameOfflineContracts = "
            + encode({"config": config, "writeback": writeback, "rules": rules})
            + b";\n",
        }
        if config["gameKey"] == "beacon-relay":
            files["static/返航 提示.wav"] = navigation_chime()
        target = directory / (config["gameKey"] + ".zip")
        with zipfile.ZipFile(target, "w") as archive:
            for name, data in sorted(files.items()):
                item = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
                item.compress_type = zipfile.ZIP_DEFLATED
                item.external_attr = 0o100644 << 16
                archive.writestr(item, data)
        yield target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "docs/minigames/downloads"
    )
    for package in build(parser.parse_args().output):
        print(package)
