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


def _orientation(img):
    try:
        return img.getexif().get(0x0112, 1)
    except Exception:
        return 1


def _size_already_upright(img):
    """True if Pillow has already swapped img.size for the orientation.

    Its TIFF reader does: it reports the turned size as soon as the file is
    opened, turns the pixels itself while loading, and then drops the tag. So
    the stored dimensions differ from img.size, and swapping again would be
    wrong. (Compared with the tags rather than assumed from the format, so it
    holds whichever Pillow does it.) A square image reads the same either way.
    """
    tags = getattr(img, "tag_v2", None)
    if tags is None:
        return False
    stored = (tags.get(0x0100), tags.get(0x0101))    # ImageWidth, ImageLength
    return None not in stored and stored != img.size


def upright_size(img):
    """Size the image will have once EXIF orientation is applied."""
    w, h = img.size
    if _orientation(img) in (5, 6, 7, 8) and not _size_already_upright(img):
        w, h = h, w
    return w, h


def to_arrays(img):
    """-> (rgb uint8 HxWx3, alpha uint8 HxW or None), upright.

    Every step that would copy all the pixels is skipped when it has nothing
    to do (an upright image, one already RGB, alpha that is already a band):
    at 8192x8192 each copy is 192 MB, and they used to stack up.
    """
    if _orientation(img) in (2, 3, 4, 5, 6, 7, 8):
        img = ImageOps.exif_transpose(img)       # always copies, so only when it rotates
    if img.mode == "I":
        img = img.point(lambda v: v * (1 / 255))
    bands = img.getbands()
    has_alpha = ("A" in bands or "a" in bands
                 or (img.mode == "P" and "transparency" in img.info))
    rgb = np.asarray(img if img.mode == "RGB" else img.convert("RGB"))
    alpha = None
    if has_alpha:
        band = img.getchannel("A") if "A" in bands else img.convert("RGBA").getchannel("A")
        alpha = np.asarray(band)
    return rgb, alpha


def to_float(rgb):
    """uint8 HxWx3 -> float32 in 0..1, exactly rgb.astype(float32) / 255.

    Written in place and a band of rows at a time: the one-liner builds the
    whole float array twice (1.6 GB at 8192x8192 instead of 0.8 GB), and the
    result is a fresh writable array, which torch.from_numpy needs to be
    quiet (a crop is a read-only view of the decoded image).
    """
    out = np.empty(rgb.shape, np.float32)
    for y in range(0, rgb.shape[0], 256):
        band = out[y:y + 256]
        band[...] = rgb[y:y + 256]
        band /= 255.0
    return out


def _mask_of(alpha, out):
    """Into `out` (float32, same shape as alpha): 1 where transparent. Exactly
    1.0 - alpha.astype(float32) / 255.0, without the three temporaries."""
    np.divide(alpha, 255.0, out=out, dtype=np.float32)
    np.subtract(1.0, out, out=out)
    return out


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
        return out, _mask_of(alpha[y:y + h, x:x + w], np.empty((h, w), np.float32))

    # the part of the box that lies on the image, in image and in box coordinates
    x0, y0, x1, y1 = max(0, x), max(0, y), min(iw, x + w), min(ih, y + h)
    overlap = x1 > x0 and y1 > y0

    if fill == "edge" and overlap:
        out = np.pad(rgb[y0:y1, x0:x1],
                     ((y0 - y, y + h - y1), (x0 - x, x + w - x1), (0, 0)), mode="edge")
    elif fill == "edge":        # no image under the box at all: repeat the nearest edge pixels
        cols = np.clip(np.arange(x, x + w), 0, iw - 1)
        rows = np.clip(np.arange(y, y + h), 0, ih - 1)
        out = rgb[rows[:, None], cols[None, :]]
    else:
        out = np.full((h, w, 3), PAD_FILLS[fill], np.uint8)
        _paste(out, rgb, -x, -y)

    mask = np.ones((h, w), np.float32)                  # padding is 1; the image is cleared below
    if overlap:
        under = mask[y0 - y:y1 - y, x0 - x:x1 - x]
        if alpha is None:
            under[...] = 0.0
        else:
            _mask_of(alpha[y0:y1, x0:x1], under)
    return out, mask


# What an EXIF orientation value asks for (the table ImageOps.exif_transpose uses).
_UPRIGHT = {
    2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}
_SHRINK_FIRST = ("RGB", "RGBA", "L", "LA")     # modes where shrinking needs no conversion


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
        if img.mode in _SHRINK_FIRST:
            # Shrink before anything else copies the pixels: turning a 64 MP
            # image into arrays and back costs about a gigabyte and a second
            # to make a picture 1600 px across. The bound is square, so
            # shrinking then turning gives the picture turning then shrinking
            # does. Load first and read the orientation then, as
            # exif_transpose does: a TIFF has its orientation applied (and the
            # tag cleared) by the load, and the shrunk copy may not carry the
            # metadata at all.
            img.load()
            orientation = _orientation(img)
            img.thumbnail((max_edge, max_edge), Image.LANCZOS, reducing_gap=2.0)
            pil = img.transpose(_UPRIGHT[orientation]) if orientation in _UPRIGHT else img
            has_alpha = "A" in pil.getbands()
            pil = pil.convert("RGBA" if has_alpha else "RGB")
        else:
            rgb, alpha = to_arrays(img)
            has_alpha = alpha is not None
            pil = Image.fromarray(rgb)
            if has_alpha:
                pil.putalpha(Image.fromarray(alpha))
            pil.thumbnail((max_edge, max_edge), Image.LANCZOS, reducing_gap=2.0)
        buf = io.BytesIO()
        if has_alpha:
            pil.save(buf, "PNG", compress_level=1)
            return buf.getvalue(), "image/png", width, height
        pil.save(buf, "JPEG", quality=88)
        return buf.getvalue(), "image/jpeg", width, height
