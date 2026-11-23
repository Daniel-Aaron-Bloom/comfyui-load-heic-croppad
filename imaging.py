"""Image decoding, preview encoding and the crop/pad cut itself.

No ComfyUI imports: paths come in as plain strings, so this runs (and is
tested) without a ComfyUI install.
"""

import io
import os

import numpy as np
from PIL import Image, ImageOps

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif",
              ".tif", ".tiff", ".heic", ".heif")
HEIF_EXTS = (".heic", ".heif")

PAD_FILLS = {"gray": 128, "black": 0, "white": 255}   # plus "edge"
PAD_CHOICES = ["gray", "black", "white", "edge"]

_heif_registered = None


def _register_heif():
    """Teach Pillow to read HEIC/HEIF. True if pillow-heif is available."""
    global _heif_registered
    if _heif_registered is None:
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
            _heif_registered = True
        except Exception:
            _heif_registered = False
    return _heif_registered


# Pillow reads these through the same registered plugins; do it once up front
# so the first HEIC in the file list needs no special handling.
_register_heif()


def is_image_name(name):
    return os.path.splitext(name)[1].lower() in IMAGE_EXTS


def open_image(path):
    """Open the first frame, upright. Raises a readable error, not a traceback
    about plugins, when a HEIC cannot be decoded."""
    ext = os.path.splitext(path)[1].lower()
    if ext in HEIF_EXTS and not _register_heif():
        raise RuntimeError(
            "HEIC/HEIF support needs the pillow-heif package: "
            "pip install pillow-heif")
    return Image.open(path)


def upright_size(img):
    """Size the image will have once EXIF orientation is applied."""
    w, h = img.size
    try:
        if img.getexif().get(0x0112, 1) in (5, 6, 7, 8):
            w, h = h, w
    except Exception:
        pass
    return w, h


def to_arrays(img):
    """-> (rgb uint8 HxWx3, alpha uint8 HxW or None), upright."""
    img = ImageOps.exif_transpose(img)
    if img.mode == "I":
        img = img.point(lambda v: v * (1 / 255))
    bands = img.getbands()
    has_alpha = ("A" in bands or "a" in bands
                 or (img.mode == "P" and "transparency" in img.info))
    rgb = np.asarray(img.convert("RGB"))
    alpha = np.asarray(img.convert("RGBA").getchannel("A")) if has_alpha else None
    return rgb, alpha


def load_arrays(path):
    with open_image(path) as img:
        return to_arrays(img)


def read_size(path):
    """Upright pixel size without decoding the pixels."""
    with open_image(path) as img:
        return upright_size(img)


def _paste(dst, src, x, y):
    """Copy src into dst, where src's top-left sits at (x, y) in dst; the
    parts that fall outside dst are dropped."""
    sh, sw = src.shape[:2]
    dh, dw = dst.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(dw, x + sw), min(dh, y + sh)
    if x1 > x0 and y1 > y0:
        dst[y0:y1, x0:x1] = src[y0 - y:y1 - y, x0 - x:x1 - x]


def crop_pad(rgb, alpha, box, fill):
    """Cut `box` (x, y, w, h; may overhang the image) out of the image.

    -> (rgb uint8 hxwx3, mask float32 hxw). The mask follows ComfyUI's
    LoadImage convention (1 = transparent) and is also 1 over all padding,
    so it doubles as the "area to fill" mask for outpainting.
    """
    x, y, w, h = box
    ih, iw = rgb.shape[:2]

    if x >= 0 and y >= 0 and x + w <= iw and y + h <= ih:        # no padding
        out = rgb[y:y + h, x:x + w]
        if alpha is None:
            return out, np.zeros((h, w), np.float32)
        return out, 1.0 - alpha[y:y + h, x:x + w].astype(np.float32) / 255.0

    if fill == "edge":
        cols = np.clip(np.arange(x, x + w), 0, iw - 1)
        rows = np.clip(np.arange(y, y + h), 0, ih - 1)
        out = rgb[rows[:, None], cols[None, :]]
    else:
        out = np.full((h, w, 3), PAD_FILLS[fill], np.uint8)
        _paste(out, rgb, -x, -y)

    mask = np.ones((h, w), np.float32)
    if alpha is None:
        src_mask = np.zeros((ih, iw), np.float32)
    else:
        src_mask = 1.0 - alpha.astype(np.float32) / 255.0
    _paste(mask, src_mask, -x, -y)
    return out, mask


def encode_preview(path, max_edge):
    """-> (bytes, content_type, source_width, source_height).

    A downscaled copy for the on-node editor. The editor stores crop boxes in
    SOURCE pixels, so the true size travels alongside. JPEG unless the image
    has transparency.
    """
    with open_image(path) as img:
        width, height = upright_size(img)
        if img.format == "JPEG":
            img.draft("RGB", (max_edge, max_edge))   # decode at reduced scale
        rgb, alpha = to_arrays(img)
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
