"""LessonDoc 2.1 编辑器模型的校验单测(设计: docs/lessondoc-editor-2026-09.md §4 / §10 E0).

覆盖:新块矩阵(button/codewalk/group/html)、frame 裁剪、style 白名单(含注入样本)、
html 消毒、动作目标存在性、canvas/overlays 丢页规则、globals 限额、id 去重、
codewalk ref 越界、manifest.home、旧 deck 零变化。纯内存,不触库。
"""

import copy
import unittest

from classroom_app.services.lessondoc import spec, validate_deck, validate_manifest
from classroom_app.services.lessondoc.validate_html import sanitize_html_body, scope_html_css
from classroom_app.services.lessondoc.validate_style import clean_actions, clean_frame, clean_style


def _deck(slides=None, **overrides):
    base = {
        "spec": "lessondoc/2.0",
        "kind": "lesson",
        "lesson": 1,
        "course": "《测试课程》",
        "title": "第一课",
        "slides": slides or [
            {"layout": "title"},
            {"layout": "content", "title": "目标", "blocks": [{"type": "text", "md": "hello"}]},
        ],
    }
    base.update(overrides)
    return base


class TestFrameAndStyle(unittest.TestCase):
    def test_frame_clamped_and_optional_fields(self):
        warnings = []
        frame = clean_frame({"x": -900, "y": 10, "w": 5, "h": 9000, "r": 370, "z": "3"}, warnings, where="t")
        self.assertEqual(frame["x"], spec.FRAME_X_RANGE[0])
        self.assertEqual(frame["w"], spec.FRAME_SIZE_RANGE[0])
        self.assertEqual(frame["h"], spec.FRAME_SIZE_RANGE[1])
        self.assertEqual(frame["r"], 10)          # 370 → 10
        self.assertEqual(frame["z"], 3)
        self.assertTrue(any("裁剪" in w for w in warnings))

    def test_frame_without_xy_dropped(self):
        warnings = []
        self.assertIsNone(clean_frame({"w": 10}, warnings, where="t"))
        self.assertTrue(warnings)

    def test_style_whitelist_rejects_injection(self):
        warnings = []
        style = clean_style(
            {
                "font": "kai",
                "size": 999,
                "weight": 650,
                "color": "url(javascript:alert(1))",
                "bg": "expression(1)",
                "gradient": {"from": "#fff", "to": "primary", "angle": 720},
                "stroke": {"width": 99, "color": "#12"},
                "shadow": "drop-shadow(...)",
                "position": "fixed",
                "zIndex": 9999,
            },
            warnings,
            where="t",
        )
        self.assertEqual(style["font"], "kai")
        self.assertEqual(style["size"], spec.STYLE_SIZE_RANGE[1])
        self.assertNotIn("weight", style)
        self.assertNotIn("color", style)
        self.assertNotIn("bg", style)
        self.assertEqual(style["gradient"], {"from": "#fff", "to": "primary", "angle": 360})
        self.assertEqual(style["stroke"], {"width": 6, "color": "text"})
        self.assertNotIn("shadow", style)
        self.assertNotIn("position", style)
        self.assertNotIn("zIndex", style)
        self.assertTrue(any("position" in w and "zIndex" in w for w in warnings))

    def test_style_empty_returns_none(self):
        self.assertIsNone(clean_style({"color": "red"}, [], where="t"))
        self.assertIsNone(clean_style(None, [], where="t"))


class TestActions(unittest.TestCase):
    def test_actions_normalized(self):
        warnings = []
        actions = clean_actions(
            [
                {"do": "show", "target": "b_1", "ms": 99999},
                {"do": "move", "target": "b_2", "dx": "40", "dy": 5000, "ease": "inout"},
                {"do": "goto", "slide": 3},
                {"do": "goto"},
                {"do": "explode", "target": "b_1"},
                {"do": "hide"},
                {"do": "next"},
            ],
            warnings,
            where="t",
        )
        self.assertEqual([a["do"] for a in actions], ["show", "move", "goto", "next"])
        self.assertEqual(actions[0]["ms"], spec.ACTION_MS_RANGE[1])
        self.assertEqual(actions[1]["dx"], 40)
        self.assertEqual(actions[1]["dy"], 2000)
        self.assertEqual(actions[2]["slide"], 3)
        self.assertEqual(len(warnings), 3)

    def test_dangling_action_target_pruned_at_deck_level(self):
        deck = _deck(slides=[
            {"layout": "title"},
            {"layout": "content", "title": "t", "blocks": [
                {"type": "text", "id": "b_ans", "md": "答案", "hidden": True},
                {"type": "button", "id": "b_btn", "label": "看答案",
                 "actions": [{"do": "show", "target": "b_ans"}, {"do": "hide", "target": "b_ghost"}]},
            ]},
        ])
        clean, warnings = validate_deck(deck)
        btn = clean["slides"][1]["blocks"][1]
        self.assertEqual(btn["actions"], [{"do": "show", "target": "b_ans"}])
        self.assertTrue(any("b_ghost" in w for w in warnings))

    def test_action_target_in_globals_is_valid(self):
        deck = _deck(
            slides=[
                {"layout": "title"},
                {"layout": "content", "title": "t", "blocks": [
                    {"type": "button", "label": "x", "actions": [{"do": "toggle", "target": "g_logo"}]}]},
            ],
            globals=[{"type": "text", "id": "g_logo", "md": "LOGO", "frame": {"x": 1100, "y": 640, "w": 120, "h": 40}}],
        )
        clean, warnings = validate_deck(deck)
        self.assertEqual(len(clean["slides"][1]["blocks"][0]["actions"]), 1)
        self.assertFalse(any("不存在" in w for w in warnings))


class TestNewBlocks(unittest.TestCase):
    def test_button_requires_label_and_normalizes_variant(self):
        deck = _deck(slides=[
            {"layout": "title"},
            {"layout": "content", "title": "t", "blocks": [
                {"type": "button", "label": "  "},
                {"type": "button", "label": "Go", "variant": "neon", "size": "xl"},
            ]},
        ])
        clean, warnings = validate_deck(deck)
        blocks = clean["slides"][1]["blocks"]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["label"], "Go")
        self.assertNotIn("variant", blocks[0])
        self.assertNotIn("size", blocks[0])

    def test_codewalk_ref_validation_and_speed_clamp(self):
        deck = _deck(slides=[
            {"layout": "title"},
            {"layout": "content", "title": "t", "blocks": [
                {"type": "codewalk", "speedMs": 5, "loop": "yes", "lines": [
                    {"code": "total = 0"},
                    "for i in range(3):",
                    {"code": "    total += i", "out": "1"},
                    {"ref": 2, "out": "3"},
                    {"ref": 9, "out": "never"},
                    {"ref": -1},
                ]},
            ]},
        ])
        clean, warnings = validate_deck(deck)
        cw = clean["slides"][1]["blocks"][0]
        self.assertEqual(cw["speedMs"], spec.CODEWALK_SPEED_RANGE[0])
        self.assertIs(cw["loop"], True)
        self.assertEqual([ln.get("code", ln.get("ref")) for ln in cw["lines"]],
                         ["total = 0", "for i in range(3):", "    total += i", 2])
        self.assertEqual(sum(1 for w in warnings if "ref" in w), 2)

    def test_codewalk_without_lines_dropped(self):
        deck = _deck(slides=[
            {"layout": "title"},
            {"layout": "content", "title": "t", "blocks": [{"type": "codewalk", "lines": []}, {"type": "text", "md": "k"}]},
        ])
        clean, _ = validate_deck(deck)
        self.assertEqual([b["type"] for b in clean["slides"][1]["blocks"]], ["text"])

    def test_group_children_need_frames_and_natural_backfilled(self):
        deck = _deck(slides=[
            {"layout": "title"},
            {"layout": "canvas", "objects": [
                {"type": "group", "id": "grp", "frame": {"x": 0, "y": 0, "w": 400, "h": 200}, "children": [
                    {"type": "text", "md": "a", "frame": {"x": 10, "y": 10, "w": 100, "h": 40}},
                    {"type": "text", "md": "no frame"},
                    {"type": "group", "frame": {"x": 0, "y": 0}, "children": [
                        {"type": "group", "frame": {"x": 0, "y": 0}, "children": [
                            {"type": "text", "md": "too deep", "frame": {"x": 0, "y": 0}}]}]},
                ]},
            ]},
        ])
        clean, warnings = validate_deck(deck)
        grp = clean["slides"][1]["objects"][0]
        self.assertEqual(len(grp["children"]), 1)
        self.assertEqual(grp["natural"], {"w": 110, "h": 50})
        self.assertTrue(any("嵌套过深" in w for w in warnings))
        self.assertTrue(any("缺少 frame" in w for w in warnings))

    def test_html_block_sanitized_and_css_scoped(self):
        deck = _deck(slides=[
            {"layout": "title"},
            {"layout": "content", "title": "t", "blocks": [
                {"type": "html", "id": "h1",
                 "body": '<div class="x" onclick="evil()"><script>alert(1)</script><p style="color:red;background:url(x)">hi</p>'
                         '<a href="javascript:alert(1)">l</a><iframe src="//x"></iframe><img src="media/a.png"></div>',
                 "css": ".x{color:red} @import url(x); p{background:url(evil)} .y,.z{margin:0}"},
            ]},
        ])
        clean, warnings = validate_deck(deck)
        html = clean["slides"][1]["blocks"][0]
        self.assertNotIn("<script", html["body"])
        self.assertNotIn("onclick", html["body"])
        self.assertNotIn("iframe", html["body"])
        self.assertNotIn("javascript:", html["body"])
        self.assertNotIn("url(", html["body"])
        self.assertIn('src="media/a.png"', html["body"])
        self.assertIn(".x{color:red}", html["css"])
        self.assertIn(".y, .z{margin:0}", html["css"])
        self.assertNotIn("@import", html["css"])
        self.assertNotIn("evil", html["css"])
        self.assertTrue(any("不允许的标签" in w for w in warnings))

    def test_html_block_gets_deterministic_id(self):
        warnings = []
        self.assertEqual(sanitize_html_body("<b>x</b>", warnings, where="t"), "<b>x</b>")
        deck = _deck(slides=[
            {"layout": "title"},
            {"layout": "content", "title": "t", "blocks": [{"type": "html", "body": "<b>x</b>"}]},
        ])
        a, _ = validate_deck(copy.deepcopy(deck))
        b, _ = validate_deck(copy.deepcopy(deck))
        self.assertEqual(a["slides"][1]["blocks"][0]["id"], b["slides"][1]["blocks"][0]["id"])
        self.assertTrue(a["slides"][1]["blocks"][0]["id"].startswith("h"))

    def test_scope_css_drops_at_rules(self):
        warnings = []
        css = scope_html_css("@media (max-width:1px){.a{color:red}} .b{color:blue}", "k", warnings, where="t")
        self.assertEqual(css, ".ld-html-k .b{color:blue}")
        self.assertTrue(any("@ 规则" in w for w in warnings))


class TestSlideAndDeckRules(unittest.TestCase):
    def test_canvas_layout_requires_objects(self):
        deck = _deck(slides=[
            {"layout": "title"},
            {"layout": "canvas", "objects": [{"type": "text", "md": "no frame"}]},
            {"layout": "canvas", "objects": [{"type": "text", "md": "ok", "frame": {"x": 1, "y": 2}}]},
        ])
        clean, warnings = validate_deck(deck)
        self.assertEqual(len(clean["slides"]), 2)
        self.assertEqual(clean["slides"][1]["objects"][0]["frame"]["w"], 320)   # 默认宽
        self.assertTrue(any("自由排版页无有效元素" in w for w in warnings))

    def test_content_page_with_only_overlays_kept(self):
        deck = _deck(slides=[
            {"layout": "title"},
            {"layout": "content", "title": "t", "blocks": [],
             "overlays": [{"type": "text", "md": "sticker", "frame": {"x": 900, "y": 40, "w": 200, "h": 60}}]},
        ])
        clean, _ = validate_deck(deck)
        self.assertEqual(len(clean["slides"]), 2)
        self.assertEqual(clean["slides"][1]["blocks"], [])
        self.assertEqual(len(clean["slides"][1]["overlays"]), 1)

    def test_positioned_limit_and_globals_limit(self):
        many = [{"type": "text", "md": str(i), "frame": {"x": i, "y": 0}} for i in range(60)]
        deck = _deck(slides=[{"layout": "title"}, {"layout": "canvas", "objects": many}], globals=many[:20])
        clean, warnings = validate_deck(deck)
        self.assertEqual(len(clean["slides"][1]["objects"]), spec.MAX_POSITIONED_PER_SLIDE)
        self.assertEqual(len(clean["globals"]), spec.MAX_GLOBALS)
        self.assertTrue(any("全局元素超过" in w for w in warnings))

    def test_globals_flags_and_exclude_ids_cleaned(self):
        deck = _deck(globals=[
            {"type": "text", "md": "g", "frame": {"x": 0, "y": 0}, "skipCovers": 0,
             "excludeSlides": ["s_ok", "bad id!", ""]},
        ])
        clean, _ = validate_deck(deck)
        g = clean["globals"][0]
        self.assertIs(g["skipCovers"], False)
        self.assertEqual(g["excludeSlides"], ["s_ok", "badid"])

    def test_bg_validation(self):
        deck = _deck(
            bg={"color": "primary-soft", "image": {"src": "https://x/y.png"}},
            slides=[
                {"layout": "title"},
                {"layout": "content", "title": "t", "blocks": [{"type": "text", "md": "k"}],
                 "bg": {"image": {"src": "media/bg.jpg", "fit": "weird", "scale": 9999, "rotate": 30, "opacity": 2},
                        "tint": {"color": "#000", "opacity": 0.5}}},
            ],
        )
        clean, warnings = validate_deck(deck)
        self.assertEqual(clean["bg"], {"color": "primary-soft"})
        img = clean["slides"][1]["bg"]["image"]
        self.assertEqual(img["fit"], "cover")
        self.assertEqual(img["scale"], spec.BG_SCALE_RANGE[1])
        self.assertEqual(img["opacity"], 1)
        self.assertEqual(clean["slides"][1]["bg"]["tint"]["opacity"], 0.5)
        self.assertTrue(any("bg.image 路径不合规" in w for w in warnings))

    def test_duplicate_ids_reassigned(self):
        deck = _deck(slides=[
            {"layout": "title", "id": "s1"},
            {"layout": "content", "id": "s1", "title": "t", "blocks": [
                {"type": "text", "id": "dup", "md": "a"},
                {"type": "text", "id": "dup", "md": "b"},
            ]},
        ])
        clean, warnings = validate_deck(deck)
        ids = [clean["slides"][0]["id"], clean["slides"][1]["id"]] + [b["id"] for b in clean["slides"][1]["blocks"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(sum(1 for w in warnings if "重复" in w), 2)

    def test_hidden_and_name_normalized(self):
        deck = _deck(slides=[
            {"layout": "title"},
            {"layout": "content", "title": "t", "blocks": [
                {"type": "text", "md": "a", "hidden": 0, "name": "   "},
                {"type": "text", "md": "b", "hidden": "yes", "name": "提示卡"},
            ]},
        ])
        clean, _ = validate_deck(deck)
        a, b = clean["slides"][1]["blocks"]
        self.assertNotIn("hidden", a)
        self.assertNotIn("name", a)
        self.assertIs(b["hidden"], True)
        self.assertEqual(b["name"], "提示卡")

    def test_legacy_deck_unchanged(self):
        """2.0 deck(无任何 2.1 字段)经校验后与旧行为完全一致——零告警、零新增键。"""
        deck = _deck(slides=[
            {"layout": "title"},
            {"layout": "content", "section": "开场", "title": "目标", "blocks": [
                {"type": "cards", "cols": 2, "items": [{"title": "a", "text": "b", "step": 1}]},
                {"type": "quiz", "q": "?", "options": [{"k": "A", "text": "x"}, {"k": "B", "text": "y"}], "answer": "B"},
            ]},
            {"layout": "end", "summary": "s"},
        ])
        clean, warnings = validate_deck(copy.deepcopy(deck))
        self.assertEqual(warnings, [])
        for key in ("globals", "bg"):
            self.assertNotIn(key, clean)
        for slide in clean["slides"]:
            for key in ("id", "bg", "overlays", "objects"):
                self.assertNotIn(key, slide)
        self.assertEqual(clean["slides"][1]["blocks"], deck["slides"][1]["blocks"])


class TestManifestHome(unittest.TestCase):
    def _manifest(self, **overrides):
        base = {
            "spec": "lessondoc/2.0",
            "kind": "home",
            "course": {"name": "课"},
            "lessons": [{"n": 1, "title": "一"}],
            "stages": [{"label": "全部", "lessons": [1]}],
        }
        base.update(overrides)
        return base

    def test_home_sections_validated_and_backfilled(self):
        manifest, warnings = validate_manifest(self._manifest(home={
            "bg": {"color": "#eee"},
            "sections": [
                {"key": "nav", "title": "课次", "hidden": True},
                {"key": "blocks", "title": "说明", "blocks": [{"type": "text", "md": "hi"}, {"type": "nope"}]},
                {"key": "hero", "stats": ["credits", "bogus"]},
                {"key": "nav"},
                {"key": "alien"},
            ],
        }))
        home = manifest["home"]
        self.assertEqual(home["bg"], {"color": "#eee"})
        keys = [s["key"] for s in home["sections"]]
        self.assertEqual(keys, ["nav", "blocks", "hero", "mindmap", "tabs", "footer"])
        self.assertIs(home["sections"][0]["hidden"], True)
        self.assertEqual(home["sections"][2]["stats"], ["credits"])
        self.assertEqual(len(home["sections"][1]["blocks"]), 2)     # 未知块→占位
        self.assertEqual(sum(1 for w in warnings if "未知或重复" in w), 2)

    def test_manifest_without_home_untouched(self):
        manifest, _ = validate_manifest(self._manifest())
        self.assertNotIn("home", manifest)


if __name__ == "__main__":
    unittest.main()
