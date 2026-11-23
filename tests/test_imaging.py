import os
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import imaging  # noqa: E402

try:
    import pillow_heif  # noqa: F401
    HAVE_HEIF = True
except ImportError:
    HAVE_HEIF = False


def card(w=40, h=30):
    """Left-to-right ramp so every column is distinguishable, red 5x5 top-left."""
    rgb = np.zeros((h, w, 3), np.uint8)
    rgb[..., 0] = np.linspace(10, 250, w, dtype=np.uint8)[None, :]
    rgb[..., 1] = 100
    rgb[:5, :5] = (255, 0, 0)
    return rgb


class CropPad(unittest.TestCase):
    def test_plain_crop_has_no_mask(self):
        rgb = card()
        out, mask = imaging.crop_pad(rgb, None, (5, 6, 10, 8), "gray")
        self.assertEqual(out.shape, (8, 10, 3))
        np.testing.assert_array_equal(out, rgb[6:14, 5:15])
        self.assertEqual(mask.shape, (8, 10))
        self.assertEqual(mask.max(), 0)

    def test_alpha_becomes_a_ComfyUI_style_mask(self):
        alpha = np.full((30, 40), 255, np.uint8)
        alpha[:10] = 0
        _, mask = imaging.crop_pad(card(), alpha, (0, 0, 40, 30), "gray")
        self.assertEqual(mask[:10].min(), 1.0)
        self.assertEqual(mask[10:].max(), 0.0)

    def test_solid_pad_fills_and_masks_the_overhang(self):
        rgb = card()
        for fill, value in (("gray", 128), ("black", 0), ("white", 255)):
            out, mask = imaging.crop_pad(rgb, None, (-4, -2, 50, 36), fill)
            self.assertEqual(out.shape, (36, 50, 3))
            self.assertTrue((out[:2] == value).all(), fill)          # top pad
            self.assertTrue((out[:, :4] == value).all(), fill)       # left pad
            np.testing.assert_array_equal(out[2:32, 4:44], rgb)      # image untouched, in place
            self.assertTrue((mask[:2] == 1).all() and (mask[:, :4] == 1).all())
            self.assertEqual(mask[2:32, 4:44].max(), 0)

    def test_edge_pad_repeats_the_border(self):
        rgb = card()
        out, mask = imaging.crop_pad(rgb, None, (-3, -3, 46, 36), "edge")
        np.testing.assert_array_equal(out[3:33, 3:43], rgb)
        np.testing.assert_array_equal(out[0, 3:43], rgb[0])          # row above = first row
        np.testing.assert_array_equal(out[10, 0], rgb[7, 0])         # left of row 7 = its first pixel
        np.testing.assert_array_equal(out[0, 0], rgb[0, 0])          # corner
        self.assertEqual(mask[0, 0], 1.0)                            # still marked as padding

    def test_overhang_on_every_side_and_far_outside(self):
        rgb = card()
        out, mask = imaging.crop_pad(rgb, None, (-100, -100, 300, 300), "black")
        self.assertEqual(out.shape, (300, 300, 3))
        self.assertEqual(mask.sum(), 300 * 300 - 40 * 30)
        out, mask = imaging.crop_pad(rgb, None, (39, 29, 10, 10), "white")    # one pixel of image
        self.assertEqual(mask.sum(), 100 - 1)

    def test_partial_overhang_keeps_alpha_of_the_overlap(self):
        alpha = np.full((30, 40), 255, np.uint8)
        alpha[:, :10] = 0
        _, mask = imaging.crop_pad(card(), alpha, (-5, 0, 20, 30), "gray")
        self.assertTrue((mask[:, :5] == 1).all())      # padding
        self.assertTrue((mask[:, 5:15] == 1).all())    # transparent image columns
        self.assertTrue((mask[:, 15:] == 0).all())


class Files(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def path(self, name):
        return os.path.join(self.dir.name, name)

    def test_exif_orientation_is_applied_and_size_agrees(self):
        im = Image.fromarray(card(40, 30))
        ex = Image.Exif()
        ex[0x0112] = 6
        im.save(self.path("rot.jpg"), exif=ex.tobytes())
        self.assertEqual(imaging.read_size(self.path("rot.jpg")), (30, 40))
        rgb, _ = imaging.load_arrays(self.path("rot.jpg"))
        self.assertEqual(rgb.shape[:2], (40, 30))

    def test_rgba_png_gives_alpha(self):
        Image.new("RGBA", (8, 6), (1, 2, 3, 0)).save(self.path("a.png"))
        rgb, alpha = imaging.load_arrays(self.path("a.png"))
        self.assertEqual((rgb.shape, alpha.shape), ((6, 8, 3), (6, 8)))
        self.assertEqual(alpha.max(), 0)

    def test_palette_png_with_transparency_gives_alpha(self):
        im = Image.new("P", (4, 4), 0)
        im.info["transparency"] = 0
        im.save(self.path("p.png"), transparency=0)
        _, alpha = imaging.load_arrays(self.path("p.png"))
        self.assertIsNotNone(alpha)

    def test_preview_is_small_but_reports_the_true_size(self):
        Image.fromarray(card(3000, 2000)).save(self.path("big.jpg"))
        body, content_type, w, h = imaging.encode_preview(self.path("big.jpg"), 400)
        self.assertEqual((w, h, content_type), (3000, 2000, "image/jpeg"))
        import io
        self.assertEqual(Image.open(io.BytesIO(body)).size, (400, 267))

    def test_preview_of_transparent_image_is_png(self):
        Image.new("RGBA", (20, 20), (0, 0, 0, 0)).save(self.path("a.png"))
        self.assertEqual(imaging.encode_preview(self.path("a.png"), 400)[1], "image/png")

    @unittest.skipUnless(HAVE_HEIF, "pillow-heif not installed")
    def test_heic_roundtrip_and_orientation(self):
        im = Image.fromarray(card(40, 30))
        im.save(self.path("c.heic"), quality=100)
        self.assertEqual(imaging.read_size(self.path("c.heic")), (40, 30))
        ex = Image.Exif()
        ex[0x0112] = 6
        im.save(self.path("r.heic"), exif=ex.tobytes(), quality=100)
        rgb, _ = imaging.load_arrays(self.path("r.heic"))
        self.assertEqual(rgb.shape[:2], (40, 30))
        self.assertEqual(imaging.read_size(self.path("r.heic")), (30, 40))

    def test_missing_heif_support_is_a_readable_error(self):
        saved = imaging._heif_registered
        imaging._heif_registered = False
        self.addCleanup(setattr, imaging, "_heif_registered", saved)
        open(self.path("x.heic"), "wb").write(b"not really")
        with self.assertRaisesRegex(RuntimeError, "pip install pillow-heif"):
            imaging.open_image(self.path("x.heic"))


if __name__ == "__main__":
    unittest.main()
