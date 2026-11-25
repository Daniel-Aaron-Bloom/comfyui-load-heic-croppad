"""web/geometry.js must agree with geometry.py to the pixel: the editor shows
the box the node will cut. Skipped when node is not installed."""

import json
import os
import random
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))

from geometry import (conform_box, default_box, default_size, parse_aspect,  # noqa: E402
                      snap_size)

RATIOS = [None] + [parse_aspect(t) for t in (
    "1:1", "5:4", "4:3", "3:2", "16:9", "21:9", "4:5", "3:4", "2:3", "9:16", "9:21",
    "2.39", "1.91:1", "0.8", "1234:57", "7:1000")]
MULTIPLES = [1, 2, 8, 14, 16, 32, 64, 128, 256, 512]


def _js(cases):
    done = subprocess.run(["node", os.path.join(HERE, "geometry_parity.mjs")],
                          input=json.dumps(cases), capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class Parity(unittest.TestCase):
    def test_python_and_js_agree(self):
        rng = random.Random(7)
        cases, expected = [], []
        for _ in range(4000):
            ratio, n = rng.choice(RATIOS), rng.choice(MULTIPLES)
            r = list(ratio) if ratio else None
            w, h = rng.randint(1, 9000), rng.randint(1, 9000)
            iw, ih = rng.randint(1, 9000), rng.randint(1, 9000)
            box = (rng.randint(-5000, 9000), rng.randint(-5000, 9000), w, h)
            cases += [
                {"fn": "snap", "w": w, "h": h, "ratio": r, "n": n},
                {"fn": "default", "iw": iw, "ih": ih, "ratio": r, "n": n},
                {"fn": "box", "iw": iw, "ih": ih, "ratio": r, "n": n},
            ]
            expected += [
                list(snap_size(w, h, ratio, n)),
                list(default_size(iw, ih, ratio, n)),
                list(default_box(iw, ih, ratio, n)),
            ]
            try:    # the node refuses a box over its size limit; JS only clamps
                want = list(conform_box(box, iw, ih, ratio, n))
            except ValueError:
                continue
            cases.append({"fn": "conform", "box": dict(zip("xywh", box)), "iw": iw, "ih": ih, "ratio": r, "n": n})
            expected.append(want)
        got = _js(cases)
        for case, want, have in zip(cases, expected, got):
            self.assertEqual(have, want, case)

    def test_aspect_parsing_agrees(self):
        texts = ["free", "FREE", " free ", "", "  ", "none", "16:9", " 9 : 16 ", "4/3", "1920x1080",
                 "1920X1080", "1920\u00d71080", "32:18", "1.5", "0.75", "1.78", "2.39:1", "1.5:0.5",
                 "2", "abc", "16:", ":9", "16:9:1", "-1:1", "1e3", "1.", ".5", "0", "0:1", "1:0",
                 "12345:1", "1.12345", "1,5", "\uff11:1", "1:1\n2", "9999.9999:0.0001",
                 "9999:0.0001", "0.0001:9999", "1234:57", "7:1000", "9999.9999", "0.0001"]
        rng = random.Random(9)
        for _ in range(500):
            texts.append("%d%s:%d%s" % (rng.randint(0, 99999), rng.choice(["", ".25", ".5", ".0001"]),
                                        rng.randint(0, 99999), rng.choice(["", ".75", ".333"])))
        want = []
        for t in texts:
            try:
                r = parse_aspect(t)
                want.append(list(r) if r else None)
            except ValueError:
                want.append({"error": True})
        got = _js([{"fn": "aspect", "text": t} for t in texts])
        for t, w, g in zip(texts, want, got):
            self.assertEqual(g, w, t)

    def test_bounded_editor_snap_is_always_a_legal_box(self):
        # What the editor writes while dragging inside the image must be a
        # fixed point of the node's own snapping, or the node would re-cut it.
        rng = random.Random(8)
        cases = []
        for _ in range(4000):
            ratio, n = rng.choice(RATIOS), rng.choice(MULTIPLES)
            cases.append({"fn": "within", "w": rng.randint(1, 6000), "h": rng.randint(1, 6000),
                          "ratio": list(ratio) if ratio else None, "n": n,
                          "maxW": rng.randint(1, 6000), "maxH": rng.randint(1, 6000)})
        for case, (w, h) in zip(cases, _js(cases)):
            ratio = tuple(case["ratio"]) if case["ratio"] else None
            self.assertEqual(snap_size(w, h, ratio, case["n"]), (w, h), case)
            smallest = snap_size(1, 1, ratio, case["n"])
            if smallest[0] <= case["maxW"] and smallest[1] <= case["maxH"]:   # room for at least one
                self.assertTrue(w <= case["maxW"] and h <= case["maxH"], case)


if __name__ == "__main__":
    unittest.main()
