import importlib.util
import json
from pathlib import Path
import unittest

from tools.ui.generate_lq_tones import registry

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("lq_tones_pure", ROOT / "classroom_app/lq_tones.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class LqToneTests(unittest.TestCase):
    def test_generated_levels_match_every_canonical_css_state(self):
        self.assertEqual(json.loads((ROOT / "classroom_app/lq_tones.generated.json").read_text(encoding="utf-8")), registry())
        self.assertEqual(len(module.TONE_LEVELS), 9)
        for family, states in registry().items():
            for state, level in states.items():
                self.assertEqual(module.lq_tone(family, state), {"name": f"{family}-{state}", "level": level, "known": True})

    def test_missing_and_unsafe_states_have_safe_neutral_fallback(self):
        for family, state in ((None, None), ("save", "missing"), ("__proto__", "constructor"), ("save", "x]{}"), ({}, [])):
            self.assertEqual(module.lq_tone(family, state), {"name": "neutral", "level": "neutral", "known": False})

    def test_local_save_and_server_sync_remain_distinct(self):
        self.assertEqual(module.lq_tone("save", "local_saved")["level"], "neutral")
        self.assertEqual(module.lq_tone("save", "synced")["level"], "success")
        self.assertEqual(module.lq_tone("save", "conflict")["level"], "danger")
        self.assertEqual(module.lq_tone("job", "superseded")["level"], "neutral")
        with self.assertRaises(TypeError):
            module.TONE_LEVELS["save"]["local_saved"] = "success"


if __name__ == "__main__":
    unittest.main()
