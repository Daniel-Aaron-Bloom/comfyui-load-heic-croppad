import io
import os
import random
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageOps

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


# The straightforward versions that imaging.py's memory-lean ones replaced.
# The lean ones must give the same bytes (and the same float bits).

def reference_to_arrays(img):
    img = ImageOps.exif_transpose(img)
    if img.mode == "I":
        img = img.point(lambda v: v * (1 / 255))
    bands = img.getbands()
    has_alpha = ("A" in bands or "a" in bands
                 or (img.mode == "P" and "transparency" in img.info))
    rgb = np.asarray(img.convert("RGB"))
    alpha = np.asarray(img.convert("RGBA").getchannel("A")) if has_alpha else None
    return rgb, alpha


def reference_crop_pad(rgb, alpha, box, fill):
    x, y, w, h = box
    ih, iw = rgb.shape[:2]
    if x >= 0 and y >= 0 and x + w <= iw and y + h <= ih:
        out = rgb[y:y + h, x:x + w]
        if alpha is None:
            return out, np.zeros((h, w), np.float32)
        return out, 1.0 - alpha[y:y + h, x:x + w].astype(np.float32) / 255.0
    if fill == "edge":
        cols = np.clip(np.arange(x, x + w), 0, iw - 1)
        rows = np.clip(np.arange(y, y + h), 0, ih - 1)
        out = rgb[rows[:, None], cols[None, :]]
    else:
        out = np.full((h, w, 3), imaging.PAD_FILLS[fill], np.uint8)
        imaging._paste(out, rgb, -x, -y)
    mask = np.ones((h, w), np.float32)
    src_mask = np.zeros((ih, iw), np.float32) if alpha is None else 1.0 - alpha.astype(np.float32) / 255.0
    imaging._paste(mask, src_mask, -x, -y)
    return out, mask


def noisy(h, w, seed=0, channels=3):
    return np.random.default_rng(seed).integers(0, 256, (h, w, channels), dtype=np.uint8)


class LeanMatchesReference(unittest.TestCase):
    def test_crop_pad_is_identical_for_every_fill_and_box(self):
        rng = random.Random(5)
        for seed in range(60):
            iw, ih = rng.randint(1, 40), rng.randint(1, 30)
            rgb = noisy(ih, iw, seed)
            alpha = noisy(ih, iw, seed + 1, 1)[..., 0] if seed % 2 else None
            for fill in imaging.PAD_CHOICES:
                # boxes on, over, and entirely off the image
                x, y = rng.randint(-60, iw + 20), rng.randint(-60, ih + 20)
                box = (x, y, rng.randint(1, 80), rng.randint(1, 60))
                want, want_mask = reference_crop_pad(rgb, alpha, box, fill)
                got, got_mask = imaging.crop_pad(rgb, alpha, box, fill)
                np.testing.assert_array_equal(got, want, (seed, fill, box))
                self.assertEqual(got_mask.dtype, np.float32)
                np.testing.assert_array_equal(got_mask.view(np.uint32), want_mask.view(np.uint32), (seed, fill, box))

    def test_to_float_matches_astype_divide_to_the_bit(self):
        for shape in ((1, 1, 3), (7, 5, 3), (300, 3, 3), (513, 2, 3)):     # across the 256-row bands
            rgb = noisy(*shape[:2], 3)
            want = rgb.astype(np.float32) / 255.0
            got = imaging.to_float(rgb)
            self.assertEqual((got.dtype, got.flags.writeable), (np.float32, True))
            np.testing.assert_array_equal(got.view(np.uint32), want.view(np.uint32))
        view = noisy(40, 40, 4)[5:20, 7:30]                 # a non-contiguous crop, as the node passes
        np.testing.assert_array_equal(imaging.to_float(view), view.astype(np.float32) / 255.0)

    def test_to_arrays_matches_for_every_mode_and_orientation(self):
        modes = {
            "RGB": Image.fromarray(noisy(6, 9, 1)),
            "RGBA": Image.fromarray(noisy(6, 9, 2, 4), "RGBA"),
            "L": Image.fromarray(noisy(6, 9, 3, 1)[..., 0], "L"),
            "LA": Image.fromarray(noisy(6, 9, 4, 2), "LA"),
            "P": Image.fromarray(noisy(6, 9, 5, 1)[..., 0], "L").convert("P"),
            "CMYK": Image.fromarray(noisy(6, 9, 6, 4), "CMYK"),
            "I": Image.fromarray(noisy(6, 9, 7, 1)[..., 0].astype(np.int32), "I"),
        }
        p = Image.fromarray(noisy(6, 9, 8, 1)[..., 0], "L").convert("P")
        p.info["transparency"] = 3
        modes["P+transparency"] = p
        for name, im in modes.items():
            for orientation in (None, 1, 2, 3, 4, 5, 6, 7, 8):
                im = im.copy()
                if orientation:
                    ex = Image.Exif()
                    ex[0x0112] = orientation
                    im.info["exif"] = ex.tobytes()
                want_rgb, want_alpha = reference_to_arrays(im)
                got_rgb, got_alpha = imaging.to_arrays(im)
                np.testing.assert_array_equal(got_rgb, want_rgb, (name, orientation))
                self.assertEqual(got_alpha is None, want_alpha is None, (name, orientation))
                if want_alpha is not None:
                    np.testing.assert_array_equal(got_alpha, want_alpha, (name, orientation))
                self.assertEqual(imaging.upright_size(im), got_rgb.shape[1::-1], (name, orientation))

    def test_to_arrays_leaves_the_source_image_alone(self):
        im = Image.fromarray(noisy(6, 9, 1))
        before = im.tobytes()
        imaging.to_arrays(im)
        self.assertEqual(im.tobytes(), before)


def reference_encode_preview(path, max_edge):
    """encode_preview as it was: every image through arrays, then shrunk."""
    with imaging.open_image(path) as img:
        width, height = imaging.upright_size(img)
        if img.format == "JPEG":
            img.draft("RGB", (max_edge, max_edge))
        rgb, alpha = reference_to_arrays(img)
    pil = Image.fromarray(rgb)
    if alpha is not None:
        pil.putalpha(Image.fromarray(alpha))
    pil.thumbnail((max_edge, max_edge), Image.LANCZOS, reducing_gap=2.0)
    buf = io.BytesIO()
    if alpha is not None:
        pil.save(buf, "PNG", compress_level=1)
        return buf.getvalue(), "image/png", width, height
    pil.save(buf, "JPEG", quality=88)
    return buf.getvalue(), "image/jpeg", width, height


class PreviewMatchesReference(unittest.TestCase):
    """The preview shrinks before it converts (a gigabyte less at 64 MP). It
    must show the same picture, the right way up."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def save(self, fmt, mode, orientation):
        arr = np.random.default_rng(3).integers(0, 256, (60, 90, {"RGB": 3, "RGBA": 4, "L": 1}[mode]), np.uint8)
        arr = np.kron(arr[::6, ::6], np.ones((6, 6, 1), np.uint8))[:60, :90]    # blocks, so rotation is visible
        im = Image.fromarray(arr[..., 0] if mode == "L" else arr, mode)
        ex = Image.Exif()
        ex[0x0112] = orientation
        path = os.path.join(self.dir.name, "t." + fmt)
        im.save(path, exif=ex if fmt == "tiff" else ex.tobytes())    # a TIFF keeps it as a tag
        return path

    @staticmethod
    def pixels(body):
        return np.asarray(Image.open(io.BytesIO(body))).astype(int)

    def test_not_shrunk_is_exactly_the_old_picture_right_way_up(self):
        # lossless formats, every orientation, nothing to resample
        for fmt, mode in (("png", "RGBA"), ("png", "RGB"), ("png", "L"), ("tiff", "RGB"), ("webp", "RGBA")):
            for orientation in range(1, 9):
                path = self.save(fmt, mode, orientation)
                got, want = imaging.encode_preview(path, 400), reference_encode_preview(path, 400)
                self.assertEqual(got[1:], want[1:], (fmt, mode, orientation))
                if fmt == "webp" and mode == "RGB":
                    continue
                np.testing.assert_array_equal(self.pixels(got[0]), self.pixels(want[0]), (fmt, mode, orientation))

    def test_shrunk_and_upright_is_exactly_the_old_picture(self):
        for fmt, mode in (("png", "RGB"), ("png", "RGBA"), ("png", "L"), ("jpg", "RGB"), ("tiff", "RGBA")):
            path = self.save(fmt, mode, 1)
            got, want = imaging.encode_preview(path, 40), reference_encode_preview(path, 40)
            self.assertEqual(got[1:], want[1:], (fmt, mode))
            np.testing.assert_array_equal(self.pixels(got[0]), self.pixels(want[0]), (fmt, mode))

    def test_shrunk_and_turned_is_the_same_picture_up_to_resampling_rounding(self):
        # turning before or after the shrink swaps the order of the two
        # resampling passes, which moves 8-bit rounding by a level or two
        for fmt, mode in (("png", "RGB"), ("png", "L"), ("jpg", "RGB"), ("tiff", "RGB"), ("tiff", "L")):
            for orientation in range(2, 9):
                path = self.save(fmt, mode, orientation)
                got, want = imaging.encode_preview(path, 40), reference_encode_preview(path, 40)
                self.assertEqual(got[1:], want[1:], (fmt, mode, orientation))
                a, b = self.pixels(got[0]), self.pixels(want[0])
                self.assertEqual(a.shape, b.shape, (fmt, mode, orientation))
                self.assertLess(np.abs(a - b).mean(), 4, (fmt, mode, orientation))

    def test_tiff_with_an_orientation_tag_is_turned_once(self):
        # Pillow applies a TIFF's orientation itself while loading; turning it
        # again from the tag read before the load would lay it on its side
        path = self.save("tiff", "RGB", 6)
        small = Image.open(io.BytesIO(imaging.encode_preview(path, 40)[0]))
        self.assertEqual(small.size, (27, 40))


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

    def test_reported_size_is_the_size_the_node_cuts_for_every_format_and_orientation(self):
        # The editor draws boxes in the size the preview reports; the node cuts
        # the decoded array. They must agree, whoever applies the orientation
        # (a TIFF is turned by Pillow itself as it loads, the rest by us).
        arr = np.random.default_rng(2).integers(0, 256, (30, 40, 3), np.uint8)    # not square
        for fmt in ("png", "jpg", "tiff", "webp"):
            for orientation in range(1, 9):
                ex = Image.Exif()
                ex[0x0112] = orientation
                path = self.path("o.%s" % fmt)
                Image.fromarray(arr).save(path, exif=ex if fmt == "tiff" else ex.tobytes())
                rgb, _ = imaging.load_arrays(path)
                upright = rgb.shape[1::-1]
                self.assertEqual(upright, (30, 40) if orientation >= 5 else (40, 30), (fmt, orientation))
                self.assertEqual(imaging.read_size(path), upright, (fmt, orientation))
                self.assertEqual(imaging.encode_preview(path, 400)[2:], upright, (fmt, orientation))

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
