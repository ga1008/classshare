"""Server-side lq_nav_menu contract: real Jinja rendering, no app/DB imports."""
from html.parser import HTMLParser
from pathlib import Path
import unittest

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from classroom_app.lq import lq_props
from classroom_app.lq_components import BUTTON_VARIANTS, SIZES, TONES
from classroom_app.lq_nav_menu import (
    NAV_MENU_ALIGNMENTS,
    NAV_MENU_CARET,
    NAV_MENU_KINDS,
    NAV_MENU_SHAPES,
    NAV_MENU_VARIANTS,
    lq_nav_menu_kind_props,
    lq_nav_menu_props,
)

ROOT = Path(__file__).resolve().parents[1]
ITEMS = [
    {"id": "homework", "label": "我的作业", "icon": "check"},
    {"id": "paused", "label": "暂不可用", "disabled": True},
    {"id": "outside", "label": "校外资源", "href": "/details", "target": "_blank"},
    {"id": "drop", "label": "退出课程", "danger": True, "group": "risk"},
]


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.text = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.nodes.append({"tag": tag, "attrs": dict(attrs)})

    def handle_data(self, data):
        self.text.append(data)

    def find(self, **match):
        return [node for node in self.nodes
                if all(node["attrs"].get(key) == value for key, value in match.items())]


class LqNavMenuPropsTests(unittest.TestCase):
    def props(self, **overrides):
        return lq_nav_menu_props(id="nav-study", label="学习", items=ITEMS, **overrides)

    def test_defaults_match_the_frozen_signature(self):
        p = self.props()
        self.assertEqual(p["variant"], "glass")
        self.assertEqual(p["tone"], "neutral")
        self.assertEqual(p["size"], "md")
        self.assertEqual(p["shape"], "capsule")
        self.assertEqual(p["align"], "start")
        self.assertEqual(p["caret"], NAV_MENU_CARET)
        self.assertEqual(p["attrs"], {"class": "lq-nav-menu", "data-lq-nav-menu": "",
                                      "data-lq-nav-align": "start"})

    def test_every_valid_combination_produces_a_trigger_bound_to_its_panel(self):
        for variant in NAV_MENU_VARIANTS:
            for tone in TONES:
                for size in SIZES:
                    for shape in NAV_MENU_SHAPES:
                        for align in NAV_MENU_ALIGNMENTS:
                            with self.subTest(variant=variant, tone=tone, size=size, shape=shape, align=align):
                                p = self.props(variant=variant, tone=tone, size=size, shape=shape, align=align)
                                trigger = p["trigger"]
                                self.assertEqual(trigger["tag"], "button")
                                self.assertEqual(trigger["classes"],
                                                 f"lq-nav-menu__trigger lq-btn lq-btn--{variant} lq-btn--{size}")
                                self.assertEqual(trigger["attrs"]["id"], "nav-study--lq-trigger")
                                self.assertEqual(trigger["attrs"]["aria-controls"], p["menu"]["attrs"]["id"])
                                self.assertEqual(trigger["attrs"]["aria-haspopup"], "menu")
                                self.assertEqual(trigger["attrs"]["aria-expanded"], "false")
                                self.assertEqual(trigger["attrs"]["data-tone"], tone)
                                self.assertEqual(trigger["attrs"]["data-lq-nav-shape"], shape)
                                self.assertEqual(trigger["attrs"]["data-lq-nav-trigger"], "")
                                self.assertEqual(p["attrs"]["data-lq-nav-align"], align)

    def test_panel_is_the_menu_products_own_output(self):
        p = self.props()
        self.assertEqual(p["menu"], lq_props("menu", id="nav-study", label="学习", items=ITEMS))

    def test_item_validation_is_delegated_and_not_reimplemented(self):
        for items in ([],
                      [{"id": "a", "label": "项"}, {"id": "a", "label": "重复"}],
                      [{"id": "a", "label": "项", "html": "<b>x</b>"}],
                      [{"id": "a", "label": "项", "href": "javascript:alert(1)"}],
                      [{"id": "a", "label": "项", "target": "_blank"}],
                      [{"id": "a", "label": "项", "disabled": "false"}],
                      [{"id": "a", "label": "  "}],
                      "not-a-list"):
            with self.subTest(items=items):
                with self.assertRaises(ValueError):
                    lq_nav_menu_props(id="nav-a", label="标题", items=items)

    def test_invalid_options_and_unknown_keys_are_rejected(self):
        rejected = [
            {"variant": "prominent"}, {"variant": "destructive"}, {"variant": "link"}, {"variant": True},
            {"variant": None}, {"tone": "teal"}, {"tone": 1}, {"size": "xl"}, {"shape": "pill"},
            {"align": "center"}, {"align": "END"}, {"icon": "Book Open"}, {"icon": 7},
            {"badge": "3"}, {"attrs": {"data-x": "1"}}, {"href": "/somewhere"}, {"disabled": True},
        ]
        for overrides in rejected:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    self.props(**overrides)
        for identity in ("nav a", "9nav", "", None, "nav#a"):
            with self.subTest(identity=identity):
                with self.assertRaises(ValueError):
                    lq_nav_menu_props(id=identity, label="标题", items=ITEMS)
        for label in ("", "   ", None, 7):
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    lq_nav_menu_props(id="nav-a", label=label, items=ITEMS)

    def test_nav_variants_are_the_documented_subset_of_button_variants(self):
        self.assertEqual(NAV_MENU_VARIANTS, ("glass", "soft", "ghost"))
        self.assertTrue(set(NAV_MENU_VARIANTS) <= set(BUTTON_VARIANTS))
        self.assertEqual(NAV_MENU_KINDS, ("nav_menu",))

    def test_dispatchers_route_the_kind_and_refuse_anything_else(self):
        self.assertEqual(lq_nav_menu_kind_props("nav_menu", id="nav-a", label="标题", items=ITEMS),
                         lq_nav_menu_props(id="nav-a", label="标题", items=ITEMS))
        with self.assertRaises(ValueError):
            lq_nav_menu_kind_props("menu", id="nav-a", label="标题", items=ITEMS)
        self.assertEqual(lq_props("nav_menu", id="nav-a", label="标题", items=ITEMS),
                         lq_nav_menu_props(id="nav-a", label="标题", items=ITEMS))


class LqNavMenuMacroTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True,
                              undefined=StrictUndefined)
        cls.env.globals["lq_props"] = lq_props
        cls.macro = cls.env.get_template("macros/lq/nav-menu.html").module.lq_nav_menu

    def render(self, **props):
        return str(self.macro(**{"id": "nav-study", "label": "学习", "items": ITEMS, **props}))

    def test_rendered_structure_matches_the_component_contract(self):
        doc = Document(self.render(icon="book-open", variant="soft", tone="primary", size="lg",
                                   shape="rounded", align="end"))
        host = doc.find(**{"data-lq-nav-menu": ""})[0]
        self.assertEqual(host["tag"], "div")
        self.assertEqual(host["attrs"]["class"], "lq-nav-menu")
        self.assertEqual(host["attrs"]["data-lq-nav-align"], "end")
        trigger = doc.find(**{"data-lq-nav-trigger": ""})[0]
        self.assertEqual(trigger["tag"], "button")
        self.assertEqual(trigger["attrs"]["type"], "button")
        self.assertEqual(trigger["attrs"]["id"], "nav-study--lq-trigger")
        self.assertEqual(trigger["attrs"]["aria-controls"], "nav-study")
        self.assertEqual(trigger["attrs"]["aria-expanded"], "false")
        self.assertIn("lq-nav-menu__trigger", trigger["attrs"]["class"])
        caret = doc.find(**{"class": "lq-nav-menu__caret"})[0]
        self.assertEqual(caret["attrs"]["aria-hidden"], "true")
        panel = doc.find(id="nav-study")[0]
        self.assertEqual(panel["attrs"]["role"], "menu")
        self.assertEqual(panel["attrs"]["class"], "lq-menu lq-glass")
        self.assertIn("hidden", panel["attrs"])

    def test_panel_markup_is_byte_identical_to_the_menu_macro(self):
        menu = str(self.env.get_template("macros/lq/menus.html").module.lq_menu("nav-study", "学习", ITEMS))
        self.assertIn(menu, self.render())

    def test_labels_and_item_text_are_escaped_not_injected(self):
        html = self.render(id="nav-escaped", label="<script>",
                           items=[{"id": "<x>", "label": "<img onerror=alert(1)>", "href": "/a?x=%22"}])
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img onerror", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn('href="/a?x=%22"', html)

    def test_panel_carries_no_second_glass_or_inline_style(self):
        html = self.render()
        self.assertNotIn("style=", html)
        self.assertEqual(html.count("lq-glass"), 1)

    def test_macro_rejects_the_same_input_the_contract_rejects(self):
        for overrides in ({"variant": "prominent"}, {"tone": "teal"}, {"size": "xl"},
                          {"shape": "pill"}, {"align": "center"}, {"items": []}):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    self.render(**overrides)


if __name__ == "__main__":
    unittest.main()
