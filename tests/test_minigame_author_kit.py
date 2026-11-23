import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
import zipfile

from classroom_app.services import minigame_author_kit as kit
from classroom_app.services import minigame_package_validation as platform
from classroom_app.services.lesson_plan_markdown import parse_blocks
from tools.minigame_pack import build


EXAMPLES = ("star-map", "beacon-relay", "three-in-line")
SCHEMAS = ("config", "writeback", "rules", "grid-rules")


class MinigameAuthorKitTests(unittest.TestCase):
    def test_kit_contains_the_live_contracts_sdk_examples_and_tools(self):
        shared = {
            "tools/minigame_pack.py",
            "classroom_app/__init__.py",
            "classroom_app/services/__init__.py",
            "classroom_app/services/minigame_package_validation.py",
            "classroom_app/services/minigame_runtime_rules.py",
            "static/minigames/lan-game-sdk.v1.js",
            *(f"docs/minigames/contracts/{name}.schema.json" for name in SCHEMAS),
            *(f"docs/minigames/downloads/{name}.zip" for name in EXAMPLES),
        }
        with zipfile.ZipFile(io.BytesIO(kit.kit_bytes())) as archive:
            self.assertCountEqual(archive.namelist(), shared | {"AI-GAME-SPEC.md", "requirements-author.txt"})
            for path in sorted(shared):
                with self.subTest(path=path):
                    self.assertEqual(archive.read(path), (platform.BASE / path).read_bytes())
            self.assertEqual(archive.read("AI-GAME-SPEC.md"),
                             (platform.BASE / "docs/minigames/ai-author-spec.md").read_bytes())
            dependencies = {"fastapi", "jsonschema", "pillow"}
            locked = (platform.BASE / "requirements.lock.txt").read_text(encoding="utf-8").splitlines()
            self.assertCountEqual(archive.read("requirements-author.txt").decode("utf-8").splitlines(),
                                  [pin for pin in locked if pin.partition("==")[0].lower() in dependencies])

    def test_documented_minimal_solo_game_builds_with_the_current_platform(self):
        work = Path(self.enterContext(TemporaryDirectory()))
        source = work / "game"
        expected = {"config.json", "contracts/writeback.json", "main.html", "js/game.js"}
        snippets = {}
        current_file = None
        for block in parse_blocks(kit.guide_bytes().decode("utf-8")):
            if block["type"] == "heading":
                current_file = block["text"].strip("`")
            elif current_file in expected and block["type"] == "code":
                self.assertNotIn(current_file, snippets)
                snippets[current_file] = block["text"]
        self.assertEqual(set(snippets), expected)
        for name, code in snippets.items():
            output = source / name
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(code, encoding="utf-8")
        icon = source / "static/icon.png"
        icon.parent.mkdir()
        with zipfile.ZipFile(platform.BASE / "docs/minigames/downloads/star-map.zip") as sample:
            icon.write_bytes(sample.read("static/icon.png"))
        accepted = build(source, work / "solo.zip")
        self.assertEqual(accepted["config"]["modes"], {"solo": True, "multiplayer": None})
        self.assertIsNone(accepted["rules"])

    def test_extracted_tool_validates_all_examples_and_builds_a_compatible_game(self):
        work = Path(self.enterContext(TemporaryDirectory()))
        bundle = work / "download"
        with zipfile.ZipFile(io.BytesIO(kit.kit_bytes())) as archive:
            archive.extractall(bundle)

        def run_cli(*args):
            # The author has the extracted kit and installed dependencies, with no repository on PYTHONPATH.
            proc = subprocess.run(
                [sys.executable, "-I", "-B", "-X", "utf8", str(bundle / "tools/minigame_pack.py"), *args],
                cwd=work, capture_output=True, encoding="utf-8", errors="replace", timeout=60,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
            return json.loads(proc.stdout)

        for name in EXAMPLES:
            with self.subTest(example=name):
                report = run_cli("validate", bundle / f"docs/minigames/downloads/{name}.zip")
                self.assertEqual(report["staticValidation"], "passed")
                self.assertEqual(report["gameKey"], name)

        source, output = work / "game", work / "classroom-stars.zip"
        with zipfile.ZipFile(bundle / "docs/minigames/downloads/star-map.zip") as example:
            example.extractall(source)
        manifest = source / "config.json"
        config = json.loads(manifest.read_bytes())
        config.update(gameKey="classroom-stars", title="课堂星图")
        manifest.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        controller = source / "js/game.js"
        controller.write_text(controller.read_text(encoding="utf-8").replace('"star-map"', '"classroom-stars"'),
                              encoding="utf-8")
        # The pack command regenerates the offline contract and installs the official SDK.
        for name in ("offline-contracts.js", "lan-game-sdk.v1.js"):
            (source / "js" / name).unlink()
        report = run_cli("pack", source, output)
        verification_dir = work / "platform-check"
        verification_dir.mkdir()
        accepted = platform.validate_package(output, verification_dir)
        self.assertEqual(accepted["config"], config)
        self.assertEqual(report["digest"], accepted["digest"])
        self.assertEqual(run_cli("validate", output)["digest"], accepted["digest"])
