"""Cut anchors for the blind Notepad flow out of a live window.

The blind flow finds its controls by matching pictures. Those pictures cannot
be committed: Notepad's chrome differs between Windows builds, themes and DPI
settings, and an anchor cropped on one machine is a near-miss on the next --
which is a fair rehearsal for a CAD application, where the anchors are captured
on the workstation that will replay them.

So they are cut here, on the machine about to run the flow, from a window that
is already open. Written next to the flow as `anchors/notepad/*.png`.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ANCHORS = Path(__file__).resolve().parents[1] / "examples" / "anchors" / "notepad"


def main() -> int:
    if sys.platform != "win32":
        print("this only means anything on Windows", file=sys.stderr)
        return 2

    from understudy.drivers.native import NativeDriver
    from understudy.vision import crop
    from harness.image import load_rgb, to_png_bytes

    notepad = subprocess.Popen(["notepad.exe"])
    time.sleep(2.5)
    try:
        driver = NativeDriver()
        driver.start({
            "window_title_pattern": "*Notepad*",
            "mouse": {"mode": "instant"},
        })
        window = load_rgb(driver.screenshot())
        height, width = window.shape[:2]
        print(f"window {width}x{height}")

        ANCHORS.mkdir(parents=True, exist_ok=True)

        # The menu bar. Text, so it has features to match on -- unlike the
        # empty text area, which is a featureless white rectangle and would
        # match everywhere or nowhere. This is exactly the lesson from the CAD
        # fixture: anchor on something that does not change, and act at an
        # offset from it.
        menu = crop(window, x=0, y=0, width=min(220, width), height=min(60, height))
        (ANCHORS / "menu_bar.png").write_bytes(to_png_bytes(menu))
        print(f"  menu_bar.png   {menu.shape[1]}x{menu.shape[0]}")

        for path in sorted(ANCHORS.glob("*.png")):
            print(f"  wrote {path}")
        driver.stop()
    finally:
        subprocess.run(["taskkill", "/pid", str(notepad.pid), "/f", "/t"],
                       capture_output=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
