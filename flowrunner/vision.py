"""Finding things by their appearance.

The bottom rung of the target ladder, for surfaces that expose nothing at all:
no DOM, no accessibility tree, no automation ids. A custom-drawn CAD toolbar, a
canvas, an embedded view with debugging switched off.

An anchor is a small image of the control, captured when the flow was authored.
At replay it is located in the *current* screenshot by normalised cross-
correlation, and the click point is derived from where it was found now.

That is not the same as storing coordinates, and the difference is the whole
point of the core design rule: a stored coordinate is wrong the moment the
window moves, whereas an anchor is re-located every run. It still fails on a
theme change or a different DPI, so it stays the last resort, below anything
semantic.

Normalised cross-correlation is used rather than exact pixel equality because
sub-pixel rendering, hover states and mild compression all shift values without
changing what the control looks like. NCC is invariant to uniform brightness and
contrast shifts, which covers most of that.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from harness.image import load_rgb

#: Below this the match is not trustworthy. Chosen to be strict: a wrong click
#: is worse than a clean failure.
DEFAULT_THRESHOLD = 0.9


@dataclass(frozen=True)
class Match:
    x: int
    y: int
    width: int
    height: int
    score: float

    @property
    def centre(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)


def to_gray(image: np.ndarray) -> np.ndarray:
    """Rec. 601 luma. Colour adds cost without adding discrimination for UI
    chrome, which is mostly greys."""
    return (
        0.299 * image[:, :, 0] + 0.587 * image[:, :, 1] + 0.114 * image[:, :, 2]
    ).astype(np.float64)


def _windows(haystack: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Every candidate window as a view. Only used by the reference
    implementation -- it is O(positions x template) in memory once anything
    subtracts from it, which on a full application window is tens of gigabytes.
    """
    height, width = shape
    output_shape = (
        haystack.shape[0] - height + 1,
        haystack.shape[1] - width + 1,
        height,
        width,
    ) + haystack.shape[2:]
    strides = haystack.strides[:2] * 2 + haystack.strides[2:]
    return np.lib.stride_tricks.as_strided(
        haystack, shape=output_shape, strides=strides, writeable=False
    )


def correlate_naive(haystack: np.ndarray, needle: np.ndarray) -> np.ndarray:
    """Direct definition. Correct, tiny, and far too hungry for real images --
    kept as the reference the fast path is checked against."""
    windows = _windows(haystack, needle.shape[:2])
    axes = tuple(range(2, windows.ndim))
    centred_needle = needle - needle.mean()
    needle_norm = np.sqrt((centred_needle**2).sum())
    if needle_norm == 0:
        return np.zeros(windows.shape[:2])
    centred = windows - windows.mean(axis=axes, keepdims=True)
    numerator = (centred * centred_needle).sum(axis=axes)
    denominator = np.sqrt((centred**2).sum(axis=axes)) * needle_norm
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(denominator > 0, numerator / denominator, 0.0)


def _window_sums(plane: np.ndarray, height: int, width: int) -> np.ndarray:
    """Sum over every h x w window, in constant time per window."""
    integral = np.pad(plane.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    return (
        integral[height:, width:]
        - integral[:-height, width:]
        - integral[height:, :-width]
        + integral[:-height, :-width]
    )


def _cross_correlate(haystack: np.ndarray, needle: np.ndarray) -> np.ndarray:
    """Sum of I*T over every window, via FFT, summed across channels."""
    rows, columns = haystack.shape[:2]
    height, width = needle.shape[:2]
    padded = (rows + height - 1, columns + width - 1)

    spectrum = np.fft.rfft2(haystack, s=padded, axes=(0, 1))
    template = np.fft.rfft2(needle, s=padded, axes=(0, 1))
    full = np.fft.irfft2(spectrum * np.conj(template), s=padded, axes=(0, 1))
    valid = full[: rows - height + 1, : columns - width + 1]
    return valid.sum(axis=2) if valid.ndim == 3 else valid


def correlate(haystack: np.ndarray, needle: np.ndarray) -> np.ndarray:
    """Normalised cross-correlation score for every position.

    Correlates across colour channels rather than on luma. Toolbar icons are
    routinely distinguished only by hue, and a red glyph and a blue one on the
    same grey chrome have almost identical luminance -- a grayscale match
    cannot tell them apart and would happily click the wrong tool.

    Computed from the expanded form of the correlation coefficient: the I*T
    term by FFT, the window sums and sums of squares by integral image. The
    direct definition is unusable here -- on a 1100x700 window it needs tens of
    gigabytes.
    """
    haystack = np.atleast_3d(haystack).astype(np.float64)
    needle = np.atleast_3d(needle).astype(np.float64)
    height, width = needle.shape[:2]
    count = height * width * needle.shape[2]

    needle_mean = needle.mean()
    centred_needle = needle - needle_mean
    needle_norm = np.sqrt((centred_needle**2).sum())
    if needle_norm == 0:
        # A flat anchor carries no information and would match everywhere.
        return np.zeros(
            (haystack.shape[0] - height + 1, haystack.shape[1] - width + 1)
        )

    sums = sum(
        _window_sums(haystack[:, :, c], height, width)
        for c in range(haystack.shape[2])
    )
    squares = sum(
        _window_sums(haystack[:, :, c] ** 2, height, width)
        for c in range(haystack.shape[2])
    )

    # sum((I-mI)(T-mT)) reduces to sum(I*T) - mean_T * sum(I), because the
    # centred template sums to zero.
    numerator = _cross_correlate(haystack, needle) - needle_mean * sums
    variance = np.maximum(squares - (sums**2) / count, 0.0)
    denominator = np.sqrt(variance) * needle_norm

    with np.errstate(divide="ignore", invalid="ignore"):
        scores = np.where(denominator > 0, numerator / denominator, 0.0)
    return np.clip(scores, -1.0, 1.0)


def locate(
    screenshot: bytes | np.ndarray,
    anchor: bytes | np.ndarray,
    threshold: float = DEFAULT_THRESHOLD,
    region: dict[str, int] | None = None,
) -> Match | None:
    """Find the anchor in the screenshot, or return None.

    `region` narrows the search, which matters for speed on a full application
    window and also disambiguates a control that appears in several places.
    """
    haystack = screenshot if isinstance(screenshot, np.ndarray) else load_rgb(screenshot)
    needle = anchor if isinstance(anchor, np.ndarray) else load_rgb(anchor)

    offset_x = offset_y = 0
    if region:
        offset_x, offset_y = int(region.get("x", 0)), int(region.get("y", 0))
        haystack = haystack[
            offset_y : offset_y + int(region["height"]),
            offset_x : offset_x + int(region["width"]),
        ]

    if needle.shape[0] > haystack.shape[0] or needle.shape[1] > haystack.shape[1]:
        return None

    scores = correlate(haystack, needle)
    if scores.size == 0:
        return None

    flat = int(np.argmax(scores))
    y, x = np.unravel_index(flat, scores.shape)
    best = float(scores[y, x])
    if best < threshold:
        return None
    return Match(
        x=int(x) + offset_x, y=int(y) + offset_y,
        width=needle.shape[1], height=needle.shape[0], score=best,
    )


def locate_all(
    screenshot: bytes | np.ndarray,
    anchor: bytes | np.ndarray,
    threshold: float = DEFAULT_THRESHOLD,
    region: dict[str, int] | None = None,
) -> list[Match]:
    """Every non-overlapping match above the threshold.

    Used to detect ambiguity: several matches means the anchor does not identify
    one control, and the same rule applies here as everywhere else -- ambiguity
    is not resolution.
    """
    haystack = screenshot if isinstance(screenshot, np.ndarray) else load_rgb(screenshot)
    needle = anchor if isinstance(anchor, np.ndarray) else load_rgb(anchor)

    offset_x = offset_y = 0
    if region:
        offset_x, offset_y = int(region.get("x", 0)), int(region.get("y", 0))
        haystack = haystack[
            offset_y : offset_y + int(region["height"]),
            offset_x : offset_x + int(region["width"]),
        ]
    if needle.shape[0] > haystack.shape[0] or needle.shape[1] > haystack.shape[1]:
        return []

    scores = correlate(haystack, needle)
    height, width = needle.shape[:2]
    matches: list[Match] = []

    working = scores.copy()
    while True:
        flat = int(np.argmax(working))
        y, x = np.unravel_index(flat, working.shape)
        best = float(working[y, x])
        if best < threshold:
            break
        matches.append(
            Match(int(x) + offset_x, int(y) + offset_y, width, height, best)
        )
        # Suppress this match's neighbourhood so the next peak is a different
        # control rather than the same one one pixel over.
        top, left = max(0, y - height // 2), max(0, x - width // 2)
        working[top : y + height // 2 + 1, left : x + width // 2 + 1] = -1.0
        if len(matches) >= 50:
            break
    return matches


def crop(screenshot: bytes | np.ndarray, region: dict[str, int]) -> np.ndarray:
    image = screenshot if isinstance(screenshot, np.ndarray) else load_rgb(screenshot)
    return image[
        int(region["y"]) : int(region["y"]) + int(region["height"]),
        int(region["x"]) : int(region["x"]) + int(region["width"]),
    ]
