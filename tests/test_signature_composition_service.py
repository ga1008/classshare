from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from classroom_app.services.signature_composition_service import compose_signature_strip


class SignatureCompositionServiceTests(unittest.TestCase):
    def test_order_and_equal_slot_geometry_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "wide-red.png"
            second = root / "tall-blue.png"
            Image.new("RGBA", (220, 70), (220, 30, 40, 255)).save(first)
            Image.new("RGBA", (70, 180), (30, 80, 220, 255)).save(second)
            with patch("classroom_app.services.signature_composition_service.SIGNATURES_DIR", root / "signatures"):
                output = Path(compose_signature_strip([str(first), str(second)], slot_width=240, height=120, gap=20))
                repeated = Path(compose_signature_strip([str(first), str(second)], slot_width=240, height=120, gap=20))
            self.assertEqual(output, repeated)
            self.assertTrue(output.is_file())
            with Image.open(output).convert("RGBA") as image:
                self.assertEqual((500, 120), image.size)
                left_pixels = [pixel for pixel in image.crop((0, 0, 240, 120)).get_flattened_data() if pixel[3] > 0]
                right_pixels = [pixel for pixel in image.crop((260, 0, 500, 120)).get_flattened_data() if pixel[3] > 0]
                self.assertGreater(len(left_pixels), 0)
                self.assertGreater(len(right_pixels), 0)
                self.assertGreater(sum(pixel[0] for pixel in left_pixels), sum(pixel[2] for pixel in left_pixels))
                self.assertGreater(sum(pixel[2] for pixel in right_pixels), sum(pixel[0] for pixel in right_pixels))


if __name__ == "__main__":
    unittest.main()
