"""Stage the binaries the packaged app carries with it.

Understudy needs three things that are not Python: a Chromium for launch-mode
web flows, ffmpeg for recording, and Tesseract for the OCR fallback. On the
machine this is built for -- a CAD workstation behind a corporate network --
fetching any of them at first run is exactly the thing that will not work, and
it will not work at the moment somebody is trying to demonstrate the tool.

So they are downloaded here, at build time, into packaging/payload, and the
spec bundles whatever it finds. Anything missing degrades to a message at
runtime rather than a crash, which is why this script reports what it got
rather than insisting on all of it.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

PAYLOAD = Path(__file__).resolve().parent / "payload"

#: A pinned build, not "latest". A packaged application that quietly changes
#: its recorder between releases is one whose output stops being comparable,
#: which is the one thing this tool exists to provide.
FFMPEG = {
    "win32": {
        "url": "https://github.com/GyanD/codexffmpeg/releases/download/7.1/ffmpeg-7.1-essentials_build.zip",
        "member": "bin/ffmpeg.exe",
        "target": "ffmpeg.exe",
    },
}

TESSERACT_DATA = {
    "url": "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/eng.traineddata",
    "target": "eng.traineddata",
}


def report(message: str) -> None:
    print(f"  {message}", flush=True)


def download(url: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size:
        report(f"already have {destination.name}")
        return destination
    report(f"downloading {url}")
    with urllib.request.urlopen(url, timeout=300) as response:
        destination.write_bytes(response.read())
    report(f"got {destination.name} ({destination.stat().st_size / 1e6:.0f} MB)")
    return destination


def stage_browsers() -> bool:
    """Ask Playwright to install into the payload rather than the user profile."""
    target = PAYLOAD / "ms-playwright"
    if any(target.glob("chromium-*")):
        report("chromium already staged")
        return True
    target.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(target))
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"],
                       env=environment, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        report(f"could not stage chromium: {exc}")
        return False
    report(f"chromium staged into {target}")
    return True


def stage_ffmpeg() -> bool:
    spec = FFMPEG.get(sys.platform)
    if not spec:
        # Linux and macOS builds take ffmpeg from the system; only the Windows
        # bundle has to carry one, because a CAD workstation will not have it.
        found = shutil.which("ffmpeg")
        report(f"using the system ffmpeg at {found}" if found else "no ffmpeg found")
        return bool(found)

    target = PAYLOAD / "tools" / spec["target"]
    if target.exists():
        report("ffmpeg already staged")
        return True
    archive = download(spec["url"], PAYLOAD / "_downloads" / "ffmpeg.zip")
    with zipfile.ZipFile(archive) as bundle:
        member = next((n for n in bundle.namelist() if n.endswith(spec["member"])), None)
        if not member:
            report(f"{spec['member']} is not in that archive")
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        with bundle.open(member) as source, target.open("wb") as out:
            shutil.copyfileobj(source, out)
    target.chmod(0o755)
    report(f"ffmpeg staged ({target.stat().st_size / 1e6:.0f} MB)")
    return True


def stage_tessdata() -> bool:
    target = PAYLOAD / "tessdata" / TESSERACT_DATA["target"]
    try:
        download(TESSERACT_DATA["url"], target)
    except Exception as exc:
        report(f"could not stage the OCR language data: {exc}")
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-browsers", action="store_true")
    parser.add_argument("--skip-ffmpeg", action="store_true")
    parser.add_argument("--skip-ocr", action="store_true")
    args = parser.parse_args()

    print("Staging the packaged payload")
    outcomes = {
        "chromium": True if args.skip_browsers else stage_browsers(),
        "ffmpeg": True if args.skip_ffmpeg else stage_ffmpeg(),
        "tessdata": True if args.skip_ocr else stage_tessdata(),
    }

    shutil.rmtree(PAYLOAD / "_downloads", ignore_errors=True)
    total = sum(f.stat().st_size for f in PAYLOAD.rglob("*") if f.is_file())
    print(f"\npayload: {total / 1e6:.0f} MB in {PAYLOAD}")
    for name, ok in outcomes.items():
        print(f"  {name:<10} {'ok' if ok else 'MISSING -- the build will work without it'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
