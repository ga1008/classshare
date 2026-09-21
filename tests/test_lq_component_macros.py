"""P1 real Jinja rendering, safe text/URLs and native control semantics; no app/DB."""
from html.parser import HTMLParser
import math
from pathlib import Path
import unittest

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

from classroom_app.lq_components import BUTTON_VARIANTS, TONES, lq_props


ROOT = Path(__file__).resolve().parents[1]


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

    def tag(self, name):
        return [node for node in self.nodes if node["tag"] == name]


class LqComponentMacroTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=True,
                              undefined=StrictUndefined)
        cls.env.globals["lq_props"] = lq_props

    def render(self, macro, **props):
        file = "button" if macro == "lq_btn" else "chip" if macro == "lq_chip" else "indicators"
        template = self.env.get_template(f"macros/lq/{file}.html")
        return str(getattr(template.module, macro)(**props))

    def test_all_button_variants_use_native_button_and_named_label(self):
        for variant in BUTTON_VARIANTS:
            with self.subTest(variant=variant):
                doc = Document(self.render("lq_btn", label="保存", variant=variant))
                button = doc.tag("button")[0]["attrs"]
                self.assertEqual(button["type"], "button")
                self.assertEqual(button["aria-label"], "保存")
                self.assertIn(f"lq-btn--{variant}", button["class"])
                self.assertIn("保存", doc.text)

    def test_navigation_link_keeps_real_href_and_blank_target_is_safe(self):
        doc = Document(self.render("lq_btn", label="材料", href="/materials?q=a&v=2",
                                   attrs={"target": "_blank", "rel": "opener nofollow"}))
        self.assertEqual(doc.tag("button"), [])
        attrs = doc.tag("a")[0]["attrs"]
        self.assertEqual(attrs["href"], "/materials?q=a&v=2")
        self.assertEqual(set(attrs["rel"].split()), {"nofollow", "noopener", "noreferrer"})
        self.assertNotIn("type", attrs)

    def test_submit_requires_explicit_type_and_disabled_is_native(self):
        attrs = Document(self.render("lq_btn", label="提交", type="submit", disabled=True,
                                     attrs={"aria-disabled": False})).tag("button")[0]["attrs"]
        self.assertEqual(attrs["type"], "submit")
        self.assertIn("disabled", attrs)
        self.assertEqual(attrs["aria-disabled"], "true")
        self.assertEqual(attrs["data-lq-disabled"], "true")

    def test_explainable_disabled_link_is_marked_for_shared_execution_guard(self):
        attrs = Document(self.render("lq_btn", label="查看", href="/x", aria_disabled=True)).tag("a")[0]["attrs"]
        self.assertEqual(attrs["href"], "/x")
        self.assertEqual(attrs["aria-disabled"], "true")
        self.assertEqual(attrs["data-lq-disabled"], "true")
        self.assertNotIn("disabled", attrs)

    def test_loading_preserves_label_and_marks_decorative_spinner(self):
        doc = Document(self.render("lq_btn", label="保存", loading=True, icon="save"))
        attrs = doc.tag("button")[0]["attrs"]
        self.assertEqual(attrs["aria-label"], "保存")
        self.assertEqual(attrs["aria-busy"], "true")
        self.assertEqual(attrs["data-lq-disabled"], "true")
        self.assertIn("保存", doc.text)
        spinner = next(n for n in doc.tag("span") if "lq-spinner " in n["attrs"].get("class", ""))
        self.assertEqual(spinner["attrs"]["aria-hidden"], "true")

    def test_icon_only_requires_both_icon_and_accessible_name(self):
        for props in ({"icon": "x"}, {"attrs": {"aria-label": "关闭"}}, {"label": " "}):
            with self.subTest(props=props), self.assertRaises(ValueError):
                self.render("lq_btn", **props)
        doc = Document(self.render("lq_btn", icon="x", attrs={"aria-label": "关闭"}))
        self.assertIn("lq-btn--icon", doc.tag("button")[0]["attrs"]["class"])
        self.assertEqual(doc.tag("button")[0]["attrs"]["aria-label"], "关闭")

    def test_unknown_safe_icon_name_uses_real_generated_registry_fallback(self):
        doc = Document(self.render("lq_btn", label="帮助", icon="not-an-allowed-icon"))
        self.assertEqual(len(doc.tag("svg")), 1)
        self.assertEqual(doc.tag("svg")[0]["attrs"]["aria-hidden"], "true")
        self.assertEqual(doc.tag("svg")[0]["attrs"]["focusable"], "false")
        self.assertTrue(doc.tag("path") or doc.tag("circle"))

    def test_markup_is_plain_text_in_label_badge_and_attributes(self):
        payload = Markup('<img src=x onerror="alert(1)">')
        html = self.render("lq_btn", label=payload, badge=payload,
                           attrs={"title": payload, "data-note": payload})
        doc = Document(html)
        self.assertEqual(doc.tag("img"), [])
        self.assertIn(str(payload), "".join(doc.text))
        self.assertEqual(doc.tag("button")[0]["attrs"]["title"], str(payload))
        self.assertIn("&lt;img", html)

    def test_malicious_icon_markup_is_never_treated_as_svg(self):
        for icon in (Markup("<svg onload=alert(1)>"), {"html": "x"}, "../../x"):
            with self.subTest(icon=icon), self.assertRaises(ValueError):
                self.render("lq_btn", label="关闭", icon=icon)

    def test_unsupported_attributes_and_structured_values_fail_closed(self):
        for attrs in ({"onclick": "x"}, {"onClick": "x"}, {"style": "color:red"},
                      {"class": "override"}, {"type": "submit"}, {"role": "link"},
                      {"data-x": {}}, {"aria-label": []}, {"data-x": math.nan},
                      {"data-x": math.inf}, {"data-x": -math.inf}, "onclick=alert(1)"):
            with self.subTest(attrs=attrs), self.assertRaises(ValueError):
                self.render("lq_btn", label="按钮", attrs=attrs)

    def test_core_states_and_name_win_over_passthrough(self):
        attrs = Document(self.render("lq_btn", label="保存", id="core",
            attrs={"id": "wrong", "aria-label": "wrong", "aria-labelledby": "missing",
                   "aria-hidden": True, "aria-live": "assertive", "aria-disabled": True,
                   "aria-busy": True, "data-lq-disabled": True, "data-ready": False})).tag("button")[0]["attrs"]
        self.assertEqual(attrs["id"], "core")
        self.assertEqual(attrs["aria-label"], "保存")
        self.assertEqual(attrs["data-ready"], "false")
        for key in ("aria-labelledby", "aria-hidden", "aria-live", "aria-disabled", "aria-busy", "data-lq-disabled"):
            self.assertNotIn(key, attrs)

    def test_dangerous_or_ambiguous_urls_fail_before_output(self):
        for href in ("javascript:alert(1)", "data:text/html,x", "vbscript:x", "//example.org/x",
                     "java\nscript:alert(1)", "\thttps://example.org", "\\\\example.org", "/a b", ""):
            with self.subTest(href=href), self.assertRaises(ValueError):
                self.render("lq_btn", label="链接", href=href)

    def test_allowed_urls_are_escaped_but_preserved(self):
        for href in ("/a?x=1&y=2", "../a", "#section", "https://example.org/x", "http://example.org",
                     "mailto:a@example.org", "tel:+8612345"):
            with self.subTest(href=href):
                attrs = Document(self.render("lq_btn", label="链接", href=href)).tag("a")[0]["attrs"]
                self.assertEqual(attrs["href"], href)

    def test_filter_is_a_native_pressed_button_without_hidden_or_live_override(self):
        attrs = Document(self.render("lq_chip", label="进行中", kind="filter", pressed=True,
                                     attrs={"aria-pressed": False, "aria-hidden": True, "aria-live": "polite"})).tag("button")[0]["attrs"]
        self.assertEqual(attrs["type"], "button")
        self.assertEqual(attrs["aria-pressed"], "true")
        self.assertNotIn("aria-hidden", attrs)
        self.assertNotIn("aria-live", attrs)

    def test_static_status_is_not_a_live_region_or_a_button(self):
        doc = Document(self.render("lq_chip", label="已保存", tone="success", attrs={"aria-live": "polite"}))
        self.assertEqual(doc.tag("button"), [])
        attrs = doc.nodes[0]["attrs"]
        self.assertEqual(attrs["data-tone"], "success")
        self.assertNotIn("role", attrs)
        self.assertNotIn("aria-live", attrs)
        self.assertNotIn("aria-pressed", attrs)

    def test_tag_remove_is_a_separate_named_button(self):
        doc = Document(self.render("lq_chip", label=Markup("<b>课程</b>"), kind="tag", removable=True))
        self.assertEqual(doc.nodes[0]["tag"], "span")
        self.assertEqual(len(doc.tag("button")), 1)
        attrs = doc.tag("button")[0]["attrs"]
        self.assertEqual(attrs["aria-label"], "移除<b>课程</b>")
        self.assertIn("data-lq-chip-remove", attrs)
        self.assertEqual(doc.tag("b"), [])

    def test_tag_disable_applies_to_remove_button_without_inventing_parent_role(self):
        doc = Document(self.render("lq_chip", label="课程", kind="tag", removable=True, disabled=True))
        self.assertIn("disabled", doc.tag("button")[0]["attrs"])
        self.assertNotIn("role", doc.nodes[0]["attrs"])

    def test_invalid_component_choices_and_non_boolean_flags_are_rejected(self):
        cases = [("lq_btn", {"label": "x", "variant": "raw"}), ("lq_btn", {"label": "x", "size": "xl"}),
                 ("lq_btn", {"label": "x", "disabled": "false"}),
                 ("lq_chip", {"label": "x", "kind": "menu"}), ("lq_chip", {"label": "x", "tone": "save-synced"}),
                 ("lq_chip", {"label": "x", "removable": True}), ("lq_chip", {"label": "x", "pressed": 1})]
        for macro, props in cases:
            with self.subTest(macro=macro, props=props), self.assertRaises(ValueError):
                self.render(macro, **props)

    def test_zero_badge_has_no_dom_even_for_a_dot_counter(self):
        for value in (None, "", 0, "0"):
            with self.subTest(value=value):
                self.assertEqual(self.render("lq_badge", value=value).strip(), "")
        self.assertEqual(self.render("lq_badge", value=0, dot=True, label="未读").strip(), "")
        self.assertNotIn("lq-btn__badge", self.render("lq_btn", label="消息", badge=0))

    def test_badge_dot_has_a_name_and_no_live_region(self):
        attrs = Document(self.render("lq_badge", dot=True, label="有未读消息", tone="info")).nodes[0]["attrs"]
        self.assertEqual(attrs["role"], "img")
        self.assertEqual(attrs["aria-label"], "有未读消息")
        self.assertNotIn("aria-live", attrs)
        with self.assertRaises(ValueError):
            self.render("lq_badge", dot=True)

    def test_avatar_hash_uses_unicode_codepoints_and_is_stable(self):
        for name in ("张老师", "Alice", "👩‍💻", "  学生甲  "):
            with self.subTest(name=name):
                attrs = Document(self.render("lq_avatar", name=name)).nodes[0]["attrs"]
                normalized = name.strip()
                expected = 0
                for char in normalized:
                    expected = (expected * 31 + ord(char)) & 0xFFFFFFFF
                self.assertEqual(attrs["data-avatar-bucket"], str(expected % 6))
                self.assertEqual(attrs["data-tone"], TONES[expected % 6])
                self.assertEqual(attrs["aria-label"], normalized)

    def test_avatar_image_keeps_named_fallback_and_rejects_unsafe_sources(self):
        doc = Document(self.render("lq_avatar", name="张老师", src="/api/profile/avatar", size=56))
        self.assertEqual(doc.nodes[0]["attrs"]["role"], "img")
        self.assertIn("张", doc.text)
        self.assertEqual(doc.tag("img")[0]["attrs"]["alt"], "")
        for src in ("data:image/svg+xml,x", "javascript:x", "//evil.example/x", "mailto:a@b"):
            with self.subTest(src=src), self.assertRaises(ValueError):
                self.render("lq_avatar", name="张", src=src)

    def test_avatar_requires_supported_integer_size_and_nonempty_name(self):
        for props in ({"name": ""}, {"name": "张", "size": 48}, {"name": "张", "size": 40.0}):
            with self.subTest(props=props), self.assertRaises(ValueError):
                self.render("lq_avatar", **props)

    def test_spinner_is_always_decorative(self):
        attrs = Document(self.render("lq_spinner", attrs={"aria-hidden": False, "aria-label": "wrong",
                                     "aria-labelledby": "external", "aria-live": "polite"})).nodes[0]["attrs"]
        self.assertEqual(attrs["aria-hidden"], "true")
        self.assertNotIn("aria-label", attrs)
        self.assertNotIn("aria-labelledby", attrs)
        self.assertNotIn("aria-live", attrs)

    def test_determinate_progress_has_native_name_max_and_value(self):
        attrs = Document(self.render("lq_progress", label="上传进度", value=2.5, max=10,
                                     attrs={"aria-label": "wrong", "aria-valuenow": 99, "aria-hidden": True})).tag("progress")[0]["attrs"]
        self.assertEqual(attrs["aria-label"], "上传进度")
        self.assertEqual(attrs["max"], "10")
        self.assertEqual(attrs["value"], "2.5")
        self.assertNotIn("aria-valuenow", attrs)
        self.assertNotIn("aria-hidden", attrs)

    def test_indeterminate_progress_never_claims_a_numeric_completion(self):
        attrs = Document(self.render("lq_progress", label="正在处理")).tag("progress")[0]["attrs"]
        self.assertNotIn("value", attrs)
        self.assertNotIn("aria-valuenow", attrs)
        self.assertEqual(attrs["aria-label"], "正在处理")

    def test_progress_rejects_nonfinite_or_out_of_range_numbers(self):
        for props in ({"value": -1}, {"value": 101}, {"value": math.nan}, {"value": math.inf},
                      {"value": True}, {"value": "30"}, {"max": 0}, {"max": -1}):
            with self.subTest(props=props), self.assertRaises(ValueError):
                self.render("lq_progress", label="进度", **props)

    def test_unknown_component_is_rejected_without_importing_application(self):
        with self.assertRaises(ValueError):
            lq_props("modal")


if __name__ == "__main__":
    unittest.main()
