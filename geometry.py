"""Crop/pad box geometry.

Pure integer math with no ComfyUI, torch or numpy dependency. web/geometry.js
is a line-for-line mirror of this file: the editor shows the same box the node
will cut, so every rounding rule here is integer-only (no float ratios, no
banker's rounding) and tests/test_geometry_parity.py checks the two agree.

A box is (x, y, w, h) in source-image pixels. It may extend past the image on
any side: whatever lies outside the image is padding.
"""

import json
import math
import re

MAX_TERM = 1_000_000    # largest numerator or denominator once reduced

# "W:H", "W/H", "WxH" or a bare width/height number; each number has at most
# 4 digits before and 4 after the point, so the exact-integer arithmetic below
# stays inside what JS doubles hold exactly.
_NUM = r"([0-9]{1,4})(?:\.([0-9]{1,4}))?"
_ASPECT = re.compile(_NUM + r"(?:[ ]*[:/x\u00d7][ ]*" + _NUM + ")?")
ASPECT_HELP = 'use W:H such as 16:9 or 2.39:1, a width/height number such as 1.5, or "free"'


def parse_aspect(text):
    """Aspect-ratio text -> (a, b) for "a:b" (width:height), or None for free.

    Decimals become exact fractions (1.78 -> 89:50), never floats. Raises
    ValueError for anything else, including a ratio that is zero or absurd.
    """
    s = (text or "").strip().lower()
    if s in ("", "free", "none"):
        return None
    m = _ASPECT.fullmatch(s)
    if not m:
        raise ValueError("Invalid aspect ratio %r: %s" % (text, ASPECT_HELP))

    def frac(whole, part):          # "1.25" -> (125, 100)
        part = part or ""
        return int(whole + part), 10 ** len(part)

    an, ad = frac(m[1], m[2])
    bn, bd = frac(m[3], m[4]) if m[3] else (1, 1)
    a, b = an * bd, ad * bn         # (an/ad) / (bn/bd)
    if a == 0 or b == 0:
        raise ValueError("Invalid aspect ratio %r: it cannot be zero" % text)
    g = math.gcd(a, b)
    a, b = a // g, b // g
    if a > MAX_TERM or b > MAX_TERM:
        raise ValueError("Invalid aspect ratio %r: too extreme or too precise" % text)
    return a, b


MAX_SIDE = 16384
MAX_PIXELS = 128 * 1024 * 1024


def _rnd(a, b):
    """a / b rounded half up (b > 0). Floor division, so negative a is right too."""
    return (2 * a + b) // (2 * b)


def snap_size(w, h, ratio, n):
    """Nearest valid (w, h) to the wanted size: multiples of n, and the aspect
    ratio when one is set.

    With a ratio the SHORTER side is rounded to a multiple of n and the longer
    one is derived from it. The rounding error of the derived side is then
    spread over the larger number, which keeps the ratio as close as the
    multiple allows. (16:9 at a multiple of 64 cannot be exact at every size;
    1920x1088 is as near as it gets there.)
    """
    if ratio is None:
        return max(1, _rnd(w, n)) * n, max(1, _rnd(h, n)) * n
    a, b = ratio
    if a >= b:
        q = max(1, _rnd(h, n))
        p = max(1, _rnd(q * a, b))
    else:
        p = max(1, _rnd(w, n))
        q = max(1, _rnd(p * b, a))
    return p * n, q * n


def default_size(iw, ih, ratio, n):
    """The largest valid size that fits inside the image.

    This is what an empty crop means: the full image where it already
    satisfies the constraints, otherwise the biggest cut that does. If the
    image is smaller than one multiple the result is one multiple, which
    overhangs the image (and is padded).
    """
    if ratio is None:
        return max(n, iw // n * n), max(n, ih // n * n)
    a, b = ratio
    if a >= b:
        # q (multiples of n on the short side) cannot exceed what fits in
        # either dimension; walk down from there until the long side fits.
        q = max(1, min(ih // n, iw * b // (a * n) + 1))
        while q > 1 and _rnd(q * a, b) * n > iw:
            q -= 1
        return max(1, _rnd(q * a, b)) * n, q * n
    p = max(1, min(iw // n, ih * a // (b * n) + 1))
    while p > 1 and _rnd(p * b, a) * n > ih:
        p -= 1
    return p * n, max(1, _rnd(p * b, a)) * n


def default_box(iw, ih, ratio, n):
    w, h = default_size(iw, ih, ratio, n)
    return (iw - w) // 2, (ih - h) // 2, w, h


def conform_box(box, iw, ih, ratio, n):
    """Make an arbitrary box legal. Never raises for a merely odd box; only
    refuses one that would allocate an absurd amount of memory."""
    x, y, w, h = box
    w, h = snap_size(w, h, ratio, n)
    if w > MAX_SIDE or h > MAX_SIDE or w * h > MAX_PIXELS:
        raise ValueError(
            "Crop/pad area %dx%d is too large (limit %d px a side, %d MP)."
            % (w, h, MAX_SIDE, MAX_PIXELS >> 20))
    # keep at least one pixel of the image inside the box
    x = max(-(w - 1), min(iw - 1, x))
    y = max(-(h - 1), min(ih - 1, y))
    return x, y, w, h


def parse_crop(text, iw, ih):
    """Stored crop JSON -> box in this image's pixels, or None if empty.

    The JSON is {"x","y","w","h","iw","ih"} in source pixels, where iw/ih are
    the image size the box was drawn on. If the file was since swapped for one
    of another size the box is scaled to the new one rather than misplaced.
    """
    if not text or not text.strip():
        return None
    if len(text) > 4096:
        raise ValueError("Crop data is too long")
    try:
        d = json.loads(text)
        x, y, w, h = (int(d[k]) for k in ("x", "y", "w", "h"))
        siw = int(d.get("iw", iw))
        sih = int(d.get("ih", ih))
        if w < 1 or h < 1 or siw < 1 or sih < 1:
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ValueError("Crop data is not valid; clear the crop on the node.") from None
    if (siw, sih) != (iw, ih):
        x = _rnd(x * iw, siw)
        y = _rnd(y * ih, sih)
        w = max(1, _rnd(w * iw, siw))
        h = max(1, _rnd(h * ih, sih))
    return x, y, w, h


def resolve_box(text, iw, ih, ratio, n):
    """The box the node will actually cut for these widget values."""
    box = parse_crop(text, iw, ih)
    if box is None:
        box = default_box(iw, ih, ratio, n)
    return conform_box(box, iw, ih, ratio, n)
