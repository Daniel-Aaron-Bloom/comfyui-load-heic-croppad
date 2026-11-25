// Mirror of geometry.py. Integer math only, so the editor shows exactly the
// box the node will cut. tests/test_geometry_parity.py checks the two agree;
// change them together.

const MAX_TERM = 1000000;   // largest numerator or denominator once reduced

// "W:H", "W/H", "WxH" or a bare width/height number; see parse_aspect.
const NUM = "([0-9]{1,4})(?:\\.([0-9]{1,4}))?";
const ASPECT = new RegExp("^" + NUM + "(?:[ ]*[:/x\u00d7][ ]*" + NUM + ")?$");

const gcd = (a, b) => (b ? gcd(b, a % b) : a);

// Aspect-ratio text -> [a, b] (width:height), or null for free. Throws on
// anything the node would refuse. Decimals become exact fractions, no floats.
export function parseAspect(text) {
    const s = String(text ?? "").trim().toLowerCase();
    if (s === "" || s === "free" || s === "none") return null;
    const m = ASPECT.exec(s);
    if (!m) throw new Error(`Invalid aspect ratio "${text}"`);
    const frac = (whole, part = "") => [Number(whole + part), 10 ** part.length];
    const [an, ad] = frac(m[1], m[2]);
    const [bn, bd] = m[3] ? frac(m[3], m[4]) : [1, 1];
    let a = an * bd, b = ad * bn;
    if (a === 0 || b === 0) throw new Error(`Invalid aspect ratio "${text}"`);
    const g = gcd(a, b);
    a /= g;
    b /= g;
    if (a > MAX_TERM || b > MAX_TERM) throw new Error(`Invalid aspect ratio "${text}"`);
    return [a, b];
}

export const MAX_SIDE = 16384;
export const MAX_PIXELS = 128 * 1024 * 1024;

const div = (a, b) => Math.floor(a / b);
// a / b rounded half up (b > 0); floor division keeps negative a right.
const rnd = (a, b) => div(2 * a + b, 2 * b);

export function snapSize(w, h, ratio, n) {
    if (!ratio) {
        return [Math.max(1, rnd(w, n)) * n, Math.max(1, rnd(h, n)) * n];
    }
    const [a, b] = ratio;
    let p, q;
    if (a >= b) {
        q = Math.max(1, rnd(h, n));
        p = Math.max(1, rnd(q * a, b));
    } else {
        p = Math.max(1, rnd(w, n));
        q = Math.max(1, rnd(p * b, a));
    }
    return [p * n, q * n];
}

// Editor-only (not mirrored in Python): like snapSize, but never larger than
// maxW x maxH unless not even one multiple fits. Used while a box is being
// shaped inside the image, so rounding up never creates a sliver of padding.
export function snapWithin(w, h, ratio, n, maxW, maxH) {
    if (!ratio) {
        const [sw, sh] = snapSize(w, h, null, n);
        return [Math.min(sw, Math.max(n, div(maxW, n) * n)), Math.min(sh, Math.max(n, div(maxH, n) * n))];
    }
    const [a, b] = ratio;
    if (a >= b) {
        for (let q = Math.max(1, rnd(h, n)); q > 1; q--) {
            const p = Math.max(1, rnd(q * a, b));
            if (p * n <= maxW && q * n <= maxH) return [p * n, q * n];
        }
        return [Math.max(1, rnd(a, b)) * n, n];
    }
    for (let p = Math.max(1, rnd(w, n)); p > 1; p--) {
        const q = Math.max(1, rnd(p * b, a));
        if (p * n <= maxW && q * n <= maxH) return [p * n, q * n];
    }
    return [n, Math.max(1, rnd(b, a)) * n];
}

export function defaultSize(iw, ih, ratio, n) {
    if (!ratio) {
        return [Math.max(n, div(iw, n) * n), Math.max(n, div(ih, n) * n)];
    }
    const [a, b] = ratio;
    if (a >= b) {
        let q = Math.max(1, Math.min(div(ih, n), div(iw * b, a * n) + 1));
        while (q > 1 && rnd(q * a, b) * n > iw) q -= 1;
        return [Math.max(1, rnd(q * a, b)) * n, q * n];
    }
    let p = Math.max(1, Math.min(div(iw, n), div(ih * a, b * n) + 1));
    while (p > 1 && rnd(p * b, a) * n > ih) p -= 1;
    return [p * n, Math.max(1, rnd(p * b, a)) * n];
}

export function defaultBox(iw, ih, ratio, n) {
    const [w, h] = defaultSize(iw, ih, ratio, n);
    return { x: div(iw - w, 2), y: div(ih - h, 2), w, h };
}

// Make a box legal the way the node will. Unlike the node it does not throw
// for an oversized box; use `fits` to refuse one while dragging.
export function conformBox(box, iw, ih, ratio, n) {
    const [w, h] = snapSize(box.w, box.h, ratio, n);
    return {
        x: Math.max(-(w - 1), Math.min(iw - 1, box.x)),
        y: Math.max(-(h - 1), Math.min(ih - 1, box.y)),
        w, h,
    };
}

export function fits(box) {
    return box.w <= MAX_SIDE && box.h <= MAX_SIDE && box.w * box.h <= MAX_PIXELS;
}

export function overlapsImage(box, iw, ih) {
    return box.x < iw && box.x + box.w > 0 && box.y < ih && box.y + box.h > 0;
}
