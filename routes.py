"""Preview endpoint for the on-node editor.

Browsers cannot show HEIC, and a camera original is far too big to stream to
a node-sized canvas, so the editor asks for a downscaled JPEG/PNG instead.
Two things keep it responsive: decoding runs on a worker thread (never on the
event loop, which would stall all of ComfyUI), and results are kept in a small
cache and revalidated by ETag, so a repeat view costs a stat and a 304.
"""

import asyncio
import collections
import os

from aiohttp import web
from server import PromptServer

from . import imaging
from .nodes import input_path

PREVIEW_EDGE = 1600
CACHE_BYTES = 64 << 20

_cache = collections.OrderedDict()      # key -> (body, content_type, w, h)
_cache_size = 0
_inflight = {}                          # key -> Future; a burst decodes once


def _remember(key, entry):
    global _cache_size
    _cache[key] = entry
    _cache_size += len(entry[0])
    while _cache_size > CACHE_BYTES and len(_cache) > 1:
        _, old = _cache.popitem(last=False)
        _cache_size -= len(old[0])


async def _preview(path, key):
    entry = _cache.get(key)
    if entry is not None:
        _cache.move_to_end(key)
        return entry
    task = _inflight.get(key)
    if task is None:
        task = asyncio.get_running_loop().run_in_executor(
            None, imaging.encode_preview, path, PREVIEW_EDGE)
        _inflight[key] = task

        def done(fut):
            _inflight.pop(key, None)
            if not fut.cancelled() and fut.exception() is None:
                _remember(key, fut.result())
        task.add_done_callback(done)
    # shield: one client giving up must not cancel the decode others await
    return await asyncio.shield(task)


@PromptServer.instance.routes.get("/loadheic_croppad/preview")
async def preview(request):
    name = request.rel_url.query.get("image")
    if not name:
        return web.Response(status=400, text="image is required")
    try:
        path = input_path(name)
        if not imaging.is_image_name(path):
            raise ValueError("not an image")
        st = os.stat(path)
    except (ValueError, OSError):
        return web.Response(status=404, text="image not found")

    etag = '"%x-%x"' % (st.st_mtime_ns, st.st_size)
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if request.headers.get("If-None-Match") == etag:
        return web.Response(status=304, headers=headers)
    try:
        body, content_type, width, height = await _preview(path, path + etag)
    except Exception as exc:
        return web.Response(status=422, text="cannot decode image: %s" % exc)
    headers.update({"X-Image-Width": str(width), "X-Image-Height": str(height)})
    return web.Response(body=body, content_type=content_type, headers=headers)
