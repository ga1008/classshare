import unittest
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


class CultivationCardPartialTest(unittest.TestCase):
    def setUp(self):
        template_root = Path(__file__).resolve().parents[1] / "templates"
        self.env = Environment(
            loader=FileSystemLoader(str(template_root)),
            autoescape=select_autoescape(("html", "xml")),
        )
        self.template = self.env.get_template("partials/cultivation_card.html")

    def render_card(self, **card):
        return self.template.render(cultivation_card=card)

    def test_renders_all_supported_variants(self):
        for variant in ("chip", "compact", "full"):
            with self.subTest(variant=variant):
                html = self.render_card(
                    variant=variant,
                    eyebrow="Course",
                    title="Data Literacy",
                    subtitle="Class A",
                    score=86.5,
                    progress_percent=72,
                    level_short="L6",
                    level_name="Foundation",
                    theme="foundation",
                    badge="ready",
                    next_label="Next level",
                    next_hint="Finish the reading task.",
                )

                self.assertIn(f"cultivation-card--{variant}", html)
                self.assertIn('data-theme="foundation"', html)
                self.assertIn("--cultivation-card-progress: 72%;", html)
                self.assertIn("Data Literacy", html)
                self.assertIn("Finish the reading task.", html)

    def test_uses_public_level_payload_fallbacks(self):
        html = self.render_card(
            level={
                "short_name": "L2",
                "level_name": "Qi Awakening",
                "theme": "qi_awakening",
            },
            score=18,
            progress_percent=24,
        )

        self.assertIn('data-theme="qi_awakening"', html)
        self.assertIn(">L2<", html)
        self.assertIn("Qi Awakening", html)
        self.assertIn("--cultivation-card-progress: 24%;", html)

    def test_missing_optional_fields_still_render_stable_shell(self):
        html = self.template.render()

        self.assertIn("cultivation-card--compact", html)
        self.assertIn('data-theme="mortal"', html)
        self.assertIn("--cultivation-card-progress: 0%;", html)

    def test_structured_next_copy_uses_public_message_fields(self):
        html = self.render_card(
            title="Course",
            next_label={"label": "Ready"},
            next_hint={
                "tier": "summit",
                "message": "Your current pace is steady.",
                "scope_label": "Hidden metadata",
            },
        )

        self.assertIn("Ready", html)
        self.assertIn("Your current pace is steady.", html)
        self.assertNotIn("Hidden metadata", html)
        self.assertNotIn("{&#39;tier&#39;", html)


if __name__ == "__main__":
    unittest.main()
