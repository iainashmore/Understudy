"""Reading text that only exists as pixels.

The genuine last resort, for a response rendered into a canvas or a
custom-drawn panel where there is no text to extract. Two things follow from
that, and both are deliberate:

  * The region image is always kept, whether or not OCR runs. A transcription
    is a lossy derivative; the pixels are the evidence, and a run whose only
    record was an OCR guess would be unauditable.
  * A missing OCR engine is reported, never silently treated as an empty
    response. An empty string that means "could not read" is indistinguishable
    from one that means "the assistant said nothing", and the two demand
    completely different reactions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OcrResult:
    text: str
    engine: str
    available: bool = True
    error: str | None = None


#: Where the Windows installer puts it. Ticking "add to PATH" is optional and
#: easily missed, and a new terminal is needed even when it is ticked -- so
#: "tesseract is not recognized" is the ordinary outcome of installing it
#: correctly, and looking is kinder than explaining.
WINDOWS_PLACES = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe",
    r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe",
)


def find_tesseract() -> str | None:
    """The engine, wherever it is. PATH first, then where it installs to.

    UNDERSTUDY_TESSERACT overrides everything, for an installation somewhere
    of its own.
    """
    import os
    import shutil

    override = os.environ.get("UNDERSTUDY_TESSERACT")
    if override:
        return override
    found = shutil.which("tesseract")
    if found:
        return found
    for place in WINDOWS_PLACES:
        candidate = Path(os.path.expandvars(place))
        if candidate.exists():
            return str(candidate)
    return None


def _configure(pytesseract) -> str | None:
    """Point pytesseract at the engine, if it cannot find one itself."""
    binary = find_tesseract()
    if binary:
        pytesseract.pytesseract.tesseract_cmd = binary
    return binary


def available() -> bool:
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    _configure(pytesseract)
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def read_text(image: bytes) -> OcrResult:
    """Transcribe an image. Never raises."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return OcrResult(
            text="", engine="none", available=False,
            error="OCR needs pytesseract and the tesseract binary "
                  "(pip install pytesseract; apt install tesseract-ocr)",
        )

    import io

    binary = _configure(pytesseract)
    if not binary:
        return OcrResult(
            text="", engine="none", available=False,
            error="pytesseract is installed but the tesseract engine is not. "
                  "Install it (winget install UB-Mannheim.TesseractOCR), or "
                  "point UNDERSTUDY_TESSERACT at it.",
        )

    try:
        with Image.open(io.BytesIO(image)) as picture:
            text = pytesseract.image_to_string(picture)
        return OcrResult(text=" ".join(text.split()), engine=binary)
    except Exception as exc:
        return OcrResult(
            text="", engine=binary, available=False,
            error=f"OCR failed: {exc}",
        )
