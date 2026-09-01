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

#: Sizes an anchor is tried at when it does not match at the one it was
#: captured at. Windows offers 100/125/150/175/200% and applications add their
#: own zoom on top; these are the ratios between the pairs anybody actually
#: lands on, commonest first.
SCALES = (1.25, 0.8, 1.5, 1.1, 0.9, 1.2, 1.33, 0.75, 0.67, 1.75, 2.0, 0.5)

#: A resized anchor never matches as well as the one that was captured. A UI
#: re-rendered at a different DPI is not the same picture scaled -- the text is
#: hinted differently, the borders land on different pixels -- and measured
#: against a genuine re-render the best possible score is around 0.7-0.85.
#: Holding those to DEFAULT_THRESHOLD would reject every one of them.
SCALED_FLOOR = 0.6

#: ...so the score alone cannot decide it, and this does: how far the best
#: position leads the best rival somewhere else. Measured on re-renders of a
#: toolbar of near-identical buttons, the true position leads by 0.12-0.15
#: (and by 0.27-0.32 where the controls are distinct); with the control
#: removed, the best remaining position leads by 0.04. The score cannot tell
#: those apart -- an absent control still scores 0.81 -- and the lead can.
SCALED_MARGIN = 0.08

#: Scale searching at full resolution costs ~2.4s per size on a 1936x1096
#: window -- half a minute for a dozen. At half resolution it costs 0.18s, so
#: the search is done small and only the best few are confirmed at full size.
COARSE = 0.5

#: How many sizes to confirm at full resolution. The coarse pass ranks the
#: right one first most of the time but not always -- with half the pixels
#: gone, 1.2 and 1.25 are nearly the same picture -- so the shortlist takes
#: the best few *and* the winner's immediate neighbours.
CONFIRM = 2


@dataclass(frozen=True)
class Match:
    x: int
    y: int
    width: int
    height: int
    score: float
    #: The size the anchor had to be tried at to be found. 1.0 means it matched
    #: as captured; anything else means the interface is not at the scale it
    #: was recorded at, and `width`/`height` are the scaled size.
    scale: float = 1.0

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


def resized(image: np.ndarray, scale: float) -> np.ndarray:
    """The same picture at a different size.

    Lanczos rather than nearest-neighbour: a nearest-neighbour icon is a
    different picture with aliasing all over it, and correlation notices.
    """
    from PIL import Image

    if scale == 1.0:
        return image
    picture = Image.fromarray(np.ascontiguousarray(image, dtype=np.uint8))
    size = (max(1, round(picture.width * scale)),
            max(1, round(picture.height * scale)))
    return np.asarray(picture.resize(size, Image.LANCZOS), dtype=np.uint8)


def _best_and_rival(scores: np.ndarray, shape: tuple[int, int]):
    """The top position, and the best position that is not it.

    Positions overlapping the winner are its own peak seen one pixel over, not
    a rival, so they are suppressed before the second is taken.
    """
    height, width = shape
    flat = int(np.argmax(scores))
    y, x = np.unravel_index(flat, scores.shape)
    others = scores.copy()
    others[max(0, y - height): y + height, max(0, x - width): x + width] = -1.0
    return float(scores[y, x]), float(others.max()), int(x), int(y)


def _shortlist(ranked: list[tuple[float, float]], confirm: int) -> list[float]:
    """The sizes worth a full-resolution look.

    The best few by the coarse score, plus whatever sits either side of the
    winner: at half resolution neighbouring sizes are nearly indistinguishable,
    and the one the coarse pass puts second is regularly the right one.
    """
    if not ranked:
        return []
    chosen = [scale for _, scale in ranked[:confirm]]
    order = sorted(scale for _, scale in ranked)
    winner = order.index(ranked[0][1])
    for index in (winner - 1, winner + 1):
        if 0 <= index < len(order) and order[index] not in chosen:
            chosen.append(order[index])
    return chosen


def locate_scaled(
    screenshot: bytes | np.ndarray,
    anchor: bytes | np.ndarray,
    scales: tuple[float, ...] = SCALES,
    region: dict[str, int] | None = None,
    floor: float = SCALED_FLOOR,
    margin: float = SCALED_MARGIN,
    coarse: float = COARSE,
    confirm: int = CONFIRM,
) -> Match | None:
    """Find the anchor when the interface is not at the size it was captured at.

    The rescue path for a recording made at one DPI and replayed at another --
    a different monitor, a different workstation, a laptop undocked. Only worth
    reaching for once `locate` has failed at the anchor's own size, because it
    is both slower and, necessarily, less certain.

    Accepting a match here cannot be a question of score: a re-rendered control
    scores well below what a strict threshold demands. What it asks instead is
    whether this position is *clearly* the best one on the screen -- ahead of
    the runner-up by `margin` -- which is the property that actually
    distinguishes "found it, slightly blurred" from "found the least bad of
    several things that all look like this".
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

    def fits(small: np.ndarray, large: np.ndarray) -> bool:
        return small.shape[0] <= large.shape[0] and small.shape[1] <= large.shape[1]

    ranked: list[tuple[float, float]] = []
    small_haystack = resized(haystack, coarse)
    for scale in scales:
        candidate = resized(resized(needle, scale), coarse)
        if not fits(candidate, small_haystack):
            continue
        scores = correlate(small_haystack, candidate)
        if scores.size:
            ranked.append((float(scores.max()), scale))
    ranked.sort(reverse=True)

    best: Match | None = None
    for scale in _shortlist(ranked, confirm):
        candidate = resized(needle, scale)
        if not fits(candidate, haystack):
            continue
        scores = correlate(haystack, candidate)
        if not scores.size:
            continue
        score, rival, x, y = _best_and_rival(scores, candidate.shape[:2])
        if score < floor or score - rival < margin:
            continue
        if best is None or score > best.score:
            best = Match(
                x=x + offset_x, y=y + offset_y,
                width=candidate.shape[1], height=candidate.shape[0],
                score=score, scale=scale,
            )
    return best


def crop(screenshot: bytes | np.ndarray, region: dict[str, int]) -> np.ndarray:
    image = screenshot if isinstance(screenshot, np.ndarray) else load_rgb(screenshot)
    return image[
        int(region["y"]) : int(region["y"]) + int(region["height"]),
        int(region["x"]) : int(region["x"]) + int(region["width"]),
    ]


def changed_region(before: np.ndarray, after: np.ndarray, tolerance: int = 12,
                   min_fraction: float = 0.01, pad: int = 8) -> dict[str, int] | None:
    """Where the picture changed between two moments.

    Used to find where a reply appears without anyone having to describe it:
    take the screen before the question is sent and again once the answer has
    landed, and the part that changed is the part to read.

    Rows and columns are kept only if a real proportion of them changed. A
    blinking caret, a clock, an antialiased edge -- each moves a handful of
    pixels, and a bounding box drawn around every changed pixel would stretch
    across the whole window and read the entire interface.
    """
    if before.shape != after.shape:
        return None
    difference = np.abs(before.astype(np.int16) - after.astype(np.int16))
    changed = difference.max(axis=2) > tolerance
    if not changed.any():
        return None

    rows = changed.mean(axis=1) > min_fraction
    columns = changed.mean(axis=0) > min_fraction
    if not rows.any() or not columns.any():
        # Something changed, but nothing changed *much*. Better to hand back
        # the small thing that did than to report nothing.
        rows, columns = changed.any(axis=1), changed.any(axis=0)

    top, bottom = np.flatnonzero(rows)[[0, -1]]
    left, right = np.flatnonzero(columns)[[0, -1]]
    height, width = changed.shape
    top = max(0, int(top) - pad)
    left = max(0, int(left) - pad)
    bottom = min(height - 1, int(bottom) + pad)
    right = min(width - 1, int(right) + pad)
    return {"x": left, "y": top,
            "width": right - left + 1, "height": bottom - top + 1}
