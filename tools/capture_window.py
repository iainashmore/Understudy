#!/usr/bin/env python3
"""Capture a window, and cut anchors out of the capture.

The blind path finds controls by matching pictures and reads text by OCR off
the pixels. Both need one thing first: a picture of the window, taken through
the same capture path the driver uses at replay time. A screenshot from the
Snipping Tool is not that -- different scaling, different cropping, different
colour handling -- and an anchor cut from one is a near-miss forever.

    python capture_window.py --title "*3DEXPERIENCE*"

writes window.png and window-grid.png. The second is the first with a
labelled 100-pixel grid drawn over it, which is how someone who cannot see
your screen reads a region off it and hands you back exact numbers.

    python capture_window.py --cut window.png --box 1240,880,300,60 \
        --out anchors/leo/prompt_box.png

cuts one of those regions out as an anchor. Anchors are never committed: they
belong to the machine, the theme and the DPI they were cut on.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

GRID = 100
#: Bright enough to read over a dark CAD interface and a white dialog alike.
GRID_COLOUR = (255, 64, 64)


def with_grid(image):
    """The capture with a labelled grid over it.

    Reading "the prompt box is around x=1240, y=880" off a bare screenshot is
    guesswork, and a guessed anchor fails at replay time on the machine you
    are not sitting at. With a grid it is arithmetic.
    """
    from PIL import Image, ImageDraw

    from harness.image import to_png_bytes  # noqa: F401  (kept for parity)

    picture = Image.fromarray(image).convert("RGB")
    draw = ImageDraw.Draw(picture)
    width, height = picture.size

    for x in range(0, width, GRID):
        heavy = x % (GRID * 5) == 0
        draw.line([(x, 0), (x, height)], fill=GRID_COLOUR, width=2 if heavy else 1)
        if heavy and x:
            draw.text((x + 3, 3), str(x), fill=GRID_COLOUR)
    for y in range(0, height, GRID):
        heavy = y % (GRID * 5) == 0
        draw.line([(0, y), (width, y)], fill=GRID_COLOUR, width=2 if heavy else 1)
        if heavy and y:
            draw.text((3, y + 3), str(y), fill=GRID_COLOUR)
    return picture


def parse_box(text: str) -> dict[str, int]:
    """`x,y,width,height` -- the same shape a flow's region takes."""
    try:
        x, y, width, height = (int(part) for part in text.split(","))
    except ValueError:
        raise SystemExit(f"--box wants x,y,width,height -- got {text!r}") from None
    if width <= 0 or height <= 0:
        raise SystemExit(f"--box has no area: {text!r}")
    return {"x": x, "y": y, "width": width, "height": height}


def cut(image_path: Path, box: dict[str, int], out_path: Path) -> tuple[int, int]:
    from harness.image import load_rgb, to_png_bytes
    from understudy.vision import crop

    image = load_rgb(image_path.read_bytes())
    height, width = image.shape[:2]
    if box["x"] + box["width"] > width or box["y"] + box["height"] > height:
        raise SystemExit(
            f"that box runs off the edge: {box} against a {width}x{height} "
            f"image. Anchors are cut in window coordinates, so the numbers "
            f"come from window-grid.png, not from the whole screen."
        )
    piece = crop(image, box)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(to_png_bytes(piece))
    return piece.shape[1], piece.shape[0]


def capture(title: str, process: str | None, out_dir: Path) -> int:
    from understudy.drivers.native import NativeDriver
    from harness.image import load_rgb

    driver = NativeDriver()
    driver.start({"window_title_pattern": title, "process": process,
                  "mouse": {"mode": "instant"}})
    for note in driver.warnings:
        print(f"note: {note}")
    shot = driver.screenshot()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "window.png").write_bytes(shot)

    image = load_rgb(shot)
    height, width = image.shape[:2]
    print(f"window {width}x{height}")
    if driver.geometry:
        print(f"at {driver.geometry.describe()}")

    with_grid(image).save(out_dir / "window-grid.png")
    print(f"written to {out_dir}/")
    print("  window.png       the capture the anchors are cut from")
    print("  window-grid.png  the same, with a 100px grid to read regions off")
    driver.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--title", default="*", help="window title glob")
    parser.add_argument("--process", default=None,
                        help="image name that must own the window, e.g. CATIA.exe")
    parser.add_argument("--out", default=None)
    parser.add_argument("--cut", metavar="IMAGE",
                        help="cut a region out of an existing capture instead")
    parser.add_argument("--box", help="x,y,width,height, in window coordinates")
    parser.add_argument("--name", help="what this anchor is, for the message")
    args = parser.parse_args(argv)

    if args.cut:
        if not args.box:
            raise SystemExit("--cut needs --box x,y,width,height")
        out = Path(args.out or "anchor.png")
        width, height = cut(Path(args.cut), parse_box(args.box), out)
        print(f"{args.name or out.stem}: {width}x{height} -> {out}")
        return 0

    if sys.platform != "win32":
        print("capturing a window only means anything on Windows", file=sys.stderr)
        return 2
    stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    return capture(args.title, args.process, Path(args.out or f"capture-{stamp}"))


if __name__ == "__main__":
    raise SystemExit(main())
