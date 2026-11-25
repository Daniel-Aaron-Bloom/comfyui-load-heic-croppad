# Load Image (HEIC) Crop & Pad

A ComfyUI node that loads an image, **HEIC/HEIF included**, and cuts it with a
box you draw on the node. The box can be locked to an aspect ratio, snapped to a
pixel multiple, and pulled past the edge of the image to **pad** it.

## Install

```
cd ComfyUI/custom_nodes
git clone <this repo> comfyui-load-heic-croppad
pip install -r comfyui-load-heic-croppad/requirements.txt
```

Restart ComfyUI. The node is **Load Image (HEIC) Crop & Pad**, under `image`.
`pillow-heif` is only needed for HEIC/HEIF; other formats load without it.

## Using it

Pick a file from the `image` list, or use **upload image** / drop a file onto the
node (HEIC works for both).

| On the preview | Does |
| --- | --- |
| drag on empty space | draw a new box |
| drag inside the box | move it |
| drag a corner | resize it |
| click outside the box | clear it |

With no box drawn, the node outputs the whole image, or, if `aspect_ratio` /
`multiple_of` don't suit it, the largest centered cut that does (dashed on the
preview).

**Padding.** Drag a corner (or move the box) past the edge of the image. The part
of the box outside the image is hatched in the preview and filled with `pad_fill`.
Landing on an edge never pads; you have to pull clearly past it.

| Widget | |
| --- | --- |
| `aspect_ratio` | Lock the box's shape. Free text: `16:9`, `4/3`, `1920x1080`, a decimal ratio like `2.39:1`, a bare width/height number like `1.5`, or `free` (or empty) for no lock. Exact when `multiple_of` is 1 (to the pixel); otherwise as close as the multiple allows. 16:9 on a multiple of 64 gives 1920×1088. Text that isn't a ratio is rejected when the workflow is queued; until it parses, the preview keeps the last good ratio. |
| `multiple_of` | Output width and height are multiples of this (8, 16, 32, 64 for most models). |
| `pad_fill` | `gray`, `black`, `white`, or `edge` (repeat the border pixels). |

Outputs: `image`, and `mask`, which is 1 over transparent pixels **and over all
padding**, so it can drive an outpainting/inpainting step directly.

## Notes

- HEIC previews are decoded on a worker thread, downscaled, cached and
  revalidated by ETag. A 12 MP HEIC takes about 0.6 s the first time and about a
  millisecond after that, and ComfyUI stays responsive while it decodes.
- EXIF orientation is applied. Only the first frame is loaded (no animation
  batches). Colors are loaded as-is, like the stock Load Image: a Display P3
  photo is not converted to sRGB.
- The box is stored on the node in source pixels, so a saved workflow
  reproduces the same cut. If a file is replaced by one of another size, the box
  is scaled to fit.

## Development

```
python -m unittest discover -s tests
```

`web/geometry.js` mirrors `geometry.py` exactly, so the editor shows the box the
node will cut; `tests/test_geometry_parity.py` checks the two agree (needs `node`).

## License

MIT, see [LICENSE](LICENSE).
