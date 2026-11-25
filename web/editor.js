// On-node crop/pad editor. Plain DOM + canvas, no ComfyUI imports, so it
// behaves the same in the classic canvas and in Nodes 2.0, and can be tested
// on its own.
//
// The box is in SOURCE pixels and may extend past the image: that overhang is
// the padding. The view always shows the image, the box, and a margin around
// both, so there is room to pull the box outward.

import { snapSize, snapWithin, defaultBox, fits, overlapsImage, padsOk, shiftIntoPads, NO_LIMIT } from "./geometry.js";

const HANDLE = 10;       // corner grab radius, CSS px
const MIN_DRAG = 6;      // a drag shorter than this is a click (clears the box)
const SLACK = 10;        // CSS px past an image edge that still counts as "at the edge"
const GUTTER = 0.2;      // margin around image+box, fraction of the longer side
const INFO_H = 18;       // info row under the picture
const SETTLE_FRAMES = 15;   // after the on-screen size last changed, keep looking this long (~250 ms)
const PAD_COLORS = { gray: "#808080", black: "#000000", white: "#ffffff", edge: "#808080" };
const CURSORS = { nw: "nwse-resize", se: "nwse-resize", ne: "nesw-resize", sw: "nesw-resize" };

export class CropEditor {
    constructor(container, onChange) {
        this.onChange = onChange;
        this.img = null;
        this.iw = 0;
        this.ih = 0;
        this.rect = null;          // {x, y, w, h} or null = "whatever the constraints imply"
        this.ratio = null;         // [a, b] or null
        this.n = 1;
        this.fill = "gray";
        this.pads = NO_LIMIT;      // [left, top, right, bottom]: most the box may overhang each edge
        this.message = "no image";
        this.drag = null;
        this.quiet = 0;            // frames since the on-screen size last changed (see nudge)
        this.watching = false;
        this.dead = false;

        this.canvas = document.createElement("canvas");
        Object.assign(this.canvas.style, {
            position: "absolute", inset: "0", width: "100%", height: "100%",
            touchAction: "none", display: "block",
        });
        container.appendChild(this.canvas);
        this.ctx = this.canvas.getContext("2d");

        this.canvas.addEventListener("pointerdown", (e) => this.#down(e));
        this.canvas.addEventListener("pointermove", (e) => this.#move(e));
        this.canvas.addEventListener("pointerup", (e) => this.#up(e));
        this.canvas.addEventListener("pointercancel", (e) => this.#up(e, true));
        this.resizer = new ResizeObserver(() => {
            this.draw();
            this.nudge();
        });
        this.resizer.observe(container);
    }

    destroy() {
        this.dead = true;
        this.resizer.disconnect();
        this.canvas.remove();
    }

    // ---- state in --------------------------------------------------------

    setImage(img, iw, ih) {
        this.img = img;
        this.iw = iw;
        this.ih = ih;
        this.message = "";
        this.draw();
    }

    setMessage(text) {
        this.img = null;
        this.message = text;
        this.draw();
    }

    setRect(rect) {
        this.rect = rect ? { ...rect } : null;
        this.draw();
    }

    setConstraints({ ratio, n, fill, pads }) {
        this.ratio = ratio ?? null;
        this.n = n || 1;
        this.fill = fill || "gray";
        this.pads = pads ?? NO_LIMIT;
        this.draw();
    }

    // Re-shape an existing box to the current ratio/multiple, keeping its
    // center (and, under a ratio, its area). A box that was inside the image
    // stays inside it: padding only ever comes from pulling outward. True if
    // the box changed.
    reshape() {
        if (!this.img || !this.rect) return false;
        const r = this.rect;
        const inside = r.x >= 0 && r.y >= 0 && r.x + r.w <= this.iw && r.y + r.h <= this.ih;
        let w = r.w, h = r.h;
        if (this.ratio) {
            const [a, b] = this.ratio;
            w = Math.sqrt(r.w * r.h * a / b);
            h = w * b / a;
        }
        [w, h] = inside
            ? snapWithin(Math.round(w), Math.round(h), this.ratio, this.n, this.iw, this.ih)
            : snapSize(Math.round(w), Math.round(h), this.ratio, this.n);
        let x = Math.round(r.x + r.w / 2 - w / 2);
        let y = Math.round(r.y + r.h / 2 - h / 2);
        if (inside && w <= this.iw && h <= this.ih) {
            x = Math.max(0, Math.min(this.iw - w, x));
            y = Math.max(0, Math.min(this.ih - h, y));
        }
        const sized = { x, y, w, h };
        if (!fits(sized) || !overlapsImage(sized, this.iw, this.ih)) return false;
        // a box over the padding now allowed is shifted back; one too big for it is left alone
        const next = shiftIntoPads(sized, this.iw, this.ih, this.pads);
        if (!next) return false;
        const changed = next.x !== r.x || next.y !== r.y || next.w !== r.w || next.h !== r.h;
        this.rect = next;
        this.draw();
        return changed;
    }

    // The box that will be cut (drawn dashed when it is only implied).
    shown() {
        return this.rect ?? (this.img ? defaultBox(this.iw, this.ih, this.ratio, this.n) : null);
    }

    // ---- geometry --------------------------------------------------------

    #view() {
        const r = this.shown();
        const x0 = Math.min(0, r.x), y0 = Math.min(0, r.y);
        const x1 = Math.max(this.iw, r.x + r.w), y1 = Math.max(this.ih, r.y + r.h);
        const g = Math.max(x1 - x0, y1 - y0) * GUTTER;
        return { x: x0 - g, y: y0 - g, w: x1 - x0 + 2 * g, h: y1 - y0 + 2 * g };
    }

    // Source px -> canvas CSS px:  screen = origin + src * s
    #layout(view) {
        const W = this.canvas.clientWidth, H = this.canvas.clientHeight;
        const ax = 6, ay = 6, aw = W - 12, ah = H - 12 - INFO_H;
        const s = Math.max(1e-6, Math.min(aw / view.w, ah / view.h));
        return {
            s,
            ox: ax + (aw - view.w * s) / 2 - view.x * s,
            oy: ay + (ah - view.h * s) / 2 - view.y * s,
        };
    }

    #local(e) {
        const b = this.canvas.getBoundingClientRect();
        const zoom = b.width / this.canvas.clientWidth || 1;   // canvas zoom / CSS transform
        return [(e.clientX - b.left) / zoom, (e.clientY - b.top) / zoom];
    }

    #hit(px, py, L, r) {
        const sx = L.ox + r.x * L.s, sy = L.oy + r.y * L.s;
        const sw = r.w * L.s, sh = r.h * L.s;
        const corners = { nw: [sx, sy], ne: [sx + sw, sy], sw: [sx, sy + sh], se: [sx + sw, sy + sh] };
        for (const [corner, [cx, cy]] of Object.entries(corners)) {
            if (Math.abs(px - cx) <= HANDLE && Math.abs(py - cy) <= HANDLE) return { mode: "resize", corner };
        }
        if (px >= sx && px <= sx + sw && py >= sy && py <= sy + sh) return { mode: "move" };
        return { mode: "new" };
    }

    // ---- pointer ---------------------------------------------------------

    #down(e) {
        if (!this.img || e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        this.canvas.setPointerCapture(e.pointerId);
        const view = this.#view();           // frozen for the whole drag
        const L = this.#layout(view);
        const [px, py] = this.#local(e);
        const start = this.shown();
        const hit = this.#hit(px, py, L, start);
        // Nothing drawn and the implied box is just the whole image: there is
        // nothing to move, so pressing inside it starts a new box instead.
        if (!this.rect && hit.mode === "move" && start.x === 0 && start.y === 0
            && start.w === this.iw && start.h === this.ih) hit.mode = "new";
        this.drag = {
            ...hit, view, L, px, py, start: { ...start }, hadRect: !!this.rect, moved: false,
            src: [(px - L.ox) / L.s, (py - L.oy) / L.s],
        };
        if (hit.mode !== "new") this.rect = { ...start };   // grabbing the implied box makes it real
        this.draw();
    }

    #move(e) {
        const d = this.drag;
        if (!d) {
            if (!this.img) return;
            const L = this.#layout(this.#view());
            const [px, py] = this.#local(e);
            const hit = this.#hit(px, py, L, this.shown());
            this.canvas.style.cursor = hit.mode === "resize" ? CURSORS[hit.corner]
                : hit.mode === "move" ? "grab" : this.rect ? "not-allowed" : "crosshair";
            return;
        }
        const [px, py] = this.#local(e);
        if (Math.abs(px - d.px) + Math.abs(py - d.py) > MIN_DRAG) d.moved = true;
        if (!d.moved) return;

        const { view, L } = d;
        const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
        const sx = clamp((px - L.ox) / L.s, view.x, view.x + view.w);
        const sy = clamp((py - L.oy) / L.s, view.y, view.y + view.h);
        let next = null;

        if (d.mode === "move") {
            // Moving never creates padding on an axis where the box fits in
            // the image; padding comes from pulling a corner outward.
            const s = d.start;
            // ...and never past the padding allowed (before/after: that axis's limits).
            const axis = (pos, size, imgSize, v0, vSize, delta, before, after) => {
                const free = size > imgSize || pos < 0 || pos + size > imgSize;
                if (!free) return Math.round(clamp(pos + delta, 0, imgSize - size));
                const lo = Math.max(v0, -before), hi = Math.min(v0 + vSize - size, imgSize + after - size);
                return Math.round(lo > hi ? pos : clamp(pos + delta, lo, hi));
            };
            const [pl, pt, pr, pb] = this.pads;
            next = {
                ...s,
                x: axis(s.x, s.w, this.iw, view.x, view.w, sx - d.src[0], pl, pr),
                y: axis(s.y, s.h, this.ih, view.y, view.h, sy - d.src[1], pt, pb),
            };
        } else if (d.mode === "resize") {
            const s = d.start;
            const west = d.corner.includes("w"), north = d.corner.includes("n");
            const anchor = { x: west ? s.x + s.w : s.x, y: north ? s.y + s.h : s.y };
            // keep the grab offset so the corner does not jump to the pointer
            const grab = { x: d.src[0] - (west ? s.x : s.x + s.w), y: d.src[1] - (north ? s.y : s.y + s.h) };
            next = this.#boxFrom(anchor, { x: sx - grab.x, y: sy - grab.y }, SLACK / L.s);
        } else {
            next = this.#boxFrom({ x: Math.round(d.src[0]), y: Math.round(d.src[1]) }, { x: sx, y: sy }, SLACK / L.s);
        }
        if (next && fits(next) && overlapsImage(next, this.iw, this.ih)
            && padsOk(next, this.iw, this.ih, this.pads)) this.rect = next;
        this.draw();
    }

    #up(e, cancelled = false) {
        const d = this.drag;
        if (!d) return;
        this.drag = null;
        if (this.canvas.hasPointerCapture(e.pointerId)) this.canvas.releasePointerCapture(e.pointerId);
        if (!d.moved || cancelled) {
            // a plain click clears the box; a click on the box leaves it as it was
            this.rect = d.mode === "new" ? null : (d.hadRect ? d.start : null);
        }
        this.draw();
        const before = d.hadRect ? d.start : null;
        const after = this.rect;
        const same = (!before && !after) || (before && after &&
            before.x === after.x && before.y === after.y && before.w === after.w && before.h === after.h);
        if (!same) this.onChange(after ? { ...after } : null);
    }

    // Box with one corner at `anchor`, the other dragged to `p`, snapped to the
    // ratio and multiple. Under a ratio the box encloses the dragged area.
    // While both corners are inside the image the box is kept inside it too,
    // so snapping can't round up into a sliver of padding; once the pointer
    // crosses the image edge the box is free to pad. "Inside" includes a small
    // dead zone (`slack`, source px) past the edge: aiming at an edge and
    // overshooting by a few pixels must not flip the drag into padding. Padding
    // stops where the allowed padding does.
    #boxFrom(anchor, p, slack = 0) {
        const dx = p.x - anchor.x, dy = p.y - anchor.y;
        let w = Math.abs(dx), h = Math.abs(dy);
        if (this.ratio) {
            const [a, b] = this.ratio;
            w = Math.max(w, h * a / b);
            h = w * b / a;
        }
        const inImage = (q) => q.x >= -slack && q.x <= this.iw + slack
            && q.y >= -slack && q.y <= this.ih + slack;
        // how far the box may grow from the anchor: to the image edge while
        // inside it, otherwise to the edge of the padding allowed
        const [pl, pt, pr, pb] = this.pads;
        const [x0, y0, x1, y1] = inImage(anchor) && inImage(p)
            ? [0, 0, this.iw, this.ih]
            : [-pl, -pt, this.iw + pr, this.ih + pb];
        [w, h] = snapWithin(Math.round(w), Math.round(h), this.ratio, this.n,
            dx < 0 ? anchor.x - x0 : x1 - anchor.x, dy < 0 ? anchor.y - y0 : y1 - anchor.y);
        return { x: dx < 0 ? anchor.x - w : anchor.x, y: dy < 0 ? anchor.y - h : anchor.y, w, h };
    }

    // ---- drawing ---------------------------------------------------------

    // Backing-store size for what is on screen right now: the graph zooms this
    // element with a CSS transform, so measure it instead of assuming 1:1.
    #backing() {
        const c = this.canvas;
        const W = c.clientWidth, H = c.clientHeight;
        if (!W || !H) return null;
        const zoom = c.getBoundingClientRect().width / W || 1;
        const k = Math.min(3, Math.max(1, (window.devicePixelRatio || 1) * zoom));
        return [W, H, Math.round(W * k), Math.round(H * k)];
    }

    // The graph's zoom is a CSS transform, which a ResizeObserver never hears
    // about, and the host applies it after its own draw hooks run. So the
    // owner calls this whenever the graph redraws; it looks again on the next
    // frames, redrawing if the backing size no longer fits, until the size has
    // held still for a moment. Costs nothing while idle.
    nudge() {
        this.quiet = 0;
        if (this.watching || this.dead) return;
        this.watching = true;
        const look = () => requestAnimationFrame(() => {
            if (this.dead) return;
            const b = this.#backing();
            const stale = b && (b[2] !== this.canvas.width || b[3] !== this.canvas.height);
            if (stale) this.draw();
            this.quiet = stale ? 0 : this.quiet + 1;
            if (this.quiet < SETTLE_FRAMES) look();
            else this.watching = false;
        });
        look();
    }

    draw() {
        const c = this.canvas, ctx = this.ctx;
        const b = this.#backing();
        if (!b) return;
        const [W, H, bw, bh] = b;
        if (c.width !== bw || c.height !== bh) { c.width = bw; c.height = bh; }
        ctx.setTransform(bw / W, 0, 0, bh / H, 0, 0);
        ctx.clearRect(0, 0, W, H);
        ctx.textBaseline = "alphabetic";

        if (!this.img) {
            ctx.fillStyle = "rgba(0,0,0,0.25)";
            ctx.fillRect(6, 6, W - 12, H - 12);
            ctx.fillStyle = "#999";
            ctx.font = "12px sans-serif";
            ctx.textAlign = "center";
            ctx.fillText(this.message || "no image", W / 2, H / 2);
            return;
        }

        const r = this.shown();
        const view = this.drag ? this.drag.view : this.#view();
        const L = this.#layout(view);
        const X = (v) => L.ox + v * L.s, Y = (v) => L.oy + v * L.s;
        const [vx, vy, vw, vh] = [X(view.x), Y(view.y), view.w * L.s, view.h * L.s];
        const [rx, ry, rw, rh] = [X(r.x), Y(r.y), r.w * L.s, r.h * L.s];
        const [ix, iy, iw, ih] = [X(0), Y(0), this.iw * L.s, this.ih * L.s];

        ctx.fillStyle = "rgba(0,0,0,0.25)";
        ctx.fillRect(vx, vy, vw, vh);

        // padding: the fill colour, hatched so it can't be mistaken for image
        ctx.save();
        ctx.beginPath();
        ctx.rect(rx, ry, rw, rh);
        ctx.clip();
        ctx.fillStyle = PAD_COLORS[this.fill] || PAD_COLORS.gray;
        ctx.fillRect(rx, ry, rw, rh);
        ctx.strokeStyle = this.fill === "white" ? "rgba(0,0,0,0.25)" : "rgba(255,255,255,0.3)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        for (let t = -rh; t < rw; t += 9) {
            ctx.moveTo(rx + t, ry + rh);
            ctx.lineTo(rx + t + rh, ry);
        }
        ctx.stroke();
        ctx.restore();

        ctx.drawImage(this.img, ix, iy, iw, ih);

        // dim everything outside the box
        ctx.beginPath();
        ctx.rect(vx, vy, vw, vh);
        ctx.rect(rx, ry, rw, rh);
        ctx.fillStyle = "rgba(0,0,0,0.55)";
        ctx.fill("evenodd");

        ctx.strokeStyle = "rgba(255,255,255,0.35)";
        ctx.lineWidth = 1;
        ctx.strokeRect(ix + 0.5, iy + 0.5, iw - 1, ih - 1);

        ctx.strokeStyle = "#4af";
        if (!this.rect) ctx.setLineDash([4, 3]);
        ctx.strokeRect(rx, ry, rw, rh);
        ctx.setLineDash([]);
        ctx.fillStyle = "#4af";
        for (const [hx, hy] of [[rx, ry], [rx + rw, ry], [rx, ry + rh], [rx + rw, ry + rh]]) {
            ctx.fillRect(hx - 3, hy - 3, 6, 6);
        }

        // output size, above the box (inside it if there is no room above)
        ctx.font = "11px sans-serif";
        ctx.textAlign = "left";
        const label = `${r.w} × ${r.h}`;
        const tw = ctx.measureText(label).width + 8;
        const lx = Math.max(vx, Math.min(rx + (rw - tw) / 2, vx + vw - tw));
        const ly = ry - 3 - 14 >= vy ? ry - 17 : ry + 3;
        ctx.fillStyle = "rgba(0,0,0,0.65)";
        ctx.fillRect(lx, ly, tw, 14);
        ctx.fillStyle = "#fff";
        ctx.fillText(label, lx + 4, ly + 11);

        // info row: source size, and how much padding the box adds
        const pad = [Math.max(0, -r.x), Math.max(0, -r.y),
                     Math.max(0, r.x + r.w - this.iw), Math.max(0, r.y + r.h - this.ih)];
        let info = `Image ${this.iw} × ${this.ih}`;
        if (pad.some((v) => v > 0)) info += `   Pad L${pad[0]} T${pad[1]} R${pad[2]} B${pad[3]}`;
        ctx.textAlign = "center";
        ctx.fillStyle = "#aaa";
        ctx.fillText(info, W / 2, H - 6);
    }
}
