import hashlib
import os

import numpy as np
import torch

import folder_paths

from . import imaging
from .geometry import ASPECTS, parse_crop, resolve_box


def input_path(name):
    """Absolute path of an annotated image name, confined to its base folder.

    Mirrors folder_paths' "name [input|output|temp]" convention, but does its
    own confinement check so it does not depend on the ComfyUI version having
    one (the preview route hands this a name straight from the URL).
    """
    name = str(name)
    base = folder_paths.get_input_directory()
    for suffix, getter in (("[output]", folder_paths.get_output_directory),
                           ("[input]", folder_paths.get_input_directory),
                           ("[temp]", folder_paths.get_temp_directory)):
        if name.endswith(suffix):
            name, base = name[:-len(suffix)].rstrip(), getter()
            break
    base = os.path.abspath(base)
    path = os.path.abspath(os.path.join(base, name))
    if os.path.commonpath((base, path)) != base:
        raise ValueError("Invalid file path: %r" % name)
    return path


def list_images():
    root = folder_paths.get_input_directory()
    try:
        names = [f for f in os.listdir(root)
                 if imaging.is_image_name(f) and os.path.isfile(os.path.join(root, f))]
    except OSError:
        names = []
    return sorted(names)


class LoadImageHEICCropPad:
    CATEGORY = "image"
    FUNCTION = "load"
    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("image", "mask")
    OUTPUT_TOOLTIPS = (
        "The image, cropped and padded as drawn on the node.",
        "1 where the image is transparent or where padding was added, else 0.",
    )
    DESCRIPTION = (
        "Loads an image (HEIC/HEIF included) and cuts the area drawn on the "
        "node's preview. Drag on the preview to draw the area, drag inside "
        "it to move, drag a corner to resize, click to clear. Pull the area "
        "past the edge of the image to pad it. With nothing drawn the output "
        "is the whole image, or the largest centered cut that satisfies the "
        "aspect ratio and multiple."
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": (list_images(), {
                    "tooltip": "Image file from the input folder. HEIC/HEIF "
                               "files can be uploaded from the node.",
                }),
                "aspect_ratio": (list(ASPECTS), {
                    "default": "free",
                    "tooltip": "Lock the area's shape. As close as the "
                               "multiple allows: 16:9 at a multiple of 64 "
                               "gives 1920x1088, not an exact 16:9.",
                }),
                "multiple_of": ("INT", {
                    "default": 1, "min": 1, "max": 512, "step": 1,
                    "tooltip": "Width and height of the output are "
                               "multiples of this many pixels (8, 16, 32, "
                               "64 for most models).",
                }),
                "pad_fill": (imaging.PAD_CHOICES, {
                    "default": "gray",
                    "tooltip": "What fills the area outside the image. "
                               "'edge' repeats the image's border pixels.",
                }),
                # Written by the editor on the node; never edited by hand.
                "crop": ("STRING", {"default": "", "multiline": False}),
            }
        }

    def load(self, image, aspect_ratio, multiple_of, pad_fill, crop=""):
        rgb, alpha = imaging.load_arrays(input_path(image))
        ih, iw = rgb.shape[:2]
        box = resolve_box(crop, iw, ih, ASPECTS[aspect_ratio], multiple_of)
        out, mask = imaging.crop_pad(rgb, alpha, box, pad_fill)
        out = torch.from_numpy(out.astype(np.float32) / 255.0)[None]
        return (out, torch.from_numpy(mask)[None])

    @classmethod
    def IS_CHANGED(cls, image, **kwargs):
        digest = hashlib.sha256()
        with open(input_path(image), "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def VALIDATE_INPUTS(cls, image, aspect_ratio, multiple_of, pad_fill, crop=""):
        try:
            path = input_path(image)
        except ValueError as exc:
            return str(exc)
        if not (imaging.is_image_name(path) and os.path.isfile(path)):
            return "Invalid image file: %s" % image
        if aspect_ratio not in ASPECTS:
            return "Invalid aspect ratio: %s" % aspect_ratio
        if pad_fill not in imaging.PAD_CHOICES:
            return "Invalid pad fill: %s" % pad_fill
        if not 1 <= multiple_of <= 512:
            return "multiple_of must be between 1 and 512"
        try:
            parse_crop(crop, 1, 1)
        except ValueError as exc:
            return str(exc)
        return True


NODE_CLASS_MAPPINGS = {"LoadImageHEICCropPad": LoadImageHEICCropPad}
NODE_DISPLAY_NAME_MAPPINGS = {"LoadImageHEICCropPad": "Load Image (HEIC) Crop & Pad"}
