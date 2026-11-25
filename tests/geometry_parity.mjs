// Reads JSON cases on stdin, evaluates web/geometry.js on them, writes JSON to
// stdout. Driven by test_geometry_parity.py. The module is loaded through a
// data: URL so no package.json "type": "module" is needed next to it.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(here, "..", "web", "geometry.js"), "utf8");
const g = await import("data:text/javascript;base64," + Buffer.from(src).toString("base64"));

const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const out = cases.map((c) => {
    switch (c.fn) {
        case "snap": return g.snapSize(c.w, c.h, c.ratio, c.n);
        case "default": return g.defaultSize(c.iw, c.ih, c.ratio, c.n);
        case "box": { const b = g.defaultBox(c.iw, c.ih, c.ratio, c.n); return [b.x, b.y, b.w, b.h]; }
        case "conform": {
            const b = g.conformBox(c.box, c.iw, c.ih, c.ratio, c.n, c.pads ?? undefined);
            return [b.x, b.y, b.w, b.h];
        }
        // the editor's own check must accept whatever the node would cut
        case "conform_ok": {
            const b = g.conformBox(c.box, c.iw, c.ih, c.ratio, c.n, c.pads);
            return g.padsOk(b, c.iw, c.ih, c.pads);
        }
        case "aspect":
            try { return g.parseAspect(c.text); } catch { return { error: true }; }
        case "within": return g.snapWithin(c.w, c.h, c.ratio, c.n, c.maxW, c.maxH);
    }
});
process.stdout.write(JSON.stringify(out));
