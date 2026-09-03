"""Regression inputs from the September editor audit; exercise public boundaries."""

import copy
import unittest

from lxml import html

from classroom_app.services.lessondoc import validate_deck, validate_manifest
from classroom_app.services.lessondoc.render import extract_deck_text
from classroom_app.services.lessondoc.validate_html import sanitize_html_body, scope_html_css
from classroom_app.services.lessondoc.validate import sanitize_svg_body
from classroom_app.services.lessondoc.model import ensure_editor_ids, normalization_diagnostics


def deck(*slides, **fields):
    return dict(spec="lessondoc/2.0", kind="lesson", lesson=1, slides=list(slides), **fields)


class EditorModelRegressions(unittest.TestCase):
    def test_scaled_content_dimensions_are_finite_bounded_and_idempotent(self):
        submitted = ensure_editor_ids(deck({"layout": "canvas", "objects": [
            {"type": "text", "md": "缩放后正文", "frame": {"x": 20, "y": 30, "w": 600, "h": 200}, "natural": {"w": 300, "h": 100}}
        ]}))
        clean, _ = validate_deck(submitted)
        self.assertEqual(clean["slides"][0]["objects"][0]["natural"], {"w": 300, "h": 100})
        self.assertEqual(validate_deck(clean)[0], clean)
        submitted["slides"][0]["objects"][0]["natural"]["w"] = 100000000
        self.assertNotIn("natural", validate_deck(submitted)[0]["slides"][0]["objects"][0])

    def test_editor_does_not_guess_quiz_answer_or_truncate_codewalk_text(self):
        for block in (
            {"type": "quiz", "q": "选择答案", "options": [{"k": "A", "text": "一"}, {"k": "B", "text": "二"}], "answer": ""},
            {"type": "codewalk", "lines": [{"code": "x" * 201}]},
        ):
            original = ensure_editor_ids(deck({"layout": "content", "blocks": [block]}))
            clean, warnings = validate_deck(original)
            self.assertTrue(any(d["destructive"] for d in normalization_diagnostics(original, clean, warnings)))

    def test_budget_rejects_deep_or_nonfinite_json_before_recursing(self):
        for value in (float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                validate_deck(deck({"layout": "title"}, unknown=value))
        nested = {}
        for _ in range(40):
            nested = {"child": nested}
        with self.assertRaises(ValueError):
            validate_deck(deck({"layout": "title"}, unknown=nested))

    def test_save_diagnostics_compare_submission_not_previous_page_count(self):
        submitted = ensure_editor_ids(deck({"layout": "content", "blocks": [{"type": "text", "md": "keep"}, {"type": "code", "code": ""}]}))
        clean, warnings = validate_deck(submitted)
        diagnostics = normalization_diagnostics(submitted, clean, warnings)
        self.assertEqual([d["path"] for d in diagnostics if d["destructive"]], ["slides[0].blocks[1]"])
        submitted["slides"][0]["blocks"].pop()
        clean, warnings = validate_deck(submitted)
        self.assertFalse(any(d["destructive"] for d in normalization_diagnostics(submitted, clean, warnings)))

    def test_copied_group_internal_actions_follow_its_own_ids(self):
        group = {"type": "group", "id": "group", "frame": {"x": 0, "y": 0}, "children": [
            {"type": "text", "id": "answer", "md": "a", "frame": {"x": 0, "y": 0}},
            {"type": "button", "id": "button", "label": "show", "frame": {"x": 0, "y": 60}, "actions": [{"do": "show", "target": "answer"}]},
        ]}
        clean, _ = validate_deck(deck({"layout": "canvas", "objects": [group, copy.deepcopy(group)]}))
        for item in clean["slides"][0]["objects"]:
            self.assertEqual(item["children"][1]["actions"][0]["target"], item["children"][0]["id"])

    def test_goto_uses_stable_slide_identity_and_run_requires_player(self):
        clean, warnings = validate_deck(deck({"layout": "title", "id": "s2"}, {"layout": "content", "id": "s1", "blocks": [
            {"type": "text", "id": "text", "md": "a"},
            {"type": "button", "label": "go", "actions": [{"do": "goto", "slideId": "s2", "slide": 2}, {"do": "run", "target": "text"}]}
        ]}))
        self.assertEqual(clean["slides"][1]["blocks"][1]["actions"], [{"do": "goto", "slideId": "s2", "slide": 1}])
        self.assertTrue(warnings)

    def test_css_property_allowlist_matches_browser(self):
        import re
        from pathlib import Path
        from classroom_app.services.lessondoc.css_policy import PROPERTIES
        engine = (Path(__file__).resolve().parents[1] / "static/lessondoc/2.0/deck-engine.js").read_text(encoding="utf-8")
        browser = re.search(r'var CSS_PROPS = "([^"]+)"', engine).group(1).split()
        self.assertEqual(set(browser), PROPERTIES)

    def test_encoded_text_never_becomes_markup_after_serialization(self):
        raw = '&lt;img src=missing.png onerror=void(0)&gt;<b>ok</b>'
        clean = sanitize_html_body(raw, [], where="test")
        dom = html.fragment_fromstring(clean, create_parent="div")
        self.assertEqual(dom.xpath(".//img"), [])
        self.assertIn("<img", dom.text_content())
        self.assertEqual(sanitize_html_body(clean, [], where="test"), clean)

    def test_css_escapes_and_nested_functions_cannot_hide_urls(self):
        css = scope_html_css(r'.x{color:red;background:u\72l(https://example.invalid/x)}', "a", [], where="t")
        self.assertNotIn("example.invalid", css)
        self.assertIn("color:red", css)
        body = sanitize_html_body(r'<p style="background:u\72l(x);color:red">hi</p>', [], where="t")
        self.assertNotIn("72l", body)
        self.assertIn("color:red", body)

    def test_scoping_is_idempotent_and_comma_inside_selector_is_not_split(self):
        once = scope_html_css(':is(.a,.b){color:red}', "a", [], where="t")
        self.assertEqual(scope_html_css(once, "a", [], where="t"), once)
        self.assertIn(':is(.a,.b)', once)

    def test_html_styles_survive_ten_validations_and_id_deduplication(self):
        original = deck({"layout": "content", "blocks": [
            {"type": "html", "id": "same", "body": '<p class="x">a</p>', "css": ".ld-html-same .x{color:red}"},
            {"type": "html", "id": "same", "body": '<p class="x">b</p>', "css": ".x{color:blue}"},
        ]})
        before = copy.deepcopy(original)
        clean, _ = validate_deck(original)
        self.assertEqual(original, before)
        for _ in range(10):
            next_clean, _ = validate_deck(clean)
            self.assertEqual(next_clean, clean)
        a, b = clean["slides"][0]["blocks"]
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(a["css"], ".x{color:red}")
        self.assertEqual(b["css"], ".x{color:blue}")

    def test_media_path_policy_matches_html_and_background(self):
        bad = ["file:x", "custom:x", "a/../b.png", "../assets/../secret", "a/%2e%2e/b",
               "../assets/%252e%252e/private", "a\\b", "\x01a.png", "//example.invalid/x"]
        for src in bad:
            with self.subTest(src=src):
                clean, _ = validate_deck(deck({"layout": "title"}, {"layout": "content", "blocks": [
                    {"type": "media", "kind": "image", "src": src}, {"type": "text", "md": "keep"}
                ], "bg": {"image": {"src": src}}}))
                slide = clean["slides"][1]
                self.assertEqual(len(slide["blocks"]), 1)
                self.assertNotIn("bg", slide)
                body = sanitize_html_body('<img src="' + src + '">', [], where="t")
                self.assertNotIn("src=", body)

    def test_svg_unquoted_event_and_active_elements_are_removed(self):
        raw = '<g onclick=void(0)><a href="file:x"><text>x</text></a><image href="https://example.invalid/x"/></g>'
        clean = sanitize_svg_body(raw, [], where="t")
        self.assertNotIn("onclick", clean)
        self.assertNotIn("file:", clean)
        self.assertNotIn("example.invalid", clean)
        self.assertIn("<text>x</text>", clean)

    def test_explicit_empty_pages_survive_but_invalid_blocks_are_not_blank(self):
        pages = [{"layout": layout, "empty": True} for layout in ("content", "canvas", "two-col", "grid")]
        clean, warnings = validate_deck(deck(*pages))
        self.assertEqual(len(clean["slides"]), 4)
        self.assertFalse(warnings)
        clean, warnings = validate_deck(deck({"layout": "title"}, {
            "layout": "content", "empty": True, "blocks": [{"type": "text", "md": ""}]
        }))
        self.assertEqual(len(clean["slides"]), 1)
        self.assertTrue(warnings)

    def test_overlay_only_two_col_and_grid_pages_survive(self):
        clean, _ = validate_deck(deck(*[
            {"layout": layout, "overlays": [{"type": "text", "md": "keep", "frame": {"x": 0, "y": 0}}]}
            for layout in ("two-col", "grid")
        ]))
        self.assertEqual(len(clean["slides"]), 2)

    def test_new_containers_are_visible_to_search_and_ai_text(self):
        payload = deck({"layout": "canvas", "objects": [{"type": "codewalk", "lines": [{"code": "run()", "out": "result"}]}],
                        "overlays": [{"type": "html", "body": "<p>visible</p><script>secret</script>"}]},
                       globals=[{"type": "text", "md": "global text"}])
        text = extract_deck_text(payload)
        for expected in ("run()", "result", "visible", "global text"):
            self.assertIn(expected, text)
        self.assertNotIn("secret", text)
        self.assertIn("home text", extract_deck_text({"home": {"sections": [{"blocks": [{"type": "text", "md": "home text"}]}]}}))

    def test_home_style_and_ids_use_the_same_canonical_rules(self):
        manifest = dict(spec="lessondoc/2.0", kind="home", course={"name": "test"}, lessons=[{"n": 1}],
                        stages=[{"lessons": [1]}], home={"style": {"heroGradient": {"from": "#fff", "to": "#000"}, "cardRadius": 24},
                        "sections": [{"key": "blocks", "blocks": [{"type": "text", "id": "dup", "md": "a"},
                                                                   {"type": "text", "id": "dup", "md": "b"}]}]})
        clean, _ = validate_manifest(manifest)
        self.assertEqual(clean["home"]["style"]["cardRadius"], 24)
        self.assertIn("heroGradient", clean["home"]["style"])
        blocks = clean["home"]["sections"][0]["blocks"]
        self.assertNotEqual(blocks[0]["id"], blocks[1]["id"])


if __name__ == "__main__":
    unittest.main()
