import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from geometry import (MAX_SIDE, conform_box, default_box, default_size,  # noqa: E402
                      parse_aspect, parse_crop, resolve_box, snap_size)

# the ratios the node used to offer in a dropdown
COMMON = {name: parse_aspect(name) for name in (
    "1:1", "5:4", "4:3", "3:2", "16:9", "21:9", "4:5", "3:4", "2:3", "9:16", "9:21")}
RATIOS = [None, *COMMON.values()]


class ParseAspect(unittest.TestCase):
    def test_free(self):
        for text in ("free", "FREE", " free ", "", "   ", "none", None):
            self.assertIsNone(parse_aspect(text), repr(text))

    def test_w_to_h(self):
        self.assertEqual(parse_aspect("16:9"), (16, 9))
        self.assertEqual(parse_aspect(" 9 : 16 "), (9, 16))
        self.assertEqual(parse_aspect("4/3"), (4, 3))
        self.assertEqual(parse_aspect("1920x1080"), (16, 9))
        self.assertEqual(parse_aspect("1920X1080"), (16, 9))
        self.assertEqual(parse_aspect("1920\u00d71080"), (16, 9))

    def test_reduced(self):
        self.assertEqual(parse_aspect("32:18"), (16, 9))
        self.assertEqual(parse_aspect("1:1"), (1, 1))
        self.assertEqual(parse_aspect("1000:1000"), (1, 1))

    def test_decimals_are_exact_fractions(self):
        self.assertEqual(parse_aspect("1.5"), (3, 2))
        self.assertEqual(parse_aspect("0.75"), (3, 4))
        self.assertEqual(parse_aspect("1.78"), (89, 50))
        self.assertEqual(parse_aspect("2.39:1"), (239, 100))
        self.assertEqual(parse_aspect("1.5:0.5"), (3, 1))
        self.assertEqual(parse_aspect("2"), (2, 1))

    def test_rejects_garbage_and_nonsense(self):
        for bad in ("abc", "16:", ":9", "16:9:1", "16;9", "-1:1", "1:-1", "1e3", "1.", ".5",
                    "0", "0:1", "1:0", "0.0", "12345:1", "1.12345", "1,5", "16:9 please",
                    "\uff11:1", "1:1\n2", "9999.9999:0.0001"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                parse_aspect(bad)

    def test_error_names_the_input_and_how_to_fix_it(self):
        with self.assertRaisesRegex(ValueError, r"'wide'.*16:9"):
            parse_aspect("wide")


class SnapSize(unittest.TestCase):
    def test_free_rounds_each_side_to_the_multiple(self):
        self.assertEqual(snap_size(1001, 777, None, 64), (1024, 768))
        self.assertEqual(snap_size(1000, 1000, None, 16), (1008, 1008))

    def test_never_below_one_multiple(self):
        self.assertEqual(snap_size(1, 1, None, 64), (64, 64))
        self.assertEqual(snap_size(1, 1, COMMON["16:9"], 64), (128, 64))

    def test_ratio_is_as_close_as_the_multiple_allows(self):
        # 16:9 can't be exact on a 64 grid at this size; 1920x1088 is the nearest
        self.assertEqual(snap_size(1920, 1080, COMMON["16:9"], 64), (1920, 1088))
        self.assertEqual(snap_size(1280, 720, COMMON["16:9"], 16), (1280, 720))

    def test_ratio_is_within_a_pixel_when_multiple_is_one(self):
        for name, ratio in COMMON.items():
            for size in (1000, 1001, 3333, 4032):
                w, h = snap_size(size, size, ratio, 1)
                # the derived side is rounded to the nearest pixel, no further off
                self.assertLessEqual(abs(w * ratio[1] - h * ratio[0]), max(ratio) / 2, (name, size))
        self.assertEqual(snap_size(1920, 1080, COMMON["16:9"], 1), (1920, 1080))

    def test_idempotent(self):
        rng = random.Random(1)
        for _ in range(2000):
            ratio = rng.choice(RATIOS)
            n = rng.choice([1, 2, 8, 14, 16, 32, 64, 128])
            once = snap_size(rng.randint(1, 5000), rng.randint(1, 5000), ratio, n)
            self.assertEqual(snap_size(*once, ratio, n), once)
            self.assertEqual(once[0] % n, 0)
            self.assertEqual(once[1] % n, 0)


class DefaultBox(unittest.TestCase):
    def test_full_image_when_nothing_constrains_it(self):
        self.assertEqual(default_box(4032, 3024, None, 1), (0, 0, 4032, 3024))
        self.assertEqual(default_box(4032, 3024, None, 16), (0, 0, 4032, 3024))
        self.assertEqual(default_box(4032, 3024, COMMON["4:3"], 16), (0, 0, 4032, 3024))

    def test_largest_centered_cut(self):
        self.assertEqual(default_size(4032, 3024, COMMON["16:9"], 1), (4032, 2268))
        self.assertEqual(default_box(1200, 900, COMMON["16:9"], 64), (24, 130, 1152, 640))
        self.assertEqual(default_size(1000, 500, COMMON["1:1"], 32), (480, 480))

    def test_always_fits_the_image_when_it_can(self):
        rng = random.Random(2)
        for _ in range(2000):
            iw, ih = rng.randint(64, 6000), rng.randint(64, 6000)
            ratio = rng.choice(RATIOS)
            n = rng.choice([1, 8, 16, 32, 64])
            w, h = default_size(iw, ih, ratio, n)
            self.assertEqual((w % n, h % n), (0, 0))
            smallest = snap_size(1, 1, ratio, n)    # an image narrower than this must overhang
            if iw >= smallest[0] and ih >= smallest[1]:
                self.assertTrue(w <= iw and h <= ih, (iw, ih, ratio, n, w, h))

    def test_image_smaller_than_one_multiple_overhangs(self):
        self.assertEqual(default_size(100, 100, None, 256), (256, 256))


class Conform(unittest.TestCase):
    def test_keeps_one_pixel_of_the_image_inside(self):
        x, y, w, h = conform_box((-5000, 9000, 100, 100), 1200, 900, None, 1)
        self.assertEqual((x, y), (-99, 899))

    def test_refuses_absurd_sizes(self):
        with self.assertRaises(ValueError):
            conform_box((0, 0, MAX_SIDE + 1, 10), 100, 100, None, 1)
        with self.assertRaises(ValueError):
            conform_box((0, 0, 16000, 16000), 100, 100, None, 1)


class Padding(unittest.TestCase):
    # a 1000x800 image; pads are (left, top, right, bottom)
    def cut(self, box, pads, iw=1000, ih=800):
        return conform_box(box, iw, ih, None, 1, pads)

    def test_default_is_no_limit(self):
        self.assertEqual(self.cut((-300, -200, 500, 400), None), (-300, -200, 500, 400))
        self.assertEqual(self.cut((-300, -200, 500, 400), (16384,) * 4), (-300, -200, 500, 400))

    def test_a_box_inside_the_allowance_is_untouched(self):
        self.assertEqual(self.cut((-50, -20, 500, 400), (50, 20, 50, 20)), (-50, -20, 500, 400))

    def test_over_the_limit_is_shifted_back_never_resized(self):
        self.assertEqual(self.cut((-300, -200, 500, 400), (50, 20, 50, 20)), (-50, -20, 500, 400))
        self.assertEqual(self.cut((800, 700, 500, 400), (50, 20, 100, 30)), (600, 430, 500, 400))

    def test_zero_never_pads_that_side(self):
        self.assertEqual(self.cut((-5, 10, 300, 300), (0, 0, 16384, 16384)), (0, 10, 300, 300))
        # pad on the right still allowed
        self.assertEqual(self.cut((900, 10, 300, 300), (0, 0, 16384, 0)), (900, 10, 300, 300))
        self.assertEqual(self.cut((900, 10, 300, 300), (0, 0, 0, 0)), (700, 10, 300, 300))

    def test_a_box_that_cannot_fit_is_an_error_naming_the_fix(self):
        with self.assertRaisesRegex(ValueError, r"max_pad"):
            self.cut((0, 0, 1200, 300), (50, 0, 100, 0))     # 1200 > 1000 + 50 + 100
        with self.assertRaises(ValueError):
            self.cut((0, 0, 300, 900), (0, 0, 0, 99))         # 900 > 800 + 99
        self.assertEqual(self.cut((0, 0, 1150, 300), (50, 0, 100, 0)), (-50, 0, 1150, 300))

    def test_default_box_smaller_than_one_multiple_needs_padding(self):
        # image 100x100, multiple 256: the implied cut overhangs by 78 px a side
        self.assertEqual(resolve_box("", 100, 100, None, 256, (78, 78, 78, 78)), (-78, -78, 256, 256))
        with self.assertRaises(ValueError):
            resolve_box("", 100, 100, None, 256, (0, 0, 0, 0))

    def test_result_always_within_the_allowance(self):
        rng = random.Random(3)
        for _ in range(2000):
            iw, ih = rng.randint(1, 3000), rng.randint(1, 3000)
            pads = tuple(rng.choice([0, 5, 100, 1000]) for _ in range(4))
            box = (rng.randint(-4000, 4000), rng.randint(-4000, 4000), rng.randint(1, 3000), rng.randint(1, 3000))
            try:
                x, y, w, h = conform_box(box, iw, ih, None, 1, pads)
            except ValueError:
                continue
            self.assertTrue(x >= -pads[0] and y >= -pads[1] and x + w <= iw + pads[2] and y + h <= ih + pads[3],
                            (box, pads, iw, ih))
            self.assertTrue(x < iw and y < ih and x + w > 0 and y + h > 0)    # still touches the image


class ParseCrop(unittest.TestCase):
    def test_empty_means_no_crop(self):
        self.assertIsNone(parse_crop("", 100, 100))
        self.assertIsNone(parse_crop("   ", 100, 100))

    def test_same_size_image_is_used_as_is(self):
        self.assertEqual(parse_crop('{"x":-10,"y":5,"w":50,"h":60,"iw":100,"ih":100}', 100, 100),
                         (-10, 5, 50, 60))

    def test_box_is_scaled_to_a_replaced_image(self):
        self.assertEqual(parse_crop('{"x":10,"y":10,"w":100,"h":100,"iw":1000,"ih":1000}', 2000, 2000),
                         (20, 20, 200, 200))
        self.assertEqual(parse_crop('{"x":-10,"y":0,"w":100,"h":100,"iw":1000,"ih":1000}', 500, 500),
                         (-5, 0, 50, 50))

    def test_garbage_is_an_error_not_a_silent_full_image(self):
        for bad in ("not json", "[]", '{"x":1}', '{"x":0,"y":0,"w":0,"h":5}', '{"x":"a","y":0,"w":1,"h":1}'):
            with self.assertRaises(ValueError, msg=bad):
                parse_crop(bad, 100, 100)

    def test_resolve_snaps_a_hand_edited_box(self):
        self.assertEqual(resolve_box('{"x":10,"y":10,"w":1001,"h":777}', 4000, 3000, None, 64),
                         (10, 10, 1024, 768))


if __name__ == "__main__":
    unittest.main()
