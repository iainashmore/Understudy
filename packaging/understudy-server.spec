# PyInstaller spec for the packaged server.
#
# One directory rather than one file. A one-file build unpacks the whole bundle
# to a temp folder on every launch -- with Chromium in it that is most of a
# gigabyte and tens of seconds, every time -- and a self-extracting executable
# of that size is the classic false-positive shape for antivirus. The single
# download the user asked for is the installer; this is what it installs.
#
# PyInstaller cannot cross-compile: a Windows build has to run on Windows.

import os
import shutil
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parent          # the repository checkout

datas = [
    (str(ROOT / "understudy" / "ui" / "static"), "understudy/ui/static"),
    (str(ROOT / "understudy" / "schema"), "understudy/schema"),
]
binaries = []
hiddenimports = collect_submodules("understudy")

# Playwright ships its own Node driver; without collect_all the frozen build
# imports cleanly and then fails the moment it tries to launch anything.
for module in ("playwright", "anthropic", "jsonschema", "jsonschema_specifications"):
    try:
        module_datas, module_binaries, module_hidden = collect_all(module)
    except Exception:
        continue
    datas += module_datas
    binaries += module_binaries
    hiddenimports += module_hidden

if sys.platform == "win32":
    hiddenimports += collect_submodules("pywinauto") + ["comtypes", "pyperclip"]

# Bundled tools, staged into packaging/payload by fetch_payload.py. Absent
# during a plain source build, which is fine -- everything degrades to a
# message rather than a crash.
PAYLOAD = Path(SPECPATH) / "payload"
for name, target in (("ms-playwright", "ms-playwright"),
                     ("tools", "tools"),
                     ("tessdata", "tessdata")):
    source = PAYLOAD / name
    if source.is_dir():
        datas.append((str(source), target))

a = Analysis(
    [str(Path(SPECPATH) / "server_entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    excludes=["pytest", "matplotlib", "tkinter", "cairosvg", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="understudy-server",
    console=True,            # the shell reads stdout to know it started
    debug=False, strip=False, upx=False,
)
COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name="server",
)
