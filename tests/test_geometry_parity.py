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

from geometry import ASPECTS, conform_box, default_box, default_size, snap_size  # noqa: E402

RATIOS = list(ASPECTS.values())
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
