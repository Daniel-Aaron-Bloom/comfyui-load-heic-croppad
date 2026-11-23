import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { CropEditor } from "./editor.js";
import { ASPECTS } from "./geometry.js";

const NODE = "LoadImageHEICCropPad";
const MIN_EDITOR_HEIGHT = 220;

function setup(node) {
    const widget = (name) => node.widgets.find((w) => w.name === name);
    const image = widget("image");
    const crop = widget("crop");
    const aspect = widget("aspect_ratio");
    const multiple = widget("multiple_of");
    const fill = widget("pad_fill");

    // `crop` is the editor's storage, not a control. Hide it, and drop its
    // input socket (litegraph addresses links by slot index, so re-point them).
    crop.hidden = true;
    crop.options = { ...crop.options, hidden: true };
    const slot = (node.inputs ?? []).findIndex((s) => s.widget?.name === "crop" || s.name === "crop");
    if (slot >= 0 && node.inputs[slot].link == null) {
        node.removeInput(slot);
        const links = node.graph?.links;
        (node.inputs ?? []).forEach((s, i) => {
            if (!links || s.link == null) return;
            const link = typeof links.get === "function" ? links.get(s.link) : links[s.link];
            if (link) link.target_slot = i;
        });
    }

    // ---- editor ----------------------------------------------------------

    const container = document.createElement("div");
    Object.assign(container.style, {
        position: "relative", width: "100%", height: "100%",
        minHeight: `${MIN_EDITOR_HEIGHT}px`, overflow: "hidden",
    });
    const editor = new CropEditor(container, (rect) => writeCrop(rect));
    const editorWidget = node.addDOMWidget("crop_editor", "croppad_editor", container, {
        serialize: false,
        hideOnZoom: false,
        getMinHeight: () => MIN_EDITOR_HEIGHT,
    });
    editorWidget.serialize = false;   // the box lives in `crop`, not here

    let size = null;   // true pixel size of the loaded image {w, h}

    function readCrop() {
        try {
            const d = crop.value ? JSON.parse(crop.value) : null;
            if (!d || !(d.w > 0) || !(d.h > 0)) return null;
            // A box drawn on another-sized image: scale it, as the node does.
            const sx = size && d.iw ? size.w / d.iw : 1;
            const sy = size && d.ih ? size.h / d.ih : 1;
            return { x: Math.round(d.x * sx), y: Math.round(d.y * sy),
                     w: Math.max(1, Math.round(d.w * sx)), h: Math.max(1, Math.round(d.h * sy)) };
        } catch {
            return null;
        }
    }

    function writeCrop(rect) {
        crop.value = rect && size
            ? JSON.stringify({ x: rect.x, y: rect.y, w: rect.w, h: rect.h, iw: size.w, ih: size.h })
            : "";
        node.graph?.change?.();
        node.setDirtyCanvas(true, true);
    }

    function applyConstraints() {
        editor.setConstraints({
            ratio: ASPECTS[aspect.value] ?? null,
            n: Number(multiple.value) || 1,
            fill: fill.value,
        });
    }

    // ---- preview ---------------------------------------------------------

    let loadId = 0;
    async function loadPreview() {
        const id = ++loadId;
        const name = image.value;
        if (!name) return editor.setMessage("no image");
        editor.setMessage("loading…");
        try {
            const res = await api.fetchApi(`/loadheic_croppad/preview?image=${encodeURIComponent(name)}`);
            if (!res.ok) throw new Error((await res.text()) || res.statusText);
            const w = Number(res.headers.get("X-Image-Width"));
            const h = Number(res.headers.get("X-Image-Height"));
            const bitmap = await createImageBitmap(await res.blob());
            if (id !== loadId) return;               // a newer pick superseded this one
            size = { w, h };
            applyConstraints();
            editor.setRect(readCrop());
            editor.setImage(bitmap, w, h);
        } catch (err) {
            if (id !== loadId) return;
            size = null;
            editor.setMessage(String(err?.message ?? err).slice(0, 120));
        }
    }

    // ---- widget hooks ----------------------------------------------------

    const wrap = (w, fn) => {
        const prev = w.callback;
        w.callback = function () {
            const r = prev?.apply(this, arguments);
            fn();
            return r;
        };
    };

    wrap(image, () => {
        writeCrop(null);
        editor.setRect(null);
        loadPreview();
    });
    const reshape = () => {
        applyConstraints();
        if (editor.reshape()) writeCrop(editor.rect);
    };
    wrap(aspect, reshape);
    wrap(multiple, reshape);
    wrap(fill, applyConstraints);

    // Loading a workflow assigns widget values directly, without callbacks.
    const prevConfigure = node.onConfigure;
    node.onConfigure = function () {
        const r = prevConfigure?.apply(this, arguments);
        loadPreview();
        return r;
    };
    const prevRemoved = node.onRemoved;
    node.onRemoved = function () {
        editor.destroy();
        return prevRemoved?.apply(this, arguments);
    };

    // ---- upload (button, and drops on the node) --------------------------

    async function upload(file) {
        const body = new FormData();
        body.append("image", file, file.name);
        const res = await api.fetchApi("/upload/image", { method: "POST", body });
        if (!res.ok) throw new Error(`Upload failed (${res.status})`);
        const { name, subfolder } = await res.json();
        const value = subfolder ? `${subfolder}/${name}` : name;
        const values = image.options.values;
        if (Array.isArray(values) && !values.includes(value)) values.push(value);
        image.value = value;
        image.callback?.(value);
    }

    const toast = (severity, detail) =>
        app.extensionManager?.toast?.add({ severity, summary: "Load Image (HEIC)", detail, life: 5000 });
    const uploadSafely = (file) => upload(file).catch((err) => toast("error", String(err?.message ?? err)));

    const picker = document.createElement("input");
    picker.type = "file";
    picker.accept = "image/*,.heic,.heif";
    picker.onchange = () => {
        const file = picker.files?.[0];
        picker.value = "";
        if (file) uploadSafely(file);
    };
    const button = node.addWidget("button", "upload image", null, () => picker.click());
    button.serialize = false;
    button.options.serialize = false;   // graphToPrompt reads this one
    // sit right under the image selector
    node.widgets.splice(node.widgets.indexOf(button), 1);
    node.widgets.splice(node.widgets.indexOf(image) + 1, 0, button);

    const hasFile = (e) => [...(e.dataTransfer?.items ?? [])].some((i) => i.kind === "file");
    node.onDragOver = (e) => hasFile(e);
    node.onDragDrop = async (e) => {
        const file = e.dataTransfer?.files?.[0];
        if (!file) return false;
        await uploadSafely(file);
        return true;
    };

    // ---- first layout ----------------------------------------------------

    applyConstraints();
    const min = node.computeSize();
    node.setSize([Math.max(node.size[0], 340), Math.max(node.size[1], min[1] + 80)]);
    loadPreview();
}

app.registerExtension({
    name: "loadheic_croppad",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== NODE) return;
        const onNodeCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = onNodeCreated?.apply(this, arguments);
            setup(this);
            return r;
        };
    },
});
