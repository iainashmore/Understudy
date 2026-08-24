"""Artifact normalisation.

Every layer produces something different -- a browser screenshot, a Pillow
canvas, a raw pixel buffer -- so all of it is funnelled through here and comes
out as an (H, W, 3) uint8 array before anything is compared.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image

Artifact = bytes | Path | str

# Alpha is composited over white. Task canvases are opaque, so this only
# matters for a layer that hands back a partially transparent buffer -- in which
# case a defined, boring answer beats an undefined one.
COMPOSITE_BACKGROUND = 255


def load_rgb(artifact: Artifact) -> np.ndarray:
    """Decode a PNG (bytes or path) to an (H, W, 3) uint8 array."""
    if isinstance(artifact, (str, Path)):
        path = Path(artifact)
        if not path.exists():
            raise FileNotFoundError(f"no artifact at {path}")
        data = path.read_bytes()
    else:
        data = artifact

    if not data:
        raise ValueError("artifact is empty")

    with Image.open(io.BytesIO(data)) as image:
        image.load()
        if image.mode in ("RGBA", "LA") or (
            image.mode == "P" and "transparency" in image.info
        ):
            rgba = image.convert("RGBA")
            backdrop = Image.new("RGBA", rgba.size, (
                COMPOSITE_BACKGROUND, COMPOSITE_BACKGROUND,
                COMPOSITE_BACKGROUND, 255,
            ))
            flattened = Image.alpha_composite(backdrop, rgba)
            return np.asarray(flattened.convert("RGB"), dtype=np.uint8)
        return np.asarray(image.convert("RGB"), dtype=np.uint8)


def to_png_bytes(array: np.ndarray) -> bytes:
    """Encode an (H, W, 3) uint8 array as PNG."""
    buffer = io.BytesIO()
    Image.fromarray(np.ascontiguousarray(array, dtype=np.uint8), mode="RGB").save(
        buffer, format="PNG"
    )
    return buffer.getvalue()


def resize_to(array: np.ndarray, height: int, width: int) -> np.ndarray:
    """Resample to an exact size. Used when a layer hands back the wrong
    resolution (a HiDPI screenshot, say) rather than failing the run outright."""
    if array.shape[:2] == (height, width):
        return array
    image = Image.fromarray(array, mode="RGB").resize(
        (width, height), resample=Image.LANCZOS
    )
    return np.asarray(image, dtype=np.uint8)


def _gaussian_kernel(sigma: float) -> np.ndarray:
    radius = max(1, int(np.ceil(3.0 * sigma)))
    offsets = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-(offsets**2) / (2.0 * sigma**2))
    return kernel / kernel.sum()


def gaussian_blur(array: np.ndarray, sigma: float) -> np.ndarray:
    """Separable Gaussian blur, edge-padded, returned as float64.

    Hand-rolled rather than taken from Pillow or scipy so the scorer's numbers
    do not move when a dependency changes its filter implementation. Scores have
    to be comparable across runs months apart.
    """
    working = array.astype(np.float64)
    if sigma <= 0:
        return working

    kernel = _gaussian_kernel(sigma)
    radius = (len(kernel) - 1) // 2

    padded = np.pad(working, ((radius, radius), (0, 0), (0, 0)), mode="edge")
    rows = np.zeros_like(working)
    for index, weight in enumerate(kernel):
        rows += weight * padded[index : index + working.shape[0]]

    padded = np.pad(rows, ((0, 0), (radius, radius), (0, 0)), mode="edge")
    columns = np.zeros_like(working)
    for index, weight in enumerate(kernel):
        columns += weight * padded[:, index : index + working.shape[1]]

    return columns
