"""Upload normalization + watermarked preview generation for signatures."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from classroom_app.services import signature_image_service as sis


def _signature_png(width: int = 300, height: int = 150, *, blank: bool = False) -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (width, height), "white")
    if not blank:
        draw = ImageDraw.Draw(image)
        # A plausible autograph stroke well inside the canvas.
        draw.line((40, 100, 120, 40), fill=(20, 20, 30), width=6)
        draw.line((120, 40, 200, 110), fill=(20, 20, 30), width=6)
        draw.arc((180, 50, 260, 110), 0, 300, fill=(20, 20, 30), width=5)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class NormalizeUploadImageTests(unittest.TestCase):
    def test_normalizes_to_trimmed_transparent_png(self) -> None:
        from PIL import Image

        data, ext, mime = sis.normalize_upload_image(_signature_png())
        self.assertEqual(".png", ext)
        self.assertEqual("image/png", mime)
        result = Image.open(io.BytesIO(data))
        self.assertEqual("RGBA", result.mode)
        # Trimmed: strokes span x 40..260, y 40..110 → cropped well below 300×150.
        self.assertLess(result.width, 280)
        self.assertLess(result.height, 130)
        # White background became transparent: corner pixel alpha 0.
        self.assertEqual(0, result.getpixel((0, 0))[3])

    def test_blank_image_rejected(self) -> None:
        with self.assertRaises(sis.SignatureImageError):
            sis.normalize_upload_image(_signature_png(blank=True))

    def test_tiny_image_rejected(self) -> None:
        with self.assertRaises(sis.SignatureImageError):
            sis.normalize_upload_image(_signature_png(40, 20))

    def test_garbage_bytes_rejected(self) -> None:
        with self.assertRaises(sis.SignatureImageError):
            sis.normalize_upload_image(b"not an image at all")


class PreviewTests(unittest.TestCase):
    def test_preview_is_generated_downscaled_and_cached(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source.png"
            source.write_bytes(_signature_png(1200, 600))
            with patch.object(sis, "SIGNATURES_DIR", tmp_path):
                target = sis.ensure_preview("f" * 64, source)
                self.assertTrue(target.is_file())
                self.assertEqual(tmp_path / "_previews" / ("f" * 64 + ".png"), target)
                with Image.open(target) as preview:
                    self.assertLessEqual(preview.width, sis.PREVIEW_MAX_WIDTH)
                    self.assertLessEqual(preview.height, sis.PREVIEW_MAX_HEIGHT)
                first_mtime = target.stat().st_mtime_ns
                # Second call hits the cache (no rewrite).
                sis.ensure_preview("f" * 64, source)
                self.assertEqual(first_mtime, target.stat().st_mtime_ns)


if __name__ == "__main__":
    unittest.main()
