"""S1 token contracts: real pairs, compatibility types and deterministic export."""
import colorsys
import re
import tempfile
import unittest
from pathlib import Path

from tools.ui.export_tokens import APPEARANCES, PALETTES, export_tokens, read_definitions, resolve_theme


def rgb(channels, backing=None):
    h, s, light, *alpha = map(float, re.findall(r"-?(?:\d+(?:\.\d+)?|\.\d+)", channels))
    color = colorsys.hls_to_rgb(h / 360, light / 100, s / 100)
    if alpha:
        if backing is None:
            raise ValueError("Translucent colors require their actual backing surface")
        color = tuple(c * alpha[0] + b * (1 - alpha[0]) for c, b in zip(color, backing))
    return color


def contrast(a, b):
    def luminance(color):
        linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in color]
        return sum(c * weight for c, weight in zip(linear, (.2126, .7152, .0722)))
    low, high = sorted((luminance(a), luminance(b)))
    return (high + .05) / (low + .05)


class LqTokenTests(unittest.TestCase):
    def test_component_token_references_have_actual_definitions(self):
        root = Path(__file__).resolve().parents[1] / 'static/css/lq'
        sources = {path: re.sub(r'/\*.*?\*/', '', path.read_text(encoding='utf-8'), flags=re.S)
                   for path in root.rglob('*.css')}
        defined = {token for source in sources.values() for token in re.findall(r'(--ls-[a-z0-9-]+)\s*:', source)}
        for path, source in sources.items():
            with self.subTest(source=path.relative_to(root).as_posix()):
                references = set(re.findall(r'var\(\s*(--ls-[a-z0-9-]+)', source))
                self.assertEqual(references - defined, set(), 'Inherited color can hide an undefined semantic token')

    @classmethod
    def setUpClass(cls):
        cls.themes = {(palette, appearance): resolve_theme(palette, appearance)
                      for palette in PALETTES for appearance in APPEARANCES}

    def test_text_primary_and_semantic_pairs_on_real_surfaces(self):
        for key, tokens in self.themes.items():
            with self.subTest(theme=key):
                for level in range(3):
                    surface = rgb(tokens[f"--ls-surface-{level}"])
                    for ink in ("--ls-ink", "--ls-ink-2", "--ls-ink-3"):
                        self.assertGreaterEqual(contrast(rgb(tokens[ink]), surface), 4.5, (key, ink, level))
                    soft = rgb(tokens["--ls-primary-soft"], surface)
                    self.assertGreaterEqual(contrast(rgb(tokens["--ls-on-primary-soft"]), soft), 4.5)
                    for tone in ("success", "warning", "danger", "info", "neutral"):
                        prefix = f"--ls-tone-{tone}"
                        foreground = rgb(tokens[prefix + "-fg"])
                        self.assertGreaterEqual(contrast(foreground, surface), 4.5, (key, tone, level))
                        self.assertGreaterEqual(contrast(foreground, rgb(tokens[prefix + "-soft"], surface)), 4.5)
                self.assertGreaterEqual(contrast(rgb(tokens["--ls-primary"]), rgb(tokens["--ls-on-primary"])), 4.5)
                # Retained primary hover controls use the opaque accent foreground.
                self.assertGreaterEqual(contrast(rgb(tokens["--ls-on-primary-soft"]), rgb(tokens["--ls-on-primary"])), 4.5)
                for tone in ("success", "warning", "danger", "info", "neutral"):
                    prefix = f"--ls-tone-{tone}"
                    for background, ink in (("base", "on-base"), ("solid", "on-solid")):
                        self.assertGreaterEqual(contrast(rgb(tokens[prefix + "-" + background]), rgb(tokens[prefix + "-" + ink])), 4.5)

    def test_existing_control_and_focus_contract_includes_accent_surfaces(self):
        for key, tokens in self.themes.items():
            for name in ("--ls-background", "--ls-accent"):
                surface = rgb(tokens[name])
                for ink, minimum in (("--ls-muted-foreground", 4.5), ("--ls-input", 3), ("--ls-ring", 3)):
                    with self.subTest(theme=key, surface=name, foreground=ink):
                        self.assertGreaterEqual(contrast(rgb(tokens[ink]), surface), minimum)

    def test_dark_chat_teacher_identity_retains_hue_and_readable_foreground(self):
        for palette in PALETTES:
            light = self.themes[palette, "light"]
            dark = self.themes[palette, "dark"]
            self.assertEqual(light["--ls-chat-teacher"], light["--ls-c-violet-500"])
            self.assertEqual(dark["--ls-chat-teacher"].split()[0], light["--ls-chat-teacher"].split()[0])
            for level in range(3):
                self.assertGreaterEqual(contrast(rgb(dark["--ls-chat-teacher"]), rgb(dark[f"--ls-surface-{level}"])), 4.5)

    def test_semantics_and_content_identity_do_not_follow_user_palette(self):
        names = [name for name in self.themes["teal", "light"]
                 if name.startswith(("--ls-tone-", "--tone-attachment-", "--tone-agenda-", "--tone-course-", "--ls-c-"))]
        for appearance in APPEARANCES:
            expected = self.themes["teal", appearance]
            for palette in PALETTES:
                self.assertEqual({name: expected[name] for name in names},
                                 {name: self.themes[palette, appearance][name] for name in names})

    def test_fixed_legacy_colors_and_layout_values_remain_compatible(self):
        tokens = self.themes["indigo", "light"]
        fixed = {name: value for name, value in tokens.items() if name.startswith("--ls-c-")}
        self.assertEqual(len(fixed), 86)
        for other in self.themes.values():
            self.assertEqual(fixed, {name: other[name] for name in fixed})
            self.assertEqual(other["--ls-radius"], "0.7rem")
            self.assertEqual(other["--text-md"], "0.875rem")
            self.assertEqual(other["--font-family-sans"], '"Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC", "WenQuanYi Micro Hei", system-ui, sans-serif')
            self.assertNotEqual(other["--font-family-sans"], other["--ls-font-sans"])
        self.assertEqual(len([name for name in tokens if re.fullmatch(r"--tone-course-\d+", name)]), 10)
        self.assertEqual(len([name for name in tokens if name.startswith("--tone-attachment-")]), 7)

    def test_color_channel_types_are_preserved_in_aliases(self):
        exported = export_tokens()
        for name in ("--ls-primary", "--ls-glass-ink", "--ls-glass-muted"):
            self.assertEqual(exported["tokens"][name]["type"], "hsl-channels")
        for name in ("--ls-primary-soft", "--ls-glass-line", "--ls-scrim"):
            self.assertEqual(exported["tokens"][name]["type"], "hsl-alpha-channels")
        for name in ("--ls-glass", "--ls-glass-strong", "--primary-color"):
            self.assertEqual(exported["tokens"][name]["type"], "color")
        self.assertEqual(exported["tokens"]["--ls-primary-rgb"]["type"], "rgb-channels-comma")
        self.assertEqual(exported["tokens"]["--ls-glass-shadow"]["type"], "shadow")
        self.assertEqual(exported, export_tokens())

    def test_parser_rejects_undefined_alias_cycles_and_untyped_values(self):
        fixtures = (
            ':root { --example: var(--missing); /* @type color */ }',
            ':root { --a: var(--b); /* @type color */ --b: var(--a); /* @type color */ }',
            ':root { --example: 0 0% 0%; }',
            ':root { --example: 0 0% 0% / .5; /* @type hsl-channels */ }',
            ':root { --example: 0 0% 0% / ; /* @type hsl-alpha-channels */ }',
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "tokens.css"
            for css in fixtures:
                with self.subTest(css=css):
                    source.write_text(css, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        resolve_theme("indigo", "light", source)

    def test_dark_foreground_does_not_reuse_light_text_ink(self):
        for palette in PALETTES:
            dark = self.themes[palette, "dark"]
            self.assertEqual(dark["--ls-on-primary"], "222 47% 11%")
            self.assertNotEqual(dark["--ls-on-primary"], dark["--ls-ink"])
            self.assertEqual(self.themes[palette, "light"]["--ls-ink-3"], "215 16% 45%")

    def test_monitor_scope_is_not_exported_as_a_root_override(self):
        monitor = [item for item in read_definitions() if 'data-lq-scope="monitor"' in item["selector"]]
        self.assertEqual(len(monitor), 9)
        self.assertTrue(all(item["name"] not in self.themes["indigo", "dark"] for item in monitor))


if __name__ == "__main__":
    unittest.main()
